from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


EQUITY_FACTOR_DATASETS: dict[str, dict[str, Any]] = {
    "equity_valuation_daily": {
        "label": "Equity Valuation (PIT)",
        "physical_data_types": ("equity_valuation_daily",),
        "frequency": "1d",
        "time_semantics": "SOURCE_AVAILABLE_TIME",
        "point_in_time_policy": "AS_OF",
        "sparse": False,
        "fields": {
            "market_cap": "Market Capitalization",
            "shares_outstanding": "Shares Outstanding",
        },
    },
    "corporate_actions": {
        "label": "Corporate Actions (PIT)",
        "physical_data_types": ("corporate_actions",),
        "frequency": "event",
        "time_semantics": "SOURCE_AVAILABLE_TIME",
        "point_in_time_policy": "AS_OF",
        "sparse": True,
        "fields": {
            "cash_dividend": "Cash Dividend per Share",
            "nonordinary_dividend": "Nonordinary Dividend per Share",
            "price_factor": "Price Adjustment Factor",
            "share_factor": "Share Adjustment Factor",
        },
    },
    "fundamentals": {
        "label": "SEC Fundamentals (PIT)",
        "physical_data_types": ("fundamentals_derived", "fundamentals_pit"),
        "frequency": "event",
        "time_semantics": "SOURCE_AVAILABLE_TIME",
        "point_in_time_policy": "FILED_OR_ACCEPTED_AT",
        "sparse": True,
        "fields": {
            "revenue": "Revenue (Latest Reported)",
            "revenue_ttm": "Revenue TTM",
            "net_income": "Net Income (Latest Reported)",
            "net_income_ttm": "Net Income TTM",
            "operating_cash_flow": "Operating Cash Flow (Latest Reported)",
            "operating_cash_flow_ttm": "Operating Cash Flow TTM",
            "capex": "Capital Expenditures (Latest Reported)",
            "capex_ttm": "Capital Expenditures TTM",
            "assets": "Total Assets",
            "liabilities": "Total Liabilities",
            "equity": "Shareholders' Equity",
        },
    },
    "equity_research_monthly": {
        "label": "US Equity Monthly Research Panel (PIT)",
        "physical_data_types": ("equity_research_monthly",),
        "frequency": "1d",
        "time_semantics": "SOURCE_AVAILABLE_TIME",
        "point_in_time_policy": "AS_OF",
        "sparse": True,
        "fields": {
            "size_score": "Small-cap eligible market capitalization",
            "size_div_score": "Small-cap plus dividend eligible market capitalization",
            "size_quality_score": "Small-cap plus quality eligible market capitalization",
            "size_price_score": "Small-cap plus price eligible market capitalization",
            "grandma_us_score": "Complete Grandma US eligible market capitalization",
            "market_cap_usd": "Market capitalization in US dollars",
            "adv20_usd": "Trailing 20-session average dollar volume",
            "cash_dividend_365d": "Trailing 365-day cash dividend per share",
            "dividend_yield": "Trailing cash dividend yield",
            "net_income_ttm": "Point-in-time trailing-twelve-month net income",
            "operating_cash_flow_ttm": "Point-in-time trailing-twelve-month operating cash flow",
            "shareholders_equity": "Latest point-in-time shareholders' equity",
        },
    },
}


_FUNDAMENTAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dataset_contract(dataset: str) -> dict[str, Any] | None:
    value = EQUITY_FACTOR_DATASETS.get(_clean(dataset).lower())
    return dict(value) if value else None


def physical_data_types(dataset: str) -> tuple[str, ...]:
    contract = dataset_contract(dataset)
    return tuple(contract.get("physical_data_types") or ()) if contract else (_clean(dataset).lower(),)


def dataset_fields(dataset: str) -> dict[str, str]:
    contract = dataset_contract(dataset)
    return dict(contract.get("fields") or {}) if contract else {}


def is_sparse_dataset(dataset: str) -> bool:
    contract = dataset_contract(dataset)
    return bool(contract and contract.get("sparse"))


def field_is_available(
    dataset: str,
    field: str,
    *,
    physical_data_type: str,
    catalog_fields: Iterable[str],
) -> bool:
    field = _clean(field).lower()
    fields = {_clean(item).lower() for item in catalog_fields}
    if field in fields:
        return True
    return (
        _clean(dataset).lower() == "fundamentals"
        and _clean(physical_data_type).lower() == "fundamentals_pit"
        and field in dataset_fields("fundamentals")
        and {"concept", "value", "available_time"}.issubset(fields)
    )


