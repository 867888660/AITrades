"""Export a real local Binance history slice and run it through research replay."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from services.data_platform import BinanceHistoryAdapter, FrozenManifestData, ResearchBacktestProvider


def main() -> None:
    adapter = BinanceHistoryAdapter()
    exported = adapter.export(symbol="BTCUSDT", interval="1m", limit=1000)
    manifest = exported["manifest"]
    latest_catalog = adapter.catalog.get_catalog(exported["dataset_id"])
    assert latest_catalog is not None
    assert latest_catalog.status == "READY"
    assert latest_catalog.latest_manifest_id == manifest.manifest_id
    frozen = FrozenManifestData(adapter.store, manifest.manifest_id)
    bars_by_instrument = frozen.read_bars_by_instrument()
    instrument_id = exported["instrument_id"]
    assert len(bars_by_instrument[instrument_id]) == 1000
    assert manifest.status == "READY"
    assert manifest.partitions

    first_bar = bars_by_instrument[instrument_id][0]
    provider = ResearchBacktestProvider()
    result = provider.simulate(
        bars_by_instrument=bars_by_instrument,
        alpha_signals=[{
            "as_of_time": first_bar["bar_start_time"],
            "weights": {instrument_id: 1.0},
        }],
        initial_cash=10_000,
        fee_bps=2,
        slippage_bps=10,
        dataset_manifest_ids=[manifest.manifest_id],
    )
    assert result.metrics["bar_count"] == 1000
    assert result.metrics["trade_count"] == 1
    assert result.orders[0]["event_time"] == bars_by_instrument[instrument_id][1]["bar_start_time"]
    assert result.dataset_manifest_ids == (manifest.manifest_id,)

    parquet_path = Path(manifest.partitions[0].file_uri)
    if not parquet_path.is_absolute():
        parquet_path = Path(__file__).resolve().parents[1] / parquet_path
    duckdb_count = duckdb.connect().execute(
        "SELECT COUNT(*) FROM read_parquet(?)",
        [str(parquet_path)],
    ).fetchone()[0]
    assert duckdb_count == 1000
    print("Real history export smoke test passed")
    print({
        "dataset_id": exported["dataset_id"],
        "manifest_id": manifest.manifest_id,
        "row_count": exported["row_count"],
        "parquet_rows": duckdb_count,
        "backtest_final_equity": result.metrics["final_equity"],
        "backtest_trade_count": result.metrics["trade_count"],
    })


if __name__ == "__main__":
    main()
