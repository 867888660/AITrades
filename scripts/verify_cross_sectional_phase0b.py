"""Acceptance run for the real five-symbol Phase 0B research chain.

This is a local historical replay. It creates research artifacts only and does
not create, approve, or execute a trading strategy.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import (
    AlphaComponent,
    AlphaEngine,
    AlphaSpec,
    BacktestExecutionSpec,
    BinanceHistoryAdapter,
    FactorEngine,
    FactorEvaluator,
    FactorSpec,
    FrozenManifestData,
    PortfolioEngine,
    PortfolioSpec,
    ResearchArtifactMaterializer,
    ResearchBacktestProvider,
    UniverseService,
    AlphaEvaluator,
    EvaluationSpec,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
START = "2026-04-11T00:00:00+00:00"
END = "2026-07-09T23:59:59+00:00"
PROJECT_ID = "phase0b_binance_cross_sectional_v1"


def parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reachable_artifacts(materializer: ResearchArtifactMaterializer, root_id: str) -> set[str]:
    visited: set[str] = set()
    pending = [root_id]
    while pending:
        artifact_id = pending.pop()
        if artifact_id in visited:
            continue
        visited.add(artifact_id)
        for dependency in materializer.artifacts.dependencies(artifact_id):
            if dependency["parent_type"] == "RESEARCH_ARTIFACT":
                pending.append(dependency["parent_id"])
    return visited


def deterministic_payload(result: object) -> dict[str, object]:
    return {
        "orders": [dict(item) for item in result.orders],
        "equity_curve": [dict(item) for item in result.equity_curve],
        "rebalance_events": [dict(item) for item in result.rebalance_events],
        "metrics": dict(result.metrics),
    }


def main() -> None:
    adapter = BinanceHistoryAdapter()
    exports = [
        adapter.export(symbol=symbol, interval="1h", start_time=START, end_time=END)
        for symbol in SYMBOLS
    ]
    assert all(item["row_count"] == 2160 for item in exports)
    frozen_manifests = [FrozenManifestData(adapter.store, item["manifest"].manifest_id) for item in exports]
    manifest_ids = [item.manifest_id for item in frozen_manifests]
    bars: dict[str, list[dict[str, object]]] = {}
    for frozen in frozen_manifests:
        bars.update(frozen.read_bars_by_instrument())
    assert len(bars) == 5
    timelines = [tuple(row["bar_start_time"] for row in rows) for rows in bars.values()]
    assert all(timeline == timelines[0] for timeline in timelines[1:])

    universe_service = UniverseService(adapter.store)
    definition = universe_service.create_definition(
        name="binance_spot_phase0b_static_5",
        version="1.0.0",
        universe_type="STATIC_LIST",
        parameters={"instrument_ids": [item["instrument_id"] for item in exports]},
    )
    snapshot = universe_service.resolve_snapshot(
        universe_definition_id=definition.universe_definition_id,
        as_of_time=START,
        manifests=frozen_manifests,
    )
    assert len(snapshot.actual_instrument_ids) == 5
    assert set(snapshot.dataset_manifest_ids) == set(manifest_ids)

    factor_engine = FactorEngine()
    momentum_spec = FactorSpec(
        name="momentum_20",
        version="1.0.0",
        operator="pct_change",
        window=20,
        minimum_observations=21,
        frequency="1h",
        output_direction="HIGHER_IS_BETTER",
    )
    volatility_spec = FactorSpec(
        name="volatility_20",
        version="1.0.0",
        operator="rolling_return_std",
        window=20,
        minimum_observations=21,
        frequency="1h",
        output_direction="LOWER_IS_BETTER",
    )
    factors = {
        "momentum_20": factor_engine.compute(momentum_spec, bars),
        "volatility_20": factor_engine.compute(volatility_spec, bars),
    }
    materializer = ResearchArtifactMaterializer(adapter.store)
    factor_artifacts = [
        materializer.materialize_factor(
            spec=spec,
            values_by_instrument=factors[spec.name],
            dataset_manifest_ids=manifest_ids,
            universe_snapshot_id=snapshot.universe_snapshot_id,
            project_id=PROJECT_ID,
        )
        for spec in (momentum_spec, volatility_spec)
    ]

    alpha_spec = AlphaSpec(
        name="momentum_quality_cs",
        version="1.0.0",
        components=(
            AlphaComponent("momentum_20", 0.7, "CS_RANK"),
            AlphaComponent("volatility_20", -0.3, "CS_RANK"),
        ),
        minimum_coverage=0.8,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        minimum_cross_section_size=4,
    )
    alpha_engine = AlphaEngine()
    alpha_signals = alpha_engine.build_signals(
        alpha_spec,
        factors,
        universe_snapshot=snapshot,
    )
    assert alpha_signals
    assert all(len(signal["raw_scores"]) == 5 for signal in alpha_signals)
    assert all(signal["available_time"] >= signal["as_of_time"] for signal in alpha_signals)
    alpha_artifact = materializer.materialize_alpha(
        spec=alpha_spec,
        signals=alpha_signals,
        factor_artifact_ids=[item.artifact_id for item in factor_artifacts],
        dataset_manifest_ids=manifest_ids,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        project_id=PROJECT_ID,
    )

    alternate_spec = AlphaSpec(
        name="momentum_only_cs",
        version="1.0.0",
        components=(AlphaComponent("momentum_20", 1.0, "CS_RANK"),),
        minimum_coverage=0.8,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        minimum_cross_section_size=4,
    )
    alternate_signals = alpha_engine.build_signals(alternate_spec, factors, universe_snapshot=snapshot)
    alternate_artifact = materializer.materialize_alpha(
        spec=alternate_spec,
        signals=alternate_signals,
        factor_artifact_ids=[factor_artifacts[0].artifact_id],
        dataset_manifest_ids=manifest_ids,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        project_id=PROJECT_ID,
    )
    alternate_dependencies = materializer.artifacts.dependencies(alternate_artifact.artifact_id)
    assert set(manifest_ids).issubset({item["parent_id"] for item in alternate_dependencies})

    evaluation_spec = EvaluationSpec(
        horizons=(1, 6, 24),
        quantile_count=5,
        minimum_cross_section_size=4,
        top_n=2,
        fee_bps=10.0,
        slippage_bps=5.0,
    )
    factor_evaluation_artifacts = []
    factor_evaluation_summaries = {}
    for factor_spec, factor_artifact in zip((momentum_spec, volatility_spec), factor_artifacts):
        evaluation = FactorEvaluator().evaluate(
            spec=evaluation_spec,
            factor_values_by_instrument=factors[factor_spec.name],
            bars_by_instrument=bars,
            universe_snapshot=snapshot,
        )
        assert evaluation.summary["rank_ic"]["1"]["count"] > 2000
        factor_evaluation_summaries[factor_spec.name] = evaluation.summary
        factor_evaluation_artifacts.append(materializer.materialize_evaluation(
            logical_name=f"{factor_spec.name}_evaluation",
            result=evaluation,
            spec=evaluation_spec,
            input_artifact_id=factor_artifact.artifact_id,
            dataset_manifest_ids=manifest_ids,
            universe_snapshot_id=snapshot.universe_snapshot_id,
            project_id=PROJECT_ID,
        ))
    alpha_evaluation = AlphaEvaluator().evaluate(
        spec=evaluation_spec,
        alpha_signals=alpha_signals,
        bars_by_instrument=bars,
        universe_snapshot=snapshot,
    )
    assert alpha_evaluation.summary["holding_period_decay"]["1"]["count"] > 2000
    alpha_evaluation_artifact = materializer.materialize_evaluation(
        logical_name=f"{alpha_spec.name}_evaluation",
        result=alpha_evaluation,
        spec=evaluation_spec,
        input_artifact_id=alpha_artifact.artifact_id,
        dataset_manifest_ids=manifest_ids,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        project_id=PROJECT_ID,
    )

    portfolio_spec = PortfolioSpec(
        selection_method="TOP_N",
        top_n=2,
        weighting_method="EQUAL_WEIGHT",
        direction="LONG_ONLY",
        rebalance_frequency="DAILY",
        max_position_weight=0.5,
        cash_buffer=0.0,
        universe_snapshot_id=snapshot.universe_snapshot_id,
    )
    portfolio_engine = PortfolioEngine()
    targets = portfolio_engine.build_targets(alpha_signals, portfolio_spec)
    final_bar_open = max(parse_time(rows[-1]["bar_start_time"]) for rows in bars.values())
    executable_targets = [
        item for item in targets
        if parse_time(item["available_time"]) < final_bar_open
    ]
    selected_sets = {tuple(item["selected_instrument_ids"]) for item in executable_targets}
    assert len(executable_targets) >= 85
    assert len(selected_sets) > 1
    target_artifact = materializer.materialize_portfolio_targets(
        spec=portfolio_spec,
        targets=executable_targets,
        alpha_artifact_id=alpha_artifact.artifact_id,
        universe_snapshot_id=snapshot.universe_snapshot_id,
        project_id=PROJECT_ID,
    )

    execution_spec = BacktestExecutionSpec(fee_bps=10.0, slippage_bps=5.0)
    provider = ResearchBacktestProvider()
    result = provider.simulate_manifests(
        manifests=frozen_manifests,
        alpha_signals=executable_targets,
        initial_cash=100_000.0,
        execution_spec=execution_spec,
        portfolio_spec=portfolio_spec,
        universe_snapshot_ids=[snapshot.universe_snapshot_id],
        factor_artifact_ids=[item.artifact_id for item in factor_artifacts],
        alpha_artifact_ids=[alpha_artifact.artifact_id],
        input_artifact_ids=[target_artifact.artifact_id],
    )
    assert result.metrics["rebalance_count"] == len(executable_targets)
    assert result.metrics["rebalance_count"] >= 85
    assert result.metrics["trade_count"] > result.metrics["rebalance_count"]
    assert any(order["side"] == "SELL" for order in result.orders)
    assert any(order["side"] == "BUY" for order in result.orders)
    assert result.metrics["fees"] > 0
    assert result.metrics["slippage_cost"] > 0
    assert min(point["cash"] for point in result.equity_curve) >= 0

    repeat = provider.simulate_manifests(
        manifests=frozen_manifests,
        alpha_signals=executable_targets,
        initial_cash=100_000.0,
        execution_spec=execution_spec,
        portfolio_spec=portfolio_spec,
        universe_snapshot_ids=[snapshot.universe_snapshot_id],
        factor_artifact_ids=[item.artifact_id for item in factor_artifacts],
        alpha_artifact_ids=[alpha_artifact.artifact_id],
        input_artifact_ids=[target_artifact.artifact_id],
    )
    assert deterministic_payload(result) == deterministic_payload(repeat)

    artifacts = materializer.materialize_backtest(
        logical_name="phase0b_momentum_quality_top2_daily",
        result=result,
        portfolio_target_artifact_id=target_artifact.artifact_id,
        project_id=PROJECT_ID,
    )
    reachable = reachable_artifacts(materializer, artifacts["result"].artifact_id)
    assert alpha_artifact.artifact_id in reachable
    assert target_artifact.artifact_id in reachable
    assert all(item.artifact_id in reachable for item in factor_artifacts)
    with adapter.store.connection() as conn:
        pin = conn.execute(
            "SELECT 1 FROM artifact_pins WHERE artifact_id = ?",
            (artifacts["result"].artifact_id,),
        ).fetchone()
    assert pin is not None

    output = {
        "status": "PASS",
        "symbols": list(SYMBOLS),
        "bars_per_symbol": 2160,
        "manifest_ids": manifest_ids,
        "universe_snapshot_id": snapshot.universe_snapshot_id,
        "factor_artifact_ids": [item.artifact_id for item in factor_artifacts],
        "alpha_artifact_id": alpha_artifact.artifact_id,
        "alternate_alpha_artifact_id": alternate_artifact.artifact_id,
        "factor_evaluation_artifact_ids": [item.artifact_id for item in factor_evaluation_artifacts],
        "alpha_evaluation_artifact_id": alpha_evaluation_artifact.artifact_id,
        "momentum_rank_ic_1h": factor_evaluation_summaries["momentum_20"]["rank_ic"]["1"]["mean"],
        "alpha_spread_1h": alpha_evaluation.summary["holding_period_decay"]["1"]["long_short_spread"],
        "alpha_rank_stability": alpha_evaluation.summary["average_rank_stability"],
        "alpha_membership_turnover": alpha_evaluation.summary["average_membership_turnover"],
        "portfolio_target_artifact_id": target_artifact.artifact_id,
        "backtest_result_artifact_id": artifacts["result"].artifact_id,
        "alpha_cross_sections": len(alpha_signals),
        "daily_rebalances": result.metrics["rebalance_count"],
        "selected_pair_variants": len(selected_sets),
        "trade_count": result.metrics["trade_count"],
        "final_equity": result.metrics["final_equity"],
        "total_return": result.metrics["total_return"],
        "fees": result.metrics["fees"],
        "slippage_cost": result.metrics["slippage_cost"],
        "deterministic_replay": True,
        "live_execution": False,
    }
    print("Phase 0B real cross-sectional acceptance passed")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
