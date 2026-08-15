# Factor Experiment

Use only when the active Contract has `stop_at=FACTOR` and
`run_type=FACTOR_EVALUATION`.

One Experiment tests one falsifiable claim about one Factor or one named Factor
Pack. State the expected direction, intervention, controlled variables, primary
metric, and horizons before submission. Keep Universe, period, frequency, and
evaluation protocol fixed unless a new Contract version is created.

Factor evidence may include coverage, IC/Rank IC, quantile returns, stability,
decay, and diagnostic turnover. It must not include Alpha construction,
positions, trades, Sharpe, drawdown, or a Strategy conclusion.

After a COMPLETE Result, record KEEP, REJECT, or INCONCLUSIVE with a Learning.
QUICK evidence may justify another experiment but is not enough by itself to
promote the Factor into Alpha research.
