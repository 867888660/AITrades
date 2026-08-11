from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical_dataset import CanonicalDatasetCommitter
from .equity_security_master import EquitySecurityMasterService
from .store import DataPlatformStore


SEC_PIT_NORMALIZER_VERSION = "sec_companyfacts_pit.v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _stamp(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if len(text) <= 10:
            parsed = datetime.combine(date.fromisoformat(text[:10]), time.min, tzinfo=timezone.utc)
        else:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class SecPointInTimeNormalizer:
    """Normalize SEC Company Facts using filing acceptance as availability."""

    def __init__(
        self,
        store: DataPlatformStore,
        *,
        output_root: str | Path | None = None,
    ):
        self.store = store
        self.master = EquitySecurityMasterService(store)
        self.committer = CanonicalDatasetCommitter(store, output_root)

    @staticmethod
    def acceptance_index(submissions: Mapping[str, Any] | None) -> dict[str, str]:
        recent = dict((submissions or {}).get("filings", {}).get("recent", {}) or {})
        accessions = list(recent.get("accessionNumber") or [])
        accepted = list(recent.get("acceptanceDateTime") or [])
        return {
            _clean(accession): _stamp(accepted[index])
            for index, accession in enumerate(accessions)
            if _clean(accession) and index < len(accepted) and _clean(accepted[index])
        }

    def normalize_companyfacts(
        self,
        companyfacts: Mapping[str, Any],
        *,
        submissions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cik = _clean(companyfacts.get("cik")).zfill(10)
        if not cik.isdigit() or len(cik) != 10:
            raise ValueError("Company Facts payload has an invalid CIK")
        linked = self.master.resolve("CIK", cik, as_of=date.today().isoformat())
        if not linked:
            with self.store.connection() as conn:
                linked = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM equity_security_master WHERE cik=? ORDER BY security_id", (cik,)
                    ).fetchall()
                ]
        if not linked:
            raise ValueError(f"CIK {cik} is not linked to the Equity Security Master")
        security_ids = [str(item["security_id"]) for item in linked]
        instrument_ids = [self.master.instrument_id_for_permno(item["permno"]) for item in linked]
        accepted = self.acceptance_index(submissions)
        facts = companyfacts.get("facts") if isinstance(companyfacts.get("facts"), Mapping) else {}
        rows: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for taxonomy, concepts in facts.items():
            if not isinstance(concepts, Mapping):
                continue
            for concept, definition in concepts.items():
                units = definition.get("units") if isinstance(definition, Mapping) else {}
                if not isinstance(units, Mapping):
                    continue
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    for observation in observations:
                        if not isinstance(observation, Mapping):
                            continue
                        accession = _clean(observation.get("accn"))
                        filed = _stamp(observation.get("filed"))
                        available = accepted.get(accession) or filed
                        period_end = _stamp(observation.get("end"))
                        if not available or not period_end:
                            continue
                        key = (
                            taxonomy, concept, unit, accession,
                            _clean(observation.get("start")), _clean(observation.get("end")),
                            _clean(observation.get("frame")), repr(observation.get("val")),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        for security_id, instrument_id in zip(security_ids, instrument_ids):
                            rows.append(
                                {
                                    "security_id": security_id,
                                    "instrument_id": instrument_id,
                                    "cik": cik,
                                    "entity_name": _clean(companyfacts.get("entityName")),
                                    "taxonomy": _clean(taxonomy),
                                    "concept": _clean(concept),
                                    "label": _clean(definition.get("label")),
                                    "unit": _clean(unit),
                                    "value": observation.get("val"),
                                    "period_start": _stamp(observation.get("start")),
                                    "period_end": period_end,
                                    "event_time": period_end,
                                    "filed_at": filed,
                                    "accepted_at": accepted.get(accession) or "",
                                    "available_time": available,
                                    "form": _clean(observation.get("form")),
                                    "fiscal_year": observation.get("fy"),
                                    "fiscal_period": _clean(observation.get("fp")),
                                    "frame": _clean(observation.get("frame")),
                                    "accession_number": accession,
                                    "source": "SEC/COMPANYFACTS",
                                }
                            )
        rows.sort(key=lambda item: (item["available_time"], item["security_id"], item["concept"]))
        return {
            "schema_version": "fundamentals_pit.v1",
            "cik": cik,
            "security_ids": security_ids,
            "row_count": len(rows),
            "rows": rows,
        }

    def normalize_files(
        self,
        companyfacts_path: str | Path,
        *,
        submissions_path: str | Path | None = None,
    ) -> dict[str, Any]:
        facts_path = Path(companyfacts_path).expanduser().resolve()
        submissions = None
        if submissions_path:
            submissions = json.loads(Path(submissions_path).expanduser().resolve().read_text(encoding="utf-8"))
        return self.normalize_companyfacts(
            json.loads(facts_path.read_text(encoding="utf-8")), submissions=submissions
        )

    def commit(self, normalized: Mapping[str, Any], *, dataset_prefix: str = "sec:companyfacts") -> dict[str, Any]:
        rows = list(normalized.get("rows") or [])
        if not rows:
            raise ValueError("SEC PIT dataset contains no linked facts")
        result = self.committer.commit(
            dataset_id=f"{dataset_prefix}:{normalized['cik']}:fundamentals_pit",
            instrument_id=str(rows[0]["instrument_id"]),
            data_type="fundamentals_pit",
            frequency="event",
            source="SEC/COMPANYFACTS",
            source_version=SEC_PIT_NORMALIZER_VERSION,
            schema_version="fundamentals_pit.v1",
            rows=rows,
            event_time_field="event_time",
            point_in_time_policy="FILED_OR_ACCEPTED_AT",
            metadata={"cik": normalized["cik"], "security_ids": normalized["security_ids"]},
        )
        return {
            "status": "READY",
            "dataset_id": result["dataset_id"],
            "row_count": result["row_count"],
            "catalog": asdict(result["catalog"]),
            "manifest": asdict(result["manifest"]),
        }

    def commit_derived(
        self,
        normalized: Mapping[str, Any],
        *,
        dataset_prefix: str = "sec:companyfacts",
    ) -> dict[str, Any]:
        source_rows = [dict(row) for row in normalized.get("rows") or []]
        if not source_rows:
            raise ValueError("SEC PIT dataset contains no linked facts")
        derived: list[dict[str, Any]] = []
        security_ids = sorted({str(row["security_id"]) for row in source_rows})
        for security_id in security_ids:
            scoped = [row for row in source_rows if str(row["security_id"]) == security_id]
            for available in sorted({str(row["available_time"]) for row in scoped}):
                values = FundamentalPointInTimeView.as_of(scoped, available)
                derived.append(
                    {
                        "security_id": security_id,
                        "instrument_id": next(str(row["instrument_id"]) for row in scoped),
                        "event_time": available,
                        "available_time": available,
                        **{
                            key: value
                            for key, value in values.items()
                            if not key.endswith("_provenance") and key != "as_of"
                        },
                        "source": "SEC/COMPANYFACTS",
                    }
                )
        logical_fields = sorted(
            {key for row in derived for key in row if key not in {"security_id", "instrument_id"}}
        )
        result = self.committer.commit(
            dataset_id=f"{dataset_prefix}:{normalized['cik']}:fundamentals_derived",
            instrument_id=str(derived[0]["instrument_id"]),
            data_type="fundamentals_derived",
            frequency="event",
            source="SEC/COMPANYFACTS",
            source_version=SEC_PIT_NORMALIZER_VERSION,
            schema_version="fundamentals_derived.v1",
            rows=derived,
            event_time_field="event_time",
            point_in_time_policy="FILED_OR_ACCEPTED_AT",
            metadata={
                "cik": normalized["cik"],
                "source_schema_version": "fundamentals_pit.v1",
                "logical_fields": logical_fields,
                "derivation_policy": "latest_as_of; ttm_only_when_four_discrete_quarters_exist",
            },
        )
        return {
            "status": "READY",
            "dataset_id": result["dataset_id"],
            "row_count": result["row_count"],
            "catalog": asdict(result["catalog"]),
            "manifest": asdict(result["manifest"]),
        }


class FundamentalPointInTimeView:
    """Deterministic as-of selection and simple TTM derivation over long facts."""

    CONCEPTS = {
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "net_income": ("NetIncomeLoss", "ProfitLoss"),
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
        "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        "shares": ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    }

    @classmethod
    def as_of(cls, rows: Iterable[Mapping[str, Any]], as_of: str) -> dict[str, Any]:
        cutoff = _stamp(as_of)
        eligible = [dict(row) for row in rows if _clean(row.get("available_time")) <= cutoff]
        result: dict[str, Any] = {}
        for field, concepts in cls.CONCEPTS.items():
            candidates = [row for row in eligible if _clean(row.get("concept")) in concepts]
            candidates.sort(key=lambda row: (_clean(row.get("period_end")), _clean(row.get("available_time"))))
            if candidates:
                latest = candidates[-1]
                result[field] = latest.get("value")
                result[f"{field}_provenance"] = {
                    "concept": latest.get("concept"),
                    "accession_number": latest.get("accession_number"),
                    "available_time": latest.get("available_time"),
                    "period_end": latest.get("period_end"),
                }
            quarterlies = []
            for row in candidates:
                if _clean(row.get("form")) != "10-Q" or not _clean(row.get("period_start")):
                    continue
                try:
                    duration = (
                        datetime.fromisoformat(_clean(row["period_end"]))
                        - datetime.fromisoformat(_clean(row["period_start"]))
                    ).days
                except (TypeError, ValueError):
                    continue
                if 70 <= duration <= 120:
                    quarterlies.append(row)
            quarterlies.sort(key=lambda row: _clean(row.get("period_end")))
            latest_four = quarterlies[-4:]
            if field in {"revenue", "net_income", "operating_cash_flow", "capex"} and len(latest_four) == 4:
                try:
                    result[f"{field}_ttm"] = sum(float(row["value"]) for row in latest_four)
                except (TypeError, ValueError):
                    pass
        result["as_of"] = cutoff
        return result
