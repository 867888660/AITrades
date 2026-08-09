from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    BundleInputClosure,
    BundleInputMode,
    BundleIntegrityStatus,
    BundleReuseStatus,
    DataPlatformStore,
    FrozenBundleStatus,
    HistoricalAuthorizationEvidence,
    IdempotencyConflictError,
    ReadinessCheck,
    ReadinessDimension,
    ReadinessReport,
    ReadinessStatus,
    RemediationCode,
    ResearchReasonCode,
    ResearchRunPreviewService,
    build_preview_fingerprint,
    enforce_idempotent_run_request,
    is_preview_stale,
)


class ResearchRunContractsTest(unittest.TestCase):
    def test_readiness_is_layered_and_overall_is_server_aggregated(self) -> None:
        report = ReadinessReport.build(
            [
                ReadinessCheck(
                    code=ResearchReasonCode.DEFINITION_VALID,
                    dimension=ReadinessDimension.DEFINITION,
                    status=ReadinessStatus.READY,
                ),
                ReadinessCheck(
                    code=ResearchReasonCode.WARMUP_NOT_COVERED,
                    dimension=ReadinessDimension.DATA,
                    status=ReadinessStatus.BLOCKED,
                    object_ref="BTCUSDT:1h",
                    required={"start": "2026-03-27T00:00:00Z"},
                    actual={"start": "2026-04-11T00:00:00Z"},
                    remediation_code=RemediationCode.CREATE_BACKFILL_TASK,
                    message="warmup range is not covered",
                ),
                ReadinessCheck(
                    code=ResearchReasonCode.AUTHORIZATION_VALID,
                    dimension=ReadinessDimension.AUTHORIZATION,
                    status=ReadinessStatus.READY,
                ),
                ReadinessCheck(
                    code=ResearchReasonCode.WORKER_CAPABILITY_UNKNOWN,
                    dimension=ReadinessDimension.EXECUTION,
                    status=ReadinessStatus.UNKNOWN,
                ),
            ]
        )
        payload = report.to_dict()
        self.assertEqual("BLOCKED", payload["overall"]["status"])
        self.assertEqual(
            {"DEFINITION", "DATA", "AUTHORIZATION", "EXECUTION"},
            set(payload["dimensions"]),
        )
        data_check = payload["dimensions"]["DATA"]["checks"][0]
        self.assertEqual("WARMUP_NOT_COVERED", data_check["code"])
        self.assertEqual("CREATE_BACKFILL_TASK", data_check["remediation_code"])

    def test_missing_dimension_is_unknown_not_ready(self) -> None:
        report = ReadinessReport.build(
            [
                ReadinessCheck(
                    code=ResearchReasonCode.DEFINITION_VALID,
                    dimension=ReadinessDimension.DEFINITION,
                    status=ReadinessStatus.READY,
                )
            ]
        )
        payload = report.to_dict()
        self.assertEqual("UNKNOWN", payload["overall"]["status"])
        self.assertEqual(
            "DIMENSION_NOT_EVALUATED",
            payload["dimensions"]["DATA"]["checks"][0]["code"],
        )

    def test_preview_fingerprint_covers_all_four_closures(self) -> None:
        arguments = {
            "definition_closure": {
                "project_version": 4,
                "universe_definition_id": "universe_crypto",
                "universe_definition_version": "3",
                "universe_snapshot_id": "snapshot_1",
                "factor_definitions": [
                    {"factor_definition_id": "factor_momentum", "version": "4", "spec_hash": "factor-spec"}
                ],
                "alpha_definitions": [
                    {"alpha_definition_id": "alpha_cross_section", "version": "3", "spec_hash": "alpha-spec"}
                ],
                "requirement_set_id": "requirements_1",
            },
            "data_resolution_closure": {
                "resolved_manifest_ids": ["manifest_a", "manifest_b"],
                "resolver_version": "resolver.v1",
                "source_selection_policy_version": "selection.v1",
            },
            "execution_closure": {
                "evaluation_spec_hash": "evaluation",
                "portfolio_spec_hash": "portfolio",
                "execution_spec_hash": "execution",
                "engine_version": "engine.v1",
                "code_hash": "code.v1",
                "readiness_rule_version": "readiness.v1",
            },
            "authorization_closure": {
                "grant_id": "grant_1",
                "grant_version": "1",
                "policy_version": "policy.v1",
            },
        }
        first = build_preview_fingerprint(**arguments)
        reordered = build_preview_fingerprint(
            definition_closure={
                key: arguments["definition_closure"][key]
                for key in reversed(tuple(arguments["definition_closure"]))
            },
            data_resolution_closure=arguments["data_resolution_closure"],
            execution_closure=arguments["execution_closure"],
            authorization_closure=arguments["authorization_closure"],
        )
        self.assertEqual(first.value, reordered.value)

        changed_resolver = dict(arguments["data_resolution_closure"])
        changed_resolver["resolver_version"] = "resolver.v2"
        second = build_preview_fingerprint(
            definition_closure=arguments["definition_closure"],
            data_resolution_closure=changed_resolver,
            execution_closure=arguments["execution_closure"],
            authorization_closure=arguments["authorization_closure"],
        )
        self.assertNotEqual(first.value, second.value)
        self.assertTrue(is_preview_stale(first.value, second))

        missing_resolver = dict(arguments["data_resolution_closure"])
        missing_resolver.pop("resolver_version")
        with self.assertRaisesRegex(ValueError, "resolver_version"):
            build_preview_fingerprint(
                definition_closure=arguments["definition_closure"],
                data_resolution_closure=missing_resolver,
                execution_closure=arguments["execution_closure"],
                authorization_closure=arguments["authorization_closure"],
            )

    def test_idempotency_key_conflicts_on_different_preview(self) -> None:
        self.assertEqual(
            "fingerprint-a",
            enforce_idempotent_run_request(
                idempotency_key="run-request-1",
                existing_preview_fingerprint="fingerprint-a",
                requested_preview_fingerprint="fingerprint-a",
            ),
        )
        with self.assertRaises(IdempotencyConflictError) as raised:
            enforce_idempotent_run_request(
                idempotency_key="run-request-1",
                existing_preview_fingerprint="fingerprint-a",
                requested_preview_fingerprint="fingerprint-b",
            )
        self.assertEqual("IDEMPOTENCY_KEY_CONFLICT", raised.exception.code)

    def test_bundle_keeps_historical_authorization_separate_from_reuse(self) -> None:
        evidence = HistoricalAuthorizationEvidence(
            grant_id="grant_1",
            grant_version="3",
            scope_snapshot={"providers": ["binance"]},
            policy_version="policy.v2",
            authorization_check_result={"status": "READY"},
        )
        status = FrozenBundleStatus(
            integrity_status=BundleIntegrityStatus.VERIFIED,
            reuse_status=BundleReuseStatus.PROHIBITED,
            reuse_reason_code="GRANT_REVOKED",
        )
        self.assertEqual("grant_1", evidence.to_dict()["grant_id"])
        self.assertEqual("FROZEN", status.to_dict()["lifecycle_status"])
        self.assertEqual("VERIFIED", status.to_dict()["integrity_status"])
        self.assertEqual("PROHIBITED", status.to_dict()["reuse_status"])
        with self.assertRaisesRegex(ValueError, "DAMAGED"):
            FrozenBundleStatus(integrity_status=BundleIntegrityStatus.DAMAGED)

    def test_bundle_input_closure_supports_definition_and_artifact_runs(self) -> None:
        common = {
            "run_type": "RESEARCH_BACKTEST",
            "exact_manifest_ids": ("manifest_b", "manifest_a"),
            "universe_snapshot_id": "universe_snapshot_1",
            "requirement_set_id": "requirement_set_1",
            "engine_version": "engine.v1",
            "code_hash": "code.v1",
            "resolver_version": "resolver.v1",
            "source_selection_policy_version": "selection.v1",
        }
        definitions = BundleInputClosure(
            input_mode=BundleInputMode.DEFINITIONS,
            resolved_instrument_weights={"equity:XNAS:TSLA": 0.6, "equity:XNYS:IBM": 0.4},
            universe_resolution_metadata={
                "universe_type": "benchmark_set",
                "benchmark": {"benchmark_id": "000300.SH"},
            },
            factor_definitions=(
                {"factor_definition_id": "factor_1", "version": "4", "spec_hash": "factor-spec"},
            ),
            alpha_definitions=(
                {"alpha_definition_id": "alpha_1", "version": "3", "spec_hash": "alpha-spec"},
            ),
            **common,
        )
        self.assertEqual([], definitions.to_dict()["input_factor_artifact_ids"])
        self.assertEqual(["manifest_a", "manifest_b"], definitions.to_dict()["exact_manifest_ids"])
        self.assertEqual(
            "000300.SH",
            definitions.to_dict()["universe_resolution_metadata"]["benchmark"]["benchmark_id"],
        )

        artifacts = BundleInputClosure(
            input_mode=BundleInputMode.PRECOMPUTED_ARTIFACTS,
            input_factor_artifact_ids=("factor_artifact_1",),
            input_alpha_artifact_ids=("alpha_artifact_1",),
            **common,
        )
        self.assertEqual(["factor_artifact_1"], artifacts.to_dict()["input_factor_artifact_ids"])
        self.assertNotIn("produced_factor_artifact_ids", artifacts.to_dict())

    def test_group_universe_is_blocked_instead_of_silently_flattened(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            service = ResearchRunPreviewService(
                DataPlatformStore(Path(root) / "metadata.db")
            )
            _closure, checks = service._execution_closure(
                "FACTOR_EVALUATION",
                {
                    "universe_snapshot_id": "snapshot_group",
                    "universe_resolution_id": "resolution_group",
                    "resolved_instrument_tuples": [["A", "B"]],
                    "factor_definitions": [],
                    "alpha_definitions": [],
                },
                {
                    "evaluation_spec": {
                        "horizons": [1],
                        "minimum_cross_section_size": 1,
                    }
                },
            )
        self.assertTrue(any(
            str(item.code) == "UNIVERSE_GROUP_EXECUTION_UNSUPPORTED"
            and item.status == ReadinessStatus.BLOCKED
            for item in checks
        ))

    def test_alpha_evaluation_and_research_backtest_require_distinct_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            service = ResearchRunPreviewService(
                DataPlatformStore(Path(root) / "metadata.db")
            )
            definitions = {
                "universe_snapshot_id": "snapshot_alpha",
                "resolved_instrument_tuples": [],
                "factor_definitions": [],
                "alpha_definitions": [],
            }
            _closure, missing_checks = service._execution_closure(
                "ALPHA_EVALUATION",
                definitions,
                {},
            )
            self.assertTrue(any(
                item.status == ReadinessStatus.BLOCKED
                and item.actual.get("missing") == ["evaluation_spec"]
                for item in missing_checks
            ))
            closure, ready_checks = service._execution_closure(
                "ALPHA_EVALUATION",
                definitions,
                {
                    "evaluation_spec": {"horizons": [1], "minimum_cross_section_size": 1},
                },
            )
            self.assertFalse(any(item.status == ReadinessStatus.BLOCKED for item in ready_checks))
            self.assertIn("evaluation-engine", closure["engine_version"])
            self.assertNotIn("portfolio-engine", closure["engine_version"])
            self.assertNotIn("research-backtest", closure["engine_version"])

            backtest_closure, backtest_checks = service._execution_closure(
                "RESEARCH_BACKTEST",
                definitions,
                {
                    "portfolio_spec": {"top_n": 1},
                    "execution_spec": {"fee_bps": 2, "slippage_bps": 10},
                },
            )
            self.assertFalse(any(item.status == ReadinessStatus.BLOCKED for item in backtest_checks))
            self.assertTrue(any(
                item.code == ResearchReasonCode.BENCHMARK_NOT_CONFIGURED
                and item.status == ReadinessStatus.WARNING
                for item in backtest_checks
            ))
            self.assertIn("portfolio-engine", backtest_closure["engine_version"])
            self.assertIn("research-backtest", backtest_closure["engine_version"])


if __name__ == "__main__":
    unittest.main()
