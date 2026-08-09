from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import strategy_data_source
from services import strategy_registry_service
from services.strategy_signal_source_service import (
    LEGACY_STRATEGY_CODE,
    LIBRARY_ALPHA,
    effective_strategy_signal_source,
    list_library_alpha_sources,
    resolve_library_alpha_source,
)


class FakeLibrary:
    def __init__(self, *, factor_hash: str = "factor-hash", factor_version: str = "1.0.0"):
        factor_definition = {
            "definition_id": "factor_1",
            "definition_type": "FACTOR",
            "name": "Momentum 20",
            "version": factor_version,
            "state": "VALIDATED",
            "spec_hash": factor_hash,
            "spec": {},
        }
        alpha_definition = {
            "definition_id": "alpha_1",
            "definition_type": "ALPHA",
            "name": "Momentum Composite",
            "version": "2.0.0",
            "state": "VALIDATED",
            "spec_hash": "alpha-hash",
            "engine_version": "alpha-engine.v1",
            "code_hash": "alpha-code-hash",
            "spec": {
                "components": [{
                    "factor_definition_id": "factor_1",
                    "factor_version": "1.0.0",
                    "factor_spec_hash": "factor-hash",
                    "factor_name": "Momentum 20",
                    "weight": 1.0,
                    "transform": "CS_RANK",
                    "ascending": False,
                }]
            },
        }
        self.alpha = {
            "library_asset_id": "library_alpha_1",
            "component_type": "ALPHA",
            "name": "Momentum Composite",
            "version": 3,
            "source_object_id": "alpha_1",
            "source_object_version": "2.0.0",
            "content_hash": "alpha-hash",
            "content": alpha_definition,
        }
        self.factor = {
            "library_asset_id": "library_factor_1",
            "component_type": "FACTOR",
            "name": "Momentum 20",
            "version": 4,
            "source_object_id": "factor_1",
            "source_object_version": factor_version,
            "content_hash": factor_hash,
            "content": factor_definition,
        }

    def get(self, library_asset_id: str):
        return self.alpha if library_asset_id == self.alpha["library_asset_id"] else None

    def list(self, *, component_type: str = ""):
        if component_type == "ALPHA":
            return [self.alpha]
        if component_type == "FACTOR":
            return [self.factor]
        return [self.alpha, self.factor]


