"""
CRUD service for strategy_registry + strategy_legs tables.
All operations target the same DB as the old monitoring table.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.config_loader import load_web_settings
from services.strategy_data_source import connect as _ds_connect, derive_instrument_id

_BASE_DIR = Path(__file__).resolve().parent.parent
_STRATEGY_CODE_DIR = _BASE_DIR / "StrategyCode"
_VALID_STATES = {"Stop", "Virtual", "Real"}

_LEG_IDENTITY_FIELDS = (
    "condition_id",
    "yes_token",
    "no_token",
    "asset_class",
    "venue",
    "symbol",
    "instrument_id",
    "instrument_json",
)


def _db_path() -> Path:
    settings = load_web_settings()
    raw = settings.get("strategy_monitoring_db_path", "")
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return _BASE_DIR / "Data" / "PolyMarketMonitoring.db"


def _connect() -> sqlite3.Connection:
    return _ds_connect()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(v)
    except Exception:
        return default


def _derive_strategy_bankroll(payload: Dict[str, Any], legs_payload: List[Dict[str, Any]]) -> float:
    explicit = _safe_float(payload.get("strategy_bankroll"), 0.0)
    if explicit > 0:
        return explicit
    top_budget = _safe_float(payload.get("budget_cap"), 0.0)
    if top_budget > 0 and not legs_payload:
        return top_budget
    leg_total = sum(
        _safe_float(leg.get("budget_cap"), 0.0)
        for leg in (legs_payload or [])
        if isinstance(leg, dict)
    )
    if leg_total > 0:
        return leg_total
    return _safe_float(payload.get("initial_capital"), 0.0)


# --- helpers ---


def list_strategy_codes() -> List[str]:
    if not _STRATEGY_CODE_DIR.is_dir():
        return []
    return sorted(
        f.stem for f in _STRATEGY_CODE_DIR.iterdir()
        if f.is_file() and f.suffix == ".py" and not f.name.startswith("_")
    )


def _normalize_input_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _parse_function_introduction_inputs(text: Any) -> Dict[str, Dict[str, Any]]:
    intro = str(text or "")
    if not intro:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    current_name = ""
    for raw_line in intro.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name_match = re.match(r"^-\s*name\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if name_match:
            current_name = name_match.group(1).strip().strip("'\"")
            if current_name:
                result.setdefault(_normalize_input_name(current_name), {"name": current_name})
            continue
        if not current_name or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key in {"type", "required", "description"}:
            result.setdefault(_normalize_input_name(current_name), {"name": current_name})[key] = value
    return result


def get_strategy_code_inputs(code_name: str) -> List[Dict[str, Any]]:
    import importlib.util, sys as _sys
    safe = "".join(ch for ch in code_name if ch.isalnum() or ch in ("_", "-"))
    if not safe:
        return []
    py_file = _STRATEGY_CODE_DIR / (safe + ".py")
    if not py_file.is_file():
        return []
    mod_name = "_stg_tmp_" + safe
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(py_file))
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        inputs_raw = getattr(mod, "Inputs", [])
        intro = getattr(mod, "FunctionIntroduction", "")
        intro_inputs = _parse_function_introduction_inputs(intro)
        result = []
        for inp in inputs_raw:
            if not isinstance(inp, dict):
                continue
            name = str(inp.get("name", inp.get("Id", "")) or "")
            if not name or name.lower() == "usedata":
                continue
            intro_meta = intro_inputs.get(_normalize_input_name(name), {})
            result.append({
                "name": name,
                "kind": intro_meta.get("type") or inp.get("Kind", "String"),
                "required": (
                    str(intro_meta.get("required", "")).strip().lower() in {"true", "yes", "1"}
                    or bool(inp.get("Isnecessary", False))
                ),
                "default": inp.get("Default", inp.get("Num", inp.get("Context"))),
                "min": inp.get("Min"),
                "max": inp.get("Max"),
                "description": intro_meta.get("description") or inp.get("Description", ""),
            })
        return result
    except Exception:
        return []
    finally:
        _sys.modules.pop(mod_name, None)


def _row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def _parse_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _json_text(raw: Any, default: str = "{}") -> str:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw.strip() else json.loads(default)
        except Exception:
            return default
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    try:
        return json.dumps(raw if raw is not None else json.loads(default), ensure_ascii=False, sort_keys=True)
    except Exception:
        return default


def _stable_json_text(raw: Any) -> str:
    parsed = _parse_json(raw)
    return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False, sort_keys=True)


def _normalize_leg_payload(leg: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    result = dict(leg or {})
    result["leg_uid"] = str(result.get("leg_uid") or "").strip()
    result["leg_index"] = int(_safe_float(result.get("leg_index"), index))
    result["condition_id"] = str(result.get("condition_id") or "").strip()
    result["yes_token"] = result.get("yes_token") or None
    result["no_token"] = result.get("no_token") or None
    asset_class = str(result.get("asset_class") or "polymarket_binary").strip() or "polymarket_binary"
    result["asset_class"] = asset_class
    result["venue"] = str(result.get("venue") or ("polymarket" if asset_class == "polymarket_binary" else "")).strip()
    result["symbol"] = str(result.get("symbol") or "").strip().upper()
    result["instrument_json"] = _json_text(result.get("instrument_json"), "{}")
    if not str(result.get("instrument_id") or "").strip():
        result["instrument_id"] = derive_instrument_id({**result, "instrument_json": _parse_json(result["instrument_json"])})
    else:
        result["instrument_id"] = str(result.get("instrument_id") or "").strip()
    result["params_json"] = _json_text(result.get("params_json"), "{}")
    result["budget_cap"] = _safe_float(result.get("budget_cap"))
    return result


def _leg_identity(leg: Dict[str, Any], fallback_index: int = 0) -> tuple:
    return (
        str(leg.get("condition_id") or "").strip(),
        str(leg.get("yes_token") or "").strip(),
        str(leg.get("no_token") or "").strip(),
        str(leg.get("asset_class") or "polymarket_binary").strip(),
        str(leg.get("venue") or "").strip(),
        str(leg.get("symbol") or "").strip().upper(),
        str(leg.get("instrument_id") or "").strip(),
        _stable_json_text(leg.get("instrument_json")),
    )


def _new_leg_uid(strategy_id: Optional[int] = None) -> str:
    prefix = f"leg_{strategy_id}_" if strategy_id else "leg_"
    return prefix + uuid.uuid4().hex[:16]


def _leg_uid_for_payload(
    existing_legs: List[Dict[str, Any]],
    incoming_leg: Dict[str, Any],
    fallback_index: int,
    strategy_id: Optional[int] = None,
) -> str:
    explicit = str(incoming_leg.get("leg_uid") or "").strip()
    if explicit:
        return explicit
    incoming_identity = _leg_identity(incoming_leg, fallback_index)
    incoming_index = int(_safe_float(incoming_leg.get("leg_index"), fallback_index))
    for existing in existing_legs:
        if int(_safe_float(existing.get("leg_index"), fallback_index)) != incoming_index:
            continue
        if _leg_identity(existing, incoming_index) == incoming_identity:
            existing_uid = str(existing.get("leg_uid") or "").strip()
            if existing_uid:
                return existing_uid
    for existing in existing_legs:
        if _leg_identity(existing, fallback_index) == incoming_identity:
            existing_uid = str(existing.get("leg_uid") or "").strip()
            if existing_uid:
                return existing_uid
    return _new_leg_uid(strategy_id)


def _legs_identity_changed(
    existing_legs: List[Dict[str, Any]],
    incoming_legs: List[Dict[str, Any]],
) -> bool:
    existing = sorted(_leg_identity(leg) for leg in existing_legs)
    incoming = sorted(_leg_identity(_normalize_leg_payload(leg, i), i) for i, leg in enumerate(incoming_legs))
    return existing != incoming


def _clear_virtual_state(conn: sqlite3.Connection, strategy_id: int) -> None:
    for table in (
        "strategy_virtual_positions",
        "strategy_virtual_orders",
        "strategy_virtual_open_orders",
        "strategy_virtual_positions_v2",
        "strategy_virtual_orders_v2",
        "strategy_order_intents",
        "strategy_cash_ledger",
        "strategy_virtual_events",
        "strategy_virtual_ticks",
        "strategy_virtual_account",
    ):
        conn.execute(f"DELETE FROM {table} WHERE strategy_id = ?", (strategy_id,))


def _fmt_leg(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["leg_uid"] = str(r.get("leg_uid") or "").strip()
    r["params_json"] = _parse_json(r.get("params_json"))
    r["instrument_json"] = _parse_json(r.get("instrument_json"))
    return r


def _fmt_strategy(row: Dict[str, Any], legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    r = dict(row)
    r["input_json"] = _parse_json(r.get("input_json"))
    r["legs"] = [_fmt_leg(lg) for lg in legs]
    return r


def _load_with_legs(conn: sqlite3.Connection, sid: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM strategy_registry WHERE strategy_id = ?", (sid,)
    ).fetchone()
    if not row:
        return None
    legs = conn.execute(
        "SELECT * FROM strategy_legs WHERE strategy_id = ? ORDER BY leg_index",
        (sid,),
    ).fetchall()
    return _fmt_strategy(_row_dict(row), [_row_dict(lg) for lg in legs])


# --- list / get ---


def list_strategies() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        strats = [_row_dict(r) for r in conn.execute(
            "SELECT * FROM strategy_registry ORDER BY strategy_id"
        ).fetchall()]
        all_legs = [_row_dict(r) for r in conn.execute(
            "SELECT * FROM strategy_legs ORDER BY strategy_id, leg_index"
        ).fetchall()]
    finally:
        conn.close()
    by_sid: Dict[int, List[Dict[str, Any]]] = {}
    for lg in all_legs:
        by_sid.setdefault(lg["strategy_id"], []).append(lg)
    return [_fmt_strategy(s, by_sid.get(s["strategy_id"], [])) for s in strats]


def get_strategy(strategy_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        return _load_with_legs(conn, strategy_id)
    finally:
        conn.close()


# --- create ---


def create_strategy(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("strategy_name") or "").strip()
    if not name:
        raise ValueError("strategy_name is required")
    code = str(payload.get("strategy_code") or "").strip()
    state = str(payload.get("state") or "Stop").strip()
    if state not in _VALID_STATES:
        raise ValueError(f"state must be one of {_VALID_STATES}")

    input_raw = payload.get("input_json")
    if isinstance(input_raw, str):
        input_raw = json.loads(input_raw) if input_raw.strip() else {}
    input_json_str = json.dumps(input_raw or {}, ensure_ascii=False)

    legs_payload: List[Dict[str, Any]] = payload.get("legs") or []
    if not legs_payload:
        legs_payload = [{
            "leg_index": 0,
            "condition_id": str(payload.get("condition_id") or "").strip(),
            "yes_token": payload.get("yes_token"),
            "no_token": payload.get("no_token"),
            "asset_class": payload.get("asset_class") or "polymarket_binary",
            "venue": payload.get("venue") or "polymarket",
            "symbol": payload.get("symbol") or "",
            "instrument_id": payload.get("instrument_id") or "",
            "instrument_json": payload.get("instrument_json") or {},
            "budget_cap": _safe_float(payload.get("budget_cap")),
        }]
    payload["strategy_bankroll"] = _derive_strategy_bankroll(payload, legs_payload)

    ts = _now()
    uid = f"stg_{uuid.uuid4().hex[:16]}"
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO strategy_registry(
                strategy_uid, strategy_name, strategy_code, state,
                initial_capital, strategy_bankroll, profit_roll_ratio, realized_profit,
                input_json, created_at_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, name, code, state,
                _safe_float(payload.get("initial_capital")),
                _safe_float(payload.get("strategy_bankroll")),
                _safe_float(payload.get("profit_roll_ratio")),
                _safe_float(payload.get("realized_profit")),
                input_json_str, ts, ts,
            ),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i, raw_leg in enumerate(legs_payload):
            leg = _normalize_leg_payload(raw_leg, i)
            leg["leg_uid"] = leg["leg_uid"] or _new_leg_uid(sid)
            conn.execute(
                """INSERT INTO strategy_legs(
                    strategy_id, leg_uid, leg_index, condition_id, yes_token, no_token,
                    asset_class, venue, symbol, instrument_id, instrument_json,
                    budget_cap, params_json,
                    created_at_utc, updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid,
                    leg["leg_uid"],
                    leg["leg_index"],
                    leg["condition_id"],
                    leg["yes_token"],
                    leg["no_token"],
                    leg["asset_class"],
                    leg["venue"],
                    leg["symbol"],
                    leg["instrument_id"],
                    leg["instrument_json"],
                    leg["budget_cap"],
                    leg["params_json"],
                    ts, ts,
                ),
            )
        conn.commit()
        result = _load_with_legs(conn, sid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result  # type: ignore[return-value]


# --- update ---


def update_strategy(strategy_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT * FROM strategy_registry WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()
        if not existing:
            raise ValueError(f"strategy {strategy_id} not found")

        if "legs" in payload:
            derived_bankroll = _derive_strategy_bankroll(payload, payload.get("legs") or [])
            if derived_bankroll > 0 and _safe_float(payload.get("strategy_bankroll"), 0.0) <= 0:
                payload["strategy_bankroll"] = derived_bankroll

        sets: List[str] = []
        vals: List[Any] = []
        for col in ("strategy_name", "strategy_code", "state",
                     "initial_capital", "strategy_bankroll",
                     "profit_roll_ratio", "realized_profit"):
            if col not in payload:
                continue
            if col == "state":
                v = str(payload[col]).strip()
                if v not in _VALID_STATES:
                    raise ValueError(f"state must be one of {_VALID_STATES}")
                sets.append(f"{col} = ?")
                vals.append(v)
            elif col in ("initial_capital", "strategy_bankroll",
                         "profit_roll_ratio", "realized_profit"):
                sets.append(f"{col} = ?")
                vals.append(_safe_float(payload[col]))
            else:
                sets.append(f"{col} = ?")
                vals.append(str(payload[col]).strip())

        if "input_json" in payload:
            raw = payload["input_json"]
            if isinstance(raw, str):
                raw = json.loads(raw) if raw.strip() else {}
            sets.append("input_json = ?")
            vals.append(json.dumps(raw or {}, ensure_ascii=False))

        if sets:
            ts = _now()
            sets.append("updated_at_utc = ?")
            vals.append(ts)
            vals.append(strategy_id)
            conn.execute(
                f"UPDATE strategy_registry SET {', '.join(sets)} WHERE strategy_id = ?",
                vals,
            )
            if "legs" not in payload:
                conn.commit()

        if "legs" in payload:
            legs_payload = payload.get("legs") or []
            ts = _now()
            existing_legs = [
                _row_dict(row)
                for row in conn.execute(
                    "SELECT * FROM strategy_legs WHERE strategy_id = ? ORDER BY leg_index",
                    (strategy_id,),
                ).fetchall()
            ]
            if _legs_identity_changed(existing_legs, legs_payload):
                _clear_virtual_state(conn, strategy_id)
            conn.execute("DELETE FROM strategy_legs WHERE strategy_id = ?", (strategy_id,))
            for i, raw_leg in enumerate(legs_payload):
                leg = _normalize_leg_payload(raw_leg, i)
                leg["leg_uid"] = _leg_uid_for_payload(existing_legs, leg, i, strategy_id)
                conn.execute(
                    """INSERT INTO strategy_legs(
                        strategy_id, leg_uid, leg_index, condition_id, yes_token, no_token,
                        asset_class, venue, symbol, instrument_id, instrument_json,
                        budget_cap, params_json,
                        created_at_utc, updated_at_utc
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        strategy_id,
                        leg["leg_uid"],
                        leg["leg_index"],
                        leg["condition_id"],
                        leg["yes_token"],
                        leg["no_token"],
                        leg["asset_class"],
                        leg["venue"],
                        leg["symbol"],
                        leg["instrument_id"],
                        leg["instrument_json"],
                        leg["budget_cap"],
                        leg["params_json"],
                        ts, ts,
                    ),
                )
            conn.execute(
                "UPDATE strategy_registry SET updated_at_utc = ? WHERE strategy_id = ?",
                (ts, strategy_id),
            )
            conn.commit()

        result = _load_with_legs(conn, strategy_id)
    finally:
        conn.close()
    if "strategy_bankroll" in payload:
        from services.virtual_execution import sync_virtual_account_bankroll

        sync_virtual_account_bankroll(strategy_id, payload["strategy_bankroll"])
    if not result:
        raise ValueError(f"strategy {strategy_id} not found after update")
    return result


# All state transitions are allowed; confirmation is handled on the frontend.
def update_strategy_state(strategy_id: int, new_state: str) -> Dict[str, Any]:
    if new_state not in _VALID_STATES:
        raise ValueError(f"state must be one of {_VALID_STATES}")
    return update_strategy(strategy_id, {"state": new_state})


def update_strategy_legs(strategy_id: int, legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT strategy_id FROM strategy_registry WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        if not existing:
            raise ValueError(f"strategy {strategy_id} not found")

        ts = _now()
        existing_legs = [
            _row_dict(row)
            for row in conn.execute(
                "SELECT * FROM strategy_legs WHERE strategy_id = ? ORDER BY leg_index",
                (strategy_id,),
            ).fetchall()
        ]
        should_clear_virtual_state = _legs_identity_changed(existing_legs, legs)
        if should_clear_virtual_state:
            _clear_virtual_state(conn, strategy_id)

        conn.execute("DELETE FROM strategy_legs WHERE strategy_id = ?", (strategy_id,))
        for i, raw_leg in enumerate(legs):
            leg = _normalize_leg_payload(raw_leg, i)
            leg["leg_uid"] = _leg_uid_for_payload(existing_legs, leg, i, strategy_id)
            conn.execute(
                """INSERT INTO strategy_legs(
                    strategy_id, leg_uid, leg_index, condition_id, yes_token, no_token,
                    asset_class, venue, symbol, instrument_id, instrument_json,
                    budget_cap, params_json,
                    created_at_utc, updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    strategy_id,
                    leg["leg_uid"],
                    leg["leg_index"],
                    leg["condition_id"],
                    leg["yes_token"],
                    leg["no_token"],
                    leg["asset_class"],
                    leg["venue"],
                    leg["symbol"],
                    leg["instrument_id"],
                    leg["instrument_json"],
                    leg["budget_cap"],
                    leg["params_json"],
                    ts, ts,
                ),
            )
        conn.execute(
            "UPDATE strategy_registry SET updated_at_utc = ? WHERE strategy_id = ?",
            (ts, strategy_id),
        )
        conn.commit()
        result = _load_with_legs(conn, strategy_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result  # type: ignore[return-value]


# --- delete ---


def delete_strategy(strategy_id: int) -> bool:
    conn = _connect()
    try:
        affected = conn.execute(
            "DELETE FROM strategy_registry WHERE strategy_id = ?", (strategy_id,)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return affected > 0
