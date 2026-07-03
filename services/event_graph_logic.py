from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


STRUCTURED_EVENT_FIELDS = [
    "subject",
    "predicate",
    "object",
    "comparator",
    "threshold",
    "unit",
    "time_window_start",
    "time_window_end",
    "jurisdiction",
    "resolution_rule",
    "resolution_source",
    "outcome_space_id",
]

LOGICAL_RELATIONS = {"EQUAL", "IMPLIES", "DISJOINT", "OVERLAP"}
IMPACT_RELATIONS = {
    "POSITIVE_IMPACT",
    "NEGATIVE_IMPACT",
    "INCREASES_PROBABILITY",
    "DECREASES_PROBABILITY",
    "ASSOCIATED",
}
CAUSAL_RELATIONS = {"CAUSES", "CONTRIBUTES_TO", "RISK_CHANNEL"}
SCENARIO_RELATIONS = {"ASSUMES", "CONDITIONAL_ON", "LEADS_TO"}
EVIDENCE_RELATIONS = {"REPORTED_BY", "SUPPORTED_BY", "CONTRADICTED_BY", "OBSERVED_IN"}
MAPPING_RELATIONS = {"DIRECTLY_PRICES", "TRACKS", "HEDGES", "EXPOSED_TO"}
MARKET_MOVE_RELATIONS = {
    "ODDS_MOVED_WITH",
    "PRICE_MOVED_WITH",
    "VOLUME_SPIKE_WITH",
    "LIQUIDITY_MOVED_WITH",
}
RELATION_CLASS_RELATIONS = {
    "LOGICAL": LOGICAL_RELATIONS,
    "IMPACT": IMPACT_RELATIONS,
    "CAUSAL": CAUSAL_RELATIONS,
    "SCENARIO": SCENARIO_RELATIONS,
    "EVIDENCE": EVIDENCE_RELATIONS,
    "MAPPING": MAPPING_RELATIONS,
    "MARKET_MOVE": MARKET_MOVE_RELATIONS,
}
REASONING_RELATION_CLASSES = {"IMPACT", "CAUSAL", "SCENARIO", "MARKET_MOVE"}
EXPRESSION_OPERATORS = {"AND", "OR", "NOT", "DIFFERENCE"}
_NUMBER_WORDS = {
    "no": 0,
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _hash_id(prefix: str, value: str, size: int = 16) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:size]
    return f"{prefix}_{digest}"


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff$.,%><=\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _count_value(value: Any, default: Optional[float] = None) -> Optional[float]:
    text = str(value or "").strip().lower()
    if text in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[text])
    return _safe_float(text, default)


def _compact_key(*parts: Any) -> str:
    text = "|".join(_normalize_text(part) for part in parts if str(part or "").strip())
    return text or "unknown"


def infer_relation_class(relation_type: Any, relation_class: Any = "") -> str:
    relation_class_text = str(relation_class or "").strip().upper()
    if relation_class_text:
        return relation_class_text
    relation_type_text = str(relation_type or "").strip().upper()
    for class_name, relation_types in RELATION_CLASS_RELATIONS.items():
        if relation_type_text in relation_types:
            return class_name
    return "IMPACT"


def relation_class_is_known(relation_class: Any) -> bool:
    return str(relation_class or "").strip().upper() in RELATION_CLASS_RELATIONS


def relation_type_is_valid(relation_class: Any, relation_type: Any) -> bool:
    relation_class_text = str(relation_class or "").strip().upper()
    relation_type_text = str(relation_type or "").strip().upper()
    if not relation_class_text or not relation_type_text:
        return True
    return relation_type_text in RELATION_CLASS_RELATIONS.get(relation_class_text, set())


def _year_window(text: str) -> Tuple[str, str]:
    clean = _normalize_text(text)
    before = re.search(r"\bbefore\s+(20\d{2})\b", clean)
    if before:
        year = int(before.group(1)) - 1
        return f"{year}-01-01", f"{year}-12-31"
    year_match = re.search(r"\b(20\d{2})\b", clean)
    if year_match:
        year = int(year_match.group(1))
        return f"{year}-01-01", f"{year}-12-31"
    return "", ""


