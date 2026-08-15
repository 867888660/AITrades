from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .alpha_run_result_service import AlphaRunResultService
from .backtest_contract import BacktestExecutionSpec
from .definition_registry import DefinitionRegistry
from .factor_alpha import FactorSpec
from .factor_pack import FactorPackRegistry
from .factor_pack_result_service import FactorPackRunResultService
from .factor_run_result_service import FactorRunResultService
from .portfolio import PortfolioSpec
from .process_guard import run_guarded_process
from .workload_scheduler import (
    ResourceAdmissionController,
    automatic_queue_status,
    worker_log_path,
)
from .requirement_compiler import RequirementCompiler
from .requirement_maintenance_service import RequirementMaintenanceService
from .research_run_service import PreviewStaleError, ResearchRunService, ResearchRunWorker
from .research_backtest_result_service import ResearchBacktestResultService
from .run_preview_service import ResearchRunPreviewService
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService
from .research_semantics import (
    RESEARCH_RESULT_SCHEMA_VERSION,
    ResearchContractService,
    ResearchSemanticError,
    normalize_candidate,
)


EXPERIMENT_STATES = {
    "ACCEPTED",
    "COMPILING",
    "PREPARING_DATA",
    "QUEUED",
    "RUNNING",
    "EVALUATING",
    "COMPLETE",
    "INVALID",
    "SYSTEM_BLOCKED",
    "FAILED",
    "CANCELLED",
}
TERMINAL_EXPERIMENT_STATES = {
    "COMPLETE",
    "INVALID",
    "SYSTEM_BLOCKED",
    "FAILED",
    "CANCELLED",
}
RESEARCH_DECISIONS = {"KEEP", "REJECT", "INCONCLUSIVE"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(value)).encode("utf-8")).hexdigest()


