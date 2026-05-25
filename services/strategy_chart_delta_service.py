from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from services.polymarket_service import get_strategy_chart_capabilities, get_strategy_chart_defaults
from services.strategy_chart_service import (
    _allowed_overlay_symbols,
    _bucket_ts,
    _detail_sample,
    _derive_stats_from_price_samples,
    _load_crypto_overlay_samples,
    _load_finance_overlay_samples,
    _load_metric_numeric_samples,
    _load_price_samples,
    _load_stats_samples,
    _load_strategy_tick_price_samples,
    _merge_samples,
    _overlay_sample_maps,
    _parse_iso,
    _parse_seconds,
    _prefix_sample_map,
    _apply_virtual_account_pnl_to_rows,
    _resolve_chart_detail,
    _resolve_time_bounds,
    _select_sample_keys,
    _selected_overlay_fields,
    _selected_metric_keys,
    _sync_row_pnl_to_visible_prices,
    _sub_series_debug,
    _requested_sub_series,
    _watch_market_sample_map,
)
from services.strategy_event_service import list_strategy_events


_STREAM_KEYS = {"price", "stats", "metrics", "watch_markets", "overlay", "events"}
_PRICE_KEYS = {"yes_bid", "yes_ask", "no_bid", "no_ask", "yes_last_price", "no_last_price", "yes_mid", "no_mid"}
_STAT_KEYS = {
    "yes_position",
    "no_position",
    "yes_qty",
    "no_qty",
    "yes_avg",
    "no_avg",
    "strategy_pnl",
    "strategy_bankroll",
    "initial_capital",
    "profit_roll_ratio",
    "realized_profit",
}

# Short-lived process-level cache for _resolve_chart_detail.
# Delta requests arrive every 2s; the heavy detail fetch only needs to run
# once per TTL window instead of on every single request.
_DETAIL_CACHE_TTL = 30.0
_detail_cache: Dict[str, Any] = {}
_detail_cache_lock = threading.Lock()


def _cached_resolve_chart_detail(row_id: int, args: Dict[str, Any]):
    """Thin wrapper around _resolve_chart_detail with a short TTL cache."""
    cache_key = str(row_id)
    now = time.monotonic()
    with _detail_cache_lock:
        entry = _detail_cache.get(cache_key)
        if entry and (now - entry["ts"]) < _DETAIL_CACHE_TTL:
            return entry["detail"], entry["market_targets"], entry["target_meta"]
    detail, market_targets, target_meta = _resolve_chart_detail(row_id, args)
    with _detail_cache_lock:
        _detail_cache[cache_key] = {
            "ts": now,
            "detail": detail,
            "market_targets": market_targets,
            "target_meta": target_meta,
        }
    return detail, market_targets, target_meta


def _requested_streams(args: Dict[str, Any]) -> List[str]:
    raw = str(args.get("streams") or "").strip().lower()
    if not raw:
        return ["price", "stats", "watch_markets", "overlay"]
    output: List[str] = []
    for item in raw.split(","):
        key = str(item or "").strip().lower()
        if key in _STREAM_KEYS and key not in output:
            output.append(key)
    return output or ["price"]


def _cursor_key(stream_name: str) -> str:
    return f"cursor_{stream_name}"


def _query_from_ts(from_ts: str, cursor_ts: str, interval_seconds: int) -> str:
    cursor_text = str(cursor_ts or "").strip()
    if not cursor_text:
        return from_ts
    bucket = _bucket_ts(cursor_text, interval_seconds)
    from_dt = _parse_iso(from_ts)
    bucket_dt = _parse_iso(bucket)
    if from_dt is not None and bucket_dt is not None and bucket_dt < from_dt:
        return from_ts
    return bucket or from_ts


def _build_stream_payload(rows: List[Dict[str, Any]], cursor_ts: str) -> Dict[str, Any]:
    cursor_text = str(cursor_ts or "").strip()
    patched_last_bucket = False
    if cursor_text and rows:
        cursor_bucket = _bucket_ts(cursor_text, 1)
        patched_last_bucket = any(
            str(row.get("ts") or "") == cursor_bucket
            for row in rows
        )
    next_cursor = cursor_text
    if rows:
        next_cursor = str(rows[-1].get("ts") or cursor_text)
    return {
        "points": rows,
        "patched_last_bucket": patched_last_bucket,
        "next_cursor": next_cursor,
    }


