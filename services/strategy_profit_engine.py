from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from services.config_loader import BASE_DIR, get_default_wallets, load_web_settings
from services.clob_orderbook_service import fetch_binary_orderbook_quotes
from services.http_client import SESSION, get_timeout
from services import strategy_data_source
from services.strategy_data_source import connect as ds_connect


DATA_API = "https://data-api.polymarket.com"
STRATEGY_TABLE_CANDIDATES = ["strategy_registry"]
PROFIT_COLUMNS = ["profit", "Profit"]


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _side_cost(item: Dict[str, Any], side: str) -> float:
    side_title = side.capitalize()
    filled_cost = _first_float(
        item.get(f"{side_title}_filled_cost"),
        item.get(f"{side_title}_Filled_Cost"),
        item.get(f"{side}_filled_cost"),
        item.get(f"{side}_Filled_Cost"),
    ) or 0.0
    open_buy_cost = _first_float(
        item.get(f"{side_title}_open_buy_cost"),
        item.get(f"{side_title}_Open_Buy_Cost"),
        item.get(f"{side}_open_buy_cost"),
        item.get(f"{side}_Open_Buy_Cost"),
    ) or 0.0
    return filled_cost + open_buy_cost


def resolve_strategy_bankroll(item: Dict[str, Any]) -> float | None:
    bankroll = _first_float(
        item.get("strategy_bankroll"),
        item.get("Strategy_Bankroll"),
    )
    if bankroll is not None and bankroll > 0:
        return bankroll

    initial_capital = _first_float(
        item.get("initial_capital"),
        item.get("Initial_Capital"),
    )
    profit_roll_ratio = _first_float(
        item.get("profit_roll_ratio"),
        item.get("Profit_Roll_Ratio"),
    )
    realized_profit = _first_float(
        item.get("realized_profit"),
        item.get("Realized_Profit"),
    )
    if initial_capital is None:
        return None
    computed_bankroll = initial_capital + (realized_profit or 0.0) * (profit_roll_ratio or 0.0)
    return computed_bankroll if computed_bankroll > 0 else initial_capital


def calculate_position_pcts(
    item: Dict[str, Any],
    yes_qty: float,
    yes_price: float | None,
    no_qty: float,
    no_price: float | None,
) -> tuple[float, float]:
    bankroll = resolve_strategy_bankroll(item)
    yes_cost = _side_cost(item, "yes")
    no_cost = _side_cost(item, "no")
    if bankroll and bankroll > 0 and (yes_cost > 0 or no_cost > 0):
        return yes_cost / bankroll, no_cost / bankroll

    yes_value = yes_qty * (yes_price or 0.0)
    no_value = no_qty * (no_price or 0.0)

    total_value = yes_value + no_value
    if total_value > 0:
        return yes_value / total_value, no_value / total_value

    total_qty = yes_qty + no_qty
    if total_qty > 0:
        return yes_qty / total_qty, no_qty / total_qty
    return 0.0, 0.0


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "ok", "filled", "success"}


def _resolve_workspace_db_path(value: Any, default_name: str) -> Path:
    text = str(value or "").strip()
    path = Path(text).expanduser() if text else (BASE_DIR / default_name)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _ensure_sqlite_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def _strategy_db_info() -> tuple[Path, str]:
    settings = load_web_settings()
    db_path = _resolve_workspace_db_path(settings.get("strategy_monitoring_db_path", ""), "PolyMarketMonitoring.db")
    preferred_table = str(settings.get("strategy_monitoring_table", "monitoring")).strip() or "monitoring"
    return db_path, preferred_table


def _order_db_path() -> Path:
    settings = load_web_settings()
    return _resolve_workspace_db_path(settings.get("order_list_db_path", ""), "PolyMarketOrderList.db")


def _discover_table(conn: sqlite3.Connection, preferred_table: str) -> str:
    tables = [str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()]
    candidates = [preferred_table] + STRATEGY_TABLE_CANDIDATES
    for table in candidates:
        if table in tables:
            return table
    raise ValueError("No strategy table found.")


