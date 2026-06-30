from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from services.order_store import get_db_path as get_order_db_path
from services.strategy_data_source import connect as strategy_connect


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    suffix = f" WHERE {where}" if where else ""
    row = conn.execute(f"SELECT COUNT(*) FROM {table}{suffix}", params).fetchone()
    return int(row[0] or 0) if row else 0


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _sample(rows: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    return rows[:limit]


def _check(status: str, title: str, detail: str, *, count: int = 0, sample: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "detail": detail,
        "count": count,
        "sample": sample or [],
    }


def _read_real_orders(limit: int) -> tuple[List[Dict[str, Any]], int, List[Dict[str, Any]], int, str | None]:
    path = Path(get_order_db_path())
    if not path.exists():
        return [], 0, [], 0, None
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "orders"):
                return [], 0, [], 0, None
            active_statuses = ("created", "submitted", "open", "partially_filled", "cancel_requested")
            placeholders = ",".join("?" * len(active_statuses))
            active_total_row = conn.execute(
                f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})",
                active_statuses,
            ).fetchone()
            active_total = int(active_total_row[0] or 0) if active_total_row else 0
            active = _rows(
                conn,
                f"""SELECT * FROM orders
                    WHERE status IN ({placeholders})
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?""",
                (*active_statuses, limit),
            )
            historical_missing_total_row = conn.execute(
                """SELECT COUNT(*) FROM orders
                   WHERE status IN ('filled', 'reconciled', 'partially_filled')
                     AND (strategy_id IS NULL OR COALESCE(leg_uid, '') = '')"""
            ).fetchone()
            historical_missing_total = int(historical_missing_total_row[0] or 0) if historical_missing_total_row else 0
            historical_missing = _rows(
                conn,
                """SELECT * FROM orders
                   WHERE status IN ('filled', 'reconciled', 'partially_filled')
                     AND (strategy_id IS NULL OR COALESCE(leg_uid, '') = '')
                   ORDER BY updated_at DESC, created_at DESC
                   LIMIT ?""",
                (limit,),
            )
            return active, active_total, historical_missing, historical_missing_total, None
        finally:
            conn.close()
    except Exception as exc:
        return [], 0, [], 0, str(exc)


