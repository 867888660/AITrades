from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

from .factor_pack import ALPHA158_NO_VWAP_PACK_ID, FactorPackRegistry
from .store import DataPlatformStore, json_dumps, utc_now


ALIGNED_RESEARCH_INTENT_SCHEMA_VERSION = "aligned-research-intent.v1"
RESEARCH_CONTRACT_SCHEMA_VERSION = "research-contract.v2"
CANDIDATE_SPEC_SCHEMA_VERSION = "candidate-spec.v1"
RESEARCH_RESULT_SCHEMA_VERSION = "research-result.v1"
CONTRACT_STATES = {"DRAFT", "ACTIVE", "SUPERSEDED"}

STOP_AT_TO_RUN_TYPE = {
    "UNIVERSE": "UNIVERSE_DESIGN",
    "FACTOR": "FACTOR_EVALUATION",
    "ALPHA": "ALPHA_EVALUATION",
    "PORTFOLIO_EVIDENCE": "RESEARCH_BACKTEST",
}
RUN_TYPE_TO_STOP_AT = {value: key for key, value in STOP_AT_TO_RUN_TYPE.items()}
RESEARCHER_AVAILABLE_STOP_AT = set(STOP_AT_TO_RUN_TYPE)
EVIDENCE_PROFILES = {"QUICK", "STANDARD", "DEEP"}

_INFRASTRUCTURE_KEYS = {
    "provider",
    "providers",
    "manifest",
    "manifest_id",
    "manifest_ids",
    "requirement",
    "requirements",
    "requirement_set_id",
    "bundle",
    "bundle_id",
    "preview",
    "preview_id",
    "data_source",
    "source_selection_policy",
    "pit_policy",
    "point_in_time_policy",
    "warmup",
    "worker",
    "worker_id",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(value)).encode("utf-8")).hexdigest()


def _scope_items(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    raw = _clean(value)
    if not raw:
        return []
    if "," in raw:
        return [_clean(item) for item in raw.split(",") if _clean(item)]
    return [raw]


def infer_asset_class(objective: str, instrument_scope: Any, provider: str = "") -> str:
    text = f"{_clean(objective)} {' '.join(_scope_items(instrument_scope))} {_clean(provider)}".upper()
    if any(token in text for token in ("POLYMARKET", "预测市场", "预测合约")):
        return "POLYMARKET_BINARY"
    if any(
        token in text
        for token in (
            "US_EQUITY",
            "US EQUITY",
            "US STOCK",
            "美股",
            "美国股票",
            "股票",
            "AAPL",
            "MSFT",
            "NVDA",
            "OPENBB",
            "CRSP",
            "ALPHA158",
        )
    ):
        return "US_EQUITY"
    if any(token in text for token in ("BTC", "ETH", "SOL", "BINANCE", "CRYPTO", "加密")):
        return "CRYPTO_SPOT"
    return ""


def equity_universe_guidance(objective: str) -> dict[str, Any]:
    return {
        "reason_code": "RESEARCH_UNIVERSE_REQUIRED",
        "question": (
            "这项美股研究需要先确定研究股票池。建议使用历史时点可交易的美国普通股，"
            "排除上市不足 12 个月的股票，并先保留全部合格样本，避免在研究开始前"
            "通过股票池筛选引入结果偏差。"
            "你希望采用这个广泛样本，还是只研究大盘股？"
        ),
        "research_context": {"objective": _clean(objective), "asset_class": "US_EQUITY"},
        "recommended": {
            "instrument_scope": "equity:CRSP:ALL",
            "frequency": "1d",
            "universe_policy": {
                "eligibility": {
                    "mode": "HISTORICAL_EQUITY_PIT",
                    "security_types": ["COMMON_STOCK"],
                    "minimum_listing_age_days": 365,
                },
                "selection": {"method": "ALL_ELIGIBLE"},
                "exclusions": [],
            },
        },
        "alternative": {
            "label": "明确列出的大盘股样本",
            "instrument_scope": ["AAPL", "MSFT", "NVDA"],
            "universe_policy": {
                "eligibility": {
                    "mode": "STATIC_LIST",
                    "instrument_scope": ["AAPL", "MSFT", "NVDA"],
                },
                "selection": {"method": "ALL_ELIGIBLE"},
            },
        },
    }


def infer_research_stop_at(objective: str, explicit_run_type: str = "") -> str:
    run_type = _clean(explicit_run_type).upper()
    if run_type:
        return RUN_TYPE_TO_STOP_AT.get(run_type, "")
    text = _clean(objective).upper()
    if any(token in text for token in (
        "PORTFOLIO", "RESEARCH_BACKTEST", "BACKTEST", "STRATEGY", "回测", "策略",
        "能不能赚钱", "收益曲线", "最大回撤", "SHARPE",
    )):
        return "PORTFOLIO_EVIDENCE"
    if any(token in text for token in (
        "ALPHA", "信号组合", "因子组合", "组合因子", "组合信号", "权重", "冗余",
    )):
        return "ALPHA"
    if any(token in text for token in (
        "UNIVERSE", "股票池", "标的池", "选哪些股票", "研究池",
    )):
        return "UNIVERSE"
    if any(token in text for token in (
        "FACTOR", "因子", "动量", "波动", "估值", "质量", "ROE", "PE", "FCF",
        "预测能力", "IC", "ALPHA158",
    )):
        return "FACTOR"
    return ""


def _normalize_stop_at(value: Any) -> str:
    raw = _clean(value).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "FACTOR_EVALUATION": "FACTOR",
        "ALPHA_EVALUATION": "ALPHA",
        "RESEARCH_BACKTEST": "PORTFOLIO_EVIDENCE",
        "PORTFOLIO": "PORTFOLIO_EVIDENCE",
        "STRATEGY": "PORTFOLIO_EVIDENCE",
        "UNIVERSE_DESIGN": "UNIVERSE",
    }
    return aliases.get(raw, raw)


