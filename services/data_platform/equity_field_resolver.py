from __future__ import annotations

from typing import Any, Iterable

from .catalog_service import DatasetCatalogService
from .store import DataPlatformStore


FIELD_CONTRACTS: dict[str, tuple[str, ...]] = {
    "open": ("bars",), "high": ("bars",), "low": ("bars",), "close": ("bars",),
    "volume": ("bars",), "turnover": ("bars",), "trade_count": ("bars",),
    "total_return": ("bars",), "price_return": ("bars",),
    "market_cap": ("equity_valuation_daily",), "shares_outstanding": ("equity_valuation_daily",),
    "cash_dividend": ("corporate_actions",), "price_factor": ("corporate_actions",),
    "share_factor": ("corporate_actions",),
    "revenue": ("fundamentals_derived", "fundamentals_pit"),
    "net_income": ("fundamentals_derived", "fundamentals_pit"),
    "assets": ("fundamentals_derived", "fundamentals_pit"),
    "liabilities": ("fundamentals_derived", "fundamentals_pit"),
    "equity": ("fundamentals_derived", "fundamentals_pit"),
}


class EquityFieldResolver:
    """Resolve logical equity fields to immutable READY manifests."""

    def __init__(self, store: DataPlatformStore):
        self.catalog = DatasetCatalogService(store)

    def resolve(self, fields: Iterable[str]) -> dict[str, Any]:
        requested = sorted({str(field).strip().lower() for field in fields if str(field).strip()})
        resolved: dict[str, Any] = {}
        blocked: list[dict[str, Any]] = []
        entries = self.catalog.list_catalog(status="READY") + self.catalog.list_catalog(status="PARTIAL")
        for field in requested:
            types = FIELD_CONTRACTS.get(field, ())
            candidates = [
                entry for entry in entries
                if entry.data_type in types and field in set(entry.fields)
                and entry.latest_manifest_id and entry.quality_status == "PASS"
                and entry.point_in_time_policy not in {"", "NONE"}
            ]
            candidates.sort(key=lambda entry: (entry.updated_at or "", entry.dataset_id), reverse=True)
            if not candidates:
                blocked.append(
                    {"field": field, "code": "NO_PIT_MANIFEST", "accepted_data_types": list(types)}
                )
                continue
            selected = candidates[0]
            resolved[field] = {
                "dataset_id": selected.dataset_id,
                "manifest_id": selected.latest_manifest_id,
                "data_type": selected.data_type,
                "schema_version": selected.schema_version,
                "point_in_time_policy": selected.point_in_time_policy,
            }
        return {
            "schema_version": "equity_field_resolution.v1",
            "status": "READY" if not blocked else "BLOCKED",
            "requested_fields": requested,
            "resolved": resolved,
            "blocked": blocked,
            "manifest_ids": sorted({item["manifest_id"] for item in resolved.values()}),
        }
