from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .equity_security_master import EquitySecurityMasterService
from .store import DataPlatformStore
from .universe_service import UniverseService


class HistoricalEquityUniverseService:
    """Create a survivorship-bias-safe Universe from master validity intervals."""

    def __init__(self, store: DataPlatformStore):
        self.master = EquitySecurityMasterService(store)
        self.universes = UniverseService(store)

    def create_snapshot(
        self,
        *,
        name: str,
        as_of: str,
        primary_exchanges: Iterable[str] = (),
        manifest_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        exchanges = {str(value).strip().upper() for value in primary_exchanges if str(value).strip()}
        eligible = [
            item for item in self.master.list_active(as_of=as_of)
            if not exchanges or str(item.get("primary_exchange") or "").upper() in exchanges
        ]
        instruments = [self.master.instrument_id_for_permno(item["permno"]) for item in eligible]
        if not instruments:
            raise ValueError("historical equity universe has no eligible securities")
        material = {"as_of": as_of[:10], "exchanges": sorted(exchanges), "instruments": sorted(instruments)}
        version = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        definition = self.universes.create_definition(
            name=name,
            version=version,
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": instruments},
            selection_rule_version="historical_equity_master.v1",
        )
        manifests = []
        if manifest_ids:
            from .data_client import FrozenManifestData

            manifests = [FrozenManifestData(self.master.store, item) for item in manifest_ids]
        snapshot = self.universes.resolve_snapshot(
            universe_definition_id=definition.universe_definition_id,
            as_of_time=as_of,
            manifests=manifests,
            selection_inputs_override={
                "method": "SECURITY_MASTER_VALIDITY",
                "primary_exchanges": sorted(exchanges),
                "eligible_security_ids": [item["security_id"] for item in eligible],
                "survivorship_policy": "AS_OF_VALIDITY_INTERVAL",
            },
        )
        return {"definition": definition, "snapshot": snapshot, "eligible_count": len(eligible)}
