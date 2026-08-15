# Portfolio Evidence Experiment

Use only when the active Contract has `stop_at=PORTFOLIO_EVIDENCE` and
`run_type=RESEARCH_BACKTEST`.

The Candidate declares one or more Factors, one explicit Alpha component map,
`portfolio_spec`, `execution_spec`, and a non-empty `benchmark_spec`. State one
portfolio hypothesis while keeping the Contract's Universe, period, frequency,
primary metric, and validation lane fixed.

The Result may report targets, cost-adjusted return, Sharpe, drawdown, turnover,
fees, slippage, and benchmark evidence. If the configured benchmark was not
materialized, report the warning and do not infer excess return or information
ratio.

This product stops at portfolio evidence. It never creates a Strategy, virtual
trading state, approval, or live order. Load `research-strategy-handoff.md` only
after an explicit current-user request to cross that boundary.