def _factor_spec(raw: Mapping[str, Any], frequency: str, candidate_hash: str) -> FactorSpec:
    raw = dict(raw)
    formula = dict(raw.get("formula") or {})
    raw_name = _clean(raw.get("name") or "research_factor")
    name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", raw_name).strip("_") or "research_factor"
    raw_window = raw.get("window")
    formula_window = formula.get("window")
    window = raw_window if raw_window is not None else formula_window
    if window is None:
        window = 1
    try:
        return FactorSpec(
            name=name[:80],
            version=f"experiment.{candidate_hash[:12]}",
            operator=_clean(raw.get("operator") or formula.get("operator")),
            input_field=_clean(raw.get("input_field") or formula.get("input") or "close"),
            window=int(window),
            minimum_observations=raw.get("minimum_observations"),
            missing_policy=_clean(raw.get("missing_policy") or "STRICT"),
            parameters=dict(raw.get("parameters") or formula.get("parameters") or {}),
            frequency=_clean(frequency),
            output_unit=_clean(raw.get("output_unit") or "RATIO"),
            output_direction=_clean(raw.get("output_direction") or "NO_PREDEFINED_DIRECTION"),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchSemanticError(
            "CANDIDATE_FACTOR_INVALID",
            f"Factor 假设无法编译：{exc}",
        ) from exc


def _instrument_ids(value: Any, provider: str) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        raw = _clean(value)
        items = [item.strip() for item in raw.split(",")] if "," in raw else ([raw] if raw else [])
    result: list[str] = []
    for item in items:
        instrument = _clean(item).split(" ", 1)[0]
        if not instrument:
            continue
        if ":" in instrument:
            result.append(instrument)
        elif _clean(provider).upper() == "BINANCE":
            result.append(f"crypto_spot:BINANCE:{instrument.upper()}")
        else:
            result.append(instrument.upper())
    return sorted(set(result))


def _provider_bar_contract(
    provider: Any,
    *,
    universe_type: Any = "",
) -> dict[str, str]:
    normalized = _clean(provider).upper()
    if _clean(universe_type).upper() == "HISTORICAL_EQUITY_PIT":
        # The current historical equity PIT engine is backed by the managed
        # CRSP/CIZ collection.  US_EQUITY Sessions default to OPENBB before
        # their Universe is compiled, so the frozen Universe contract must
        # take precedence over that generic Session default.
        normalized = "CRSP"
    if normalized in {"CRSP", "CRSP/CIZ"}:
        return {
            "adjustment": "CRSP_FIELDS",
            "time_semantics": "SOURCE_AVAILABLE_TIME",
            "preferred_source": "crsp/ciz",
        }
    return {
        "adjustment": "NONE",
        "time_semantics": "BAR_END_AVAILABLE_TIME",
        "preferred_source": _clean(provider).lower(),
    }


class ResearchExperimentService:
    """Researcher-facing Experiment facade over DataTube's internal IR objects."""

    _advance_locks_guard = threading.Lock()
    _advance_locks: dict[str, threading.Lock] = {}
    _background_guard = threading.Lock()
    _background_inflight: set[str] = set()
    _background_classes: dict[str, str] = {}
    _background_limit = 2
    _admission = ResourceAdmissionController()

    def __init__(
        self,
        store: DataPlatformStore,
        *,
        isolate_run_execution: bool = False,
        isolate_experiment_execution: bool = False,
        defer_run_execution: bool = False,
    ):
        self.store = store
        self.contracts = ResearchContractService(store)
        self.isolate_run_execution = bool(isolate_run_execution)
        self.isolate_experiment_execution = bool(isolate_experiment_execution)
        self.defer_run_execution = bool(defer_run_execution)

    def submit(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        advance_immediately: bool = True,
    ) -> dict[str, Any]:
        from .research_agent_session import ResearchAgentSessionService

        session = ResearchAgentSessionService(self.store).get(
            session_id, include_events=False, include_iterations=False
        )
        if not session:
            raise ResearchSemanticError("RESEARCH_SESSION_NOT_FOUND", "Research Session 不存在。")
        if _clean(session.get("status")).upper() in {
            "PAUSED", "NEED_HUMAN", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"
        }:
            raise ResearchSemanticError(
                "RESEARCH_SESSION_INACTIVE",
                f"Research Session 当前状态为 {session.get('status')}，不能提交实验。",
            )
        contract = self.contracts.active_for_session(session_id)
        if not contract:
            raise ResearchSemanticError(
                "RESEARCH_CONTRACT_NOT_ACTIVE",
                "必须先形成有效的 Research Contract 才能开始实验。",
            )
        candidate = normalize_candidate(dict(payload.get("candidate") or payload), contract["contract"])
        candidate_hash = _hash(candidate)
        frequency = _clean(contract["contract"].get("frequency"))
        run_type = _clean(dict(candidate.get("evaluation") or {}).get("run_type")).upper()
        if candidate.get("factor_pack"):
            FactorPackRegistry.require(_clean(dict(candidate["factor_pack"]).get("pack_id")))
        for raw_factor in list(candidate.get("factors") or []):
            _factor_spec(raw_factor, frequency, candidate_hash)
        if run_type == "RESEARCH_BACKTEST":
            try:
                PortfolioSpec(**dict(candidate.get("portfolio_spec") or {}))
                BacktestExecutionSpec.from_payload(dict(candidate.get("execution_spec") or {}))
            except (TypeError, ValueError) as exc:
                raise ResearchSemanticError(
                    "CANDIDATE_PORTFOLIO_EVIDENCE_INVALID",
                    f"组合或执行假设无法编译：{exc}",
                ) from exc
        explicit_key = _clean(payload.get("idempotency_key"))
        idempotency_key = explicit_key or f"experiment:{session_id}:{candidate_hash}"
        with self.store.connection() as conn:
            prior = conn.execute(
                "SELECT experiment_id FROM research_experiments WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if prior:
            existing = self.get(str(prior[0]), public=False)
            if existing and existing["candidate_hash"] != candidate_hash:
                raise ResearchSemanticError(
                    "RESEARCH_IDEMPOTENCY_CONFLICT",
                    "该 idempotency_key 已用于另一个 CandidateSpec。",
                )
            return self.get(str(prior[0]))  # type: ignore[return-value]

        with self.store.connection() as conn:
            active = conn.execute(
                "SELECT experiment_id FROM research_experiments WHERE session_id=? "
                "AND status IN ('ACCEPTED','COMPILING','PREPARING_DATA','QUEUED','RUNNING','EVALUATING') "
                "ORDER BY created_at DESC LIMIT 1",
                (_clean(session_id),),
            ).fetchone()
            experiment_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM research_experiments WHERE session_id=?",
                    (_clean(session_id),),
                ).fetchone()[0]
            )
        if active:
            raise ResearchSemanticError(
                "RESEARCH_EXPERIMENT_ALREADY_ACTIVE",
                "当前已有一个 Experiment 在执行；请等待其完成后再提出下一项假设。",
                context={"experiment_id": str(active[0])},
            )
        max_experiments = int(
            dict(contract["contract"].get("experiment_policy") or {}).get("max_experiments") or 10
        )
        if experiment_count >= max_experiments:
            raise ResearchSemanticError(
                "RESEARCH_EXPERIMENT_BUDGET_EXHAUSTED",
                "Research Contract 的实验预算已用完，不能继续搜索候选。",
                context={"used": experiment_count, "limit": max_experiments},
            )

        iteration = ResearchAgentSessionService(self.store).create_iteration(
            session_id,
            {
                "hypothesis": candidate["hypothesis"],
                "intervention_set": candidate["intervention_set"],
                "controlled_variables": candidate["controlled_variables"],
                "change_set": self._change_set(candidate),
            },
        )
        experiment_id = f"experiment_{uuid.uuid4().hex}"
        now = utc_now()
        plan = {
            "schema_version": "execution-plan.v1",
            "contract_id": contract["contract_id"],
            "contract_hash": contract["contract_hash"],
            "candidate_hash": candidate_hash,
            "compiler": "research-experiment-compiler.v1",
            "state": "DERIVATION_PENDING",
        }
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO research_experiments(
                    experiment_id, session_id, project_id, contract_id, iteration_id,
                    idempotency_key, status, phase, candidate_json, candidate_hash,
                    execution_plan_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACCEPTED', 'COMPILING', ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    _clean(session_id),
                    _clean(session.get("project_id")),
                    contract["contract_id"],
                    iteration["iteration_id"],
                    idempotency_key,
                    json_dumps(candidate),
                    candidate_hash,
                    json_dumps(plan),
                    now,
                    now,
                ),
            )
        if advance_immediately:
            self.advance(experiment_id)
        return self.get(experiment_id)  # type: ignore[return-value]

    def get(self, experiment_id: str, *, public: bool = True) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_experiments WHERE experiment_id=?",
                (_clean(experiment_id),),
            ).fetchone()
        if not row:
            return None
        result = self._row(row)
        if public:
            result["queue"] = self._queue_status(result)
            result["progress"] = self._progress_status(result)
            result.pop("execution_plan", None)
            for internal_key in (
                "project_id",
                "contract_id",
                "iteration_id",
                "idempotency_key",
                "candidate_hash",
                "run_id",
            ):
                result.pop(internal_key, None)
            internal = dict(result.pop("system_block_internal", {}) or {})
            if internal:
                result["system_block"] = {
                    "code": internal.get("code") or "SYSTEM_BLOCKED",
                    "phase": internal.get("phase") or result.get("phase"),
                    "message": internal.get("public_message") or "DataTube 暂时无法完成该实验。",
                    "retryable": bool(internal.get("retryable")),
                    "issue_id": internal.get("issue_id") or "",
                }
        return result

    def list(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT experiment_id FROM research_experiments WHERE session_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (_clean(session_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [item for row in rows if (item := self.get(str(row[0]))) is not None]

    def _queue_status(self, experiment: Mapping[str, Any]) -> dict[str, Any]:
        experiment_id = _clean(experiment.get("experiment_id"))
        status = _clean(experiment.get("status")).upper()
        resource_class = self._resource_class(experiment_id)
        worker_memory_mb = self._worker_memory_mb(resource_class)
        pending_states = (
            "ACCEPTED", "COMPILING", "PREPARING_DATA", "QUEUED", "RUNNING", "EVALUATING"
        )
        position = 0
        total = 0
        if status in pending_states:
            with self.store.connection() as conn:
                total = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM research_experiments "
                        "WHERE status IN ('ACCEPTED','COMPILING','PREPARING_DATA','QUEUED','RUNNING','EVALUATING')"
                    ).fetchone()[0]
                )
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM research_experiments
                    WHERE status IN ('ACCEPTED','COMPILING','PREPARING_DATA','QUEUED','RUNNING','EVALUATING')
                      AND (created_at < ? OR (created_at = ? AND experiment_id <= ?))
                    """,
                    (
                        _clean(experiment.get("created_at")),
                        _clean(experiment.get("created_at")),
                        experiment_id,
                    ),
                ).fetchone()
                position = max(1, int(row[0] or 0)) if row else 1
        with self._background_guard:
            dispatched = experiment_id in self._background_inflight
        if status in TERMINAL_EXPERIMENT_STATES:
            state = "TERMINAL"
            reason = status
        elif status in {"RUNNING", "EVALUATING"} or dispatched:
            state = "DISPATCHED"
            reason = "WORKER_ACTIVE"
        else:
            allowed, reason = self._admission.can_dispatch(
                resource_class, worker_memory_mb
            )
            state = "READY" if allowed and position <= 1 else "WAITING"
            if state == "WAITING" and allowed:
                reason = "FIFO_QUEUE"
        return automatic_queue_status(
            state=state,
            position=position,
            total=total,
            queued_at=experiment.get("created_at"),
            reason=reason,
        )

    def _progress_status(self, experiment: Mapping[str, Any]) -> dict[str, Any]:
        status = _clean(experiment.get("status")).upper()
        phase = _clean(experiment.get("phase")).upper() or status
        percent_by_status = {
            "ACCEPTED": 2,
            "COMPILING": 12,
            "PREPARING_DATA": 30,
            "QUEUED": 45,
            "RUNNING": 65,
            "EVALUATING": 90,
            "COMPLETE": 100,
            "INVALID": 100,
            "SYSTEM_BLOCKED": 100,
            "FAILED": 100,
            "CANCELLED": 100,
        }
        heartbeat_at = _clean(experiment.get("updated_at"))
        run_id = _clean(experiment.get("run_id"))
        attempt = 0
        if run_id:
            with self.store.connection() as conn:
                row = conn.execute(
                    "SELECT heartbeat_at, attempt_count FROM research_runs_v2 WHERE run_id=?",
                    (run_id,),
                ).fetchone()
            if row:
                heartbeat_at = _clean(row["heartbeat_at"]) or heartbeat_at
                attempt = int(row["attempt_count"] or 0)
        messages = {
            "ACCEPTED": "实验已接收，系统正在自动安排执行。",
            "COMPILING": "正在编译研究定义。",
            "PREPARING_DATA": "正在准备研究所需数据。",
            "QUEUED": "实验已进入计算队列。",
            "RUNNING": "研究计算正在受控环境中运行。",
            "EVALUATING": "正在评估并整理实验结果。",
            "COMPLETE": "实验已完成。",
            "INVALID": "实验语义无效，未产生研究结论。",
            "SYSTEM_BLOCKED": "系统已安全停止本次实验，其他功能不受影响。",
            "FAILED": "实验未完成，其他功能不受影响。",
            "CANCELLED": "实验已取消。",
        }
        return {
            "phase": phase,
            "percent": percent_by_status.get(status, 0),
            "message": messages.get(status, "系统正在处理实验。"),
            "heartbeat_at": heartbeat_at,
            "attempt": attempt,
            "action_required": False,
            "next_update_seconds": 5 if status not in TERMINAL_EXPERIMENT_STATES else 0,
        }

    def decide(
        self,
        experiment_id: str,
        decision: str,
        learning: Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        from .research_agent_session import ResearchAgentSessionService

        current = self.get(experiment_id, public=False)
        if not current:
            raise ResearchSemanticError("RESEARCH_EXPERIMENT_NOT_FOUND", "Experiment 不存在。")
        decision = _clean(decision).upper()
        if decision not in RESEARCH_DECISIONS:
            raise ResearchSemanticError(
                "RESEARCH_DECISION_INVALID",
                "decision 必须是 KEEP、REJECT 或 INCONCLUSIVE。",
            )
        if current["status"] != "COMPLETE":
            raise ResearchSemanticError(
                "RESEARCH_EXPERIMENT_NOT_COMPLETE",
                "只有完成并产生有效研究结果的 Experiment 才能形成研究判断。",
            )
        learning_payload = (
            {"summary": _clean(learning)} if isinstance(learning, str) else dict(learning or {})
        )
        if not any(_clean(value) for value in learning_payload.values()):
            raise ResearchSemanticError("RESEARCH_LEARNING_REQUIRED", "必须记录本轮实验学到了什么。")
        ResearchAgentSessionService(self.store).complete_iteration(
            current["iteration_id"],
            {
                "candidate_run_id": current.get("run_id") or "",
                "decision": decision,
                "decision_reason": _clean(learning_payload.get("summary")),
                "metrics_after": dict(current.get("result") or {}).get("decision_metrics") or {},
                "comparison": dict(current.get("result") or {}).get("comparison") or {},
                "warnings": dict(current.get("result") or {}).get("warnings") or [],
            },
        )
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_experiments SET decision=?, learning_json=?, updated_at=? WHERE experiment_id=?",
                (decision, json_dumps(learning_payload), utc_now(), _clean(experiment_id)),
            )
        return self.get(experiment_id)  # type: ignore[return-value]

    def advance_pending(self, *, limit: int = 20) -> dict[str, Any]:
        experiment_ids = self._pending_experiment_ids(limit=limit)
        dispatched: list[str] = []
        for experiment_id in experiment_ids:
            resource_class = self._resource_class(experiment_id)
            with self._background_guard:
                if experiment_id in self._background_inflight:
                    continue
                active_classes = set(self._background_classes.values())
                if resource_class == "HEAVY":
                    if self._background_inflight:
                        continue
                elif "HEAVY" in active_classes:
                    continue
                elif len(self._background_inflight) >= self._background_limit:
                    continue
                worker_memory_mb = self._worker_memory_mb(resource_class)
                if not self._admission.acquire(
                    experiment_id, resource_class, worker_memory_mb
                ):
                    continue
                self._background_inflight.add(experiment_id)
                self._background_classes[experiment_id] = resource_class
            threading.Thread(
                target=self._advance_in_background,
                args=(experiment_id,),
                name=f"research-experiment-{experiment_id[-8:]}",
                daemon=True,
            ).start()
            dispatched.append(experiment_id)
        return {"advanced": dispatched, "count": len(dispatched)}

    @staticmethod
    def _worker_memory_mb(resource_class: str) -> int:
        env_name = (
            "DATATUBE_RESEARCH_WORKER_MEMORY_MB"
            if str(resource_class).upper() == "HEAVY"
            else "DATATUBE_STANDARD_WORKER_MEMORY_MB"
        )
        default = "8192" if str(resource_class).upper() == "HEAVY" else "4096"
        return max(512, int(os.environ.get(env_name, default)))

    def _resource_class(self, experiment_id: str) -> str:
        """Classify full-market/long-history work as mutually exclusive."""

        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT s.brief_json, c.contract_json
                FROM research_experiments AS e
                LEFT JOIN research_agent_sessions AS s ON s.session_id=e.session_id
                LEFT JOIN research_contracts AS c ON c.contract_id=e.contract_id
                WHERE e.experiment_id=?
                """,
                (_clean(experiment_id),),
            ).fetchone()
        if row is None:
            # Tests and synthetic callers may provide IDs without persisted
            # metadata. Keep those as bounded standard work.
            return "STANDARD"
        try:
            brief = json.loads(row["brief_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            brief = {}
        try:
            contract = json.loads(row["contract_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = {}
        universe_policy = dict(
            dict(contract or {}).get("universe_policy")
            or dict(brief or {}).get("universe_policy")
            or {}
        )
        eligibility = dict(universe_policy.get("eligibility") or {})
        if _clean(eligibility.get("mode")).upper() == "HISTORICAL_EQUITY_PIT":
            return "HEAVY"
        scope = dict(brief or {}).get("instrument_scope") or eligibility.get(
            "instrument_scope"
        )
        if isinstance(scope, (list, tuple, set)) and len(scope) > 250:
            return "HEAVY"
        return "STANDARD"

    def _pending_experiment_ids(self, *, limit: int) -> list[str]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT experiment_id FROM research_experiments "
                "WHERE status IN ('ACCEPTED','COMPILING','PREPARING_DATA','QUEUED','RUNNING','EVALUATING') "
                "ORDER BY updated_at ASC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _advance_in_background(self, experiment_id: str) -> None:
        try:
            if self.isolate_experiment_execution:
                self._advance_isolated(experiment_id)
            else:
                self.advance(experiment_id)
        except Exception as exc:
            current = self.get(experiment_id, public=False)
            if current and current.get("status") not in TERMINAL_EXPERIMENT_STATES:
                self._mark_system_blocked(experiment_id, exc)
        finally:
            with self._background_guard:
                self._background_inflight.discard(experiment_id)
                self._background_classes.pop(experiment_id, None)
            self._admission.release(experiment_id)

    def _advance_isolated(self, experiment_id: str) -> None:
        resource_class = self._resource_class(experiment_id)
        command = [
            sys.executable,
            "-m",
            "services.data_platform.research_experiment_child",
            "--db-path",
            str(self.store.db_path),
            "--experiment-id",
            _clean(experiment_id),
        ]
        run_guarded_process(
            command,
            cwd=Path(__file__).resolve().parents[2],
            log_path=worker_log_path(
                self.store.db_path, "research_experiments", experiment_id, "latest.log"
            ),
            timeout_seconds=int(
                os.environ.get("DATATUBE_RESEARCH_EXPERIMENT_TIMEOUT_SECONDS", "3600")
            ),
            memory_limit_mb=self._worker_memory_mb(resource_class),
        )

    def quarantine_interrupted(self) -> dict[str, Any]:
        """Quarantine work that was active in a previous server process."""

        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT e.experiment_id, e.run_id, e.status,
                       COALESCE(r.status, '') AS run_status
                FROM research_experiments AS e
                LEFT JOIN research_runs_v2 AS r ON r.run_id=e.run_id
                WHERE e.status IN ('RUNNING','EVALUATING')
                   OR (e.status='QUEUED' AND r.status='RUNNING')
                ORDER BY e.updated_at
                """
            ).fetchall()
        quarantined: list[str] = []
        for row in rows:
            experiment_id = str(row["experiment_id"])
            run_id = _clean(row["run_id"])
            if run_id and _clean(row["run_status"]).upper() == "RUNNING":
                ResearchRunWorker(
                    self.store, "research-restart-recovery"
                ).fail_interrupted(run_id)
            self._mark_system_blocked(
                experiment_id,
                RuntimeError("server process ended during active experiment"),
                code="RESEARCH_PROCESS_INTERRUPTED",
                public_message=(
                    "DataTube 检测到服务曾在实验运行期间退出；为防止重启后自动重复重型任务，"
                    "本次实验已安全隔离，且没有产生研究结论。"
                ),
            )
            quarantined.append(experiment_id)
        return {"quarantined": quarantined, "count": len(quarantined)}

    def advance(self, experiment_id: str) -> dict[str, Any]:
        experiment_id = _clean(experiment_id)
        with self._advance_locks_guard:
            advance_lock = self._advance_locks.setdefault(
                experiment_id, threading.Lock()
            )
        if not advance_lock.acquire(blocking=False):
            # The submit request and background orchestrator may observe the
            # same active row.  Only one may mutate its definition,
            # RequirementSet, Preview and Run closure at a time.
            return self.get(experiment_id) or {}
        try:
            return self._advance_locked(experiment_id)
        finally:
            advance_lock.release()

    def _advance_locked(self, experiment_id: str) -> dict[str, Any]:
        current = self.get(experiment_id, public=False)
        if not current or current["status"] in TERMINAL_EXPERIMENT_STATES:
            return current or {}
        try:
            if current["status"] in {"ACCEPTED", "COMPILING"}:
                self._compile(current)
                # OPTIMIZATION: Explicitly release heavy objects after compilation
                del current
                current = self.get(experiment_id, public=False) or {}
            if current["status"] == "PREPARING_DATA":
                self._try_preview_and_run(current)
                del current
                current = self.get(experiment_id, public=False) or {}
            if current["status"] in {"QUEUED", "RUNNING", "EVALUATING"}:
                self._finish_run(current)
                del current
                current = self.get(experiment_id, public=False) or {}
        except ResearchSemanticError as exc:
            self._mark_invalid(experiment_id, exc)
        except Exception as exc:
            self._mark_system_blocked(experiment_id, exc)
        finally:
            # OPTIMIZATION: Force garbage collection after heavy research operations
            import gc
            gc.collect()
        return self.get(experiment_id) or {}

    def _compile(self, experiment: Mapping[str, Any]) -> None:
        from .research_agent_session import ResearchAgentSessionService

        session = ResearchAgentSessionService(self.store).get(
            experiment["session_id"], include_events=False, include_iterations=False
        )
        contract_row = self.contracts.active_for_session(experiment["session_id"])
        if not session or not contract_row or contract_row["contract_id"] != experiment["contract_id"]:
            raise ResearchSemanticError(
                "RESEARCH_CONTRACT_CHANGED",
                "Research Contract 已变化；旧 Candidate 不能在新边界下静默执行。",
            )
        contract = contract_row["contract"]
        candidate = dict(experiment["candidate"])
        brief = dict(session.get("brief") or {})
        provider = _clean(brief.get("provider") or "BINANCE").upper()
        run_type = _clean(dict(candidate.get("evaluation") or {}).get("run_type")).upper()
        instruments = _instrument_ids(brief.get("instrument_scope"), provider)
        project_id = _clean(experiment["project_id"])
        period = dict(contract.get("research_period") or {})
        history_start = _clean(period.get("start"))
        history_end = _clean(period.get("end"))
        if not history_start or not history_end:
            raise ResearchSemanticError(
                "RESEARCH_PERIOD_REQUIRED",
                "Research Contract 必须明确研究开始和结束时间。",
            )
        universe_policy = dict(contract.get("universe_policy") or {})
        eligibility = dict(universe_policy.get("eligibility") or {})
        exclusions = list(universe_policy.get("exclusions") or [])
        candidate_universe_selection = dict(candidate.get("universe_selection") or {})
        if candidate_universe_selection and run_type != "UNIVERSE_DESIGN":
            raise ResearchSemanticError(
                "GOAL_CONFORMANCE_FAILED",
                "只有 Universe Design Experiment 可以改变冻结的研究池规则。",
                context={"candidate_universe_selection": candidate_universe_selection},
            )
        if run_type == "UNIVERSE_DESIGN":
            candidate_eligibility = candidate_universe_selection.get("eligibility") or {}
            candidate_selection = candidate_universe_selection.get("selection") or {}
            if not isinstance(candidate_eligibility, Mapping) or not isinstance(candidate_selection, Mapping):
                raise ResearchSemanticError(
                    "CANDIDATE_UNIVERSE_SELECTION_INVALID",
                    "Universe Candidate 的 eligibility 与 selection 必须是对象。",
                )
            eligibility = {**eligibility, **dict(candidate_eligibility)}
            universe_policy["eligibility"] = eligibility
            universe_policy["selection"] = {
                **dict(universe_policy.get("selection") or {}),
                **dict(candidate_selection),
            }
            if "exclusions" in candidate_universe_selection:
                raw_exclusions = candidate_universe_selection.get("exclusions")
                if not isinstance(raw_exclusions, list):
                    raise ResearchSemanticError(
                        "CANDIDATE_UNIVERSE_SELECTION_INVALID",
                        "Universe Candidate.exclusions 必须是明确标的 ID 数组。",
                    )
                exclusions = list(raw_exclusions)
                universe_policy["exclusions"] = exclusions
            if "instrument_scope" in candidate_universe_selection:
                instruments = _instrument_ids(candidate_universe_selection.get("instrument_scope"), provider)
        universe_mode = _clean(eligibility.get("mode") or "STATIC_LIST").upper()
        if not instruments and universe_mode != "HISTORICAL_EQUITY_PIT":
            instruments = _instrument_ids(eligibility.get("instrument_scope"), provider)
        if not instruments and universe_mode != "HISTORICAL_EQUITY_PIT":
            raise ResearchSemanticError(
                "RESEARCH_UNIVERSE_REQUIRED",
                "Research Contract 没有可执行的标的范围。",
            )
        excluded_ids = set(_instrument_ids(exclusions, provider))
        unknown_exclusions = [item for item in exclusions if not isinstance(item, str)]
        if unknown_exclusions:
            raise ResearchSemanticError(
                "UNIVERSE_EXCLUSIONS_INVALID",
                "Universe exclusions 当前只接受明确的标的 ID。",
            )
        universe_service = UniverseService(self.store)
        version = f"experiment.{experiment['candidate_hash'][:12]}"
        selection = dict(universe_policy.get("selection") or {})
        selection_method = _clean(selection.get("method") or "ALL_ELIGIBLE").upper()
        if selection_method not in {"", "ALL_ELIGIBLE"}:
            raise ResearchSemanticError(
                "UNIVERSE_SELECTION_ENGINE_NOT_AVAILABLE",
                "Researcher Universe 当前只支持 ALL_ELIGIBLE；不会静默退化为全市场替代执行。",
                context={"selection_method": selection_method},
            )
        if run_type == "UNIVERSE_DESIGN" and list(
            eligibility.get("point_in_time_filters") or []
        ):
            raise ResearchSemanticError(
                "DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION",
                "Dynamic PIT field eligibility requires a formal Factor, Alpha, or Portfolio Evidence Run so every required valuation/fundamentals Manifest can be frozen.",
            )
        if universe_mode == "HISTORICAL_EQUITY_PIT" or any(
            item.upper() == "EQUITY:CRSP:ALL" for item in instruments
        ):
            universe = universe_service.create_definition(
                name=f"research_{experiment['session_id'][:24]}_equity_pit",
                version=version,
                universe_type="HISTORICAL_EQUITY_PIT",
                parameters={
                    "history_start": history_start[:10],
                    "history_end": history_end[:10],
                    "minimum_listing_age_days": int(eligibility.get("minimum_listing_age_days") or 0),
                    "primary_exchanges": list(eligibility.get("primary_exchanges") or []),
                    "security_types": [
                        "EQTY" if _clean(item).upper() in {"COMMON_STOCK", "COMMON STOCK"}
                        else _clean(item).upper()
                        for item in (eligibility.get("security_types") or ["EQTY"])
                    ],
                    "share_types": list(eligibility.get("share_types") or ["NS", "COM"]),
                    "excluded_instrument_ids": sorted(excluded_ids),
                    "point_in_time_filters": list(
                        eligibility.get("point_in_time_filters") or []
                    ),
                },
                owner_project_id=project_id,
                library_scope="PROJECT",
            )
        else:
            selected_instruments = [item for item in instruments if item not in excluded_ids]
            if not selected_instruments:
                raise ResearchSemanticError(
                    "RESEARCH_UNIVERSE_EMPTY",
                    "Universe Candidate 的排除规则移除了全部标的。",
                )
            universe = universe_service.create_definition(
                name=f"research_{experiment['session_id'][:24]}_static",
                version=version,
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": selected_instruments},
                owner_project_id=project_id,
                library_scope="PROJECT",
            )
        bar_contract = _provider_bar_contract(
            provider,
            universe_type=universe.universe_type,
        )
        snapshot = universe_service.resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time=f"{history_end[:10]}T23:59:59+00:00" if len(history_end) == 10 else history_end,
        )
        universe_service.set_research_ref(
            project_id=project_id,
            universe_snapshot_id=snapshot.universe_snapshot_id,
        )

        if run_type == "UNIVERSE_DESIGN":
            result = self._universe_research_result(
                experiment,
                universe_type=universe.universe_type,
                universe_policy=universe_policy,
                as_of_time=snapshot.as_of_time,
                actual_instrument_ids=list(snapshot.actual_instrument_ids),
                selection_inputs=dict(snapshot.selection_inputs),
            )
            self._update(
                experiment["experiment_id"],
                status="COMPLETE",
                phase="COMPLETE",
                execution_plan={
                    **dict(experiment.get("execution_plan") or {}),
                    "state": "UNIVERSE_MATERIALIZED",
                    "universe_definition_id": universe.universe_definition_id,
                    "universe_snapshot_id": snapshot.universe_snapshot_id,
                    "run_type": run_type,
                    "goal_conformance": {"status": "PASS", "contract_hash": contract_row["contract_hash"]},
                },
                result=result,
                completed=True,
            )
            return
        factor_pack = (
            FactorPackRegistry.require(_clean(dict(candidate.get("factor_pack") or {}).get("pack_id")))
            if candidate.get("factor_pack") else None
        )
        registry = DefinitionRegistry(self.store)
        self._clear_previous_experiment_refs(registry, project_id)
        factor_specs: list[FactorSpec] = []
        definitions: dict[str, Any] = {}
        requirement_factor_specs: list[FactorSpec] = []
        if factor_pack:
            requirement_factor_specs = [
                FactorSpec(
                    name=f"{factor_pack.pack_id.replace('.', '_')}_{field}",
                    version=f"requirements.{factor_pack.spec_hash[:12]}",
                    operator="rolling_mean",
                    input_field=field,
                    window=factor_pack.minimum_history_bars,
                    minimum_observations=factor_pack.minimum_history_bars,
                    frequency=factor_pack.frequency,
                )
                for field in factor_pack.required_fields
            ]
        else:
            for index, raw_factor in enumerate(list(candidate.get("factors") or [])):
                factor_spec = _factor_spec(
                    raw_factor, _clean(contract.get("frequency")), experiment["candidate_hash"]
                )
                factor_specs.append(factor_spec)
                definition = registry.create(
                    "FACTOR",
                    {
                        **factor_spec.to_dict(),
                        "operator": factor_spec.operator,
                        "input_field": factor_spec.input_field,
                        "window": factor_spec.window,
                        "parameters": factor_spec.parameters,
                    },
                    state="VALIDATED",
                    created_by="research_experiment_compiler",
                    owner_project_id=project_id,
                    library_scope="PROJECT",
                )
                definitions[factor_spec.name] = definition
                registry.set_project_ref(
                    project_id=project_id,
                    slot_key=f"factor:researcher_candidate:{index}",
                    definition_id=definition.definition_id,
                    definition_version=definition.version,
                    reference_mode="PINNED",
                )
            requirement_factor_specs = factor_specs
        alpha_definition = None
        alpha_spec: dict[str, Any] = {}
        if run_type in {"ALPHA_EVALUATION", "RESEARCH_BACKTEST"}:
            if not factor_specs or not definitions:
                raise ResearchSemanticError(
                    "FACTOR_PACK_ALPHA_NOT_AVAILABLE",
                    "当前不会把 Factor Pack 隐式合成为 Alpha。",
                )
            raw_alpha = dict(candidate.get("alpha") or {})
            raw_components = [dict(item) for item in list(raw_alpha.get("components") or [])]
            components: list[dict[str, Any]] = []
            for component in raw_components:
                factor_name = _clean(component.get("factor"))
                definition = definitions.get(factor_name)
                if definition is None:
                    raise ResearchSemanticError(
                        "CANDIDATE_ALPHA_COMPONENT_UNKNOWN",
                        "Alpha 组件引用了 Candidate 之外的 Factor。",
                        context={"factor": factor_name, "available": sorted(definitions)},
                    )
                components.append({
                    "factor_definition_id": definition.definition_id,
                    "factor_version": definition.version,
                    "factor_spec_hash": definition.spec_hash,
                    "factor_name": factor_name,
                    "weight": float(component.get("weight", 1.0)),
                    "transform": _clean(component.get("transform") or "CS_RANK").upper(),
                    "ascending": bool(component.get("ascending", True)),
                })
            alpha_spec = {
                "name": _clean(raw_alpha.get("name") or f"{factor_specs[0].name}_alpha"),
                "version": version,
                "components": components,
                "minimum_coverage": float(raw_alpha.get("minimum_coverage", 1.0)),
                "minimum_cross_section_size": int(
                    raw_alpha.get("minimum_cross_section_size")
                    or dict(candidate.get("evaluation") or {}).get("minimum_cross_section_size")
                    or (2 if len(snapshot.actual_instrument_ids) > 1 else 1)
                ),
                "missing_policy": _clean(raw_alpha.get("missing_policy") or "EXCLUDE").upper(),
                "rank_method": _clean(raw_alpha.get("rank_method") or "AVERAGE").upper(),
                "output_scale": _clean(raw_alpha.get("output_scale") or "PERCENTILE").upper(),
            }
            alpha_definition = registry.create(
                "ALPHA",
                alpha_spec,
                state="VALIDATED",
                created_by="research_experiment_compiler",
                owner_project_id=project_id,
                library_scope="PROJECT",
            )
            registry.set_project_ref(
                project_id=project_id,
                slot_key="alpha:researcher_candidate",
                definition_id=alpha_definition.definition_id,
                definition_version=alpha_definition.version,
                reference_mode="PINNED",
            )
        requirements = RequirementCompiler(self.store).compile(
            project_id=project_id,
            factor_specs=requirement_factor_specs,
            alpha_specs=[alpha_spec] if alpha_spec else [],
            universe_requirements=UniverseService.data_requirements(universe),
            context={
                "instrument_ids": list(snapshot.actual_instrument_ids),
                "data_type": "bars",
                "frequency": _clean(contract.get("frequency")),
                "history_start": (
                    f"{history_start[:10]}T00:00:00+00:00"
                    if len(history_start) == 10 else history_start
                ),
                "history_end": (
                    f"{history_end[:10]}T23:59:59+00:00"
                    if len(history_end) == 10 else history_end
                ),
                "adjustment": bar_contract["adjustment"],
                "time_semantics": bar_contract["time_semantics"],
                "point_in_time_policy": "AS_OF",
                "quality_policy": "STRICT",
                "source_policy": "FIXED",
            },
        )
        evaluation = dict(candidate.get("evaluation") or {})
        horizons = list(evaluation.get("horizons") or [])
        if not horizons:
            horizons = [1, 5, 20] if _clean(contract.get("frequency")) == "1d" else [1, 6, 24]
        minimum_cross_section_size = evaluation.get("minimum_cross_section_size")
        if minimum_cross_section_size is None:
            minimum_cross_section_size = 2 if len(snapshot.actual_instrument_ids) > 1 else 1
        evaluation_spec = {
            "horizons": horizons,
            "minimum_cross_section_size": int(minimum_cross_section_size),
            # Full-market multi-decade evaluations can contain tens of
            # millions of instrument/horizon observations.  Preserve the
            # complete aggregate IC, quantile-return and stability evidence,
            # but do not retain that redundant row-level expansion in memory.
            "retain_observations": len(snapshot.actual_instrument_ids) <= 500,
        }
        for key in ("quantile_count", "top_n"):
            if evaluation.get(key) is not None:
                evaluation_spec[key] = int(evaluation[key])
        plan = {
            **dict(experiment.get("execution_plan") or {}),
            "state": "COMPILED",
            "universe_definition_id": universe.universe_definition_id,
            "universe_snapshot_id": snapshot.universe_snapshot_id,
            "factor_definition_ids": [item.definition_id for item in definitions.values()],
            "factor_definition_id": next(
                (item.definition_id for item in definitions.values()), ""
            ),
            "factor_pack_definition": factor_pack.to_dict() if factor_pack else {},
            "alpha_definition_id": alpha_definition.definition_id if alpha_definition else "",
            "requirement_set_id": requirements.requirement_set_id,
            "run_type": run_type,
            "evaluation_spec": evaluation_spec,
            "portfolio_spec": dict(candidate.get("portfolio_spec") or {}),
            "execution_spec": dict(candidate.get("execution_spec") or {}),
            "benchmark_spec": dict(candidate.get("benchmark_spec") or {}),
            "source_selection_policy": {
                "preferred_sources": [bar_contract["preferred_source"]]
            },
            "goal_conformance": {
                "status": "PASS",
                "contract_hash": contract_row["contract_hash"],
                "assertions": [
                    {"name": "run_type", "expected": run_type, "actual": run_type, "status": "PASS"},
                    {
                        "name": "frequency",
                        "expected": contract.get("frequency"),
                        "actual": factor_pack.frequency if factor_pack else factor_specs[0].frequency,
                        "status": "PASS",
                    },
                    {"name": "universe_mode", "expected": universe_mode, "actual": universe.universe_type, "status": "PASS"},
                    *([{
                        "name": "factor_pack_id",
                        "expected": dict(contract.get("factor_pack") or {}).get("pack_id"),
                        "actual": factor_pack.pack_id,
                        "status": "PASS",
                    }, {
                        "name": "factor_count",
                        "expected": dict(contract.get("factor_pack") or {}).get("factor_count"),
                        "actual": factor_pack.factor_count,
                        "status": "PASS",
                    }] if factor_pack else []),
                ],
            },
        }
        self._update(
            experiment["experiment_id"],
            status="PREPARING_DATA",
            phase="PREPARING_DATA",
            execution_plan=plan,
        )
        # Preview the requirement set before invoking the global maintenance
        # scanner.  A ready archive-backed study (for example CRSP all-equity)
        # must not synchronously rescan every Library asset and Research
        # project before it can proceed.  _try_preview_and_run schedules
        # maintenance itself when readiness actually contains a gap.
        self._try_preview_and_run(self.get(experiment["experiment_id"], public=False) or experiment)

    def _try_preview_and_run(self, experiment: Mapping[str, Any]) -> None:
        plan = dict(experiment.get("execution_plan") or {})
        if not plan.get("requirement_set_id"):
            return
        from .research_agent_session import ResearchAgentSessionService

        session = ResearchAgentSessionService(self.store).get(
            experiment["session_id"], include_events=False, include_iterations=False
        ) or {}
        preview = ResearchRunPreviewService(self.store).create(
            experiment["project_id"],
            {
                "run_type": plan["run_type"],
                "requirement_set_id": plan["requirement_set_id"],
                "universe_snapshot_id": plan["universe_snapshot_id"],
                "grant_id": _clean(session.get("internal_grant_id")),
                "source_selection_policy": plan["source_selection_policy"],
                "evaluation_spec": plan["evaluation_spec"],
                "portfolio_spec": plan.get("portfolio_spec") or {},
                "execution_spec": plan.get("execution_spec") or {},
                "benchmark_spec": plan.get("benchmark_spec") or {},
                "factor_pack_definition": plan.get("factor_pack_definition") or {},
                "research_semantics": {
                    "contract_hash": plan["contract_hash"],
                    "candidate_hash": plan["candidate_hash"],
                    "goal_conformance": plan["goal_conformance"],
                },
                "budget": {"runs": 1, "runtime_seconds": 300, "download_bytes": 0},
                "actor_id": "research_experiment_orchestrator",
                "actor_type": "AGENT",
            },
            created_by="research_experiment_orchestrator",
        )
        plan["preview_id"] = preview["preview_id"]
        plan["preview_fingerprint"] = preview["preview_fingerprint"]
        frozen_semantics = dict(dict(preview.get("request") or {}).get("research_semantics") or {})
        if (
            frozen_semantics.get("contract_hash") != plan.get("contract_hash")
            or frozen_semantics.get("candidate_hash") != plan.get("candidate_hash")
        ):
            raise ResearchSemanticError(
                "GOAL_CONFORMANCE_FAILED",
                "Preview 没有冻结当前 Contract 与 Candidate 身份，已阻止 Run。",
            )
        if dict(preview.get("readiness") or {}).get("overall", {}).get("status") != "READY":
            self._update(
                experiment["experiment_id"],
                status="PREPARING_DATA",
                phase="PREPARING_DATA",
                execution_plan=plan,
            )
            maintenance = RequirementMaintenanceService(self.store).run_once(
                project_id=_clean(experiment.get("project_id")),
                requirement_set_id=_clean(plan.get("requirement_set_id")),
            )
            if not list(maintenance.get("task_types") or []):
                data_checks = list(
                    dict(dict(preview.get("readiness") or {}).get("dimensions") or {})
                    .get("DATA", {})
                    .get("checks", [])
                )
                reason_codes = sorted({
                    _clean(check.get("code"))
                    for check in data_checks
                    if _clean(check.get("status")).upper() != "READY"
                    and _clean(check.get("code"))
                })
                maintenance_errors = [
                    _clean(item.get("error"))
                    for item in maintenance.get("errors") or []
                    if _clean(item.get("error"))
                ]
                detail = ", ".join(reason_codes or maintenance_errors) or "UNKNOWN_DATA_GAP"
                raise RuntimeError(
                    f"Research data readiness cannot be prepared automatically: {detail}"
                )
            return
        run_service = ResearchRunService(self.store)
        try:
            run = run_service.create(
                preview_id=preview["preview_id"],
                preview_fingerprint=preview["preview_fingerprint"],
                idempotency_key=f"research-experiment:{experiment['experiment_id']}",
                actor_id="research_experiment_orchestrator",
                actor_type="AGENT",
            )
        except PreviewStaleError:
            # One deterministic refresh is safe: it re-freezes the same
            # Contract/Candidate request against the current definition closure.
            # A second stale result is a genuine concurrent mutation and must
            # remain blocked instead of being retried indefinitely.
            preview = ResearchRunPreviewService(self.store).refresh(preview["preview_id"])
            plan["preview_id"] = preview["preview_id"]
            plan["preview_fingerprint"] = preview["preview_fingerprint"]
            frozen_semantics = dict(
                dict(preview.get("request") or {}).get("research_semantics") or {}
            )
            if (
                frozen_semantics.get("contract_hash") != plan.get("contract_hash")
                or frozen_semantics.get("candidate_hash") != plan.get("candidate_hash")
            ):
                raise ResearchSemanticError(
                    "GOAL_CONFORMANCE_FAILED",
                    "Refreshed Preview no longer represents the current Contract and Candidate.",
                )
            if dict(preview.get("readiness") or {}).get("overall", {}).get("status") != "READY":
                self._update(
                    experiment["experiment_id"],
                    status="PREPARING_DATA",
                    phase="PREPARING_DATA",
                    execution_plan=plan,
                )
                return
            run = run_service.create(
                preview_id=preview["preview_id"],
                preview_fingerprint=preview["preview_fingerprint"],
                idempotency_key=f"research-experiment:{experiment['experiment_id']}",
                actor_id="research_experiment_orchestrator",
                actor_type="AGENT",
            )
        bundle = ResearchRunService(self.store).get_bundle(_clean(run.get("bundle_id"))) or {}
        bundle_semantics = dict(dict(bundle.get("canonical_payload") or {}).get("research_semantics") or {})
        if (
            bundle_semantics.get("contract_hash") != plan.get("contract_hash")
            or bundle_semantics.get("candidate_hash") != plan.get("candidate_hash")
        ):
            raise ResearchSemanticError(
                "GOAL_CONFORMANCE_FAILED",
                "Frozen Experiment 与当前 Contract/Candidate 身份不一致，已阻止执行。",
            )
        plan["run_id"] = run["run_id"]
        self._update(
            experiment["experiment_id"],
            status="QUEUED",
            phase="RUNNING",
            execution_plan=plan,
            run_id=run["run_id"],
        )
        self._finish_run(self.get(experiment["experiment_id"], public=False) or experiment)

    def _finish_run(self, experiment: Mapping[str, Any]) -> None:
        run_id = _clean(experiment.get("run_id"))
        if not run_id:
            return
        run = ResearchRunService(self.store).get(run_id)
        if not run:
            raise RuntimeError("formal Research Run disappeared")
        status = _clean(run.get("status")).upper()
        if status in {"QUEUED", "RUNNING"}:
            self._update(experiment["experiment_id"], status=status, phase="RUNNING")
            # Formal compute is owned by the dedicated durable Run dispatcher.
            # The Experiment orchestrator only observes state; it never claims
            # or executes a Run inside its orchestration process.
            if self.defer_run_execution:
                return
            ResearchRunWorker(self.store, "research-experiment-orchestrator").run_once(
                project_id=experiment["project_id"],
                isolate_execution=self.isolate_run_execution,
            )
            run = ResearchRunService(self.store).get(run_id) or run
            status = _clean(run.get("status")).upper()
        if status in {"QUEUED", "RUNNING"}:
            return
        if status != "SUCCEEDED":
            error = dict(run.get("error") or {})
            error_code = _clean(error.get("code")).upper()
            if error_code == "FORMAL_RESEARCH_RESOURCE_LIMIT":
                self._mark_system_blocked(
                    experiment["experiment_id"],
                    RuntimeError(_clean(error.get("message")) or error_code),
                    code="RESEARCH_RESOURCE_PLAN_BLOCKED",
                    public_message=(
                        "该实验的数据规模超过当前分区执行引擎的单任务内存上限，"
                        "已在加载数据前安全停止；Web 与其他任务不受影响，且没有产生研究结论。"
                    ),
                )
                return
            if error_code == "FORMAL_RESEARCH_EXECUTION_TIMEOUT":
                self._mark_system_blocked(
                    experiment["experiment_id"],
                    RuntimeError(_clean(error.get("message")) or error_code),
                    code="RESEARCH_EXECUTION_TIMEOUT",
                    public_message=(
                        "该实验超过受控执行时限，已安全终止；Web 与其他任务不受影响，"
                        "且没有产生研究结论。"
                    ),
                )
                return
            raise RuntimeError("formal Research Run did not complete successfully")
        self._update(experiment["experiment_id"], status="EVALUATING", phase="EVALUATING")
        run_type = _clean(dict(experiment.get("candidate") or {}).get("evaluation", {}).get("run_type")).upper()
        if dict(experiment.get("candidate") or {}).get("factor_pack"):
            product = FactorPackRunResultService(self.store).build(run_id)
            result = self._factor_pack_research_result(experiment, product)
        elif run_type == "RESEARCH_BACKTEST":
            product = ResearchBacktestResultService(self.store).build(run_id)
            result = self._backtest_research_result(experiment, product)
        elif run_type == "ALPHA_EVALUATION":
            product = AlphaRunResultService(self.store).build(run_id)
            result = self._alpha_research_result(experiment, product)
        else:
            product = FactorRunResultService(self.store).build(run_id)
            result = self._research_result(experiment, product)
        self._update(
            experiment["experiment_id"],
            status="COMPLETE",
            phase="COMPLETE",
            result=result,
            completed=True,
        )

    def _universe_research_result(
        self,
        experiment: Mapping[str, Any],
        *,
        universe_type: str,
        universe_policy: Mapping[str, Any],
        as_of_time: str,
        actual_instrument_ids: list[str],
        selection_inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate = dict(experiment.get("candidate") or {})
        eligible_count = len(actual_instrument_ids)
        requested = len(_instrument_ids(
            dict(candidate.get("universe_selection") or {}).get("instrument_scope"), ""
        ))
        coverage = (eligible_count / requested) if requested else 1.0
        decision_metrics = {"eligible_count": eligible_count, "coverage": coverage}
        return {
            "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "status": "COMPLETE",
            "product_type": "UNIVERSE_DESIGN",
            "goal_conformance": "PASS",
            "decision_metrics": decision_metrics,
            "research_diagnostics": {
                "selection_method": dict(selection_inputs).get("method"),
                "dynamic_membership": bool(dict(selection_inputs).get("dynamic_membership")),
                "survivorship_policy": dict(selection_inputs).get("survivorship_policy") or "STATIC_LIST",
            },
            "gates": {"eligibility": "PASS", "pit": "PASS", "non_empty": "PASS"},
            "comparison": self._comparison(experiment, decision_metrics),
            "product": {
                "run_type": "UNIVERSE_DESIGN",
                "universe_type": universe_type,
                "policy": dict(universe_policy),
                "as_of_time": as_of_time,
                "eligible_count": eligible_count,
                "instrument_ids": actual_instrument_ids,
                "selection_evidence": {
                    key: value for key, value in dict(selection_inputs).items()
                    if key != "membership_intervals"
                },
                "membership_intervals": dict(selection_inputs).get("membership_intervals") or {},
            },
            "warnings": [],
            "provenance": {
                "reproducible": True,
                "reference": f"experiment:{experiment['experiment_id']}",
            },
        }

    def _research_result(
        self,
        experiment: Mapping[str, Any],
        product: Mapping[str, Any],
    ) -> dict[str, Any]:
        product = dict(product)
        candidate = dict(experiment.get("candidate") or {})
        primary_metric = _clean(dict(candidate.get("evaluation") or {}).get("primary_metric"))
        results = list(product.get("results") or [])
        first = dict(results[0]) if results else {}
        decision_metrics: dict[str, Any] = {}
        predictive = list(first.get("predictive_power") or [])
        if primary_metric in {"ic", "rank_ic"} and predictive:
            metric = dict(dict(predictive[0]).get(primary_metric) or {})
            decision_metrics[primary_metric] = metric.get("mean")
            decision_metrics[f"{primary_metric}_observations"] = metric.get("count")
        coverage = dict(first.get("coverage") or {})
        if coverage.get("overall") is not None:
            decision_metrics["coverage"] = coverage.get("overall")
        diagnostics = list(first.get("diagnostics") or [])
        comparison = self._comparison(experiment, decision_metrics)
        return {
            "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "status": "COMPLETE",
            "product_type": "FACTOR_EVALUATION",
            "goal_conformance": "PASS",
            "decision_metrics": decision_metrics,
            "research_diagnostics": {
                "coverage": coverage,
                "distribution": dict(first.get("distribution") or {}),
                "items": diagnostics,
            },
            "gates": {
                "execution": "PASS",
                "pit": "PASS",
                "data_quality": "PASS",
            },
            "comparison": comparison,
            "product": self._public_factor_product(product),
            "warnings": list(product.get("warnings") or []),
            "provenance": {
                "reproducible": True,
                "reference": f"experiment:{experiment['experiment_id']}",
            },
        }

    def _alpha_research_result(
        self,
        experiment: Mapping[str, Any],
        product: Mapping[str, Any],
    ) -> dict[str, Any]:
        product = dict(product)
        candidate = dict(experiment.get("candidate") or {})
        primary_metric = _clean(dict(candidate.get("evaluation") or {}).get("primary_metric"))
        results = list(product.get("results") or [])
        first = dict(results[0]) if results else {}
        decision_metrics: dict[str, Any] = {}
        metric_values = dict(first.get(primary_metric) or {})
        if metric_values:
            first_horizon = sorted(metric_values, key=lambda value: int(value))[0]
            summary = dict(metric_values.get(first_horizon) or {})
            decision_metrics[primary_metric] = summary.get("mean")
            decision_metrics[f"{primary_metric}_observations"] = summary.get("count")
            decision_metrics["horizon_bars"] = int(first_horizon)
        signal_summary = dict(first.get("signal_summary") or {})
        if signal_summary.get("score_count") is not None:
            decision_metrics["score_count"] = signal_summary.get("score_count")
        return {
            "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "status": "COMPLETE",
            "product_type": "ALPHA_EVALUATION",
            "goal_conformance": "PASS",
            "decision_metrics": decision_metrics,
            "research_diagnostics": {
                "signal_summary": signal_summary,
                "holding_period_decay": dict(first.get("holding_period_decay") or {}),
                "regime_performance": dict(first.get("regime_performance") or {}),
                "items": list(first.get("diagnostics") or []),
            },
            "gates": {
                "execution": "PASS",
                "pit": "PASS",
                "data_quality": "PASS",
                "portfolio_boundary": "NOT_APPLICABLE",
            },
            "comparison": self._comparison(experiment, decision_metrics),
            "product": self._public_alpha_product(product),
            "warnings": [],
            "provenance": {
                "reproducible": True,
                "reference": f"experiment:{experiment['experiment_id']}",
            },
        }

    def _backtest_research_result(
        self,
        experiment: Mapping[str, Any],
        product: Mapping[str, Any],
    ) -> dict[str, Any]:
        product = dict(product)
        candidate = dict(experiment.get("candidate") or {})
        primary_metric = _clean(dict(candidate.get("evaluation") or {}).get("primary_metric"))
        results = list(product.get("results") or [])
        first = dict(results[0]) if results else {}
        performance = dict(first.get("performance") or {})
        metric_key = {
            "sharpe_ratio": "sharpe",
            "cost_adjusted_return": "total_return",
        }.get(primary_metric, primary_metric)
        decision_metrics = {
            primary_metric: performance.get(metric_key),
            "total_return": performance.get("total_return"),
            "annualized_return": performance.get("annualized_return"),
            "sharpe_ratio": performance.get("sharpe"),
            "max_drawdown": performance.get("max_drawdown"),
            "fees": performance.get("fees"),
            "slippage_cost": performance.get("slippage_cost"),
            "turnover": performance.get("turnover"),
        }
        decision_metrics = {key: value for key, value in decision_metrics.items() if value is not None}
        benchmark_status = dict(product.get("benchmark_status") or {})
        warnings = []
        if benchmark_status.get("warning"):
            warnings.append({"code": "BENCHMARK_NOT_MATERIALIZED", "message": benchmark_status["warning"]})
        return {
            "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "status": "COMPLETE",
            "product_type": "RESEARCH_BACKTEST",
            "goal_conformance": "PASS",
            "decision_metrics": decision_metrics,
            "research_diagnostics": {
                "costs": dict(first.get("costs") or {}),
                "benchmark_status": self._public_benchmark_status(benchmark_status),
                "items": list(first.get("diagnostics") or []),
            },
            "gates": {
                "execution": "PASS",
                "pit": "PASS",
                "data_quality": "PASS",
                "strategy_boundary": "STOPPED",
            },
            "comparison": self._comparison(experiment, decision_metrics),
            "product": self._public_backtest_product(product),
            "warnings": warnings,
            "provenance": {
                "reproducible": True,
                "reference": f"experiment:{experiment['experiment_id']}",
            },
        }

    def _factor_pack_research_result(
        self,
        experiment: Mapping[str, Any],
        product: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate = dict(experiment.get("candidate") or {})
        expected = FactorPackRegistry.require(
            _clean(dict(candidate.get("factor_pack") or {}).get("pack_id"))
        )
        results = [dict(item) for item in list(dict(product).get("results") or [])]
        names = [_clean(dict(item.get("factor") or {}).get("name")) for item in results]
        if len(results) != expected.factor_count or set(expected.excluded_factors).intersection(names):
            raise ResearchSemanticError(
                "GOAL_CONFORMANCE_FAILED",
                "Factor Pack 的实际成员与 Research Contract 不一致。",
                context={
                    "expected_factor_count": expected.factor_count,
                    "actual_factor_count": len(results),
                    "excluded_factors_found": sorted(set(expected.excluded_factors).intersection(names)),
                },
            )
        evaluation = dict(candidate.get("evaluation") or {})
        primary_metric = _clean(evaluation.get("primary_metric") or "rank_ic")
        horizons = list(evaluation.get("horizons") or [])
        horizon = str(horizons[0] if horizons else 1)
        scored: list[dict[str, Any]] = []
        warning_count = 0
        for item in results:
            summary = dict(item.get("summary") or {})
            metric = dict(dict(summary.get(primary_metric) or {}).get(horizon) or {})
            value = metric.get("mean")
            diagnostics = list(summary.get("diagnostics") or [])
            warning_count += sum(
                1 for diagnostic in diagnostics
                if _clean(dict(diagnostic or {}).get("severity")).upper() in {"WARNING", "ERROR"}
            )
            scored.append({
                "factor_name": _clean(dict(item.get("factor") or {}).get("name")),
                "member_index": dict(item.get("factor") or {}).get("member_index"),
                primary_metric: value,
                "observations": metric.get("count"),
                "coverage": summary.get("coverage"),
            })
        numeric = [float(item[primary_metric]) for item in scored if isinstance(item.get(primary_metric), (int, float))]
        ranked = sorted(
            (item for item in scored if isinstance(item.get(primary_metric), (int, float))),
            key=lambda item: float(item[primary_metric]),
            reverse=True,
        )
        decision_metrics = {
            "factor_count": expected.factor_count,
            "evaluated_factor_count": len(results),
            f"median_{primary_metric}": statistics.median(numeric) if numeric else None,
            f"mean_{primary_metric}": statistics.fmean(numeric) if numeric else None,
            f"positive_{primary_metric}_ratio": (
                sum(value > 0 for value in numeric) / len(numeric) if numeric else None
            ),
            "horizon_bars": int(horizon),
        }
        return {
            "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "status": "COMPLETE",
            "product_type": "FACTOR_EVALUATION",
            "research_object": "FACTOR_PACK",
            "goal_conformance": "PASS",
            "decision_metrics": decision_metrics,
            "research_diagnostics": {
                "warning_count": warning_count,
                "factors_with_primary_metric": len(numeric),
                "primary_metric": primary_metric,
                "horizon_bars": int(horizon),
            },
            "gates": {
                "execution": "PASS",
                "pit": "PASS",
                "data_quality": "PASS",
                "factor_pack_identity": "PASS",
            },
            "comparison": self._comparison(experiment, decision_metrics),
            "product": {
                "schema_version": dict(product).get("schema_version"),
                "factor_pack": {
                    "pack_id": expected.pack_id,
                    "version": expected.version,
                    "display_name": expected.display_name,
                    "engine": expected.engine,
                    "factor_count": expected.factor_count,
                    "compatibility_mode": expected.compatibility_mode,
                    "excluded_factors": list(expected.excluded_factors),
                    "is_standard_alpha158": expected.is_standard_alpha158,
                },
                "top_factors": ranked[:10],
                "bottom_factors": list(reversed(ranked[-10:])),
            },
            "warnings": ([{
                "code": "FACTOR_PACK_MEMBER_WARNINGS",
                "count": warning_count,
            }] if warning_count else []),
            "provenance": {
                "reproducible": True,
                "reference": f"experiment:{experiment['experiment_id']}",
            },
        }

    def _comparison(
        self,
        experiment: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT experiment_id, result_json FROM research_experiments "
                "WHERE session_id=? AND contract_id=? AND experiment_id<>? "
                "AND status='COMPLETE' AND decision='KEEP' "
                "ORDER BY updated_at DESC LIMIT 1",
                (
                    _clean(experiment.get("session_id")),
                    _clean(experiment.get("contract_id")),
                    _clean(experiment.get("experiment_id")),
                ),
            ).fetchone()
        control_id = str(row[0]) if row else ""
        control_metrics: dict[str, Any] = {}
        if row:
            try:
                control_metrics = dict(json.loads(row[1] or "{}").get("decision_metrics") or {})
            except (TypeError, ValueError, json.JSONDecodeError):
                control_metrics = {}
        deltas: dict[str, float] = {}
        for key, value in candidate_metrics.items():
            control = control_metrics.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(control, (int, float)) and not isinstance(control, bool):
                deltas[key] = float(value) - float(control)
        return {
            "control_experiment_id": control_id,
            "candidate_experiment_id": experiment["experiment_id"],
            "control_metrics": control_metrics,
            "improvement": deltas,
        }

    @staticmethod
    def _public_factor_product(product: Mapping[str, Any]) -> dict[str, Any]:
        """Keep typed research evidence while hiding internal lineage identities."""
        public_results = []
        for raw in list(dict(product).get("results") or []):
            item = dict(raw or {})
            factor = dict(item.get("factor") or {})
            public_results.append({
                "factor": {
                    key: factor.get(key)
                    for key in ("name", "version", "dimension", "frequency", "output_unit")
                    if factor.get(key) not in (None, "")
                },
                "evaluation_spec": dict(item.get("evaluation_spec") or {}),
                "coverage": dict(item.get("coverage") or {}),
                "distribution": dict(item.get("distribution") or {}),
                "predictive_power": list(item.get("predictive_power") or []),
                "quantile_returns": list(item.get("quantile_returns") or []),
                "diagnostics": list(item.get("diagnostics") or []),
            })
        return {
            "schema_version": dict(product).get("schema_version"),
            "run_type": "FACTOR_EVALUATION",
            "results": public_results,
        }

    @staticmethod
    def _public_alpha_product(product: Mapping[str, Any]) -> dict[str, Any]:
        """Expose predictive evidence only; portfolio and lineage remain private."""
        public_results = []
        for raw in list(dict(product).get("results") or []):
            item = dict(raw or {})
            alpha = dict(item.get("alpha") or {})
            factor_inputs = []
            for component in list(item.get("factor_inputs") or []):
                component = dict(component or {})
                factor_inputs.append({
                    key: component.get(key)
                    for key in ("name", "version", "weight", "transform", "ascending")
                    if component.get(key) not in (None, "")
                })
            public_results.append({
                "alpha": {
                    key: alpha.get(key)
                    for key in (
                        "name", "version", "engine_version", "output_scale",
                        "minimum_coverage", "minimum_cross_section_size",
                    )
                    if alpha.get(key) not in (None, "")
                },
                "factor_inputs": factor_inputs,
                "signal_summary": dict(item.get("signal_summary") or {}),
                "ic": dict(item.get("ic") or {}),
                "rank_ic": dict(item.get("rank_ic") or {}),
                "holding_period_decay": dict(item.get("holding_period_decay") or {}),
                "regime_performance": dict(item.get("regime_performance") or {}),
                "diagnostics": list(item.get("diagnostics") or []),
            })
        return {
            "schema_version": dict(product).get("schema_version"),
            "run_type": "ALPHA_EVALUATION",
            "results": public_results,
        }

    @staticmethod
    def _public_backtest_product(product: Mapping[str, Any]) -> dict[str, Any]:
        """Expose portfolio evidence while keeping frozen execution lineage private."""
        public_results = []
        for raw in list(dict(product).get("results") or []):
            item = dict(raw or {})
            alpha = dict(item.get("alpha") or {})
            factor_inputs = []
            for raw_component in list(item.get("factor_inputs") or []):
                component = dict(raw_component or {})
                factor_inputs.append({
                    key: component.get(key)
                    for key in ("name", "version", "weight", "transform", "ascending")
                    if component.get(key) not in (None, "")
                })
            public_results.append({
                "alpha": {
                    key: alpha.get(key)
                    for key in ("name", "version", "output_scale", "minimum_coverage")
                    if alpha.get(key) not in (None, "")
                },
                "factor_inputs": factor_inputs,
                "signal_summary": dict(item.get("signal_summary") or {}),
                "performance": {
                    key: dict(item.get("performance") or {}).get(key)
                    for key in (
                        "initial_cash", "final_equity", "total_return", "annualized_return",
                        "volatility", "sharpe", "max_drawdown", "max_underwater_bars",
                        "fees", "slippage_cost", "turnover", "trade_count", "rebalance_count",
                        "bar_count", "instrument_count", "average_exposure", "average_cash_ratio",
                        "benchmark_total_return", "excess_total_return", "information_ratio",
                    )
                    if dict(item.get("performance") or {}).get(key) is not None
                },
                "costs": dict(item.get("costs") or {}),
                "diagnostics": list(item.get("diagnostics") or []),
            })
        return {
            "schema_version": dict(product).get("schema_version"),
            "run_type": "RESEARCH_BACKTEST",
            "boundary": dict(product).get("boundary") or {},
            "portfolio_rules": dict(product).get("portfolio_rules") or {},
            "execution_assumptions": dict(product).get("execution_assumptions") or {},
            "benchmark_spec": dict(product).get("benchmark_spec") or {},
            "benchmark_status": ResearchExperimentService._public_benchmark_status(
                dict(product).get("benchmark_status") or {}
            ),
            "results": public_results,
        }

    @staticmethod
    def _public_benchmark_status(value: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(value or {})
        comparisons = []
        for raw in list(value.get("comparisons") or []):
            item = dict(raw or {})
            comparisons.append({
                key: item.get(key)
                for key in ("benchmark_total_return", "excess_total_return", "information_ratio")
                if item.get(key) is not None
            })
        return {
            "configured": bool(value.get("configured")),
            "materialized": bool(value.get("materialized")),
            "comparisons": comparisons,
            "warning": _clean(value.get("warning")),
        }

    @staticmethod
    def _clear_previous_experiment_refs(registry: DefinitionRegistry, project_id: str) -> None:
        """Detach prior compiler refs while preserving immutable definitions and Runs."""
        refs = registry.list_project_refs(project_id)
        compiler_refs = [
            (slot_key, dict(ref))
            for slot_key, ref in refs.items()
            if _clean(ref.get("definition_version")).startswith("experiment.")
        ]
        for definition_type in ("ALPHA", "FACTOR"):
            for slot_key, ref in compiler_refs:
                if _clean(ref.get("definition_type")).upper() != definition_type:
                    continue
                registry.remove_project_ref(
                    project_id=project_id,
                    slot_key=slot_key,
                    expected_definition_id=_clean(ref.get("definition_id")),
                )

    @staticmethod
    def _change_set(candidate: Mapping[str, Any]) -> dict[str, Any]:
        if candidate.get("universe_selection"):
            return {"object": "UNIVERSE"}
        if candidate.get("portfolio_spec") or candidate.get("execution_spec"):
            return {"object": "PORTFOLIO_EVIDENCE"}
        if candidate.get("alpha"):
            return {"object": "ALPHA_DEFINITION"}
        if candidate.get("factor_pack"):
            return {"object": "FACTOR_PACK"}
        return {"object": "FACTOR_DEFINITION"}

    def _mark_invalid(self, experiment_id: str, exc: ResearchSemanticError) -> None:
        self._update(
            experiment_id,
            status="INVALID",
            phase="VALIDATION",
            result={
                "schema_version": RESEARCH_RESULT_SCHEMA_VERSION,
                "status": "INVALID",
                "code": exc.code,
                "message": str(exc),
                "context": exc.context,
            },
            completed=True,
        )

    def _mark_system_blocked(
        self,
        experiment_id: str,
        exc: Exception,
        *,
        code: str = "RESEARCH_ORCHESTRATION_BLOCKED",
        public_message: str = "",
    ) -> None:
        issue_id = f"engineering_issue_{uuid.uuid4().hex}"
        print(
            f"[RESEARCH-EXPERIMENT][BLOCKED] experiment={experiment_id} "
            f"issue={issue_id} error={type(exc).__name__}: {_clean(exc)[:1000]}",
            flush=True,
        )
        self._update(
            experiment_id,
            status="SYSTEM_BLOCKED",
            phase="SYSTEM_BLOCKED",
            system_block={
                "code": _clean(code) or "RESEARCH_ORCHESTRATION_BLOCKED",
                "phase": "SYSTEM_BLOCKED",
                "retryable": False,
                "public_message": _clean(public_message)
                or "DataTube 无法完成本次实验；研究边界和 Candidate 已保留，没有产生研究结论。",
                "issue_id": issue_id,
                "error_type": type(exc).__name__,
                "internal_error": _clean(exc)[:1000],
            },
            completed=True,
        )

    def _update(
        self,
        experiment_id: str,
        *,
        status: str,
        phase: str,
        execution_plan: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        result: Mapping[str, Any] | None = None,
        system_block: Mapping[str, Any] | None = None,
        completed: bool = False,
    ) -> None:
        if status not in EXPERIMENT_STATES:
            raise ValueError(f"unsupported Experiment status: {status}")
        now = utc_now()
        fields = ["status=?", "phase=?", "updated_at=?"]
        values: list[Any] = [status, phase, now]
        for column, value in (
            ("execution_plan_json", execution_plan),
            ("result_json", result),
            ("system_block_json", system_block),
        ):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(json_dumps(dict(value)))
        if run_id is not None:
            fields.append("run_id=?")
            values.append(_clean(run_id))
        if completed:
            fields.append("completed_at=?")
            values.append(now)
        values.append(_clean(experiment_id))
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                f"UPDATE research_experiments SET {', '.join(fields)} WHERE experiment_id=?",
                values,
            )
            session_row = conn.execute(
                "SELECT session_id FROM research_experiments WHERE experiment_id=?",
                (_clean(experiment_id),),
            ).fetchone()
        if session_row:
            from .research_agent_session import ResearchAgentSessionService

            session_status = {
                "ACCEPTED": "PLANNING",
                "COMPILING": "BUILDING",
                "PREPARING_DATA": "PREPARING_DATA",
                "QUEUED": "RUNNING",
                "RUNNING": "RUNNING",
                "EVALUATING": "EVALUATING",
                "COMPLETE": "ITERATING",
                "INVALID": "ITERATING",
                "SYSTEM_BLOCKED": "BLOCKED",
                "FAILED": "FAILED",
                "CANCELLED": "CANCELLED",
            }[status]
            try:
                ResearchAgentSessionService(self.store).set_status(
                    str(session_row[0]),
                    session_status,
                    message=f"Experiment {status}",
                    payload={"experiment_id": _clean(experiment_id), "phase": phase},
                )
            except ValueError:
                # A concurrent human pause/cancel or terminal Session wins over
                # background progress; the Experiment record remains durable.
                pass

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for source, target in (
            ("candidate_json", "candidate"),
            ("execution_plan_json", "execution_plan"),
            ("result_json", "result"),
            ("system_block_json", "system_block_internal"),
            ("learning_json", "learning"),
        ):
            try:
                result[target] = json.loads(result.pop(source) or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                result[target] = {}
        return result


__all__ = [
    "EXPERIMENT_STATES",
    "RESEARCH_DECISIONS",
    "ResearchExperimentService",
]
