from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .research_agent_authorization import DEFAULT_RESEARCH_OPERATIONS
from .research_context_resolver import ResearchContextResolver
from .research_control_plane import ResearchControlPlane
from .research_semantics import (
    ResearchContractService,
    ResearchSemanticError,
    build_research_contract,
    infer_asset_class,
    infer_research_stop_at,
)
from .store import DataPlatformStore, json_dumps, utc_now


SESSION_STATES = {
    "BRIEFING",
    "PLANNING",
    "BUILDING",
    "PREPARING_DATA",
    "PREVIEWING",
    "RUNNING",
    "EVALUATING",
    "ITERATING",
    "NEED_HUMAN",
    "PAUSED",
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}

TERMINAL_SESSION_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
NEED_HUMAN_REASONS = {
    "AMBIGUOUS_INTENT",
    "AMBIGUOUS_CONTEXT",
    "MATERIAL_SCOPE_CHANGE",
    "LIMIT_EXTENSION_REQUIRED",
    "CROSS_RESEARCH_BOUNDARY",
}

ITERATION_STATES = {"PLANNED", "RUNNING", "EVALUATING", "COMPLETED", "FAILED", "CANCELLED"}
ITERATION_DECISIONS = {"KEEP", "REJECT", "INCONCLUSIVE", "NEED_HUMAN"}

DEFAULT_SESSION_POLICY = {
    "policy_version": "research_session.v1",
    "research_only": True,
    "max_runs": 10,
    "max_runtime_seconds": 1800,
    "max_download_bytes": 5 * 1024 * 1024 * 1024,
    "forbidden_operations": [
        "GLOBAL_PUBLISH",
        "LIVE_STRATEGY_CREATE",
        "LIVE_TRADING",
        "HISTORY_DELETE",
    ],
}