def _build_price_delta(detail: Dict[str, Any], from_ts: str, to_ts: str, interval_seconds: int) -> List[Dict[str, Any]]:
    price_samples = _overlay_sample_maps(
        _load_strategy_tick_price_samples(detail, from_ts, to_ts, interval_seconds),
        _load_price_samples(detail, from_ts, to_ts, interval_seconds),
    )
    return _merge_samples(
        _prefix_sample_map(
            _select_sample_keys(price_samples, _PRICE_KEYS),
            "market_0_",
        ),
        _prefix_sample_map(
            _select_sample_keys(_detail_sample(detail, interval_seconds, from_ts, to_ts), _PRICE_KEYS),
            "market_0_",
        ),
    )


def _build_stats_delta(detail: Dict[str, Any], from_ts: str, to_ts: str, interval_seconds: int) -> List[Dict[str, Any]]:
    stats_samples = _load_stats_samples(detail, from_ts, to_ts, interval_seconds)
    raw_price_samples = _overlay_sample_maps(
        _load_strategy_tick_price_samples(detail, from_ts, to_ts, interval_seconds),
        _load_price_samples(detail, from_ts, to_ts, interval_seconds),
    )
    price_samples = _prefix_sample_map(
        _select_sample_keys(raw_price_samples, _PRICE_KEYS),
        "market_0_",
    )
    if not stats_samples:
        stats_samples = _derive_stats_from_price_samples(detail, price_samples)
    rows = _merge_samples(
        price_samples,
        stats_samples,
        _prefix_sample_map(
            _select_sample_keys(_detail_sample(detail, interval_seconds, from_ts, to_ts), _PRICE_KEYS),
            "market_0_",
        ),
        _select_sample_keys(_detail_sample(detail, interval_seconds, from_ts, to_ts), _STAT_KEYS),
    )
    _sync_row_pnl_to_visible_prices(rows)
    _apply_virtual_account_pnl_to_rows(detail, rows)
    aligned_price_keys = {f"market_0_{key}" for key in _PRICE_KEYS}
    aligned_output_keys = _STAT_KEYS | aligned_price_keys
    return [
        {"ts": row["ts"], **{key: row[key] for key in aligned_output_keys if key in row}}
        for row in rows
        if any(key in row for key in _STAT_KEYS)
    ]


