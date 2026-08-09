from __future__ import annotations

from typing import Any, Dict


def is_opaque_market_identifier(value: Any) -> bool:
    """Return True for condition/token identifiers that should not be UI labels."""
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("0x") and len(text) >= 18:
        return all(char in "0123456789abcdef" for char in lowered[2:])
    return len(text) >= 20 and text.isdigit()


def preferred_leg_display_name(
    leg: Dict[str, Any] | None,
    snapshot: Dict[str, Any] | None = None,
    *,
    fallback: str = "",
) -> str:
    """Prefer a Polymarket question or asset name over opaque identifiers."""
    leg = leg or {}
    snapshot = snapshot or {}
    instrument = leg.get("instrument_json") if isinstance(leg.get("instrument_json"), dict) else {}
    asset_class = str(leg.get("asset_class") or "").strip().lower()
    venue = str(leg.get("venue") or "").strip().lower()
    is_binary = bool(
        leg.get("condition_id")
        or leg.get("yes_token")
        or leg.get("no_token")
        or asset_class in {"polymarket_binary", "binary", "binary_market"}
        or venue == "polymarket"
    )
    candidates = (
        [
            instrument.get("question"),
            instrument.get("title"),
            leg.get("question"),
            snapshot.get("question"),
            leg.get("display_name"),
            instrument.get("name"),
            leg.get("label"),
            leg.get("symbol"),
        ]
        if is_binary
        else [
            leg.get("display_name"),
            instrument.get("name"),
            leg.get("symbol"),
            instrument.get("question"),
            leg.get("question"),
            snapshot.get("question"),
            leg.get("label"),
        ]
    )
    normalized = [str(value or "").strip() for value in candidates]
    readable = next((value for value in normalized if value and not is_opaque_market_identifier(value)), "")
    if readable:
        return readable
    return next((value for value in normalized if value), str(fallback or "").strip())
