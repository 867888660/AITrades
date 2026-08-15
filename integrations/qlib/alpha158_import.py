from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.data_platform import FrozenManifestData
from services.data_platform.factor_pack import (
    ALPHA158_NO_VWAP_DISPLAY_NAME,
    ALPHA158_NO_VWAP_EXCLUDED_FACTORS,
    ALPHA158_NO_VWAP_FACTOR_COUNT,
    ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS,
    ALPHA158_NO_VWAP_PACK_ID,
    ALPHA158_NO_VWAP_REQUIRED_FIELDS,
)
from services.data_platform.store import BASE_DIR, DataPlatformStore, get_default_store, json_dumps


IMPORTER_VERSION = "datatube-qlib-alpha158.v2"
FACTOR_CACHE_SCHEMA_VERSION = "factor-pack-cache.v1"
FACTOR_FRAME_SCHEMA_VERSION = "factor-frame.wide.v1"
MINIMUM_HISTORY_BARS = ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS
REQUIRED_FIELDS = ALPHA158_NO_VWAP_REQUIRED_FIELDS
EXCLUDED_FACTORS = ALPHA158_NO_VWAP_EXCLUDED_FACTORS


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("bar timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_text(value: Any) -> str:
    return _parse_time(value).date().isoformat()


def _finite_number(value: Any, *, field: str, instrument_id: str, event_date: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{instrument_id} has invalid {field} at {event_date}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{instrument_id} has non-finite {field} at {event_date}")
    if field == "volume":
        if result < 0:
            raise ValueError(f"{instrument_id} has negative volume at {event_date}")
    elif result <= 0:
        raise ValueError(f"{instrument_id} has non-positive {field} at {event_date}")
    return result


def _load_qlib_components() -> tuple[Any, Any, Any, str]:
    try:
        import qlib
        from qlib.config import REG_US
        from qlib.contrib.data.loader import Alpha158DL
    except ImportError as exc:
        raise RuntimeError(
            "Qlib Alpha158 requires the optional Qlib runtime. Install "
            "requirements-qlib.txt and run this command with that Python environment."
        ) from exc
    return qlib, REG_US, Alpha158DL, str(getattr(qlib, "__version__", "unknown"))


def alpha158_no_vwap_feature_config(loader: Any | None = None) -> tuple[list[str], list[str]]:
    """Return Qlib's official Alpha158 expression set with only VWAP0 removed."""
    if loader is None:
        _, _, loader, _ = _load_qlib_components()
    fields, names = loader.get_feature_config(
        {
            "kbar": {},
            "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW"]},
            "rolling": {},
        }
    )
    fields = list(fields)
    names = list(names)
    if len(fields) != len(names):
        raise RuntimeError("Qlib Alpha158 returned mismatched expressions and names")
    if len(names) != ALPHA158_NO_VWAP_FACTOR_COUNT:
        raise RuntimeError(
            f"Qlib Alpha158 compatibility contract changed: expected "
            f"{ALPHA158_NO_VWAP_FACTOR_COUNT} factors, got {len(names)}"
        )
    if "VWAP0" in names or len(set(names)) != len(names):
        raise RuntimeError("Qlib Alpha158 no-VWAP factor names are invalid")
    return fields, names