def _build_metrics_delta(
    detail: Dict[str, Any],
    args: Dict[str, Any],
    defaults: Dict[str, Any],
    capabilities: Dict[str, Any],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> List[Dict[str, Any]]:
    selected_series = _requested_sub_series(args, defaults, capabilities)
    metric_keys, _state_keys = _selected_metric_keys(selected_series)
    return _merge_samples(_load_metric_numeric_samples(detail, metric_keys, from_ts, to_ts, interval_seconds))


def _build_watch_delta(
    market_targets: List[Dict[str, Any]],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
    main_side: str,
) -> List[Dict[str, Any]]:
    sample_maps: List[Dict[str, Dict[str, Any]]] = []
    for index, target in enumerate(market_targets[1:], start=1):
        prefix = f"market_{index}_"
        detail = target["detail"]
        sample_maps.append(_watch_market_sample_map(_load_price_samples(detail, from_ts, to_ts, interval_seconds), prefix, main_side))
        sample_maps.append(_watch_market_sample_map(_detail_sample(detail, interval_seconds, from_ts, to_ts), prefix, main_side))
    return _merge_samples(*sample_maps) if sample_maps else []


def _build_overlay_delta(
    args: Dict[str, Any],
    defaults: Dict[str, Any],
    capabilities: Dict[str, Any],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> List[Dict[str, Any]]:
    overlay_crypto = _allowed_overlay_symbols(args, capabilities, "overlay_crypto")
    overlay_finance = _allowed_overlay_symbols(args, capabilities, "overlay_finance")
    overlay_crypto_fields = _selected_overlay_fields(args, defaults, capabilities, "crypto")
    overlay_finance_fields = _selected_overlay_fields(args, defaults, capabilities, "finance")
    return _merge_samples(
        _load_crypto_overlay_samples(from_ts, to_ts, interval_seconds, overlay_crypto, overlay_crypto_fields),
        _load_finance_overlay_samples(from_ts, to_ts, interval_seconds, overlay_finance, overlay_finance_fields),
    )


def get_strategy_chart_delta(row_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    detail, market_targets, target_meta = _cached_resolve_chart_detail(row_id, args)
    defaults = get_strategy_chart_defaults(detail)
    capabilities = get_strategy_chart_capabilities(detail)
    interval = str(args.get("interval") or defaults.get("interval") or "5s")
    interval_seconds = _parse_seconds(interval)
    from_ts, to_ts = _resolve_time_bounds(args, defaults)
    requested_streams = _requested_streams(args)
    main_side = str(args.get("main_side") or defaults.get("main_side") or "all").strip().lower()

    response: Dict[str, Any] = {
        "meta": {
            "strategy_row_id": row_id,
            "from": from_ts,
            "to": to_ts,
            "interval": interval,
            "target": target_meta,
            "streams": requested_streams,
            "debug": {
                **_sub_series_debug(args, defaults, capabilities),
                "strategy_detail_row_id": detail.get("row_id"),
                "market_target_count": len(market_targets),
            },
        },
        "reload_required": False,
    }

    if "price" in requested_streams and market_targets:
        cursor = str(args.get(_cursor_key("price")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        rows = _build_price_delta(market_targets[0]["detail"], query_from, to_ts, interval_seconds)
        response["price"] = _build_stream_payload(rows, cursor)

    if "stats" in requested_streams:
        cursor = str(args.get(_cursor_key("stats")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        rows = _build_stats_delta(detail, query_from, to_ts, interval_seconds)
        response["stats"] = _build_stream_payload(rows, cursor)

    if "metrics" in requested_streams:
        _metric_keys, state_metric_keys = _selected_metric_keys(_requested_sub_series(args, defaults, capabilities))
        if state_metric_keys:
            response["reload_required"] = True
            response["reload_reason"] = "state-metrics-require-full-render"
            return response
        cursor = str(args.get(_cursor_key("metrics")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        rows = _build_metrics_delta(detail, args, defaults, capabilities, query_from, to_ts, interval_seconds)
        response["metrics"] = _build_stream_payload(rows, cursor)

    if "watch_markets" in requested_streams:
        cursor = str(args.get(_cursor_key("watch_markets")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        rows = _build_watch_delta(market_targets, query_from, to_ts, interval_seconds, main_side)
        response["watch_markets"] = _build_stream_payload(rows, cursor)

    if "overlay" in requested_streams:
        cursor = str(args.get(_cursor_key("overlay")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        rows = _build_overlay_delta(args, defaults, capabilities, query_from, to_ts, interval_seconds)
        response["overlay"] = _build_stream_payload(rows, cursor)

    if "events" in requested_streams:
        cursor = str(args.get(_cursor_key("events")) or "").strip()
        query_from = _query_from_ts(from_ts, cursor, interval_seconds)
        events_payload = list_strategy_events(row_id, {"limit": 30, "from": query_from, "to": to_ts}, detail=detail)
        event_rows = events_payload.get("data") or []
        next_cursor = cursor
        if event_rows:
            sorted_ts = sorted(
                [str(item.get("ts") or "") for item in event_rows if str(item.get("ts") or "").strip()],
                key=lambda item: _parse_iso(item) or _parse_iso(cursor) or _parse_iso(to_ts),
            )
            next_cursor = sorted_ts[-1] if sorted_ts else cursor
        response["events"] = {
            "items": event_rows,
            "next_cursor": next_cursor,
        }

    dt = (time.perf_counter() - t0) * 1000
    print(f"[SV][chart-delta] row_id={row_id} streams={requested_streams} {dt:.1f}ms")
    return response
