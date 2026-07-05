from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.config_loader import load_web_settings
from services.event_graph_logic import (
    CAUSAL_RELATIONS,
    EVIDENCE_RELATIONS,
    IMPACT_RELATIONS,
    LOGICAL_RELATIONS,
    MAPPING_RELATIONS,
    MARKET_MOVE_RELATIONS,
    REASONING_RELATION_CLASSES,
    SCENARIO_RELATIONS,
    infer_relation_class,
    relation_class_is_known,
    relation_type_is_valid,
    validate_expression_shape,
    validate_logical_conflicts,
    validate_reasoning_edges,
)
from services.event_graph_service import build_event_graph
from services.event_news_service import (
    apply_change_request,
    create_change_request,
    get_change_request,
    get_status as get_event_news_status,
    list_graph_core,
    list_graph_edges,
    list_graph_events,
    list_graph_expressions,
    list_graph_finance_nodes,
    list_graph_versions,
    list_change_requests,
    list_events as list_news_events,
    list_observations as list_news_observations,
    refresh_news,
    review_change_request,
)
from services.history_data_service import (
    create_backtest_batch as history_create_backtest_batch,
    create_backtest_case as history_create_backtest_case,
    create_backtest_run as history_create_backtest_run,
    get_backtest_batch as history_get_backtest_batch,
    get_backtest_run as history_get_backtest_run,
    list_backtest_batches as history_list_backtest_batches,
    list_backtest_cases as history_list_backtest_cases,
    list_backtest_runs as history_list_backtest_runs,
)
from services.polymarket_service import (
    fetch_strategy_detail,
    fetch_strategy_monitoring,
    list_market_categories,
    resolve_market_selection,
    search_markets,
)
from services.strategy_data_source import _db_path as _strategy_db_path, read_strategy_state_bundle
from services.strategy_event_service import list_strategy_events
from services.strategy_registry_service import create_strategy
from services.strategy_workspace_service import get_strategy_usedata_snapshot, get_strategy_workspace


DEFAULT_AGENT_ID = "agent_strategy_assistant"
DEFAULT_HUMAN_ID = "local_user"
VALID_STRATEGY_MODES = {"Stop", "Virtual", "Real"}

AGENT_REPORT_FIELDS = [
    {
        "key": "strategy_reason",
        "label": "策略选择原因",
        "hint": "为什么选择这个市场、这个方向和这个策略代码。",
    },
    {
        "key": "market_observation",
        "label": "市场观察",
        "hint": "只写和当前策略选择有关的价格、方向、流动性或事件信息。",
    },
    {
        "key": "parameter_rationale",
        "label": "参数设计思考",
        "hint": "解释 fair_price、entry_edge、预算、止损止盈等关键参数为什么这样设。",
    },
    {
        "key": "risk_control",
        "label": "风险控制",
        "hint": "说明预算、单笔上限、退出和人工确认边界。",
    },
    {
        "key": "human_review_focus",
        "label": "人工确认重点",
        "hint": "告诉人类审批时最该核对什么。",
    },
]

AGENT_STRATEGY_SUBMISSION_PROMPT = """创建或提交策略草案时，请在 draft.agent_report 中填写结构化说明。
要求：每项 1-2 句，少写泛泛的投资观点，多写和本次市场、方向、参数直接相关的原因。
必须包含这些字段：
- strategy_reason: 为什么选择这个市场、这个方向和这个策略代码。
- market_observation: 当前价格/盘口/事件信息里，哪些事实支持这个草案值得人工看。
- parameter_rationale: fair_price、entry_edge、预算、止损止盈、时间参数等为什么这样设。
- risk_control: 资金上限、单笔上限、退出条件和不自动实盘的边界。
- human_review_focus: 人类批准前最需要确认的 2-3 个点。
不要写“保证盈利”“确定性机会”等结论；不确定时明确写需要人工判断。
"""

MARKET_QUERY_CAPABILITIES: Dict[str, Any] = {
    "source": "polymarket",
    "categories_endpoint": "/api/agent/market-categories",
    "search_endpoint": "/api/agent/markets",
    "resolve_endpoint": "/api/agent/markets/resolve",
    "hot_scan_endpoint": "/api/agent/market-scan",
    "batch_propose_endpoint": "/api/agent/market-scan/propose-strategies",
    "default_categories": ["Elections Politics", "World", "Geopolitics"],
    "price_filters": {
        "yes_ask": {"min": "yes_ask_min", "max": "yes_ask_max", "aliases": ["ask_min", "ask_max"]},
        "yes_bid": {"min": "yes_bid_min", "max": "yes_bid_max", "aliases": ["bid_min", "bid_max"]},
        "no_ask": {"min": "no_ask_min", "max": "no_ask_max"},
        "no_bid": {"min": "no_bid_min", "max": "no_bid_max"},
    },
    "price_filter_note": "Price filters use current cached/snapshot bid/ask values, not millisecond-level live order book state.",
    "sorts": [
        {"key": "volume24h", "label": "24小时热门", "default_order": "desc"},
        {"key": "volume", "label": "总交易量", "default_order": "desc"},
        {"key": "liquidity", "label": "流动性最高", "default_order": "desc"},
        {"key": "spread", "label": "价差最小", "default_order": "asc"},
        {"key": "end_date", "label": "即将到期", "default_order": "asc"},
        {"key": "updated_at", "label": "最新更新", "default_order": "desc"},
        {"key": "price_change_24h", "label": "24小时变化", "default_order": "desc"},
        {"key": "last_trade_price", "label": "最近成交价", "default_order": "desc"},
    ],
}

STRATEGY_OBSERVATION_CAPABILITIES: Dict[str, Any] = {
    "strategies_endpoint": "/api/agent/strategies",
    "strategy_detail_endpoint": "/api/agent/strategies/{strategy_id}",
    "workspace_endpoint": "/api/agent/strategies/{strategy_id}/workspace",
    "usedata_endpoint": "/api/agent/strategies/{strategy_id}/usedata",
    "events_endpoint": "/api/agent/strategies/{strategy_id}/events",
    "state_endpoint": "/api/agent/strategies/{strategy_id}/state",
    "read_only": True,
    "notes": [
        "Agent strategy observation APIs are read-only.",
        "PnL fields are omitted when pnl.read is disabled.",
    ],
}

BACKTEST_CAPABILITIES: Dict[str, Any] = {
    "cases_endpoint": "/api/agent/backtests/cases",
    "runs_endpoint": "/api/agent/backtests/runs",
    "run_detail_endpoint": "/api/agent/backtests/runs/{run_id}",
    "batch_create_endpoint": "/api/agent/backtests/batches",
    "batch_detail_endpoint": "/api/agent/backtests/batches/{batch_id}",
    "read_capability": "backtest.read",
    "case_create_capability": "backtest.case.create",
    "run_create_capability": "backtest.run.create",
    "batch_create_capability": "backtest.batch.create",
    "notes": [
        "Backtest APIs create local historical replay runs only; they do not approve or execute live orders.",
        "Batch requests select existing history cases by case_ids, collection_name, or strategy_id.",
        "Mixed Binance/Polymarket source replay is reported as planned rather than executed.",
    ],
}

EVENT_GRAPH_CAPABILITIES: Dict[str, Any] = {
    "graph_endpoint": "/api/agent/event-graph",
    "events_endpoint": "/api/agent/event-graph/events",
    "observations_endpoint": "/api/agent/event-graph/observations",
    "news_status_endpoint": "/api/agent/event-graph/news/status",
    "news_refresh_endpoint": "/api/agent/event-graph/news/refresh",
    "news_search_endpoint": "/api/agent/event-graph/news/search",
    "patch_validate_endpoint": "/api/agent/event-graph/patches/validate",
    "change_requests_endpoint": "/api/agent/event-graph/change-requests",
    "read_capability": "event.read",
    "refresh_capability": "event.news.refresh",
    "search_capability": "event.news.search",
    "patch_validate_capability": "event.graph.patch.validate",
    "change_request_capability": "event.graph.change_request",
    "approval_modes": ["manual", "trusted_low_risk", "trusted_all"],
    "supported_apply_actions": [
        "event_create",
        "event_update",
        "event_archive",
        "event_merge",
        "finance_create",
        "finance_update",
        "finance_archive",
        "edge_create",
        "edge_update",
        "edge_delete",
        "finance_mapping_create",
        "expression_create",
        "expression_update",
        "expression_archive",
    ],
    "relation_classes": {
        "LOGICAL": sorted(LOGICAL_RELATIONS),
        "IMPACT": sorted(IMPACT_RELATIONS),
        "CAUSAL": sorted(CAUSAL_RELATIONS),
        "SCENARIO": sorted(SCENARIO_RELATIONS),
        "EVIDENCE": sorted(EVIDENCE_RELATIONS),
        "MAPPING": sorted(MAPPING_RELATIONS),
        "MARKET_MOVE": sorted(MARKET_MOVE_RELATIONS),
    },
    "notes": [
        "EventGraph news data is stored as observations first, then grouped into derived events.",
        "News refresh/search writes observations and derived event records, but does not approve final human-verified facts.",
        "External agents submit EventGraph mutations as validated change requests; Settings may allow trusted system auto-apply.",
    ],
}

LIFECYCLE_AI_DRAFTING = "AI_DRAFTING"
LIFECYCLE_AI_PROPOSED = "AI_PROPOSED"
LIFECYCLE_WAITING = "WAITING_HUMAN_CONFIRM"
LIFECYCLE_REVISION = "HUMAN_REVISION_REQUESTED"
LIFECYCLE_APPROVED = "HUMAN_APPROVED"
LIFECYCLE_REJECTED = "HUMAN_REJECTED"
LIFECYCLE_RISK_BLOCKED = "RISK_BLOCKED"
LIFECYCLE_CANCELLED = "CANCELLED"
LIFECYCLE_ARCHIVED = "ARCHIVED"

APPROVAL_PENDING = LIFECYCLE_WAITING
APPROVAL_APPROVED = LIFECYCLE_APPROVED
APPROVAL_REJECTED = LIFECYCLE_REJECTED
APPROVAL_REVISION = LIFECYCLE_REVISION
APPROVAL_EXPIRED = "EXPIRED"

DEFAULT_POLICY: Dict[str, Any] = {
    "agent_id": DEFAULT_AGENT_ID,
    "enabled": True,
    "role": "strategy_assistant",
    "allow": [
        "agent.activity",
        "market.read",
        "market.category.list",
        "market.search",
        "market.resolve",
        "market.hot_scan",
        "account.read_limited",
        "strategy.read_all",
        "strategy.detail.read",
        "strategy.workspace.read",
        "strategy.usedata.read",
        "strategy.events.read",
        "strategy.state.read",
        "strategy.draft.create",
        "strategy.draft.update",
        "strategy.draft.delete",
        "strategy.batch.propose",
        "risk.check",
        "strategy.simulate",
        "strategy.submit",
        "backtest.read",
        "backtest.case.create",
        "backtest.run.create",
        "backtest.batch.create",
        "approval.status",
        "order.read",
        "pnl.read",
        "audit.read",
        "event.read",
        "event.news.refresh",
        "event.news.search",
        "event.graph.patch.validate",
        "event.graph.change_request",
    ],
    "deny": [
        "strategy.approve",
        "execution.apply",
        "admin.policy.set",
    ],
    "limits": {
        "max_strategy_budget_usdc": 100.0,
        "max_single_order_usdc": 20.0,
        "max_daily_spend_usdc": 150.0,
        "max_market_exposure_usdc": 50.0,
        "max_global_exposure_usdc": 300.0,
        "max_slippage_bps": 100.0,
        "allowed_market_ids": [],
        "allowed_venues": ["polymarket"],
        "allow_market_order": False,
        "require_human_approval": True,
        "approval_expires_minutes": 1440,
    },
    "event_graph_approval": {
        "mode": "manual",
        "auto_apply_actor_id": "event_graph_trusted_rule",
        "max_items_per_request": 100,
        "min_confidence": 0.0,
        "require_evidence_summary": False,
    },
}

PERMISSION_CAPABILITIES: Dict[str, List[str]] = {
    "market_read": ["market.read", "market.category.list", "market.resolve", "account.read_limited", "approval.status"],
    "market_search": ["market.search"],
    "market_scan": ["market.hot_scan"],
    "strategy_read_all": ["strategy.read_all"],
    "strategy_detail_read": ["strategy.detail.read"],
    "strategy_workspace_read": ["strategy.workspace.read", "strategy.usedata.read"],
    "strategy_events_read": ["strategy.events.read"],
    "strategy_state_read": ["strategy.state.read"],
    "strategy_draft_create": ["strategy.draft.create"],
    "strategy_draft_update": ["strategy.draft.update"],
    "strategy_draft_delete": ["strategy.draft.delete"],
    "strategy_batch_propose": ["strategy.batch.propose"],
    "risk_check": ["risk.check"],
    "strategy_simulate": ["strategy.simulate"],
    "strategy_submit": ["strategy.submit"],
    "backtest_read": ["backtest.read"],
    "backtest_case_create": ["backtest.case.create"],
    "backtest_run_create": ["backtest.run.create"],
    "backtest_batch_create": ["backtest.batch.create"],
    "order_read": ["order.read"],
    "pnl_read": ["pnl.read"],
    "audit_read": ["audit.read"],
    "event_read": ["event.read"],
    "event_news_refresh": ["event.news.refresh"],
    "event_news_search": ["event.news.search"],
    "event_graph_change_request": ["event.graph.change_request", "event.graph.patch.validate"],
}

CAPABILITY_PERMISSION = {
    capability: permission
    for permission, capabilities in PERMISSION_CAPABILITIES.items()
    for capability in capabilities
}

