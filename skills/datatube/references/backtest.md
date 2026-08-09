# Backtest Workflow

Use this reference to create, run, inspect, compare, or report DataTube history
backtests. They are local historical replays and never authorize live orders.

This workflow requires a registered Strategy and a History Case. For a formal,
Manifest-pinned `RESEARCH_BACKTEST` built directly from a Project Alpha, use
[research-agent-workflow.md](research-agent-workflow.md) instead. Do not merge
the two result contracts or reproducibility claims.

## Contents

- Preflight and Strategy selection
- Library Alpha contract
- Case and run creation
- Data identity, batches, workspace, and reporting

## Preflight And Strategy Selection

Check runtime and capabilities, then select the registered Strategy before the
case. Prefer `strategy_id` because it preserves the Strategy signal source.

```bash
python scripts/datatube_client.py health
python scripts/datatube_client.py capabilities
python scripts/datatube_client.py strategies --limit 100
python scripts/datatube_client.py strategy <strategy_id>
```

Require `backtest.read` and the relevant create capability. Read
`signal_source.type` and route exactly as follows:

| Signal source | Engine | Run input |
| --- | --- | --- |
| `LEGACY_STRATEGY_CODE` | `legacy_strategy_code_history.v1` | Prefer `strategy_id`; a raw `strategy_code` remains supported for compatibility. |
| `LIBRARY_ALPHA` | `library_alpha_history.v1` | Require `strategy_id`; do not invent or require `strategy_code`. |

Old Strategy rows without `signal_source` are legacy StrategyCode rows. A
Library Alpha Strategy must remain in `Stop`: its backtest path is ready, but
live execution is `NOT_CONNECTED`.

## Library Alpha Contract

A Library Alpha Strategy pins a published, `VALIDATED` Alpha plus every
published, `VALIDATED` Factor dependency by definition ID, version, and hash.
At run creation the compiler re-resolves that closure. Stop if it changed; do
not silently run a newer Alpha or Factor.

The history adapter executes:

```text
pinned Factors -> pinned Alpha -> target weights -> next-bar-open replay
```

Library Alpha cases currently require:

- only Binance `crypto_spot` legs;
- one shared bar interval;
- a unique `instrument_id` or symbol for every leg;
- instruments compatible with the pinned Universe Snapshot when one exists.

Mixed Binance/Polymarket replay is not an implemented Library Alpha path.

## Select Or Create A Case

List cases first:

```bash
python scripts/datatube_client.py backtest-cases --limit 100
```

Create a case only when no suitable history window and leg set exists:

```json
{
  "case_name": "BTCUSDT trend 1h",
  "collection_name": "Default",
  "strategy_id": 82,
  "legs": [
    {
      "source": "binance",
      "venue": "binance",
      "asset_class": "crypto_spot",
      "symbol": "BTCUSDT",
      "instrument_id": "crypto_spot:binance:BTCUSDT",
      "display_name": "BTC / USDT",
      "interval": "1h"
    }
  ],
  "data_window": {
    "start": "2021-01-01T00:00:00Z",
    "end": "2025-12-31T23:00:00Z",
    "interval": "1h"
  },
  "params": {"initial_cash": 10000}
}
```

```bash
python scripts/datatube_client.py backtest-case-create --data case.json
```

The non-mutating UI evaluator is available when an explicit compatibility
check is needed:

```bash
python scripts/datatube_client.py post /api/history/backtest-cases/evaluate --data compatibility.json
```

Treat evaluator `severity=error` as blocked. Review warnings for missing local
coverage or leg/schema differences before running.

## Create And Wait For A Run

Legacy registered Strategy:

```bash
python scripts/datatube_client.py backtest-run-create <case_id> --data '{"strategy_id":82,"params":{"initial_cash":10000},"run_mode":"async"}'
```

Raw code compatibility path:

```bash
python scripts/datatube_client.py backtest-run-create <case_id> --data '{"strategy_code":"Stragy_Crypto_Trend_Follow","params":{"initial_cash":10000},"run_mode":"async"}'
```

Library Alpha Strategy with explicit comparable execution and portfolio inputs:

```json
{
  "strategy_id": 96,
  "params": {"initial_cash": 10000},
  "execution_spec": {
    "fee_bps": 2,
    "slippage_bps": 10,
    "allow_short": false,
    "allow_leverage": false
  },
  "portfolio_spec": {
    "top_n": 2,
    "rebalance_frequency": "DAILY",
    "max_position_weight": 0.5,
    "cash_buffer": 0.0
  },
  "run_mode": "async"
}
```

Pass that file with `backtest-run-create`. Stored Strategy inputs are merged
first and run inputs override them. The compiled runtime freezes the selected
source, parameters, execution spec, portfolio spec, and `runtime_hash` in
`case_snapshot.run_strategy_runtime`.

```bash
python scripts/datatube_client.py backtest-wait <run_id> --timeout 600 --interval 2
python scripts/datatube_client.py backtest-run <run_id> --equity-limit 50 --orders-limit 50 --events-limit 50 --summary
```

Terminal statuses are `completed`, `failed`, `cancelled`, and `error`.
`backtest-wait` is compact by default; use `--full` only when needed. Never use
limit `0` to mean none because History treats it as unbounded detail.

## Data Identity And Reproducibility

For every result, report `signal_source_type`, engine, and `runtime_hash`.
Library Alpha History runs currently record:

```text
data_identity_mode = HISTORY_CASE_SNAPSHOT
dataset_manifest_ids = []
```

This is reproducible against the frozen Strategy runtime and History Case
snapshot, but it is not a Data Platform Manifest-pinned formal Research Run.
State that distinction explicitly; do not claim Manifest-level reproducibility.

## Batch And Iteration

Create batches by explicit cases, collection, or Strategy. Omit
`strategy_code` for Library Alpha:

```bash
python scripts/datatube_client.py backtest-batch-create --data '{"case_ids":[47,52],"strategy_id":96,"params":{"initial_cash":10000},"run_mode":"async","batch_name":"Alpha comparison"}'
python scripts/datatube_client.py backtest-batches --limit 50
python scripts/datatube_client.py backtest-batch <batch_id> --include-runs 1
```

Compare only runs with compatible legs, windows, data identity, runtime inputs,
and cost assumptions. Rank by return only after checking drawdown, Sharpe,
orders, turnover, coverage, and sample size. For parameter sweeps, read
[backtest-optimization.md](backtest-optimization.md).

## Workspace And Metrics

Completed runs may be opened at:

```text
/strategies/<strategy_id>/workspace?source=backtest&run_id=<run_id>
```

Import when needed:

```bash
python scripts/datatube_client.py post /api/history/backtest-runs/<run_id>/workspace --data '{}'
```

- `Strategy Metrics` and `State Lanes` come from strategy-emitted metrics.
- `Backtest Metrics` and `Backtest State` are replay-derived.
- Library Alpha lineage is under run metrics and runtime fields are in the case
  snapshot.

Do not relabel derived metrics as Strategy Metrics. Old runs may need a rerun
before current runtime, Strategy Metrics, or State Lanes are available.

## Report

Report: run/batch and case IDs; Strategy ID/name; signal source, engine, runtime
hash; legs/window; status/errors; execution/portfolio inputs; equity, return,
drawdown, Sharpe, orders and turnover; Strategy Metrics/State Lanes; lineage and
data identity; risk notes; and the next controlled hypothesis. End by saying
the result is historical analysis and did not approve or execute live trades.
