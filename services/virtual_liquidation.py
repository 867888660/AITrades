from __future__ import annotations

import json
from typing import Any, Dict, List


CASH_EPSILON = 1e-9


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_book_levels(raw: Any, *, side: str) -> List[Dict[str, float]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    by_price: Dict[float, float] = {}
    for level in raw:
        if not isinstance(level, dict):
            continue
        price = safe_float(level.get("price"))
        qty = safe_float(level.get("qty", level.get("size")))
        if price is None or qty is None or price <= 0 or qty <= 0:
            continue
        by_price[price] = by_price.get(price, 0.0) + qty
    reverse = str(side or "").lower() == "bid"
    return [
        {"price": price, "qty": qty}
        for price, qty in sorted(by_price.items(), key=lambda item: item[0], reverse=reverse)
    ]


def binary_fee(qty: float, price: float, fee_rate: float) -> float:
    if qty <= 0 or price <= 0 or fee_rate <= 0:
        return 0.0
    return qty * fee_rate * price * (1.0 - price)


def liquidate_position(
    qty: Any,
    fallback_price: Any,
    fee_rate: Any,
    *,
    bid_levels: Any = None,
) -> Dict[str, Any]:
    requested_qty = max(0.0, safe_float(qty, 0.0) or 0.0)
    rate = max(0.0, safe_float(fee_rate, 0.0) or 0.0)
    levels = normalize_book_levels(bid_levels, side="bid")

    gross = 0.0
    fee = 0.0
    filled_qty = 0.0
    fills: List[Dict[str, float]] = []

    for level in levels:
        remaining = requested_qty - filled_qty
        if remaining <= CASH_EPSILON:
            break
        price = safe_float(level.get("price"), 0.0) or 0.0
        available = safe_float(level.get("qty"), 0.0) or 0.0
        if price <= 0 or available <= 0:
            continue
        take_qty = min(remaining, available)
        fills.append({"price": price, "qty": take_qty})
        gross += take_qty * price
        fee += binary_fee(take_qty, price, rate)
        filled_qty += take_qty

    used_depth = bool(levels)
    if filled_qty <= CASH_EPSILON and not used_depth:
        price = safe_float(fallback_price)
        if requested_qty > CASH_EPSILON and price is not None and price > 0:
            filled_qty = requested_qty
            gross = requested_qty * price
            fee = binary_fee(requested_qty, price, rate)
            fills.append({"price": price, "qty": requested_qty})

    vwap = gross / filled_qty if filled_qty > CASH_EPSILON else 0.0
    return {
        "requested_qty": requested_qty,
        "filled_qty": filled_qty,
        "gross": gross,
        "fee": fee,
        "vwap": vwap,
        "fills": fills,
        "levels_used": len(fills),
        "used_depth": used_depth,
        "depth_limited": used_depth and filled_qty + CASH_EPSILON < requested_qty,
    }