_DDL_AGENT = """
CREATE TABLE IF NOT EXISTS agent_activity_events (
    event_id       TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL DEFAULT '',
    ref_type       TEXT NOT NULL DEFAULT '',
    ref_id         TEXT NOT NULL DEFAULT '',
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_activity_created
ON agent_activity_events(created_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_drafts (
    draft_id              TEXT PRIMARY KEY,
    name                  TEXT NOT NULL DEFAULT '',
    lifecycle_state       TEXT NOT NULL DEFAULT 'AI_DRAFTING',
    created_by_type       TEXT NOT NULL DEFAULT 'agent',
    created_by_id         TEXT NOT NULL DEFAULT '',
    current_version       INTEGER NOT NULL DEFAULT 1,
    draft_json            TEXT NOT NULL DEFAULT '{}',
    last_risk_report_json TEXT NOT NULL DEFAULT '{}',
    last_simulation_json  TEXT NOT NULL DEFAULT '{}',
    created_at_utc        TEXT NOT NULL,
    updated_at_utc        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_drafts_state
ON strategy_drafts(lifecycle_state, updated_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_draft_versions (
    version_id       TEXT PRIMARY KEY,
    draft_id         TEXT NOT NULL,
    version_number   INTEGER NOT NULL,
    draft_json       TEXT NOT NULL DEFAULT '{}',
    change_reason    TEXT NOT NULL DEFAULT '',
    created_by_type  TEXT NOT NULL DEFAULT 'agent',
    created_by_id    TEXT NOT NULL DEFAULT '',
    created_at_utc   TEXT NOT NULL,
    UNIQUE(draft_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_strategy_draft_versions_draft
ON strategy_draft_versions(draft_id, version_number DESC);

CREATE TABLE IF NOT EXISTS strategy_approval_requests (
    approval_id      TEXT PRIMARY KEY,
    draft_id         TEXT NOT NULL,
    draft_version    INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'WAITING_HUMAN_CONFIRM',
    submitted_by_type TEXT NOT NULL DEFAULT 'agent',
    submitted_by_id   TEXT NOT NULL DEFAULT '',
    approved_by_type  TEXT NOT NULL DEFAULT '',
    approved_by_id    TEXT NOT NULL DEFAULT '',
    approved_strategy_id INTEGER,
    risk_report_json TEXT NOT NULL DEFAULT '{}',
    note             TEXT NOT NULL DEFAULT '',
    expires_at_utc   TEXT,
    created_at_utc   TEXT NOT NULL,
    updated_at_utc   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_approval_status
ON strategy_approval_requests(status, updated_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_approval_snapshots (
    approval_id          TEXT PRIMARY KEY,
    snapshot_json        TEXT NOT NULL DEFAULT '{}',
    market_snapshot_json TEXT NOT NULL DEFAULT '[]',
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    risk_snapshot_json   TEXT NOT NULL DEFAULT '{}',
    created_at_utc       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_audit_events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    actor_type      TEXT NOT NULL DEFAULT '',
    actor_id        TEXT NOT NULL DEFAULT '',
    agent_kind      TEXT NOT NULL DEFAULT '',
    workflow_id     TEXT NOT NULL DEFAULT '',
    capability      TEXT NOT NULL DEFAULT '',
    target_type     TEXT NOT NULL DEFAULT '',
    target_id       TEXT NOT NULL DEFAULT '',
    endpoint        TEXT NOT NULL DEFAULT '',
    method          TEXT NOT NULL DEFAULT '',
    status_code     INTEGER,
    duration_ms     REAL,
    input_json      TEXT NOT NULL DEFAULT '{}',
    output_json     TEXT NOT NULL DEFAULT '{}',
    error_json      TEXT NOT NULL DEFAULT '{}',
    policy_decision TEXT NOT NULL DEFAULT 'allow',
    risk_decision   TEXT NOT NULL DEFAULT 'not_required',
    created_at_utc  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_audit_created
ON agent_audit_events(created_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_agent_audit_run
ON agent_audit_events(run_id, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,
    actor_type      TEXT NOT NULL DEFAULT '',
    actor_id        TEXT NOT NULL DEFAULT '',
    agent_kind      TEXT NOT NULL DEFAULT '',
    workflow_id     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'completed',
    ref_type        TEXT NOT NULL DEFAULT '',
    ref_id          TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    input_json      TEXT NOT NULL DEFAULT '{}',
    output_json     TEXT NOT NULL DEFAULT '{}',
    error_json      TEXT NOT NULL DEFAULT '{}',
    started_at_utc  TEXT NOT NULL,
    finished_at_utc TEXT,
    duration_ms     REAL
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_started
ON agent_runs(started_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_agent_runs_actor
ON agent_runs(agent_kind, started_at_utc DESC);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    step_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL DEFAULT '',
    parent_step_id  TEXT NOT NULL DEFAULT '',
    step_index      INTEGER NOT NULL DEFAULT 0,
    step_type       TEXT NOT NULL DEFAULT 'api_call',
    name            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'completed',
    capability      TEXT NOT NULL DEFAULT '',
    target_type     TEXT NOT NULL DEFAULT '',
    target_id       TEXT NOT NULL DEFAULT '',
    input_json      TEXT NOT NULL DEFAULT '{}',
    output_json     TEXT NOT NULL DEFAULT '{}',
    error_json      TEXT NOT NULL DEFAULT '{}',
    started_at_utc  TEXT NOT NULL,
    finished_at_utc TEXT,
    duration_ms     REAL
);

CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run
ON agent_run_steps(run_id, step_index, started_at_utc);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json_text(value: Any, default: Any = None) -> str:
    if default is None:
        default = {}
    try:
        parsed = json.loads(value) if isinstance(value, str) and value.strip() else value
    except Exception:
        parsed = default
    if parsed is None:
        parsed = default
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _parse_json(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or json.dumps(default, ensure_ascii=False))
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Migrate columns first so that index creation in _DDL_AGENT won't fail
    # on existing DBs that predate the run_id / agent_kind columns.
    # (If the table doesn't exist yet the ALTER fails silently; executescript creates it.)
    try:
        _ensure_agent_audit_columns(conn)
        conn.commit()
    except Exception:
        pass
    conn.executescript(_DDL_AGENT)
    conn.commit()


def _ensure_agent_audit_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(agent_audit_events)").fetchall()
    existing = {str(row["name"]) for row in rows}
    columns = {
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "step_id": "TEXT NOT NULL DEFAULT ''",
        "agent_kind": "TEXT NOT NULL DEFAULT ''",
        "workflow_id": "TEXT NOT NULL DEFAULT ''",
        "endpoint": "TEXT NOT NULL DEFAULT ''",
        "method": "TEXT NOT NULL DEFAULT ''",
        "status_code": "INTEGER",
        "duration_ms": "REAL",
        "error_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE agent_audit_events ADD COLUMN {name} {definition}")


def _connect() -> sqlite3.Connection:
    path = _strategy_db_path()
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _row_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _policy() -> Dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY, ensure_ascii=False))
    try:
        settings_policy = load_web_settings().get("agent_policy", {})
    except Exception:
        settings_policy = {}
    if not isinstance(settings_policy, dict):
        settings_policy = {}

    policy["enabled"] = bool(settings_policy.get("enabled", policy.get("enabled", True)))

    limits = settings_policy.get("limits")
    if isinstance(limits, dict):
        policy["limits"].update(limits)

    event_graph_approval = settings_policy.get("event_graph_approval")
    if isinstance(event_graph_approval, dict):
        policy["event_graph_approval"].update(event_graph_approval)

    permissions = settings_policy.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    allowed = []
    for capability in DEFAULT_POLICY["allow"]:
        permission = CAPABILITY_PERMISSION.get(capability)
        if permission is None or bool(permissions.get(permission, True)):
            allowed.append(capability)
    policy["allow"] = allowed
    policy["permissions"] = dict(permissions)

    defaults = settings_policy.get("defaults")
    policy["defaults"] = dict(defaults) if isinstance(defaults, dict) else {}
    return policy


def _limits() -> Dict[str, Any]:
    return _policy().get("limits", {})


def _agent_defaults() -> Dict[str, Any]:
    defaults = _policy().get("defaults", {})
    return defaults if isinstance(defaults, dict) else {}


def _event_graph_approval_policy() -> Dict[str, Any]:
    defaults = DEFAULT_POLICY.get("event_graph_approval", {})
    policy = _policy().get("event_graph_approval", {})
    if not isinstance(policy, dict):
        policy = {}
    merged = {**defaults, **policy}
    mode = str(merged.get("mode") or "manual").strip().lower()
    if mode not in {"manual", "trusted_low_risk", "trusted_all"}:
        mode = "manual"
    merged["mode"] = mode
    merged["auto_apply_actor_id"] = str(merged.get("auto_apply_actor_id") or "event_graph_trusted_rule").strip() or "event_graph_trusted_rule"
    merged["max_items_per_request"] = max(1, min(_safe_int(merged.get("max_items_per_request"), 100), 1000))
    merged["min_confidence"] = max(0.0, min(_safe_float(merged.get("min_confidence"), 0.0), 1.0))
    merged["require_evidence_summary"] = bool(merged.get("require_evidence_summary"))
    return merged


def _require_agent_capability(capability: str, actor_type: str = "agent") -> None:
    if actor_type != "agent":
        return
    policy = _policy()
    if not policy.get("enabled", True):
        raise ValueError("agent is disabled by settings")
    if capability not in set(policy.get("allow") or []):
        raise ValueError(f"agent capability disabled by settings: {capability}")


def _agent_capability_enabled(capability: str, actor_type: str = "agent") -> bool:
    if actor_type != "agent":
        return True
    policy = _policy()
    return bool(policy.get("enabled", True)) and capability in set(policy.get("allow") or [])


_PNL_FIELD_NAMES = {
    "pnl",
    "strategy_pnl",
    "virtual_total_pnl",
    "virtual_unrealized_pnl",
    "virtual_realized_pnl",
    "realized_pnl",
    "unrealized_pnl",
    "cash_pnl",
    "percent_pnl",
    "profit",
    "total_strategy_profit",
    "total_strategy_return_pct",
    "roi",
    "return_pct",
    "strategy_return_pct",
    "virtual_fees_paid",
    "total_fees_paid",
}


def _strip_pnl_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_pnl_fields(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in _PNL_FIELD_NAMES:
                continue
            result[key] = _strip_pnl_fields(item)
        return result
    return value


def _agent_read_payload(payload: Optional[Dict[str, Any]], default_limit: int = 100, max_limit: int = 500) -> tuple[Dict[str, Any], str, str, int]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    limit = max(1, min(_safe_int(payload.get("limit"), default_limit), max_limit))
    return payload, actor_type, actor_id, limit


def _maybe_strip_pnl(payload: Any, actor_type: str) -> Any:
    return payload if _agent_capability_enabled("pnl.read", actor_type) else _strip_pnl_fields(payload)


def _actor(payload: Dict[str, Any] | None = None, default_type: str = "agent", default_id: str = DEFAULT_AGENT_ID) -> tuple[str, str]:
    payload = payload or {}
    actor_type = str(payload.get("actor_type") or default_type).strip() or default_type
    actor_id = str(payload.get("actor_id") or default_id).strip() or default_id
    return actor_type, actor_id


def _agent_kind(actor_type: str, capability: str = "", target_type: str = "") -> str:
    actor = str(actor_type or "").strip()
    cap = str(capability or "")
    target = str(target_type or "")
    if (
        actor == "internal_agent"
        or cap.startswith("event.investigation.")
        or cap.startswith("event.proposal.")
        or "investigation" in target
        or "graph_proposal" in target
    ):
        return "internal"
    if actor in {"agent", "external_agent"}:
        return "external"
    if actor == "human":
        return "human"
    if actor == "system":
        return "system"
    return "external"


def _audit_context(input_data: Any = None) -> Dict[str, str]:
    payload = input_data if isinstance(input_data, dict) else {}
    return {
        "run_id": str(payload.get("run_id") or payload.get("agent_run_id") or "").strip(),
        "step_id": str(payload.get("step_id") or payload.get("agent_step_id") or "").strip(),
        "workflow_id": str(payload.get("workflow_id") or payload.get("workflow") or "").strip(),
        "endpoint": str(payload.get("endpoint") or payload.get("_endpoint") or "").strip(),
        "method": str(payload.get("method") or payload.get("_method") or "").strip().upper(),
    }


def _audit(
    conn: sqlite3.Connection,
    *,
    actor_type: str,
    actor_id: str,
    capability: str,
    target_type: str = "",
    target_id: str = "",
    input_data: Any = None,
    output_data: Any = None,
    error_data: Any = None,
    policy_decision: str = "allow",
    risk_decision: str = "not_required",
    run_id: str = "",
    step_id: str = "",
    workflow_id: str = "",
    endpoint: str = "",
    method: str = "",
    status_code: int | None = None,
    duration_ms: float | None = None,
) -> None:
    now = _now()
    context = _audit_context(input_data)
    run_id = str(run_id or context["run_id"] or _new_id("run")).strip()
    step_id = str(step_id or context["step_id"] or _new_id("step")).strip()
    workflow_id = str(workflow_id or context["workflow_id"]).strip()
    endpoint = str(endpoint or context["endpoint"]).strip()
    method = str(method or context["method"]).strip().upper()
    agent_kind = _agent_kind(actor_type, capability, target_type)
    status = "failed" if error_data else "completed"
    input_compact = _compact_audit_value(input_data or {})
    output_compact = _compact_audit_value(output_data or {})
    error_compact = _compact_audit_value(error_data or {})

    conn.execute(
        """INSERT OR IGNORE INTO agent_runs(
            run_id, actor_type, actor_id, agent_kind, workflow_id, status,
            ref_type, ref_id, summary, input_json, output_json, error_json,
            started_at_utc, finished_at_utc, duration_ms
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            actor_type,
            actor_id,
            agent_kind,
            workflow_id,
            status,
            target_type,
            target_id,
            capability,
            _json_text(input_compact),
            _json_text(output_compact),
            _json_text(error_compact),
            now,
            now,
            duration_ms,
        ),
    )
    conn.execute(
        """UPDATE agent_runs
           SET status = CASE WHEN status = 'failed' OR ? = 'failed' THEN 'failed' ELSE ? END,
               output_json = ?, error_json = ?, finished_at_utc = ?, duration_ms = COALESCE(?, duration_ms),
               summary = CASE WHEN summary = '' THEN ? ELSE summary END
           WHERE run_id = ?""",
        (status, status, _json_text(output_compact), _json_text(error_compact), now, duration_ms, capability, run_id),
    )
    step_count = conn.execute("SELECT COUNT(*) AS c FROM agent_run_steps WHERE run_id = ?", (run_id,)).fetchone()["c"]
    conn.execute(
        """INSERT OR IGNORE INTO agent_run_steps(
            step_id, run_id, parent_step_id, step_index, step_type, name, status,
            capability, target_type, target_id, input_json, output_json, error_json,
            started_at_utc, finished_at_utc, duration_ms
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            step_id,
            run_id,
            "",
            int(step_count or 0) + 1,
            "api_call",
            capability,
            status,
            capability,
            target_type,
            target_id,
            _json_text(input_compact),
            _json_text(output_compact),
            _json_text(error_compact),
            now,
            now,
            duration_ms,
        ),
    )
    conn.execute(
        """INSERT INTO agent_audit_events(
            event_id, run_id, step_id, actor_type, actor_id, agent_kind, workflow_id,
            capability, target_type, target_id, endpoint, method, status_code, duration_ms,
            input_json, output_json, error_json, policy_decision, risk_decision, created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("audit"),
            run_id,
            step_id,
            actor_type,
            actor_id,
            agent_kind,
            workflow_id,
            capability,
            target_type,
            target_id,
            endpoint,
            method,
            status_code,
            duration_ms,
            _json_text(input_compact),
            _json_text(output_compact),
            _json_text(error_compact),
            policy_decision,
            risk_decision,
            now,
        ),
    )


def _compact_audit_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _clip_text(str(value), 300)
    if isinstance(value, list):
        compact = [_compact_audit_value(item, depth=depth + 1) for item in value[:10]]
        if len(value) > 10:
            compact.append({"_truncated_items": len(value) - 10})
        return compact
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 60:
                result["_truncated_keys"] = len(value) - 60
                break
            result[str(key)] = _compact_audit_value(item, depth=depth + 1)
        return result
    if isinstance(value, str):
        return _clip_text(value, 800)
    return value


