from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

from services.event_news_service import list_events as list_news_events, list_graph_core
from services.polymarket_service import list_market_categories, search_markets


DEFAULT_MARKET_LIMIT = 80
MAX_MARKET_LIMIT = 180


ASSET_RULES: List[Dict[str, Any]] = [
    {
        "id": "fin_btc_price",
        "label": "BTC price",
        "finance_type": "VARIABLE",
        "venue": "binance",
        "symbol": "BTCUSDT",
        "keywords": ["btc", "bitcoin", "btcusdt"],
    },
    {
        "id": "fin_eth_price",
        "label": "ETH price",
        "finance_type": "VARIABLE",
        "venue": "binance",
        "symbol": "ETHUSDT",
        "keywords": ["eth", "ethereum", "ethusdt"],
    },
    {
        "id": "fin_sol_price",
        "label": "SOL price",
        "finance_type": "VARIABLE",
        "venue": "binance",
        "symbol": "SOLUSDT",
        "keywords": ["sol", "solana", "solusdt"],
    },
    {
        "id": "fin_crypto_beta",
        "label": "Crypto beta",
        "finance_type": "VARIABLE",
        "venue": "multi",
        "symbol": "CRYPTO",
        "keywords": ["crypto", "stablecoin", "memecoin", "token", "defi"],
    },
    {
        "id": "fin_brent_oil",
        "label": "Crude oil risk",
        "finance_type": "VARIABLE",
        "venue": "multi",
        "symbol": "BRENT/WTI",
        "keywords": ["oil", "crude", "brent", "wti", "opec", "hormuz", "iran"],
    },
    {
        "id": "fin_gold",
        "label": "Gold",
        "finance_type": "ASSET",
        "venue": "multi",
        "symbol": "XAU/GLD",
        "keywords": ["gold", "xau", "gld"],
    },
    {
        "id": "fin_us_rates",
        "label": "US rate expectations",
        "finance_type": "VARIABLE",
        "venue": "macro",
        "symbol": "US_RATES",
        "keywords": ["fed", "fomc", "rate", "rates", "inflation", "cpi", "treasury", "yield"],
    },
    {
        "id": "fin_us_equity_index",
        "label": "US equity index",
        "finance_type": "VARIABLE",
        "venue": "equity",
        "symbol": "SPY/QQQ",
        "keywords": ["s&p", "spx", "spy", "nasdaq", "qqq", "stock market"],
    },
    {
        "id": "fin_nvda",
        "label": "NVDA",
        "finance_type": "ASSET",
        "venue": "equity",
        "symbol": "NVDA",
        "keywords": ["nvda", "nvidia"],
    },
    {
        "id": "fin_tsla",
        "label": "TSLA",
        "finance_type": "ASSET",
        "venue": "equity",
        "symbol": "TSLA",
        "keywords": ["tsla", "tesla", "musk"],
    },
    {
        "id": "fin_aapl",
        "label": "AAPL",
        "finance_type": "ASSET",
        "venue": "equity",
        "symbol": "AAPL",
        "keywords": ["aapl", "apple"],
    },
    {
        "id": "fin_geopolitical_risk",
        "label": "Geopolitical risk",
        "finance_type": "VARIABLE",
        "venue": "macro",
        "symbol": "GEO_RISK",
        "keywords": ["war", "ceasefire", "russia", "ukraine", "china", "taiwan", "israel", "iran", "nato"],
    },
    {
        "id": "fin_us_election_risk",
        "label": "US election risk",
        "finance_type": "VARIABLE",
        "venue": "macro",
        "symbol": "US_ELECTION",
        "keywords": ["trump", "biden", "president", "election", "senate", "congress"],
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_id(prefix: str, value: str, size: int = 14) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:size]
    return f"{prefix}_{digest}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    return max(min_value, min(result, max_value))


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(value: Any, limit: int = 520) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _title_from_slug(value: str) -> str:
    text = re.sub(r"[-_]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    keep_upper = {"btc", "eth", "sol", "us", "usa", "uk", "ai", "nfl", "nba", "nhl", "mlb", "fifa"}
    words = []
    for part in text.split(" "):
        lower = part.lower()
        words.append(lower.upper() if lower in keep_upper else lower.capitalize())
    return " ".join(words)


def _normalize_event_phrase(question: str) -> str:
    text = str(question or "").strip()
    text = re.sub(r"^\s*will\s+", "", text, flags=re.I)
    text = re.sub(r"\?$", "", text).strip()
    return text or str(question or "Untitled event").strip() or "Untitled event"


def _normalize_key(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _market_group_key(market: Dict[str, Any]) -> Tuple[str, str, str]:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    event_slug = str(market.get("event_slug") or raw.get("eventSlug") or raw.get("event_slug") or "").strip()
    if event_slug:
        return f"event_slug:{event_slug}", _title_from_slug(event_slug), "POLYMARKET_EVENT"

    event_id = str(market.get("EventID") or raw.get("EventID") or raw.get("eventId") or raw.get("event_id") or "").strip()
    if event_id:
        title = _title_from_slug(str(raw.get("slug") or market.get("slug") or "")) or _normalize_event_phrase(market.get("question") or "")
        return f"event_id:{event_id}", title, "POLYMARKET_EVENT_ID"

    question = str(market.get("question") or raw.get("question") or "").strip()
    normalized = _normalize_key(_normalize_event_phrase(question))
    tokens = normalized.split(" ")[:14]
    key = "question:" + " ".join(tokens)
    return key, _normalize_event_phrase(question), "QUESTION_DERIVED"


def _market_heat(market: Dict[str, Any]) -> Dict[str, float]:
    volume_24h = _safe_float(market.get("volume_24h") or market.get("volume24hr"))
    volume_total = _safe_float(market.get("volume") or market.get("volumeNum"))
    liquidity = _safe_float(market.get("liquidity") or market.get("liquidity_clob") or market.get("liquidityClob"))
    price_change = abs(_safe_float(market.get("price_change_24h") or market.get("oneDayPriceChange")))
    spread = _safe_float(market.get("spread"))
    active_bonus = 8.0 if market.get("active", True) and not market.get("closed", False) else 0.0

    volume_component = min(42.0, math.log1p(volume_24h) / math.log1p(5_000_000) * 42.0) if volume_24h else 0.0
    total_component = min(16.0, math.log1p(volume_total) / math.log1p(80_000_000) * 16.0) if volume_total else 0.0
    liquidity_component = min(24.0, math.log1p(liquidity) / math.log1p(10_000_000) * 24.0) if liquidity else 0.0
    price_component = min(16.0, price_change * 180.0)
    spread_penalty = min(10.0, max(0.0, spread - 0.02) * 160.0) if spread else 0.0
    heat = max(1.0, min(100.0, volume_component + total_component + liquidity_component + price_component + active_bonus - spread_penalty))
    return {
        "heat": round(heat, 1),
        "volume_24h": volume_24h,
        "volume": volume_total,
        "liquidity": liquidity,
        "price_change_24h": price_change,
        "spread": spread,
    }


def _strength_from_heat(heat: float) -> str:
    if heat >= 72:
        return "HIGH"
    if heat >= 38:
        return "MEDIUM"
    return "LOW"


def _search_text(market: Dict[str, Any]) -> str:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    parts = [
        market.get("question"),
        market.get("category"),
        market.get("rules"),
        market.get("resolution_source"),
        raw.get("description"),
        raw.get("rules"),
        raw.get("resolutionSource"),
        raw.get("slug"),
        market.get("event_slug"),
    ]
    return _normalize_key(" ".join(str(part or "") for part in parts))


def _matched_assets(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    haystack = f" {_search_text(market)} "
    matches = []
    for rule in ASSET_RULES:
        for keyword in rule["keywords"]:
            token = _normalize_key(keyword)
            if not token:
                continue
            if f" {token} " in haystack or token in haystack:
                matches.append(rule)
                break
    return matches[:4]


def _node(
    node_id: str,
    *,
    node_type: str,
    label: str,
    heat: float = 1.0,
    subtitle: str = "",
    status: str = "ACTIVE",
    verification_status: str = "SYSTEM_DERIVED",
    source_type: str = "SYSTEM",
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "subtitle": subtitle,
        "heat": round(float(heat or 1.0), 1),
        "status": status,
        "verification_status": verification_status,
        "source_type": source_type,
        "details": details or {},
    }


def _edge(
    source: str,
    target: str,
    relation_type: str,
    *,
    relation_class: str,
    confidence: float = 0.65,
    strength: str = "MEDIUM",
    reason: str = "",
) -> Dict[str, Any]:
    edge_id = _hash_id("edge", f"{source}|{target}|{relation_type}", 16)
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation_type": relation_type,
        "relation_class": relation_class,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "strength": strength,
        "reason": reason,
        "verification_status": "SYSTEM_DERIVED",
        "source_type": "EVENT_GRAPH_PREVIEW",
    }


def _merge_unique_edges(edges: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for edge in edges:
        merged[edge["id"]] = edge
    return list(merged.values())


def get_event_graph_categories(limit: int = 120) -> List[Dict[str, Any]]:
    return list_market_categories(limit=limit)


def _add_news_event_nodes(
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    query: str,
    params: Dict[str, Any],
) -> int:
    if not _truthy(params.get("include_news"), True):
        return 0
    news_limit = _safe_int(params.get("news_limit"), 40, 0, 120)
    if news_limit <= 0:
        return 0
    try:
        events = list_news_events(q=query, limit=news_limit, include_observations=True)
    except Exception:
        return 0
    added = 0
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        heat = _safe_float(event.get("heat"), 1.0)
        observations = event.get("observations") if isinstance(event.get("observations"), list) else []
        source_count = _safe_int(event.get("source_count"), 0, 0, 10000)
        observation_count = _safe_int(event.get("observation_count"), len(observations), 0, 10000)
        nodes[event_id] = _node(
            event_id,
            node_type="EVENT",
            label=_clip(event.get("title") or "News event", 90),
            subtitle=f"News · {source_count} sources · {observation_count} observations",
            heat=heat,
            verification_status=str(event.get("verification_status") or "SYSTEM_DERIVED"),
            source_type=str(event.get("source_type") or "NEWS_EVENT_DB"),
            details={
                "event_type": "NEWS_CANONICAL",
                "canonical_key": event.get("canonical_key") or "",
                "summary": event.get("summary") or "",
                "source_count": source_count,
                "observation_count": observation_count,
                "first_seen_utc": event.get("first_seen_utc") or "",
                "last_seen_utc": event.get("last_seen_utc") or "",
                "updated_at_utc": event.get("updated_at_utc") or "",
                "observations": [
                    {
                        "observation_id": item.get("observation_id") or "",
                        "source": item.get("source_label") or item.get("source_id") or "",
                        "title": item.get("clean_title") or item.get("title") or "",
                        "url": item.get("url") or "",
                        "published_at_utc": item.get("published_at_utc") or "",
                        "heat": item.get("heat") or 1.0,
                    }
                    for item in observations[:8]
                    if isinstance(item, dict)
                ],
            },
        )
        signal_id = _hash_id("sig_news", event_id, 14)
        nodes[signal_id] = _node(
            signal_id,
            node_type="SIGNAL",
            label="News heat signal",
            subtitle=f"{observation_count} news observations",
            heat=heat,
            verification_status="SYSTEM_DERIVED",
            source_type="NEWS_RSS",
            details={
                "signal_type": "NEWS_HEAT",
                "event_id": event_id,
                "metrics": {
                    "heat": round(heat, 1),
                    "source_count": source_count,
                    "observation_count": observation_count,
                },
            },
        )
        edges.append(
            _edge(
                signal_id,
                event_id,
                "REPORTED_BY",
                relation_class="EVIDENCE",
                confidence=0.72,
                strength=_strength_from_heat(heat),
                reason="News observations are grouped into this derived event.",
            )
        )
        added += 1
    return added


def _add_core_graph_nodes(
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    query: str,
    params: Dict[str, Any],
) -> int:
    if not _truthy(params.get("include_core"), True):
        return 0
    core_limit = _safe_int(params.get("core_limit"), 80, 0, 240)
    if core_limit <= 0:
        return 0
    try:
        core = list_graph_core(q=query, limit=core_limit)
    except Exception:
        return 0
    added = 0
    def core_node_type(value: Any) -> str:
        raw = str(value or "").strip().upper()
        if raw == "FINANCE":
            return "FINANCE"
        if raw == "SIGNAL":
            return "SIGNAL"
        return "EVENT"

    for event in core.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        heat = _safe_float(payload.get("heat"), 56.0)
        nodes[event_id] = _node(
            event_id,
            node_type="EVENT",
            label=_clip(event.get("title") or event_id, 90),
            subtitle=f"Graph Core v{event.get('current_version') or 1}",
            heat=heat,
            status=str(event.get("lifecycle") or "ACTIVE"),
            verification_status=str(event.get("verification_status") or "HUMAN_VERIFIED"),
            source_type="GRAPH_CORE",
            details={
                "event_type": event.get("event_type") or "ATOMIC",
                "summary": event.get("summary") or "",
                "time_window_start": event.get("time_window_start") or "",
                "time_window_end": event.get("time_window_end") or "",
                "origin_request_id": event.get("origin_request_id") or "",
                "origin_run_id": event.get("origin_run_id") or "",
                "current_version": event.get("current_version") or 1,
                "updated_at_utc": event.get("updated_at_utc") or "",
                "payload": payload,
            },
        )
        added += 1

    for finance in core.get("finance_nodes") or []:
        if not isinstance(finance, dict):
            continue
        finance_id = str(finance.get("finance_id") or "").strip()
        if not finance_id:
            continue
        payload = finance.get("payload") if isinstance(finance.get("payload"), dict) else {}
        heat = _safe_float(payload.get("heat"), 38.0)
        nodes[finance_id] = _node(
            finance_id,
            node_type="FINANCE",
            label=_clip(finance.get("label") or finance.get("symbol") or finance_id, 90),
            subtitle=" · ".join([part for part in [finance.get("finance_type"), finance.get("venue"), finance.get("symbol")] if part]),
            heat=heat,
            status=str(finance.get("lifecycle") or "ACTIVE"),
            verification_status=str(finance.get("verification_status") or "HUMAN_VERIFIED"),
            source_type="GRAPH_CORE",
            details={
                "finance_type": finance.get("finance_type") or "",
                "venue": finance.get("venue") or "",
                "symbol": finance.get("symbol") or "",
                "summary": finance.get("summary") or "",
                "origin_request_id": finance.get("origin_request_id") or "",
                "origin_run_id": finance.get("origin_run_id") or "",
                "current_version": finance.get("current_version") or 1,
                "updated_at_utc": finance.get("updated_at_utc") or "",
                "payload": payload,
            },
        )
        added += 1

    for expression in core.get("expressions") or []:
        if not isinstance(expression, dict):
            continue
        expression_id = str(expression.get("expression_id") or "").strip()
        if not expression_id:
            continue
        expression_body = expression.get("expression") if isinstance(expression.get("expression"), dict) else {}
        payload = expression.get("payload") if isinstance(expression.get("payload"), dict) else {}
        nodes[expression_id] = _node(
            expression_id,
            node_type="SIGNAL",
            label=_clip(expression.get("label") or expression_id, 90),
            subtitle=f"Expression · {expression.get('expression_type') or 'RULE'}",
            heat=_safe_float(payload.get("heat"), 34.0),
            status=str(expression.get("lifecycle") or "ACTIVE"),
            verification_status=str(expression.get("verification_status") or "HUMAN_VERIFIED"),
            source_type="GRAPH_CORE",
            details={
                "signal_type": "GRAPH_EXPRESSION",
                "summary": expression.get("summary") or "",
                "expression_type": expression.get("expression_type") or "",
                "language": expression.get("language") or "",
                "expression": expression_body,
                "origin_request_id": expression.get("origin_request_id") or "",
                "origin_run_id": expression.get("origin_run_id") or "",
                "current_version": expression.get("current_version") or 1,
                "updated_at_utc": expression.get("updated_at_utc") or "",
                "payload": payload,
            },
        )
        added += 1
        refs = expression_body.get("target_refs") if isinstance(expression_body.get("target_refs"), list) else []
        inputs = expression_body.get("inputs") if isinstance(expression_body.get("inputs"), list) else []
        for ref in [*refs, *inputs][:12]:
            if isinstance(ref, dict):
                ref_id = str(ref.get("id") or ref.get("event_id") or ref.get("finance_id") or ref.get("node_id") or "").strip()
                ref_type = str(ref.get("type") or "").strip().lower()
            else:
                ref_id = str(ref or "").strip()
                ref_type = ""
            if not ref_id:
                continue
            if ref_id not in nodes:
                nodes[ref_id] = _node(
                    ref_id,
                    node_type=core_node_type(ref_type),
                    label=ref_id,
                    subtitle="Expression reference",
                    heat=14,
                    verification_status="REFERENCE_ONLY",
                    source_type="GRAPH_CORE",
                )
            edges.append(
                _edge(
                    expression_id,
                    ref_id,
                    "ASSOCIATED",
                    relation_class="IMPACT",
                    confidence=0.55,
                    strength="MEDIUM",
                    reason="Graph Core expression references this object.",
                )
            )

    for edge in core.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source_id") or "").strip()
        target = str(edge.get("target_id") or "").strip()
        if not source or not target:
            continue
        if source not in nodes:
            nodes[source] = _node(
                source,
                node_type=core_node_type(edge.get("source_type")),
                label=source,
                subtitle="Graph Core endpoint",
                heat=16,
                verification_status="REFERENCE_ONLY",
                source_type="GRAPH_CORE",
            )
        if target not in nodes:
            nodes[target] = _node(
                target,
                node_type=core_node_type(edge.get("target_type")),
                label=target,
                subtitle="Graph Core endpoint",
                heat=16,
                verification_status="REFERENCE_ONLY",
                source_type="GRAPH_CORE",
            )
        edges.append(
            {
                "id": edge.get("edge_id") or _hash_id("edge", f"{source}|{target}|{edge.get('relation_type') or ''}", 16),
                "source": source,
                "target": target,
                "relation_type": edge.get("relation_type") or "ASSOCIATED",
                "relation_class": edge.get("relation_class") or "IMPACT",
                "confidence": round(max(0.0, min(1.0, _safe_float(edge.get("confidence"), 0.65))), 2),
                "strength": edge.get("strength") or "MEDIUM",
                "reason": edge.get("reason") or edge.get("mechanism") or "",
                "verification_status": edge.get("verification_status") or "HUMAN_VERIFIED",
                "source_type": edge.get("source_kind") or "GRAPH_CORE",
            }
        )
        added += 1
    return added


def build_event_graph(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = params or {}
    query = str(params.get("q") or params.get("query") or "").strip()
    category = str(params.get("category") or "").strip()
    sort = str(params.get("sort") or "volume24h").strip() or "volume24h"
    order = str(params.get("order") or "desc").strip() or "desc"
    limit = _safe_int(params.get("limit"), DEFAULT_MARKET_LIMIT, 10, MAX_MARKET_LIMIT)

    markets = search_markets(
        query=query,
        category=category,
        limit=limit,
        force_refresh=str(params.get("refresh") or "").lower() in {"1", "true", "yes", "on"},
        sort_by=sort,
        sort_dir=order,
    )

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    event_buckets: Dict[str, Dict[str, Any]] = {}
    market_count = 0

    for market in markets:
        condition_id = str(market.get("condition_id") or "").strip()
        if not condition_id:
            continue
        market_count += 1
        heat_info = _market_heat(market)
        market_heat = heat_info["heat"]
        group_key, event_label, key_source = _market_group_key(market)
        event_id = _hash_id("evt", group_key)
        bucket = event_buckets.setdefault(
            event_id,
            {
                "id": event_id,
                "group_key": group_key,
                "label": event_label,
                "key_source": key_source,
                "markets": [],
                "heat": 0.0,
                "volume_24h": 0.0,
                "volume": 0.0,
                "liquidity": 0.0,
                "categories": set(),
                "assets": {},
                "rules": "",
            },
        )
        bucket["markets"].append(market)
        bucket["heat"] = max(float(bucket["heat"]), market_heat)
        bucket["volume_24h"] += heat_info["volume_24h"]
        bucket["volume"] += heat_info["volume"]
        bucket["liquidity"] += heat_info["liquidity"]
        category_text = str(market.get("category") or "").strip()
        if category_text:
            bucket["categories"].add(category_text)
        if not bucket["rules"] and market.get("rules"):
            bucket["rules"] = _clip(market.get("rules"), 900)

        finance_id = _hash_id("fin_pm", condition_id, 16)
        finance_label = str(market.get("group_item_title") or market.get("question") or condition_id).strip()
        nodes[finance_id] = _node(
            finance_id,
            node_type="FINANCE",
            label=_clip(finance_label, 90),
            subtitle="Polymarket market",
            heat=market_heat,
            status="ACTIVE" if market.get("active", True) and not market.get("closed", False) else "ARCHIVED",
            verification_status="AUTO_COLLECTED",
            source_type="POLYMARKET",
            details={
                "finance_type": "MARKET",
                "venue": "polymarket",
                "condition_id": condition_id,
                "yes_token": market.get("yes_token") or "",
                "no_token": market.get("no_token") or "",
                "question": market.get("question") or "",
                "category": market.get("category") or "",
                "url": market.get("url") or "",
                "end_date": market.get("end_date") or "",
                "metrics": heat_info,
                "rules": _clip(market.get("rules"), 900),
            },
        )
        edges.append(
            _edge(
                finance_id,
                event_id,
                "DIRECTLY_PRICES",
                relation_class="MAPPING",
                confidence=0.82,
                strength=_strength_from_heat(market_heat),
                reason="Polymarket market condition is mapped to the derived event bucket.",
            )
        )

        for asset in _matched_assets(market):
            bucket["assets"][asset["id"]] = asset

    for event_id, bucket in event_buckets.items():
        market_total = len(bucket["markets"])
        heat = min(100.0, float(bucket["heat"]) + min(12.0, math.log1p(market_total) * 3.5))
        categories = sorted(bucket["categories"])
        top_markets = sorted(bucket["markets"], key=lambda item: _market_heat(item)["heat"], reverse=True)[:8]
        nodes[event_id] = _node(
            event_id,
            node_type="EVENT",
            label=_clip(bucket["label"], 82),
            subtitle=", ".join(categories[:3]) or "Derived canonical event",
            heat=heat,
            verification_status="SYSTEM_DERIVED",
            source_type=bucket["key_source"],
            details={
                "event_type": "CANONICAL",
                "group_key": bucket["group_key"],
                "market_count": market_total,
                "categories": categories,
                "heat_metrics": {
                    "heat": round(heat, 1),
                    "volume_24h": round(bucket["volume_24h"], 2),
                    "volume": round(bucket["volume"], 2),
                    "liquidity": round(bucket["liquidity"], 2),
                },
                "rules_sample": bucket["rules"],
                "top_markets": [
                    {
                        "condition_id": market.get("condition_id") or "",
                        "question": market.get("question") or "",
                        "url": market.get("url") or "",
                        "heat": _market_heat(market)["heat"],
                        "volume_24h": _market_heat(market)["volume_24h"],
                        "liquidity": _market_heat(market)["liquidity"],
                        "end_date": market.get("end_date") or "",
                    }
                    for market in top_markets
                ],
            },
        )

        signal_id = _hash_id("sig_heat", event_id, 14)
        nodes[signal_id] = _node(
            signal_id,
            node_type="SIGNAL",
            label="Market heat signal",
            subtitle=f"{market_total} market observations",
            heat=heat,
            verification_status="SYSTEM_DERIVED",
            source_type="POLYMARKET_METRICS",
            details={
                "signal_type": "MARKET_HEAT",
                "event_id": event_id,
                "metrics": nodes[event_id]["details"]["heat_metrics"],
            },
        )
        edges.append(
            _edge(
                signal_id,
                event_id,
                "ASSOCIATED",
                relation_class="IMPACT",
                confidence=0.7,
                strength=_strength_from_heat(heat),
                reason="Aggregated market volume, liquidity, and price movement contribute to event heat.",
            )
        )

        for asset_id, asset in bucket["assets"].items():
            if asset_id not in nodes:
                nodes[asset_id] = _node(
                    asset_id,
                    node_type="FINANCE",
                    label=asset["label"],
                    subtitle=f"{asset['finance_type']} · {asset.get('symbol') or ''}",
                    heat=max(20.0, heat * 0.55),
                    verification_status="SYSTEM_DERIVED",
                    source_type="KEYWORD_MAPPING",
                    details={
                        "finance_type": asset["finance_type"],
                        "venue": asset.get("venue") or "",
                        "symbol": asset.get("symbol") or "",
                        "mapping": "keyword",
                    },
                )
            else:
                nodes[asset_id]["heat"] = max(float(nodes[asset_id].get("heat") or 1), round(heat * 0.55, 1))
            edges.append(
                _edge(
                    event_id,
                    asset_id,
                    "ASSOCIATED",
                    relation_class="IMPACT",
                    confidence=0.55,
                    strength=_strength_from_heat(heat * 0.7),
                    reason="Keyword-derived asset association from market question or resolution rules.",
                )
            )

    core_objects = _add_core_graph_nodes(nodes, edges, query=query, params=params)
    _add_news_event_nodes(nodes, edges, query=query, params=params)

    node_list = sorted(nodes.values(), key=lambda item: (item["type"] != "EVENT", -float(item.get("heat") or 0), item["label"]))
    edge_list = _merge_unique_edges(edges)
    event_nodes = [node for node in node_list if node["type"] == "EVENT"]
    finance_nodes = [node for node in node_list if node["type"] == "FINANCE"]
    signal_nodes = [node for node in node_list if node["type"] == "SIGNAL"]
    max_heat = max([float(node.get("heat") or 0) for node in node_list] or [0.0])
    news_events = sum(1 for node in event_nodes if node.get("source_type") == "NEWS_EVENT_DB" or str(node.get("id") or "").startswith("news_evt_"))

    return {
        "ok": True,
        "source": "derived_preview",
        "generated_at": _now_iso(),
        "query": {
            "q": query,
            "category": category,
            "sort": sort,
            "order": order,
            "limit": limit,
        },
        "summary": {
            "events": len(event_nodes),
            "news_events": news_events,
            "finance_nodes": len(finance_nodes),
            "signals": len(signal_nodes),
            "edges": len(edge_list),
            "markets": market_count,
            "core_objects": core_objects,
            "max_heat": round(max_heat, 1),
        },
        "nodes": node_list,
        "edges": edge_list,
        "event_rankings": event_nodes[:30],
    }
