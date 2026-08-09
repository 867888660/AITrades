"""Read-only API smoke test for the first Research/Data Center endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def main() -> None:
    client = app.test_client()
    capabilities = client.get("/api/research/backtest/capabilities")
    assert capabilities.status_code == 200
    body = capabilities.get_json()
    assert body["ok"] is True
    assert body["data"]["research_provider"]["supports_target_weight"] is True

    catalog = client.get("/api/research/data/catalog")
    assert catalog.status_code == 200
    assert catalog.get_json()["ok"] is True

    artifacts = client.get("/api/research/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.get_json()["ok"] is True

    universes = client.get("/api/research/universes")
    assert universes.status_code == 200
    assert universes.get_json()["ok"] is True

    requirement_sets = client.get("/api/research/data/requirement-sets")
    assert requirement_sets.status_code == 200
    assert requirement_sets.get_json()["ok"] is True

    resolved_plans = client.get("/api/research/resolved-plans")
    assert resolved_plans.status_code == 200 and resolved_plans.get_json()["ok"] is True
    input_bundles = client.get("/api/research/input-bundles")
    assert input_bundles.status_code == 200 and input_bundles.get_json()["ok"] is True
    research_page = client.get("/research")
    assert research_page.status_code == 200

    backfill_jobs = client.get("/api/research/data/backfill/binance/jobs")
    assert backfill_jobs.status_code == 200
    assert backfill_jobs.get_json()["ok"] is True

    backfill_worker = client.get("/api/research/data/backfill/binance/worker-status")
    assert backfill_worker.status_code == 200
    assert backfill_worker.get_json()["ok"] is True

    validation = client.post(
        "/api/research/backtest/validate",
        json={
            "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
            "execution_spec": {
                "signal_generation": "BAR_CLOSE",
                "order_submission": "CURRENT_BAR_CLOSE",
                "fill_price_rule": "CURRENT_CLOSE",
                "fee_model": "FIXED_BPS",
                "slippage_model": "NONE",
                "portfolio_input": "TARGET_POSITION",
            },
        },
    )
    assert validation.status_code == 200
    assert validation.get_json()["data"]["status"] == "READY"

    research_validation = client.post(
        "/api/research/backtest/validate",
        json={"provider": "research", "execution_spec": {}},
    )
    assert research_validation.status_code == 200
    assert research_validation.get_json()["data"]["status"] == "READY"

    projects = client.get("/api/research/projects")
    assert projects.status_code == 200
    assert projects.get_json()["ok"] is True
    print("Research API smoke test passed")


if __name__ == "__main__":
    main()
