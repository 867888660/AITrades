from __future__ import annotations

import hashlib
import json
from typing import Any

from services.config_loader import load_web_settings
from services.data_source_definitions import (
    normalize_data_source_settings,
    openbb_equity_provider_sequence,
)

from .requirement_workspace_service import RequirementWorkspaceService
from .requirement_compiler import RequirementCompiler
from .research_control_plane import ResearchControlPlane
from .store import DataPlatformStore


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RequirementMaintenanceService:
    """Continuously turn Requirement coverage gaps into bounded provider tasks."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.workspace = RequirementWorkspaceService(store)
        self.compiler = RequirementCompiler(store)
        self.control = ResearchControlPlane(store)

    def run_once(self) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        # Library is the source of truth.  Assets are scanned even when no
        # Research currently references them.
        for asset in self.workspace.list_library_assets():
            asset_id = _clean(asset.get("library_asset_id"))
            for row in (asset.get("data_status") or {}).get("rows", []):
                candidates.append(
                    {
                        **dict(row),
                        "owner_kind": "LIBRARY",
                        "library_asset_id": asset_id,
                        "library_asset_ids": [asset_id] if asset_id else [],
                    }
                )

        # Derived Factor/Alpha requirements may not have a Library asset, so
        # active Research RequirementSets are scanned as a second source.
        for project in self.control.list_projects(limit=500):
            project_id = _clean(project.get("project_id"))
            active_sets = [
                item for item in self.compiler.list(project_id=project_id)
                if _clean(item.status).upper() != "SUPERSEDED"
            ]
            set_ids = [item.requirement_set_id for item in active_sets] or [""]
            for requirement_set_id in set_ids:
                try:
                    status = self.workspace.data_status(project_id, requirement_set_id)
                except Exception as exc:
                    errors.append({"owner_id": project_id, "error": str(exc)[:500]})
                    continue
                for row in status.get("rows", []):
                    candidates.append(
                        {
                            **dict(row),
                            "owner_kind": "RESEARCH",
                            "owner_project_id": project_id,
                        }
                    )

        scheduled: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate.get("can_prepare"):
                continue
            preparation = candidate.get("preparation") or {}
            if (
                _clean(preparation.get("status")).upper() == "FAILED"
                and int(preparation.get("maintenance_version") or 0) >= 3
            ):
                # Terminal provider errors stay visible in Library/Research;
                # a tight scheduler loop must not create endless retry tasks.
                continue
            try:
                spec = self._task_spec(candidate)
            except Exception as exc:
                errors.append(
                    {
                        "owner_id": _clean(
                            candidate.get("library_asset_id")
                            or candidate.get("owner_project_id")
                        ),
                        "error": str(exc)[:500],
                    }
                )
                continue
            if spec is None:
                continue
            dedupe_key = _clean(spec["idempotency_key"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            try:
                task = self.control.compile_maintenance_task(spec)
                scheduled.append(task)
            except Exception as exc:
                errors.append(
                    {
                        "owner_id": _clean(
                            candidate.get("library_asset_id")
                            or candidate.get("owner_project_id")
                        ),
                        "error": str(exc)[:500],
                    }
                )

        return {
            "scanned": len(candidates),
            "scheduled": len(scheduled),
            "task_types": sorted(
                {
                    _clean(task.get("task_type"))
                    for task in scheduled
                    if task.get("status") in {"READY", "RUNNING"}
                }
            ),
            "errors": errors,
        }

    def _task_spec(self, row: dict[str, Any]) -> dict[str, Any] | None:
        instrument_id = _clean(row.get("instrument_id"))
        interval = _clean(row.get("frequency")).lower()
        data_type = _clean(row.get("data_type")).upper()
        required_range = dict(row.get("required_range") or {})
        start_time = _clean(required_range.get("start"))
        end_value = required_range.get("end")
        end_time = _clean(required_range.get("resolved_end"))
        if not end_time and _clean(end_value).upper() != "LATEST_AVAILABLE":
            end_time = _clean(end_value)
        if not instrument_id or not interval or not start_time or not end_time:
            return None

        provider = _clean(row.get("provider")).lower()
        adjustment = _clean(row.get("adjustment") or "NONE").upper()
        material = {
            "scheduler_version": 3,
            "instrument_id": instrument_id.lower(),
            "data_type": data_type,
            "interval": interval,
            "start_time": start_time,
            "end_time": end_time,
            "provider": provider,
            "adjustment": adjustment,
        }
        digest = _fingerprint(material)
        common = {
            "maintenance_version": 3,
            "instrument_id": instrument_id,
            "interval": interval,
            "start_time": start_time,
            "end_time": end_time,
            "latest_available": _clean(end_value).upper() == "LATEST_AVAILABLE",
            "requirement_id": _clean(row.get("requirement_id")),
            "owner_project_id": _clean(row.get("owner_project_id")),
            "library_asset_id": _clean(row.get("library_asset_id")),
            "library_asset_ids": list(row.get("library_asset_ids") or []),
            "budget": {"download_bytes": 20_000_000, "runtime_seconds": 300},
        }
        base = {
            "workflow_run_id": f"requirement-maintenance:{digest[:24]}",
            "idempotency_key": digest,
            "logical_key": f"requirement-maintenance:{digest[:24]}",
            "priority": 60,
            "max_attempts": 3,
            "timeout_seconds": 900,
        }

        lowered = instrument_id.lower()
        if data_type == "BARS" and lowered.startswith("crypto_spot:binance:"):
            return {
                **base,
                "task_type": "BINANCE_BARS_BACKFILL",
                "input": {
                    **common,
                    "symbol": instrument_id.split(":")[-1].upper(),
                    "page_limit": 1000,
                    "max_pages_per_attempt": 500,
                },
            }
        if data_type == "BARS" and interval == "1d" and (
            lowered.startswith("equity:") or ":" not in instrument_id
        ):
            parts = instrument_id.split(":", 2)
            if len(parts) >= 3:
                venue, symbol = parts[1].upper(), parts[2].upper()
            else:
                # Grant scope authorizes bare tickers (e.g. "AAPL"); OpenBB
                # export still needs a venue, so default to XNAS.
                venue, symbol = "XNAS", instrument_id.upper()
            source_selection_policy = row.get("source_selection_policy")
            source_selection_policy = (
                dict(source_selection_policy)
                if isinstance(source_selection_policy, dict)
                else {}
            )
            mode = _clean(source_selection_policy.get("mode") or "FIXED").upper()
            preferred_sources = list(source_selection_policy.get("preferred_sources") or [])
            allowed_sources = list(source_selection_policy.get("allowed_sources") or [])
            source = provider if provider not in {"", "auto", "xnas", "xnys"} else "yfinance"
            settings = load_web_settings()
            if mode == "FIXED":
                providers = [str(preferred_sources[0] if preferred_sources else source).lower()]
            else:
                providers = openbb_equity_provider_sequence(
                    settings,
                    preferred_sources=preferred_sources,
                    allowed_sources=allowed_sources,
                )
                if not providers:
                    providers = [source]
            source_policy_mode = "PRIMARY_FALLBACK" if len(providers) > 1 else "FIXED"
            routing = normalize_data_source_settings(settings.get("data_source_settings"))
            openbb_digest = _fingerprint({
                **material,
                "providers": providers,
                "source_policy_mode": source_policy_mode,
                "data_source_routing_version": routing["version"],
            })
            return {
                **base,
                "workflow_run_id": f"requirement-maintenance:{openbb_digest[:24]}",
                "idempotency_key": openbb_digest,
                "logical_key": f"requirement-maintenance:{openbb_digest[:24]}",
                "task_type": "OPENBB_EQUITY_DAILY_EXPORT",
                "input": {
                    **common,
                    "provider": providers[0],
                    "venue": venue,
                    "symbol": symbol,
                    "frequency": interval,
                    "start_date": start_time[:10],
                    "end_date": end_time[:10],
                    "adjustment": adjustment,
                    "source_policy": {"mode": source_policy_mode, "providers": providers},
                    "data_source_routing_version": routing["version"],
                },
            }
        if data_type == "PRICE_HISTORY" and lowered.startswith(
            "polymarket_binary:polymarket:"
        ):
            return {
                **base,
                "task_type": "POLYMARKET_PRICE_HISTORY_EXPORT",
                "input": common,
            }
        return None
