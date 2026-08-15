from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from services.http_client import SESSION, get_timeout


SEC_DATA_BASE = "https://data.sec.gov"
SEC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "40-F", "6-K"}
DEFAULT_COMPANY_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "NetIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CommonStockSharesOutstanding",
    "EarningsPerShareDiluted",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_cik(value: Any) -> str:
    text = str(value or "").strip().upper().removeprefix("CIK")
    if not text.isdigit() or len(text) > 10:
        raise ValueError("CIK must contain 1 to 10 digits")
    return text.zfill(10)


def normalize_sec_user_agent(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError("SEC EDGAR User-Agent is not configured")
    if len(text) > 200 or "\r" in text or "\n" in text:
        raise ValueError("SEC EDGAR User-Agent is invalid")
    if not re.search(r"\S+@\S+\.\S+", text):
        raise ValueError("SEC EDGAR User-Agent must include a contact email")
    return text


def _headers(user_agent: Any) -> dict[str, str]:
    return {
        "User-Agent": normalize_sec_user_agent(user_agent),
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }


def fetch_sec_submissions(cik: Any, *, user_agent: Any) -> dict[str, Any]:
    normalized_cik = normalize_cik(cik)
    response = SESSION.get(
        f"{SEC_DATA_BASE}/submissions/CIK{normalized_cik}.json",
        headers=_headers(user_agent),
        timeout=get_timeout(),
    )
    response.raise_for_status()
    payload = response.json()
    recent = dict(dict(payload.get("filings") or {}).get("recent") or {})
    forms = list(recent.get("form") or [])
    accessions = list(recent.get("accessionNumber") or [])
    filing_dates = list(recent.get("filingDate") or [])
    return {
        "source": "SEC_EDGAR",
        "cik": normalized_cik,
        "entity_name": payload.get("name") or "",
        "latest_filing": {
            "form": forms[0] if forms else "",
            "accession": accessions[0] if accessions else "",
            "filed": filing_dates[0] if filing_dates else "",
        },
        "fetched_at": _utc_now(),
    }


def _clean_concepts(values: Iterable[Any] | None) -> list[str]:
    concepts: list[str] = []
    for value in values or DEFAULT_COMPANY_CONCEPTS:
        concept = str(value or "").strip()
        if concept and concept not in concepts:
            concepts.append(concept)
    if not concepts:
        raise ValueError("At least one SEC concept is required")
    if len(concepts) > 20:
        raise ValueError("At most 20 SEC concepts may be requested")
    return concepts


def _latest_fact(concept: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for unit, rows in dict(raw.get("units") or {}).items():
        for row in rows or []:
            if str(row.get("form") or "").upper() not in SEC_FORMS or row.get("val") is None:
                continue
            candidates.append({
                "concept": concept,
                "label": raw.get("label") or concept,
                "description": raw.get("description") or "",
                "unit": unit,
                "value": row.get("val"),
                "period_start": row.get("start"),
                "period_end": row.get("end"),
                "filed": row.get("filed"),
                "form": row.get("form"),
                "fiscal_year": row.get("fy"),
                "fiscal_period": row.get("fp"),
                "accession": row.get("accn"),
                "frame": row.get("frame"),
            })
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            str(item.get("filed") or ""),
            str(item.get("period_end") or ""),
            str(item.get("accession") or ""),
        ),
    )


def fetch_sec_company_facts(
    cik: Any,
    *,
    user_agent: Any,
    concepts: Iterable[Any] | None = None,
) -> dict[str, Any]:
    normalized_cik = normalize_cik(cik)
    selected = _clean_concepts(concepts)
    response = SESSION.get(
        f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{normalized_cik}.json",
        headers=_headers(user_agent),
        timeout=get_timeout(),
    )
    response.raise_for_status()
    payload = response.json()
    us_gaap = dict(dict(payload.get("facts") or {}).get("us-gaap") or {})
    facts = [
        fact
        for concept in selected
        if (fact := _latest_fact(concept, dict(us_gaap.get(concept) or {})))
    ]
    return {
        "source": "SEC_EDGAR_COMPANYFACTS",
        "cik": normalized_cik,
        "entity_name": payload.get("entityName") or "",
        "requested_concepts": selected,
        "available_concept_count": len(us_gaap),
        "facts": facts,
        "fetched_at": _utc_now(),
        "point_in_time_note": "Each value is available no earlier than its SEC filed date.",
    }