def _audit_request(
    *,
    actor_type: str,
    actor_id: str,
    capability: str,
    target_type: str = "",
    target_id: str = "",
    input_data: Any = None,
    output_data: Any = None,
    policy_decision: str = "allow",
    risk_decision: str = "not_required",
) -> None:
    conn = _connect()
    try:
        _ensure_schema(conn)
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability=capability,
            target_type=target_type,
            target_id=target_id,
            input_data=_compact_audit_value(input_data or {}),
            output_data=_compact_audit_value(output_data or {}),
            policy_decision=policy_decision,
            risk_decision=risk_decision,
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _add_activity(
    conn: sqlite3.Connection,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    state: str,
    message: str,
    ref_type: str = "",
    ref_id: str = "",
    payload: Any = None,
) -> Dict[str, Any]:
    event = {
        "event_id": _new_id("evt"),
        "agent_id": agent_id,
        "state": state,
        "message": message,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "payload": payload or {},
        "created_at": _now(),
    }
    conn.execute(
        """INSERT INTO agent_activity_events(
            event_id, agent_id, state, message, ref_type, ref_id, payload_json, created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            event["event_id"],
            agent_id,
            state,
            message,
            ref_type,
            ref_id,
            _json_text(payload or {}),
            event["created_at"],
        ),
    )
    return event


def _format_activity(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    return {
        "event_id": data.get("event_id"),
        "agent_id": data.get("agent_id"),
        "state": data.get("state"),
        "message": data.get("message"),
        "ref_type": data.get("ref_type"),
        "ref_id": data.get("ref_id"),
        "payload": _parse_json(data.get("payload_json"), {}),
        "created_at": data.get("created_at_utc"),
    }


def _format_draft(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    draft = _parse_json(data.get("draft_json"), {})
    if isinstance(draft, dict):
        draft = dict(draft)
        draft["agent_report"] = _normalize_agent_report(draft, draft)
        cleaned_thesis = _clean_agent_text(draft.get("thesis"), 260)
        if cleaned_thesis:
            draft["thesis"] = cleaned_thesis
        else:
            draft["thesis"] = draft["agent_report"].get("summary") or ""
        draft["risk_notes"] = _clean_agent_notes(draft.get("risk_notes"))
    return {
        "draft_id": data.get("draft_id"),
        "name": data.get("name"),
        "lifecycle_state": data.get("lifecycle_state"),
        "created_by_type": data.get("created_by_type"),
        "created_by_id": data.get("created_by_id"),
        "current_version": data.get("current_version"),
        "draft": draft,
        "last_risk_report": _parse_json(data.get("last_risk_report_json"), {}),
        "last_simulation": _parse_json(data.get("last_simulation_json"), {}),
        "created_at": data.get("created_at_utc"),
        "updated_at": data.get("updated_at_utc"),
    }


def _load_draft(conn: sqlite3.Connection, draft_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM strategy_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
    return _format_draft(row) if row else None


def _resolve_market_identity(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        resolved = resolve_market_selection(condition_id=text, limit=1)
    except Exception:
        return {}
    selected = resolved.get("selected") if isinstance(resolved, dict) else {}
    return selected if isinstance(selected, dict) else {}


def _normalize_market(raw: Dict[str, Any]) -> Dict[str, Any]:
    market = dict(raw or {})
    instrument_id = str(market.get("instrument_id") or market.get("condition_id") or market.get("market_id") or "").strip()
    condition_id = str(market.get("condition_id") or market.get("market_id") or "").strip()
    resolved = _resolve_market_identity(condition_id or instrument_id)
    resolved_condition_id = str(resolved.get("condition_id") or "").strip()
    if resolved_condition_id:
        if not condition_id or resolved_condition_id.lower().startswith(condition_id.lower()) or condition_id.lower().startswith("0x"):
            condition_id = resolved_condition_id
        if not instrument_id or resolved_condition_id.lower().startswith(instrument_id.lower()) or instrument_id.lower().startswith("0x"):
            instrument_id = resolved_condition_id
    outcome = str(market.get("outcome") or market.get("side") or "YES").strip().upper()
    if outcome not in {"YES", "NO"}:
        outcome = "YES"
    return {
        **resolved,
        **market,
        "instrument_id": instrument_id,
        "condition_id": condition_id,
        "outcome": outcome,
        "action": str(market.get("action") or "buy").strip().lower(),
        "venue": str(market.get("venue") or "polymarket").strip().lower(),
        "max_entry_price": _safe_float(market.get("max_entry_price", market.get("max_price")), 0.0),
        "max_exposure_usdc": _safe_float(market.get("max_exposure_usdc", market.get("budget_cap")), 0.0),
        "yes_token": market.get("yes_token") or resolved.get("yes_token") or "",
        "no_token": market.get("no_token") or resolved.get("no_token") or "",
    }


_DEADLINE_PARAM_KEY = "Enddate"
_DEADLINE_PARAM_ALIASES = {"enddate", "endtime", "l0endtime"}


def _normalize_param_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _params_have_deadline(params: Dict[str, Any]) -> bool:
    for key, value in (params or {}).items():
        if _normalize_param_key(key) in _DEADLINE_PARAM_ALIASES and str(value or "").strip():
            return True
    return False


def _market_end_date_value(market: Dict[str, Any], *, allow_resolve: bool = False) -> str:
    if not isinstance(market, dict):
        return ""
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    for value in (
        market.get("end_date"),
        market.get("endDate"),
        market.get("Enddate"),
        market.get("EndDate"),
        raw.get("endDate"),
        raw.get("umaEndDate"),
        raw.get("end_date"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    if not allow_resolve:
        return ""
    condition_id = str(market.get("condition_id") or market.get("instrument_id") or market.get("market_id") or "").strip()
    if not condition_id:
        return ""
    try:
        resolved = resolve_market_selection(condition_id=condition_id, limit=1)
        selected = resolved.get("selected") if isinstance(resolved, dict) else {}
        if isinstance(selected, dict):
            return _market_end_date_value(selected, allow_resolve=False)
    except Exception:
        return ""
    return ""


def _first_market_end_date(markets: List[Dict[str, Any]], *, allow_resolve: bool = False) -> str:
    for market in markets or []:
        end_date = _market_end_date_value(market, allow_resolve=allow_resolve)
        if end_date:
            return end_date
    return ""


def _ensure_params_deadline(params: Dict[str, Any], markets: List[Dict[str, Any]], *, allow_resolve: bool = False) -> Dict[str, Any]:
    clean = dict(params or {})
    if _params_have_deadline(clean):
        return clean
    end_date = _first_market_end_date(markets, allow_resolve=allow_resolve)
    if end_date:
        clean[_DEADLINE_PARAM_KEY] = end_date
    return clean


def _clip_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _looks_encoding_damaged(text: Any) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    if "\ufffd" in text:
        return True
    question_count = text.count("?")
    if question_count < 6:
        return False
    question_ratio = question_count / max(len(text), 1)
    return question_ratio >= 0.18 or "???" in text


def _clean_agent_text(value: Any, limit: int = 220) -> str:
    text = _clip_text(value, limit)
    if _looks_encoding_damaged(text):
        return ""
    return text


def _clean_agent_notes(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _clean_agent_text(item, 260)
        if text:
            result.append(text)
    return result


def _format_agent_number(value: Any, digits: int = 4) -> str:
    num = _safe_float(value, 0.0)
    text = f"{num:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _market_report_text(markets: List[Dict[str, Any]]) -> str:
    if not markets:
        return "尚未绑定具体市场"
    items = []
    for market in markets[:2]:
        title = str(market.get("question") or market.get("title") or market.get("condition_id") or "未命名市场").strip()
        outcome = str(market.get("outcome") or "YES").upper()
        price = market.get("max_entry_price") or market.get("best_ask") or market.get("price")
        price_text = f"，入场上限 {_format_agent_number(price, 4)}" if _safe_float(price, 0.0) > 0 else ""
        items.append(f"{title} / {outcome}{price_text}")
    extra = f" 等 {len(markets)} 个市场" if len(markets) > 2 else ""
    return "；".join(items) + extra


def _budget_report_text(budget: Dict[str, Any], markets: List[Dict[str, Any]]) -> str:
    max_total = _safe_float(
        budget.get("max_total_usdc"),
        sum(_safe_float(market.get("max_exposure_usdc"), 0.0) for market in markets),
    )
    max_single = _safe_float(budget.get("max_single_order_usdc"), _safe_float(_limits().get("max_single_order_usdc"), 20.0))
    return f"总预算 {max_total:.2f} USDC，单笔上限 {max_single:.2f} USDC"


def _params_report_text(params: Dict[str, Any], markets: List[Dict[str, Any]]) -> str:
    keys = [
        "fair_price",
        "entry_edge",
        "full_entry_edge",
        "exit_edge",
        "stop_loss_pct",
        "take_profit_pct",
        "take_profit_price",
        "starter_position_ratio",
        "Enddate",
    ]
    parts = []
    for key in keys:
        if key in params and params.get(key) not in (None, ""):
            parts.append(f"{key}={params.get(key)}")
    if not parts and markets:
        prices = [market.get("max_entry_price") for market in markets if _safe_float(market.get("max_entry_price"), 0.0) > 0]
        if prices:
            parts.append("max_entry_price=" + "/".join(_format_agent_number(price, 4) for price in prices[:2]))
    return "，".join(parts) if parts else "参数尚未细化，审批前需要补齐核心入场和退出参数"


def _report_value(raw_report: Any, source: Dict[str, Any], key: str, aliases: List[str]) -> str:
    candidates: List[Any] = []
    if isinstance(raw_report, dict):
        candidates.extend(raw_report.get(item) for item in [key, *aliases])
    elif isinstance(raw_report, str):
        candidates.append(raw_report)
    candidates.extend(source.get(item) for item in [key, *aliases])
    for value in candidates:
        text = _clean_agent_text(value)
        if text:
            return text
    return ""


def _normalize_agent_report(source: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, str]:
    source = source if isinstance(source, dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    markets = draft.get("markets") if isinstance(draft.get("markets"), list) else []
    params = draft.get("params") if isinstance(draft.get("params"), dict) else {}
    budget = draft.get("budget") if isinstance(draft.get("budget"), dict) else {}
    raw_report = source.get("agent_report") if "agent_report" in source else draft.get("agent_report")
    market_text = _market_report_text(markets)
    budget_text = _budget_report_text(budget, markets)
    params_text = _params_report_text(params, markets)
    strategy_code = str(draft.get("strategy_code") or source.get("strategy_code") or "当前策略代码").strip()
    thesis = _clean_agent_text(source.get("thesis") or draft.get("thesis"), 220)
    generated = {
        "strategy_reason": thesis or f"选择 {market_text}，用 {strategy_code} 先做小额规则化草案，核心是让方向、价格和预算都进入人工审批。",
        "market_observation": f"当前草案绑定 {market_text}；这里只把市场价格和选择方向作为策略输入，不把单一盘口当作确定性判断。",
        "parameter_rationale": f"参数围绕 {params_text} 设置；{budget_text}，先限制仓位，再由入场边际和退出条件决定是否继续加仓或退出。",
        "risk_control": f"保持 {budget_text}，默认需要人工确认后才可落地；若风控失败、无成交、触发退出或临近结束，应优先降风险。",
        "human_review_focus": "请重点确认市场和方向是否选对、fair_price/entry_edge 是否符合你的判断、预算是否在可接受范围内。",
    }
    aliases = {
        "strategy_reason": ["reason", "why", "why_this_strategy", "selection_reason"],
        "market_observation": ["market_notes", "market_context", "observation"],
        "parameter_rationale": ["params_reason", "parameter_reason", "parameter_notes"],
        "risk_control": ["risk_plan", "risk"],
        "human_review_focus": ["review_focus", "human_check", "approval_focus"],
    }
    report = {}
    for field in AGENT_REPORT_FIELDS:
        key = field["key"]
        report[key] = _report_value(raw_report, source, key, aliases.get(key, [])) or generated[key]
    report["summary"] = _clip_text(report.get("strategy_reason") or generated["strategy_reason"], 180)
    return report


def _normalize_draft_payload(payload: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = dict(existing.get("draft") if existing else {})
    source.update(payload.get("draft") if isinstance(payload.get("draft"), dict) else payload)
    markets_raw = source.get("markets") or source.get("legs") or []
    markets = [_normalize_market(item) for item in markets_raw if isinstance(item, dict)]
    budget = source.get("budget") if isinstance(source.get("budget"), dict) else {}
    execution_rules = source.get("execution_rules") if isinstance(source.get("execution_rules"), dict) else {}
    exit_rules = source.get("exit_rules") if isinstance(source.get("exit_rules"), dict) else {}
    params = source.get("params") if isinstance(source.get("params"), dict) else {}
    params = _ensure_params_deadline(params, markets, allow_resolve=True)
    mode = str(source.get("mode") or source.get("state") or "Stop").strip() or "Stop"
    if mode not in VALID_STRATEGY_MODES:
        mode = "Stop"
    name = str(source.get("name") or payload.get("name") or (existing or {}).get("name") or "Agent Strategy Draft").strip()
    max_total = _safe_float(budget.get("max_total_usdc"), sum(_safe_float(m.get("max_exposure_usdc")) for m in markets))
    max_single = _safe_float(budget.get("max_single_order_usdc"), min(max_total, _safe_float(_limits().get("max_single_order_usdc"), 20.0)))
    draft = {
        "name": name,
        "strategy_code": str(source.get("strategy_code") or "").strip(),
        "mode": mode,
        "thesis": _clean_agent_text(source.get("thesis"), 260),
        "markets": markets,
        "budget": {
            **budget,
            "max_total_usdc": max_total,
            "max_single_order_usdc": max_single,
        },
        "execution_rules": {
            "order_type": str(execution_rules.get("order_type") or "limit").strip().lower(),
            "cooldown_seconds": int(_safe_float(execution_rules.get("cooldown_seconds"), 300)),
            "max_slippage_bps": _safe_float(execution_rules.get("max_slippage_bps"), 100.0),
            **{k: v for k, v in execution_rules.items() if k not in {"order_type", "cooldown_seconds", "max_slippage_bps"}},
        },
        "exit_rules": exit_rules,
        "params": params,
        "risk_notes": _clean_agent_notes(source.get("risk_notes")),
        "source_markets": source.get("source_markets") if isinstance(source.get("source_markets"), list) else [],
    }
    draft["agent_report"] = _normalize_agent_report(source, draft)
    if not draft["thesis"]:
        draft["thesis"] = draft["agent_report"].get("summary") or ""
    return draft


def get_capabilities() -> Dict[str, Any]:
    policy = _policy()
    policy["strategy_submission_template"] = {
        "fields": AGENT_REPORT_FIELDS,
        "prompt": AGENT_STRATEGY_SUBMISSION_PROMPT,
    }
    query_capabilities = json.loads(json.dumps(MARKET_QUERY_CAPABILITIES, ensure_ascii=False))
    defaults = _agent_defaults()
    if isinstance(defaults.get("scan_categories"), list) and defaults["scan_categories"]:
        query_capabilities["default_categories"] = list(defaults["scan_categories"])
    if isinstance(defaults.get("scan_sorts"), list) and defaults["scan_sorts"]:
        query_capabilities["default_sorts"] = list(defaults["scan_sorts"])
    policy["market_query_capabilities"] = query_capabilities
    policy["strategy_observation_capabilities"] = STRATEGY_OBSERVATION_CAPABILITIES
    policy["backtest_capabilities"] = BACKTEST_CAPABILITIES
    event_graph_capabilities = json.loads(json.dumps(EVENT_GRAPH_CAPABILITIES, ensure_ascii=False))
    event_graph_capabilities["approval_policy"] = _event_graph_approval_policy()
    policy["event_graph_capabilities"] = event_graph_capabilities
    return policy


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _listish(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if default is None:
        default = []
    if value is None or value == "":
        return list(default)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "")
    parts = []
    for chunk in text.replace("/", ",").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or list(default)


def _agent_category_text(payload: Dict[str, Any]) -> str:
    default_categories = _agent_defaults().get("scan_categories") or MARKET_QUERY_CAPABILITIES["default_categories"]
    categories = _listish(payload.get("categories", payload.get("category")), default_categories)
    return ",".join(categories)


def _price_bound(value: Any) -> Any:
    if value is None or str(value).strip() == "":
        return None
    number = _safe_float(value, -1.0)
    if number < 0:
        return None
    return max(0.0, min(1.0, number))


def _nested_price_bound(price_filters: Any, field: str, bound: str) -> Any:
    if not isinstance(price_filters, dict):
        return None
    item = price_filters.get(field)
    if isinstance(item, dict):
        return item.get(bound)
    if isinstance(item, (list, tuple)):
        idx = 0 if bound == "min" else 1
        return item[idx] if len(item) > idx else None
    return None


def _agent_price_filters(payload: Dict[str, Any]) -> Dict[str, tuple[Any, Any]]:
    nested = payload.get("price_filters")
    if isinstance(nested, str):
        nested = _parse_json(nested, {})
    pairs = {
        "yes_ask": (
            payload.get("yes_ask_min", payload.get("ask_min", _nested_price_bound(nested, "yes_ask", "min"))),
            payload.get("yes_ask_max", payload.get("ask_max", _nested_price_bound(nested, "yes_ask", "max"))),
        ),
        "yes_bid": (
            payload.get("yes_bid_min", payload.get("bid_min", _nested_price_bound(nested, "yes_bid", "min"))),
            payload.get("yes_bid_max", payload.get("bid_max", _nested_price_bound(nested, "yes_bid", "max"))),
        ),
        "no_ask": (
            payload.get("no_ask_min", _nested_price_bound(nested, "no_ask", "min")),
            payload.get("no_ask_max", _nested_price_bound(nested, "no_ask", "max")),
        ),
        "no_bid": (
            payload.get("no_bid_min", _nested_price_bound(nested, "no_bid", "min")),
            payload.get("no_bid_max", _nested_price_bound(nested, "no_bid", "max")),
        ),
    }
    return {
        field: (min_value, max_value)
        for field, (min_value, max_value) in pairs.items()
        if _price_bound(min_value) is not None or _price_bound(max_value) is not None
    }


def _agent_price_filter_response(price_filters: Dict[str, tuple[Any, Any]]) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for field, (min_value, max_value) in (price_filters or {}).items():
        item: Dict[str, float] = {}
        parsed_min = _price_bound(min_value)
        parsed_max = _price_bound(max_value)
        if parsed_min is not None:
            item["min"] = parsed_min
        if parsed_max is not None:
            item["max"] = parsed_max
        if item:
            result[field] = item
    return result


def _default_sort_order(sort_key: str) -> str:
    key = str(sort_key or "").strip().lower()
    for item in MARKET_QUERY_CAPABILITIES["sorts"]:
        if item["key"].lower() == key:
            return item["default_order"]
    return "asc" if key in {"spread", "end_date"} else "desc"


def _agent_sort_list(payload: Dict[str, Any]) -> List[str]:
    sorts = _listish(payload.get("sorts", payload.get("sort")), [])
    if not sorts:
        sorts = _agent_defaults().get("scan_sorts") or ["volume24h", "volume", "liquidity", "spread"]
    seen = set()
    result = []
    for sort in sorts:
        key = str(sort or "").strip()
        lower = key.lower()
        if key and lower not in seen:
            seen.add(lower)
            result.append(key)
    return result


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _market_key(market: Dict[str, Any]) -> str:
    return str(
        market.get("condition_id")
        or market.get("market_id")
        or market.get("slug")
        or market.get("question")
        or uuid.uuid4().hex
    )


def _market_metric(market: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    for key in keys:
        value = market.get(key)
        if value in (None, ""):
            value = raw.get(key)
        if value in (None, ""):
            continue
        return _safe_float(value, default)
    return default


def _market_raw_value(market: Dict[str, Any], *keys: str) -> Any:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    for key in keys:
        value = market.get(key)
        if value not in (None, ""):
            return value
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_url(market: Dict[str, Any]) -> str:
    url = str(market.get("url") or "").strip()
    if url:
        return url
    event_slug = str(market.get("event_slug") or "").strip()
    slug = str(market.get("slug") or "").strip()
    if event_slug and slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if slug:
        return f"https://polymarket.com/event/{slug}"
    question = str(market.get("question") or "").strip()
    return f"https://polymarket.com/search?q={question}" if question else "https://polymarket.com"


def _market_tokens(market: Dict[str, Any]) -> Dict[str, str]:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    yes_token = str(market.get("yes_token") or "").strip()
    no_token = str(market.get("no_token") or "").strip()
    if yes_token and no_token:
        return {"yes": yes_token, "no": no_token}
    tokens = raw.get("clobTokenIds") or raw.get("clob_token_ids") or raw.get("tokenIds")
    outcomes = raw.get("outcomes") or raw.get("shortOutcomes")
    token_items = _parse_json(tokens, []) if isinstance(tokens, str) else (tokens if isinstance(tokens, list) else [])
    outcome_items = _parse_json(outcomes, []) if isinstance(outcomes, str) else (outcomes if isinstance(outcomes, list) else [])
    for idx, outcome in enumerate(outcome_items):
        token = str(token_items[idx] if idx < len(token_items) else "").strip()
        name = str(outcome or "").strip().lower()
        if name == "yes" and token:
            yes_token = yes_token or token
        if name == "no" and token:
            no_token = no_token or token
    return {"yes": yes_token, "no": no_token}


def agent_list_market_categories(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("market.category.list", actor_type)
    limit = max(0, min(_safe_int(payload.get("limit"), 120), 500))
    force_refresh = bool(payload.get("refresh") or payload.get("force_refresh"))
    data = list_market_categories(force_refresh=force_refresh, limit=limit)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "count": len(data),
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="market.category.list", target_type="market_category", input_data=payload, output_data=result)
    return result


def agent_search_markets(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("market.search", actor_type)
    limit = max(1, min(_safe_int(payload.get("limit"), 60), 200))
    query = str(payload.get("q", payload.get("query", "")) or "").strip()
    category = _agent_category_text(payload) if payload.get("category") or payload.get("categories") else str(payload.get("category") or "")
    sort_by = str(payload.get("sort", payload.get("sort_by", "")) or "").strip()
    sort_dir = str(payload.get("order", payload.get("sort_dir", _default_sort_order(sort_by))) or "desc").strip()
    force_refresh = bool(payload.get("refresh") or payload.get("force_refresh"))
    price_filters = _agent_price_filters(payload)
    data = search_markets(
        query=query,
        category=category,
        limit=limit,
        force_refresh=force_refresh,
        sort_by=sort_by,
        sort_dir=sort_dir,
        price_filters=price_filters,
    )
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "query": query,
        "category": category,
        "sort": sort_by,
        "order": sort_dir,
        "price_filters": _agent_price_filter_response(price_filters),
        "count": len(data),
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="market.search", target_type="market_query", input_data=payload, output_data=result)
    return result


def agent_resolve_market(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("market.resolve", actor_type)
    limit = max(1, min(_safe_int(payload.get("limit"), 20), 100))
    data = resolve_market_selection(
        query=str(payload.get("q", payload.get("query", "")) or ""),
        condition_id=str(payload.get("condition_id") or ""),
        token_id=str(payload.get("token_id") or ""),
        limit=limit,
        force_refresh=bool(payload.get("refresh") or payload.get("force_refresh")),
    )
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="market.resolve", target_type="market", input_data=payload, output_data=result)
    return result


def agent_hot_market_scan(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("market.hot_scan", actor_type)
    query = str(payload.get("q", payload.get("query", "")) or "").strip()
    category = _agent_category_text(payload)
    sorts = _agent_sort_list(payload)
    per_sort_limit = max(1, min(_safe_int(payload.get("limit"), 30), 200))
    candidate_limit = max(1, min(_safe_int(payload.get("candidate_limit"), per_sort_limit), 200))
    force_refresh = bool(payload.get("refresh") or payload.get("force_refresh"))
    price_filters = _agent_price_filters(payload)
    rankings = []
    by_key: Dict[str, Dict[str, Any]] = {}
    score_by_key: Dict[str, float] = {}
    hits_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for sort in sorts:
        order = str(payload.get("order") or payload.get("sort_dir") or _default_sort_order(sort))
        markets = search_markets(
            query=query,
            category=category,
            limit=per_sort_limit,
            force_refresh=force_refresh,
            sort_by=sort,
            sort_dir=order,
            price_filters=price_filters,
        )
        rankings.append({"sort": sort, "order": order, "count": len(markets), "markets": markets})
        for idx, market in enumerate(markets):
            key = _market_key(market)
            by_key.setdefault(key, market)
            rank_score = max(1.0, float(per_sort_limit - idx))
            score_by_key[key] = score_by_key.get(key, 0.0) + rank_score
            hits_by_key.setdefault(key, []).append({"sort": sort, "order": order, "rank": idx + 1, "score": rank_score})
    candidates = []
    for key, market in by_key.items():
        item = dict(market)
        item["agent_scan"] = {
            "score": round(score_by_key.get(key, 0.0), 4),
            "ranking_hits": hits_by_key.get(key, []),
        }
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            _safe_float((item.get("agent_scan") or {}).get("score"), 0.0),
            _market_metric(item, "volume_24h", "volume24hr", "volume24hrClob"),
            _market_metric(item, "liquidity", "liquidityNum", "liquidity_clob", "liquidityClob"),
        ),
        reverse=True,
    )
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "query": query,
        "category": category,
        "sorts": sorts,
        "price_filters": _agent_price_filter_response(price_filters),
        "limit_per_sort": per_sort_limit,
        "candidate_count": min(len(candidates), candidate_limit),
        "candidates": candidates[:candidate_limit],
        "rankings": rankings,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="market.hot_scan", target_type="market_scan", input_data=payload, output_data=result)
    return result


def agent_list_strategies(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=100, max_limit=500)
    _require_agent_capability("strategy.read_all", actor_type)
    sync_stats = _truthy(payload.get("sync_stats"), False)
    data = fetch_strategy_monitoring(limit=limit, sync_stats=sync_stats, allow_remote_positions=False)
    data = _maybe_strip_pnl(data, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.read_all", target_type="strategy", input_data=payload, output_data=result)
    return result


def agent_get_strategy(strategy_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.detail.read", actor_type)
    detail = fetch_strategy_detail(int(strategy_id), allow_remote_positions=False)
    detail = _maybe_strip_pnl(detail, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "strategy_id": int(strategy_id),
        "read_only": True,
        "data": detail,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.detail.read", target_type="strategy", target_id=str(strategy_id), input_data=payload, output_data=result)
    return result


def agent_get_strategy_workspace(strategy_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.workspace.read", actor_type)
    include_events = _truthy(payload.get("include_events"), False)
    if include_events:
        _require_agent_capability("strategy.events.read", actor_type)
    data = get_strategy_workspace(int(strategy_id), include_events=include_events)
    data = _maybe_strip_pnl(data, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "strategy_id": int(strategy_id),
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.workspace.read", target_type="strategy", target_id=str(strategy_id), input_data=payload, output_data=result)
    return result


def agent_get_strategy_usedata(strategy_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.usedata.read", actor_type)
    include_live_orderbook = _truthy(payload.get("live_orderbook"), True)
    data = get_strategy_usedata_snapshot(int(strategy_id), include_live_orderbook=include_live_orderbook)
    data = _maybe_strip_pnl(data, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "strategy_id": int(strategy_id),
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.usedata.read", target_type="strategy", target_id=str(strategy_id), input_data=payload, output_data=result)
    return result


def agent_get_strategy_events(strategy_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.events.read", actor_type)
    data = list_strategy_events(int(strategy_id), payload)
    data = _maybe_strip_pnl(data, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "strategy_id": int(strategy_id),
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.events.read", target_type="strategy", target_id=str(strategy_id), input_data=payload, output_data=result)
    return result


def agent_get_strategy_state(strategy_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.state.read", actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "strategy_id": int(strategy_id),
        "read_only": True,
        "data": read_strategy_state_bundle(int(strategy_id)),
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.state.read", target_type="strategy", target_id=str(strategy_id), input_data=payload, output_data=result)
    return result


def agent_list_backtest_cases(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=100, max_limit=500)
    _require_agent_capability("backtest.read", actor_type)
    data = history_list_backtest_cases()[:limit]
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.read", target_type="backtest_case", input_data=payload, output_data={"count": len(data)})
    return result


def agent_create_backtest_case(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("backtest.case.create", actor_type)
    case = history_create_backtest_case(payload)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": False,
        "data": case,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.case.create", target_type="backtest_case", target_id=str(case.get("case_id") or ""), input_data=payload, output_data=case)
    return result


def agent_list_backtest_runs(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=100, max_limit=500)
    _require_agent_capability("backtest.read", actor_type)
    case_id = _safe_int(payload.get("case_id"), 0) or None
    batch_id = str(payload.get("batch_id") or "").strip()
    runs = history_list_backtest_runs(case_id=case_id, batch_id=batch_id)[:limit]
    data = _maybe_strip_pnl(runs, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.read", target_type="backtest_run", input_data=payload, output_data={"count": len(runs)})
    return result


def agent_create_backtest_run(case_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("backtest.run.create", actor_type)
    run = history_create_backtest_run(int(case_id), payload)
    data = _maybe_strip_pnl(run, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": False,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.run.create", target_type="backtest_run", target_id=str(run.get("run_id") or ""), input_data=payload, output_data={"run_id": run.get("run_id"), "status": run.get("status"), "batch_id": run.get("batch_id")})
    return result


def agent_get_backtest_run(run_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("backtest.read", actor_type)
    equity_limit = max(0, min(_safe_int(payload.get("equity_limit"), 1000), 10000))
    orders_limit = max(0, min(_safe_int(payload.get("orders_limit"), 1000), 10000))
    events_limit = max(0, min(_safe_int(payload.get("events_limit"), 300), 2000))
    run = history_get_backtest_run(int(run_id), equity_limit=equity_limit, orders_limit=orders_limit, events_limit=events_limit)
    if not run:
        raise ValueError("backtest run not found")
    data = _maybe_strip_pnl(run, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.read", target_type="backtest_run", target_id=str(run_id), input_data=payload, output_data={"run_id": run_id, "status": run.get("status")})
    return result


def agent_list_backtest_batches(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=50, max_limit=200)
    _require_agent_capability("backtest.read", actor_type)
    data = _maybe_strip_pnl(history_list_backtest_batches(limit=limit), actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.read", target_type="backtest_batch", input_data=payload, output_data={"count": len(data)})
    return result


def agent_create_backtest_batch(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("backtest.batch.create", actor_type)
    batch = history_create_backtest_batch(payload)
    data = _maybe_strip_pnl(batch, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": False,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.batch.create", target_type="backtest_batch", target_id=str(batch.get("batch_id") or ""), input_data=payload, output_data={"batch_id": batch.get("batch_id"), "case_count": batch.get("case_count"), "run_mode": batch.get("run_mode")})
    return result


def agent_get_backtest_batch(batch_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("backtest.read", actor_type)
    include_runs = _truthy(payload.get("include_runs"), True)
    batch = history_get_backtest_batch(batch_id, include_runs=include_runs)
    if not batch:
        raise ValueError("backtest batch not found")
    data = _maybe_strip_pnl(batch, actor_type)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "read_only": True,
        "data": data,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="backtest.read", target_type="backtest_batch", target_id=str(batch_id), input_data=payload, output_data={"batch_id": batch_id, "run_count": (batch.get("summary") or {}).get("run_count")})
    return result


def agent_get_event_graph(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    params = dict(payload)
    result = build_event_graph(params)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="event_graph",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"summary": result.get("summary"), "query": result.get("query")}),
    )
    return result


def agent_event_news_status(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    result = get_event_news_status()
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="event_news_status",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"events": result.get("events"), "observations": result.get("observations")}),
    )
    return result


def agent_list_event_graph_events(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    _, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=80, max_limit=300)
    _require_agent_capability("event.read", actor_type)
    include_observations = str(payload.get("include_observations", "1")).strip().lower() not in {"0", "false", "no"}
    query = str(payload.get("q") or payload.get("query") or "").strip()
    events = list_news_events(q=query, limit=limit, include_observations=include_observations)
    result = {"events": events, "count": len(events), "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="event",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(events), "query": query}),
    )
    return result


def agent_list_event_graph_observations(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    _, actor_type, actor_id, limit = _agent_read_payload(payload, default_limit=120, max_limit=500)
    _require_agent_capability("event.read", actor_type)
    event_id = str(payload.get("event_id") or "").strip()
    query = str(payload.get("q") or payload.get("query") or "").strip()
    observations = list_news_observations(event_id=event_id, q=query, limit=limit)
    result = {"observations": observations, "count": len(observations), "event_id": event_id, "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="event_observation",
        target_id=event_id,
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(observations), "event_id": event_id, "query": query}),
    )
    return result


def agent_refresh_event_news(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.news.refresh", actor_type)
    limit = _safe_int(payload.get("limit_per_source", payload.get("limit", 24)), 24)
    result = refresh_news(limit_per_source=limit)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.news.refresh",
        target_type="event_news",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value(result),
    )
    return result


def agent_search_event_news(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.news.search", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    if not query:
        raise ValueError("q is required")
    limit = _safe_int(payload.get("limit_per_source", payload.get("limit", 30)), 30)
    result = refresh_news(query=query, limit_per_source=limit)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.news.search",
        target_type="event_news",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value(result),
    )
    return result


_LOGICAL_RELATIONS = LOGICAL_RELATIONS
_IMPACT_RELATIONS = IMPACT_RELATIONS
_CAUSAL_RELATIONS = CAUSAL_RELATIONS
_SCENARIO_RELATIONS = SCENARIO_RELATIONS
_EVIDENCE_RELATIONS = EVIDENCE_RELATIONS
_MAPPING_RELATIONS = MAPPING_RELATIONS
_MARKET_MOVE_RELATIONS = MARKET_MOVE_RELATIONS
_REASONING_RELATION_CLASSES = REASONING_RELATION_CLASSES
_LOW_RISK_ACTIONS: set[str] = set()
_HIGH_RISK_ACTIONS = {
    "edge_create",
    "edge_update",
    "edge_delete",
    "event_merge",
    "event_archive",
    "finance_archive",
    "finance_mapping_create",
    "expression_create",
    "expression_update",
    "expression_archive",
    "archive",
    "merge",
}
_EVENT_GRAPH_APPLY_ACTIONS = {
    "event_create",
    "event_update",
    "event_archive",
    "event_merge",
    "finance_create",
    "finance_update",
    "finance_archive",
    "edge_create",
    "edge_update",
    "edge_delete",
    "finance_mapping_create",
    "expression_create",
    "expression_update",
    "expression_archive",
}


def _event_patch_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_patch = payload.get("patch")
    if not isinstance(raw_patch, dict):
        raw_patch = payload.get("proposed_changes")
    if isinstance(raw_patch, list):
        raw_patch = {"items": raw_patch}
    if not isinstance(raw_patch, dict):
        raw_patch = {}
    items = raw_patch.get("items") if isinstance(raw_patch.get("items"), list) else []
    if not items and payload.get("change_type"):
        proposed = payload.get("proposed_changes") if isinstance(payload.get("proposed_changes"), dict) else {}
        item = dict(proposed)
        item.setdefault("action", str(payload.get("change_type") or "").strip())
        items = [item]
    normalized_items = [dict(item) for item in items if isinstance(item, dict)]
    patch = dict(raw_patch)
    patch["items"] = normalized_items
    return patch


def _event_patch_target_refs(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    seen = set()
    keys = {
        "event_id": "event",
        "target_event_id": "event",
        "canonical_event_id": "event",
        "source_event_id": "event",
        "finance_id": "finance",
        "source_finance_id": "finance",
        "target_finance_id": "finance",
        "edge_id": "edge",
        "expression_id": "expression",
        "source_id": "node",
        "target_id": "node",
        "observation_id": "observation",
    }
    for item in items:
        for key, kind in keys.items():
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            identity = (kind, value)
            if identity in seen:
                continue
            seen.add(identity)
            refs.append({"type": kind, "id": value})
        for key in ("source_event_ids", "merged_event_ids", "event_ids"):
            raw_values = item.get(key)
            if not isinstance(raw_values, list):
                continue
            for raw_value in raw_values:
                value = str(raw_value or "").strip()
                if not value:
                    continue
                identity = ("event", value)
                if identity in seen:
                    continue
                seen.add(identity)
                refs.append({"type": "event", "id": value})
    return refs[:40]


def _highest_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 1) >= order.get(right, 1) else right


def _validate_event_graph_patch_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    patch = _event_patch_from_payload(payload)
    items = patch.get("items") if isinstance(patch.get("items"), list) else []
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    risk_level = "low"
    normalized_items: List[Dict[str, Any]] = []

    if not items:
        errors.append({"code": "EMPTY_PATCH", "message": "patch.items or proposed_changes is required"})

    for index, raw in enumerate(items):
        item = dict(raw)
        action = str(item.get("action") or item.get("change_type") or "").strip()
        if not action:
            errors.append({"code": "ACTION_REQUIRED", "index": index, "message": "patch item action is required"})
            action = "unknown"
        action_key = action.lower()
        item["action"] = action_key
        if action_key != "unknown" and action_key not in _EVENT_GRAPH_APPLY_ACTIONS:
            errors.append({
                "code": "UNSUPPORTED_APPLY_ACTION",
                "index": index,
                "action": action_key,
                "message": f"Action '{action_key}' cannot be applied to Graph Core in this release. Use one of: {', '.join(sorted(_EVENT_GRAPH_APPLY_ACTIONS))}",
            })

        relation_class = str(item.get("relation_class") or "").strip().upper()
        relation_type = str(item.get("relation_type") or "").strip().upper()
        if not relation_class and relation_type:
            relation_class = infer_relation_class(relation_type)
        if relation_class:
            item["relation_class"] = relation_class
        if relation_type:
            item["relation_type"] = relation_type

        if action_key in _HIGH_RISK_ACTIONS or relation_class in {"IMPACT", "MAPPING", "CAUSAL", "SCENARIO", "MARKET_MOVE"} or relation_type in _MAPPING_RELATIONS:
            risk_level = _highest_risk(risk_level, "high")
        elif action_key not in _LOW_RISK_ACTIONS:
            risk_level = _highest_risk(risk_level, "medium")

        if action_key in {"event_update", "event_archive"} and not str(item.get("event_id") or item.get("target_id") or "").strip():
            errors.append({"code": "EVENT_ID_REQUIRED", "index": index, "message": "event update/archive requires event_id"})
        if action_key == "event_merge":
            target_event_id = str(item.get("target_event_id") or item.get("canonical_event_id") or item.get("event_id") or item.get("target_id") or "").strip()
            source_values = []
            for key in ("source_event_ids", "merged_event_ids", "event_ids"):
                raw_values = item.get(key)
                if isinstance(raw_values, list):
                    source_values.extend(raw_values)
            if item.get("source_event_id") or item.get("source_id"):
                source_values.append(item.get("source_event_id") or item.get("source_id"))
            source_ids = [str(value or "").strip() for value in source_values if str(value or "").strip() and str(value or "").strip() != target_event_id]
            if not target_event_id:
                errors.append({"code": "MERGE_TARGET_REQUIRED", "index": index, "message": "event merge requires target_event_id"})
            if not source_ids:
                errors.append({"code": "MERGE_SOURCE_REQUIRED", "index": index, "message": "event merge requires at least one source_event_id"})
        if action_key in {"finance_update", "finance_archive"} and not str(item.get("finance_id") or item.get("target_id") or "").strip():
            errors.append({"code": "FINANCE_ID_REQUIRED", "index": index, "message": "finance update/archive requires finance_id"})
        if action_key == "finance_create" and not str(item.get("finance_id") or item.get("label") or item.get("name") or item.get("symbol") or "").strip():
            warnings.append({"code": "FINANCE_ID_RECOMMENDED", "index": index, "message": "finance create should include finance_id, label, or symbol"})
        if action_key == "expression_create" and not any(item.get(key) for key in ("expression", "formula", "condition")):
            errors.append({"code": "EXPRESSION_REQUIRED", "index": index, "message": "expression create requires expression, formula, or condition"})
        if action_key in {"expression_create", "expression_update"}:
            expression = item.get("expression") if isinstance(item.get("expression"), dict) else {}
            if expression:
                operator = str(expression.get("operator") or "").strip().upper()
                if operator:
                    expression = dict(expression)
                    expression["operator"] = operator
                    item["expression"] = expression
                expression_errors, expression_warnings = validate_expression_shape(expression)
                for error in expression_errors:
                    errors.append({"index": index, **error})
                for warning in expression_warnings:
                    warnings.append({"index": index, **warning})
        if action_key in {"expression_update", "expression_archive"} and not str(item.get("expression_id") or "").strip():
            errors.append({"code": "EXPRESSION_ID_REQUIRED", "index": index, "message": "expression update/archive requires expression_id"})

        if "edge" in action_key or action_key == "finance_mapping_create" or relation_type or relation_class:
            edge_id = str(item.get("edge_id") or "").strip()
            requires_edge_endpoints = action_key in {"edge_create", "finance_mapping_create"} or not edge_id
            if requires_edge_endpoints and not str(item.get("source_id") or item.get("source_event_id") or item.get("source_finance_id") or "").strip():
                errors.append({"code": "EDGE_SOURCE_REQUIRED", "index": index, "message": "edge item requires source_id"})
            if requires_edge_endpoints and not str(item.get("target_id") or item.get("target_event_id") or item.get("target_finance_id") or "").strip():
                errors.append({"code": "EDGE_TARGET_REQUIRED", "index": index, "message": "edge item requires target_id"})
            if requires_edge_endpoints and not relation_type:
                errors.append({"code": "RELATION_TYPE_REQUIRED", "index": index, "message": "edge item requires relation_type"})
            if relation_class and not relation_class_is_known(relation_class):
                errors.append({"code": "INVALID_RELATION_CLASS", "index": index, "relation_class": relation_class})
            if relation_class and relation_type and not relation_type_is_valid(relation_class, relation_type):
                errors.append({
                    "code": f"INVALID_{relation_class}_RELATION",
                    "index": index,
                    "relation_class": relation_class,
                    "relation_type": relation_type,
                })
            if relation_class in {"IMPACT", "MAPPING", "CAUSAL", "SCENARIO", "MARKET_MOVE"} and not str(item.get("mechanism") or item.get("reason") or "").strip():
                warnings.append({"code": "MECHANISM_RECOMMENDED", "index": index, "message": "non-logical edges should include mechanism or reason"})
            if relation_class in {"IMPACT", "MAPPING", "CAUSAL", "SCENARIO", "MARKET_MOVE"} and not item.get("evidence_refs") and not payload.get("evidence_summary") and not item.get("evidence_summary"):
                warnings.append({"code": "EVIDENCE_RECOMMENDED", "index": index, "message": "non-logical edges should include evidence_refs or evidence_summary"})

        if item.get("confidence") not in (None, ""):
            confidence = _safe_float(item.get("confidence"), -1.0)
            if confidence < 0.0 or confidence > 1.0:
                errors.append({"code": "CONFIDENCE_RANGE", "index": index, "message": "confidence must be between 0 and 1"})
            else:
                item["confidence"] = round(confidence, 4)

        normalized_items.append(item)

    conflict_errors, conflict_warnings = validate_logical_conflicts(normalized_items)
    errors.extend(conflict_errors)
    warnings.extend(conflict_warnings)
    reasoning_errors, reasoning_warnings = validate_reasoning_edges(normalized_items, payload=payload)
    errors.extend(reasoning_errors)
    warnings.extend(reasoning_warnings)

    normalized_patch = dict(patch)
    normalized_patch["items"] = normalized_items
    target_refs = _event_patch_target_refs(normalized_items)
    return {
        "valid": not errors,
        "risk_level": risk_level,
        "requires_human_review": risk_level != "low",
        "errors": errors,
        "warnings": warnings,
        "normalized_patch": normalized_patch,
        "target_refs": target_refs,
    }


def _event_graph_item_has_evidence(item: Dict[str, Any]) -> bool:
    if item.get("evidence_summary") or item.get("reason") or item.get("source_url"):
        return True
    refs = item.get("evidence_refs")
    return isinstance(refs, list) and bool(refs)


def _event_graph_auto_apply_decision(validation: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    policy = _event_graph_approval_policy()
    mode = str(policy.get("mode") or "manual")
    decision: Dict[str, Any] = {
        "mode": mode,
        "enabled": False,
        "reason": "manual approval mode",
        "actor_type": "system",
        "actor_id": policy.get("auto_apply_actor_id") or "event_graph_trusted_rule",
        "policy": policy,
    }
    if mode == "manual":
        return decision
    if not validation.get("valid"):
        decision["reason"] = "patch validation failed"
        return decision
    risk_level = str(validation.get("risk_level") or "medium").strip().lower()
    if mode == "trusted_low_risk" and risk_level != "low":
        decision["reason"] = f"risk level {risk_level} requires human review"
        return decision

    patch = validation.get("normalized_patch") if isinstance(validation.get("normalized_patch"), dict) else {}
    items = patch.get("items") if isinstance(patch.get("items"), list) else []
    max_items = int(policy.get("max_items_per_request") or 100)
    if not items:
        decision["reason"] = "patch has no items"
        return decision
    if len(items) > max_items:
        decision["reason"] = f"patch has {len(items)} items, above auto limit {max_items}"
        return decision

    unsupported = sorted({
        str(item.get("action") or "").strip().lower()
        for item in items
        if isinstance(item, dict) and str(item.get("action") or "").strip().lower() not in _EVENT_GRAPH_APPLY_ACTIONS
    })
    if unsupported:
        decision["reason"] = "unsupported auto-apply action(s): " + ", ".join(unsupported)
        return decision

    logic_sensitive = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        relation_class = str(item.get("relation_class") or "").strip().upper()
        relation_type = str(item.get("relation_type") or "").strip().upper()
        if action.startswith("expression_"):
            logic_sensitive.append(action)
        if relation_class == "LOGICAL" or relation_type in _LOGICAL_RELATIONS:
            logic_sensitive.append(relation_type or "LOGICAL")
    if logic_sensitive:
        decision["reason"] = "logic relations and expressions require manual review in this release"
        decision["logic_sensitive_items"] = sorted(set(logic_sensitive))
        return decision

    min_confidence = float(policy.get("min_confidence") or 0.0)
    if min_confidence > 0:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if "confidence" not in item or _safe_float(item.get("confidence"), -1.0) < min_confidence:
                decision["reason"] = f"item {index} confidence below {min_confidence}"
                return decision

    evidence = str(payload.get("evidence_summary") or payload.get("rationale") or payload.get("reason") or "").strip()
    if policy.get("require_evidence_summary") and not evidence and not any(
        isinstance(item, dict) and _event_graph_item_has_evidence(item)
        for item in items
    ):
        decision["reason"] = "evidence summary is required by EventGraph approval settings"
        return decision

    decision["enabled"] = True
    decision["reason"] = f"auto-apply allowed by EventGraph approval mode {mode}"
    decision["risk_level"] = risk_level
    return decision


def _apply_event_graph_approval_policy(validation: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = dict(validation or {})
    base_requires_review = bool(result.get("requires_human_review"))
    decision = _event_graph_auto_apply_decision(result, payload)
    result["base_requires_human_review"] = base_requires_review
    result["requires_human_review"] = not bool(decision.get("enabled"))
    result["auto_apply_decision"] = decision
    return result


def _maybe_auto_apply_event_graph_change_request(
    result: Dict[str, Any],
    *,
    validation: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    decision = validation.get("auto_apply_decision") if isinstance(validation.get("auto_apply_decision"), dict) else _event_graph_auto_apply_decision(validation, payload)
    request_id = str(result.get("request_id") or "").strip()
    if not request_id or not decision.get("enabled"):
        result["auto_apply"] = decision
        return result

    actor_type = str(decision.get("actor_type") or "system")
    actor_id = str(decision.get("actor_id") or "event_graph_trusted_rule")
    review_note = f"Auto-approved by EventGraph approval mode {decision.get('mode')}"
    try:
        reviewed = review_change_request(
            request_id,
            decision="approve",
            reviewer_type=actor_type,
            reviewer_id=actor_id,
            note=review_note,
        )
        _audit_request(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="event.graph.review.auto_approve",
            target_type="event_graph_change_request",
            target_id=request_id,
            input_data={"request_id": request_id, "decision": decision},
            output_data={"request_id": request_id, "status": reviewed.get("status")},
            policy_decision="auto_allow",
            risk_decision="pass",
        )
        applied = apply_change_request(request_id, actor_type=actor_type, actor_id=actor_id)
        _audit_request(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="event.graph.apply.auto",
            target_type="event_graph_change_request",
            target_id=request_id,
            input_data={"request_id": request_id, "decision": decision},
            output_data=applied,
            policy_decision="auto_allow",
            risk_decision="pass",
        )
        final = get_change_request(request_id)
        final["auto_apply"] = {**decision, "reviewed_status": reviewed.get("status"), "apply_result": applied}
        return final
    except Exception as exc:
        final = get_change_request(request_id)
        final["auto_apply"] = {**decision, "error": str(exc)}
        _audit_request(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="event.graph.apply.auto",
            target_type="event_graph_change_request",
            target_id=request_id,
            input_data={"request_id": request_id, "decision": decision},
            output_data={"error": str(exc), "status": final.get("status")},
            policy_decision="auto_allow",
            risk_decision="blocked",
        )
        return final


def agent_validate_event_graph_patch(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.graph.patch.validate", actor_type)
    result = _apply_event_graph_approval_policy(_validate_event_graph_patch_payload(payload), payload)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.graph.patch.validate",
        target_type="event_graph_patch",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value(result),
        risk_decision="blocked" if not result.get("valid") else ("review_required" if result.get("requires_human_review") else "not_required"),
    )
    return result


def agent_submit_change_request(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.graph.change_request", actor_type)
    change_type = str(payload.get("change_type") or payload.get("action") or "").strip()
    if not change_type:
        raise ValueError("change_type is required")
    validation = _apply_event_graph_approval_policy(_validate_event_graph_patch_payload(payload), payload)
    proposed = payload.get("proposed_changes") or {}
    patch = validation.get("normalized_patch") if isinstance(validation.get("normalized_patch"), dict) else {}
    evidence = str(payload.get("evidence_summary") or payload.get("rationale") or payload.get("reason") or "").strip()
    run_id = str(payload.get("run_id") or payload.get("agent_run_id") or "").strip()
    workflow_id = str(payload.get("workflow_id") or payload.get("workflow") or "EXT_GRAPH_CHANGE_REQUEST").strip()
    result = create_change_request(
        change_type=change_type,
        requester=actor_id or "agent",
        requester_type=actor_type,
        requester_id=actor_id,
        run_id=run_id,
        workflow_id=workflow_id,
        title=str(payload.get("title") or change_type).strip(),
        summary=str(payload.get("summary") or "").strip(),
        reason=str(payload.get("reason") or payload.get("rationale") or "").strip(),
        evidence_summary=evidence,
        risk_level=str(validation.get("risk_level") or "medium"),
        status="PENDING" if validation.get("valid") else "NEEDS_CHANGES",
        patch=patch,
        validation=validation,
        target_refs=validation.get("target_refs") if isinstance(validation.get("target_refs"), list) else [],
        payload={
            "proposed_changes": proposed,
            "evidence_summary": evidence,
            "raw": payload,
        },
    )
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.graph.change_request",
        target_type="event_graph_change_request",
        input_data=_compact_audit_value({"change_type": change_type, "evidence": evidence}),
        output_data=_compact_audit_value(result),
        risk_decision="blocked" if not validation.get("valid") else ("review_required" if validation.get("requires_human_review") else "not_required"),
    )
    return _maybe_auto_apply_event_graph_change_request(result, validation=validation, payload=payload)


def agent_list_change_requests(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    status_filter = str(payload.get("status") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    items = list_change_requests(status=status_filter, limit=limit)
    return {"count": len(items), "items": items}


def agent_get_change_request(request_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    item = get_change_request(request_id)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="event_graph_change_request",
        target_id=str(request_id or ""),
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"request_id": item.get("request_id"), "status": item.get("status")}),
    )
    return item


def agent_list_graph_core_events(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    events = list_graph_events(q=query, limit=limit)
    result = {"events": events, "count": len(events), "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core_event",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(events), "query": query}),
    )
    return result


def agent_list_graph_core(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    result = list_graph_core(q=query, limit=limit)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value(result.get("summary") or {}),
    )
    return result


def agent_list_graph_core_finance_nodes(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    items = list_graph_finance_nodes(q=query, limit=limit)
    result = {"finance_nodes": items, "count": len(items), "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core_finance_node",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(items), "query": query}),
    )
    return result


def agent_list_graph_core_edges(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    items = list_graph_edges(q=query, limit=limit)
    result = {"edges": items, "count": len(items), "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core_edge",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(items), "query": query}),
    )
    return result


def agent_list_graph_core_expressions(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    query = str(payload.get("q") or payload.get("query") or "").strip()
    limit = _safe_int(payload.get("limit", 50), 50)
    items = list_graph_expressions(q=query, limit=limit)
    result = {"expressions": items, "count": len(items), "query": query}
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core_expression",
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": len(items), "query": query}),
    )
    return result


def agent_list_graph_core_versions(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("event.read", actor_type)
    object_type = str(payload.get("object_type") or payload.get("type") or "").strip()
    object_id = str(payload.get("object_id") or payload.get("id") or "").strip()
    limit = _safe_int(payload.get("limit", 30), 30)
    result = list_graph_versions(object_type=object_type, object_id=object_id, limit=limit)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.read",
        target_type="graph_core_version",
        target_id=object_id,
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"count": result.get("count"), "object_type": result.get("object_type")}),
    )
    return result


def human_review_event_graph_change_request(request_id: str, payload: Optional[Dict[str, Any]] = None, *, decision: str) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type not in {"human", "admin"}:
        raise ValueError("only human/admin can review EventGraph change requests")
    if str(decision or "").strip().lower() in {"approve", "approved"}:
        _ensure_event_graph_change_request_applyable(get_change_request(request_id))
    item = review_change_request(
        request_id,
        decision=decision,
        reviewer_type=actor_type,
        reviewer_id=actor_id,
        note=str(payload.get("note") or payload.get("reason") or "").strip(),
    )
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability=f"event.graph.review.{decision}",
        target_type="event_graph_change_request",
        target_id=str(request_id or ""),
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value({"request_id": item.get("request_id"), "status": item.get("status")}),
    )
    return item


def _ensure_event_graph_change_request_applyable(request: Dict[str, Any]) -> None:
    validation = request.get("validation") if isinstance(request.get("validation"), dict) else {}
    if validation and validation.get("valid") is False:
        raise ValueError("cannot apply invalid change request")
    patch = request.get("patch") if isinstance(request.get("patch"), dict) else {}
    items = patch.get("items") if isinstance(patch.get("items"), list) else []
    if not items:
        raise ValueError("change request patch has no items")
    unsupported = sorted({
        str(item.get("action") or "").strip().lower()
        for item in items
        if isinstance(item, dict) and str(item.get("action") or "").strip().lower() not in _EVENT_GRAPH_APPLY_ACTIONS
    })
    if unsupported:
        raise ValueError("unsupported apply action(s): " + ", ".join(unsupported))


def human_apply_event_graph_change_request(request_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type not in {"human", "admin"}:
        raise ValueError("only human/admin can apply EventGraph change requests")
    _ensure_event_graph_change_request_applyable(get_change_request(request_id))
    result = apply_change_request(request_id, actor_type=actor_type, actor_id=actor_id)
    _audit_request(
        actor_type=actor_type,
        actor_id=actor_id,
        capability="event.graph.apply",
        target_type="event_graph_change_request",
        target_id=str(request_id or ""),
        input_data=_compact_audit_value(payload),
        output_data=_compact_audit_value(result),
    )
    return result


def human_approve_and_apply_event_graph_change_request(request_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type not in {"human", "admin"}:
        raise ValueError("only human/admin can approve and apply EventGraph change requests")
    request = get_change_request(request_id)
    status = str(request.get("status") or "").strip().upper()
    if status not in {"PENDING", "APPROVED", "APPLY_FAILED"}:
        raise ValueError("change request must be PENDING, APPROVED, or APPLY_FAILED before approve-and-apply")
    _ensure_event_graph_change_request_applyable(request)
    reviewed = request
    if status != "APPROVED":
        reviewed = human_review_event_graph_change_request(
            request_id,
            payload,
            decision="approve",
        )
    applied = human_apply_event_graph_change_request(request_id, payload)
    final = get_change_request(request_id)
    final["review_result"] = {
        "request_id": reviewed.get("request_id"),
        "status": reviewed.get("status"),
    }
    final["apply_result"] = applied
    return final


def _select_market_outcome(market: Dict[str, Any], mode: str = "yes") -> str:
    mode = str(mode or "yes").strip().lower()
    yes_price = _market_metric(market, "yes_price", default=0.0)
    no_price = _market_metric(market, "no_price", default=0.0)
    if mode == "no":
        return "NO"
    if mode in {"cheaper", "cheap"}:
        if no_price > 0 and (yes_price <= 0 or no_price < yes_price):
            return "NO"
        return "YES"
    if mode in {"balanced", "mid"}:
        yes_dist = abs((yes_price or 0.5) - 0.5)
        no_dist = abs((no_price or 0.5) - 0.5)
        return "NO" if no_dist < yes_dist else "YES"
    return "YES"


def _outcome_price(market: Dict[str, Any], outcome: str) -> float:
    outcome = str(outcome or "YES").upper()
    if outcome == "NO":
        price = _market_metric(market, "no_ask", "noAsk", "opp_ask_price", "no_price", default=0.0)
        if price > 0:
            return price
        yes_bid = _market_metric(market, "best_bid", default=0.0)
        return max(0.0, min(0.9999, 1.0 - yes_bid)) if yes_bid > 0 else 0.0
    ask = _market_metric(market, "yes_ask", "best_ask", "yesAsk", "bestAsk", default=0.0)
    if ask > 0:
        return ask
    return _market_metric(market, "yes_price", "last_trade_price", default=0.0)


def _build_agent_strategy_from_market(market: Dict[str, Any], payload: Dict[str, Any], scan_context: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _agent_defaults()
    outcome = _select_market_outcome(market, str(payload.get("selection_mode") or payload.get("outcome") or defaults.get("selection_mode") or "yes"))
    side_label = "Yes" if outcome == "YES" else "No"
    side_price = _outcome_price(market, outcome)
    entry_edge = _safe_float(payload.get("entry_edge"), 0.015)
    full_entry_edge = max(entry_edge + 0.001, _safe_float(payload.get("full_entry_edge"), 0.04))
    price_buffer = _safe_float(payload.get("entry_price_buffer"), max(0.004, min(0.02, side_price * 0.08 if side_price > 0 else 0.01)))
    max_entry_price = _safe_float(payload.get("max_entry_price"), min(0.98, side_price + price_buffer if side_price > 0 else 0.05))
    fair_price = _safe_float(payload.get("fair_price"), min(0.98, max_entry_price + entry_edge + max(0.01, max_entry_price * 0.06)))
    limits = _limits()
    max_budget = min(_safe_float(limits.get("max_strategy_budget_usdc"), 100.0), _safe_float(limits.get("max_market_exposure_usdc"), 50.0))
    default_budget = min(_safe_float(defaults.get("proposal_budget_usdc"), 20.0), max_budget)
    budget_usdc = _safe_float(payload.get("budget_usdc", payload.get("max_total_usdc")), default_budget)
    default_single_order = min(_safe_float(defaults.get("proposal_single_order_usdc"), 5.0), _safe_float(limits.get("max_single_order_usdc"), 20.0), budget_usdc)
    single_order = _safe_float(payload.get("max_single_order_usdc"), default_single_order)
    tokens = _market_tokens(market)
    condition_id = str(market.get("condition_id") or _market_raw_value(market, "conditionId") or "").strip()
    question = str(market.get("question") or _market_raw_value(market, "question") or condition_id or "Polymarket binary market").strip()
    category = str(market.get("category") or "Unknown").strip()
    end_date = str(market.get("end_date") or _market_raw_value(market, "endDate", "umaEndDate") or "").strip()
    volume_24h = _market_metric(market, "volume_24h", "volume24hr", "volume24hrClob")
    volume = _market_metric(market, "volume", "volumeNum", "volumeClob")
    liquidity = _market_metric(market, "liquidity", "liquidityNum", "liquidity_clob", "liquidityClob")
    spread = _market_metric(market, "spread")
    yes_bid = _market_metric(market, "yes_bid", "best_bid", "yesBid", "bestBid")
    yes_ask = _market_metric(market, "yes_ask", "best_ask", "yesAsk", "bestAsk")
    no_bid = _market_metric(market, "no_bid", "noBid", "opp_bid_price", "opp_bids_price")
    no_ask = _market_metric(market, "no_ask", "noAsk", "opp_ask_price")
    scan_hits = ((market.get("agent_scan") or {}).get("ranking_hits") or []) if isinstance(market.get("agent_scan"), dict) else []
    hit_text = ", ".join(f"{hit.get('sort')}#{hit.get('rank')}" for hit in scan_hits[:4]) or "single search"
    strategy_name = str(payload.get("strategy_name_prefix") or "Agent Hot Scan").strip()
    name = f"{strategy_name} - {question} {outcome}"
    thesis = (
        f"Agent 从 {scan_context.get('category') or category} 热门市场扫描中选出该市场；"
        f"排序命中 {hit_text}。本草案只做小额参数化提案，需要人工确认方向和 fair_price。"
    )
    agent_report = {
        "strategy_reason": (
            f"选择 {side_label} 方向，是因为该市场在本次扫描中命中 {hit_text}，"
            f"类别为 {category}，适合进入人工审批列表逐项判断。"
        ),
        "market_observation": (
            f"当前参考价格约 {side_price:.4f}，Yes bid/ask={yes_bid:.4f}/{yes_ask:.4f}，"
            f"No bid/ask={no_bid:.4f}/{no_ask:.4f}，spread={spread:.4f}，"
            f"24h volume≈{volume_24h:.0f}，total volume≈{volume:.0f}，liquidity≈{liquidity:.0f}。"
        ),
        "parameter_rationale": (
            f"fair_price={fair_price:.4f}、entry_edge={entry_edge:.4f}、max_entry_price={max_entry_price:.4f}；"
            f"预算 {budget_usdc:.2f} USDC、单笔 {single_order:.2f} USDC，用于先小额测试，不代表确定性判断。"
        ),
        "risk_control": (
            "Agent 只能提交人工确认，不能批准或实盘执行；若价格高于入场上限、风控失败、流动性变化或临近到期，应要求修改或拒绝。"
        ),
        "human_review_focus": (
            f"请确认市场问题是否匹配你的主题、{side_label} 方向是否合理、fair_price={fair_price:.4f} 是否符合你的主观判断。"
        ),
    }
    return {
        "actor_type": "agent",
        "actor_id": str(payload.get("actor_id") or DEFAULT_AGENT_ID),
        "reason": "agent hot market scan strategy proposal",
        "draft": {
            "name": name,
            "strategy_code": str(payload.get("strategy_code") or "Stragy_Fllow_Truth").strip(),
            "thesis": thesis,
            "agent_report": agent_report,
            "markets": [
                {
                    "source": "polymarket",
                    "type": "prediction_market_binary",
                    "venue": "polymarket",
                    "instrument_id": condition_id,
                    "condition_id": condition_id,
                    "market_id": str(_market_raw_value(market, "id", "market_id") or ""),
                    "question": question,
                    "title": question,
                    "slug": str(market.get("slug") or ""),
                    "event_slug": str(market.get("event_slug") or ""),
                    "url": _market_url(market),
                    "category": category,
                    "outcome": outcome,
                    "action": "buy",
                    "best_bid": yes_bid,
                    "best_ask": yes_ask,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "no_bid": no_bid,
                    "no_ask": no_ask,
                    "selected_outcome_price": side_price,
                    "last_trade_price": _market_metric(market, "last_trade_price"),
                    "spread": spread,
                    "liquidity": liquidity,
                    "volume": volume,
                    "volume_24h": volume_24h,
                    "active": bool(market.get("active", True)),
                    "closed": bool(market.get("closed", False)),
                    "accepting_orders": bool(_market_raw_value(market, "accepting_orders", "acceptingOrders") if _market_raw_value(market, "accepting_orders", "acceptingOrders") is not None else True),
                    "max_entry_price": round(max_entry_price, 4),
                    "max_exposure_usdc": budget_usdc,
                    "yes_token": tokens.get("yes") or "",
                    "no_token": tokens.get("no") or "",
                    "rules": market.get("rules") or _market_raw_value(market, "description", "rules") or "",
                    "snapshot": {
                        "source": "agent_hot_market_scan",
                        "scan_context": {
                            "category": scan_context.get("category"),
                            "sorts": scan_context.get("sorts"),
                            "ranking_hits": scan_hits,
                        },
                    },
                }
            ],
            "budget": {
                "max_total_usdc": budget_usdc,
                "max_single_order_usdc": single_order,
            },
            "execution_rules": {
                "order_type": "limit",
                "cooldown_seconds": _safe_int(payload.get("cooldown_seconds"), 300),
                "max_slippage_bps": _safe_float(payload.get("max_slippage_bps"), 40.0),
                "allow_market_order": False,
            },
            "exit_rules": {
                "take_profit_pct": _safe_float(payload.get("take_profit_pct"), 0.30),
                "stop_loss_pct": _safe_float(payload.get("stop_loss_pct"), 0.40),
                "no_add_days": _safe_int(payload.get("no_add_days"), 14),
                "de_risk_start_days": _safe_int(payload.get("de_risk_start_days"), 60),
            },
            "params": {
                "FactSide": side_label,
                "fair_price": round(fair_price, 4),
                "entry_edge": round(entry_edge, 4),
                "full_entry_edge": round(full_entry_edge, 4),
                "starter_position_ratio": _safe_float(payload.get("starter_position_ratio"), 0.25),
                "exit_edge": _safe_float(payload.get("exit_edge"), 0.004),
                "stop_loss_pct": _safe_float(payload.get("stop_loss_pct"), 0.40),
                "take_profit_pct": _safe_float(payload.get("take_profit_pct"), 0.30),
                "take_profit_order_mode": "trigger_exit",
                "use_momentum_exit_filter": True,
                "de_risk_start_days": _safe_int(payload.get("de_risk_start_days"), 60),
                "no_add_days": _safe_int(payload.get("no_add_days"), 14),
                "Enddate": end_date,
            },
            "risk_notes": [
                "Agent 热门市场扫描草案默认提交人工确认，不自动实盘。",
                "fair_price 是模板估值，需要人工按市场事实重新判断。",
            ],
            "source_markets": [market],
        },
    }


def propose_strategies_from_market_scan(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    if actor_type != "agent":
        actor_type = "agent"
    _require_agent_capability("strategy.batch.propose", actor_type)
    payload = {**payload, "actor_type": actor_type, "actor_id": actor_id}
    scan = agent_hot_market_scan(payload)
    candidates = scan.get("candidates") or []
    batch_limit = max(1, min(_safe_int(_agent_defaults().get("max_batch_drafts"), 5), 50))
    max_drafts = max(1, min(_safe_int(payload.get("max_drafts"), batch_limit), batch_limit))
    submit_for_approval = payload.get("submit_for_approval", True) is not False
    proposals = []
    for market in candidates[:max_drafts]:
        item: Dict[str, Any] = {"market": market, "ok": False}
        try:
            draft_payload = _build_agent_strategy_from_market(market, payload, scan)
            draft = create_draft(draft_payload)
            risk = risk_check(draft["draft_id"], {"actor_type": "agent", "actor_id": actor_id})
            simulation = simulate_draft(draft["draft_id"], {"actor_type": "agent", "actor_id": actor_id})
            approval = None
            if submit_for_approval:
                approval = submit_draft(
                    draft["draft_id"],
                    {"actor_type": "agent", "actor_id": actor_id, "note": "agent hot market scan proposal"},
                )
            item.update({
                "ok": True,
                "draft": draft,
                "risk": risk,
                "simulation": simulation,
                "approval": approval,
            })
        except Exception as exc:
            item["error"] = str(exc)
        proposals.append(item)
    result = {
        "actor": {"type": actor_type, "id": actor_id},
        "scan": {key: value for key, value in scan.items() if key != "rankings"},
        "submit_for_approval": submit_for_approval,
        "requested": max_drafts,
        "created": len([item for item in proposals if item.get("ok")]),
        "proposals": proposals,
    }
    _audit_request(actor_type=actor_type, actor_id=actor_id, capability="strategy.batch.propose", target_type="market_scan", input_data=payload, output_data=result)
    return result


def list_activity(limit: int = 50) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM agent_activity_events ORDER BY created_at_utc DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_format_activity(row) for row in rows]
    finally:
        conn.close()


def create_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = str(payload.get("state") or LIFECYCLE_AI_DRAFTING).strip()
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("agent.activity", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        event = _add_activity(
            conn,
            agent_id=actor_id,
            state=state,
            message=message,
            ref_type=str(payload.get("ref_type") or ""),
            ref_id=str(payload.get("ref_id") or ""),
            payload=payload.get("payload") or {},
        )
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability="agent.activity",
            target_type=str(payload.get("ref_type") or ""),
            target_id=str(payload.get("ref_id") or ""),
            input_data=payload,
            output_data=event,
        )
        conn.commit()
        return event
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_drafts(limit: int = 100, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    conn = _connect()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM strategy_drafts ORDER BY updated_at_utc DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        result = [_format_draft(row) for row in rows]
        if payload.get("_audit", True) is not False:
            _audit(
                conn,
                actor_type=actor_type,
                actor_id=actor_id,
                capability="strategy.draft.list",
                target_type="draft",
                input_data=_compact_audit_value(payload),
                output_data=_compact_audit_value({"count": len(result), "data": result}),
            )
            conn.commit()
        return result
    finally:
        conn.close()


def get_draft(draft_id: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    conn = _connect()
    try:
        _ensure_schema(conn)
        result = _load_draft(conn, draft_id)
        if payload.get("_audit", True) is not False:
            _audit(
                conn,
                actor_type=actor_type,
                actor_id=actor_id,
                capability="strategy.draft.read",
                target_type="draft",
                target_id=draft_id,
                input_data=_compact_audit_value(payload),
                output_data=_compact_audit_value(result or {}),
            )
            conn.commit()
        return result
    finally:
        conn.close()


def _insert_draft_version(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    version_number: int,
    draft_json: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    reason: str,
) -> None:
    conn.execute(
        """INSERT INTO strategy_draft_versions(
            version_id, draft_id, version_number, draft_json, change_reason,
            created_by_type, created_by_id, created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            _new_id("ver"),
            draft_id,
            version_number,
            _json_text(draft_json),
            reason,
            actor_type,
            actor_id,
            _now(),
        ),
    )