def project_factor_rows(
    dataset: str,
    field: str,
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project a logical Factor field from one immutable physical Manifest.

    Raw SEC Company Facts stay immutable and pinned. Logical latest/TTM values
    are derived strictly in available-time order, so a later filing or
    restatement cannot leak into an earlier observation.
    """

    dataset = _clean(dataset).lower()
    field = _clean(field).lower()

    # OPTIMIZATION: Avoid unnecessary deep copy. Only copy when we need to modify.
    # Old behavior: always copied all rows even when no projection needed.
    # New behavior: return original reference when no modification required.
    if not rows:
        return []

    # Check if projection is needed
    needs_projection = (
        dataset == "fundamentals" and
        field not in (rows[0] if rows else {})
    )

    if not needs_projection:
        # No modification needed, return original reference (or convert if needed)
        if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
            return rows  # type: ignore[return-value]
        return [dict(row) for row in rows]

    # Only copy when we actually need to project
    copied = [dict(row) for row in rows]
    return _project_fundamental_rows(field, copied)


def _project_fundamental_rows(field: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trailing = field.endswith("_ttm")
    base_field = field.removesuffix("_ttm")
    concepts = _FUNDAMENTAL_CONCEPTS.get(base_field)
    if not concepts:
        return []
    priority = {concept: index for index, concept in enumerate(concepts)}
    eligible = [
        row for row in rows
        if _clean(row.get("concept")) in priority
        and (not _clean(row.get("unit")) or _clean(row.get("unit")).upper() == "USD")
        and _clean(row.get("available_time"))
        and row.get("value") is not None
    ]
    eligible.sort(key=lambda row: (_parse_time(row["available_time"]), _clean(row.get("period_end"))))
    latest_by_period: dict[str, dict[str, Any]] = {}
    output: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(eligible):
        available = _clean(eligible[cursor]["available_time"])
        batch: list[dict[str, Any]] = []
        while cursor < len(eligible) and _clean(eligible[cursor]["available_time"]) == available:
            batch.append(eligible[cursor])
            cursor += 1
        for row in batch:
            period_end = _clean(row.get("period_end"))
            if not period_end:
                continue
            existing = latest_by_period.get(period_end)
            if existing is None or priority[_clean(row.get("concept"))] <= priority[_clean(existing.get("concept"))]:
                latest_by_period[period_end] = row
        value: float | None = None
        source_rows: list[dict[str, Any]] = []
        if trailing:
            quarterlies = [row for row in latest_by_period.values() if _is_discrete_quarter(row)]
            quarterlies.sort(key=lambda row: _clean(row.get("period_end")))
            source_rows = quarterlies[-4:]
            if len(source_rows) == 4:
                try:
                    value = sum(float(row["value"]) for row in source_rows)
                except (TypeError, ValueError):
                    value = None
        else:
            source_rows = sorted(
                latest_by_period.values(),
                key=lambda row: (_clean(row.get("period_end")), -priority[_clean(row.get("concept"))]),
            )[-1:]
            if source_rows:
                try:
                    value = float(source_rows[-1]["value"])
                except (TypeError, ValueError):
                    value = None
        if value is None:
            continue
        representative = source_rows[-1]
        output.append({
            "security_id": representative.get("security_id"),
            "instrument_id": representative.get("instrument_id"),
            "event_time": available,
            "available_time": available,
            field: value,
            "source": "SEC/COMPANYFACTS_PIT_DERIVED",
            "source_concepts": sorted({_clean(row.get("concept")) for row in source_rows}),
            "source_accessions": sorted({_clean(row.get("accession_number")) for row in source_rows}),
        })
    return output


def _is_discrete_quarter(row: Mapping[str, Any]) -> bool:
    if _clean(row.get("form")).upper() not in {"10-Q", "10-Q/A"}:
        return False
    start = _clean(row.get("period_start"))
    end = _clean(row.get("period_end"))
    if not start or not end:
        return False
    try:
        duration = (_parse_time(end) - _parse_time(start)).days
    except (TypeError, ValueError):
        return False
    return 70 <= duration <= 120
