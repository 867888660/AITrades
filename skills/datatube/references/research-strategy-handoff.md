# Strategy Handoff Boundary

Research Backtest evidence is not a Strategy. Crossing from research evidence
into a Strategy draft requires an explicit current-user request. Route that
request to the Strategy workflow and stop at `WAITING_HUMAN_CONFIRM`.

Never infer Strategy authorization from a KEEP decision, strong Sharpe, a Deep
evidence profile, or a request to continue research. Never create virtual or
live trading state from this workflow.