def create_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.draft.create", actor_type)
    draft_json = _normalize_draft_payload(payload)
    draft_id = _new_id("draft")
    ts = _now()
    conn = _connect()
    try:
        _ensure_schema(conn)
        conn.execute(
            """INSERT INTO strategy_drafts(
                draft_id, name, lifecycle_state, created_by_type, created_by_id,
                current_version, draft_json, created_at_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                draft_id,
                draft_json["name"],
                LIFECYCLE_AI_DRAFTING,
                actor_type,
                actor_id,
                1,
                _json_text(draft_json),
                ts,
                ts,
            ),
        )
        _insert_draft_version(
            conn,
            draft_id=draft_id,
            version_number=1,
            draft_json=draft_json,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=str(payload.get("reason") or "create draft"),
        )
        _add_activity(conn, agent_id=actor_id, state=LIFECYCLE_AI_DRAFTING, message=f"创建策略草案：{draft_json['name']}", ref_type="draft", ref_id=draft_id)
        result = _load_draft(conn, draft_id)
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.draft.create", target_type="draft", target_id=draft_id, input_data=payload, output_data=result)
        conn.commit()
        return result or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_draft(draft_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.draft.update", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        existing = _load_draft(conn, draft_id)
        if not existing:
            raise ValueError("draft not found")
        if existing["lifecycle_state"] in {LIFECYCLE_APPROVED, LIFECYCLE_CANCELLED, LIFECYCLE_ARCHIVED}:
            raise ValueError("approved/cancelled/archived draft cannot be edited")
        if existing["lifecycle_state"] == LIFECYCLE_WAITING:
            raise ValueError("waiting approval draft cannot be edited; request changes first")
        draft_json = _normalize_draft_payload(payload, existing=existing)
        version_number = int(existing.get("current_version") or 1) + 1
        state = str(payload.get("lifecycle_state") or LIFECYCLE_AI_DRAFTING).strip() or LIFECYCLE_AI_DRAFTING
        if state not in {LIFECYCLE_AI_DRAFTING, LIFECYCLE_AI_PROPOSED, LIFECYCLE_RISK_BLOCKED, LIFECYCLE_REVISION}:
            state = LIFECYCLE_AI_DRAFTING
        ts = _now()
        conn.execute(
            """UPDATE strategy_drafts
               SET name = ?, lifecycle_state = ?, current_version = ?, draft_json = ?,
                   last_risk_report_json = '{}', last_simulation_json = '{}', updated_at_utc = ?
               WHERE draft_id = ?""",
            (draft_json["name"], state, version_number, _json_text(draft_json), ts, draft_id),
        )
        _insert_draft_version(
            conn,
            draft_id=draft_id,
            version_number=version_number,
            draft_json=draft_json,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=str(payload.get("reason") or "update draft"),
        )
        _add_activity(conn, agent_id=actor_id, state=state, message=f"修改策略草案：{draft_json['name']}", ref_type="draft", ref_id=draft_id)
        result = _load_draft(conn, draft_id)
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.draft.update", target_type="draft", target_id=draft_id, input_data=payload, output_data=result)
        conn.commit()
        return result or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_draft(draft_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    actor_type, actor_id = _actor(payload or {})
    _require_agent_capability("strategy.draft.delete", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        existing = _load_draft(conn, draft_id)
        if not existing:
            raise ValueError("draft not found")
        if existing["lifecycle_state"] in {LIFECYCLE_WAITING, LIFECYCLE_APPROVED}:
            raise ValueError("submitted or approved draft cannot be deleted; cancel or archive it")
        conn.execute("DELETE FROM strategy_drafts WHERE draft_id = ?", (draft_id,))
        conn.execute("DELETE FROM strategy_draft_versions WHERE draft_id = ?", (draft_id,))
        _add_activity(conn, agent_id=actor_id, state=LIFECYCLE_CANCELLED, message=f"删除策略草案：{existing['name']}", ref_type="draft", ref_id=draft_id)
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.draft.delete", target_type="draft", target_id=draft_id, input_data=payload or {}, output_data={"deleted": True})
        conn.commit()
        return {"deleted": True, "draft_id": draft_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_risk_report(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft_json = draft.get("draft") or {}
    limits = _limits()
    markets = [_normalize_market(item) for item in draft_json.get("markets", []) if isinstance(item, dict)]
    budget = draft_json.get("budget") if isinstance(draft_json.get("budget"), dict) else {}
    execution_rules = draft_json.get("execution_rules") if isinstance(draft_json.get("execution_rules"), dict) else {}
    max_total = _safe_float(budget.get("max_total_usdc"), sum(_safe_float(m.get("max_exposure_usdc")) for m in markets))
    max_single = _safe_float(budget.get("max_single_order_usdc"), 0.0)
    slippage = _safe_float(execution_rules.get("max_slippage_bps"), 0.0)
    order_type = str(execution_rules.get("order_type") or "limit").lower()
    allowed_market_ids = set(str(x) for x in (limits.get("allowed_market_ids") or []))
    allowed_venues = set(str(x).lower() for x in (limits.get("allowed_venues") or []))
    violations: List[Dict[str, Any]] = []

    def add(code: str, message: str, field: str = "", current: Any = None, allowed: Any = None) -> None:
        violations.append({
            "code": code,
            "message": message,
            "field": field,
            "current": current,
            "allowed": allowed,
        })

    if not markets:
        add("NO_MARKETS", "策略至少需要一个市场", "markets", 0, ">=1")
    if max_total <= 0:
        add("INVALID_STRATEGY_BUDGET", "策略总预算必须大于 0", "budget.max_total_usdc", max_total, ">0")
    if max_total > _safe_float(limits.get("max_strategy_budget_usdc")):
        add("MAX_STRATEGY_BUDGET_EXCEEDED", "策略总预算超过上限", "budget.max_total_usdc", max_total, limits.get("max_strategy_budget_usdc"))
    if max_single <= 0:
        add("INVALID_SINGLE_ORDER", "单笔金额必须大于 0", "budget.max_single_order_usdc", max_single, ">0")
    if max_single > _safe_float(limits.get("max_single_order_usdc")):
        add("MAX_SINGLE_ORDER_EXCEEDED", "单笔金额超过上限", "budget.max_single_order_usdc", max_single, limits.get("max_single_order_usdc"))
    if slippage > _safe_float(limits.get("max_slippage_bps")):
        add("MAX_SLIPPAGE_EXCEEDED", "最大滑点超过上限", "execution_rules.max_slippage_bps", slippage, limits.get("max_slippage_bps"))
    if order_type == "market" and not bool(limits.get("allow_market_order")):
        add("MARKET_ORDER_NOT_ALLOWED", "当前权限不允许市价单", "execution_rules.order_type", order_type, "limit")
    for idx, market in enumerate(markets):
        exposure = _safe_float(market.get("max_exposure_usdc"), 0.0)
        if exposure <= 0:
            add("INVALID_MARKET_EXPOSURE", "每个市场敞口必须大于 0", f"markets.{idx}.max_exposure_usdc", exposure, ">0")
        if exposure > _safe_float(limits.get("max_market_exposure_usdc")):
            add("MAX_MARKET_EXPOSURE_EXCEEDED", "单市场敞口超过上限", f"markets.{idx}.max_exposure_usdc", exposure, limits.get("max_market_exposure_usdc"))
        market_id = str(market.get("instrument_id") or market.get("condition_id") or "")
        if allowed_market_ids and market_id not in allowed_market_ids and str(market.get("condition_id") or "") not in allowed_market_ids:
            add("MARKET_NOT_ALLOWED", "市场不在 agent 白名单", f"markets.{idx}.instrument_id", market_id, list(allowed_market_ids))
        venue = str(market.get("venue") or "polymarket").lower()
        if allowed_venues and venue not in allowed_venues:
            add("VENUE_NOT_ALLOWED", "交易场所不在 agent 权限范围", f"markets.{idx}.venue", venue, list(allowed_venues))
    risk_level = "low" if not violations else ("medium" if len(violations) <= 2 else "high")
    return {
        "risk_report_id": _new_id("risk"),
        "passed": not violations,
        "risk_level": risk_level,
        "violations": violations,
        "limits": limits,
        "checked_at": _now(),
    }


def risk_check(draft_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("risk.check", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        draft = _load_draft(conn, draft_id)
        if not draft:
            raise ValueError("draft not found")
        report = _build_risk_report(draft)
        next_state = LIFECYCLE_AI_PROPOSED if report["passed"] else LIFECYCLE_RISK_BLOCKED
        conn.execute(
            "UPDATE strategy_drafts SET lifecycle_state = ?, last_risk_report_json = ?, updated_at_utc = ? WHERE draft_id = ?",
            (next_state, _json_text(report), _now(), draft_id),
        )
        _add_activity(
            conn,
            agent_id=actor_id,
            state=next_state,
            message=("风控检查通过" if report["passed"] else "风控检查未通过"),
            ref_type="draft",
            ref_id=draft_id,
            payload={"risk_report_id": report["risk_report_id"], "risk_level": report["risk_level"]},
        )
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="risk.check", target_type="draft", target_id=draft_id, input_data=payload, output_data=report, risk_decision="pass" if report["passed"] else "blocked")
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def simulate_draft(draft_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.simulate", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        draft = _load_draft(conn, draft_id)
        if not draft:
            raise ValueError("draft not found")
        result = _build_simulation_result(draft)
        conn.execute(
            "UPDATE strategy_drafts SET last_simulation_json = ?, updated_at_utc = ? WHERE draft_id = ?",
            (_json_text(result), _now(), draft_id),
        )
        _add_activity(conn, agent_id=actor_id, state=draft.get("lifecycle_state") or LIFECYCLE_AI_PROPOSED, message="完成策略模拟", ref_type="draft", ref_id=draft_id, payload={"simulation_id": result["simulation_id"]})
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.simulate", target_type="draft", target_id=draft_id, input_data=payload, output_data=result)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_simulation_result(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft_json = draft.get("draft") or {}
    markets = draft_json.get("markets") or []
    total_exposure = sum(_safe_float(item.get("max_exposure_usdc"), 0.0) for item in markets if isinstance(item, dict))
    budget = draft_json.get("budget") if isinstance(draft_json.get("budget"), dict) else {}
    max_total = _safe_float(budget.get("max_total_usdc"), total_exposure)
    max_loss = min(max_total, total_exposure if total_exposure > 0 else max_total)
    return {
        "simulation_id": _new_id("sim"),
        "max_loss_usdc": round(max_loss, 4),
        "max_exposure_usdc": round(max_loss, 4),
        "estimated_orders": max(1, len(markets)),
        "scenarios": [
            {"name": "take_profit", "estimated_pnl_usdc": round(max_loss * 0.35, 4)},
            {"name": "flat_or_no_fill", "estimated_pnl_usdc": 0},
            {"name": "resolve_against_position", "estimated_pnl_usdc": round(-max_loss, 4)},
        ],
        "generated_at": _now(),
    }


def submit_draft(draft_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    _require_agent_capability("strategy.submit", actor_type)
    conn = _connect()
    try:
        _ensure_schema(conn)
        draft = _load_draft(conn, draft_id)
        if not draft:
            raise ValueError("draft not found")
        if draft["lifecycle_state"] in {LIFECYCLE_APPROVED, LIFECYCLE_CANCELLED, LIFECYCLE_ARCHIVED}:
            raise ValueError("draft cannot be submitted from its current state")
        report = _build_risk_report(draft)
        if not report["passed"]:
            conn.execute(
                "UPDATE strategy_drafts SET lifecycle_state = ?, last_risk_report_json = ?, updated_at_utc = ? WHERE draft_id = ?",
                (LIFECYCLE_RISK_BLOCKED, _json_text(report), _now(), draft_id),
            )
            _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.submit", target_type="draft", target_id=draft_id, input_data=payload, output_data=report, risk_decision="blocked")
            conn.commit()
            raise ValueError("risk check failed")
        existing = conn.execute(
            """SELECT * FROM strategy_approval_requests
               WHERE draft_id = ? AND draft_version = ? AND status = ?
               ORDER BY created_at_utc DESC LIMIT 1""",
            (draft_id, draft["current_version"], APPROVAL_PENDING),
        ).fetchone()
        if existing:
            return format_approval(existing)
        approval_id = _new_id("appr")
        ts = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=int(_safe_float(_limits().get("approval_expires_minutes"), 1440)))).isoformat()
        conn.execute(
            """INSERT INTO strategy_approval_requests(
                approval_id, draft_id, draft_version, status, submitted_by_type,
                submitted_by_id, risk_report_json, note, expires_at_utc, created_at_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id,
                draft_id,
                int(draft["current_version"]),
                APPROVAL_PENDING,
                actor_type,
                actor_id,
                _json_text(report),
                str(payload.get("note") or ""),
                expires_at,
                ts,
                ts,
            ),
        )
        draft_json = draft.get("draft") or {}
        conn.execute(
            """INSERT INTO strategy_approval_snapshots(
                approval_id, snapshot_json, market_snapshot_json, policy_snapshot_json,
                risk_snapshot_json, created_at_utc
            ) VALUES (?,?,?,?,?,?)""",
            (
                approval_id,
                _json_text(draft_json),
                _json_text(draft_json.get("markets") or [], []),
                _json_text(_policy()),
                _json_text(report),
                ts,
            ),
        )
        conn.execute(
            "UPDATE strategy_drafts SET lifecycle_state = ?, last_risk_report_json = ?, updated_at_utc = ? WHERE draft_id = ?",
            (LIFECYCLE_WAITING, _json_text(report), ts, draft_id),
        )
        _add_activity(conn, agent_id=actor_id, state=LIFECYCLE_WAITING, message=f"提交人工确认：{draft['name']}", ref_type="approval", ref_id=approval_id)
        approval = conn.execute("SELECT * FROM strategy_approval_requests WHERE approval_id = ?", (approval_id,)).fetchone()
        result = format_approval(approval)
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability="strategy.submit", target_type="approval", target_id=approval_id, input_data=payload, output_data=result, risk_decision="pass")
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def format_approval(row: sqlite3.Row | Dict[str, Any] | None) -> Dict[str, Any]:
    data = dict(row or {})
    return {
        "approval_id": data.get("approval_id"),
        "draft_id": data.get("draft_id"),
        "draft_version": data.get("draft_version"),
        "status": data.get("status"),
        "submitted_by_type": data.get("submitted_by_type"),
        "submitted_by_id": data.get("submitted_by_id"),
        "approved_by_type": data.get("approved_by_type"),
        "approved_by_id": data.get("approved_by_id"),
        "approved_strategy_id": data.get("approved_strategy_id"),
        "risk_report": _parse_json(data.get("risk_report_json"), {}),
        "note": data.get("note"),
        "expires_at": data.get("expires_at_utc"),
        "created_at": data.get("created_at_utc"),
        "updated_at": data.get("updated_at_utc"),
    }