_START_LOCK = threading.Lock()
_BRIEF_FIELDS = {
    "objective",
    "goal",
    "instrument_scope",
    "provider",
    "frequency",
    "research_period",
    "evaluation_metrics",
    "constraints",
    "benchmark",
    "universe_policy",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def normalize_research_brief(payload: dict[str, Any]) -> dict[str, Any]:
    aligned = payload.get("aligned_research_intent") or payload.get("alignment") or {}
    aligned = dict(aligned) if isinstance(aligned, dict) else {}
    aligned_scope = dict(aligned.get("scope") or {})
    objective = _clean(
        payload.get("objective") or payload.get("goal") or aligned.get("question")
    )
    if not objective:
        raise ValueError("objective is required")
    instrument_scope = payload.get("instrument_scope") or aligned_scope.get("instrument_scope")
    if not instrument_scope:
        objective_upper = objective.upper()
        for symbol in ("BTC", "ETH", "SOL"):
            if symbol in objective_upper:
                instrument_scope = f"{symbol}USDT spot"
                break
        else:
            instrument_scope = ""
    asset_class = infer_asset_class(objective, instrument_scope, _clean(payload.get("provider")))
    scope_text = json_dumps(instrument_scope).upper()
    if _clean(payload.get("provider")):
        provider = _clean(payload.get("provider")).upper()
    elif "CRSP" in scope_text:
        provider = "CRSP"
    elif asset_class == "US_EQUITY":
        provider = "OPENBB"
    elif asset_class == "POLYMARKET_BINARY":
        provider = "POLYMARKET"
    elif asset_class == "CRYPTO_SPOT":
        provider = "BINANCE"
    else:
        provider = ""
    supplied_evaluation = dict(dict(payload.get("research_contract") or {}).get("evaluation") or {})
    explicit_stop = _clean(
        aligned.get("stop_at")
        or payload.get("stop_at")
        or dict(payload.get("research_contract") or {}).get("stop_at")
    ).upper()
    stop_at = {
        "FACTOR_EVALUATION": "FACTOR",
        "ALPHA_EVALUATION": "ALPHA",
        "RESEARCH_BACKTEST": "PORTFOLIO_EVIDENCE",
        "UNIVERSE_DESIGN": "UNIVERSE",
        "STRATEGY": "PORTFOLIO_EVIDENCE",
        "PORTFOLIO": "PORTFOLIO_EVIDENCE",
    }.get(explicit_stop, explicit_stop)
    if not stop_at:
        stop_at = infer_research_stop_at(objective, _clean(supplied_evaluation.get("run_type")))
    if stop_at == "UNIVERSE":
        default_metrics = ["eligible_count", "coverage"]
    elif stop_at in {"FACTOR", "ALPHA"}:
        default_metrics = ["rank_ic" if asset_class == "US_EQUITY" else "ic"]
    elif stop_at == "PORTFOLIO_EVIDENCE":
        default_metrics = [
            "annualized_return", "max_drawdown", "sharpe_ratio",
            "turnover", "cost_adjusted_return",
        ]
    else:
        default_metrics = []
    metrics = payload.get("evaluation_metrics") or default_metrics
    period = (
        payload.get("research_period")
        or aligned_scope.get("research_period")
        or {"start": "2021-01-01", "end": _today()}
    )
    if isinstance(period, str):
        period = {"label": period}
    constraints = dict(payload.get("constraints") or aligned_scope.get("constraints") or {})
    if stop_at == "PORTFOLIO_EVIDENCE":
        constraints.setdefault("long_only", True)
        constraints.setdefault("leverage", False)
        constraints.setdefault("max_turnover", None)
    return {
        "objective": objective,
        "instrument_scope": instrument_scope,
        "provider": provider,
        "frequency": _clean(
            payload.get("frequency")
            or aligned_scope.get("frequency")
            or ("1d" if asset_class == "US_EQUITY" else "1h")
        ),
        "research_period": period,
        "evaluation_metrics": list(metrics),
        "constraints": constraints,
        "benchmark": _clean(
            payload.get("benchmark")
            or ("buy_and_hold" if stop_at == "PORTFOLIO_EVIDENCE" else "NONE")
        ),
        "universe_policy": dict(
            payload.get("universe_policy") or aligned_scope.get("universe_policy") or {}
        ),
        "iteration_budget": {
            "max_runs": DEFAULT_SESSION_POLICY["max_runs"],
            "max_runtime_minutes": DEFAULT_SESSION_POLICY["max_runtime_seconds"] // 60,
        },
    }


class ResearchAgentSessionService:
    """Persistent START/RESUME research sessions and explainable iterations."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.control = ResearchControlPlane(store)
        self.resolver = ResearchContextResolver(store)

    @staticmethod
    def _semantic_payload(
        payload: dict[str, Any],
        brief: dict[str, Any],
        *,
        require_alignment: bool,
    ) -> dict[str, Any]:
        if require_alignment or payload.get("aligned_research_intent") or payload.get("alignment"):
            return payload
        supplied_contract = dict(payload.get("research_contract") or {})
        if (
            payload.get("stop_at")
            or supplied_contract.get("stop_at")
            or dict(supplied_contract.get("evaluation") or {}).get("run_type")
        ):
            return payload
        # Compatibility for the legacy /api/agent/research/sessions surface.
        # The researcher facade never takes this branch. Existing callers may
        # still create a generic Session from a goal while they migrate to
        # ALIGN -> START.
        adapted = dict(payload)
        adapted["aligned_research_intent"] = {
            "question": brief["objective"],
            "stop_at": "FACTOR",
            "evidence_profile": "STANDARD",
            "assumptions": ["Legacy Session compatibility: implicit Factor stopping point"],
            "out_of_scope": ["Alpha construction", "portfolio backtest", "strategy creation"],
        }
        return adapted

    def start(
        self,
        payload: dict[str, Any],
        *,
        created_by: str = "local_user",
        require_alignment: bool = False,
    ) -> dict[str, Any]:
        brief = normalize_research_brief(payload)
        semantic_payload = self._semantic_payload(
            payload, brief, require_alignment=require_alignment
        )
        # Validate the user-visible research boundary before creating any
        # Project, internal authorization, or audit IR. Missing Universe choices
        # are research questions for the Agent and user, not backend defaults.
        build_research_contract(brief, semantic_payload)
        title = _clean(payload.get("title")) or brief["objective"][:96]
        idempotency_key = self._start_idempotency_key(payload, brief, title, created_by)
        with _START_LOCK:
            existing = self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if dict(existing.get("brief") or {}) != brief:
                    raise ValueError(
                        "RESEARCH_IDEMPOTENCY_CONFLICT: idempotency_key was already used "
                        "for a different Research Brief"
                    )
                grant = self.control.get_grant(_clean(existing.get("internal_grant_id")))
                ResearchContractService(self.store).ensure_for_session(
                    existing["session_id"], existing["project_id"], existing["brief"], semantic_payload
                )
                existing = self.get(existing["session_id"]) or existing
                existing["resolved_grant_scope"] = dict((grant or {}).get("scope") or {})
                existing["idempotency_reused"] = True
                return existing

            project = self.control.create_project(title=title, objective=brief["objective"], created_by=created_by)
            context = self.resolver.resolve("PROJECT", project["project_id"])
            grant = self._create_internal_research_budget(project["project_id"], brief, created_by=created_by)
            session = self._insert_session(
                entry_mode="START",
                project_id=project["project_id"],
                objective=brief["objective"],
                brief=brief,
                context=context,
                internal_grant_id=grant["grant_id"],
                idempotency_key=idempotency_key,
                created_by=created_by,
            )
            ResearchContractService(self.store).ensure_for_session(
                session["session_id"], project["project_id"], brief, semantic_payload
            )
            session = self.get(session["session_id"]) or session
        # Echo back the resolved grant scope so agents see exactly what was
        # authorized; this makes silent truncation or scope mismatches visible
        # at START instead of three steps later at write time.
        session["resolved_grant_scope"] = dict(grant.get("scope") or {})
        session["idempotency_reused"] = False
        return session

    def resume(
        self,
        anchor_type: str,
        anchor_id: str,
        payload: dict[str, Any] | None = None,
        *,
        created_by: str = "local_user",
        require_alignment: bool = False,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        context = self.resolver.resolve(anchor_type, anchor_id)
        resolution_status = _clean(context.get("resolution_status")).upper()
        if resolution_status == "NOT_FOUND":
            raise ResearchSemanticError(
                "RESEARCH_RESUME_ANCHOR_NOT_FOUND",
                f"Research resume anchor not found: {_clean(anchor_type).upper()}:{_clean(anchor_id)}",
                context={
                    "anchor_type": _clean(anchor_type).upper(),
                    "anchor_id": _clean(anchor_id),
                },
            )
        if resolution_status != "RESOLVED" and not list(context.get("candidates") or []):
            raise ResearchSemanticError(
                "RESEARCH_RESUME_CONTEXT_UNRESOLVABLE",
                "Research resume anchor does not identify a selectable Research Project",
                context=context,
            )
        project_id = _clean(context.get("project_id"))
        base_brief, resume_brief_source = self._resume_base_brief(
            anchor_type, anchor_id, project_id
        )
        merged_brief = dict(base_brief)
        for key in _BRIEF_FIELDS:
            if key in payload and payload.get(key) is not None:
                merged_brief[key] = payload[key]
        objective = _clean(merged_brief.get("objective") or merged_brief.get("goal"))
        if not objective:
            objective = _clean((context.get("project") or {}).get("objective")) or f"Resume research from {anchor_type}:{anchor_id}"
        merged_brief["objective"] = objective
        brief = normalize_research_brief(merged_brief)
        semantic_payload = self._semantic_payload(
            payload, brief, require_alignment=require_alignment
        )
        if context.get("resolution_status") != "RESOLVED":
            return self._insert_session(
                entry_mode="RESUME",
                project_id=None,
                objective=objective,
                brief=brief,
                context=context,
                anchor_type=anchor_type,
                anchor_id=anchor_id,
                status="NEED_HUMAN",
                pending_question={
                    "reason_code": "AMBIGUOUS_CONTEXT",
                    "question": "这个锚点无法唯一确定研究项目，请选择要继续的项目。",
                    "candidates": context.get("candidates") or [],
                },
                created_by=created_by,
            )
        build_research_contract(brief, semantic_payload)
        grant = self._create_internal_research_budget(project_id, brief, created_by=created_by)
        baseline = _clean(context.get("baseline_run_id"))
        session = self._insert_session(
            entry_mode="RESUME",
            project_id=project_id,
            objective=brief["objective"],
            brief=brief,
            context=context,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            original_baseline_run_id=baseline,
            current_branch_head_run_id=baseline,
            internal_grant_id=grant["grant_id"],
            created_by=created_by,
        )
        ResearchContractService(self.store).ensure_for_session(
            session["session_id"], project_id, brief, semantic_payload
        )
        session = self.get(session["session_id"]) or session
        session["resolved_grant_scope"] = dict(grant.get("scope") or {})
        session["resume_brief_source"] = resume_brief_source
        return session

    def get(self, session_id: str, *, include_events: bool = True, include_iterations: bool = True) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_agent_sessions WHERE session_id=?", (_clean(session_id),)).fetchone()
            if not row:
                return None
            result = self._session_row(row)
            result["usage"] = self._usage_for_grant_conn(conn, result.get("internal_grant_id"))
            if include_iterations:
                iterations = conn.execute(
                    "SELECT * FROM research_agent_iterations WHERE session_id=? ORDER BY sequence",
                    (_clean(session_id),),
                ).fetchall()
                result["iterations"] = [self._iteration_row(item) for item in iterations]
            if include_events:
                events = conn.execute(
                    "SELECT * FROM research_agent_session_events WHERE session_id=? ORDER BY created_at, event_id",
                    (_clean(session_id),),
                ).fetchall()
                result["events"] = [self._event_row(item) for item in events]
        project_id = _clean(result.get("project_id"))
        if project_id and _clean(result.get("resolution_status")).upper() == "RESOLVED":
            latest_context = self.resolver.resolve("PROJECT", project_id)
            if latest_context.get("resolution_status") == "RESOLVED":
                result["context"] = latest_context
        contract = ResearchContractService(self.store).active_for_session(session_id)
        if contract:
            result["research_contract"] = contract
        return result

    def _get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT session_id FROM research_agent_sessions WHERE idempotency_key=?",
                (_clean(idempotency_key),),
            ).fetchone()
        return self.get(str(row[0])) if row else None

    @staticmethod
    def _start_idempotency_key(
        payload: dict[str, Any], brief: dict[str, Any], title: str, created_by: str
    ) -> str:
        explicit = _clean(payload.get("idempotency_key"))
        if explicit:
            return f"research-start:{_clean(created_by) or 'local_user'}:{explicit}"
        fingerprint = hashlib.sha256(
            json_dumps(
                {
                    "brief": brief,
                    "created_by": _clean(created_by) or "local_user",
                    "title": title,
                }
            ).encode("utf-8")
        ).hexdigest()
        return f"research-start:auto:{fingerprint}"

    def _resume_base_brief(
        self, anchor_type: str, anchor_id: str, project_id: str
    ) -> tuple[dict[str, Any], str]:
        if _clean(anchor_type).upper() == "SESSION":
            anchored = self.get(anchor_id, include_events=False, include_iterations=False)
            if anchored is not None:
                brief = dict(anchored.get("brief") or {})
                if brief:
                    return brief, "ANCHOR_SESSION"
        if not project_id:
            return {}, "REQUEST_DEFAULTS"
        project = self.control.get_project(project_id) or {}
        current_version = int(project.get("current_plan_version") or 0)
        plans = self.control.list_plans(project_id)
        candidates = [item for item in plans if int(item.get("plan_version") or 0) == current_version]
        candidates.sort(
            key=lambda item: (
                _clean(item.get("status")).upper() == "APPROVED",
                _clean(item.get("plan_stage")).upper() in {"APPROVED", "RESOLVED"},
            ),
            reverse=True,
        )
        for item in candidates:
            brief = dict((item.get("plan") or {}).get("research_brief") or {})
            if brief:
                if self._looks_like_legacy_resume_default_drift(brief):
                    recovered = self._recover_start_brief(project_id)
                    if recovered:
                        return recovered, "START_SESSION_RECOVERY"
                return brief, "PROJECT_PLAN"
        recovered = self._recover_start_brief(project_id)
        if recovered:
            return recovered, "START_SESSION_RECOVERY"
        return {}, "REQUEST_DEFAULTS"

    @staticmethod
    def _looks_like_legacy_resume_default_drift(brief: dict[str, Any]) -> bool:
        scope = brief.get("instrument_scope")
        has_scope = bool(scope) if isinstance(scope, (list, tuple, set, dict)) else bool(_clean(scope))
        return (
            not has_scope
            and _clean(brief.get("provider") or "BINANCE").upper() == "BINANCE"
            and _clean(brief.get("frequency") or "1h").lower() == "1h"
        )

    def _recover_start_brief(self, project_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT brief_json
                FROM research_agent_sessions
                WHERE project_id=? AND entry_mode='START'
                ORDER BY created_at ASC, session_id ASC
                """,
                (_clean(project_id),),
            ).fetchall()
        for row in rows:
            try:
                candidate = json.loads(str(row["brief_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                isinstance(candidate, dict)
                and candidate
                and not self._looks_like_legacy_resume_default_drift(candidate)
            ):
                return candidate
        return {}

    def list(self, *, project_id: str = "", status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if _clean(project_id):
            clauses.append("project_id=?")
            params.append(_clean(project_id))
        if _clean(status):
            clauses.append("status=?")
            params.append(_clean(status).upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_agent_sessions{where} ORDER BY updated_at DESC LIMIT ?", params
            ).fetchall()
            result = []
            for row in rows:
                item = self._session_row(row)
                item["usage"] = self._usage_for_grant_conn(conn, item.get("internal_grant_id"))
                result.append(item)
        return result

    def set_status(self, session_id: str, status: str, *, message: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        status = _clean(status).upper()
        if status not in SESSION_STATES:
            raise ValueError(f"unsupported research session status: {status}")
        if status == "NEED_HUMAN":
            raise ValueError("use need_human with a stable reason code and question")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            current = conn.execute(
                "SELECT status, internal_grant_id FROM research_agent_sessions WHERE session_id=?", (_clean(session_id),)
            ).fetchone()
            if not current:
                raise ValueError("research session not found")
            if str(current["status"]) in TERMINAL_SESSION_STATES:
                raise ValueError("terminal research session cannot change status")
            resume_state = str(current["status"]) if status in {"PAUSED", "NEED_HUMAN"} else ""
            conn.execute(
                "UPDATE research_agent_sessions SET status=?, resume_state=?, updated_at=? WHERE session_id=?",
                (status, resume_state, now, _clean(session_id)),
            )
            self._insert_event_conn(conn, session_id, "STATE_CHANGED", status, message, payload or {})
        if _clean(current["internal_grant_id"]) and status == "PAUSED":
            self.control.set_grant_agent_state(str(current["internal_grant_id"]), paused=True, actor_type="human")
        return self.get(session_id)  # type: ignore[return-value]

    def continue_session(self, session_id: str) -> dict[str, Any]:
        current = self.get(session_id, include_events=False, include_iterations=False)
        if not current:
            raise ValueError("research session not found")
        if current["status"] not in {"PAUSED", "NEED_HUMAN", "BLOCKED"}:
            return current
        next_state = _clean(current.get("resume_state")) or "PLANNING"
        if next_state in {"PAUSED", "NEED_HUMAN", "BLOCKED"}:
            next_state = "PLANNING"
        grant_id = _clean(current.get("internal_grant_id"))
        if grant_id:
            self.control.set_grant_agent_state(grant_id, paused=False, actor_type="human")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_agent_sessions SET status=?, pending_question_json='{}', updated_at=? WHERE session_id=?",
                (next_state, now, _clean(session_id)),
            )
            self._insert_event_conn(conn, session_id, "SESSION_CONTINUED", next_state, "研究继续执行", {})
        return self.get(session_id)  # type: ignore[return-value]

    def need_human(self, session_id: str, *, reason_code: str, question: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        reason_code = _clean(reason_code).upper()
        if reason_code not in NEED_HUMAN_REASONS:
            raise ValueError("reason_code is not a permitted NEED_HUMAN gate")
        question_payload = {"reason_code": reason_code, "question": _clean(question), "context": context or {}}
        if not question_payload["question"]:
            raise ValueError("question is required")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT status FROM research_agent_sessions WHERE session_id=?", (_clean(session_id),)).fetchone()
            if not row:
                raise ResearchSemanticError(
                    "RESEARCH_SESSION_NOT_FOUND", "Research Session not found"
                )
            if str(row["status"]).upper() in TERMINAL_SESSION_STATES:
                raise ResearchSemanticError(
                    "RESEARCH_SESSION_TERMINAL",
                    "A terminal Research Session cannot request human input",
                    context={"session_id": _clean(session_id), "status": str(row["status"])},
                )
            conn.execute(
                "UPDATE research_agent_sessions SET status='NEED_HUMAN', resume_state=?, pending_question_json=?, updated_at=? WHERE session_id=?",
                (str(row["status"]), json_dumps(question_payload), now, _clean(session_id)),
            )
            self._insert_event_conn(conn, session_id, "NEED_HUMAN", "NEED_HUMAN", question_payload["question"], question_payload)
        return self.get(session_id)  # type: ignore[return-value]

    def answer(self, session_id: str, answer: Any) -> dict[str, Any]:
        current = self.get(session_id, include_events=False, include_iterations=False)
        if not current:
            raise ResearchSemanticError(
                "RESEARCH_SESSION_NOT_FOUND", "Research Session not found"
            )
        if current["status"] != "NEED_HUMAN":
            raise ResearchSemanticError(
                "RESEARCH_SESSION_NOT_WAITING_FOR_INPUT",
                "Research Session is not waiting for human input",
                context={"session_id": _clean(session_id), "status": current["status"]},
            )
        question = dict(current.get("pending_question") or {})
        context_update: dict[str, Any] | None = None
        internal_grant_id = _clean(current.get("internal_grant_id"))
        project_id = _clean(current.get("project_id"))
        if _clean(question.get("reason_code")).upper() == "AMBIGUOUS_CONTEXT":
            selected_project_id = _clean(answer.get("project_id")) if isinstance(answer, dict) else _clean(answer)
            candidates = {
                _clean(item.get("project_id"))
                for item in question.get("candidates") or []
                if isinstance(item, dict) and _clean(item.get("project_id"))
            }
            if candidates and selected_project_id not in candidates:
                raise ResearchSemanticError(
                    "RESEARCH_ANSWER_PROJECT_INVALID",
                    "Answer must select one of the candidate project_id values",
                    context={"candidate_project_ids": sorted(candidates)},
                )
            context_update = self.resolver.resolve("PROJECT", selected_project_id)
            if context_update.get("resolution_status") != "RESOLVED":
                raise ResearchSemanticError(
                    "RESEARCH_ANSWER_PROJECT_NOT_FOUND",
                    "Answer does not identify an existing Research Project",
                    context={"project_id": selected_project_id},
                )
            project_id = selected_project_id
            internal = self._create_internal_research_budget(
                project_id, dict(current.get("brief") or {}), created_by="local_user"
            )
            internal_grant_id = str(internal["grant_id"])
        now = utc_now()
        resume_state = _clean(current.get("resume_state")) or "PLANNING"
        with self.store.transaction(immediate=True) as conn:
            if context_update is not None:
                baseline = _clean(context_update.get("baseline_run_id"))
                conn.execute(
                    """
                    UPDATE research_agent_sessions
                    SET project_id=?, context_json=?, resolution_status='RESOLVED',
                        original_baseline_run_id=?, current_branch_head_run_id=?,
                        internal_grant_id=?, status=?, pending_question_json='{}', updated_at=?
                    WHERE session_id=?
                    """,
                    (
                        project_id, json_dumps(context_update), baseline, baseline,
                        internal_grant_id, resume_state, now, _clean(session_id),
                    ),
                )
            else:
                conn.execute(
                    "UPDATE research_agent_sessions SET status=?, pending_question_json='{}', updated_at=? WHERE session_id=?",
                    (resume_state, now, _clean(session_id)),
                )
            self._insert_event_conn(conn, session_id, "HUMAN_ANSWERED", resume_state, "用户已补充关键信息", {"answer": answer})
        return self.get(session_id)  # type: ignore[return-value]

    def create_iteration(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        hypothesis = payload.get("hypothesis")
        interventions = payload.get("intervention_set") or []
        if not hypothesis or not interventions:
            raise ValueError("hypothesis and intervention_set are required")
        now = utc_now()
        iteration_id = f"iteration_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            session = conn.execute(
                "SELECT current_branch_head_run_id FROM research_agent_sessions WHERE session_id=?", (_clean(session_id),)
            ).fetchone()
            if not session:
                raise ValueError("research session not found")
            sequence = int(conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM research_agent_iterations WHERE session_id=?",
                (_clean(session_id),),
            ).fetchone()[0])
            invalidation = payload.get("invalidation_plan") or self.plan_invalidation(payload.get("change_set") or {})
            conn.execute(
                """
                INSERT INTO research_agent_iterations(
                    iteration_id, session_id, sequence, status, control_run_id,
                    hypothesis_json, intervention_set_json, controlled_variables_json,
                    change_set_json, invalidation_plan_json, started_at
                ) VALUES (?, ?, ?, 'PLANNED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iteration_id, _clean(session_id), sequence, str(session[0] or ""),
                    json_dumps(hypothesis), json_dumps(interventions),
                    json_dumps(payload.get("controlled_variables") or []),
                    json_dumps(payload.get("change_set") or {}), json_dumps(invalidation), now,
                ),
            )
            conn.execute(
                "UPDATE research_agent_sessions SET active_iteration_id=?, status='ITERATING', updated_at=? WHERE session_id=?",
                (iteration_id, now, _clean(session_id)),
            )
            self._insert_event_conn(conn, session_id, "ITERATION_CREATED", "ITERATING", "已创建单一假设实验", {"sequence": sequence}, iteration_id)
        return self.get_iteration(iteration_id)  # type: ignore[return-value]

    def complete_iteration(self, iteration_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        decision = _clean(payload.get("decision")).upper()
        if decision not in ITERATION_DECISIONS:
            raise ValueError("decision must be KEEP, REJECT, INCONCLUSIVE, or NEED_HUMAN")
        now = utc_now()
        candidate_run_id = _clean(payload.get("candidate_run_id"))
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT session_id FROM research_agent_iterations WHERE iteration_id=?", (_clean(iteration_id),)
            ).fetchone()
            if not row:
                raise ValueError("research iteration not found")
            session_id = str(row[0])
            conn.execute(
                """
                UPDATE research_agent_iterations
                SET status='COMPLETED', candidate_run_id=?, metrics_before_json=?, metrics_after_json=?,
                    comparison_json=?, decision=?, decision_reason=?, warnings_json=?, completed_at=?
                WHERE iteration_id=?
                """,
                (
                    candidate_run_id, json_dumps(payload.get("metrics_before") or {}),
                    json_dumps(payload.get("metrics_after") or {}), json_dumps(payload.get("comparison") or {}),
                    decision, _clean(payload.get("decision_reason")), json_dumps(payload.get("warnings") or []),
                    now, _clean(iteration_id),
                ),
            )
            if decision == "KEEP" and candidate_run_id:
                conn.execute(
                    "UPDATE research_agent_sessions SET current_branch_head_run_id=?, active_iteration_id='', status='ITERATING', updated_at=? WHERE session_id=?",
                    (candidate_run_id, now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE research_agent_sessions SET active_iteration_id='', status=?, updated_at=? WHERE session_id=?",
                    ("NEED_HUMAN" if decision == "NEED_HUMAN" else "ITERATING", now, session_id),
                )
            self._insert_event_conn(conn, session_id, "ITERATION_COMPLETED", decision, _clean(payload.get("decision_reason")), {"candidate_run_id": candidate_run_id}, iteration_id)
        return self.get_iteration(iteration_id)  # type: ignore[return-value]

    def get_iteration(self, iteration_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_agent_iterations WHERE iteration_id=?", (_clean(iteration_id),)
            ).fetchone()
        return self._iteration_row(row) if row else None

    @staticmethod
    def plan_invalidation(change_set: dict[str, Any]) -> dict[str, Any]:
        target = _clean(change_set.get("object") or change_set.get("target_type")).upper()
        routes = {
            "EXPLANATION": ([], "NONE"),
            "DISPLAY": ([], "NONE"),
            "TRANSACTION_COST": (["preview", "input_bundle", "run", "metrics"], "PREVIEW"),
            "REBALANCE_FREQUENCY": (["preview", "input_bundle", "run", "metrics"], "PREVIEW"),
            "RESEARCH_PERIOD": (["requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "REQUIREMENTS"),
            "ALPHA_WEIGHT": (["alpha_definition", "requirement_set", "preview", "input_bundle", "run", "metrics"], "ALPHA"),
            "ALPHA_DEFINITION": (["alpha_definition", "requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "ALPHA"),
            "FACTOR_DEFINITION": (["factor_definition", "requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "FACTOR"),
            "FACTOR_FORMULA": (["factor_definition", "requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "FACTOR"),
            "UNIVERSE": (["universe_snapshot", "requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "UNIVERSE"),
            "PROVIDER": (["requirement_set", "prepared_data", "preview", "input_bundle", "run", "metrics"], "REQUIREMENTS"),
        }
        invalidated, start = routes.get(target, (["requirement_set", "preview", "input_bundle", "run", "metrics"], "REQUIREMENTS"))
        return {
            "change_target": target or "UNKNOWN",
            "invalidated": invalidated,
            "possibly_reusable": ["prepared_bars_data"] if target not in {"UNIVERSE", "PROVIDER", "RESEARCH_PERIOD"} else [],
            "execution_start_point": start,
        }

    def _create_internal_research_budget(self, project_id: str, brief: dict[str, Any], *, created_by: str) -> dict[str, Any]:
        project = self.control.get_project(project_id)
        if not project:
            raise ValueError("research project not found")
        plan_payload = {"research_brief": brief, "managed_by": "research_agent_session.v1"}
        intent = self.control.create_plan(project_id=project_id, stage="INTENT", payload=plan_payload, created_by=created_by)
        version = int(intent["plan_version"])
        self.control.create_plan(
            project_id=project_id,
            stage="RESOLVED",
            plan_version=version,
            payload={**plan_payload, "session_policy": DEFAULT_SESSION_POLICY},
            created_by="research_session_service",
        )
        allowed_ids = self._instrument_ids(
            brief.get("instrument_scope"), provider=brief.get("provider") or "BINANCE"
        )
        frequency = _clean(brief.get("frequency") or "1h")
        # Equity instruments only support 1d frequency; fail fast at START with
        # a clear error instead of waiting for data preparation to fail obscurely.
        has_equity = any(iid.upper().startswith("EQUITY:") for iid in allowed_ids)
        if has_equity and frequency.lower() != "1d":
            raise ValueError(
                f"RESEARCH_FREQUENCY_MISMATCH: equity instruments require frequency: 1d; "
                f"received {frequency!r}. Set 'frequency: 1d' in your Research Brief."
            )
        scope = {
            "grant_kind": "PROJECT_RESEARCH",
            "autonomy_level": "FULL_RESEARCH",
            "allowed_operations": list(DEFAULT_RESEARCH_OPERATIONS),
            "allowed_run_types": ["FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST"],
            "allowed_providers": [brief.get("provider") or "BINANCE"],
            "allowed_intervals": [frequency],
            "allowed_instrument_ids": allowed_ids,
            "time_start": self._period_value(brief.get("research_period"), "start"),
            "time_end": self._period_value(brief.get("research_period"), "end"),
            "session_managed": True,
        }
        budgets = {
            "max_backtest_runs": DEFAULT_SESSION_POLICY["max_runs"],
            "max_runtime_seconds": DEFAULT_SESSION_POLICY["max_runtime_seconds"],
            "max_download_bytes": DEFAULT_SESSION_POLICY["max_download_bytes"],
        }
        return self.control.approve_plan(
            project_id=project_id,
            plan_version=version,
            scope=scope,
            budgets=budgets,
            approved_by=created_by or "local_user",
            actor_type="human",
        )

    def _insert_session(
        self,
        *,
        entry_mode: str,
        project_id: str | None,
        objective: str,
        brief: dict[str, Any],
        context: dict[str, Any],
        anchor_type: str = "",
        anchor_id: str = "",
        original_baseline_run_id: str = "",
        current_branch_head_run_id: str = "",
        internal_grant_id: str = "",
        idempotency_key: str = "",
        status: str = "PLANNING",
        pending_question: dict[str, Any] | None = None,
        created_by: str = "local_user",
    ) -> dict[str, Any]:
        session_id = f"research_session_{uuid.uuid4().hex}"
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO research_agent_sessions(
                    session_id, project_id, entry_mode, anchor_type, anchor_id, resolution_status,
                    status, objective, brief_json, context_json, original_baseline_run_id,
                    current_branch_head_run_id, session_policy_json, usage_json,
                    pending_question_json, resume_state, internal_grant_id, created_by,
                    created_at, updated_at, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, project_id, entry_mode, _clean(anchor_type).upper(), _clean(anchor_id),
                    _clean(context.get("resolution_status") or "RESOLVED"), status, objective,
                    json_dumps(brief), json_dumps(context), original_baseline_run_id,
                    current_branch_head_run_id, json_dumps(DEFAULT_SESSION_POLICY),
                    json_dumps({"runs": 0, "runtime_seconds": 0, "download_bytes": 0}),
                    json_dumps(pending_question or {}), "PLANNING" if status == "NEED_HUMAN" else "",
                    internal_grant_id, _clean(created_by) or "local_user", now, now,
                    _clean(idempotency_key),
                ),
            )
            message = "已恢复历史研究上下文" if entry_mode == "RESUME" else "已根据用户目标创建研究草案"
            self._insert_event_conn(conn, session_id, "SESSION_CREATED", status, message, {"entry_mode": entry_mode})
        return self.get(session_id)  # type: ignore[return-value]

    @staticmethod
    def _instrument_ids(value: Any, *, provider: str = "BINANCE") -> list[str]:
        """Parse instrument_scope into fully qualified instrument IDs.

        Accepts:
        - list of strings (each item may have trailing asset-class qualifier)
        - comma-separated string for multi-instrument input
        - single "TICKER spot" convention (space separates ticker from qualifier)

        Returns list of fully qualified IDs like "CRYPTO_SPOT:BINANCE:BTCUSDT".
        """
        if isinstance(value, list):
            items_raw = value
        else:
            raw = _clean(value)
            # Accept comma-separated multi-instrument: "AAPL, MSFT, NVDA"
            if "," in raw:
                items_raw = [part.strip() for part in raw.split(",") if part.strip()]
            else:
                items_raw = [raw] if raw else []
        result: list[str] = []
        for item in items_raw:
            # Strip trailing asset-class qualifier: "BTCUSDT spot" → "BTCUSDT"
            instrument = _clean(item).split(" ", 1)[0].upper()
            if not instrument:
                continue
            if ":" in instrument:
                result.append(instrument)
            elif _clean(provider).upper() == "BINANCE":
                result.append(f"CRYPTO_SPOT:BINANCE:{instrument}")
            else:
                result.append(instrument)
        return result

    @staticmethod
    def _period_value(value: Any, field: str) -> str:
        return _clean(value.get(field)) if isinstance(value, dict) else ""

    @staticmethod
    def _insert_event_conn(
        conn: Any,
        session_id: str,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any],
        iteration_id: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO research_agent_session_events(
                event_id, session_id, iteration_id, event_type, status, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"research_event_{uuid.uuid4().hex}", _clean(session_id), _clean(iteration_id),
                _clean(event_type).upper(), _clean(status).upper(), _clean(message), json_dumps(payload), utc_now(),
            ),
        )

    @staticmethod
    def _session_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for source, target, fallback in (
            ("brief_json", "brief", {}),
            ("context_json", "context", {}),
            ("session_policy_json", "session_policy", {}),
            ("usage_json", "usage", {}),
            ("pending_question_json", "pending_question", {}),
        ):
            result[target] = _loads(result.pop(source), fallback)
        return result

    @staticmethod
    def _iteration_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for source, target, fallback in (
            ("hypothesis_json", "hypothesis", {}),
            ("intervention_set_json", "intervention_set", []),
            ("controlled_variables_json", "controlled_variables", []),
            ("change_set_json", "change_set", {}),
            ("invalidation_plan_json", "invalidation_plan", {}),
            ("metrics_before_json", "metrics_before", {}),
            ("metrics_after_json", "metrics_after", {}),
            ("comparison_json", "comparison", {}),
            ("warnings_json", "warnings", []),
        ):
            result[target] = _loads(result.pop(source), fallback)
        return result

    @staticmethod
    def _event_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _loads(result.pop("payload_json"), {})
        return result

    @staticmethod
    def _usage_for_grant_conn(conn: Any, grant_id: Any) -> dict[str, int]:
        if not _clean(grant_id):
            return {"runs": 0, "runtime_seconds": 0, "download_bytes": 0}
        row = conn.execute(
            """
            SELECT reserved_runs, consumed_runs, reserved_runtime_seconds, consumed_runtime_seconds,
                   reserved_download_bytes, consumed_download_bytes
            FROM approval_budget_counters WHERE grant_id=?
            """,
            (_clean(grant_id),),
        ).fetchone()
        if not row:
            return {"runs": 0, "runtime_seconds": 0, "download_bytes": 0}
        return {
            "runs": int(row["reserved_runs"]) + int(row["consumed_runs"]),
            "runtime_seconds": int(row["reserved_runtime_seconds"]) + int(row["consumed_runtime_seconds"]),
            "download_bytes": int(row["reserved_download_bytes"]) + int(row["consumed_download_bytes"]),
        }
