from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from .instrument_registry import InstrumentRegistry, make_instrument_id
from .models import Instrument
from .store import DataPlatformStore, json_dumps, utc_now


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _date_text(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text.isdigit() and len(text) == 8:
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text[:10]).isoformat()


class EquitySecurityMasterService:
    """Stable US-equity identity and point-in-time alias resolution."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.registry = InstrumentRegistry(store)

    @staticmethod
    def security_id_for_permno(permno: Any) -> str:
        value = int(permno)
        if value <= 0:
            raise ValueError("PERMNO must be positive")
        return f"crsp:permno:{value}"

    @staticmethod
    def instrument_id_for_permno(permno: Any) -> str:
        return make_instrument_id("equity", "CRSP", str(int(permno)))

    def upsert(
        self,
        security: Mapping[str, Any],
        *,
        aliases: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        permno = int(security["permno"])
        security_id = _clean(security.get("security_id")) or self.security_id_for_permno(permno)
        valid_from = _date_text(security.get("valid_from"))
        valid_to = _date_text(security.get("valid_to"))
        if valid_from and valid_to and valid_from > valid_to:
            raise ValueError("security valid_from must not be after valid_to")
        now = utc_now()
        payload = {
            "security_id": security_id,
            "permno": permno,
            "permco": int(security["permco"]) if _clean(security.get("permco")) else None,
            "cik": _clean(security.get("cik")).zfill(10) if _clean(security.get("cik")) else "",
            "cusip": _clean(security.get("cusip")).upper(),
            "issuer_name": _clean(security.get("issuer_name")),
            "security_name": _clean(security.get("security_name")),
            "security_type": _clean(security.get("security_type")).upper(),
            "share_type": _clean(security.get("share_type")).upper(),
            "share_class": _clean(security.get("share_class")).upper(),
            "primary_exchange": _clean(security.get("primary_exchange")).upper(),
            "currency": _clean(security.get("currency") or "USD").upper(),
            "country": _clean(security.get("country") or "US").upper(),
            "valid_from": valid_from or None,
            "valid_to": valid_to or None,
            "active": 1 if bool(security.get("active", True)) else 0,
            "source": _clean(security.get("source") or "CRSP/CIZ"),
            "metadata": dict(security.get("metadata") or {}),
        }
        normalized_aliases: list[dict[str, str]] = []
        for alias in aliases:
            alias_type = _clean(alias.get("alias_type")).upper()
            alias_value = _clean(alias.get("alias_value")).upper()
            if not alias_type or not alias_value:
                continue
            alias_from = _date_text(alias.get("valid_from"))
            alias_to = _date_text(alias.get("valid_to"))
            if alias_from and alias_to and alias_from > alias_to:
                raise ValueError("alias valid_from must not be after valid_to")
            normalized_aliases.append(
                {
                    "alias_type": alias_type,
                    "alias_value": alias_value,
                    "valid_from": alias_from,
                    "valid_to": alias_to,
                    "source": _clean(alias.get("source") or payload["source"]),
                }
            )
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT permno FROM equity_security_master WHERE security_id=?",
                (security_id,),
            ).fetchone()
            if existing is not None and int(existing[0]) != permno:
                raise ValueError("security_id cannot be reassigned to another PERMNO")
            conn.execute(
                """
                INSERT INTO equity_security_master(
                    security_id,permno,permco,cik,cusip,issuer_name,security_name,
                    security_type,share_type,share_class,primary_exchange,currency,
                    country,valid_from,valid_to,active,source,metadata_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(security_id) DO UPDATE SET
                    permco=excluded.permco,
                    cik=CASE WHEN excluded.cik<>'' THEN excluded.cik ELSE equity_security_master.cik END,
                    cusip=CASE WHEN excluded.cusip<>'' THEN excluded.cusip ELSE equity_security_master.cusip END,
                    issuer_name=excluded.issuer_name,security_name=excluded.security_name,
                    security_type=excluded.security_type,share_type=excluded.share_type,
                    share_class=excluded.share_class,primary_exchange=excluded.primary_exchange,
                    currency=excluded.currency,country=excluded.country,
                    valid_from=COALESCE(excluded.valid_from,equity_security_master.valid_from),
                    valid_to=COALESCE(excluded.valid_to,equity_security_master.valid_to),
                    active=excluded.active,source=excluded.source,metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    payload["security_id"], payload["permno"], payload["permco"], payload["cik"],
                    payload["cusip"], payload["issuer_name"], payload["security_name"],
                    payload["security_type"], payload["share_type"], payload["share_class"],
                    payload["primary_exchange"], payload["currency"], payload["country"],
                    payload["valid_from"], payload["valid_to"], payload["active"], payload["source"],
                    json_dumps(payload["metadata"]), now, now,
                ),
            )
            for alias in normalized_aliases:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO equity_security_aliases(
                        security_id,alias_type,alias_value,valid_from,valid_to,source,created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        security_id, alias["alias_type"], alias["alias_value"],
                        alias["valid_from"], alias["valid_to"], alias["source"], now,
                    ),
                )

        ticker = next(
            (item["alias_value"] for item in normalized_aliases if item["alias_type"] == "TICKER"),
            str(permno),
        )
        instrument_id = self.instrument_id_for_permno(permno)
        self.registry.register(
            Instrument(
                instrument_id=instrument_id,
                asset_class="equity",
                venue="CRSP",
                market_type="EQUITY",
                native_symbol=str(permno),
                display_symbol=ticker,
                display_name=payload["security_name"] or payload["issuer_name"] or ticker,
                currency=payload["currency"],
                listing_time=payload["valid_from"],
                delisting_time=payload["valid_to"],
                timezone="America/New_York",
                trading_calendar="XNYS",
                status="ACTIVE" if payload["active"] else "INACTIVE",
                metadata={
                    "security_id": security_id,
                    "permno": permno,
                    "permco": payload["permco"],
                    "cik": payload["cik"],
                    "primary_exchange": payload["primary_exchange"],
                    "identity_source": payload["source"],
                },
            ),
            aliases=[("crsp:permno", str(permno))],
        )
        return {**payload, "instrument_id": instrument_id, "aliases": normalized_aliases}

    def get(self, security_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM equity_security_master WHERE security_id=?", (_clean(security_id),)
            ).fetchone()
            if row is None:
                return None
            aliases = conn.execute(
                """SELECT alias_type,alias_value,valid_from,valid_to,source
                   FROM equity_security_aliases WHERE security_id=?
                   ORDER BY alias_type,valid_from,alias_value""",
                (_clean(security_id),),
            ).fetchall()
        result = dict(row)
        result["aliases"] = [dict(item) for item in aliases]
        return result

    def link_cik(self, security_id: str, cik: Any, *, source: str = "SEC/CRSP_LINK") -> dict[str, Any]:
        normalized = _clean(cik).zfill(10)
        if not normalized.isdigit() or len(normalized) != 10:
            raise ValueError("CIK must contain at most 10 digits")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM equity_security_master WHERE security_id=?", (_clean(security_id),)
            ).fetchone()
            if row is None:
                raise ValueError("security not found")
            conn.execute(
                "UPDATE equity_security_master SET cik=?,updated_at=? WHERE security_id=?",
                (normalized, now, _clean(security_id)),
            )
            conn.execute(
                """INSERT OR IGNORE INTO equity_security_aliases(
                       security_id,alias_type,alias_value,valid_from,valid_to,source,created_at
                   ) VALUES (?,'CIK',?,'','',?,?)""",
                (_clean(security_id), normalized, _clean(source), now),
            )
        result = self.get(_clean(security_id))
        if result is None:
            raise RuntimeError("failed to persist CIK link")
        return result

    def resolve(self, alias_type: str, alias_value: str, *, as_of: str) -> list[dict[str, Any]]:
        cutoff = _date_text(as_of)
        if not cutoff:
            raise ValueError("as_of is required for point-in-time alias resolution")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT m.*,a.alias_type,a.alias_value,a.valid_from AS alias_valid_from,
                       a.valid_to AS alias_valid_to
                FROM equity_security_aliases AS a
                JOIN equity_security_master AS m ON m.security_id=a.security_id
                WHERE a.alias_type=? AND a.alias_value=?
                  AND (a.valid_from='' OR a.valid_from<=?)
                  AND (a.valid_to='' OR a.valid_to>=?)
                ORDER BY m.security_id
                """,
                (_clean(alias_type).upper(), _clean(alias_value).upper(), cutoff, cutoff),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_active(self, *, as_of: str) -> list[dict[str, Any]]:
        cutoff = _date_text(as_of)
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM equity_security_master
                WHERE (valid_from IS NULL OR valid_from<=?)
                  AND (valid_to IS NULL OR valid_to>=?)
                ORDER BY security_id
                """,
                (cutoff, cutoff),
            ).fetchall()
        return [dict(row) for row in rows]