def _load_approval(conn: sqlite3.Connection, approval_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM strategy_approval_requests WHERE approval_id = ?", (approval_id,)).fetchone()
    return format_approval(row) if row else None


def _load_snapshot(conn: sqlite3.Connection, approval_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM strategy_approval_snapshots WHERE approval_id = ?", (approval_id,)).fetchone()
    if not row:
        return {}
    data = dict(row)
    snapshot = _parse_json(data.get("snapshot_json"), {})
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), list) else []
        params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
        snapshot["params"] = _ensure_params_deadline(params, markets, allow_resolve=True)
        snapshot["agent_report"] = _normalize_agent_report(snapshot, snapshot)
        cleaned_thesis = _clean_agent_text(snapshot.get("thesis"), 260)
        if cleaned_thesis:
            snapshot["thesis"] = cleaned_thesis
        else:
            snapshot["thesis"] = snapshot["agent_report"].get("summary") or ""
        snapshot["risk_notes"] = _clean_agent_notes(snapshot.get("risk_notes"))
    return {
        "approval_id": approval_id,
        "snapshot": snapshot,
        "markets": _parse_json(data.get("market_snapshot_json"), []),
        "policy": _parse_json(data.get("policy_snapshot_json"), {}),
        "risk": _parse_json(data.get("risk_snapshot_json"), {}),
        "created_at": data.get("created_at_utc"),
    }


