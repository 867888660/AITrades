# Backtest Workflow

Use this reference when an agent needs to create, run, inspect, compare, or report
DataTube backtests. Backtests are local historical replays only; they do not
approve or place live orders.

## Hard Boundary

Do not approve, reject, request changes, place live orders, switch a strategy to
real trading, or modify private keys. A successful backtest is analysis evidence,
not execution approval.

## Preflight

1. Check runtime health and capabilities:

```bash
python scripts/datatube_client.py health
python scripts/datatube_client.py capabilities
```

2. Confirm these capabilities when creating backtests:

```text
backtest.read
backtest.case.create
backtest.run.create
backtest.batch.create
```

3. Prefer controlled agent APIs under `/api/agent/backtests/...`.
4. Use direct history APIs only for UI-only actions such as rerun, rename,
   delete, or importing a run into the workspace.

## Select Or Create A Case

List existing cases first:

```bash
python scripts/datatube_client.py backtest-cases --limit 100
```

Create a case when no suitable one exists. A case needs at least one leg. For a
Binance crypto spot leg, use fields like:

```json
{
  "case_name": "BTCUSDT trend follow 2025-06",
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
      "interval": "1m"
    }
  ],
  "data_window": {
    "start": "2025-06-04T16:04",
    "end": "2025-07-01T16:04",
    "interval": "1m"
  },
  "params": {
    "initial_cash": "10000",
    "fast_window": "20",
    "slow_window": "60"
  }
}
```

Create with:

```bash
python scripts/datatube_client.py backtest-case-create --data '<json>'
```

## Create And Wait For A Run

Create a run from an existing case:

```bash
python scripts/datatube_client.py backtest-run-create <case_id> --data '{"strategy_id":82,"strategy_code":"Stragy_Crypto_Trend_Follow","params":{"entry_z":"0.002","exit_z":"0.0005","stop_loss_pct":"0.04","trailing_stop_pct":"0.08"},"run_mode":"async"}'
```

Wait for completion:

```bash
python scripts/datatube_client.py backtest-wait <run_id> --timeout 600 --interval 2
```

`backtest-wait` returns a compact summary by default so agents do not dump a
full equity/order/event payload. Use `--full` only when the raw payload is truly
needed. Do not use limit `0` as "return none"; the DataTube history service
treats `0` as unbounded/full detail.

Read a compact detail summary:

```bash
python scripts/datatube_client.py backtest-run <run_id> --equity-limit 50 --orders-limit 50 --events-limit 50 --summary
```

Read raw details only when needed:

```bash
python scripts/datatube_client.py backtest-run <run_id> --equity-limit 1000 --orders-limit 1000 --events-limit 300
```

Statuses to treat as terminal:

```text
completed
failed
cancelled
error
```

If a run is old and lacks strategy `metrics` in equity point metadata, rerun it
with the current executor before claiming Strategy Metrics or State Lanes are
available.

## Batch Backtest

Use batch mode when comparing many cases or parameter sets.

Create by explicit cases:

```bash
python scripts/datatube_client.py backtest-batch-create --data '{"case_ids":[47,52],"strategy_id":82,"strategy_code":"Stragy_Crypto_Trend_Follow","params":{"initial_cash":"10000"},"run_mode":"async","batch_name":"BTC trend sweep"}'
```

Create by collection or strategy:

```bash
python scripts/datatube_client.py backtest-batch-create --data '{"collection_name":"Default","strategy_id":82,"max_cases":20,"params":{"initial_cash":"10000"},"run_mode":"async"}'
```

Inspect:

```bash
python scripts/datatube_client.py backtest-batches --limit 50
python scripts/datatube_client.py backtest-batch <batch_id> --include-runs 1
```

Rank runs by return only after checking drawdown, order count, sample size, and
whether all runs used comparable legs and windows.

## Workspace And Chart Analysis

Use the workspace after a completed run when visual or metric-state analysis is
needed. The workspace URL usually follows:

```text
/strategies/<strategy_id>/workspace?source=backtest&run_id=<run_id>
```

Import a history run into workspace when needed:

```bash
python scripts/datatube_client.py post /api/history/backtest-runs/<run_id>/workspace --data '{}'
```

Important chart groups:

- `Strategy Metrics`: numeric fields emitted by strategy code in `metrics`.
- `State Lanes`: state/text/bool fields emitted by strategy code in `metrics`.
- `Backtest Metrics`: derived replay metrics such as return and drawdown.
- `Backtest State`: derived replay state such as flat/long/short position.

Do not label derived Backtest Metrics as Strategy Metrics. If Strategy Metrics
or State Lanes are missing, say whether the run is old, the strategy emitted no
metrics, or the run did not save `strategy_metrics` / `strategy_metrics_meta`.

## Analysis Report

Return a compact report with:

```text
run_id / batch_id
case_id and strategy_code
legs and data window
status and errors
initial/final equity
total return
max drawdown
Sharpe if present
order count and turnover clue
Strategy Metrics observed
State Lanes observed
best/worst periods or state transitions
risk notes
next parameter or data checks
```

Use cautious wording. Backtest performance is historical and local to the chosen
data, parameters, fees, and execution model.

For AI parameter sweeps that create many runs, read
[backtest-optimization.md](backtest-optimization.md).
