from __future__ import annotations

import csv
import io
import math
import zipfile
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical_dataset import CanonicalDatasetCommitter
from .equity_security_master import EquitySecurityMasterService
from .store import DataPlatformStore


CRSP_CIZ_NORMALIZER_VERSION = "crsp_ciz_normalizer.v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if _clean(value):
            return value
    return None


def _day(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) == 8:
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text[:10])


def _number(value: Any, *, integer: bool = False) -> float | int | None:
    text = _clean(value)
    if not text or text.upper() in {"NA", "N/A", "NULL", ".", "B", "C"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if integer else number


def _stamp(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat()


def _available_after_close(day: date) -> str:
    return _stamp(day + timedelta(days=1))


class EquityDataQualityGate:
    """Fail closed on structural errors before a CRSP manifest is committed."""

    @staticmethod
    def inspect(outputs: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in outputs.get("bars", []):
            key = (str(row.get("instrument_id") or ""), str(row.get("bar_start_time") or ""))
            if key in seen:
                errors.append({"code": "DUPLICATE_BAR", "key": key})
            seen.add(key)
            if not key[0] or not key[1]:
                errors.append({"code": "MISSING_BAR_IDENTITY", "key": key})
            values = [row.get(name) for name in ("open", "high", "low", "close")]
            present = [value for value in values if value is not None]
            if present:
                high, low = row.get("high"), row.get("low")
                if high is not None and low is not None and float(high) < float(low):
                    errors.append({"code": "INVALID_HIGH_LOW", "key": key})
                for name in ("open", "close"):
                    value = row.get(name)
                    if value is not None and high is not None and low is not None:
                        if not float(low) <= float(value) <= float(high):
                            errors.append({"code": "INVALID_OHLC_RANGE", "field": name, "key": key})
            else:
                warnings.append({"code": "PRICE_FIELDS_ALL_NULL", "key": key})
            for name in ("volume", "trade_count"):
                value = row.get(name)
                if value is not None and float(value) < 0:
                    errors.append({"code": "NEGATIVE_ACTIVITY", "field": name, "key": key})
        for row in outputs.get("valuation", []):
            for name in ("market_cap", "shares_outstanding"):
                value = row.get(name)
                if value is not None and float(value) < 0:
                    errors.append(
                        {"code": "NEGATIVE_VALUATION", "field": name, "security_id": row.get("security_id")}
                    )
        return {
            "schema_version": "equity_data_quality_report.v1",
            "status": "PASS" if not errors else "FAIL",
            "row_counts": {key: len(value) for key, value in outputs.items()},
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors[:100],
            "warnings": warnings[:100],
        }


class CrspCizNormalizer:
    """Normalize CRSP Stock Version 2 (CIZ 2.0) rows without ticker look-ahead."""

    def __init__(
        self,
        store: DataPlatformStore,
        *,
        output_root: str | Path | None = None,
    ):
        self.store = store
        self.master = EquitySecurityMasterService(store)
        self.committer = CanonicalDatasetCommitter(store, output_root)

    def normalize_rows(self, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        securities: dict[str, dict[str, Any]] = {}
        bars: list[dict[str, Any]] = []
        valuation: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        source_rows = 0
        for raw in rows:
            source_rows += 1
            permno = _number(_pick(raw, "permno"), integer=True)
            event_day = _day(_pick(raw, "dlycaldt", "yyyymmdd", "date"))
            if permno is None or event_day is None:
                continue
            security_id = self.master.security_id_for_permno(permno)
            instrument_id = self.master.instrument_id_for_permno(permno)
            info_start = _day(_pick(raw, "secinfostartdt", "securitybegdt")) or event_day
            info_end = _day(_pick(raw, "secinfoenddt", "securityenddt"))
            security_start = _day(_pick(raw, "securitybegdt")) or info_start
            security_end = _day(_pick(raw, "securityenddt"))
            ticker = _clean(_pick(raw, "ticker", "tradingsymbol", "tsymbol")).upper()
            cusip = _clean(_pick(raw, "cusip9", "hdrcusip9", "cusip", "hdrcusip")).upper()
            security = {
                "security_id": security_id,
                "permno": int(permno),
                "permco": _number(_pick(raw, "permco"), integer=True),
                "cusip": cusip,
                "issuer_name": _clean(_pick(raw, "issuernm", "comnam")),
                "security_name": _clean(_pick(raw, "securitynm", "comnam")),
                "security_type": _clean(_pick(raw, "securitytype")),
                "share_type": _clean(_pick(raw, "sharetype")),
                "share_class": _clean(_pick(raw, "shareclass", "shrcls")),
                "primary_exchange": _clean(_pick(raw, "primaryexch", "primexch", "exchcd")),
                "valid_from": security_start.isoformat(),
                "valid_to": security_end.isoformat() if security_end else "",
                "active": _clean(_pick(raw, "securityactiveflg", "secstat")).upper() not in {"N", "I", "INACTIVE"},
                "source": "CRSP/CIZ",
                "metadata": {
                    "sic": _clean(_pick(raw, "siccd")),
                    "naics": _clean(_pick(raw, "naics")),
                },
            }
            alias_rows = []
            if ticker:
                alias_rows.append(
                    {
                        "alias_type": "TICKER",
                        "alias_value": ticker,
                        "valid_from": info_start.isoformat(),
                        "valid_to": info_end.isoformat() if info_end else "",
                        "source": "CRSP/CIZ",
                    }
                )
            if cusip:
                alias_rows.append(
                    {
                        "alias_type": "CUSIP",
                        "alias_value": cusip,
                        "valid_from": info_start.isoformat(),
                        "valid_to": info_end.isoformat() if info_end else "",
                        "source": "CRSP/CIZ",
                    }
                )
            current = securities.get(security_id)
            if current is None:
                securities[security_id] = {"security": security, "aliases": alias_rows}
            else:
                current["security"] = security
                known = {
                    (item["alias_type"], item["alias_value"], item["valid_from"], item["valid_to"])
                    for item in current["aliases"]
                }
                current["aliases"].extend(
                    item
                    for item in alias_rows
                    if (item["alias_type"], item["alias_value"], item["valid_from"], item["valid_to"]) not in known
                )

            def price(*names: str) -> float | None:
                value = _number(_pick(raw, *names))
                return abs(float(value)) if value is not None else None

            close = price("dlyclose", "dlyprc", "prc")
            bar = {
                "security_id": security_id,
                "instrument_id": instrument_id,
                "native_ticker": ticker,
                "frequency": "1d",
                "bar_start_time": _stamp(event_day),
                "bar_end_time": _available_after_close(event_day),
                "available_time": _available_after_close(event_day),
                "open": price("dlyopen", "openprc"),
                "high": price("dlyhigh", "askhi"),
                "low": price("dlylow", "bidlo"),
                "close": close,
                "volume": _number(_pick(raw, "dlyvol", "vol")),
                "turnover": _number(_pick(raw, "dlyprcvol")),
                "trade_count": _number(_pick(raw, "dlynumtrd", "numtrd"), integer=True),
                "total_return": _number(_pick(raw, "dlyret", "ret")),
                "price_return": _number(_pick(raw, "dlyretx", "retx")),
                "income_return": _number(_pick(raw, "dlyreti")),
                "price_adjustment_factor": _number(_pick(raw, "dlyfacprc", "cfacpr")),
                "bar_status": "COMPLETE",
                "source": "CRSP/CIZ",
                "quality_status": "PASS",
            }
            if any(bar[name] is not None for name in ("open", "high", "low", "close", "volume")):
                bars.append(bar)

            market_cap = _number(_pick(raw, "dlycap"))
            shares = _number(_pick(raw, "shrout"))
            if market_cap is not None or shares is not None:
                valuation.append(
                    {
                        "security_id": security_id,
                        "instrument_id": instrument_id,
                        "event_time": _stamp(event_day),
                        "available_time": _available_after_close(event_day),
                        "market_cap": market_cap,
                        "shares_outstanding": shares,
                        "capitalization_flag": _clean(_pick(raw, "dlycapflg")),
                        "shares_source": _clean(_pick(raw, "shrsource")),
                        "source": "CRSP/CIZ",
                    }
                )

            distribution_values = [
                _number(_pick(raw, name))
                for name in ("disdivamt", "dlyorddivamt", "dlynonorddivamt", "disfacpr", "disfacshr")
            ]
            has_distribution = bool(_day(_pick(raw, "disexdt"))) or any(
                value is not None and value != 0 for value in distribution_values
            )
            daily_delisting = _clean(_pick(raw, "dlydelflg")).upper() in {
                "Y", "1", "TRUE", "D", "DELISTED"
            }
            has_action = has_distribution or daily_delisting
            if has_action:
                action_day = _day(_pick(raw, "disexdt", "dlycaldt", "yyyymmdd")) or event_day
                declared = _day(_pick(raw, "disdeclaredt"))
                available_day = max(action_day, declared) if declared else action_day
                actions.append(
                    {
                        "security_id": security_id,
                        "instrument_id": instrument_id,
                        "event_time": _stamp(action_day),
                        "available_time": _stamp(available_day),
                        "action_type": _clean(
                            _pick(raw, "distype", "disdetailtype")
                            if has_distribution
                            else _pick(raw, "delactiontype")
                        ),
                        "cash_dividend": _number(_pick(raw, "disdivamt", "dlyorddivamt")),
                        "nonordinary_dividend": _number(_pick(raw, "dlynonorddivamt")),
                        "price_factor": _number(_pick(raw, "disfacpr")),
                        "share_factor": _number(_pick(raw, "disfacshr")),
                        "declared_date": declared.isoformat() if declared else "",
                        "record_date": (_day(_pick(raw, "disrecorddt")) or "").isoformat() if _day(_pick(raw, "disrecorddt")) else "",
                        "payment_date": (_day(_pick(raw, "dispaydt")) or "").isoformat() if _day(_pick(raw, "dispaydt")) else "",
                        "source": "CRSP/CIZ",
                    }
                )

        master_rows: list[dict[str, Any]] = []
        for item in securities.values():
            persisted = self.master.upsert(item["security"], aliases=item["aliases"])
            start = persisted.get("valid_from") or "1900-01-01"
            master_rows.append(
                {
                    "security_id": persisted["security_id"],
                    "instrument_id": persisted["instrument_id"],
                    "permno": persisted["permno"],
                    "permco": persisted["permco"],
                    "cik": persisted["cik"],
                    "cusip": persisted["cusip"],
                    "issuer_name": persisted["issuer_name"],
                    "security_name": persisted["security_name"],
                    "primary_exchange": persisted["primary_exchange"],
                    "valid_from": persisted["valid_from"] or "",
                    "valid_to": persisted["valid_to"] or "",
                    "aliases": "|".join(
                        f"{alias['alias_type']}:{alias['alias_value']}:{alias['valid_from']}:{alias['valid_to']}"
                        for alias in persisted["aliases"]
                    ),
                    "event_time": _stamp(date.fromisoformat(start)),
                    "available_time": _stamp(date.fromisoformat(start)),
                    "source": "CRSP/CIZ",
                }
            )
        outputs = {
            "security_master": master_rows,
            "bars": bars,
            "valuation": valuation,
            "corporate_actions": actions,
        }
        quality = EquityDataQualityGate.inspect(outputs)
        return {"source_row_count": source_rows, "outputs": outputs, "quality": quality}

    def normalize_csv(self, path: str | Path, *, max_rows: int = 0) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        def rows() -> Iterable[Mapping[str, Any]]:
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                for index, row in enumerate(csv.DictReader(handle), start=1):
                    if max_rows and index > max_rows:
                        break
                    yield row

        result = self.normalize_rows(rows())
        result["source_path"] = str(source)
        return result

    def normalize_zip(
        self,
        path: str | Path,
        *,
        entry_name: str = "",
        max_rows: int = 0,
    ) -> dict[str, Any]:
        """Stream a CIZ CSV directly from ZIP without materializing the 60GB CSV."""
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        archive = zipfile.ZipFile(source)
        csv_entries = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        selected = entry_name or (csv_entries[0] if len(csv_entries) == 1 else "")
        if not selected or selected not in csv_entries:
            archive.close()
            raise ValueError("CRSP ZIP requires one explicit CSV entry")

        def rows() -> Iterable[Mapping[str, Any]]:
            try:
                with archive.open(selected) as binary:
                    text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                    for index, row in enumerate(csv.DictReader(text), start=1):
                        if max_rows and index > max_rows:
                            break
                        yield row
            finally:
                archive.close()

        result = self.normalize_rows(rows())
        result["source_path"] = str(source)
        result["source_entry"] = selected
        return result

    def commit(self, normalized: Mapping[str, Any], *, dataset_prefix: str = "crsp:ciz") -> dict[str, Any]:
        quality = dict(normalized.get("quality") or {})
        if quality.get("status") != "PASS":
            raise ValueError(f"CRSP quality gate failed with {quality.get('error_count', 0)} errors")
        outputs = dict(normalized.get("outputs") or {})
        contracts = (
            ("security_master", "security_master", "security_master.v1", "event_time", "event"),
            ("bars", "bars", "bars_daily.v2", "bar_start_time", "1d"),
            ("valuation", "equity_valuation_daily", "equity_valuation_daily.v1", "event_time", "1d"),
            ("corporate_actions", "corporate_actions", "corporate_actions.v1", "event_time", "event"),
        )
        committed: dict[str, Any] = {}
        for key, data_type, schema_version, event_field, frequency in contracts:
            rows = list(outputs.get(key) or [])
            if not rows:
                continue
            result = self.committer.commit(
                dataset_id=f"{dataset_prefix}:{data_type}",
                instrument_id="equity:CRSP:ALL",
                data_type=data_type,
                frequency=frequency,
                source="CRSP/CIZ",
                source_version=CRSP_CIZ_NORMALIZER_VERSION,
                schema_version=schema_version,
                rows=rows,
                event_time_field=event_field,
                point_in_time_policy="AS_OF",
                adjustment="CRSP_FIELDS" if key == "bars" else "NONE",
                metadata={"quality_report": quality, "vwap_included": False},
            )
            committed[key] = {
                "dataset_id": result["dataset_id"],
                "row_count": result["row_count"],
                "catalog": asdict(result["catalog"]),
                "manifest": asdict(result["manifest"]),
            }
        return {"status": "READY", "quality": quality, "datasets": committed}