def _draft_json_from_strategy_form(base: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base or {})
    if "strategy_name" in payload or "name" in payload:
        result["name"] = str(payload.get("strategy_name") or payload.get("name") or result.get("name") or "").strip()
    if "strategy_code" in payload:
        result["strategy_code"] = str(payload.get("strategy_code") or "").strip()
    if "mode" in payload or "state" in payload:
        mode = str(payload.get("mode") or payload.get("state") or result.get("mode") or "Stop").strip() or "Stop"
        result["mode"] = mode if mode in VALID_STRATEGY_MODES else "Stop"

    params = payload.get("params")
    if params is None:
        params = payload.get("input_json")
    if params is not None:
        result["params"] = _parse_json(params, {}) if isinstance(params, str) else (params if isinstance(params, dict) else {})

    budget = dict(result.get("budget") if isinstance(result.get("budget"), dict) else {})
    if "strategy_bankroll" in payload:
        budget["max_total_usdc"] = _safe_float(payload.get("strategy_bankroll"), _safe_float(budget.get("max_total_usdc"), 0.0))
    budget.setdefault("max_single_order_usdc", _safe_float(_limits().get("max_single_order_usdc"), 20.0))
    result["budget"] = budget

    existing_markets = result.get("markets") if isinstance(result.get("markets"), list) else []
    legs = payload.get("legs") if isinstance(payload.get("legs"), list) else None
    if legs is not None:
        markets = []
        for index, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            existing = existing_markets[index] if index < len(existing_markets) and isinstance(existing_markets[index], dict) else {}
            params_json = _parse_json(leg.get("params_json"), {}) if isinstance(leg.get("params_json"), str) else (leg.get("params_json") if isinstance(leg.get("params_json"), dict) else {})
            instrument_json = _parse_json(leg.get("instrument_json"), {}) if isinstance(leg.get("instrument_json"), str) else (leg.get("instrument_json") if isinstance(leg.get("instrument_json"), dict) else {})
            max_exposure = _safe_float(leg.get("budget_cap"), _safe_float(existing.get("max_exposure_usdc"), 0.0))
            markets.append({
                **existing,
                "instrument_id": str(leg.get("instrument_id") or existing.get("instrument_id") or leg.get("condition_id") or "").strip(),
                "condition_id": str(leg.get("condition_id") or existing.get("condition_id") or "").strip(),
                "yes_token": leg.get("yes_token") or existing.get("yes_token") or "",
                "no_token": leg.get("no_token") or existing.get("no_token") or "",
                "venue": str(leg.get("venue") or existing.get("venue") or "polymarket").strip().lower(),
                "question": instrument_json.get("question") or existing.get("question") or existing.get("title") or "",
                "title": instrument_json.get("question") or existing.get("title") or existing.get("question") or "",
                "outcome": params_json.get("outcome") or instrument_json.get("outcome") or existing.get("outcome") or "YES",
                "action": params_json.get("action") or existing.get("action") or "buy",
                "max_entry_price": _safe_float(params_json.get("max_entry_price"), _safe_float(existing.get("max_entry_price"), 0.0)),
                "max_exposure_usdc": max_exposure,
            })
        result["markets"] = markets
        if markets and _safe_float(result["budget"].get("max_total_usdc"), 0.0) <= 0:
            result["budget"]["max_total_usdc"] = sum(_safe_float(m.get("max_exposure_usdc"), 0.0) for m in markets)

    result["params"] = _ensure_params_deadline(
        result.get("params") if isinstance(result.get("params"), dict) else {},
        result.get("markets") if isinstance(result.get("markets"), list) else [],
        allow_resolve=True,
    )
    return _normalize_draft_payload({"draft": result})


