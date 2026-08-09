from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from services.config_loader import load_web_settings

from .data_capability_service import (
    BINANCE_INTERVALS,
    POLYMARKET_INTERVALS,
    ResearchDataCapabilityService,
)
from .instrument_registry import InstrumentRegistry
from .store import BASE_DIR, DataPlatformStore, json_dumps
from .universe_service import UniverseService


INPUT_CANDIDATE_SCHEMA_VERSION = "factor_input_candidates.v1"
_CRYPTO_BAR_FIELDS = (
    ("open", "Open"),
    ("high", "High"),
    ("low", "Low"),
    ("close", "Close"),
    ("volume", "Base Volume"),
    ("quote_volume", "Quote Volume"),
    ("trade_count", "Trade Count"),
)
_EQUITY_BAR_FIELDS = (
    ("open", "Open"),
    ("high", "High"),
    ("low", "Low"),
    ("close", "Close"),
    ("volume", "Volume"),
)
_PRICE_HISTORY_FIELDS = (
    ("price", "Outcome Price"),
)
_FACTOR_FREQUENCIES = {"1m", "5m", "15m", "1h", "4h", "1d"}
_KNOWN_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "EUR", "TRY")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


class FactorInputCandidateResolver:
    """Resolve Factor Inputs from the pinned Universe and real data capabilities.

    The result intentionally distinguishes theoretical requestability from
    already-prepared local data. Factor authoring may select only candidates
    supported end-to-end by the current Factor engine and Preview runtime.
    """

    def __init__(
        self,
        store: DataPlatformStore,
        *,
        settings: Mapping[str, Any] | None = None,
        base_dir: str | Path = BASE_DIR,
    ):
        self.store = store
        self.registry = InstrumentRegistry(store)
        self.universes = UniverseService(store)
        self.capabilities = ResearchDataCapabilityService(
            dict(settings) if settings is not None else load_web_settings(),
            base_dir=base_dir,
        )

    def resolve_project(self, project_id: str) -> dict[str, Any]:
        project_id = _clean(project_id)
        universe_ref = self.universes.get_research_ref(project_id)
        if universe_ref is None:
            raise ValueError("Select a primary Universe before choosing Factor Inputs")
        snapshot = self.universes.get_snapshot(universe_ref["universe_snapshot_id"])
        if snapshot is None or not snapshot.actual_instrument_ids:
            raise ValueError("The current Universe Snapshot has no Instruments")
        return self.resolve_snapshot(
            snapshot.universe_snapshot_id,
            project_id=project_id,
            universe_name=_clean(universe_ref.get("name")),
        )

    def resolve_snapshot(
        self,
        universe_snapshot_id: str,
        *,
        project_id: str = "",
        universe_name: str = "",
    ) -> dict[str, Any]:
        snapshot = self.universes.get_snapshot(_clean(universe_snapshot_id))
        if snapshot is None:
            raise ValueError("Universe Snapshot not found")
        instrument_ids = tuple(snapshot.actual_instrument_ids)
        instruments = [self._instrument(item) for item in instrument_ids]
        datasets = self._datasets(instruments)
        input_candidates = [
            candidate
            for dataset in datasets
            for field in dataset["fields"]
            for candidate in field["frequencies"]
        ]
        selectable_count = sum(
            bool(item.get("factor_selectable"))
            for item in input_candidates
        )
        diagnostics: list[dict[str, str]] = []
        if selectable_count == 0:
            unavailable = [
                dataset for dataset in datasets
                if _clean((dataset.get("provider_status") or {}).get("status")).upper()
                == "UNAVAILABLE"
            ]
            if unavailable:
                reasons = list(dict.fromkeys(
                    _clean((item.get("provider_status") or {}).get("reason"))
                    for item in unavailable
                    if _clean((item.get("provider_status") or {}).get("reason"))
                ))
                diagnostics.append({
                    "level": "ERROR",
                    "code": "INPUT_PROVIDER_UNAVAILABLE",
                    "path": "inputs",
                    "message": " ".join(reasons) or "The required data provider is unavailable.",
                })
            else:
                diagnostics.append({
                    "level": "ERROR",
                    "code": "FACTOR_INPUT_CANDIDATE_UNAVAILABLE",
                    "path": "inputs",
                    "message": (
                        "No Dataset, Field, and Frequency combination can be prepared "
                        "for every Instrument in the current Universe."
                    ),
                })
        material = {
            "schema_version": INPUT_CANDIDATE_SCHEMA_VERSION,
            "universe_snapshot_id": snapshot.universe_snapshot_id,
            "universe_fingerprint": snapshot.fingerprint,
            "instrument_ids": list(instrument_ids),
            "datasets": datasets,
        }
        return {
            **material,
            "project_id": _clean(project_id),
            "universe": {
                "name": universe_name,
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "universe_fingerprint": snapshot.fingerprint,
                "as_of_time": snapshot.as_of_time,
                "member_count": len(instrument_ids),
                "instrument_ids": list(instrument_ids),
            },
            "instrument_summary": self._instrument_summary(instruments),
            "datasets": datasets,
            "input_candidates": input_candidates,
            "diagnostics": diagnostics,
            "selectable_candidate_count": selectable_count,
            "candidate_fingerprint": hashlib.sha256(
                json_dumps(material).encode("utf-8")
            ).hexdigest(),
            "semantics": {
                "requestable": (
                    "The provider adapter can theoretically request or prepare this data "
                    "for every applicable Instrument."
                ),
                "prepared": (
                    "A matching local Data Catalog entry already exists. Prepared does not "
                    "guarantee that every requested historical timestamp is covered."
                ),
                "factor_selectable": (
                    "The Dataset, Field, and Frequency are supported from Factor authoring "
                    "through Requirement compilation and Preview execution."
                ),
            },
        }

    def assert_inputs_selectable(
        self,
        project_id: str,
        inputs: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        resolved = self.resolve_project(project_id)
        selectable = {
            (
                _clean(item.get("dataset")).lower(),
                _clean(item.get("field")).lower(),
                _clean(item.get("frequency")).lower(),
            ): item
            for item in resolved["input_candidates"]
            if item.get("factor_selectable")
        }
        all_candidates = {
            (
                _clean(item.get("dataset")).lower(),
                _clean(item.get("field")).lower(),
                _clean(item.get("frequency")).lower(),
            ): item
            for item in resolved["input_candidates"]
        }
        if not selectable and resolved.get("diagnostics"):
            diagnostic = resolved["diagnostics"][0]
            raise ValueError(
                f"{_clean(diagnostic.get('code')) or 'FACTOR_INPUT_CANDIDATE_UNAVAILABLE'}: "
                f"{_clean(diagnostic.get('message')) or 'No Factor Input candidate is available'}"
            )
        selected: list[dict[str, Any]] = []
        for index, input_spec in enumerate(inputs):
            key = (
                _clean(input_spec.get("dataset") or "bars").lower(),
                _clean(input_spec.get("field")).lower(),
                _clean(input_spec.get("frequency")).lower(),
            )
            candidate = selectable.get(key)
            if candidate is None:
                unavailable = all_candidates.get(key)
                if (
                    unavailable
                    and _clean(unavailable.get("provider_status")).upper()
                    == "UNAVAILABLE"
                ):
                    raise ValueError(
                        "INPUT_PROVIDER_UNAVAILABLE: "
                        + (
                            _clean(unavailable.get("availability_reason"))
                            or "The historical-data provider for this Input is unavailable"
                        )
                    )
                raise ValueError(
                    "FACTOR_INPUT_CANDIDATE_UNAVAILABLE: "
                    f"inputs.{index} ({key[0]}.{key[1]} · {key[2]}) is not requestable "
                    "for every Instrument in the current Universe Snapshot"
                )
            selected.append({
                "variable_name": _clean(input_spec.get("variable_name")),
                **candidate,
            })
        return {
            "candidate_fingerprint": resolved["candidate_fingerprint"],
            "universe": resolved["universe"],
            "selected_inputs": selected,
        }

    def _instrument(self, instrument_id: str) -> dict[str, Any]:
        stored = self.registry.get(instrument_id) or {}
        parts = instrument_id.split(":", 2)
        asset_class = _clean(stored.get("asset_class") or (parts[0] if parts else "")).lower()
        venue = _upper(stored.get("venue") or (parts[1] if len(parts) > 1 else ""))
        symbol = _upper(stored.get("native_symbol") or (parts[2] if len(parts) > 2 else ""))
        quote = _upper(stored.get("quote_asset") or stored.get("currency"))
        if not quote and asset_class == "crypto_spot":
            quote = next((item for item in _KNOWN_QUOTES if symbol.endswith(item)), "")
        with self.store.connection() as conn:
            aliases = conn.execute(
                """
                SELECT source, source_symbol
                FROM instrument_aliases
                WHERE instrument_id=?
                ORDER BY source, source_symbol
                """,
                (instrument_id,),
            ).fetchall()
        provider_ids = [
            {"provider": _upper(row["source"]), "id": str(row["source_symbol"])}
            for row in aliases
        ]
        if not provider_ids and venue and symbol:
            provider_ids.append({"provider": venue, "id": symbol})
        return {
            "instrument_id": instrument_id,
            "asset_class": asset_class,
            "asset_type": {
                "crypto_spot": "Crypto Spot",
                "crypto_derivative": "Crypto Derivative",
                "equity": "Equity",
                "polymarket_binary": "Prediction Market",
            }.get(asset_class, asset_class.replace("_", " ").title()),
            "venue": venue,
            "symbol": symbol,
            "quote_currency": quote,
            "status": _upper(stored.get("status") or "ACTIVE"),
            "provider_ids": provider_ids,
            "provider_id_matched": bool(provider_ids),
        }

    @staticmethod
    def _instrument_summary(instruments: list[dict[str, Any]]) -> dict[str, Any]:
        def common(field: str) -> str:
            values = sorted({_clean(item.get(field)) for item in instruments if _clean(item.get(field))})
            return values[0] if len(values) == 1 else ("Mixed" if values else "")

        return {
            "member_count": len(instruments),
            "asset_type": common("asset_type"),
            "venue": common("venue"),
            "quote_currency": common("quote_currency"),
            "provider_id_matches": sum(bool(item["provider_id_matched"]) for item in instruments),
            "instruments": instruments,
        }

    def _datasets(self, instruments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        asset_classes = {
            _clean(item.get("asset_class")).lower()
            for item in instruments
        }
        datasets: list[dict[str, Any]] = []
        if asset_classes & {"crypto_spot", "crypto_derivative", "equity"}:
            bar_fields = list(_CRYPTO_BAR_FIELDS)
            if asset_classes <= {"equity"}:
                bar_fields = list(_EQUITY_BAR_FIELDS)
            elif "equity" in asset_classes:
                equity_names = {item[0] for item in _EQUITY_BAR_FIELDS}
                bar_fields = [item for item in _CRYPTO_BAR_FIELDS if item[0] in equity_names]
            bar_frequencies = ["1d"] if asset_classes <= {"equity"} else BINANCE_INTERVALS
            datasets.append(self._dataset(
                instruments,
                dataset="bars",
                label="Market Bars",
                fields=tuple(bar_fields),
                frequencies=bar_frequencies,
                provider_status=self._provider_status(instruments, "bars"),
            ))
        if "polymarket_binary" in asset_classes:
            datasets.append(self._dataset(
                instruments,
                dataset="price_history",
                label="Outcome Price History",
                fields=_PRICE_HISTORY_FIELDS,
                frequencies=POLYMARKET_INTERVALS,
                provider_status=self._provider_status(instruments, "price_history"),
            ))
        return datasets

    def _dataset(
        self,
        instruments: list[dict[str, Any]],
        *,
        dataset: str,
        label: str,
        fields: tuple[tuple[str, str], ...],
        frequencies: list[str],
        provider_status: dict[str, Any],
    ) -> dict[str, Any]:
        total = len(instruments)
        field_rows = []
        for field, field_label in fields:
            applicable = [
                item for item in instruments
                if self._applicable(item, dataset, field)
            ]
            frequency_rows = []
            for frequency in frequencies:
                requestable = [
                    item for item in applicable
                    if self._frequency_applicable(item, dataset, frequency)
                    and self._requestable(item, dataset, frequency)
                ]
                prepared = [
                    item for item in requestable
                    if self._prepared(item["instrument_id"], dataset, field, frequency)
                ]
                selectable = (
                    self._engine_supported(dataset, field, frequency)
                    and len(applicable) == total
                    and len(requestable) == total
                    and total > 0
                )
                if not applicable:
                    status = "NOT_APPLICABLE"
                elif len(requestable) == total:
                    status = "REQUESTABLE"
                elif requestable:
                    status = "PARTIAL"
                else:
                    status = "UNAVAILABLE"
                frequency_rows.append({
                    "candidate_id": f"{dataset}.{field}:{frequency}",
                    "dataset": dataset,
                    "dataset_label": label,
                    "field": field,
                    "field_label": field_label,
                    "frequency": frequency,
                    "applicable_instrument_count": len(applicable),
                    "requestable_instrument_count": len(requestable),
                    "prepared_instrument_count": len(prepared),
                    "instrument_count": total,
                    "status": status,
                    "factor_selectable": selectable,
                    "providers": list(provider_status.get("providers") or []),
                    "provider_status": provider_status.get("status") or "UNKNOWN",
                    "availability_reason": (
                        "" if selectable else
                        _clean(provider_status.get("reason"))
                        or "This field is not requestable for every Instrument in the current Universe."
                    ),
                    "not_applicable_reason": (
                        "Not applicable to the current Universe Instruments"
                        if not applicable else ""
                    ),
                    "factor_limitation": (
                        "" if selectable else
                        "The current Factor engine or data provider cannot prepare this candidate end to end."
                    ),
                })
            field_rows.append({
                "id": field,
                "label": field_label,
                "applicable_instrument_count": len(applicable),
                "instrument_count": total,
                "frequencies": frequency_rows,
            })
        return {
            "id": dataset,
            "label": label,
            "provider_status": provider_status,
            "fields": field_rows,
        }

    @staticmethod
    def _applicable(instrument: Mapping[str, Any], dataset: str, field: str) -> bool:
        asset_class = _clean(instrument.get("asset_class")).lower()
        if dataset == "bars":
            if asset_class == "equity":
                return field in {item[0] for item in _EQUITY_BAR_FIELDS}
            if asset_class in {"crypto_spot", "crypto_derivative"}:
                return field in {item[0] for item in _CRYPTO_BAR_FIELDS}
        if dataset == "price_history":
            return asset_class == "polymarket_binary" and field == "price"
        return False

    @staticmethod
    def _frequency_applicable(
        instrument: Mapping[str, Any],
        dataset: str,
        frequency: str,
    ) -> bool:
        asset_class = _clean(instrument.get("asset_class")).lower()
        if dataset == "bars" and asset_class == "equity":
            return frequency == "1d"
        if dataset == "bars" and asset_class in {"crypto_spot", "crypto_derivative"}:
            return frequency in BINANCE_INTERVALS
        if dataset == "price_history" and asset_class == "polymarket_binary":
            return frequency in POLYMARKET_INTERVALS
        return False

    @staticmethod
    def _engine_supported(dataset: str, field: str, frequency: str) -> bool:
        if frequency not in _FACTOR_FREQUENCIES:
            return False
        if dataset == "bars":
            return field in {item[0] for item in _CRYPTO_BAR_FIELDS}
        return dataset == "price_history" and field == "price"

    def _requestable(
        self,
        instrument: Mapping[str, Any],
        dataset: str,
        frequency: str,
    ) -> bool:
        instrument_id = _clean(instrument.get("instrument_id"))
        return self.capabilities.can_prepare(instrument_id, dataset, frequency)

    def _provider_status(
        self,
        instruments: list[Mapping[str, Any]],
        dataset: str,
    ) -> dict[str, Any]:
        asset_classes = {
            _clean(item.get("asset_class")).lower()
            for item in instruments
        }
        if dataset == "price_history" and asset_classes == {"polymarket_binary"}:
            return {
                "status": "READY",
                "providers": ["POLYMARKET"],
                "reason": "",
            }
        if dataset == "bars" and asset_classes <= {"crypto_spot", "crypto_derivative"}:
            return {
                "status": "READY",
                "providers": sorted({
                    _upper(item.get("venue")) or "BINANCE"
                    for item in instruments
                }),
                "reason": "",
            }
        if dataset == "bars" and asset_classes == {"equity"}:
            providers = [
                item for item in self.capabilities.describe().get("providers") or []
                if item.get("gateway") == "OPENBB"
                and any(
                    _upper(market.get("id")) in {
                        _upper(instrument.get("venue"))
                        for instrument in instruments
                    }
                    for market in item.get("markets") or []
                )
            ]
            ready = [
                item for item in providers
                if item.get("configured")
                and item.get("online")
                and any(market.get("prepare_supported") for market in item.get("markets") or [])
            ]
            if ready:
                return {
                    "status": "READY",
                    "providers": [str(item.get("id")) for item in ready],
                    "reason": "",
                }
            names = [str(item.get("id")) for item in providers] or ["OPENBB"]
            return {
                "status": "UNAVAILABLE",
                "providers": names,
                "reason": (
                    "The OpenBB historical-data gateway is offline or its configured "
                    "equity provider extension is unavailable. Daily OHLCV cannot be "
                    "prepared until that provider is ready."
                ),
            }
        if dataset == "bars" and asset_classes <= {
            "crypto_spot", "crypto_derivative", "equity"
        }:
            crypto_instruments = [
                item for item in instruments
                if _clean(item.get("asset_class")).lower() in {
                    "crypto_spot", "crypto_derivative"
                }
            ]
            equity_instruments = [
                item for item in instruments
                if _clean(item.get("asset_class")).lower() == "equity"
            ]
            common_frequency = "1d"
            individually_ready = all(
                self.capabilities.can_prepare(
                    _clean(item.get("instrument_id")), dataset, common_frequency
                )
                for item in instruments
            )
            described = self.capabilities.describe().get("providers") or []
            equity_providers = [
                str(item.get("id"))
                for item in described
                if item.get("gateway") == "OPENBB"
                and item.get("configured")
                and item.get("online")
                and any(market.get("prepare_supported") for market in item.get("markets") or [])
            ]
            providers = (
                (["BINANCE"] if crypto_instruments else [])
                + (equity_providers if equity_instruments else [])
            )
            if individually_ready:
                return {
                    "status": "READY",
                    "providers": list(dict.fromkeys(providers)),
                    "reason": "",
                }
            return {
                "status": "UNAVAILABLE",
                "providers": list(dict.fromkeys(providers)),
                "reason": (
                    "At least one Instrument has no ready historical-data source at "
                    "the common 1d frequency."
                ),
            }
        return {
            "status": "UNAVAILABLE",
            "providers": [],
            "reason": "No source combination covers every Instrument in this Universe.",
        }

    def _prepared(
        self,
        instrument_id: str,
        dataset: str,
        field: str,
        frequency: str,
    ) -> bool:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT fields_json, schema_version
                FROM dataset_catalog
                WHERE instrument_id=? AND lower(data_type)=? AND lower(frequency)=?
                  AND status='READY' AND quality_status!='FAIL'
                """,
                (instrument_id, dataset, frequency.lower()),
            ).fetchall()
        for row in rows:
            catalog_fields = set(json.loads(row["fields_json"] or "[]"))
            if not catalog_fields and "bars" in str(row["schema_version"]).lower():
                catalog_fields = {item[0] for item in _CRYPTO_BAR_FIELDS}
            if not catalog_fields and "polymarket_price" in str(row["schema_version"]).lower():
                catalog_fields = {"price"}
            if field in catalog_fields:
                return True
        return False