def _default_decision_supported(stop_at: str) -> str:
    return {
        "UNIVERSE": "判断该研究股票池是否足以支持后续因子研究",
        "FACTOR": "判断该因子是否值得进入 Alpha 研究",
        "ALPHA": "判断该预测信号是否值得进入组合研究",
        "PORTFOLIO_EVIDENCE": "判断组合证据是否足以进入显式 Strategy Handoff",
    }.get(stop_at, "明确本轮研究结论支持的下一项决策")


def _default_out_of_scope(stop_at: str) -> list[str]:
    return {
        "UNIVERSE": ["Factor construction", "Alpha construction", "portfolio backtest", "strategy creation"],
        "FACTOR": ["Alpha construction", "portfolio backtest", "strategy creation"],
        "ALPHA": ["portfolio backtest", "strategy creation"],
        "PORTFOLIO_EVIDENCE": ["strategy creation", "virtual trading", "live trading"],
    }.get(stop_at, [])


def align_research_intent(
    brief: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize research meaning without creating Projects, Sessions, or execution IR."""
    payload = dict(payload or {})
    supplied = payload.get("aligned_research_intent") or payload.get("alignment") or {}
    supplied = dict(supplied) if isinstance(supplied, Mapping) else {}
    objective = _clean(
        supplied.get("question")
        or supplied.get("objective")
        or brief.get("objective")
        or brief.get("goal")
    )
    if not objective:
        raise ResearchSemanticError("RESEARCH_OBJECTIVE_REQUIRED", "请先说明这次想研究什么问题。")

    explicit_evaluation = dict(
        supplied.get("evaluation")
        or dict(payload.get("research_contract") or {}).get("evaluation")
        or {}
    )
    stop_at = _normalize_stop_at(
        supplied.get("stop_at")
        or payload.get("stop_at")
        or dict(payload.get("research_contract") or {}).get("stop_at")
    )
    if not stop_at:
        stop_at = infer_research_stop_at(objective, _clean(explicit_evaluation.get("run_type")))

    instrument_scope = supplied.get("instrument_scope", brief.get("instrument_scope"))
    scope_block = dict(supplied.get("scope") or {})
    if "instrument_scope" in scope_block:
        instrument_scope = scope_block.get("instrument_scope")
    asset_class = _clean(
        scope_block.get("asset_class")
        or dict(supplied.get("asset_scope") or {}).get("asset_class")
        or infer_asset_class(objective, instrument_scope, _clean(brief.get("provider")))
    ).upper()
    universe_policy = (
        scope_block.get("universe_policy")
        or supplied.get("universe_policy")
        or payload.get("universe_policy")
        or brief.get("universe_policy")
        or {}
    )
    universe_policy = dict(universe_policy) if isinstance(universe_policy, Mapping) else {}
    research_period = (
        scope_block.get("research_period")
        or supplied.get("research_period")
        or brief.get("research_period")
        or {}
    )
    research_period = dict(research_period) if isinstance(research_period, Mapping) else {"label": research_period}
    frequency = _clean(scope_block.get("frequency") or supplied.get("frequency") or brief.get("frequency"))

    evidence_block = dict(supplied.get("evidence") or {})
    evidence_profile = _clean(
        evidence_block.get("profile")
        or supplied.get("evidence_profile")
        or payload.get("evidence_profile")
        or "STANDARD"
    ).upper()
    if evidence_profile not in EVIDENCE_PROFILES:
        raise ResearchSemanticError(
            "RESEARCH_EVIDENCE_PROFILE_INVALID",
            "Evidence Profile 只能是 QUICK、STANDARD 或 DEEP。",
            context={"actual": evidence_profile, "allowed": sorted(EVIDENCE_PROFILES)},
        )

    route = STOP_AT_TO_RUN_TYPE.get(stop_at, "")
    default_metric = (
        "eligible_count"
        if stop_at == "UNIVERSE"
        else (
            "annualized_return"
            if stop_at == "PORTFOLIO_EVIDENCE"
            else ("rank_ic" if asset_class == "US_EQUITY" else "ic")
        )
    )
    primary_metric = _clean(
        evidence_block.get("primary_metric")
        or explicit_evaluation.get("primary_metric")
        or default_metric
    ).lower()
    allowed_metrics = {
        "UNIVERSE": {"eligible_count", "coverage"},
        "FACTOR": {"ic", "rank_ic"},
        "ALPHA": {"ic", "rank_ic"},
        "PORTFOLIO_EVIDENCE": {
            "annualized_return", "sharpe_ratio", "max_drawdown", "cost_adjusted_return"
        },
    }.get(stop_at, set())
    if allowed_metrics and primary_metric not in allowed_metrics:
        raise ResearchSemanticError(
            "RESEARCH_PRIMARY_METRIC_INVALID",
            f"{stop_at} 研究不能使用 {primary_metric} 作为主指标。",
            context={"stop_at": stop_at, "actual": primary_metric, "allowed": sorted(allowed_metrics)},
        )
    profile_protocol = {
        "QUICK": {"validation_protocol": "HOLDOUT", "horizons": [1]},
        "STANDARD": {"validation_protocol": "WALK_FORWARD", "horizons": [1, 5, 20]},
        "DEEP": {"validation_protocol": "PURGED_WALK_FORWARD", "horizons": [1, 5, 20, 60]},
    }[evidence_profile]

    unresolved: dict[str, Any] | None = None
    if not asset_class:
        unresolved = {
            "reason_code": "RESEARCH_ASSET_SCOPE_REQUIRED",
            "question": "这次研究面向股票、加密资产，还是预测市场？",
        }
    elif not stop_at:
        unresolved = {
            "reason_code": "RESEARCH_STOP_AT_REQUIRED",
            "question": "你这次主要想验证单个指标、组合预测信号，还是组合交易表现？",
            "recommended": "先停在 Factor，验证单个指标是否具有预测能力。",
        }
    elif asset_class == "US_EQUITY" and not _scope_items(instrument_scope) and not universe_policy:
        unresolved = equity_universe_guidance(objective)

    route_available = stop_at in RESEARCHER_AVAILABLE_STOP_AT
    status = "READY"
    if unresolved:
        status = "UNSUPPORTED" if unresolved.get("reason_code") == "RESEARCH_PRODUCT_NOT_AVAILABLE" else "NEEDS_INPUT"
    elif not route_available:
        status = "UNSUPPORTED"
        unresolved = {
            "reason_code": "RESEARCH_PRODUCT_NOT_AVAILABLE",
            "question": "该研究产品尚未由 Researcher facade 开放。",
            "requested_stop_at": stop_at,
        }

    base_refs = supplied.get("base_refs") or payload.get("base_refs") or []
    if isinstance(base_refs, str):
        base_refs = [base_refs]
    entry_mode = _clean(supplied.get("entry_mode") or payload.get("entry_mode") or "START").upper()
    alignment = {
        "schema_version": ALIGNED_RESEARCH_INTENT_SCHEMA_VERSION,
        "status": status,
        "question": objective,
        "decision_supported": _clean(
            supplied.get("decision_supported")
            or payload.get("decision_supported")
            or _default_decision_supported(stop_at)
        ),
        "stop_at": stop_at,
        "entry_mode": entry_mode,
        "base_refs": list(base_refs),
        "scope": {
            "asset_class": asset_class,
            "instrument_scope": _scope_items(instrument_scope),
            "universe_policy": universe_policy,
            "research_period": research_period,
            "frequency": frequency,
            "constraints": dict(scope_block.get("constraints") or supplied.get("constraints") or brief.get("constraints") or {}),
        },
        "evidence": {
            "profile": evidence_profile,
            "primary_metric": primary_metric,
            "decision_rule": dict(
                evidence_block.get("decision_rule")
                or explicit_evaluation.get("decision_rule")
                or {}
            ),
            "protocol": {
                **profile_protocol,
                **dict(evidence_block.get("protocol") or {}),
            },
        },
        "assumptions": list(supplied.get("assumptions") or payload.get("assumptions") or []),
        "out_of_scope": list(
            supplied.get("out_of_scope")
            or payload.get("out_of_scope")
            or _default_out_of_scope(stop_at)
        ),
        "unresolved_material_question": unresolved,
        "route": route,
        "route_available": route_available,
    }
    alignment_hash = _canonical_hash(alignment)
    supplied_hash = _clean(supplied.get("alignment_hash"))
    if supplied_hash and supplied_hash != alignment_hash:
        raise ResearchSemanticError(
            "RESEARCH_ALIGNMENT_HASH_MISMATCH",
            "Alignment 内容在确认后发生了变化；请重新执行 ALIGN。",
            context={"expected": supplied_hash, "actual": alignment_hash},
        )
    alignment["alignment_hash"] = alignment_hash
    return json.loads(json_dumps(alignment))


class ResearchSemanticError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = _clean(code).upper()
        self.context = dict(context or {})


def build_research_contract(
    brief: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(payload or {})
    supplied = payload.get("research_contract")
    supplied = dict(supplied) if isinstance(supplied, Mapping) else {}
    alignment_payload = dict(payload)
    if supplied:
        alignment_payload.setdefault("aligned_research_intent", {
            **dict(payload.get("aligned_research_intent") or {}),
            "question": supplied.get("question") or supplied.get("objective") or brief.get("objective"),
            "decision_supported": supplied.get("decision_supported"),
            "stop_at": supplied.get("stop_at") or payload.get("stop_at"),
            "entry_mode": supplied.get("entry_mode") or payload.get("entry_mode"),
            "base_refs": list(supplied.get("base_refs") or payload.get("base_refs") or []),
            "evidence_profile": supplied.get("evidence_profile") or payload.get("evidence_profile"),
            "assumptions": list(supplied.get("assumptions") or payload.get("assumptions") or []),
            "out_of_scope": list(supplied.get("out_of_scope") or payload.get("out_of_scope") or []),
            "scope": {
                **dict(dict(payload.get("aligned_research_intent") or {}).get("scope") or {}),
                "asset_class": dict(supplied.get("asset_scope") or {}).get("asset_class"),
                "instrument_scope": supplied.get("instrument_scope", brief.get("instrument_scope")),
                "universe_policy": supplied.get("universe_policy") or payload.get("universe_policy"),
                "research_period": supplied.get("research_period") or brief.get("research_period"),
                "frequency": supplied.get("frequency") or brief.get("frequency"),
                "constraints": supplied.get("constraints") or brief.get("constraints"),
            },
            "evidence": {
                **dict(dict(payload.get("aligned_research_intent") or {}).get("evidence") or {}),
                "primary_metric": dict(supplied.get("evaluation") or {}).get("primary_metric"),
                "decision_rule": dict(supplied.get("evaluation") or {}).get("decision_rule") or {},
            },
        })
    alignment = align_research_intent(brief, alignment_payload)
    if alignment["status"] != "READY":
        unresolved = dict(alignment.get("unresolved_material_question") or {})
        code = _clean(unresolved.get("reason_code") or "RESEARCH_ALIGNMENT_REQUIRED").upper()
        raise ResearchSemanticError(
            code,
            _clean(unresolved.get("question") or "研究问题仍有重大歧义，不能开始实验。"),
            context=unresolved,
        )

    objective = _clean(alignment["question"])
    scope = dict(alignment.get("scope") or {})
    asset_class = _clean(scope.get("asset_class")).upper()
    scope_items = _scope_items(scope.get("instrument_scope"))
    universe_policy = dict(scope.get("universe_policy") or {})
    if not universe_policy:
        universe_policy = {
            "eligibility": {
                "mode": "STATIC_LIST",
                "instrument_scope": scope_items,
            },
            "selection": {"method": "ALL_ELIGIBLE"},
            "exclusions": [],
        }

    research_period = scope.get("research_period") or supplied.get("research_period") or brief.get("research_period") or {}
    research_period = dict(research_period) if isinstance(research_period, Mapping) else {"label": research_period}
    frequency = _clean(scope.get("frequency") or supplied.get("frequency") or brief.get("frequency"))
    evaluation = supplied.get("evaluation") or {}
    evaluation = dict(evaluation) if isinstance(evaluation, Mapping) else {}
    run_type = _clean(alignment.get("route")).upper()
    requested_run_type = _clean(evaluation.get("run_type")).upper()
    if requested_run_type and requested_run_type != run_type:
        raise ResearchSemanticError(
            "RESEARCH_PRODUCT_ALIGNMENT_MISMATCH",
            "Research Contract 的 run_type 必须由 Alignment 的 STOP_AT 推导，不能单独改写。",
            context={"stop_at": alignment.get("stop_at"), "expected": run_type, "actual": requested_run_type},
        )
    evidence = dict(alignment.get("evidence") or {})
    primary_metric = _clean(evidence.get("primary_metric")).lower()
    requested_pack = supplied.get("factor_pack") or payload.get("factor_pack")
    if not requested_pack and "ALPHA158" in objective.upper():
        requested_pack = {"pack_id": ALPHA158_NO_VWAP_PACK_ID}
    if isinstance(requested_pack, str):
        requested_pack = {"pack_id": _clean(requested_pack)}
    requested_pack = dict(requested_pack) if isinstance(requested_pack, Mapping) else {}
    factor_pack_goal: dict[str, Any] = {}
    if requested_pack:
        try:
            pack = FactorPackRegistry.require(_clean(requested_pack.get("pack_id")))
        except ValueError as exc:
            raise ResearchSemanticError(
                "FACTOR_PACK_NOT_SUPPORTED",
                str(exc),
                context={"pack_id": _clean(requested_pack.get("pack_id"))},
            ) from exc
        if asset_class != pack.asset_class or frequency.lower() != pack.frequency:
            raise ResearchSemanticError(
                "FACTOR_PACK_CONTRACT_MISMATCH",
                "Factor Pack 与 Research Contract 的资产类型或频率不兼容。",
                context={
                    "pack_id": pack.pack_id,
                    "required_asset_class": pack.asset_class,
                    "actual_asset_class": asset_class,
                    "required_frequency": pack.frequency,
                    "actual_frequency": frequency,
                },
            )
        factor_pack_goal = pack.goal_identity()
    experiment_policy = supplied.get("experiment_policy") or {}
    experiment_policy = dict(experiment_policy) if isinstance(experiment_policy, Mapping) else {}
    experiment_policy.setdefault("max_experiments", 10)
    experiment_policy.setdefault("one_major_hypothesis_per_experiment", True)
    experiment_policy.setdefault("universe_change_is_major", True)
    experiment_policy.setdefault(
        "validation_protocol",
        dict(evidence.get("protocol") or {}).get("validation_protocol") or "WALK_FORWARD",
    )

    contract = {
        "schema_version": RESEARCH_CONTRACT_SCHEMA_VERSION,
        "objective": objective,
        "question": objective,
        "decision_supported": alignment.get("decision_supported"),
        "stop_at": alignment.get("stop_at"),
        "entry_mode": alignment.get("entry_mode"),
        "base_refs": list(alignment.get("base_refs") or []),
        "evidence_profile": evidence.get("profile"),
        "assumptions": list(alignment.get("assumptions") or []),
        "out_of_scope": list(alignment.get("out_of_scope") or []),
        "alignment_hash": alignment.get("alignment_hash"),
        "asset_scope": {
            "asset_class": asset_class,
            "instrument_scope": scope_items,
        },
        "research_period": research_period,
        "frequency": frequency,
        "universe_policy": universe_policy,
        "constraints": dict(scope.get("constraints") or supplied.get("constraints") or brief.get("constraints") or {}),
        "factor_pack": factor_pack_goal,
        "evaluation": {
            "run_type": run_type,
            "primary_metric": primary_metric,
            "baseline": evaluation.get("baseline") or brief.get("benchmark") or "NONE",
            "decision_rule": dict(evidence.get("decision_rule") or evaluation.get("decision_rule") or {}),
            "protocol": {
                "horizons": list(
                    evaluation.get("horizons")
                    or dict(evidence.get("protocol") or {}).get("horizons")
                    or []
                ),
                "quantile_count": evaluation.get("quantile_count"),
                "minimum_cross_section_size": evaluation.get("minimum_cross_section_size"),
                "top_n": evaluation.get("top_n"),
            },
        },
        "experiment_policy": experiment_policy,
    }
    return json.loads(json_dumps(contract))


def _find_infrastructure_key(value: Any, path: str = "candidate") -> str:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _clean(key).lower()
            if normalized in _INFRASTRUCTURE_KEYS:
                return f"{path}.{key}"
            found = _find_infrastructure_key(nested, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found = _find_infrastructure_key(nested, f"{path}[{index}]")
            if found:
                return found
    return ""


def normalize_candidate(candidate: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(candidate or {})
    forbidden = _find_infrastructure_key(candidate)
    if forbidden:
        raise ResearchSemanticError(
            "CANDIDATE_INFRASTRUCTURE_FIELD_FORBIDDEN",
            f"CandidateSpec 只能描述研究假设，不能控制基础设施字段：{forbidden}",
            context={"field": forbidden},
        )
    hypothesis = candidate.get("hypothesis")
    if isinstance(hypothesis, str):
        hypothesis = {"statement": _clean(hypothesis)}
    hypothesis = dict(hypothesis) if isinstance(hypothesis, Mapping) else {}
    if not _clean(hypothesis.get("statement")):
        raise ResearchSemanticError("CANDIDATE_HYPOTHESIS_REQUIRED", "CandidateSpec 必须包含可证伪假设。")
    factor = candidate.get("factor")
    raw_factors = candidate.get("factors")
    if raw_factors is None:
        raw_factors = [factor] if isinstance(factor, Mapping) else []
    if not isinstance(raw_factors, list) or any(not isinstance(item, Mapping) for item in raw_factors):
        raise ResearchSemanticError(
            "CANDIDATE_FACTORS_INVALID",
            "CandidateSpec.factors 必须是 Factor 定义数组。",
        )
    factors = [dict(item) for item in raw_factors]
    raw_factor_pack = candidate.get("factor_pack")
    if isinstance(raw_factor_pack, str):
        raw_factor_pack = {"pack_id": _clean(raw_factor_pack)}
    factor_pack = dict(raw_factor_pack) if isinstance(raw_factor_pack, Mapping) else {}
    if factors and factor_pack:
        raise ResearchSemanticError(
            "CANDIDATE_RESEARCH_OBJECT_AMBIGUOUS",
            "CandidateSpec 每轮只能评价一个 Factor 或一个 Factor Pack。",
        )
    interventions = candidate.get("intervention_set") or []
    if not isinstance(interventions, list) or not interventions:
        raise ResearchSemanticError(
            "CANDIDATE_INTERVENTION_REQUIRED",
            "CandidateSpec 必须说明本轮实验改变了什么。",
        )
    evaluation = dict(candidate.get("evaluation") or {})
    contract_evaluation = dict(contract.get("evaluation") or {})
    contract_run_type = _clean(contract_evaluation.get("run_type") or "FACTOR_EVALUATION").upper()
    run_type = _clean(evaluation.get("run_type") or contract_run_type).upper()
    if run_type != contract_run_type:
        raise ResearchSemanticError(
            "GOAL_CONFORMANCE_FAILED",
            "CandidateSpec 不能改变 Research Contract 冻结的研究产品类型。",
            context={"expected_run_type": contract_run_type, "actual_run_type": run_type},
        )
    if run_type not in {
        "UNIVERSE_DESIGN", "FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST"
    }:
        raise ResearchSemanticError(
            "RESEARCH_PRODUCT_NOT_AVAILABLE",
            "Researcher facade 不支持该研究产品。",
            context={
                "requested_run_type": run_type,
                "available": [
                    "UNIVERSE_DESIGN", "FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST"
                ],
            },
        )
    if run_type == "UNIVERSE_DESIGN":
        if factors or factor_pack or candidate.get("alpha"):
            raise ResearchSemanticError(
                "CANDIDATE_RESEARCH_OBJECT_AMBIGUOUS",
                "Universe Candidate 只能改变研究池资格与选择规则，不能同时构造 Factor 或 Alpha。",
            )
    elif run_type == "FACTOR_EVALUATION":
        if not factors and not factor_pack:
            raise ResearchSemanticError(
                "CANDIDATE_FACTOR_REQUIRED",
                "Factor Candidate 必须包含一个 Factor 定义或一个原生 Factor Pack。",
            )
        if len(factors) > 1:
            raise ResearchSemanticError(
                "CANDIDATE_FACTOR_COUNT_INVALID",
                "Factor Evaluation 每轮只评价一个 Factor；多因子组合请使用 Alpha Candidate。",
            )
    else:
        if not factors:
            raise ResearchSemanticError(
                "CANDIDATE_FACTOR_REQUIRED",
                "Alpha 与 Portfolio Evidence Candidate 至少需要一个 Factor 定义。",
            )
        if factor_pack:
            raise ResearchSemanticError(
                "FACTOR_PACK_ALPHA_NOT_AVAILABLE",
                "原生 Factor Pack 不会被隐式合成为 Alpha；请显式列出参与组合的 Factor。",
            )

    factor_names = [_clean(item.get("name")) for item in factors]
    if factors and (any(not name for name in factor_names) or len(set(factor_names)) != len(factor_names)):
        raise ResearchSemanticError(
            "CANDIDATE_FACTOR_NAMES_INVALID",
            "Candidate 中每个 Factor 都必须有唯一名称。",
            context={"factor_names": factor_names},
        )

    contract_pack = dict(contract.get("factor_pack") or {})
    normalized_pack: dict[str, Any] = {}
    if factor_pack:
        if run_type != "FACTOR_EVALUATION":
            raise ResearchSemanticError(
                "FACTOR_PACK_ALPHA_NOT_AVAILABLE",
                "当前原生 Factor Pack 先用于 Factor Evaluation；不会把 157 个因子隐式合成为 Alpha。",
            )
        try:
            pack = FactorPackRegistry.require(_clean(factor_pack.get("pack_id")))
        except ValueError as exc:
            raise ResearchSemanticError("FACTOR_PACK_NOT_SUPPORTED", str(exc)) from exc
        normalized_pack = pack.to_dict()
        if not contract_pack or contract_pack.get("spec_hash") != pack.spec_hash:
            raise ResearchSemanticError(
                "GOAL_CONFORMANCE_FAILED",
                "Factor Pack 必须在 Research Contract 中冻结，Candidate 不能临时更换研究对象。",
                context={
                    "expected_factor_pack": contract_pack,
                    "actual_factor_pack": pack.goal_identity(),
                },
            )
    elif contract_pack and run_type != "UNIVERSE_DESIGN":
        raise ResearchSemanticError(
            "GOAL_CONFORMANCE_FAILED",
            "Research Contract 已冻结 Factor Pack，Candidate 不能用单一 Factor 替换它。",
            context={"expected_factor_pack": contract_pack},
        )
    contract_metric = _clean(contract_evaluation.get("primary_metric")).lower()
    requested_metric = _clean(evaluation.get("primary_metric") or contract_metric).lower()
    if contract_metric and requested_metric != contract_metric:
        raise ResearchSemanticError(
            "GOAL_CONFORMANCE_FAILED",
            "CandidateSpec 不能在看到结果前更换 Research Contract 的主指标。",
            context={"expected_primary_metric": contract_metric, "actual_primary_metric": requested_metric},
        )
    alpha: dict[str, Any] = {}
    if run_type in {"ALPHA_EVALUATION", "RESEARCH_BACKTEST"}:
        raw_alpha = candidate.get("alpha")
        if not isinstance(raw_alpha, Mapping):
            raise ResearchSemanticError(
                "CANDIDATE_ALPHA_REQUIRED",
                "ALPHA_EVALUATION 的 CandidateSpec 必须说明如何把 Factor 转换为预测信号。",
            )
        raw_alpha = dict(raw_alpha)
        components = raw_alpha.get("components")
        if components is None:
            components = [
                {
                    "factor": name,
                    "weight": raw_alpha.get("weight", 1.0),
                    "transform": raw_alpha.get("transform", "CS_RANK"),
                    "ascending": raw_alpha.get("ascending", True),
                }
                for name in factor_names
            ]
        if not isinstance(components, list) or not components or any(
            not isinstance(item, Mapping) for item in components
        ):
            raise ResearchSemanticError(
                "CANDIDATE_ALPHA_COMPONENTS_INVALID",
                "Alpha Candidate.components 必须是非空的 Factor 组件数组。",
            )
        normalized_components: list[dict[str, Any]] = []
        component_names: list[str] = []
        for raw_component in components:
            component = dict(raw_component)
            component_factor = _clean(component.get("factor") or component.get("factor_name"))
            if component_factor not in factor_names:
                raise ResearchSemanticError(
                    "CANDIDATE_ALPHA_COMPONENT_UNKNOWN",
                    "Alpha 组件只能引用当前 Candidate 中声明的 Factor 名称，不能提交内部定义 ID。",
                    context={"factor": component_factor, "available": factor_names},
                )
            component_names.append(component_factor)
            normalized_components.append({
                "factor": component_factor,
                "weight": component.get("weight", 1.0),
                "transform": _clean(component.get("transform") or "CS_RANK").upper(),
                "ascending": bool(component.get("ascending", True)),
            })
        if len(set(component_names)) != len(component_names) or set(component_names) != set(factor_names):
            raise ResearchSemanticError(
                "CANDIDATE_ALPHA_COMPONENTS_INCOMPLETE",
                "Alpha 必须且只能为 Candidate 中的每个 Factor 声明一个组件。",
                context={"components": component_names, "factors": factor_names},
            )
        alpha = {
            "name": _clean(raw_alpha.get("name") or f"{factor_names[0]}_alpha"),
            "components": normalized_components,
            "minimum_coverage": raw_alpha.get("minimum_coverage", 1.0),
            "minimum_cross_section_size": raw_alpha.get("minimum_cross_section_size"),
            "missing_policy": _clean(raw_alpha.get("missing_policy") or "EXCLUDE").upper(),
            "rank_method": _clean(raw_alpha.get("rank_method") or "AVERAGE").upper(),
            "output_scale": _clean(raw_alpha.get("output_scale") or "PERCENTILE").upper(),
        }
    protocol = dict(contract_evaluation.get("protocol") or {})
    raw_universe_selection = candidate.get("universe_selection") or {}
    if not isinstance(raw_universe_selection, Mapping):
        raise ResearchSemanticError(
            "CANDIDATE_UNIVERSE_SELECTION_INVALID",
            "universe_selection 必须是研究池规则对象。",
        )
    portfolio_spec: dict[str, Any] = {}
    execution_spec: dict[str, Any] = {}
    benchmark_spec: dict[str, Any] = {}
    if run_type == "RESEARCH_BACKTEST":
        raw_portfolio = candidate.get("portfolio_spec") or candidate.get("portfolio")
        raw_execution = candidate.get("execution_spec") or candidate.get("execution")
        raw_benchmark = candidate.get("benchmark_spec") or candidate.get("benchmark") or {}
        if not isinstance(raw_portfolio, Mapping) or not isinstance(raw_execution, Mapping):
            raise ResearchSemanticError(
                "CANDIDATE_PORTFOLIO_EVIDENCE_REQUIRED",
                "Portfolio Evidence Candidate 必须显式声明 portfolio_spec 与 execution_spec。",
            )
        if not isinstance(raw_benchmark, Mapping):
            raise ResearchSemanticError(
                "CANDIDATE_BENCHMARK_INVALID",
                "benchmark_spec 必须是对象。",
            )
        if not raw_benchmark:
            raise ResearchSemanticError(
                "CANDIDATE_BENCHMARK_REQUIRED",
                "Portfolio Evidence 必须显式声明 benchmark_spec，避免把绝对收益误当成相对证据。",
            )
        portfolio_spec = dict(raw_portfolio)
        execution_spec = dict(raw_execution)
        benchmark_spec = dict(raw_benchmark)

    normalized = {
        "schema_version": CANDIDATE_SPEC_SCHEMA_VERSION,
        "hypothesis": hypothesis,
        "intervention_set": interventions,
        "controlled_variables": list(candidate.get("controlled_variables") or []),
        "factor": dict(factors[0]) if len(factors) == 1 else {},
        "factors": factors,
        "factor_pack": normalized_pack,
        "alpha": alpha,
        "universe_selection": dict(raw_universe_selection),
        "portfolio_spec": portfolio_spec,
        "execution_spec": execution_spec,
        "benchmark_spec": benchmark_spec,
        "evaluation": {
            "run_type": run_type,
            "primary_metric": requested_metric,
            "horizons": list(evaluation.get("horizons") or protocol.get("horizons") or []),
            "quantile_count": evaluation.get("quantile_count", protocol.get("quantile_count")),
            "minimum_cross_section_size": evaluation.get(
                "minimum_cross_section_size", protocol.get("minimum_cross_section_size")
            ),
            "top_n": evaluation.get("top_n", protocol.get("top_n")),
            "decision_rule": dict(
                evaluation.get("decision_rule") or contract_evaluation.get("decision_rule") or {}
            ),
        },
    }
    return json.loads(json_dumps(normalized))


class ResearchContractService:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def ensure_for_session(
        self,
        session_id: str,
        project_id: str,
        brief: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.active_for_session(session_id) or self.latest_for_session(session_id)
        if existing:
            return existing
        contract = build_research_contract(brief, payload)
        return self._insert(
            session_id=session_id,
            project_id=project_id,
            contract=contract,
            status="ACTIVE",
            change_reason="INITIAL_RESEARCH_CONTRACT",
        )

    def amend(
        self,
        session_id: str,
        contract: Mapping[str, Any],
        *,
        change_reason: str,
    ) -> dict[str, Any]:
        with self.store.connection() as conn:
            session = conn.execute(
                "SELECT project_id FROM research_agent_sessions WHERE session_id=?",
                (_clean(session_id),),
            ).fetchone()
        if not session:
            raise ResearchSemanticError("RESEARCH_SESSION_NOT_FOUND", "Research Session 不存在。")
        normalized = build_research_contract(contract, {"research_contract": contract})
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_contracts SET status='SUPERSEDED' WHERE session_id=? AND status='ACTIVE'",
                (_clean(session_id),),
            )
        return self._insert(
            session_id=session_id,
            project_id=str(session["project_id"]),
            contract=normalized,
            status="ACTIVE",
            change_reason=_clean(change_reason) or "MATERIAL_SCOPE_CHANGE",
        )

    def active_for_session(self, session_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_contracts WHERE session_id=? AND status='ACTIVE' "
                "ORDER BY contract_version DESC LIMIT 1",
                (_clean(session_id),),
            ).fetchone()
        return self._row(row) if row else None

    def latest_for_session(self, session_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_contracts WHERE session_id=? "
                "ORDER BY contract_version DESC LIMIT 1",
                (_clean(session_id),),
            ).fetchone()
        return self._row(row) if row else None

    def _insert(
        self,
        *,
        session_id: str,
        project_id: str,
        contract: Mapping[str, Any],
        status: str,
        change_reason: str,
    ) -> dict[str, Any]:
        status = _clean(status).upper()
        if status not in CONTRACT_STATES:
            raise ValueError(f"unsupported Research Contract status: {status}")
        now = utc_now()
        contract_hash = _canonical_hash(contract)
        with self.store.transaction(immediate=True) as conn:
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(contract_version), 0) + 1 FROM research_contracts WHERE session_id=?",
                    (_clean(session_id),),
                ).fetchone()[0]
            )
            contract_id = f"research_contract_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO research_contracts(
                    contract_id, session_id, project_id, contract_version, status,
                    schema_version, contract_json, contract_hash, change_reason,
                    created_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract_id,
                    _clean(session_id),
                    _clean(project_id),
                    version,
                    status,
                    RESEARCH_CONTRACT_SCHEMA_VERSION,
                    json_dumps(dict(contract)),
                    contract_hash,
                    _clean(change_reason),
                    now,
                    now if status == "ACTIVE" else None,
                ),
            )
        return self.active_for_session(session_id) or self.latest_for_session(session_id)  # type: ignore[return-value]

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["contract"] = json.loads(result.pop("contract_json") or "{}")
        return result


__all__ = [
    "ALIGNED_RESEARCH_INTENT_SCHEMA_VERSION",
    "CANDIDATE_SPEC_SCHEMA_VERSION",
    "EVIDENCE_PROFILES",
    "RESEARCH_CONTRACT_SCHEMA_VERSION",
    "RESEARCH_RESULT_SCHEMA_VERSION",
    "RESEARCHER_AVAILABLE_STOP_AT",
    "RUN_TYPE_TO_STOP_AT",
    "STOP_AT_TO_RUN_TYPE",
    "ResearchContractService",
    "ResearchSemanticError",
    "align_research_intent",
    "build_research_contract",
    "equity_universe_guidance",
    "infer_research_stop_at",
    "infer_asset_class",
    "normalize_candidate",
]