def _threshold_interval(comparator: str, threshold: Optional[float]) -> Dict[str, Any]:
    comparator = str(comparator or "").strip()
    if threshold is None:
        return {}
    if comparator in {">", ">="}:
        return {
            "lower": threshold,
            "lower_inclusive": comparator == ">=",
            "upper": None,
            "upper_inclusive": False,
        }
    if comparator in {"<", "<="}:
        return {
            "lower": None,
            "lower_inclusive": False,
            "upper": threshold,
            "upper_inclusive": comparator == "<=",
        }
    if comparator in {"=", "==", "EXACT"}:
        return {
            "lower": threshold,
            "lower_inclusive": True,
            "upper": threshold,
            "upper_inclusive": True,
        }
    return {}


def _parse_crypto_money_threshold(match: re.Match[str], clean: str, *, dollar_group: int, amount_group: int, suffix_group: int) -> Optional[float]:
    tail = clean[match.end(): match.end() + 10].strip()
    if tail.startswith("%") or tail.startswith("percent") or tail.startswith("bps"):
        return None
    threshold = _safe_float(match.group(amount_group), None)
    if threshold is None:
        return None
    suffix = str(match.group(suffix_group) or "").lower()
    has_money_marker = bool(match.group(dollar_group) or suffix in {"k", "m"} or abs(float(threshold)) >= 1000)
    if not has_money_marker:
        return None
    if suffix == "k":
        threshold = float(threshold) * 1_000
    elif suffix == "m":
        threshold = float(threshold) * 1_000_000
    return threshold


