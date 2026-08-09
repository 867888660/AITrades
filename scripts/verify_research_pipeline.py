"""Run the first real Data -> Factor -> Alpha -> Backtest chain."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import (
    AlphaComponent,
    AlphaEngine,
    AlphaSpec,
    ResearchArtifactMaterializer,
    BinanceHistoryAdapter,
    FactorEngine,
    FactorSpec,
    FrozenManifestData,
    ResearchBacktestProvider,
)


def main() -> None:
    adapter = BinanceHistoryAdapter()
    exported = adapter.export(symbol="BTCUSDT", interval="1m", limit=1000)
    frozen = FrozenManifestData(adapter.store, exported["manifest"].manifest_id)
    bars = frozen.read_bars_by_instrument()
    instrument_id = exported["instrument_id"]

    factor_engine = FactorEngine()
    momentum_spec = FactorSpec(name="momentum_20", version="1.0.0", operator="pct_change", window=20)
    volatility_spec = FactorSpec(name="volatility_20", version="1.0.0", operator="rolling_std", window=20)
    factors = {
        "momentum_20": factor_engine.compute(momentum_spec, bars),
        "volatility_20": factor_engine.compute(volatility_spec, bars),
    }
    materializer = ResearchArtifactMaterializer(adapter.store)
    factor_artifacts = [
        materializer.materialize_factor(
            spec=momentum_spec,
            values_by_instrument=factors["momentum_20"],
            dataset_manifest_ids=[exported["manifest"].manifest_id],
            project_id="smoke_research_pipeline",
        ),
        materializer.materialize_factor(
            spec=volatility_spec,
            values_by_instrument=factors["volatility_20"],
            dataset_manifest_ids=[exported["manifest"].manifest_id],
            project_id="smoke_research_pipeline",
        ),
    ]
    alpha_engine = AlphaEngine()
    alpha_spec = AlphaSpec(
        name="btc_momentum_rank",
        version="1.0.0",
        components=(
            AlphaComponent("momentum_20", 1.0, "RANK"),
            AlphaComponent("volatility_20", -0.25, "RANK"),
        ),
    )
    alpha_signals = alpha_engine.build_signals(
        alpha_spec,
        factors,
    )
    targets = alpha_engine.top_n_equal_weight(alpha_signals, top_n=1, max_position_weight=1.0)
    alpha_artifact = materializer.materialize_alpha(
        spec=alpha_spec,
        signals=targets,
        factor_artifact_ids=[artifact.artifact_id for artifact in factor_artifacts],
        dataset_manifest_ids=[exported["manifest"].manifest_id],
        project_id="smoke_research_pipeline",
    )
    provider = ResearchBacktestProvider()
    result = provider.simulate_manifests(
        manifests=[frozen],
        alpha_signals=targets[:-1],
        initial_cash=10_000,
        fee_bps=2,
        slippage_bps=10,
        input_artifact_ids=[alpha_artifact.artifact_id],
    )
    assert len(alpha_signals) > 0
    assert len(targets) == len(alpha_signals)
    assert result.metrics["bar_count"] == 1000
    assert result.metrics["trade_count"] >= 1
    assert result.dataset_manifest_ids == (exported["manifest"].manifest_id,)
    assert result.input_artifact_ids == (alpha_artifact.artifact_id,)
    assert len(materializer.artifacts.dependencies(alpha_artifact.artifact_id)) == 3
    print("Research pipeline smoke test passed")
    print({
        "manifest_id": exported["manifest"].manifest_id,
        "bars": len(bars[instrument_id]),
        "factor_signals": len(alpha_signals),
        "target_signals": len(targets),
        "factor_artifacts": [artifact.artifact_id for artifact in factor_artifacts],
        "alpha_artifact": alpha_artifact.artifact_id,
        "trade_count": result.metrics["trade_count"],
        "final_equity": result.metrics["final_equity"],
    })


if __name__ == "__main__":
    main()