def get_ledger_snapshot(limit: int = 100) -> Dict[str, Any]:
    """Return a cross-strategy ledger view plus diagnostics for broken ownership links."""
    limit = max(1, min(int(limit or 100), 500))
    conn = strategy_connect(readonly=True)
    try:
        registry_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(strategy_registry)").fetchall()}
        mode_col = "mode" if "mode" in registry_cols else "state"
        strategies = _rows(
            conn,
            f"""SELECT strategy_id, strategy_name, strategy_code, {mode_col} AS mode, strategy_bankroll,
                      updated_at_utc
               FROM strategy_registry
               ORDER BY strategy_id""",
        )
        for strategy in strategies:
            strategy["state"] = strategy.get("mode") or "Stop"
        strategy_by_id = {int(s["strategy_id"]): s for s in strategies}

        legs = _rows(
            conn,
            """SELECT strategy_id, leg_uid, leg_index, condition_id, yes_token, no_token,
                      leg_kind, asset_class, venue, symbol, instrument_id, instrument_json, budget_cap
               FROM strategy_legs
               ORDER BY strategy_id, leg_index""",
        )
        leg_keys = {
            (int(leg["strategy_id"]), str(leg.get("leg_uid") or ""))
            for leg in legs
            if str(leg.get("leg_uid") or "")
        }
        leg_by_index = {
            (int(leg["strategy_id"]), int(leg.get("leg_index") or 0)): leg
            for leg in legs
        }

        virtual_open_orders = _rows(
            conn,
            """SELECT id, strategy_id, leg_uid, leg_index, condition_id, token_id,
                      side, action, qty, filled_qty, remaining_qty, price,
                      order_type, post_only, reduce_only, client_order_tag,
                      status, reason, updated_at_utc
               FROM strategy_virtual_open_orders
               ORDER BY updated_at_utc DESC, id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "strategy_virtual_open_orders") else []

        virtual_positions = _rows(
            conn,
            """SELECT p.id, p.strategy_id, p.leg_index, p.side, p.qty, p.avg_price,
                      p.cost, p.realized_pnl, p.updated_at_utc
               FROM strategy_virtual_positions p
               ORDER BY p.updated_at_utc DESC, p.id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "strategy_virtual_positions") else []

        virtual_positions_v2 = _rows(
            conn,
            """SELECT id, strategy_id, instrument_id, asset_class, side, qty,
                      avg_price, cost, market_value, realized_pnl, unrealized_pnl,
                      updated_at_utc
               FROM strategy_virtual_positions_v2
               ORDER BY updated_at_utc DESC, id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "strategy_virtual_positions_v2") else []

        real_positions = _rows(
            conn,
            """SELECT id, strategy_id, leg_uid, leg_index_snapshot, condition_id,
                      token_id, outcome, qty, avg_price, cost, realized_pnl,
                      source, updated_at_utc
               FROM strategy_real_positions
               ORDER BY updated_at_utc DESC, id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "strategy_real_positions") else []

        unassigned_positions = _rows(
            conn,
            """SELECT id, wallet_address, condition_id, token_id, outcome, qty,
                      avg_price, source, reason, updated_at_utc
               FROM unassigned_positions
               ORDER BY updated_at_utc DESC, id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "unassigned_positions") else []

        recent_virtual_orders = _rows(
            conn,
            """SELECT id, strategy_id, leg_index, side, action, qty, price,
                      gross_notional, fee, status, reason, liquidity_role,
                      created_at_utc
               FROM strategy_virtual_orders
               ORDER BY created_at_utc DESC, id DESC
               LIMIT ?""",
            (limit,),
        ) if _table_exists(conn, "strategy_virtual_orders") else []

        checks: List[Dict[str, Any]] = []

        missing_leg_uid = [leg for leg in legs if not str(leg.get("leg_uid") or "").strip()]
        checks.append(_check(
            "error" if missing_leg_uid else "ok",
            "Leg UID 完整性",
            "所有 strategy_legs 都应该有不可变 leg_uid。" if not missing_leg_uid else "存在缺失 leg_uid 的 leg，订单归属会不可靠。",
            count=len(missing_leg_uid),
            sample=_sample(missing_leg_uid),
        ))

        orphan_open_orders = [
            row for row in virtual_open_orders
            if int(row.get("strategy_id") or 0) not in strategy_by_id
            or (
                str(row.get("leg_uid") or "").strip()
                and (int(row.get("strategy_id") or 0), str(row.get("leg_uid") or "")) not in leg_keys
            )
            or (
                not str(row.get("leg_uid") or "").strip()
                and (int(row.get("strategy_id") or 0), int(row.get("leg_index") or 0)) not in leg_by_index
            )
        ]
        checks.append(_check(
            "error" if orphan_open_orders else "ok",
            "虚拟挂单归属",
            "虚拟挂单都能映射到策略 leg。" if not orphan_open_orders else "存在无法映射到当前 leg 的虚拟挂单。",
            count=len(orphan_open_orders),
            sample=_sample(orphan_open_orders),
        ))

        active_reduce_sell = [
            row for row in virtual_open_orders
            if str(row.get("status") or "") in {"open", "partially_filled"}
            and str(row.get("action") or "").upper() == "SELL"
            and int(row.get("reduce_only") or 0)
        ]
        checks.append(_check(
            "warning" if active_reduce_sell else "ok",
            "待撤 reduce-only 卖单",
            "当前没有待撤的 reduce-only SELL。" if not active_reduce_sell else "强平前必须先撤这些 reduce-only SELL。",
            count=len(active_reduce_sell),
            sample=_sample(active_reduce_sell),
        ))

        real_without_uid = [
            row for row in real_positions
            if not str(row.get("leg_uid") or "").strip()
            or (int(row.get("strategy_id") or 0), str(row.get("leg_uid") or "")) not in leg_keys
        ]
        checks.append(_check(
            "error" if real_without_uid else "ok",
            "实盘子仓归属",
            "实盘子仓都带有可追踪 leg_uid。" if not real_without_uid else "存在无法映射到当前 leg 的实盘子仓，自动强平应阻断。",
            count=len(real_without_uid),
            sample=_sample(real_without_uid),
        ))

        shared_tokens: Dict[str, set] = defaultdict(set)
        for leg in legs:
            sid = int(leg.get("strategy_id") or 0)
            for token_key in ("yes_token", "no_token"):
                token = str(leg.get(token_key) or "").strip()
                if token:
                    shared_tokens[token].add(sid)
        shared_token_rows = [
            {"token_id": token, "strategy_ids": sorted(sids), "strategy_count": len(sids)}
            for token, sids in shared_tokens.items()
            if len(sids) > 1
        ]
        checks.append(_check(
            "warning" if shared_token_rows else "ok",
            "同 token 多策略",
            "没有检测到跨策略复用 token。" if not shared_token_rows else "存在同 token 多策略，实盘强平只能按子账本处理。",
            count=len(shared_token_rows),
            sample=_sample(shared_token_rows),
        ))

        unassigned_qty = sum(float(row.get("qty") or 0) for row in unassigned_positions)
        checks.append(_check(
            "warning" if unassigned_qty > 0 else "ok",
            "未归属实盘仓位",
            "没有未归属实盘仓位。" if unassigned_qty <= 0 else "存在未归属仓位，自动策略不能默认拿来卖。",
            count=len(unassigned_positions),
            sample=_sample(unassigned_positions),
        ))

        real_open_orders, real_active_total, historical_missing_orders, historical_missing_total, order_error = _read_real_orders(limit)
        if order_error:
            checks.append(_check(
                "error",
                "实盘订单库读取",
                f"读取 orders 表失败：{order_error}",
            ))

        bad_real_orders = [
            row for row in real_open_orders
            if not row.get("strategy_id") or not str(row.get("leg_uid") or "").strip()
        ]
        checks.append(_check(
            "error" if bad_real_orders else "ok",
            "实盘订单映射",
            "活跃实盘订单都有 strategy_id 和 leg_uid。" if not bad_real_orders else "存在缺少 strategy_id/leg_uid 的活跃实盘订单。",
            count=len(bad_real_orders),
            sample=_sample(bad_real_orders),
        ))

        checks.append(_check(
            "warning" if historical_missing_total else "ok",
            "历史成交归因（只读）",
            "历史成交都有策略归因。" if not historical_missing_total else "存在缺少 strategy_id/leg_uid 的历史成交；这里没有清除，不再展示噪音样本，后续应进入未归属/人工分配流程。",
            count=historical_missing_total,
            sample=[],
        ))

        debug_summary = {
            "status": "error" if any(c["status"] == "error" for c in checks) else ("warning" if any(c["status"] == "warning" for c in checks) else "ok"),
            "errors": sum(1 for c in checks if c["status"] == "error"),
            "warnings": sum(1 for c in checks if c["status"] == "warning"),
            "checks": checks,
            "table_counts": {
                "strategy_registry": len(strategies),
                "strategy_legs": len(legs),
                "strategy_virtual_open_orders_active": _count(conn, "strategy_virtual_open_orders", "status IN ('open','partially_filled')"),
                "strategy_virtual_positions": _count(conn, "strategy_virtual_positions", "qty > 0"),
                "strategy_virtual_positions_v2": _count(conn, "strategy_virtual_positions_v2", "qty > 0"),
                "strategy_virtual_orders": _count(conn, "strategy_virtual_orders"),
                "strategy_real_positions": _count(conn, "strategy_real_positions", "qty > 0"),
                "unassigned_positions": _count(conn, "unassigned_positions", "qty > 0"),
                "real_active_orders": real_active_total,
                "historical_orders_missing_attribution": historical_missing_total,
            },
        }

        return {
            "ok": True,
            "data": {
                "strategies": strategies,
                "legs": legs,
                "virtual_open_orders": virtual_open_orders,
                "virtual_positions": virtual_positions,
                "virtual_positions_v2": virtual_positions_v2,
                "virtual_orders": recent_virtual_orders,
                "real_open_orders": real_open_orders[:limit],
                "real_positions": real_positions,
                "unassigned_positions": unassigned_positions,
                "debug": debug_summary,
            },
        }
    finally:
        conn.close()