def _get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _fetch_positions_for_wallet(wallet: str) -> list[Dict[str, Any]]:
    response = SESSION.get(
        f"{DATA_API}/positions",
        params={"user": wallet, "sizeThreshold": 0},
        timeout=get_timeout(),
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else payload.get("data", [])


def fetch_remote_position_map(wallets: list[str] | None = None) -> Dict[str, Dict[str, float | str | None]]:
    wallets = wallets or get_default_wallets()
    token_map: Dict[str, Dict[str, float | str | None]] = {}
    for wallet in wallets:
        for pos in _fetch_positions_for_wallet(wallet):
            token_id = (
                pos.get("asset")
                or pos.get("tokenId")
                or pos.get("token_id")
                or pos.get("clobTokenId")
            )
            token = str(token_id or "").strip()
            if not token:
                continue
            qty = _safe_float(pos.get("size")) or 0.0
            avg_price = _safe_float(pos.get("avgPrice") or pos.get("averagePrice") or pos.get("price"))
            existing = token_map.get(token)
            if existing:
                old_qty = float(existing.get("qty") or 0.0)
                old_avg = _safe_float(existing.get("avg_price"))
                new_qty = old_qty + qty
                if avg_price is not None and new_qty > 0:
                    weighted = ((old_avg or 0.0) * old_qty + avg_price * qty) / new_qty
                else:
                    weighted = old_avg
                existing["qty"] = new_qty
                existing["avg_price"] = weighted
            else:
                token_map[token] = {
                    "qty": qty,
                    "avg_price": avg_price,
                    "wallet": wallet,
                }
    return token_map


def apply_live_position_overrides(
    strategy_item: Dict[str, Any],
    remote_map: Dict[str, Dict[str, float | str | None]],
    local_map: Dict[str, Dict[str, float | int | None]],
) -> None:
    """
    Use Polymarket positions API as source of truth for open qty/avg per clob token.
    If a token is not returned, treat as flat.
    """
    tags: list[str] = []
    for side in ("yes", "no"):
        token = str(strategy_item.get(f"{side}_token") or "").strip()
        if not token:
            continue
        qty_key = f"{side}_qty"
        avg_key = f"{side}_avg"
        if token in remote_map:
            entry = remote_map[token]
            qty = float(entry.get("qty") or 0.0)
            strategy_item[qty_key] = qty
            ap = _safe_float(entry.get("avg_price"))
            strategy_item[avg_key] = ap if qty > 0 else None
            tags.append("remote")
            continue
        strategy_item[qty_key] = 0.0
        strategy_item[avg_key] = None
        tags.append("flat")
    if tags:
        if "remote" in tags:
            strategy_item["position_source"] = "wallet_api"
        else:
            strategy_item["position_source"] = "wallet_flat"


def fetch_local_order_map() -> Dict[str, Dict[str, float | int | None]]:
    """Aggregate local order data by token.
    Priority: new `orders` table (state-machine based) -> legacy `polyMarket_OrderList`.
    """
    # Try new orders table first
    try:
        from services.order_store import aggregate_filled_by_token
        new_map = aggregate_filled_by_token()
        if new_map:
            return new_map
    except Exception:
        pass

    # Fallback: legacy table
    db_path = _order_db_path()
    _ensure_sqlite_file(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()]
        if "polyMarket_OrderList" not in tables:
            return {}
        cols = _get_columns(conn, "polyMarket_OrderList")
        rows = conn.execute('SELECT * FROM "polyMarket_OrderList"').fetchall()
        token_map: Dict[str, Dict[str, float | int | None]] = {}
        for raw in rows:
            item = {cols[idx]: raw[idx] for idx in range(len(cols))}
            token = str(item.get("Token") or "").strip()
            if not token:
                continue
            if not (_truthy(item.get("IsSuccess")) or str(item.get("tx_hash_hint") or "").strip()):
                continue
            qty = _safe_float(item.get("Qty")) or 0.0
            price = _safe_float(item.get("Buy_Price"))
            side = str(item.get("BUY/SELL") or "").strip().upper()
            bucket = token_map.setdefault(token, {"buy_qty": 0.0, "sell_qty": 0.0, "buy_cost": 0.0, "sell_cost": 0.0, "trades": 0})
            bucket["trades"] = int(bucket["trades"] or 0) + 1
            if side == "SELL":
                bucket["sell_qty"] = float(bucket["sell_qty"] or 0.0) + qty
                bucket["sell_cost"] = float(bucket["sell_cost"] or 0.0) + qty * (price or 0.0)
            else:
                bucket["buy_qty"] = float(bucket["buy_qty"] or 0.0) + qty
                bucket["buy_cost"] = float(bucket["buy_cost"] or 0.0) + qty * (price or 0.0)
        return token_map
    finally:
        conn.close()


def _ensure_profit_column(conn: sqlite3.Connection, table: str) -> str:
    cols = set(_get_columns(conn, table))
    for col in PROFIT_COLUMNS:
        if col in cols:
            return col
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "profit" TEXT')
    conn.commit()
    return "profit"


def _fetch_virtual_positions(strategy_id: int) -> Dict[tuple, Dict[str, Any]]:
    """Read virtual positions for a strategy. Returns {(leg_index, side): row_dict}."""
    result: Dict[tuple, Dict[str, Any]] = {}
    try:
        conn = ds_connect(readonly=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            result[(d["leg_index"], d["side"].upper())] = d
    except Exception:
        pass
    return result


def _fetch_live_leg_prices(yes_token: str, no_token: str, leg: Dict[str, Any]) -> tuple[float | None, float | None]:
    """Return current ask prices for PNL; fall back to legacy cached leg prices."""
    try:
        quotes = fetch_binary_orderbook_quotes(yes_token, no_token)
    except Exception:
        quotes = {}
    yes_price = _safe_float(quotes.get("yes_ask"))
    no_price = _safe_float(quotes.get("no_ask"))
    if yes_price is None:
        yes_price = _safe_float(leg.get("yes_current_price"))
    if no_price is None:
        no_price = _safe_float(leg.get("no_current_price"))
    return yes_price, no_price


def compute_and_persist_strategy_profit() -> Dict[str, Any]:
    """Compute profit for all strategies using new unified data source.
    Reads from strategy_registry + strategy_legs, writes positions back to legs.
    """
    source_statuses: Dict[str, Dict[str, Any]] = {}
    try:
        remote_map = fetch_remote_position_map()
        source_statuses["remote_positions"] = {
            "status": "good",
            "error": None,
            "count": len(remote_map),
        }
    except Exception as exc:
        remote_map = {}
        source_statuses["remote_positions"] = {
            "status": "error",
            "error": str(exc),
            "count": 0,
        }

    try:
        local_map = fetch_local_order_map()
        source_statuses["local_orders"] = {
            "status": "good",
            "error": None,
            "count": len(local_map),
        }
    except Exception as exc:
        local_map = {}
        source_statuses["local_orders"] = {
            "status": "error",
            "error": str(exc),
            "count": 0,
        }

    strategies = strategy_data_source.list_strategies()
    if not strategies:
        return {
            "ok": True,
            "table": "strategy_registry",
            "running_strategy_count": 0,
            "total_strategy_profit": 0.0,
            "total_strategy_cost": 0.0,
            "total_strategy_return_pct": None,
            "rows": [],
            "source_statuses": source_statuses,
        }

    results = []
    total_profit = 0.0
    total_cost = 0.0
    position_updates: List[Dict[str, Any]] = []

    for strat in strategies:
        strategy_id = strat["strategy_id"]
        legs = strat.get("legs") or []
        if not legs:
            continue
        leg = legs[0]
        yes_token = str(leg.get("yes_token") or "").strip()
        no_token = str(leg.get("no_token") or "").strip()
        yes_price, no_price = _fetch_live_leg_prices(yes_token, no_token, leg)

        # Virtual 模式：从 strategy_virtual_positions 读持仓
        is_virtual = str(strat.get("state") or "").strip().lower() == "virtual"
        if is_virtual:
            vpos = _fetch_virtual_positions(strategy_id)
            yes_vp = vpos.get((0, "YES"), {})
            no_vp = vpos.get((0, "NO"), {})
            yes_qty = float(yes_vp.get("qty") or 0.0)
            no_qty = float(no_vp.get("qty") or 0.0)
            yes_avg = _safe_float(yes_vp.get("avg_price"))
            no_avg = _safe_float(no_vp.get("avg_price"))
            position_source = "virtual" if (yes_qty > 0 or no_qty > 0) else "virtual_flat"
        else:
            yes_remote = remote_map.get(yes_token, {})
            no_remote = remote_map.get(no_token, {})
            yes_local = local_map.get(yes_token, {})
            no_local = local_map.get(no_token, {})

            yes_qty = float(yes_remote.get("qty") or 0.0)
            no_qty = float(no_remote.get("qty") or 0.0)
            yes_avg = _safe_float(yes_remote.get("avg_price"))
            no_avg = _safe_float(no_remote.get("avg_price"))

            position_source = "wallet_api" if (yes_remote or no_remote) else ""

            if yes_qty == 0.0 and yes_local:
                buy_qty = float(yes_local.get("buy_qty") or 0.0)
                sell_qty = float(yes_local.get("sell_qty") or 0.0)
                yes_qty = max(0.0, buy_qty - sell_qty)
                buy_cost = float(yes_local.get("buy_cost") or 0.0)
                yes_avg = (buy_cost / buy_qty) if buy_qty > 0 else yes_avg
                if not position_source:
                    position_source = "local_order_db"
            if no_qty == 0.0 and no_local:
                buy_qty = float(no_local.get("buy_qty") or 0.0)
                sell_qty = float(no_local.get("sell_qty") or 0.0)
                no_qty = max(0.0, buy_qty - sell_qty)
                buy_cost = float(no_local.get("buy_cost") or 0.0)
                no_avg = (buy_cost / buy_qty) if buy_qty > 0 else no_avg
                if not position_source:
                    position_source = "local_order_db"

        flat_item = strategy_data_source.strategy_to_flat_dict(strat)
        yes_pic, no_pic = calculate_position_pcts(flat_item, yes_qty, yes_price, no_qty, no_price)
        strategy_bankroll = resolve_strategy_bankroll(flat_item)
        profit = 0.0
        cost = 0.0
        if yes_avg is not None:
            profit += ((yes_price or 0.0) - yes_avg) * yes_qty
            cost += yes_avg * yes_qty
        if no_avg is not None:
            profit += ((no_price or 0.0) - no_avg) * no_qty
            cost += no_avg * no_qty

        total_profit += profit
        total_cost += cost
        results.append(
            {
                "row_id": strategy_id,
                "strategy": strat.get("strategy_name") or strat.get("strategy_code"),
                "profit": profit,
                "yes_qty": yes_qty,
                "yes_avg": yes_avg,
                "yes_pic": yes_pic,
                "yes_current_pct": yes_pic,
                "no_qty": no_qty,
                "no_avg": no_avg,
                "no_pic": no_pic,
                "no_current_pct": no_pic,
                "strategy_bankroll": strategy_bankroll,
            }
        )

        position_updates.append({
            "strategy_id": strategy_id,
            "leg_index": leg.get("leg_index", 0),
            "yes_qty": yes_qty,
            "no_qty": no_qty,
            "yes_avg_cost": yes_avg,
            "no_avg_cost": no_avg,
            "yes_current_price": yes_price,
            "no_current_price": no_price,
            "unrealized_pnl": profit,
            "position_source": position_source or "flat",
        })

    # Batch write positions back to strategy_legs
    strategy_data_source.batch_update_positions(position_updates)

    # Write realized_profit per strategy
    for row in results:
        strategy_data_source.update_strategy_profit(row["row_id"], row["profit"])

    running_count = sum(1 for row in results if (row["yes_qty"] or 0) > 0 or (row["no_qty"] or 0) > 0)
    total_return_pct = (total_profit / total_cost) if total_cost > 0 else None
    return {
        "ok": True,
        "table": "strategy_registry",
        "running_strategy_count": running_count,
        "total_strategy_profit": total_profit,
        "total_strategy_cost": total_cost,
        "total_strategy_return_pct": total_return_pct,
        "rows": results,
        "source_statuses": source_statuses,
    }
