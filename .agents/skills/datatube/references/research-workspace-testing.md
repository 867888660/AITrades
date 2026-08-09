# Research Workspace Automated Test

Use this workflow when the user asks an Agent to test the Research workspace or
replace repetitive manual UI checks.

## Safe Default

Run the bundled deterministic test runner:

```powershell
python "<skill-dir>/scripts/research_workspace_test.py" --mode all --repo "<datatube-repo>"
```

The script always prints JSON and exits nonzero when a required check fails.

Modes:

```text
online  GET-only runtime, capability, Research API, and HTML contract checks
suite   JavaScript syntax, Python compile, unit, integration, and fault tests
all     online + suite
```

Use `online` for a quick health check:

```powershell
python "<skill-dir>/scripts/research_workspace_test.py" --mode online
```

Use `suite` when the server is stopped or implementation changes need full
regression coverage:

```powershell
python "<skill-dir>/scripts/research_workspace_test.py" --mode suite --repo "<datatube-repo>"
```

## BTC MA Crossover Factor

To verify the built-in golden/death-cross Factor without production writes:

```powershell
python "<datatube-repo>/scripts/verify_btc_ma_crossover_factor.py" --repo "<datatube-repo>" --fast-window 5 --slow-window 20 --frequency 1h
```

The verifier selects a READY BTCUSDT Dataset through the controlled Catalog API,
requests physical Manifest verification, checks every referenced Parquet
checksum, and runs `factor-engine.v3`. It reports `+1` for a golden cross, `-1`
for a death cross, and `0` otherwise.

## Result Interpretation

Report:

- overall `PASS` or `FAIL`
- each failed check code and its output tail
- runtime health and Agent `enabled` state
- whether any `research.*` Agent capability is advertised
- counts for unit, integration, and failure-injection suites
- any skipped check and why

The HTML contract check verifies that `/research` exposes the English primary
navigation: Research, Library, Runs, Data Catalog, Agent Monitor, Approvals, and
Settings. Unit tests additionally verify the single-Research tabs (Overview,
Universe, Factor, Alpha, Data, Strategy, and Runs), immutable Library publication,
read-only Library references, version divergence after publication, backend-owned
Requirement maintenance, and live progress payloads. The default UI rejects
retired Research Project naming and must not expose manual `Complete Missing
Data`, `Prepare`, or retry buttons for normal Requirement gaps.

The Run contract check must verify all three current products:

| Product | Required visible result areas | Forbidden metric ownership |
|---|---|---|
| Factor Evaluation | Coverage, distribution, IC/Rank IC, quantile return, diagnostics | Positions, trades, equity, Sharpe, drawdown |
| Alpha Evaluation | Signals, IC/Rank IC, decay, turnover, regime analysis, diagnostics | Portfolio targets, positions, trades, costs, equity, Sharpe, drawdown |
| Research Backtest | Alpha lineage, portfolio/execution specs, Benchmark status, targets, positions, trades, costs, equity, performance, drawdown | Strategy deployment or Paper/Live claims |

Also verify that historical mixed `ALPHA_EVALUATION` results are labeled
`Legacy Hybrid Run`, retain their immutable legacy schema, and show a migration
notice. If a Benchmark series is not materialized, the UI must not show excess
return or Information Ratio.

The deterministic runner is not a pixel-level visual review. For UI changes,
add a local browser smoke pass after it succeeds: inspect the three Run entry
buttons, one strict result when available, one Legacy Hybrid result when
available, and browser console errors. Do not start a Run merely to populate a
visual fixture.

## Agent Boundary

Production Research writes are allowed only when both layers permit them:

1. `/api/agent/capabilities` advertises the exact `research.*` capability.
2. The request carries an active Research `session_id` whose operation,
   Provider, Universe, time, budget, and policy checks pass. Legacy tests may
   still create an explicit Grant fixture to verify backward compatibility.

Use [research-agent-workflow.md](research-agent-workflow.md) for authorized
write tests. Never enlarge the Session policy, increase budgets or permissions,
publish to the Global Library, delete history, or enter Paper/Live trading.

Close with:

```text
No strategy was created or submitted. No virtual or live trade was executed.
```
