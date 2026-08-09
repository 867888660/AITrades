from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    DataPlatformStore,
    FactorDraftService,
    FactorDraftValidationError,
)


def valid_document() -> dict:
    return {
        "schema_version": "factor_draft.v2",
        "identity": {
            "name": "momentum_20",
            "version": "1.0.0",
        },
        "inputs": [{
            "variable_name": "price",
            "dataset": "bars",
            "field": "close",
            "frequency": "1h",
        }],
        "parameters": [{"name": "window", "value": 20, "unit": "bars"}],
        "formula": {
            "source": "pct_change(price, window)",
        },
        "output": {
            "unit": "RATIO",
            "direction": "HIGHER_IS_BETTER",
        },
        "advanced": {
            "missing_policy": "STRICT",
            "dimension": "TIME_SERIES",
            "available_after": "BAR_CLOSE",
        },
    }


class FactorDraftServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.service = FactorDraftService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_incomplete_draft_can_be_saved_and_inspected(self) -> None:
        draft = self.service.create({"identity": {"name": "unfinished"}})

        self.assertEqual("DRAFT", draft.state)
        diagnostics = self.service.inspect(draft.draft_id)
        self.assertFalse(diagnostics["can_validate"])
        self.assertGreater(diagnostics["summary"]["errors"], 0)
        self.assertIn(
            "FACTOR_VERSION_REQUIRED",
            {item["code"] for item in diagnostics["diagnostics"]},
        )

    def test_existing_database_receives_factor_draft_migration(self) -> None:
        with self.store.transaction(immediate=True) as conn:
            conn.execute("DROP TABLE factor_drafts")
            conn.execute("DELETE FROM schema_migrations WHERE migration_version=16")

        upgraded = DataPlatformStore(self.store.db_path)
        with upgraded.connection() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='factor_drafts'"
            ).fetchone()
            migration = conn.execute(
                "SELECT migration_name FROM schema_migrations WHERE migration_version=16"
            ).fetchone()

        self.assertIsNotNone(table)
        self.assertEqual("factor_draft_documents", migration["migration_name"])

    def test_valid_draft_compiles_but_requires_preview_before_validation(self) -> None:
        draft = self.service.create(valid_document())
        diagnostics = self.service.inspect(draft.draft_id)

        self.assertTrue(diagnostics["can_compile"])
        self.assertTrue(diagnostics["can_preview"])
        self.assertFalse(diagnostics["can_validate"])
        self.assertTrue(diagnostics["can_save_factor"])
        self.assertTrue(diagnostics["preview_required"])
        self.assertEqual("NOT_RUN", diagnostics["preview_status"])
        self.assertEqual("close", diagnostics["compiled_factor_spec"]["formula"]["input"])
        self.assertEqual("time.pct_change", diagnostics["compiled_factor_spec"]["formula"]["operator"])
        self.assertEqual(20, diagnostics["compiled_factor_spec"]["formula"]["window"])
        self.assertEqual(
            "time.pct_change(Bars.close @ 1h, 20 bars)",
            diagnostics["compiled_formula"]["resolved_formula"],
        )
        self.assertEqual("21 bars", diagnostics["compiled_formula"]["required_history"])

        with self.assertRaisesRegex(ValueError, "FACTOR_PREVIEW_REQUIRED"):
            self.service.validate(
                draft.draft_id,
                expected_fingerprint=draft.draft_fingerprint,
                preview_id="",
                preview_fingerprint="",
            )

    def test_stale_fingerprint_cannot_update_or_validate(self) -> None:
        draft = self.service.create(valid_document())
        changed = valid_document()
        changed["parameters"][0]["value"] = 40
        updated = self.service.update(
            draft.draft_id,
            changed,
            expected_fingerprint=draft.draft_fingerprint,
        )

        with self.assertRaisesRegex(ValueError, "FACTOR_DRAFT_STALE"):
            self.service.update(
                draft.draft_id,
                valid_document(),
                expected_fingerprint=draft.draft_fingerprint,
            )
        with self.assertRaisesRegex(ValueError, "FACTOR_DRAFT_STALE"):
            self.service.validate(
                draft.draft_id,
                expected_fingerprint=draft.draft_fingerprint,
                preview_id="preview_old",
                preview_fingerprint="fingerprint_old",
            )
        self.assertNotEqual(draft.draft_fingerprint, updated.draft_fingerprint)

    def test_invalid_formula_reports_stable_diagnostic_codes(self) -> None:
        document = valid_document()
        document["parameters"] = [
            {"name": "fast_window", "value": 20, "unit": "bars"},
            {"name": "slow_window", "value": 10, "unit": "bars"},
        ]
        document["formula"] = {
            "source": "ma_crossover(price, fast_window, slow_window)",
        }
        document["output"]["unit"] = "DISCRETE"
        document["output"]["direction"] = "EVENT_SIGNAL"
        diagnostics = self.service.inspect_document(document)

        self.assertFalse(diagnostics["can_validate"])
        self.assertIn(
            "FACTOR_V4_PARAMETER_CONSTRAINT",
            {item["code"] for item in diagnostics["diagnostics"]},
        )
        draft = self.service.create(document)
        with self.assertRaises(FactorDraftValidationError):
            self.service.validate(
                draft.draft_id,
                expected_fingerprint=draft.draft_fingerprint,
                preview_id="preview_invalid",
                preview_fingerprint="fingerprint_invalid",
            )

    def test_nested_and_composed_expressions_compile_as_v4_graphs(self) -> None:
        nested = valid_document()
        nested["formula"]["source"] = "universe.rank(time.pct_change(price, window))"
        nested["output"].pop("unit")
        nested_result = self.service.inspect_document(nested)
        self.assertTrue(nested_result["can_save_factor"])
        self.assertEqual("HYBRID", nested_result["compiled_factor_spec"]["dimension"])
        self.assertEqual("universe.rank", nested_result["compiled_factor_spec"]["formula"]["operator"])

        composed = valid_document()
        composed["formula"]["source"] = (
            "time.pct_change(price, window) + time.pct_change(price, window)"
        )
        composed_result = self.service.inspect_document(composed)
        self.assertTrue(composed_result["can_save_factor"])
        self.assertEqual("binary", composed_result["compiled_factor_spec"]["formula"]["ast"]["kind"])

    def test_explicit_alignment_and_conditional_compile_as_v4_graphs(self) -> None:
        document = valid_document()
        document["inputs"].append({
            "variable_name": "daily_volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        document["formula"]["source"] = (
            "where("
            "greater(price, time.mean(price, window)), "
            "align.asof(daily_volume, price), "
            "align.asof(daily_volume, price)"
            ")"
        )
        document["output"].pop("unit")
        result = self.service.inspect_document(document)

        self.assertTrue(result["can_save_factor"])
        self.assertEqual("1h", result["compiled_factor_spec"]["frequency"])
        self.assertIn(
            "latest source value",
            result["compiled_formula"]["formula_meaning"],
        )

    def test_rolling_std_exposes_user_facing_formula_and_output_meaning(self) -> None:
        document = valid_document()
        document["formula"]["source"] = "rolling_std(price, window)"
        document["output"] = {"direction": "NO_PREDEFINED_DIRECTION"}
        result = self.service.inspect_document(document)
        compilation = result["compiled_formula"]

        self.assertTrue(result["can_save_factor"])
        self.assertEqual(
            "time.std(Bars.close @ 1h, 20 bars)",
            compilation["resolved_formula"],
        )
        self.assertEqual("20 bars", compilation["required_history"])
        self.assertEqual("Numeric", compilation["output_display"]["type"])
        self.assertEqual("Same as price", compilation["output_display"]["unit"])
        self.assertEqual("Every 1h · Bar Close", compilation["output_display"]["evaluation"])
        self.assertEqual("Time Series", compilation["output_display"]["dimension"])
        self.assertIn("warmup", compilation["output_display"]["nullability"])
        self.assertIn(
            "greater price variation",
            compilation["output_display"]["value_meaning"],
        )

    def test_output_metadata_change_invalidates_draft_fingerprint(self) -> None:
        original = valid_document()
        original["output"]["display_name"] = "Momentum"
        changed = valid_document()
        changed["output"]["display_name"] = "Twenty-period Momentum"

        original_result = self.service.inspect_document(original)
        changed_result = self.service.inspect_document(changed)

        self.assertNotEqual(
            original_result["draft_fingerprint"],
            changed_result["draft_fingerprint"],
        )
        self.assertEqual(
            "Twenty-period Momentum",
            changed_result["compiled_factor_spec"]["output_display_name"],
        )

    def test_engine_v4_rejects_more_than_eight_inputs(self) -> None:
        document = valid_document()
        for index in range(8):
            document["inputs"].append({
                "variable_name": f"extra_{index}",
                "dataset": "bars",
                "field": "quote_volume",
                "frequency": "1h",
            })
        result = self.service.inspect_document(document)
        self.assertIn(
            "FACTOR_V4_INPUT_LIMIT",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_draft_discard_is_soft_delete_and_fingerprint_guarded(self) -> None:
        draft = self.service.create(valid_document())

        with self.assertRaisesRegex(ValueError, "expected_fingerprint is required"):
            self.service.discard(draft.draft_id, expected_fingerprint="")
        with self.assertRaisesRegex(ValueError, "FACTOR_DRAFT_STALE"):
            self.service.discard(draft.draft_id, expected_fingerprint="stale")

        discarded = self.service.discard(
            draft.draft_id,
            expected_fingerprint=draft.draft_fingerprint,
        )

        self.assertEqual("DISCARDED", discarded.state)
        self.assertEqual([], self.service.list())
        self.assertEqual(
            [draft.draft_id],
            [item.draft_id for item in self.service.list(state="DISCARDED")],
        )
        diagnostics = self.service.inspect(draft.draft_id)
        self.assertFalse(diagnostics["can_preview"])
        self.assertFalse(diagnostics["can_validate"])
        self.assertEqual("DISCARDED", diagnostics["preview_status"])
        with self.assertRaisesRegex(ValueError, "only active Factor drafts"):
            self.service.update(
                draft.draft_id,
                valid_document(),
                expected_fingerprint=draft.draft_fingerprint,
            )
        with self.assertRaisesRegex(ValueError, "discarded Factor drafts"):
            self.service.validate(
                draft.draft_id,
                expected_fingerprint=draft.draft_fingerprint,
                preview_id="preview",
                preview_fingerprint="fingerprint",
            )


if __name__ == "__main__":
    unittest.main()
