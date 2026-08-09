from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import (
    CanonicalBarsCommitter,
    DataPlatformStore,
    DefinitionRegistry,
    FactorSpec,
    FormalResearchRunExecutor,
    RequirementCompiler,
    ResearchControlPlane,
    ResearchRunPreviewService,
    ResearchRunService,
    ResearchRunWorker,
    UniverseService,
)
from services.data_platform.store import json_dumps, utc_now


class MixedSourceResearchPipelineTests(unittest.TestCase):
    def test_binance_and_openbb_instruments_reach_one_frozen_run(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            store = DataPlatformStore(root / "metadata.db")
            project = ResearchControlPlane(store).create_project(
                title="Mixed source pipeline",
                objective="Run one Factor over Binance and OpenBB instruments",
            )
            instruments = {
                "crypto_spot:BINANCE:BTCUSDT": "BINANCE",
                "equity:XNAS:TSLA": "YFINANCE",
            }
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for instrument_id, source in instruments.items():
                rows = [{
                    "instrument_id": instrument_id,
                    "frequency": "1d",
                    "bar_start_time": (start + timedelta(days=index)).isoformat(),
                    "bar_end_time": (start + timedelta(days=index + 1)).isoformat(),
                    "available_time": (start + timedelta(days=index + 1)).isoformat(),
                    "ingested_at": "2026-01-10T00:00:00+00:00",
                    "open": 100.0 + index,
                    "high": 102.0 + index,
                    "low": 99.0 + index,
                    "close": 101.0 + index,
                    "volume": 1000.0,
                    "turnover": 101000.0,
                    "trade_count": 10,
                    "bar_status": "COMPLETE",
                    "source": source,
                    "source_version": "1",
                    "quality_status": "PASS",
                } for index in range(7)]
                CanonicalBarsCommitter(store, root / source.lower()).commit(
                    dataset_id=f"{source.lower()}:{instrument_id.split(':')[-1]}:1d",
                    instrument_id=instrument_id,
                    asset_class=instrument_id.split(":")[0],
                    venue=instrument_id.split(":")[1],
                    frequency="1d",
                    source=source,
                    source_version="1",
                    rows=rows,
                )
            universe = UniverseService(store).create_definition(
                name="Mixed",
                version="1",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": list(instruments)},
            )
            snapshot = UniverseService(store).resolve_snapshot(
                universe_definition_id=universe.universe_definition_id,
                as_of_time="2026-01-07T00:00:00+00:00",
            )
            factor = FactorSpec(
                name="mixed_return",
                version="1",
                operator="pct_change",
                input_field="close",
                window=1,
                frequency="1d",
            )
            definition = DefinitionRegistry(store).create(
                "FACTOR",
                {
                    "name": factor.name,
                    "version": factor.version,
                    "operator": factor.operator,
                    "input_field": factor.input_field,
                    "window": factor.window,
                    "frequency": factor.frequency,
                },
                state="VALIDATED",
            )
            DefinitionRegistry(store).set_project_ref(
                project_id=project["project_id"],
                slot_key="factor:mixed_return",
                definition_id=definition.definition_id,
                definition_version=definition.version,
                reference_mode="PINNED",
            )
            requirements = RequirementCompiler(store).compile(
                project_id=project["project_id"],
                factor_specs=[factor],
                context={
                    "universe_snapshot_id": snapshot.universe_snapshot_id,
                    "instrument_ids": list(instruments),
                    "data_type": "bars",
                    "frequency": "1d",
                    "history_start": "2026-01-01T00:00:00+00:00",
                    "history_end": "2026-01-07T00:00:00+00:00",
                    "source_selection_policy": {
                        "mode": "AUTO",
                        "per_instrument": {
                            instrument_id: {
                                "mode": "FIXED",
                                "allowed_sources": [source.lower()],
                                "preferred_sources": [source.lower()],
                            }
                            for instrument_id, source in instruments.items()
                        },
                    },
                },
            )
            grant_id = "grant_mixed_source"
            now = utc_now()
            with store.transaction(immediate=True) as conn:
                conn.execute(
                    """INSERT INTO approval_grants(
                       grant_id,project_id,plan_version,status,scope_json,budgets_json,
                       approved_by,created_at,approved_at,expires_at,grant_version,policy_version
                       ) VALUES (?,?,1,'ACTIVE',?,?,?,?,?,NULL,1,'research_policy.v1')""",
                    (
                        grant_id,
                        project["project_id"],
                        json_dumps({"allowed_run_types": ["FACTOR_EVALUATION"]}),
                        json_dumps({
                            "max_backtest_runs": 1,
                            "max_download_bytes": 0,
                            "max_runtime_seconds": 600,
                        }),
                        "human_reviewer",
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO approval_budget_counters(grant_id,updated_at) VALUES (?,?)",
                    (grant_id, now),
                )
            preview = ResearchRunPreviewService(store).create(
                project["project_id"],
                {
                    "run_type": "FACTOR_EVALUATION",
                    "requirement_set_id": requirements.requirement_set_id,
                    "universe_snapshot_id": snapshot.universe_snapshot_id,
                    "grant_id": grant_id,
                    "source_selection_policy": {"mode": "AUTO"},
                    "evaluation_spec": {
                        "horizons": [1],
                        "minimum_cross_section_size": 2,
                    },
                    "budget": {
                        "runs": 1,
                        "runtime_seconds": 60,
                        "download_bytes": 0,
                    },
                },
            )
            self.assertEqual("READY", preview["readiness"]["overall"]["status"])
            self.assertEqual(
                {"BINANCE", "YFINANCE"},
                {item["source"] for item in preview["resolver_output"]["bindings"]},
            )
            run = ResearchRunService(store).create(
                preview_id=preview["preview_id"],
                preview_fingerprint=preview["preview_fingerprint"],
                idempotency_key="mixed-source-run",
            )
            claimed = ResearchRunWorker(store, "mixed-source-worker").claim()
            output = FormalResearchRunExecutor(
                store, artifact_root=root_text
            ).execute(claimed)
            finished = ResearchRunWorker(
                store, "mixed-source-worker"
            ).complete(run["run_id"], output)
            self.assertEqual("SUCCEEDED", finished["status"])
            self.assertEqual(2, len(claimed["frozen_input"]["manifest_descriptors"]))