class StrategySignalSourceTests(unittest.TestCase):
    def test_existing_strategy_rows_default_to_legacy_strategy_code(self):
        source = effective_strategy_signal_source({}, strategy_code="Stragy_Fllow_Truth")
        self.assertEqual(source, {
        "schema_version": "strategy_signal_source.v1",
        "type": LEGACY_STRATEGY_CODE,
        "status": "READY",
        "execution_status": "CONNECTED",
        "strategy_code": "Stragy_Fllow_Truth",
        })


    def test_library_alpha_resolves_immutable_factor_closure(self):
        source = resolve_library_alpha_source("library_alpha_1", library=FakeLibrary())
        self.assertEqual(source["type"], LIBRARY_ALPHA)
        self.assertEqual(source["status"], "REFERENCE_READY")
        self.assertEqual(source["execution_status"], "NOT_CONNECTED")
        self.assertEqual(source["alpha_definition_id"], "alpha_1")
        self.assertEqual(source["alpha_version"], "2.0.0")
        self.assertEqual(source["alpha_spec_hash"], "alpha-hash")
        self.assertEqual(source["library_asset_version"], 3)
        self.assertEqual(source["factor_closure"], [{
        "component_index": 0,
        "library_asset_id": "library_factor_1",
        "library_asset_version": 4,
        "factor_definition_id": "factor_1",
        "factor_version": "1.0.0",
        "factor_spec_hash": "factor-hash",
        "factor_name": "Momentum 20",
        "weight": 1.0,
        "transform": "CS_RANK",
        "ascending": False,
        }])


    def test_library_alpha_rejects_dependency_version_drift(self):
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            resolve_library_alpha_source(
                "library_alpha_1",
                library=FakeLibrary(factor_version="1.1.0"),
            )


    def test_library_alpha_list_reports_invalid_assets_instead_of_hiding_them(self):
        rows = list_library_alpha_sources(library=FakeLibrary(factor_hash="changed"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "INVALID")
        self.assertIn("hash mismatch", rows[0]["error"])


    def test_strategy_registry_migration_adds_signal_source_column(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE strategy_registry(
            strategy_id INTEGER PRIMARY KEY,
            strategy_name TEXT NOT NULL,
            strategy_code TEXT NOT NULL,
            mode TEXT NOT NULL,
            input_json TEXT NOT NULL
            )"""
        )
        strategy_data_source._migrate_registry_signal_source_column(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_registry)")}
        self.assertIn("signal_source_json", columns)
        conn.execute(
            "INSERT INTO strategy_registry(strategy_id,strategy_name,strategy_code,mode,input_json) VALUES (1,'s','c','Stop','{}')"
        )
        self.assertEqual(conn.execute("SELECT signal_source_json FROM strategy_registry").fetchone()[0], "{}")


    def test_library_alpha_cannot_start_before_execution_adapter(self):
        with patch.object(
            strategy_registry_service,
            "resolve_strategy_signal_source",
            return_value={"type": LIBRARY_ALPHA},
        ):
            with self.assertRaisesRegex(ValueError, "Stop mode"):
                strategy_registry_service.create_strategy({
                    "strategy_name": "Alpha Strategy",
                    "signal_source": {"type": LIBRARY_ALPHA, "library_asset_id": "library_alpha_1"},
                    "mode": "Virtual",
                })

    def test_registry_persists_new_legacy_source_without_changing_strategy_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "strategy.db"

            def connect():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                return conn

            conn = connect()
            conn.executescript(strategy_data_source._DDL_REGISTRY)
            conn.executescript(strategy_data_source._DDL_LEGS)
            conn.close()
            with patch.object(strategy_registry_service, "_connect", side_effect=connect):
                created = strategy_registry_service.create_strategy({
                    "strategy_name": "Legacy Strategy",
                    "strategy_code": "Stragy_Fllow_Truth",
                    "mode": "Stop",
                })
                self.assertEqual(created["strategy_code"], "Stragy_Fllow_Truth")
                self.assertEqual(created["signal_source"]["type"], LEGACY_STRATEGY_CODE)
                self.assertEqual(created["signal_source"]["strategy_code"], "Stragy_Fllow_Truth")

    def test_registry_persists_resolved_library_alpha_pin(self):
        pinned_source = resolve_library_alpha_source("library_alpha_1", library=FakeLibrary())
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "strategy.db"

            def connect():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                return conn

            conn = connect()
            conn.executescript(strategy_data_source._DDL_REGISTRY)
            conn.executescript(strategy_data_source._DDL_LEGS)
            conn.close()
            with (
                patch.object(strategy_registry_service, "_connect", side_effect=connect),
                patch.object(
                    strategy_registry_service,
                    "resolve_strategy_signal_source",
                    return_value=pinned_source,
                ),
            ):
                created = strategy_registry_service.create_strategy({
                    "strategy_name": "Pinned Alpha Strategy",
                    "signal_source": {"type": LIBRARY_ALPHA, "library_asset_id": "library_alpha_1"},
                    "mode": "Stop",
                })
                self.assertEqual(created["strategy_code"], "")
                self.assertEqual(created["signal_source"]["alpha_spec_hash"], "alpha-hash")
                self.assertEqual(created["signal_source"]["factor_closure"][0]["factor_spec_hash"], "factor-hash")


    def test_strategy_ui_exposes_library_alpha_source_contract(self):
        root = Path(__file__).resolve().parents[2]
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        js = (root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="strategySignalSourceType"', html)
        self.assertIn('value="LIBRARY_ALPHA"', html)
        self.assertIn("/api/strategy-signal-sources/library-alphas", js)
        self.assertIn('body.signal_source = signalSourceType === "LIBRARY_ALPHA"', js)


if __name__ == "__main__":
    unittest.main()