def _human_override_record(
    *,
    actor_type: str,
    actor_id: str,
    reason: str,
    report: Dict[str, Any],
    created_at: str,
) -> Dict[str, Any]:
    return {
        "type": "human_parameter_override",
        "actor_type": actor_type,
        "actor_id": actor_id,
        "reason": reason,
        "allows_risk_approval": True,
        "risk_passed": bool(report.get("passed")),
        "risk_report_id": report.get("risk_report_id"),
        "violations": report.get("violations") if isinstance(report.get("violations"), list) else [],
        "created_at": created_at,
    }


def _allows_human_risk_override(snapshot: Dict[str, Any], actor_type: str) -> bool:
    if actor_type == "agent":
        return False
    override = snapshot.get("human_override") if isinstance(snapshot, dict) else None
    return isinstance(override, dict) and bool(override.get("allows_risk_approval"))


def update_approval_draft(approval_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type == "agent":
        raise ValueError("agent cannot edit pending approval parameters")
    conn = _connect()
    try:
        _ensure_schema(conn)
        approval = _load_approval(conn, approval_id)
        if not approval:
            raise ValueError("approval not found")
        if approval["status"] != APPROVAL_PENDING:
            raise ValueError("only waiting approvals can be edited")
        draft = _load_draft(conn, str(approval.get("draft_id") or ""))
        if not draft:
            raise ValueError("draft not found")
        snapshot = _load_snapshot(conn, approval_id).get("snapshot") or draft.get("draft") or {}
        draft_json = _draft_json_from_strategy_form(snapshot, payload)
        version_number = int(draft.get("current_version") or 1) + 1
        report = _build_risk_report({"draft": draft_json})
        simulation = _build_simulation_result({"draft": draft_json})
        ts = _now()
        change_reason = str(payload.get("reason") or "human edited pending approval parameters")
        draft_json["human_override"] = _human_override_record(
            actor_type=actor_type,
            actor_id=actor_id,
            reason=change_reason,
            report=report,
            created_at=ts,
        )
        draft_id = str(draft.get("draft_id") or approval.get("draft_id") or "")
        conn.execute(
            """UPDATE strategy_drafts
               SET name = ?, lifecycle_state = ?, current_version = ?, draft_json = ?,
                   last_risk_report_json = ?, last_simulation_json = ?, updated_at_utc = ?
               WHERE draft_id = ?""",
            (
                draft_json.get("name") or draft.get("name") or "",
                LIFECYCLE_WAITING,
                version_number,
                _json_text(draft_json),
                _json_text(report),
                _json_text(simulation),
                ts,
                draft_id,
            ),
        )
        _insert_draft_version(
            conn,
            draft_id=draft_id,
            version_number=version_number,
            draft_json=draft_json,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=change_reason,
        )
        conn.execute(
            """UPDATE strategy_approval_requests
               SET draft_version = ?, risk_report_json = ?, updated_at_utc = ?
               WHERE approval_id = ?""",
            (version_number, _json_text(report), ts, approval_id),
        )
        conn.execute(
            """UPDATE strategy_approval_snapshots
               SET snapshot_json = ?, market_snapshot_json = ?, policy_snapshot_json = ?,
                   risk_snapshot_json = ?, created_at_utc = ?
               WHERE approval_id = ?""",
            (
                _json_text(draft_json),
                _json_text(draft_json.get("markets") or [], []),
                _json_text(_policy()),
                _json_text(report),
                ts,
                approval_id,
            ),
        )
        _add_activity(
            conn,
            agent_id=str(approval.get("submitted_by_id") or DEFAULT_AGENT_ID),
            state=LIFECYCLE_WAITING,
            message=f"人工修改待确认策略参数：{draft_json.get('name') or draft.get('name') or approval_id}",
            ref_type="approval",
            ref_id=approval_id,
        )
        result = _load_approval(conn, approval_id) or {}
        result["draft"] = _load_draft(conn, draft_id)
        result["snapshot"] = _load_snapshot(conn, approval_id)
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability="strategy.approval.update_draft",
            target_type="approval",
            target_id=approval_id,
            input_data=payload,
            output_data=result,
            risk_decision="pass" if report.get("passed") else "blocked",
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_approvals(status: str = "", limit: int = 100, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    conn = _connect()
    try:
        _ensure_schema(conn)
        args: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            args.append(status)
        args.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"SELECT * FROM strategy_approval_requests {where} ORDER BY updated_at_utc DESC LIMIT ?",
            args,
        ).fetchall()
        result = []
        for row in rows:
            approval = format_approval(row)
            draft = _load_draft(conn, str(approval["draft_id"]))
            approval["draft"] = draft
            approval["snapshot"] = _load_snapshot(conn, str(approval["approval_id"]))
            result.append(approval)
        if payload.get("_audit", True) is not False:
            _audit(
                conn,
                actor_type=actor_type,
                actor_id=actor_id,
                capability="approval.status",
                target_type="approval",
                input_data=_compact_audit_value({**payload, "status": status, "limit": limit}),
                output_data=_compact_audit_value({"count": len(result), "data": result}),
            )
            conn.commit()
        return result
    finally:
        conn.close()


def get_approval(approval_id: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload)
    conn = _connect()
    try:
        _ensure_schema(conn)
        approval = _load_approval(conn, approval_id)
        if not approval:
            return None
        approval["draft"] = _load_draft(conn, str(approval["draft_id"]))
        approval["snapshot"] = _load_snapshot(conn, approval_id)
        if payload.get("_audit", True) is not False:
            _audit(
                conn,
                actor_type=actor_type,
                actor_id=actor_id,
                capability="approval.status",
                target_type="approval",
                target_id=approval_id,
                input_data=_compact_audit_value(payload),
                output_data=_compact_audit_value(approval),
            )
            conn.commit()
        return approval
    finally:
        conn.close()


def _strategy_payload_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = dict(snapshot or {})
    snapshot["agent_report"] = _normalize_agent_report(snapshot, snapshot)
    cleaned_thesis = _clean_agent_text(snapshot.get("thesis"), 260)
    snapshot["thesis"] = cleaned_thesis or snapshot["agent_report"].get("summary") or ""
    snapshot["risk_notes"] = _clean_agent_notes(snapshot.get("risk_notes"))
    markets = [_normalize_market(item) for item in snapshot.get("markets", []) if isinstance(item, dict)]
    budget = snapshot.get("budget") if isinstance(snapshot.get("budget"), dict) else {}
    execution_rules = snapshot.get("execution_rules") if isinstance(snapshot.get("execution_rules"), dict) else {}
    exit_rules = snapshot.get("exit_rules") if isinstance(snapshot.get("exit_rules"), dict) else {}
    params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
    params = _ensure_params_deadline(params, markets, allow_resolve=True)
    mode = str(snapshot.get("mode") or snapshot.get("state") or "Stop").strip() or "Stop"
    if mode not in VALID_STRATEGY_MODES:
        mode = "Stop"
    legs = []
    for idx, market in enumerate(markets):
        leg_params = {
            "outcome": market.get("outcome") or "YES",
            "action": market.get("action") or "buy",
            "max_entry_price": market.get("max_entry_price"),
            "max_exposure_usdc": market.get("max_exposure_usdc"),
            "execution_rules": execution_rules,
            "exit_rules": exit_rules,
        }
        legs.append({
            "leg_index": idx,
            "condition_id": market.get("condition_id") or "",
            "yes_token": market.get("yes_token") or None,
            "no_token": market.get("no_token") or None,
            "asset_class": "polymarket_binary",
            "venue": market.get("venue") or "polymarket",
            "instrument_id": market.get("instrument_id") or market.get("condition_id") or "",
            "instrument_json": {
                "question": market.get("question") or market.get("title") or "",
                "outcome": market.get("outcome") or "YES",
            },
            "budget_cap": _safe_float(market.get("max_exposure_usdc"), 0.0),
            "params_json": leg_params,
        })
    input_json = {
        **params,
        "AgentThesis": snapshot.get("thesis") or "",
        "AgentReport": snapshot.get("agent_report") or {},
        "AgentHumanOverride": snapshot.get("human_override") or {},
        "AgentApproved": True,
        "AgentLifecycleState": LIFECYCLE_APPROVED,
        "MaxSingleOrderUsdc": budget.get("max_single_order_usdc"),
        "ExecutionRules": execution_rules,
        "ExitRules": exit_rules,
    }
    return {
        "strategy_name": snapshot.get("name") or "Agent Approved Strategy",
        "strategy_code": snapshot.get("strategy_code") or "",
        "mode": mode,
        "strategy_bankroll": _safe_float(budget.get("max_total_usdc"), sum(_safe_float(m.get("max_exposure_usdc")) for m in markets)),
        "input_json": input_json,
        "legs": legs,
    }


def approve_approval(approval_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type == "agent":
        raise ValueError("SELF_APPROVAL_FORBIDDEN")
    conn = _connect()
    try:
        _ensure_schema(conn)
        approval = _load_approval(conn, approval_id)
        if not approval:
            raise ValueError("approval not found")
        if approval["status"] != APPROVAL_PENDING:
            raise ValueError("approval is not waiting for human confirm")
        if str(approval.get("submitted_by_id") or "") == actor_id:
            raise ValueError("SELF_APPROVAL_FORBIDDEN")
        snapshot_bundle = _load_snapshot(conn, approval_id)
        snapshot = snapshot_bundle.get("snapshot") or {}
        risk = snapshot_bundle.get("risk") or approval.get("risk_report") or {}
        risk_overridden = bool(risk and not bool(risk.get("passed")) and _allows_human_risk_override(snapshot, actor_type))
        if risk and not bool(risk.get("passed")) and not risk_overridden:
            raise ValueError("risk check failed; approval cannot be approved")
        strategy_payload = _strategy_payload_from_snapshot(snapshot)
        # create_strategy opens its own connection; commit this conn first to avoid long write locks.
        conn.commit()
    finally:
        conn.close()

    strategy = create_strategy(strategy_payload)
    strategy_id = int(strategy.get("strategy_id") or 0)

    conn = _connect()
    try:
        _ensure_schema(conn)
        ts = _now()
        conn.execute(
            """UPDATE strategy_approval_requests
               SET status = ?, approved_by_type = ?, approved_by_id = ?,
                   approved_strategy_id = ?, updated_at_utc = ?
               WHERE approval_id = ?""",
            (APPROVAL_APPROVED, actor_type, actor_id, strategy_id, ts, approval_id),
        )
        draft_id = str(approval.get("draft_id") or "")
        conn.execute(
            "UPDATE strategy_drafts SET lifecycle_state = ?, updated_at_utc = ? WHERE draft_id = ?",
            (LIFECYCLE_APPROVED, ts, draft_id),
        )
        _add_activity(conn, agent_id=str(approval.get("submitted_by_id") or DEFAULT_AGENT_ID), state=LIFECYCLE_APPROVED, message=f"人工批准策略：{strategy_payload['strategy_name']}", ref_type="strategy", ref_id=strategy_id and str(strategy_id) or approval_id)
        result = get_approval_payload_with_strategy(conn, approval_id, strategy)
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability="strategy.approve",
            target_type="approval",
            target_id=approval_id,
            input_data=payload,
            output_data=result,
            risk_decision="human_override" if risk_overridden else "pass",
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_approval_payload_with_strategy(conn: sqlite3.Connection, approval_id: str, strategy: Dict[str, Any]) -> Dict[str, Any]:
    approval = _load_approval(conn, approval_id) or {}
    approval["approved_strategy"] = strategy
    approval["snapshot"] = _load_snapshot(conn, approval_id)
    return approval


def reject_approval(approval_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _finish_approval(approval_id, payload or {}, status=APPROVAL_REJECTED, draft_state=LIFECYCLE_REJECTED, capability="strategy.reject")


def request_changes(approval_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _finish_approval(approval_id, payload or {}, status=APPROVAL_REVISION, draft_state=LIFECYCLE_REVISION, capability="strategy.request_changes")


def _finish_approval(approval_id: str, payload: Dict[str, Any], *, status: str, draft_state: str, capability: str) -> Dict[str, Any]:
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type == "agent":
        raise ValueError("agent cannot finish approvals")
    conn = _connect()
    try:
        _ensure_schema(conn)
        approval = _load_approval(conn, approval_id)
        if not approval:
            raise ValueError("approval not found")
        if approval["status"] != APPROVAL_PENDING:
            raise ValueError("approval is not waiting for human confirm")
        ts = _now()
        reason = str(payload.get("reason") or payload.get("note") or "")
        conn.execute(
            """UPDATE strategy_approval_requests
               SET status = ?, approved_by_type = ?, approved_by_id = ?, note = ?, updated_at_utc = ?
               WHERE approval_id = ?""",
            (status, actor_type, actor_id, reason, ts, approval_id),
        )
        conn.execute(
            "UPDATE strategy_drafts SET lifecycle_state = ?, updated_at_utc = ? WHERE draft_id = ?",
            (draft_state, ts, str(approval.get("draft_id") or "")),
        )
        _add_activity(conn, agent_id=str(approval.get("submitted_by_id") or DEFAULT_AGENT_ID), state=draft_state, message=reason or ("拒绝策略" if status == APPROVAL_REJECTED else "要求修改策略"), ref_type="approval", ref_id=approval_id)
        result = _load_approval(conn, approval_id) or {}
        _audit(conn, actor_type=actor_type, actor_id=actor_id, capability=capability, target_type="approval", target_id=approval_id, input_data=payload, output_data=result)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dashboard(limit: int = 20) -> Dict[str, Any]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        activities = [
            _format_activity(row)
            for row in conn.execute(
                "SELECT * FROM agent_activity_events ORDER BY created_at_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        pending = list_approvals(status=APPROVAL_PENDING, limit=limit, payload={"_audit": False})
        drafts = [
            _format_draft(row)
            for row in conn.execute(
                "SELECT * FROM strategy_drafts ORDER BY updated_at_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        counts = {
            "pending_approvals": len(pending),
            "drafts": len(drafts),
            "activity": len(activities),
        }
        return {
            "policy": _policy(),
            "counts": counts,
            "activity": activities,
            "pending_approvals": pending,
            "drafts": drafts,
        }
    finally:
        conn.close()


def list_audit(limit: int = 100, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    actor_type, _ = _actor(payload or {})
    _require_agent_capability("audit.read", actor_type)
    payload = payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    agent_kind = str(payload.get("agent_kind") or "").strip()
    conn = _connect()
    try:
        _ensure_schema(conn)
        args: List[Any] = []
        where_parts: List[str] = []
        if run_id:
            where_parts.append("run_id = ?")
            args.append(run_id)
        if agent_kind:
            where_parts.append("agent_kind = ?")
            args.append(agent_kind)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        args.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"SELECT * FROM agent_audit_events {where} ORDER BY created_at_utc DESC LIMIT ?",
            args,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["input"] = _parse_json(item.pop("input_json", "{}"), {})
            item["output"] = _parse_json(item.pop("output_json", "{}"), {})
            item["error"] = _parse_json(item.pop("error_json", "{}"), {})
            item["created_at"] = item.pop("created_at_utc", None)
            result.append(item)
        return result
    finally:
        conn.close()


def list_runs(limit: int = 100, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    actor_type, _ = _actor(payload or {})
    _require_agent_capability("audit.read", actor_type)
    payload = payload or {}
    agent_kind = str(payload.get("agent_kind") or "").strip()
    workflow_id = str(payload.get("workflow_id") or "").strip()
    status = str(payload.get("status") or "").strip()
    conn = _connect()
    try:
        _ensure_schema(conn)
        args: List[Any] = []
        where_parts: List[str] = []
        if agent_kind:
            where_parts.append("agent_kind = ?")
            args.append(agent_kind)
        if workflow_id:
            where_parts.append("workflow_id = ?")
            args.append(workflow_id)
        if status:
            where_parts.append("status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        args.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"""SELECT r.*,
                       (SELECT COUNT(*) FROM agent_run_steps s WHERE s.run_id = r.run_id) AS step_count,
                       (SELECT COUNT(*) FROM agent_audit_events a WHERE a.run_id = r.run_id) AS audit_count
                FROM agent_runs r
                {where}
                ORDER BY started_at_utc DESC
                LIMIT ?""",
            args,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["input"] = _parse_json(item.pop("input_json", "{}"), {})
            item["output"] = _parse_json(item.pop("output_json", "{}"), {})
            item["error"] = _parse_json(item.pop("error_json", "{}"), {})
            item["started_at"] = item.pop("started_at_utc", None)
            item["finished_at"] = item.pop("finished_at_utc", None)
            result.append(item)
        return result
    finally:
        conn.close()


def list_run_steps(run_id: str, limit: int = 200, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    actor_type, _ = _actor(payload or {})
    _require_agent_capability("audit.read", actor_type)
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    conn = _connect()
    try:
        _ensure_schema(conn)
        run_row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run_row:
            raise ValueError("run not found")
        step_rows = conn.execute(
            """SELECT * FROM agent_run_steps
               WHERE run_id = ?
               ORDER BY step_index ASC, started_at_utc ASC
               LIMIT ?""",
            (run_id, max(1, min(int(limit), 500))),
        ).fetchall()
        audit_rows = conn.execute(
            """SELECT * FROM agent_audit_events
               WHERE run_id = ?
               ORDER BY created_at_utc ASC
               LIMIT ?""",
            (run_id, max(1, min(int(limit), 500))),
        ).fetchall()

        def _format_step(row: sqlite3.Row) -> Dict[str, Any]:
            item = dict(row)
            item["input"] = _parse_json(item.pop("input_json", "{}"), {})
            item["output"] = _parse_json(item.pop("output_json", "{}"), {})
            item["error"] = _parse_json(item.pop("error_json", "{}"), {})
            item["started_at"] = item.pop("started_at_utc", None)
            item["finished_at"] = item.pop("finished_at_utc", None)
            return item

        run = dict(run_row)
        run["input"] = _parse_json(run.pop("input_json", "{}"), {})
        run["output"] = _parse_json(run.pop("output_json", "{}"), {})
        run["error"] = _parse_json(run.pop("error_json", "{}"), {})
        run["started_at"] = run.pop("started_at_utc", None)
        run["finished_at"] = run.pop("finished_at_utc", None)
        audits = []
        for row in audit_rows:
            item = dict(row)
            item["input"] = _parse_json(item.pop("input_json", "{}"), {})
            item["output"] = _parse_json(item.pop("output_json", "{}"), {})
            item["error"] = _parse_json(item.pop("error_json", "{}"), {})
            item["created_at"] = item.pop("created_at_utc", None)
            audits.append(item)
        return {"run": run, "steps": [_format_step(row) for row in step_rows], "audit": audits}
    finally:
        conn.close()


def record_request_error(
    *,
    path: str,
    method: str,
    status_code: int,
    error: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    payload = dict(payload or {})
    actor_type, actor_id = _actor(payload)
    payload.setdefault("_endpoint", path)
    payload.setdefault("_method", method)
    capability = str(payload.get("capability") or "agent.request.error")
    target_type = "agent_request"
    if "event-graph" in path:
        target_type = "event_graph"
    elif "strategy-drafts" in path:
        target_type = "draft"
    elif "approvals" in path:
        target_type = "approval"
    conn = _connect()
    try:
        _ensure_schema(conn)
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability=capability,
            target_type=target_type,
            input_data=payload,
            output_data={},
            error_data={"message": str(error), "status_code": status_code},
            policy_decision="error",
            risk_decision="not_required",
            endpoint=path,
            method=method,
            status_code=status_code,
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def clear_audit(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    actor_type, actor_id = _actor(payload, default_type="human", default_id=DEFAULT_HUMAN_ID)
    if actor_type == "agent":
        raise ValueError("agent cannot clear audit history")
    event_ids = payload.get("event_ids")
    confirm_all = bool(payload.get("confirm_all"))
    conn = _connect()
    try:
        _ensure_schema(conn)
        deleted = 0
        if isinstance(event_ids, list) and event_ids:
            ids = [str(item).strip() for item in event_ids if str(item or "").strip()]
            for start in range(0, len(ids), 200):
                chunk = ids[start:start + 200]
                placeholders = ",".join("?" for _ in chunk)
                cur = conn.execute(f"DELETE FROM agent_audit_events WHERE event_id IN ({placeholders})", chunk)
                deleted += int(cur.rowcount or 0)
        elif confirm_all:
            cur = conn.execute("DELETE FROM agent_audit_events")
            deleted = int(cur.rowcount or 0)
        else:
            raise ValueError("event_ids or confirm_all is required")
        _audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            capability="audit.clear",
            target_type="audit",
            input_data=_compact_audit_value(payload),
            output_data={"deleted": deleted},
        )
        conn.commit()
        return {"deleted": deleted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