def normalize_event_semantic(raw: Optional[Dict[str, Any]], *, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = dict(fallback or {})
    source = dict(raw or {})
    semantic: Dict[str, Any] = {}
    for field in STRUCTURED_EVENT_FIELDS:
        value = source.get(field, fallback.get(field, ""))
        semantic[field] = str(value).strip() if value is not None else ""
    threshold = _safe_float(source.get("threshold", fallback.get("threshold")), None)
    if threshold is not None:
        semantic["threshold"] = threshold
    comparator = str(semantic.get("comparator") or "").strip()
    if comparator == "==":
        comparator = "="
    semantic["comparator"] = comparator
    semantic["semantic_type"] = str(source.get("semantic_type") or fallback.get("semantic_type") or "").strip()
    semantic["source"] = str(source.get("source") or fallback.get("source") or "").strip()
    semantic["confidence"] = _safe_float(source.get("confidence", fallback.get("confidence")), None)
    if semantic["confidence"] is None:
        semantic.pop("confidence", None)

    subject = semantic.get("subject") or ""
    predicate = semantic.get("predicate") or ""
    outcome_space_id = semantic.get("outcome_space_id") or ""
    semantic["structured"] = bool(subject and predicate)
    semantic["family_key"] = _compact_key(
        subject,
        predicate,
        semantic.get("unit") or "",
        semantic.get("time_window_start") or "",
        semantic.get("time_window_end") or "",
        semantic.get("jurisdiction") or "",
    )
    if not outcome_space_id and subject and predicate in {"winner", "nominee", "champion"}:
        semantic["outcome_space_id"] = _hash_id("os", _compact_key(subject, predicate), 18)
    interval = _threshold_interval(comparator, threshold)
    if interval:
        semantic["comparison_interval"] = interval
    return semantic


def _extract_threshold_semantic(text: str) -> Dict[str, Any]:
    clean = _normalize_text(text)
    start, end = _year_window(clean)
    subject = ""
    predicate = ""
    unit = ""
    comparator = ""
    threshold: Optional[float] = None

    crypto = re.search(
        r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b.*?\b(above|over|greater than|exceed(?:s|ed)?|hit(?:s)?|reach(?:es)?)\b\s*(\$?)\s*([0-9][0-9,]*(?:\.\d+)?)\s*(k|m)?",
        clean,
    )
    if crypto:
        parsed_threshold = _parse_crypto_money_threshold(crypto, clean, dollar_group=3, amount_group=4, suffix_group=5)
        if parsed_threshold is not None:
            asset = crypto.group(1)
            subject = {"bitcoin": "BTC price", "btc": "BTC price", "ethereum": "ETH price", "eth": "ETH price", "solana": "SOL price", "sol": "SOL price"}.get(asset, asset.upper())
            predicate = "price"
            comparator = ">="
            threshold = parsed_threshold
            unit = "USD"

    crypto_down = re.search(
        r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b.*?\b(below|under|less than)\b\s*(\$?)\s*([0-9][0-9,]*(?:\.\d+)?)\s*(k|m)?",
        clean,
    )
    if crypto_down:
        parsed_threshold = _parse_crypto_money_threshold(crypto_down, clean, dollar_group=3, amount_group=4, suffix_group=5)
        if parsed_threshold is not None:
            asset = crypto_down.group(1)
            subject = {"bitcoin": "BTC price", "btc": "BTC price", "ethereum": "ETH price", "eth": "ETH price", "solana": "SOL price", "sol": "SOL price"}.get(asset, asset.upper())
            predicate = "price"
            comparator = "<="
            threshold = parsed_threshold
            unit = "USD"

    fed_cut = _fed_count_semantic(clean, text, action="cut", predicate="rate_cut_count")
    if fed_cut and not subject:
        return fed_cut

    fed_hike = _fed_count_semantic(clean, text, action="hike", predicate="rate_hike_count")
    if fed_hike and not subject:
        return fed_hike

    if not subject or not predicate:
        return {}
    return normalize_event_semantic(
        {
            "semantic_type": "threshold_event",
            "subject": subject,
            "predicate": predicate,
            "comparator": comparator,
            "threshold": threshold,
            "unit": unit,
            "time_window_start": start,
            "time_window_end": end,
            "resolution_rule": text,
            "source": "market_rule_parser",
            "confidence": 0.72,
        }
    )


def _fed_count_semantic(clean: str, original_text: str, *, action: str, predicate: str) -> Dict[str, Any]:
    action_pattern = "cuts?" if action == "cut" else "hikes?"
    if not re.search(r"\b(fed|federal reserve|fomc)\b", clean) or not re.search(rf"\b(?:rate\s+)?{action_pattern}\b|\b{action}s?\b", clean):
        return {}

    comparator = ">="
    threshold: Optional[float] = 1.0
    number_token = r"(\d+|no|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
    no_before = re.search(rf"\b(no|zero|0)\s+(?:fed|federal reserve|fomc)\s+(?:rate\s+)?{action_pattern}\b", clean)
    exact_before = re.search(rf"\b{number_token}\s+(?:fed|federal reserve|fomc)\s+(?:rate\s+)?{action_pattern}\b", clean)
    at_least = re.search(rf"\b(?:at least|>=)\s*{number_token}\s+(?:fed\s+)?(?:rate\s+)?{action_pattern}\b", clean)
    or_more = re.search(rf"\b{number_token}\s*(?:\+|or more)\s+(?:fed\s+)?(?:rate\s+)?{action_pattern}\b", clean)
    more_than = re.search(rf"\bmore than\s*{number_token}\s+(?:fed\s+)?(?:rate\s+)?{action_pattern}\b", clean)
    fed_then_number = re.search(rf"\b(?:fed|federal reserve|fomc)\b.*?\b(?:at least|>=)\s*{number_token}\b.*?\b(?:rate\s+)?{action_pattern}\b", clean)

    if no_before:
        comparator = "="
        threshold = 0.0
    elif at_least:
        comparator = ">="
        threshold = _count_value(at_least.group(1), 1.0)
    elif or_more:
        comparator = ">="
        threshold = _count_value(or_more.group(1), 1.0)
    elif more_than:
        comparator = ">"
        threshold = _count_value(more_than.group(1), 1.0)
    elif fed_then_number:
        comparator = ">="
        threshold = _count_value(fed_then_number.group(1), 1.0)
    elif exact_before:
        comparator = "="
        threshold = _count_value(exact_before.group(1), 1.0)

    start, end = _year_window(clean)
    return normalize_event_semantic(
        {
            "semantic_type": "threshold_event",
            "subject": "Federal Reserve",
            "predicate": predicate,
            "comparator": comparator,
            "threshold": threshold,
            "unit": "count",
            "time_window_start": start,
            "time_window_end": end,
            "resolution_rule": original_text,
            "outcome_space_id": _hash_id("os", _compact_key("Federal Reserve", predicate, start, end), 18),
            "source": "market_rule_parser",
            "confidence": 0.76,
        }
    )


def _winner_semantic(text: str, *, event_label: str, outcome_label: str, category: str) -> Dict[str, Any]:
    clean = _normalize_text(text)
    if not re.search(r"\bwin(?:s|ner)?\b|\bchampion\b|\bnomination\b|\belection\b|\bprimary\b", clean):
        return {}
    start, end = _year_window(clean)
    subject = str(event_label or "").strip()
    obj = str(outcome_label or "").strip()
    if not subject or not obj or subject.lower() == obj.lower():
        return {}
    predicate = "winner"
    if "nomination" in clean:
        predicate = "nominee"
    elif "champion" in clean:
        predicate = "champion"
    return normalize_event_semantic(
        {
            "semantic_type": "single_winner_outcome",
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "comparator": "=",
            "threshold": 1,
            "unit": "boolean",
            "time_window_start": start,
            "time_window_end": end,
            "jurisdiction": str(category or "").strip(),
            "resolution_rule": text,
            "outcome_space_id": _hash_id("os", _compact_key(subject, predicate, start, end), 18),
            "source": "market_rule_parser",
            "confidence": 0.78,
        }
    )


def build_market_semantic(
    market: Dict[str, Any],
    *,
    event_id: str,
    event_label: str,
    finance_id: str,
    finance_label: str,
) -> Dict[str, Any]:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    question = str(market.get("question") or raw.get("question") or "").strip()
    rules = str(market.get("rules") or raw.get("rules") or raw.get("description") or "").strip()
    category = str(market.get("category") or raw.get("category") or "").strip()
    text = " ".join(part for part in [question, rules] if part)
    semantic = _winner_semantic(text, event_label=event_label, outcome_label=finance_label, category=category)
    if not semantic:
        semantic = _extract_threshold_semantic(text)
    if not semantic:
        semantic = normalize_event_semantic(
            {
                "subject": event_label,
                "predicate": "",
                "object": finance_label,
                "resolution_rule": text,
                "source": "market_rule_parser",
            }
        )
        semantic["semantic_type"] = "unstructured_market"
    semantic["event_id"] = event_id
    semantic["finance_id"] = finance_id
    semantic["market_condition_id"] = str(market.get("condition_id") or "").strip()
    semantic["market_question"] = question
    semantic["market_label"] = finance_label
    return semantic


def _interval_subset(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if not left or not right:
        return False
    l_lower = left.get("lower")
    l_upper = left.get("upper")
    r_lower = right.get("lower")
    r_upper = right.get("upper")
    if r_lower is not None:
        if l_lower is None or float(l_lower) < float(r_lower):
            return False
        if float(l_lower) == float(r_lower) and left.get("lower_inclusive") and not right.get("lower_inclusive"):
            return False
    if r_upper is not None:
        if l_upper is None or float(l_upper) > float(r_upper):
            return False
        if float(l_upper) == float(r_upper) and left.get("upper_inclusive") and not right.get("upper_inclusive"):
            return False
    return True


def _interval_disjoint(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if not left or not right:
        return False
    l_lower = left.get("lower")
    l_upper = left.get("upper")
    r_lower = right.get("lower")
    r_upper = right.get("upper")
    if l_upper is not None and r_lower is not None:
        if float(l_upper) < float(r_lower):
            return True
        if float(l_upper) == float(r_lower) and (not left.get("upper_inclusive") or not right.get("lower_inclusive")):
            return True
    if r_upper is not None and l_lower is not None:
        if float(r_upper) < float(l_lower):
            return True
        if float(r_upper) == float(l_lower) and (not right.get("upper_inclusive") or not left.get("lower_inclusive")):
            return True
    return False


def _interval_equivalent(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if not left or not right:
        return False
    keys = ("lower", "lower_inclusive", "upper", "upper_inclusive")
    return all(left.get(key) == right.get(key) for key in keys)


def _candidate_id(relation_type: str, source_id: str, target_id: str) -> str:
    return _hash_id("logic", f"{source_id}|{target_id}|{relation_type}", 18)


def _candidate_edge(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": candidate["candidate_id"],
        "source": candidate["source_id"],
        "target": candidate["target_id"],
        "relation_class": "LOGICAL",
        "relation_type": candidate["relation_type"],
        "confidence": candidate["confidence"],
        "strength": candidate.get("strength") or "MEDIUM",
        "reason": candidate["reason"],
        "verification_status": "CANDIDATE",
        "source_type": "LOGIC_CANDIDATE_PREVIEW",
        "details": {
            "candidate": True,
            "rule": candidate.get("rule") or "",
            "compared_fields": candidate.get("compared_fields") or {},
            "requires_review": candidate.get("requires_review", True),
        },
    }


def _limit_candidates(candidates: List[Dict[str, Any]], max_candidates: int) -> List[Dict[str, Any]]:
    if max_candidates <= 0:
        return []
    if len(candidates) <= max_candidates:
        return candidates

    relation_order = ["IMPLIES", "DISJOINT", "EQUAL", "OVERLAP"]
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        relation_type = str(candidate.get("relation_type") or "")
        buckets.setdefault(relation_type, []).append(candidate)
    for rows in buckets.values():
        rows.sort(key=lambda item: float(item.get("_rank_score") or 0.0), reverse=True)

    limited: List[Dict[str, Any]] = []
    while len(limited) < max_candidates:
        before = len(limited)
        for relation_type in relation_order:
            rows = buckets.get(relation_type) or []
            if rows and len(limited) < max_candidates:
                limited.append(rows.pop(0))
        for relation_type, rows in list(buckets.items()):
            if relation_type in relation_order:
                continue
            if rows and len(limited) < max_candidates:
                limited.append(rows.pop(0))
        if len(limited) == before:
            break
    return limited


def build_logic_candidates(
    market_semantics: Iterable[Dict[str, Any]],
    *,
    node_heat: Optional[Dict[str, float]] = None,
    max_candidates: int = 90,
) -> Dict[str, Any]:
    node_heat = node_heat or {}
    semantic_rows = [
        dict(item)
        for item in market_semantics
        if isinstance(item, dict) and item.get("finance_id") and item.get("structured")
    ]
    candidates: List[Dict[str, Any]] = []
    seen = set()

    def add_candidate(source_id: str, target_id: str, relation_type: str, **extra: Any) -> None:
        if source_id == target_id:
            return
        key = (source_id, target_id, relation_type)
        if key in seen:
            return
        seen.add(key)
        confidence = float(extra.get("confidence") or 0.7)
        heat = max(float(node_heat.get(source_id, 1.0)), float(node_heat.get(target_id, 1.0)))
        candidates.append(
            {
                "candidate_id": _candidate_id(relation_type, source_id, target_id),
                "source_id": source_id,
                "target_id": target_id,
                "relation_class": "LOGICAL",
                "relation_type": relation_type,
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "strength": "HIGH" if heat >= 75 else "MEDIUM",
                "_rank_score": round(heat + confidence * 8.0, 3),
                **extra,
            }
        )

    by_outcome_space: Dict[str, List[Dict[str, Any]]] = {}
    for row in semantic_rows:
        if row.get("semantic_type") != "single_winner_outcome":
            continue
        outcome_space_id = str(row.get("outcome_space_id") or "").strip()
        if outcome_space_id:
            by_outcome_space.setdefault(outcome_space_id, []).append(row)
    for outcome_space_id, rows in by_outcome_space.items():
        ordered = sorted(rows, key=lambda item: float(node_heat.get(str(item.get("finance_id")), 0.0)), reverse=True)[:14]
        for idx, left in enumerate(ordered):
            for right in ordered[idx + 1:]:
                add_candidate(
                    str(left["finance_id"]),
                    str(right["finance_id"]),
                    "DISJOINT",
                    confidence=0.86,
                    reason="Same single-winner outcome space; only one outcome can resolve true.",
                    rule="same_outcome_space_single_winner",
                    outcome_space_id=outcome_space_id,
                    compared_fields={
                        "subject": left.get("subject"),
                        "predicate": left.get("predicate"),
                        "left_object": left.get("object"),
                        "right_object": right.get("object"),
                        "time_window_start": left.get("time_window_start"),
                        "time_window_end": left.get("time_window_end"),
                    },
                    requires_review=False,
                )

    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for row in semantic_rows:
        if row.get("semantic_type") != "threshold_event" or not row.get("comparison_interval"):
            continue
        by_family.setdefault(str(row.get("family_key") or ""), []).append(row)
    for _, rows in by_family.items():
        ordered = sorted(rows, key=lambda item: float(node_heat.get(str(item.get("finance_id")), 0.0)), reverse=True)[:18]
        for left in ordered:
            for right in ordered:
                if left is right:
                    continue
                left_interval = left.get("comparison_interval") or {}
                right_interval = right.get("comparison_interval") or {}
                if _interval_subset(left_interval, right_interval) and not _interval_equivalent(left_interval, right_interval):
                    add_candidate(
                        str(left["finance_id"]),
                        str(right["finance_id"]),
                        "IMPLIES",
                        confidence=0.82,
                        reason="Threshold interval containment: source condition is stricter than target condition.",
                        rule="threshold_interval_subset",
                        compared_fields={
                            "subject": left.get("subject"),
                            "predicate": left.get("predicate"),
                            "source_comparator": left.get("comparator"),
                            "source_threshold": left.get("threshold"),
                            "target_comparator": right.get("comparator"),
                            "target_threshold": right.get("threshold"),
                            "unit": left.get("unit"),
                            "time_window_start": left.get("time_window_start"),
                            "time_window_end": left.get("time_window_end"),
                        },
                        requires_review=False,
                    )
                left_id = str(left["finance_id"])
                right_id = str(right["finance_id"])
                same_outcome_space = left.get("outcome_space_id") and left.get("outcome_space_id") == right.get("outcome_space_id")
                if left_id < right_id and same_outcome_space and _interval_disjoint(left_interval, right_interval):
                    add_candidate(
                        left_id,
                        right_id,
                        "DISJOINT",
                        confidence=0.84,
                        reason="Same numeric outcome space with non-overlapping intervals.",
                        rule="threshold_interval_disjoint",
                        outcome_space_id=left.get("outcome_space_id"),
                        compared_fields={
                            "subject": left.get("subject"),
                            "predicate": left.get("predicate"),
                            "source_comparator": left.get("comparator"),
                            "source_threshold": left.get("threshold"),
                            "target_comparator": right.get("comparator"),
                            "target_threshold": right.get("threshold"),
                            "unit": left.get("unit"),
                            "time_window_start": left.get("time_window_start"),
                            "time_window_end": left.get("time_window_end"),
                        },
                        requires_review=False,
                    )

    by_semantic_key: Dict[str, List[Dict[str, Any]]] = {}
    for row in semantic_rows:
        key = _compact_key(
            row.get("subject"),
            row.get("predicate"),
            row.get("object"),
            row.get("comparator"),
            row.get("threshold"),
            row.get("unit"),
            row.get("time_window_start"),
            row.get("time_window_end"),
            row.get("resolution_source"),
        )
        if key and key != "unknown":
            by_semantic_key.setdefault(key, []).append(row)
    for _, rows in by_semantic_key.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda item: float(node_heat.get(str(item.get("finance_id")), 0.0)), reverse=True)[:6]
        for idx, left in enumerate(ordered):
            for right in ordered[idx + 1:]:
                add_candidate(
                    str(left["finance_id"]),
                    str(right["finance_id"]),
                    "EQUAL",
                    confidence=0.72,
                    reason="Structured semantics match; full resolution rule still needs review.",
                    rule="structured_semantic_match",
                    compared_fields={
                        "subject": left.get("subject"),
                        "predicate": left.get("predicate"),
                        "object": left.get("object"),
                        "comparator": left.get("comparator"),
                        "threshold": left.get("threshold"),
                        "unit": left.get("unit"),
                    },
                    requires_review=True,
                )

    candidates = _limit_candidates(candidates, max_candidates)
    for candidate in candidates:
        candidate.pop("_rank_score", None)
    edges = [_candidate_edge(candidate) for candidate in candidates]
    return {
        "candidates": candidates,
        "edges": edges,
        "summary": {
            "total": len(candidates),
            "implies": sum(1 for item in candidates if item.get("relation_type") == "IMPLIES"),
            "disjoint": sum(1 for item in candidates if item.get("relation_type") == "DISJOINT"),
            "equal": sum(1 for item in candidates if item.get("relation_type") == "EQUAL"),
        },
    }


def _expression_input_ids(expression: Dict[str, Any]) -> List[str]:
    raw = expression.get("input_event_ids")
    if not isinstance(raw, list):
        raw = expression.get("inputs")
    ids: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                value = item.get("event_id") or item.get("id") or item.get("node_id")
            else:
                value = item
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)
    return ids


def _expression_output_id(expression: Dict[str, Any]) -> str:
    output = expression.get("output_event_id") or expression.get("output_id")
    if isinstance(expression.get("output"), dict):
        output = output or expression["output"].get("event_id") or expression["output"].get("id")
    return str(output or "").strip()


def validate_expression_shape(expression: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    operator = str(expression.get("operator") or "").strip().upper()
    if not operator:
        warnings.append({"code": "EXPRESSION_OPERATOR_RECOMMENDED", "message": "expression.operator is recommended"})
        return errors, warnings
    if operator not in EXPRESSION_OPERATORS:
        errors.append({"code": "INVALID_EXPRESSION_OPERATOR", "operator": operator})
        return errors, warnings
    inputs = _expression_input_ids(expression)
    output_id = _expression_output_id(expression)
    if operator in {"AND", "OR"} and len(inputs) < 2:
        errors.append({"code": "EXPRESSION_INPUTS_REQUIRED", "operator": operator, "message": f"{operator} requires at least two input events"})
    if operator == "NOT" and len(inputs) != 1:
        errors.append({"code": "EXPRESSION_NOT_INPUT_COUNT", "message": "NOT requires exactly one input event"})
    if operator == "DIFFERENCE" and len(inputs) != 2:
        errors.append({"code": "EXPRESSION_DIFFERENCE_INPUT_COUNT", "message": "DIFFERENCE requires exactly two input events"})
    if not output_id:
        errors.append({"code": "EXPRESSION_OUTPUT_REQUIRED", "message": "expression output_event_id is required"})
    return errors, warnings


def derived_edges_from_expression(expression_id: str, expression: Dict[str, Any]) -> List[Dict[str, Any]]:
    operator = str(expression.get("operator") or "").strip().upper()
    inputs = _expression_input_ids(expression)
    output_id = _expression_output_id(expression)
    if not operator or not output_id:
        return []

    def edge_item(source_id: str, target_id: str, relation_type: str, rule: str) -> Dict[str, Any]:
        return {
            "action": "edge_create",
            "edge_id": _hash_id("g_edge", f"{source_id}|{target_id}|{relation_type}|{expression_id}", 18),
            "source_id": source_id,
            "source_type": "event",
            "target_id": target_id,
            "target_type": "event",
            "relation_class": "LOGICAL",
            "relation_type": relation_type,
            "confidence": 0.92,
            "strength": "HIGH",
            "reason": f"Derived from expression {expression_id} by {rule}.",
            "verification_status": "SYSTEM_DERIVED",
            "source_kind": "SYSTEM_DERIVED",
            "payload": {
                "derived_from_expression_id": expression_id,
                "derivation_rule": rule,
                "expression_operator": operator,
            },
        }

    items: List[Dict[str, Any]] = []
    if operator == "AND":
        for input_id in inputs:
            items.append(edge_item(output_id, input_id, "IMPLIES", "AND_OUTPUT_IMPLIES_INPUT"))
    elif operator == "OR":
        for input_id in inputs:
            items.append(edge_item(input_id, output_id, "IMPLIES", "OR_INPUT_IMPLIES_OUTPUT"))
    elif operator == "NOT" and inputs:
        items.append(edge_item(inputs[0], output_id, "DISJOINT", "NOT_INPUT_DISJOINT_OUTPUT"))
    elif operator == "DIFFERENCE" and len(inputs) == 2:
        items.append(edge_item(output_id, inputs[0], "IMPLIES", "DIFFERENCE_OUTPUT_IMPLIES_LEFT"))
        items.append(edge_item(output_id, inputs[1], "DISJOINT", "DIFFERENCE_OUTPUT_DISJOINT_RIGHT"))
    return items


def validate_logical_conflicts(items: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    logical_edges: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        relation_class = str(item.get("relation_class") or "").strip().upper()
        relation_type = str(item.get("relation_type") or "").strip().upper()
        if relation_class != "LOGICAL" and relation_type not in LOGICAL_RELATIONS:
            continue
        source_id = str(item.get("source_id") or item.get("source_event_id") or "").strip()
        target_id = str(item.get("target_id") or item.get("target_event_id") or "").strip()
        if not source_id or not target_id:
            continue
        edge = {
            "index": index,
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type,
            "outcome_space_id": str(item.get("outcome_space_id") or "").strip(),
        }
        logical_edges.append(edge)
        if relation_type == "EQUAL":
            warnings.append({"code": "EQUAL_REQUIRES_MANUAL_REVIEW", "index": index})
        if relation_type == "OVERLAP":
            warnings.append({"code": "OVERLAP_REQUIRES_MANUAL_REVIEW", "index": index})
        if relation_type == "DISJOINT" and not edge["outcome_space_id"]:
            warnings.append({"code": "DISJOINT_OUTCOME_SPACE_RECOMMENDED", "index": index})

    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    implies = set()
    disjoint = set()
    for edge in logical_edges:
        pair = tuple(sorted([edge["source_id"], edge["target_id"]]))
        by_pair.setdefault(pair, []).append(edge)
        if edge["relation_type"] == "IMPLIES":
            implies.add((edge["source_id"], edge["target_id"]))
        if edge["relation_type"] == "DISJOINT":
            disjoint.add(pair)

    for pair, rows in by_pair.items():
        types = {row["relation_type"] for row in rows}
        if "EQUAL" in types and "DISJOINT" in types:
            errors.append({"code": "LOGICAL_CONFLICT_EQUAL_DISJOINT", "pair": list(pair)})
        spaces = {row["outcome_space_id"] for row in rows if row["outcome_space_id"]}
        if "DISJOINT" in types and len(spaces) > 1:
            errors.append({"code": "DISJOINT_OUTCOME_SPACE_CONFLICT", "pair": list(pair), "outcome_space_ids": sorted(spaces)})

    for source_id, target_id in list(implies):
        if (target_id, source_id) in implies:
            warnings.append({"code": "BIDIRECTIONAL_IMPLIES_SUGGESTS_EQUAL", "source_id": source_id, "target_id": target_id})

    for a, b in implies:
        for b2, c in implies:
            if b != b2:
                continue
            if tuple(sorted([a, c])) in disjoint:
                errors.append({"code": "LOGICAL_CONFLICT_IMPLIES_CHAIN_DISJOINT", "source_id": a, "via": b, "target_id": c})
    return errors, warnings


def validate_reasoning_edges(
    items: Iterable[Dict[str, Any]],
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    payload = payload or {}
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    payload_evidence = str(payload.get("evidence_summary") or payload.get("rationale") or payload.get("reason") or "").strip()
    for index, item in enumerate(items):
        relation_class = str(item.get("relation_class") or "").strip().upper()
        relation_type = str(item.get("relation_type") or "").strip().upper()
        if relation_class not in REASONING_RELATION_CLASSES:
            continue
        if relation_type in LOGICAL_RELATIONS:
            errors.append({
                "code": "REASONING_EDGE_CANNOT_USE_LOGICAL_RELATION",
                "index": index,
                "relation_class": relation_class,
                "relation_type": relation_type,
            })
        if not str(item.get("mechanism") or item.get("reason") or "").strip():
            warnings.append({
                "code": "REASONING_MECHANISM_RECOMMENDED",
                "index": index,
                "message": "reasoning edges should include mechanism or reason",
            })
        if not str(item.get("time_horizon") or item.get("horizon") or "").strip():
            warnings.append({
                "code": "REASONING_TIME_HORIZON_RECOMMENDED",
                "index": index,
                "message": "reasoning edges should include time_horizon",
            })
        if relation_class in {"CAUSAL", "SCENARIO"} and not item.get("assumptions"):
            warnings.append({
                "code": "REASONING_ASSUMPTIONS_RECOMMENDED",
                "index": index,
                "message": "causal/scenario edges should include assumptions",
            })
        if "confidence" not in item:
            warnings.append({
                "code": "REASONING_CONFIDENCE_RECOMMENDED",
                "index": index,
                "message": "reasoning edges should include confidence",
            })
        evidence_refs = item.get("evidence_refs")
        has_evidence_refs = isinstance(evidence_refs, list) and bool(evidence_refs)
        has_evidence = has_evidence_refs or bool(str(item.get("evidence_summary") or "").strip()) or bool(payload_evidence)
        if not has_evidence:
            warnings.append({
                "code": "REASONING_EVIDENCE_RECOMMENDED",
                "index": index,
                "message": "reasoning edges should include evidence_refs or evidence_summary",
            })
        if relation_class in {"CAUSAL", "SCENARIO"}:
            warnings.append({
                "code": "REASONING_NOT_STRICT_LOGIC",
                "index": index,
                "message": "causal/scenario edges are hypotheses and do not participate in strict logical inference",
            })
    return errors, warnings