def _normalize_rows(
    rows_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    if not rows_by_instrument:
        raise ValueError("Alpha158 import requires at least one instrument")
    for instrument_id, raw_rows in sorted(rows_by_instrument.items()):
        instrument_id = str(instrument_id).strip()
        if not instrument_id:
            raise ValueError("Alpha158 import found an empty instrument_id")
        seen_dates: set[str] = set()
        rows: list[dict[str, Any]] = []
        issue_counts: dict[str, int] = {}
        first_issue_dates: dict[str, str] = {}
        last_issue_dates: dict[str, str] = {}
        for raw in raw_rows:
            event_date = _date_text(
                raw.get("bar_start_time") or raw.get("event_time") or raw.get("date")
            )
            if event_date in seen_dates:
                raise ValueError(f"duplicate daily bar for {instrument_id}: {event_date}")
            seen_dates.add(event_date)
            values: dict[str, float] = {}
            invalid_row = False
            for field in REQUIRED_FIELDS:
                try:
                    values[field] = _finite_number(
                        raw.get(field), field=field, instrument_id=instrument_id, event_date=event_date
                    )
                except ValueError:
                    issue_counts[field] = issue_counts.get(field, 0) + 1
                    first_issue_dates.setdefault(field, event_date)
                    last_issue_dates[field] = event_date
                    invalid_row = True
            if invalid_row:
                continue
            high = values["high"]
            low = values["low"]
            if high < low or not low <= values["open"] <= high or not low <= values["close"] <= high:
                issue_counts["ohlc_range"] = issue_counts.get("ohlc_range", 0) + 1
                first_issue_dates.setdefault("ohlc_range", event_date)
                last_issue_dates["ohlc_range"] = event_date
                continue
            rows.append({"date": event_date, **values})
        if issue_counts:
            raise ValueError(
                f"{instrument_id} does not satisfy Alpha158 OHLCV coverage; "
                f"invalid_rows_by_field={dict(sorted(issue_counts.items()))}; "
                f"first_invalid_date_by_field={dict(sorted(first_issue_dates.items()))}; "
                f"last_invalid_date_by_field={dict(sorted(last_issue_dates.items()))}"
            )
        rows.sort(key=lambda item: item["date"])
        if len(rows) < MINIMUM_HISTORY_BARS:
            raise ValueError(
                f"{instrument_id} has {len(rows)} daily bars; Alpha158-compatible "
                f"requires at least {MINIMUM_HISTORY_BARS}"
            )
        normalized[instrument_id] = rows
    return normalized


def _write_float32_file(path: Path, values: Sequence[float], *, start_index: int = 0) -> None:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Qlib dataset materialization requires numpy") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray([float(start_index), *values], dtype="<f4")
    with path.open("wb") as handle:
        data.tofile(handle)


def materialize_qlib_dataset(
    rows_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
    provider_uri: str | Path,
) -> dict[str, Any]:
    """Write a minimal, isolated Qlib file-provider dataset from canonical bars."""
    normalized = _normalize_rows(rows_by_instrument)
    root = Path(provider_uri)
    calendar = sorted({row["date"] for rows in normalized.values() for row in rows})
    calendar_index = {day: index for index, day in enumerate(calendar)}
    symbol_map = {
        f"DT{index:06d}": instrument_id
        for index, instrument_id in enumerate(sorted(normalized), start=1)
    }

    calendars_dir = root / "calendars"
    instruments_dir = root / "instruments"
    features_dir = root / "features"
    calendars_dir.mkdir(parents=True, exist_ok=True)
    instruments_dir.mkdir(parents=True, exist_ok=True)
    features_dir.mkdir(parents=True, exist_ok=True)
    (calendars_dir / "day.txt").write_text("\n".join(calendar) + "\n", encoding="utf-8")

    instrument_lines: list[str] = []
    for symbol, instrument_id in symbol_map.items():
        indexed = {calendar_index[row["date"]]: row for row in normalized[instrument_id]}
        populated = sorted(indexed)
        start_index = populated[0]
        end_index = populated[-1]
        instrument_lines.append(
            f"{symbol}\t{calendar[start_index]}\t{calendar[end_index]}"
        )
        for field in REQUIRED_FIELDS:
            values = [
                float(indexed[index][field]) if index in indexed else float("nan")
                for index in range(start_index, end_index + 1)
            ]
            _write_float32_file(
                features_dir / symbol.lower() / f"{field}.day.bin",
                values,
                start_index=start_index,
            )
    (instruments_dir / "all.txt").write_text(
        "\n".join(instrument_lines) + "\n", encoding="utf-8"
    )
    (root / "datatube_symbol_map.json").write_text(
        json.dumps(symbol_map, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return {
        "calendar": calendar,
        "symbol_map": symbol_map,
        "instrument_count": len(symbol_map),
        "bar_count": sum(len(rows) for rows in normalized.values()),
    }


def _calculate_with_qlib(
    *,
    provider_uri: Path,
    factor_path: Path,
    symbol_map: Mapping[str, str],
    calendar: Sequence[str],
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    qlib, region, loader, qlib_version = _load_qlib_components()
    fields, factor_names = alpha158_no_vwap_feature_config(loader)
    # The temporary file-provider directory contains only the frozen DataTube
    # input. Qlib is an expression engine here and never downloads market data.
    # qlib.init() also registers experiment tracking and therefore imports
    # MLflow. This integration intentionally initializes only Qlib's data and
    # expression runtime, keeping Dataset/Workflow/Recorder outside DataTube.
    from qlib.config import C
    from qlib.data.cache import H
    from qlib.data.data import register_all_wrappers
    from qlib.data.ops import register_all_ops

    H.clear()
    C.set(
        "client",
        provider_uri=str(provider_uri),
        region=region,
        expression_cache=None,
        dataset_cache=None,
    )
    register_all_ops(C)
    register_all_wrappers(C)
    # DatasetProvider invokes register_from_C even for a one-worker local
    # calculation. Mark this deliberately minimal registration as complete so
    # it does not fall through to Workflow/MLflow registration.
    C.__dict__["_registered"] = True
    from qlib.data import D

    raw = D.features(
        list(symbol_map),
        fields,
        start_time=start_time or calendar[0],
        end_time=end_time or calendar[-1],
        freq="day",
    )
    if len(raw.columns) != len(factor_names):
        raise RuntimeError(
            f"Qlib returned {len(raw.columns)} columns for {len(factor_names)} factors"
        )
    raw.columns = factor_names
    frame = raw.reset_index()
    index_columns = list(frame.columns[:2])
    instrument_column = next(
        (item for item in index_columns if str(item).lower() == "instrument"),
        index_columns[0],
    )
    datetime_column = next(
        (item for item in index_columns if str(item).lower() == "datetime"),
        index_columns[1],
    )
    frame[instrument_column] = frame[instrument_column].map(symbol_map)
    if frame[instrument_column].isna().any():
        raise RuntimeError("Qlib output contains an unknown temporary symbol")
    frame = frame.rename(
        columns={instrument_column: "instrument", datetime_column: "datetime"}
    )
    frame = frame[["datetime", "instrument", *factor_names]].sort_values(
        ["datetime", "instrument"]
    )
    factor_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(factor_path, index=False)
    return {
        "qlib_version": qlib_version,
        "factor_names": factor_names,
        "row_count": int(len(frame)),
        "instrument_count": int(frame["instrument"].nunique()),
        "start_time": str(frame["datetime"].min()) if len(frame) else "",
        "end_time": str(frame["datetime"].max()) if len(frame) else "",
    }


class Alpha158ImportService:
    """Compute and cache Qlib's 157 non-VWAP Alpha158 factors from Manifests."""

    def __init__(
        self,
        store: DataPlatformStore | None = None,
        *,
        output_root: str | Path | None = None,
        frozen_factory: Callable[[DataPlatformStore, str], FrozenManifestData] = FrozenManifestData,
    ):
        self.store = store or get_default_store()
        self.output_root = Path(
            output_root
            or BASE_DIR / "storage" / "factor_cache" / "qlib" / "alpha158_without_vwap"
        )
        self.frozen_factory = frozen_factory

    def _resolve_inputs(
        self,
        manifest_ids: Sequence[str],
        *,
        instrument_ids: Sequence[str] = (),
    ) -> tuple[list[tuple[Any, Any, str, bool]], list[dict[str, Any]], list[str]]:
        unique_ids = sorted({str(item).strip() for item in manifest_ids if str(item).strip()})
        if not unique_ids:
            raise ValueError("manifest_ids is required")
        resolved: list[tuple[Any, Any, str, bool]] = []
        descriptors: list[dict[str, Any]] = []
        adjustments: set[str] = set()
        selected_instruments = sorted({str(item).strip() for item in instrument_ids if str(item).strip()})
        for manifest_id in unique_ids:
            frozen = self.frozen_factory(self.store, manifest_id)
            descriptor = dict(frozen.descriptor())
            if descriptor.get("schema_version") not in {"bars.v1", "bars_daily.v2"}:
                raise ValueError(
                    f"Alpha158 requires bars.v1 or bars_daily.v2 Manifest: {manifest_id}"
                )
            catalog = frozen.catalog.get_catalog(frozen.dataset_id)
            if catalog is None:
                raise ValueError(f"catalog entry is missing for Manifest: {manifest_id}")
            if str(catalog.data_type).lower() != "bars" or str(catalog.frequency).lower() != "1d":
                raise ValueError(f"Alpha158 requires daily bars: {manifest_id}")
            instrument_id = str(catalog.instrument_id).strip()
            if not instrument_id.lower().startswith("equity:"):
                raise ValueError(f"Alpha158 stock MVP requires equity instruments: {instrument_id}")
            is_collection = bool(catalog.metadata.get("full_import")) or instrument_id.upper().endswith(":ALL")
            if is_collection and not selected_instruments:
                raise ValueError(
                    "Alpha158 collection Manifest requires explicit row-level instrument_ids; "
                    "the Catalog collection id must not be treated as one security"
                )
            adjustment = str(catalog.adjustment or "NONE").upper()
            adjustments.add(adjustment)
            resolved.append((frozen, catalog, instrument_id, is_collection))
            descriptors.append(
                {
                    **descriptor,
                    "instrument_id": instrument_id,
                    "source": str(catalog.source),
                    "frequency": str(catalog.frequency),
                    "adjustment": adjustment,
                    "instrument_scope": "COLLECTION" if is_collection else "SINGLE",
                    "selected_instrument_ids": selected_instruments if is_collection else [instrument_id],
                }
            )
        if len(adjustments) != 1:
            raise ValueError(
                f"Alpha158 input Manifests must use one adjustment policy, got {sorted(adjustments)}"
            )
        return resolved, descriptors, sorted(adjustments)

    def _load_inputs(
        self,
        manifest_ids: Sequence[str],
        *,
        instrument_ids: Sequence[str] = (),
        start_time: str = "",
        end_time: str = "",
        resolved_inputs: Sequence[tuple[Any, Any, str, bool]] | None = None,
        descriptors: Sequence[Mapping[str, Any]] | None = None,
        adjustments: Sequence[str] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
        selected_instruments = sorted({str(item).strip() for item in instrument_ids if str(item).strip()})
        if resolved_inputs is None or descriptors is None or adjustments is None:
            resolved_inputs, descriptor_values, adjustment_values = self._resolve_inputs(
                manifest_ids, instrument_ids=selected_instruments
            )
            descriptors = descriptor_values
            adjustments = adjustment_values
        rows_by_instrument: dict[str, list[dict[str, Any]]] = {}
        for frozen, _catalog, instrument_id, is_collection in resolved_inputs:
            rows = frozen.iter_rows(
                columns=["instrument_id", "bar_start_time", *REQUIRED_FIELDS],
                start_time=start_time or None,
                end_time=end_time or None,
                instrument_ids=selected_instruments if is_collection else None,
            )
            seen_from_manifest: set[str] = set()
            for row in rows:
                row_instrument = str(row.get("instrument_id") or instrument_id).strip()
                if selected_instruments and row_instrument not in selected_instruments:
                    continue
                rows_by_instrument.setdefault(row_instrument, []).append(row)
                seen_from_manifest.add(row_instrument)
            if not is_collection:
                if instrument_id in seen_from_manifest and len(seen_from_manifest) > 1:
                    raise ValueError(
                        f"single-instrument Manifest contains multiple instruments: {frozen.manifest_id}"
                    )
            missing_selected = set(selected_instruments) - seen_from_manifest if is_collection else set()
            if missing_selected:
                raise ValueError(
                    f"collection Manifest has no rows for selected instruments: {sorted(missing_selected)}"
                )
        return _normalize_rows(rows_by_instrument), [dict(item) for item in descriptors], list(adjustments)

    @staticmethod
    def _cache_id(
        *,
        descriptors: Sequence[Mapping[str, Any]],
        adjustment: str,
        input_bundle_id: str,
        instrument_ids: Sequence[str],
        start_time: str,
        end_time: str,
        qlib_version: str,
    ) -> str:
        identity = {
            "pack_id": ALPHA158_NO_VWAP_PACK_ID,
            "importer_version": IMPORTER_VERSION,
            "qlib_version": qlib_version,
            "input_bundle_id": str(input_bundle_id or ""),
            "instrument_ids": sorted({str(item) for item in instrument_ids}),
            "start_time": str(start_time or ""),
            "end_time": str(end_time or ""),
            "adjustment": adjustment,
            "manifests": [
                {
                    "manifest_id": item["manifest_id"],
                    "manifest_hash": item["manifest_hash"],
                }
                for item in sorted(descriptors, key=lambda value: str(value["manifest_id"]))
            ],
        }
        return hashlib.sha256(json_dumps(identity).encode("utf-8")).hexdigest()

    @staticmethod
    def _read_valid_cache(cache_dir: Path, cache_id: str) -> dict[str, Any] | None:
        manifest_path = cache_dir / "manifest.json"
        factor_path = cache_dir / "factors.parquet"
        if not manifest_path.is_file() or not factor_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if manifest.get("cache_id") != cache_id:
            return None
        if (
            manifest.get("schema_version") != FACTOR_CACHE_SCHEMA_VERSION
            or manifest.get("pack_id") != ALPHA158_NO_VWAP_PACK_ID
            or manifest.get("factor_count") != ALPHA158_NO_VWAP_FACTOR_COUNT
        ):
            return None
        expected = str(manifest.get("output", {}).get("sha256") or "")
        if not expected or _sha256_file(factor_path) != expected:
            return None
        return {
            "status": "READY",
            "cache_hit": True,
            "cache_id": cache_id,
            "cache_dir": str(cache_dir),
            "factor_path": str(factor_path),
            "manifest_path": str(manifest_path),
            "manifest": manifest,
        }

    def run(
        self,
        *,
        manifest_ids: Sequence[str],
        input_bundle_id: str = "",
        instrument_ids: Sequence[str] = (),
        start_time: str = "",
        end_time: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        requested_start = date.fromisoformat(start_time[:10]).isoformat() if start_time else ""
        requested_end = date.fromisoformat(end_time[:10]).isoformat() if end_time else ""
        if requested_start and requested_end and requested_start > requested_end:
            raise ValueError("start_time must not be after end_time")
        # Alpha158 has rolling windows up to 60 sessions. Keep a conservative
        # calendar overlap while still pushing the 80-year range into Parquet.
        read_start = (
            (date.fromisoformat(requested_start) - timedelta(days=120)).isoformat()
            if requested_start else ""
        )
        resolved_inputs, descriptors, adjustments = self._resolve_inputs(
            manifest_ids, instrument_ids=instrument_ids
        )
        _, _, _, qlib_version = _load_qlib_components()
        cache_id = self._cache_id(
            descriptors=descriptors,
            adjustment=adjustments[0],
            input_bundle_id=input_bundle_id,
            instrument_ids=instrument_ids,
            start_time=requested_start,
            end_time=requested_end,
            qlib_version=qlib_version,
        )
        cache_dir = self.output_root / cache_id
        if not force:
            cached = self._read_valid_cache(cache_dir, cache_id)
            if cached:
                return cached

        rows_by_instrument, _descriptors, _adjustments = self._load_inputs(
            manifest_ids,
            instrument_ids=instrument_ids,
            start_time=read_start,
            end_time=requested_end,
            resolved_inputs=resolved_inputs,
            descriptors=descriptors,
            adjustments=adjustments,
        )

        self.output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{cache_id}.", dir=self.output_root))
        try:
            qlib_data = staging / "qlib_data"
            materialized = materialize_qlib_dataset(rows_by_instrument, qlib_data)
            factor_path = staging / "factors.parquet"
            calculation = _calculate_with_qlib(
                provider_uri=qlib_data,
                factor_path=factor_path,
                symbol_map=materialized["symbol_map"],
                calendar=materialized["calendar"],
                start_time=requested_start,
                end_time=requested_end,
            )
            factor_hash = _sha256_file(factor_path)
            manifest = {
                "schema_version": FACTOR_CACHE_SCHEMA_VERSION,
                "factor_frame_schema_version": FACTOR_FRAME_SCHEMA_VERSION,
                "cache_id": cache_id,
                "status": "READY",
                "provider": "qlib",
                "pack_id": ALPHA158_NO_VWAP_PACK_ID,
                "display_name": ALPHA158_NO_VWAP_DISPLAY_NAME,
                "compatibility_mode": "VWAP_EXCLUDED",
                "is_standard_alpha158": False,
                "factor_count": ALPHA158_NO_VWAP_FACTOR_COUNT,
                "factor_names": calculation["factor_names"],
                "excluded_factors": list(EXCLUDED_FACTORS),
                "requirements": {
                    "asset_class": "equity",
                    "frequency": "1d",
                    "fields": list(REQUIRED_FIELDS),
                    "minimum_history_bars": MINIMUM_HISTORY_BARS,
                    "adjustment": adjustments[0],
                },
                "input_bundle_id": str(input_bundle_id or ""),
                "instrument_ids": sorted({str(item) for item in instrument_ids}),
                "input_manifests": descriptors,
                "requested_range": {"start_time": requested_start, "end_time": requested_end},
                "engine": {
                    "name": "qlib",
                    "qlib_version": qlib_version,
                    "importer_version": IMPORTER_VERSION,
                    "python_version": sys.version.split()[0],
                },
                "output": {
                    "file": "factors.parquet",
                    "sha256": factor_hash,
                    "row_count": calculation["row_count"],
                    "instrument_count": calculation["instrument_count"],
                    "start_time": calculation["start_time"],
                    "end_time": calculation["end_time"],
                },
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            shutil.rmtree(qlib_data)
            if cache_dir.exists():
                cached = self._read_valid_cache(cache_dir, cache_id)
                if cached:
                    if cached["manifest"]["output"]["sha256"] != factor_hash:
                        raise RuntimeError(
                            "Qlib recomputation was non-deterministic for an existing cache identity"
                        )
                    shutil.rmtree(staging)
                    return cached
                raise RuntimeError(
                    f"immutable Qlib cache exists but failed verification: {cache_dir}"
                )
            os.replace(staging, cache_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {
            "status": "READY",
            "cache_hit": False,
            "cache_id": cache_id,
            "cache_dir": str(cache_dir),
            "factor_path": str(cache_dir / "factors.parquet"),
            "manifest_path": str(cache_dir / "manifest.json"),
            "manifest": manifest,
        }
