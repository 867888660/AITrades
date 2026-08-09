from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import DataPlatformStore, Instrument, InstrumentRegistry, default_requirement_spec, make_instrument_id


class ResearchWorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        registry = InstrumentRegistry(self.store)
        for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
            instrument_id = make_instrument_id("crypto_spot", "BINANCE", symbol)
            registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="crypto_spot",
                    venue="BINANCE",
                    market_type="SPOT",
                    native_symbol=symbol,
                ),
                aliases=[("binance", symbol)],
            )
        self.client = app_module.app.test_client()
        self.store_patch = patch("app.get_default_store", return_value=self.store)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    def test_local_ui_can_create_compile_and_resolve_without_approval(self):
        project_response = self.client.post(
            "/api/research/projects",
            json={"title": "Five asset research", "objective": "Test a cross-sectional alpha."},
        )
        self.assertEqual(project_response.status_code, 201)
        project = project_response.get_json()["data"]

        requirement_response = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirement-sets",
            json={
                "instrument_source": "binance",
                "context": {
                    "instrument_ids": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"],
                    "data_type": "bars",
                    "frequency": "1h",
                    "history_start": "2026-04-01T00:00:00+00:00",
                    "history_end": "2026-07-01T00:00:00+00:00",
                },
                "factor_specs": [
                    {"name": "momentum", "version": "1.0.0", "input_field": "close", "window": 20},
                    {"name": "volatility", "version": "1.0.0", "input_field": "close", "window": 20},
                ],
                "backtest_requirements": [
                    {"name": "next_bar_open", "fields": ["open"], "lookback_value": 1}
                ],
            },
        )
        self.assertEqual(requirement_response.status_code, 201)
        requirement = requirement_response.get_json()["data"]
        self.assertEqual({"close", "open"}, set(requirement["requirements"][0]["fields"]))
        self.assertEqual(
            "crypto_spot:BINANCE:BTCUSDT",
            requirement["requirements"][0]["instrument_ids"][1],
        )

        plan_response = self.client.post(
            f"/api/research/projects/{project['project_id']}/resolved-plans",
            json={
                "logical_name": "binance_1h_plan",
                "requirement_set_id": requirement["requirement_set_id"],
                "route": {"gateway": "BINANCE", "endpoint": "spot.klines"},
                "source_policy": {"mode": "FIXED", "providers": ["binance"]},
                "canonical": {"adjustment": "NONE", "time_semantics": "BAR_END_AVAILABLE_TIME"},
                "estimates": {"download_bytes": 1024, "runtime_seconds": 30},
            },
        )
        self.assertEqual(plan_response.status_code, 201)
        plan = plan_response.get_json()["data"]
        self.assertEqual("RESOLVED_DATA_PLAN", plan["artifact_type"])
        self.assertEqual([], self.client.get(f"/api/research/projects/{project['project_id']}/grants").get_json()["data"])

    def test_write_routes_are_local_only(self):
        response = self.client.post(
            "/api/research/projects",
            json={"title": "Blocked", "objective": "Must not be created."},
            environ_base={"REMOTE_ADDR": "203.0.113.8"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual([], self.client.get("/api/research/projects").get_json()["data"])

    def test_equity_grant_and_openbb_export_task_use_equity_scope(self):
        project = self.client.post(
            "/api/research/projects", json={"title": "Equity", "objective": "Load AAPL daily bars."}
        ).get_json()["data"]
        grant_response = self.client.post(
            f"/api/research/projects/{project['project_id']}/run-grants",
            json={
                "objective": "Load AAPL daily bars.",
                "allowed_providers": ["YFINANCE"],
                "allowed_intervals": ["1d"],
                "allowed_instrument_ids": ["equity:XNAS:AAPL"],
                "time_start": "2026-01-01T00:00:00+00:00",
                "time_end": "2026-07-20T23:59:59+00:00",
                "expires_at": "2027-01-01T00:00:00+00:00",
            },
        )
        self.assertEqual(201, grant_response.status_code, grant_response.get_json())
        grant = grant_response.get_json()["data"]
        self.assertEqual(["equity"], grant["scope"]["asset_classes"])
        self.assertEqual(["XNAS"], grant["scope"]["venues"])
        self.assertEqual(["yfinance"], grant["scope"]["providers"])
        self.assertIn("equity.price.historical", grant["scope"]["endpoints"])

        task_response = self.client.post(
            f"/api/agent/research/projects/{project['project_id']}/openbb-export-tasks",
            json={
                "grant_id": grant["grant_id"],
                "provider": "yfinance", "venue": "XNAS", "symbol": "AAPL",
                "instrument_id": "equity:XNAS:AAPL", "interval": "1d",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-07-20T23:59:59+00:00",
                "latest_available": True,
                "library_asset_id": "library_requirement_test",
            },
        )
        self.assertEqual(201, task_response.status_code, task_response.get_json())
        task = task_response.get_json()["data"]
        self.assertEqual("OPENBB_EQUITY_DAILY_EXPORT", task["task_type"])
        self.assertEqual("yfinance", task["input"]["provider"])
        self.assertEqual("splits_only", task["input"]["adjustment"])
        self.assertTrue(task["input"]["latest_available"])

    def test_polymarket_grant_and_export_task_use_outcome_scope(self):
        project = self.client.post(
            "/api/research/projects", json={"title": "Prediction", "objective": "Load outcome history."}
        ).get_json()["data"]
        instrument_id = "polymarket_binary:POLYMARKET:token-1"
        grant_response = self.client.post(
            f"/api/research/projects/{project['project_id']}/run-grants",
            json={
                "objective": "Load outcome history.",
                "allowed_providers": ["POLYMARKET"],
                "allowed_intervals": ["1d"],
                "allowed_instrument_ids": [instrument_id],
                "time_start": "2025-07-26T00:00:00+00:00",
                "time_end": "2026-07-26T00:00:00+00:00",
                "expires_at": "2027-01-01T00:00:00+00:00",
            },
        )
        self.assertEqual(201, grant_response.status_code, grant_response.get_json())
        grant = grant_response.get_json()["data"]
        self.assertIn("polymarket.price_history", grant["scope"]["endpoints"])

        task_response = self.client.post(
            f"/api/agent/research/projects/{project['project_id']}/polymarket-export-tasks",
            json={
                "grant_id": grant["grant_id"],
                "instrument_id": instrument_id,
                "interval": "1d",
                "start_time": "2025-07-26T00:00:00+00:00",
                "end_time": "2026-07-26T00:00:00+00:00",
                "latest_available": True,
                "library_asset_id": "library_requirement_test",
            },
        )
        self.assertEqual(201, task_response.status_code, task_response.get_json())
        task = task_response.get_json()["data"]
        self.assertEqual("POLYMARKET_PRICE_HISTORY_EXPORT", task["task_type"])
        self.assertEqual(instrument_id, task["input"]["instrument_id"])
        self.assertTrue(task["input"]["latest_available"])

    def test_legacy_polymarket_prepare_route_cannot_bypass_control_plane(self):
        response = self.client.post(
            "/api/research/data/prepare/polymarket",
            json={
                "instrument_id": "polymarket_binary:POLYMARKET:token-1",
                "interval": "1d",
            },
        )
        self.assertEqual(410, response.status_code)
        self.assertEqual("CONTROLLED_TASK_REQUIRED", response.get_json()["code"])

    def test_requirement_api_uses_one_library_object_and_research_references(self):
        first = self.client.post(
            "/api/research/projects", json={"title": "First", "objective": "Own the initial selection."}
        ).get_json()["data"]
        second = self.client.post(
            "/api/research/projects", json={"title": "Second", "objective": "Reuse the shared contract."}
        ).get_json()["data"]
        created_response = self.client.post(
            f"/api/research/projects/{first['project_id']}/requirements/items",
            json={"spec": default_requirement_spec("Shared API bars")},
        )
        self.assertEqual(201, created_response.status_code, created_response.get_json())
        created = created_response.get_json()["data"]
        self.assertEqual("LIBRARY", created["origin"])

        added = self.client.post(
            f"/api/research/projects/{second['project_id']}/requirements/library-items",
            json={"library_asset_id": created["library_asset_id"]},
        )
        self.assertEqual(201, added.status_code, added.get_json())
        changed = created["spec"]
        changed["data"]["frequency"] = "4h"
        updated = self.client.patch(
            f"/api/research/projects/{first['project_id']}/requirements/items/{created['ref_id']}",
            json={"spec": changed},
        )
        self.assertEqual(200, updated.status_code, updated.get_json())
        second_items = self.client.get(
            f"/api/research/projects/{second['project_id']}/requirements/items"
        ).get_json()["data"]
        self.assertEqual("4h", second_items[0]["spec"]["data"]["frequency"])

        copied = changed
        copied["name"] = "Shared API bars custom"
        copied["data"]["frequency"] = "1d"
        save_as = self.client.post(
            f"/api/research/projects/{first['project_id']}/requirements/items/{created['ref_id']}/save-as",
            json={"spec": copied},
        )
        self.assertEqual(201, save_as.status_code, save_as.get_json())
        self.assertNotEqual(created["library_asset_id"], save_as.get_json()["data"]["library_asset_id"])

    def test_universe_requirement_suggestion_and_reconcile_api_keep_reference(self):
        project = self.client.post(
            "/api/research/projects",
            json={"title": "Universe data", "objective": "Prepare data for the current Universe."},
        ).get_json()["data"]
        universe_response = self.client.post(
            f"/api/research/projects/{project['project_id']}/universes",
            json={
                "name": "API tracked instruments",
                "type": "instrument_set",
                "members": [
                    "crypto_spot:BINANCE:BTCUSDT",
                    "crypto_spot:BINANCE:ETHUSDT",
                ],
            },
        )
        self.assertEqual(201, universe_response.status_code, universe_response.get_json())
        universe = universe_response.get_json()["data"]

        required = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirements/reconcile",
            json={"universe_id": universe["universe_id"]},
        )
        self.assertEqual(200, required.status_code, required.get_json())
        self.assertEqual("REQUIRED", required.get_json()["data"]["status"])

        suggestion_response = self.client.get(
            f"/api/research/projects/{project['project_id']}/requirements/suggestion"
            f"?universe_id={universe['universe_id']}"
        )
        self.assertEqual(200, suggestion_response.status_code, suggestion_response.get_json())
        suggestion = suggestion_response.get_json()["data"]
        self.assertEqual("SPECIFIC_UNIVERSE", suggestion["spec"]["target"]["scope"])
        self.assertEqual(universe["universe_id"], suggestion["spec"]["target"]["universe_id"])
        self.assertEqual([], suggestion["spec"]["scope"]["instruments"]["include"])
        self.assertEqual(2, suggestion["universe"]["member_count"])

        created = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirements/items",
            json={"spec": suggestion["spec"]},
        )
        self.assertEqual(201, created.status_code, created.get_json())

        reconciled = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirements/reconcile",
            json={"universe_id": universe["universe_id"]},
        )
        self.assertEqual(200, reconciled.status_code, reconciled.get_json())
        self.assertIn(reconciled.get_json()["data"]["status"], {"PREPARING", "READY"})
        stored = self.client.get(
            f"/api/research/projects/{project['project_id']}/requirements/items"
        ).get_json()["data"][0]["spec"]
        self.assertEqual(universe["universe_id"], stored["target"]["universe_id"])
        self.assertEqual([], stored["scope"]["instruments"]["include"])

    def test_simple_ui_creates_project_scoped_research_objects(self):
        project = self.client.post(
            "/api/research/projects",
            json={"title": "Simple workflow", "objective": "Keep research objects together."},
        ).get_json()["data"]

        universe_response = self.client.post(
            "/api/research/universes",
            json={
                "name": "BTC universe",
                "version": "1.0.0",
                "universe_type": "STATIC_LIST",
                "parameters": {"instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"]},
                "owner_project_id": project["project_id"],
                "library_scope": "PROJECT",
            },
        )
        self.assertEqual(201, universe_response.status_code, universe_response.get_json())
        universe = universe_response.get_json()["data"]
        self.assertEqual(project["project_id"], universe["owner_project_id"])
        self.assertEqual("PROJECT", universe["library_scope"])

        factor_response = self.client.post(
            "/api/research/definitions",
            json={
                "definition_type": "FACTOR",
                "spec": {
                    "name": "BTC moving-average crossover",
                    "version": "1.0.0",
                    "operator": "ma_crossover",
                    "input_field": "close",
                    "window": 20,
                    "parameters": {"fast_window": 5},
                    "frequency": "1h",
                },
                "owner_project_id": project["project_id"],
                "library_scope": "PROJECT",
            },
        )
        self.assertEqual(201, factor_response.status_code, factor_response.get_json())
        factor = factor_response.get_json()["data"]
        self.assertEqual(project["project_id"], factor["owner_project_id"])
        self.assertEqual("PROJECT", factor["library_scope"])

    def test_formula_contract_is_served_by_backend_capability_schema(self):
        response = self.client.get("/api/research/engine-capabilities")
        self.assertEqual(200, response.status_code)
        capabilities = response.get_json()["data"]
        factor = capabilities["factor"]
        pct_change = next(item for item in factor["operator_schema"] if item["id"] == "time.pct_change")
        self.assertEqual(
            ["window"],
            [item["name"] for item in pct_change["parameters"]],
        )
        self.assertEqual("window + 1", pct_change["warmup"])
        self.assertTrue(pct_change["pit_safe"])
        self.assertIn("close", [item["id"] for item in factor["features"]])
        self.assertEqual("factor-engine.v4", factor["engine_version"])
        self.assertIn("factor-engine.v3", factor["compatible_engine_versions"])
        self.assertEqual(8, factor["authoring_contract"]["max_inputs"])
        self.assertEqual("VARIABLE_LIST", factor["authoring_contract"]["input_model"])
        self.assertEqual("BROWSE_AND_AUTOCOMPLETE", factor["authoring_contract"]["function_picker_role"])
        self.assertEqual(
            "SUPPORTED_FIELDS_WITH_EXPLICIT_CONFIRMATION",
            factor["authoring_contract"]["input_catalog_role"],
        )
        self.assertTrue(factor["authoring_contract"]["supports_nested_expressions"])
        self.assertTrue(factor["authoring_contract"]["supports_composed_expressions"])
        self.assertTrue(factor["authoring_contract"]["supports_multiple_inputs"])
        self.assertEqual(
            "EXPLICIT_ASOF_4B",
            factor["authoring_contract"]["frequency_alignment"],
        )
        self.assertEqual(
            ["align.asof", "align.forward_fill"],
            factor["authoring_contract"]["alignment_functions"],
        )
        self.assertEqual("factor_formula.v4", factor["formula_contract"])
        self.assertIn("Across Universe", factor["function_categories"])
        self.assertIn("Alignment", factor["function_categories"])
        self.assertIn("Conditional", factor["function_categories"])
        self.assertNotIn("Alignment", factor["planned_function_categories"])
        function_ids = {item["id"] for item in factor["function_schema"]}
        self.assertTrue(
            {"align.asof", "where", "greater", "time.median", "universe.demean"}
            <= function_ids
        )
        self.assertEqual(
            ["AUTO_BACKUP", "SAVE_DRAFT", "RUN_PREVIEW", "VALIDATE_FACTOR"],
            factor["authoring_contract"]["validation_flow"],
        )
        self.assertEqual(
            "REQUIRED_VALIDATION_EVIDENCE",
            factor["authoring_contract"]["preview_role"],
        )
        self.assertEqual(
            "factor_preview.v1",
            factor["authoring_contract"]["preview_contract"],
        )
        self.assertEqual(
            ["factor_definition_id", "factor_version"],
            capabilities["alpha"]["component_contract"]["required_reference"],
        )

    def test_research_ui_uses_research_and_library_navigation(self):
        response = self.client.get("/research")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        for label in (
            "Strategy Monitor", "Research", "Library", "Runs", "Data Catalog",
            "Backtests", "Agent Monitor", "Approvals", "Settings",
        ):
            self.assertIn(label, html)
        for forbidden in ("Research Projects", "Research Project", "Research Library", "Project Workspace"):
            self.assertNotIn(forbidden, html)
        self.assertIn('class="research-sidebar"', html)
        self.assertIn('class="research-primary-nav"', html)
        self.assertIn('<a class="research-brand" href="/" aria-label="DataTube home">', html)
        self.assertNotIn('class="primary-nav"', html)
        self.assertIn('<div class="dialog-frame">', html)
        self.assertIn('id="closeEditorDialog"', html)

        library_html = self.client.get("/library").get_data(as_text=True)
        self.assertIn('data-surface="library"', library_html)

        javascript = (Path(app_module.__file__).parent / "static" / "research_workspace_simple.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (Path(app_module.__file__).parent / "static" / "research_workspace_simple.css").read_text(
            encoding="utf-8"
        )
        for label in ("overview", "universe", "factor", "alpha", "data", "strategy", "runs"):
            self.assertIn(label, javascript)
        self.assertEqual(1, javascript.count("function renderResearchData()"))
        self.assertIn("if (currentUniverse() && currentSnapshot())", javascript)
        self.assertIn(
            "const staleUniverses = arr(state.universeBindings).filter(item => item.requirements_stale_at);",
            javascript,
        )
        self.assertIn("function requirementInstrumentBreakdown(rows)", javascript)
        self.assertIn("maintains every effective Requirement automatically", javascript)
        self.assertIn("Live Download", javascript)
        for label in (
            "MEMBER FORMAT", "Individual Instruments", "Instrument Groups", "BUILD METHOD",
            "Instrument List", "Combine Existing Universes", "Manual Groups", "Cross-source Groups",
            "Unique Groups", "Ordered Groups", "LIVE PREVIEW", "Paste List", "Import CSV",
            "Discovery Source", "universeEditorDiscoveryProviders", "historical preparation is not connected",
            "DATA REQUIRED", "Create Data Requirement", "DATA PREPARING", "View Progress",
            "DATA NEEDS ATTENTION", "View Changes", "Review &amp; Update",
            "Create & Prepare Data", "Update & Prepare Data", "UNIVERSE CHANGES",
        ):
            self.assertIn(label, javascript)
        self.assertIn("/api/research/instruments/register", javascript)
        self.assertIn("function universeEditorDiscoveryScope(definition = {}, resolution = {})", javascript)
        self.assertIn("assetClass === 'equity' && ['XNAS', 'XNYS'].includes(venue)", javascript)
        self.assertIn("function universeMarketLabel(instrumentIds = [])", javascript)
        self.assertNotIn("Binance Spot", javascript)
        self.assertNotIn("discoveryProvider: 'BINANCE'", javascript)
        self.assertNotIn("state.universeEditor?.discoveryProvider || 'BINANCE'", javascript)
        self.assertNotIn(">Prepare Data</button>", javascript)
        self.assertNotIn("Needs Access", javascript)
        self.assertNotIn("Enable Research", javascript)
        self.assertNotIn("Authorization Expires", javascript)
        self.assertNotIn('id="universeAssetClass"', javascript)
        self.assertNotIn('id="universeVenue"', javascript)
        self.assertNotIn('id="universeInstrumentType"', javascript)
        for retired in (
            "Preview Resolution", "Save New Revision", "Pair / Multi-leg Set",
            "Treat reversed tuples as the same", "Manual tuples",
            "Requirements need review", "freshly compiled Requirements",
            "effective Requirements", "rebuild Requirements",
        ):
            self.assertNotIn(retired, javascript)

        for operation in ("Add from Library", "Create in Research", "Save Draft", "Validate", "Publish to Library", "Use in Research", "Create New Version", "View Usage", "Copy and Edit"):
            self.assertIn(operation, javascript)
        for factor_editor_contract in (
            "function factorDraftDialog",
            "/api/research/factor-drafts/validation",
            "factor-editor factor-editor-v3",
            "Changes are backed up automatically.",
            "Changes backed up",
            "Factor saved",
            "data-factor-input-row",
            "Add Input",
            "data-factor-parameter-row",
            "Add Parameter",
            "factor-code-editor",
            "rolling_std(price, window)",
            "Insert function",
            "Resolved Formula",
            "Required History",
            "Formula Meaning",
            "DEFINITION CHECKS",
            "Advanced Details",
            "EXECUTION CONTRACT",
            "COMPILED SPECIFICATION",
            "AUDIT INFORMATION",
            "Real Factor values",
            "Save Draft",
            "Run Preview",
            "Validate Factor",
        ):
            self.assertIn(factor_editor_contract, javascript)
        active_factor_editor = javascript.split("function factorDraftDialog(base = null)", 1)[1].split(
            "function addLibraryDefinitionDialog", 1
        )[0]
        self.assertIn("/validate", active_factor_editor)
        self.assertIn('id="factorSaveDraft" type="submit"', active_factor_editor)
        self.assertIn('id="factorRunPreview" type="button" disabled', active_factor_editor)
        self.assertIn('id="factorValidateFactor" type="button" class="primary" disabled', active_factor_editor)
        self.assertIn("/preview-context", active_factor_editor)
        self.assertIn("/requirements", active_factor_editor)
        self.assertIn("/previews", active_factor_editor)
        self.assertIn("await prepareFactorPreviewRequirements(compiledRequirement, {", active_factor_editor)
        self.assertNotIn("run Preview again when they are Ready", active_factor_editor)
        self.assertIn("data-input-dataset", active_factor_editor)
        self.assertIn("function normalizedInput", active_factor_editor)
        self.assertIn("dataset: row.querySelector('[data-input-dataset]').value", active_factor_editor)
        self.assertIn("Instruments requestable", active_factor_editor)
        self.assertIn("prepared locally", active_factor_editor)
        self.assertIn("PREVIEW REQUIREMENTSET", active_factor_editor)
        self.assertNotIn("Complete Missing Data", active_factor_editor)
        self.assertIn("Backend maintenance is scheduling", javascript)
        self.assertNotIn("state.requirementRef = compiledRequirement.reference", active_factor_editor)
        self.assertNotIn("factorInputCatalog", active_factor_editor)
        self.assertNotIn("Search data fields", active_factor_editor)
        self.assertNotIn("confirmed Data Catalog Input", active_factor_editor)
        self.assertNotIn("Controlled Factor DSL", active_factor_editor)
        self.assertNotIn("COMPILED FACTORSPEC", active_factor_editor)
        self.assertIn("Save Draft → Run Preview → Validate Factor", active_factor_editor)
        self.assertNotIn("Run Preview · Step 3", active_factor_editor)
        self.assertIn("Validation requires a current Preview fingerprint.", active_factor_editor)
        self.assertIn(
            "Nested functions, Conditional logic, and explicit mixed-frequency alignment are supported.",
            active_factor_editor,
        )
        self.assertIn("align.asof(slower_input, reference_input)", active_factor_editor)
        self.assertNotIn("Current engine supports one Input.", active_factor_editor)
        self.assertNotIn("Current engine supports one function only.", active_factor_editor)
        for automatic_preview_contract in (
            "async function prepareFactorPreviewRequirements(",
            "previewStatus = await fetchRequirementDataStatus(requirementSetId)",
            "Backend maintenance is scheduling",
            "Preview will continue automatically.",
            "function factorPreviewPreparationFailure(rows)",
        ):
            self.assertIn(automatic_preview_contract, javascript)
        self.assertIn("item.state === 'DRAFT' && type !== 'FACTOR'", javascript)
        self.assertIn('data-action="discard-factor-draft"', javascript)
        self.assertIn("Discard Draft", javascript)
        self.assertIn("expected_fingerprint: draft.draft_fingerprint", javascript)
        self.assertIn("historical Preview evidence will not be affected", javascript)
        self.assertIn("/factors/sync-library", javascript)
        self.assertIn('data-action="remove-research-factor"', javascript)
        self.assertIn("Remove from Research", javascript)
        self.assertIn("automatically available in Library", javascript)
        self.assertIn("expected_definition_id: factor.definition_id", javascript)
        self.assertIn("Historical Previews and Runs will not change", javascript)
        self.assertIn("item.state === 'VALIDATED' && type !== 'factor'", javascript)
        self.assertIn(".factor-editor-layout", stylesheet)
        self.assertIn(".factor-variable-list", stylesheet)
        self.assertIn(".factor-parameter-list", stylesheet)
        self.assertIn(".factor-code-editor", stylesheet)
        self.assertIn(".factor-advanced-details", stylesheet)
        self.assertIn("grid-template-columns: minmax(0, 68%) minmax(300px, 32%)", stylesheet)
        self.assertNotIn("Requirements = what is needed", javascript)
        self.assertNotIn("Data Coverage = what is available", javascript)
        self.assertIn("Actual datasets remain in Data Catalog", javascript)
        self.assertIn("grid-template-columns: 216px minmax(0, 1fr)", stylesheet)
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", stylesheet)
        self.assertIn("button, .button-link {\n  width: auto", stylesheet)
        self.assertNotIn(".research-tabs, .library-tabs {\n  display: flex", stylesheet)
        self.assertIn("Factor Evaluation", javascript)
        self.assertIn("Alpha Evaluation", javascript)
        self.assertIn("Research Backtest", javascript)
        self.assertIn("Start Run", javascript)
        self.assertIn("/result-summary", javascript)
        self.assertIn("/sections/", javascript)
        self.assertIn("function factorRunInlineSection", javascript)
        self.assertIn("function factorRunSectionView", javascript)
        self.assertIn("value === null || value === undefined || value === ''", javascript)
        self.assertIn("Factor Run boundary", javascript)
        self.assertIn(".factor-run-result-block", stylesheet)
        self.assertIn("function alphaRunInlineSection", javascript)
        self.assertIn("function alphaRunSectionView", javascript)
        self.assertIn("function alphaRunSeriesChart", javascript)
        self.assertIn("Alpha Evaluation boundary", javascript)
        self.assertIn("Research Backtest boundary", javascript)
        self.assertIn(".alpha-run-result-block", stylesheet)
        self.assertIn("Execution Assumptions", javascript)
        self.assertIn("RESEARCH_BACKTEST", javascript)

    def test_primary_navigation_restores_strategy_monitor_and_backtests(self):
        for path in ("/", "/history", "/backtests/52"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            html = response.get_data(as_text=True)
            self.assertIn('href="/">Strategy Monitor</a>', html)
            self.assertIn('href="/history">Backtests</a>', html)

        history_html = self.client.get("/history").get_data(as_text=True)
        self.assertIn('nav-link active" href="/history">Backtests</a>', history_html)

        report_html = self.client.get("/backtests/52").get_data(as_text=True)
        self.assertIn('nav-link active" href="/history">Backtests</a>', report_html)

    def test_library_publication_is_an_immutable_asset_not_a_scope_change(self):
        first_research = self.client.post(
            "/api/research/projects",
            json={"title": "Source Research", "objective": "Develop a momentum Factor."},
        ).get_json()["data"]
        second_research = self.client.post(
            "/api/research/projects",
            json={"title": "Consumer Research", "objective": "Reuse an exact published version."},
        ).get_json()["data"]

        def create_factor(version: str, window: int) -> dict:
            response = self.client.post(
                "/api/research/definitions",
                json={
                    "definition_type": "FACTOR",
                    "state": "DRAFT",
                    "owner_project_id": first_research["project_id"],
                    "library_scope": "PROJECT",
                    "spec": {
                        "name": "Momentum",
                        "version": version,
                        "operator": "pct_change",
                        "input_field": "close",
                        "window": window,
                        "frequency": "1h",
                    },
                },
            )
            self.assertEqual(201, response.status_code, response.get_json())
            draft = response.get_json()["data"]
            validated = self.client.post(
                f"/api/research/definitions/{draft['definition_id']}/validate",
                json={},
            ).get_json()["data"]
            return validated

        factor_v1 = create_factor("1.0.0", 20)
        published_v1 = self.client.post(
            f"/api/research/definitions/{factor_v1['definition_id']}/publish",
            json={"project_id": first_research["project_id"]},
        )
        self.assertEqual(201, published_v1.status_code, published_v1.get_json())
        asset_v1 = published_v1.get_json()["data"]
        self.assertEqual(1, asset_v1["version"])
        self.assertEqual(20, asset_v1["content"]["spec"]["formula"]["window"])

        source_after_publish = next(
            item for item in self.client.get("/api/research/definitions").get_json()["data"]
            if item["definition_id"] == factor_v1["definition_id"]
        )
        self.assertEqual("PROJECT", source_after_publish["library_scope"])
        self.assertEqual(first_research["project_id"], source_after_publish["owner_project_id"])

        use_response = self.client.put(
            f"/api/research/projects/{second_research['project_id']}/definition-refs/factor:Momentum",
            json={
                "definition_id": factor_v1["definition_id"],
                "definition_version": factor_v1["version"],
                "reference_mode": "PINNED",
                "library_asset_id": asset_v1["library_asset_id"],
            },
        )
        self.assertEqual(200, use_response.status_code, use_response.get_json())
        self.assertEqual("LIBRARY", use_response.get_json()["data"]["origin"])
        self.assertEqual(1, use_response.get_json()["data"]["library_version"])

        factor_v2 = create_factor("1.0.1", 60)
        published_v2 = self.client.post(
            f"/api/research/definitions/{factor_v2['definition_id']}/publish",
            json={"project_id": first_research["project_id"]},
        )
        self.assertEqual(201, published_v2.status_code, published_v2.get_json())
        self.assertEqual(2, published_v2.get_json()["data"]["version"])

        assets = [
            item for item in self.client.get("/api/research/library?component_type=FACTOR").get_json()["data"]
            if item["name"] == "Momentum"
        ]
        self.assertEqual([2, 1], [item["version"] for item in assets])
        self.assertEqual([60, 20], [item["content"]["spec"]["formula"]["window"] for item in assets])
        consumer_ref = self.client.get(
            f"/api/research/projects/{second_research['project_id']}/definition-refs"
        ).get_json()["data"]["factor:Momentum"]
        self.assertEqual(factor_v1["definition_id"], consumer_ref["definition_id"])
        self.assertEqual(1, consumer_ref["library_version"])

    def test_validated_factor_syncs_to_library_and_research_reference_can_be_removed(self):
        project = self.client.post(
            "/api/research/projects",
            json={"title": "Automatic Factor Library", "objective": "Reuse validated Factors."},
        ).get_json()["data"]
        factor_response = self.client.post(
            "/api/research/definitions",
            json={
                "definition_type": "FACTOR",
                "state": "VALIDATED",
                "owner_project_id": project["project_id"],
                "library_scope": "PROJECT",
                "spec": {
                    "name": "Momentum",
                    "version": "1.0.0",
                    "operator": "pct_change",
                    "input_field": "close",
                    "window": 20,
                    "frequency": "1h",
                },
            },
        )
        self.assertEqual(201, factor_response.status_code, factor_response.get_json())
        factor = factor_response.get_json()["data"]
        factor_ref = self.client.put(
            f"/api/research/projects/{project['project_id']}/definition-refs/factor:Momentum",
            json={
                "definition_id": factor["definition_id"],
                "definition_version": factor["version"],
                "reference_mode": "PINNED",
            },
        )
        self.assertEqual(200, factor_ref.status_code, factor_ref.get_json())

        first_sync = self.client.post(
            f"/api/research/projects/{project['project_id']}/factors/sync-library",
            json={},
        )
        self.assertEqual(200, first_sync.status_code, first_sync.get_json())
        first_assets = first_sync.get_json()["data"]
        self.assertEqual(1, len(first_assets))
        self.assertEqual(factor["definition_id"], first_assets[0]["source_object_id"])
        second_assets = self.client.post(
            f"/api/research/projects/{project['project_id']}/factors/sync-library",
            json={},
        ).get_json()["data"]
        self.assertEqual(
            first_assets[0]["library_asset_id"],
            second_assets[0]["library_asset_id"],
        )
        usage_before_removal = self.client.get(
            f"/api/research/library/{first_assets[0]['library_asset_id']}/usage"
        )
        self.assertEqual(200, usage_before_removal.status_code, usage_before_removal.get_json())
        self.assertEqual(1, usage_before_removal.get_json()["data"]["research_count"])

        alpha_response = self.client.post(
            "/api/research/definitions",
            json={
                "definition_type": "ALPHA",
                "state": "VALIDATED",
                "owner_project_id": project["project_id"],
                "library_scope": "PROJECT",
                "spec": {
                    "name": "Momentum Alpha",
                    "version": "1.0.0",
                    "components": [{
                        "factor_definition_id": factor["definition_id"],
                        "factor_version": factor["version"],
                        "weight": 1,
                        "transform": "RAW",
                        "ascending": True,
                    }],
                    "minimum_coverage": 1,
                    "minimum_cross_section_size": 1,
                    "missing_policy": "EXCLUDE",
                    "rank_method": "AVERAGE",
                    "output_scale": "PERCENTILE",
                },
            },
        )
        self.assertEqual(201, alpha_response.status_code, alpha_response.get_json())
        alpha = alpha_response.get_json()["data"]
        alpha_ref = self.client.put(
            f"/api/research/projects/{project['project_id']}/definition-refs/alpha:Momentum%20Alpha",
            json={
                "definition_id": alpha["definition_id"],
                "definition_version": alpha["version"],
                "reference_mode": "PINNED",
            },
        )
        self.assertEqual(200, alpha_ref.status_code, alpha_ref.get_json())

        blocked = self.client.delete(
            f"/api/research/projects/{project['project_id']}/definition-refs/factor:Momentum",
            json={"expected_definition_id": factor["definition_id"]},
        )
        self.assertEqual(400, blocked.status_code, blocked.get_json())
        self.assertIn("FACTOR_REFERENCE_IN_USE", blocked.get_json()["error"])
        self.assertIn("Momentum Alpha", blocked.get_json()["error"])

        removed_alpha = self.client.delete(
            f"/api/research/projects/{project['project_id']}/definition-refs/alpha:Momentum%20Alpha",
            json={"expected_definition_id": alpha["definition_id"]},
        )
        self.assertEqual(200, removed_alpha.status_code, removed_alpha.get_json())
        removed_factor = self.client.delete(
            f"/api/research/projects/{project['project_id']}/definition-refs/factor:Momentum",
            json={"expected_definition_id": factor["definition_id"]},
        )
        self.assertEqual(200, removed_factor.status_code, removed_factor.get_json())
        self.assertTrue(removed_factor.get_json()["data"]["history_preserved"])
        refs = self.client.get(
            f"/api/research/projects/{project['project_id']}/definition-refs"
        ).get_json()["data"]
        self.assertEqual({}, refs)
        library_assets = self.client.get(
            "/api/research/library?component_type=FACTOR"
        ).get_json()["data"]
        self.assertEqual(
            [first_assets[0]["library_asset_id"]],
            [item["library_asset_id"] for item in library_assets],
        )
        usage_after_removal = self.client.get(
            f"/api/research/library/{first_assets[0]['library_asset_id']}/usage"
        )
        self.assertEqual(200, usage_after_removal.status_code, usage_after_removal.get_json())
        self.assertEqual(0, usage_after_removal.get_json()["data"]["research_count"])

    def test_universe_and_requirements_use_explicit_library_references(self):
        source = self.client.post(
            "/api/research/projects",
            json={"title": "Source", "objective": "Publish reusable inputs."},
        ).get_json()["data"]
        consumer = self.client.post(
            "/api/research/projects",
            json={"title": "Consumer", "objective": "Reference exact Library versions."},
        ).get_json()["data"]

        universe = self.client.post(
            "/api/research/universes",
            json={
                "name": "BTC Spot",
                "version": "1.0.0",
                "universe_type": "STATIC_LIST",
                "parameters": {"instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"]},
                "owner_project_id": source["project_id"],
                "library_scope": "PROJECT",
            },
        ).get_json()["data"]
        snapshot = self.client.post(
            f"/api/research/universes/{universe['universe_definition_id']}/snapshots",
            json={"as_of_time": "2026-07-01T00:00:00+00:00", "manifest_ids": []},
        ).get_json()["data"]
        universe_asset = self.client.post(
            f"/api/research/universes/{universe['universe_definition_id']}/publish",
            json={"project_id": source["project_id"]},
        ).get_json()["data"]

        use_universe = self.client.put(
            f"/api/research/projects/{consumer['project_id']}/universe-ref",
            json={
                "universe_snapshot_id": snapshot["universe_snapshot_id"],
                "library_asset_id": universe_asset["library_asset_id"],
            },
        )
        self.assertEqual(200, use_universe.status_code, use_universe.get_json())
        self.assertEqual("LIBRARY", use_universe.get_json()["data"]["origin"])
        self.assertEqual(1, use_universe.get_json()["data"]["library_version"])

        requirements = self.client.post(
            f"/api/research/projects/{source['project_id']}/requirement-sets",
            json={
                "context": {
                    "universe_snapshot_id": snapshot["universe_snapshot_id"],
                    "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
                    "data_type": "bars",
                    "frequency": "1h",
                    "history_start": "2026-01-01T00:00:00+00:00",
                    "history_end": "2026-07-01T00:00:00+00:00",
                },
                "manual_requirements": [{"id": "price", "fields": ["close"]}],
            },
        ).get_json()["data"]
        requirements_asset = self.client.post(
            f"/api/research/projects/{source['project_id']}/requirements/publish",
            json={"requirement_set_id": requirements["requirement_set_id"], "name": "BTC 1h Requirements"},
        )
        self.assertEqual(201, requirements_asset.status_code, requirements_asset.get_json())
        asset = requirements_asset.get_json()["data"]

        use_requirements = self.client.put(
            f"/api/research/projects/{consumer['project_id']}/requirements/library-ref",
            json={"library_asset_id": asset["library_asset_id"]},
        )
        self.assertEqual(200, use_requirements.status_code, use_requirements.get_json())
        derived = use_requirements.get_json()["data"]["requirements"]
        self.assertEqual(consumer["project_id"], derived["project_id"])
        requirement_ref = self.client.get(
            f"/api/research/projects/{consumer['project_id']}/requirements/ref"
        ).get_json()["data"]
        self.assertEqual("LIBRARY", requirement_ref["origin"])
        self.assertEqual(asset["library_asset_id"], requirement_ref["library_asset_id"])

    def test_missing_research_run_result_summary_is_not_found(self):
        response = self.client.get("/api/research/runs/run_missing/result-summary")
        self.assertEqual(404, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        section = self.client.get("/api/research/runs/run_missing/sections/signals")
        self.assertEqual(404, section.status_code)
        self.assertFalse(section.get_json()["ok"])

    def test_requirement_set_library_keeps_immutable_project_versions(self):
        project = self.client.post(
            "/api/research/projects",
            json={"title": "Requirement library", "objective": "Version data contracts."},
        ).get_json()["data"]
        base = {
            "context": {
                "instrument_ids": ["BTCUSDT"],
                "data_type": "bars",
                "frequency": "1h",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-06-01T00:00:00+00:00",
                "adjustment": "NONE",
                "point_in_time_policy": "AS_OF",
                "quality_policy": "STRICT",
                "source_policy": "FIXED",
            },
            "factor_specs": [],
            "backtest_requirements": [{"id": "execution", "fields": ["open"]}],
            "manual_requirements": [{"id": "manual", "fields": ["volume"]}],
        }
        first = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirement-sets",
            json=base,
        ).get_json()["data"]
        revised = dict(base)
        revised["manual_requirements"] = [{"id": "manual", "fields": ["volume", "trade_count"]}]
        second = self.client.post(
            f"/api/research/projects/{project['project_id']}/requirement-sets",
            json=revised,
        ).get_json()["data"]

        library = self.client.get("/api/research/data/requirement-sets").get_json()["data"]
        project_versions = [item for item in library if item["project_id"] == project["project_id"]]
        self.assertEqual([2, 1], [item["version"] for item in project_versions])
        self.assertEqual("RESOLVED", project_versions[0]["status"])
        self.assertEqual("SUPERSEDED", project_versions[1]["status"])
        self.assertEqual(first["requirement_set_id"], project_versions[1]["requirement_set_id"])
        self.assertEqual(second["requirement_set_id"], project_versions[0]["requirement_set_id"])
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_shared_universe_api_uses_stable_identity_and_copy_isolates(self):
        first = self.client.post(
            "/api/research/projects", json={"title": "Shared first", "objective": "Universe API"}
        ).get_json()["data"]
        second = self.client.post(
            "/api/research/projects", json={"title": "Shared second", "objective": "Universe API"}
        ).get_json()["data"]
        created_response = self.client.post(
            f"/api/research/projects/{first['project_id']}/universes",
            json={"definition": {"name": "Shared majors", "type": "instrument_set", "members": ["BTCUSDT", "ETHUSDT"]}},
        )
        self.assertEqual(201, created_response.status_code, created_response.get_json())
        created = created_response.get_json()["data"]
        self.assertEqual(1, created["revision_number"])
        self.assertEqual(2, created["current_resolution"]["member_count"])

        added = self.client.post(
            f"/api/research/projects/{second['project_id']}/universes/add",
            json={"universe_id": created["universe_id"], "role": "PRIMARY"},
        )
        self.assertEqual(201, added.status_code, added.get_json())
        blocked = self.client.patch(
            f"/api/library/universes/{created['universe_id']}",
            json={
                "definition": {"name": "Shared majors", "type": "instrument_set", "members": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
                "expected_current_revision_id": created["current_revision_id"],
                "current_project_id": first["project_id"],
            },
        )
        self.assertEqual(409, blocked.status_code, blocked.get_json())
        self.assertEqual("UNIVERSE_SHARED_EDIT_CONFIRMATION_REQUIRED", blocked.get_json()["code"])

        copied = self.client.post(
            f"/api/research/projects/{first['project_id']}/universes/{created['universe_id']}/copy",
            json={"name": "Private majors", "replace_primary": True},
        )
        self.assertEqual(201, copied.status_code, copied.get_json())
        self.assertNotEqual(created["universe_id"], copied.get_json()["data"]["universe_id"])
        bindings = self.client.get(
            f"/api/research/projects/{first['project_id']}/universes"
        ).get_json()["data"]
        self.assertEqual("Private majors", bindings[0]["name"])
        self.assertEqual("PRIMARY", bindings[0]["role"])

        script = self.client.post(
            "/api/library/universes/script/render", json={"definition": copied.get_json()["data"]["definition"]}
        ).get_json()["data"]
        parsed = self.client.post(
            "/api/library/universes/script/parse", json={"script": script}
        )
        self.assertEqual(200, parsed.status_code, parsed.get_json())
        self.assertEqual("Private majors", parsed.get_json()["data"]["name"])


if __name__ == "__main__":
    unittest.main()
