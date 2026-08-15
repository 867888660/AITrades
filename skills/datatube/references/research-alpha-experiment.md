# Alpha Experiment

Use only when the active Contract has `stop_at=ALPHA` and
`run_type=ALPHA_EVALUATION`.

An Alpha Experiment tests how frozen Factor meaning becomes a predictive score:
transformation, direction, normalization, component choice, or weights. State
one major hypothesis and keep Universe, period, frequency, and primary metric
fixed. Do not reopen Factor meaning unless the Contract changes.

Alpha evidence may include IC/Rank IC, decay, score stability, component
redundancy, membership turnover, and regime diagnostics. It must not report
portfolio returns, positions, fees, Sharpe, drawdown, or Strategy performance.

An Alpha Candidate may declare one or more Factors in `factors`. Its `components`
must reference every declared Factor exactly once by semantic name, with explicit
weight, transform, and direction. Never submit internal Definition IDs, and never
replace a requested multi-Factor signal with a single-Factor result.
