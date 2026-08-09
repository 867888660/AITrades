from __future__ import annotations

import hashlib
import json
from typing import Any

from .store import DataPlatformStore, json_dumps, utc_now


SENSITIVE_FRAGMENTS = ("api_key", "apikey", "token", "secret", "password", "authorization")


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def sanitized_request(payload: dict[str, Any]) -> dict[str, Any]:
    excluded = {"grant_id", "budget", "source_policy"}
    return _redact({key: value for key, value in payload.items() if key not in excluded})


def request_hash(payload: dict[str, Any]) -> str:
    clean = sanitized_request(payload)
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class ManifestProvenanceService:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def record(
        self,
        *,
        manifest_id: str,
        dataset_id: str,
        gateway: str,
        upstream_provider: str,
        endpoint: str,
        request: dict[str, Any],
        original_publisher: str = "",
        gateway_version: str = "",
        provider_version: str = "",
        source_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_request = sanitized_request(request)
        digest = request_hash(request)
        now = utc_now()
        values = (
            manifest_id, dataset_id, gateway.upper(), upstream_provider.lower(), original_publisher,
            endpoint, gateway_version, provider_version, digest, json_dumps(clean_request),
            json_dumps(source_policy or {"mode": "FIXED", "providers": [upstream_provider.lower()]}), now,
        )
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM dataset_manifest_provenance WHERE manifest_id = ?", (manifest_id,)
            ).fetchone()
            if existing:
                expected = values[:-1]
                actual = tuple(existing[key] for key in (
                    "manifest_id", "dataset_id", "gateway", "upstream_provider", "original_publisher",
                    "endpoint", "gateway_version", "provider_version", "request_hash", "request_json", "source_policy_json",
                ))
                if actual != expected:
                    raise ValueError("manifest provenance is immutable")
            else:
                conn.execute(
                    """
                    INSERT INTO dataset_manifest_provenance(
                        manifest_id, dataset_id, gateway, upstream_provider, original_publisher,
                        endpoint, gateway_version, provider_version, request_hash, request_json,
                        source_policy_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        return self.get(manifest_id)  # type: ignore[return-value]

    def get(self, manifest_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_manifest_provenance WHERE manifest_id = ?", (manifest_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["source_policy"] = json.loads(result.pop("source_policy_json"))
        return result
