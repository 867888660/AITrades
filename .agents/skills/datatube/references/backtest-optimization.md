# Backtest Optimization

Use this reference when the user wants AI to batch backtest a strategy, analyze
results, and propose revised parameters. This workflow is research-only: do not
apply parameters to live trading without explicit human confirmation.

## Contents

- Strategy/source selection and input spec
- Sweep execution and scoring
- Controlled iteration and reporting

## Human/AI Parity

Map the human workflow to agent operations:

```text
Human selects case/legs      -> agent uses case_id/case_ids
Human selects Strategy       -> agent uses strategy_id and its signal source
Human edits parameters       -> agent writes base_params/search_space
Human starts batch backtest  -> agent creates runs under one batch_id
Human checks history         -> agent reads runs/batch summaries
Human opens workspace        -> agent imports/views completed run when needed
Human changes parameters     -> agent proposes candidate_params for next round
```

The current DataTube batch API batches cases with one parameter set. For a
single case with many parameter combinations, use
`scripts/backtest_optimizer.py`; it creates multiple runs under one generated
`batch_id`.

Resolve the registered Strategy first. For `LEGACY_STRATEGY_CODE`, a raw
`strategy_code` remains optional compatibility input. For `LIBRARY_ALPHA`, use
`strategy_id` only; the optimizer preserves its pinned Alpha/Factor closure and
may forward fixed `execution_spec` and `portfolio_spec` objects to every run.

## Input Spec

Create a JSON spec. Keep the first sweep small, usually 10-30 runs.

```json
{
  "case_ids": [47],
  "strategy_id": 82,
  "strategy_code": "Stragy_Crypto_Trend_Follow",
  "objective": "risk_adjusted",
  "max_runs": 18,
  "base_params": {
    "entry_z": "0.002",
    "exit_z": "0.0005",
    "fast_window": "20",
    "slow_window": "60",
    "stop_loss_pct": "0.04",
    "trailing_stop_pct": "0.08",
    "initial_cash": "10000"
  },
  "search_space": {
    "entry_z": [0.0015, 0.002, 0.0025],
    "exit_z": [0.0003, 0.0005],
    "fast_window": [12, 20, 30]
  }
}
```

For a Library Alpha sweep, omit `strategy_code` and freeze the cost/portfolio
controls outside the searched hypothesis:

```json
{
  "case_ids": [47],
  "strategy_id": 96,
  "objective": "risk_adjusted",
  "max_runs": 12,
  "base_params": {"initial_cash": 10000},
  "search_space": {"signal_threshold": [0.0, 0.1, 0.2]},
  "execution_spec": {"fee_bps": 2, "slippage_bps": 10},
  "portfolio_spec": {
    "top_n": 2,
    "rebalance_frequency": "DAILY",
    "max_position_weight": 0.5
  }
}
```

Required:

```text
case_id or case_ids
strategy_id, or strategy_code only for legacy compatibility
base_params
search_space for a real sweep
```

Optional:

```text
objective: risk_adjusted | return | drawdown
max_runs: cap generated combinations
max_orders: penalize overtrading
drawdown_weight, sharpe_weight
timeout, interval
detail_equity_limit, detail_orders_limit, detail_events_limit
batch_id, batch_name
execution_spec, portfolio_spec (fixed across generated runs)
```

Test one major hypothesis per round. An intervention may change multiple linked
fields, but keep unrelated variables, data window, runtime source, execution
costs, and portfolio construction controlled. Do not compare Library Alpha runs
when their `runtime_hash` or `data_identity_mode` changed unless that change is
the explicit hypothesis.

## Run

Preview before creating runs:

```bash
python scripts/backtest_optimizer.py run --spec spec.json --dry-run
```

On PowerShell, prefer a spec file or stdin because inline JSON quoting is easy
to corrupt:

```powershell
$spec | python scripts/backtest_optimizer.py run --spec - --dry-run
```

Execute and analyze:

```bash
python scripts/backtest_optimizer.py run --spec spec.json --out reports/backtest-opt-result.json
```

Analyze an existing batch:

```bash
python scripts/backtest_optimizer.py analyze <batch_id> --spec '{"objective":"risk_adjusted"}'
```

## Scoring

Do not pick parameters by return alone. Read these fields:

```text
total_return
max_drawdown
sharpe
orders
equity_points
strategy_metric_fields
state_lane_fields
signal_source_type, backtest_engine, strategy_runtime_hash
lineage.data_identity_mode
```

The helper returns:

```text
best_score
best_return
lowest_drawdown
top_candidates
rejected_runs
next_round.base_params
next_round.seed_params
```

Treat `next_round` as research candidates only.

## Agent Report

Return a concise report:

```text
Batch ID and run count
Dataset/cases/strategy used
Signal source, engine, runtime hash, and data identity
Best risk-adjusted parameter set
Best raw return parameter set
Lowest drawdown parameter set
Rejected/failed runs
Whether Strategy Metrics and State Lanes were present
Main risk: overtrading, drawdown, unstable state transitions, missing data
Next-round parameter suggestion
No live parameters were changed or executed
```

If Strategy Metrics or State Lanes are missing, do not guess. Say the run did
not expose them and rerun with current executor or inspect the strategy's
`metrics`/`metrics_meta` output.
