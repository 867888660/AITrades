"""End-to-end smoke test for next-bar-open target-weight research replay."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import BacktestExecutionSpec, ResearchBacktestProvider


def main() -> None:
    bars = {
        "crypto_spot:BINANCE:AAAUSDT": [
            {"event_time": "2025-01-01T00:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
            {"event_time": "2025-01-01T01:00:00+00:00", "open": 110, "high": 112, "low": 109, "close": 110, "volume": 10},
            {"event_time": "2025-01-01T02:00:00+00:00", "open": 120, "high": 121, "low": 119, "close": 120, "volume": 10},
        ],
        "crypto_spot:BINANCE:BBBUSDT": [
            {"event_time": "2025-01-01T00:00:00+00:00", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 10},
            {"event_time": "2025-01-01T01:00:00+00:00", "open": 190, "high": 191, "low": 189, "close": 190, "volume": 10},
            {"event_time": "2025-01-01T02:00:00+00:00", "open": 180, "high": 181, "low": 179, "close": 180, "volume": 10},
        ],
    }
    provider = ResearchBacktestProvider()
    spec = BacktestExecutionSpec()
    signals = [
        {
            "as_of_time": "2025-01-01T00:00:00+00:00",
            "weights": {
                "crypto_spot:BINANCE:AAAUSDT": 0.5,
                "crypto_spot:BINANCE:BBBUSDT": 0.5,
            },
        },
        {
            "as_of_time": "2025-01-01T01:00:00+00:00",
            "weights": {
                "crypto_spot:BINANCE:AAAUSDT": 1.0,
                "crypto_spot:BINANCE:BBBUSDT": 0.0,
            },
        },
    ]
    result = provider.simulate(
        bars_by_instrument=bars,
        alpha_signals=signals,
        initial_cash=10_000,
        fee_bps=0,
        slippage_bps=0,
        execution_spec=spec,
    )
    assert result.metrics["trade_count"] == 4
    assert result.metrics["final_equity"] > 10_000
    assert result.metrics["fees"] == 0
    assert result.equity_curve[0]["cash"] == 10_000
    assert result.orders[0]["event_time"] == "2025-01-01T01:00:00+00:00"
    assert result.orders[0]["signal_time"] == "2025-01-01T00:00:00+00:00"
    assert result.orders[0]["price"] == 110
    assert result.orders[-1]["event_time"] == "2025-01-01T02:00:00+00:00"

    second = provider.simulate(
        bars_by_instrument=bars,
        alpha_signals=signals,
        initial_cash=10_000,
        fee_bps=0,
        slippage_bps=0,
        execution_spec=spec,
    )
    assert result.to_dict() == second.to_dict()
    print("Research backtest smoke test passed")
    print({
        "final_equity": result.metrics["final_equity"],
        "trade_count": result.metrics["trade_count"],
        "execution_engine": result.metrics["execution_engine"],
    })


if __name__ == "__main__":
    main()
