from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog_service import DatasetCatalogService
from .data_client import FrozenManifestData
from .store import DataPlatformStore


@dataclass(frozen=True)
class SourcePolicy:
    mode: str
    manifest_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourcePolicy":
        mode = str(payload.get("mode") or "FIXED").strip().upper()
        manifest_ids = tuple(dict.fromkeys(str(item).strip() for item in payload.get("manifest_ids", []) if str(item).strip()))
        if mode not in {"FIXED", "COMPARE"}:
            raise ValueError("source policy mode must be FIXED or COMPARE")
        expected = 1 if mode == "FIXED" else 2
        if (mode == "FIXED" and len(manifest_ids) != 1) or (mode == "COMPARE" and len(manifest_ids) != expected):
            raise ValueError(f"{mode} source policy requires {expected} manifest id(s)")
        return cls(mode=mode, manifest_ids=manifest_ids)


class SourcePolicyService:
    """Resolve frozen source manifests without mutating or merging them."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.catalog = DatasetCatalogService(store)

    def fixed(self, policy: SourcePolicy) -> dict[str, Any]:
        if policy.mode != "FIXED":
            raise ValueError("fixed resolution requires FIXED policy")
        frozen = FrozenManifestData(self.store, policy.manifest_ids[0])
        return {"mode": "FIXED", "manifest": frozen.descriptor(), "automatic_fallback": False}

    def compare(self, policy: SourcePolicy, *, price_tolerance_bps: float = 1.0) -> dict[str, Any]:
        if policy.mode != "COMPARE":
            raise ValueError("comparison requires COMPARE policy")
        left, right = [FrozenManifestData(self.store, manifest_id) for manifest_id in policy.manifest_ids]
        left_catalog = self.catalog.get_catalog(left.dataset_id)
        right_catalog = self.catalog.get_catalog(right.dataset_id)
        if not left_catalog or not right_catalog:
            raise ValueError("comparison catalog entry is missing")
        if left_catalog.instrument_id != right_catalog.instrument_id:
            raise ValueError("source comparison requires the same instrument_id")
        if left_catalog.frequency != right_catalog.frequency or left_catalog.data_type != right_catalog.data_type:
            raise ValueError("source comparison requires the same data type and frequency")

        left_rows = {str(row["bar_start_time"]): row for row in left.read_rows()}
        right_rows = {str(row["bar_start_time"]): row for row in right.read_rows()}
        shared = sorted(set(left_rows) & set(right_rows))
        conflicts = []
        max_close_diff_bps = 0.0
        for event_time in shared:
            left_close = float(left_rows[event_time]["close"])
            right_close = float(right_rows[event_time]["close"])
            denominator = max(abs(left_close), abs(right_close), 1e-12)
            difference_bps = abs(left_close - right_close) / denominator * 10_000.0
            max_close_diff_bps = max(max_close_diff_bps, difference_bps)
            if difference_bps > float(price_tolerance_bps):
                conflicts.append({
                    "bar_start_time": event_time,
                    "field": "close",
                    "left": left_close,
                    "right": right_close,
                    "difference_bps": difference_bps,
                })
        return {
            "mode": "COMPARE",
            "instrument_id": left_catalog.instrument_id,
            "frequency": left_catalog.frequency,
            "left": {"manifest_id": left.manifest_id, "dataset_id": left.dataset_id, "source": left_catalog.source},
            "right": {"manifest_id": right.manifest_id, "dataset_id": right.dataset_id, "source": right_catalog.source},
            "left_only_count": len(set(left_rows) - set(right_rows)),
            "right_only_count": len(set(right_rows) - set(left_rows)),
            "shared_count": len(shared),
            "conflict_count": len(conflicts),
            "max_close_difference_bps": max_close_diff_bps,
            "conflicts": conflicts[:500],
            "resolution": "KEEP_BOTH",
            "composite_created": False,
        }
