"""Verify the Phase 0 backtest contract against the current provider facts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import BacktestExecutionSpec, ExistingBacktestAdapter


def main() -> None:
    adapter = ExistingBacktestAdapter()
    requested = {
        "instrument_ids": [
            "crypto_spot:BINANCE:BTCUSDT",
            "crypto_spot:BINANCE:ETHUSDT",
            "crypto_spot:BINANCE:SOLUSDT",
            "crypto_spot:BINANCE:BNBUSDT",
            "crypto_spot:BINANCE:XRPUSDT",
        ],
        "alpha_output": {"artifact_id": "alpha_demo_v1"},
        "execution_spec": BacktestExecutionSpec().to_dict(),
    }
    result = adapter.validate(requested)
    assert result["status"] == "BLOCKED"
    codes = {item["code"] for item in result["issues"]}
    assert "ORDER_SUBMISSION_UNSUPPORTED" in codes
    assert "FILL_PRICE_UNSUPPORTED" in codes
    assert "SLIPPAGE_MODEL_UNSUPPORTED" in codes
    assert "TARGET_WEIGHT_UNSUPPORTED" in codes
    assert "ALPHA_OUTPUT_UNSUPPORTED" in codes

    legacy_compatible = adapter.validate({
        "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
        "execution_spec": {
            "signal_generation": "BAR_CLOSE",
            "order_submission": "CURRENT_BAR_CLOSE",
            "fill_price_rule": "CURRENT_CLOSE",
            "fee_model": "FIXED_BPS",
            "slippage_model": "NONE",
            "portfolio_input": "TARGET_POSITION",
        },
    })
    assert legacy_compatible["status"] == "READY"
    assert any(item["code"] == "DATASET_MANIFEST_NOT_PINNED" for item in legacy_compatible["warnings"])

    rejected = adapter.submit(requested)
    assert rejected["accepted"] is False
    assert rejected["status"] == "BLOCKED"
    print("Backtest contract smoke test passed")
    print(json.dumps({
        "requested_status": result["status"],
        "requested_issue_codes": sorted(codes),
        "legacy_compatible_status": legacy_compatible["status"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
