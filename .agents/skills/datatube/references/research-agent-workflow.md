# Research Agent Workflow

Use one persistent Research Session for both building a study from scratch and
modifying an existing study through repeated, explainable experiments.

## Contents

- User contract, START, and formal Run products
- RESUME and controlled iteration
- Invalidation and automatic data maintenance
- Human-input boundaries and monitoring
- Downstream calls, hard boundaries, and closeout

## User Contract

The interface is deliberately small:

```text
START(goal or partial Research Brief)
RESUME(anchor_type, anchor_id, optional new goal)
STATUS(session_id)
PAUSE(session_id)
CONTINUE(session_id)
ANSWER(session_id, answer)          # only when NEED_HUMAN
```

The user's research request authorizes research-only work. Do not ask them to
create a Grant or configure budgets. The backend creates a fixed Session policy
with Run, runtime, and download limits. The Agent cannot enlarge that policy.

## START

Convert the user's language into a Research Brief before building definitions:

```yaml
objective: evaluate a BTC trend signal and one cost-aware portfolio rule
instrument_scope: BTCUSDT spot         # single instrument
# instrument_scope: "BTCUSDT, ETHUSDT" # comma-separated for multiple
# instrument_scope:                     # JSON array also accepted
#   - BTCUSDT spot
#   - ETHUSDT spot
provider: BINANCE
frequency: 1h
research_period:
  start: 2021-01-01
  end: current_date
evaluation_plan:
  factor:
    - coverage
    - rank_ic
    - quantile_return
  alpha:
    - rank_ic
    - decay
    - membership_turnover
    - regime_stability
  research_backtest:
    - annualized_return
    - max_drawdown
    - sharpe_ratio
    - trade_turnover
    - cost_adjusted_return
constraints:
  long_only: true
  leverage: false
  max_turnover: null
benchmark: buy_and_hold
```

**`instrument_scope` rules:**
- Single instrument: `"BTCUSDT spot"` (trailing asset-class qualifier is stripped automatically).
- Multiple instruments: comma-separated string `"AAPL, MSFT, NVDA"` **or** a JSON array `["AAPL", "MSFT"]`. Do not space-separate multiple tickers in a plain string — only the first token is used.
- Equity requires `frequency: 1d`; any other value is rejected at START with `RESEARCH_FREQUENCY_MISMATCH`.

Run:

```powershell
python scripts/datatube_client.py research-start --data brief.json
```

Put a stable `idempotency_key` in `brief.json` for retryable automation. Repeating
the same START request must return the existing Project and Session; it must not
create a second Project. Use a new key only when the user intentionally requests
a separate study.

Check `resolved_grant_scope` in the START response. It contains `allowed_instrument_ids`,
`allowed_intervals`, and `allowed_providers` as they were actually granted — verify they
match your intent before proceeding. A mismatch here is cheap to fix; discovering it at
Preview or Run creation costs a full session.

## Canonical Object Ledger

After every START or RESUME, record and verify this ledger before any write:

```text
project_id
session_id
entry_mode
brief.provider / brief.frequency / brief.instrument_scope
resolved_grant_scope.allowed_providers / allowed_intervals / allowed_instrument_ids
context.project_id
current_plan_version
```

Hard invariants:

- `session.project_id == context.project_id == target project_id`.
- Brief Provider, frequency, and instruments must equal the resolved Grant scope.
- RESUME without explicit scope changes must preserve the approved Project Brief.
- Every Project write must carry the canonical `session_id`; never rely on an
  implicit latest Grant or expose an internal Grant ID.
- If any invariant fails, stop before writing and enter
  `NEED_HUMAN(AMBIGUOUS_CONTEXT)` with the conflicting IDs and fields.

Safe defaults are acceptable when the missing choice is routine and reversible.
Ask only when different interpretations would create materially different
research.

Execution sequence:

```text
Research Brief → Project Draft → Universe → Factor / Alpha
→ Requirements → Data Preparation → Preview → Frozen Bundle
→ Run → Evaluation → Iteration
```

## Formal Research Run Products

Choose the Run type from the question being answered. Always send an explicit
`run_type`; never infer a portfolio backtest from an Alpha Evaluation request.

| Run type | Question | Required run specs | Result contract |
|---|---|---|---|
| `FACTOR_EVALUATION` | Is one feature valid and predictive? | `evaluation_spec` | Coverage, distribution, IC/Rank IC, quantile returns, diagnostics. No portfolio or return curve. |
| `ALPHA_EVALUATION` | Is the combined signal predictive and stable? | `evaluation_spec` | Signal observations, IC/Rank IC, decay, membership turnover, regime analysis, diagnostics. No positions, trades, equity, Sharpe, or drawdown. |
| `RESEARCH_BACKTEST` | What happens after converting one Alpha into an executable research portfolio? | `portfolio_spec`, `execution_spec`; `benchmark_spec` is strongly recommended | Portfolio targets, positions, trades, fees/slippage, equity, performance, drawdown, diagnostics. |

Research Backtest v1 requires exactly one Alpha definition. Freeze Factor and
Alpha lineage, Universe Snapshot, Manifest IDs, execution assumptions,
portfolio rules, and Benchmark identity in the Bundle.

Do not call a formal `RESEARCH_BACKTEST` a Strategy Backtest. It is a
Manifest-pinned Research product and does not require a registered Strategy.
History/Strategy Backtests use [backtest.md](backtest.md), a History Case, and a
registered Strategy signal source.

### Benchmark Semantics

Freeze `benchmark_spec` with the Research Backtest. If the benchmark comparison
series is not materialized, report only absolute strategy metrics. Do not infer
or display excess return, tracking error, or Information Ratio from the
strategy equity curve alone. A missing benchmark is a warning, not permission
to fabricate relative metrics.

### Historical Alpha Hybrid Results

Older immutable `ALPHA_EVALUATION` Runs may contain `PORTFOLIO_TARGETS`,
positions, trades, equity, `BACKTEST_RESULT`, or drawdown artifacts. Label them
`Legacy Hybrid Run`, retain schema `alpha-run-result.v1`, and explain that new
Alpha Evaluations stop at predictive-signal evaluation. Never rewrite history
or present a Legacy Hybrid result as the current Alpha Evaluation contract
(`alpha-evaluation-result.v2`).

## RESUME

Supported anchor types are:

```text
SESSION | PROJECT | RUN | PREVIEW | BUNDLE | FACTOR_DEFINITION | ALPHA_DEFINITION
```

Run:

```powershell
python scripts/datatube_client.py research-resume RUN run_123 --data resume_goal.json
```

Never interpret RESUME as “read one ID and modify it.” The Context Resolver must
restore the Project, Universe, definitions, Requirements, Preview, Bundle, Runs,
artifacts, and experiment history. If one definition belongs to several
Projects, enter `NEED_HUMAN(AMBIGUOUS_CONTEXT)` and present the candidates.

Verify the returned `resolved_grant_scope` after RESUME exactly as after START.
Do not accept default `BINANCE`, `1h`, or an empty instrument scope when the
approved Project Brief is an equity or Polymarket study.

Always keep these two fields distinct:

```text
original_baseline_run_id   # the Run selected at entry
current_branch_head_run_id # latest candidate accepted with KEEP
```

## Iteration Unit

One iteration tests one major hypothesis, not necessarily one field:

```yaml
hypothesis:
  statement: lowering concentration should reduce tail risk
intervention_set:
  - top_k: 2 -> 5
  - max_position_weight: 0.5 -> 0.2
controlled_variables:
  - universe
  - factor
  - rebalance_frequency
  - transaction_cost
```

Multiple linked fields are valid when they implement one hypothesis. Do not mix
unrelated factor, frequency, cost, and Universe changes in one iteration.

Create and complete iterations with:

```powershell
python scripts/datatube_client.py research-iteration-create <session_id> --data iteration.json
python scripts/datatube_client.py research-iteration-complete <iteration_id> --data result.json
```

Each result uses `KEEP`, `REJECT`, `INCONCLUSIVE`, or `NEED_HUMAN`. `KEEP` moves
the current branch head; `REJECT` does not. Compare every candidate to both the
current control Run and, when available, the original baseline.

## Invalidation Routing

Calculate the earliest invalid node and rerun only the necessary downstream
work:

```text
Universe Definition → Universe Snapshot → Factor Definitions
→ Alpha Definition → Requirements → Prepared Data → Preview
→ Frozen Bundle → Run → Metrics / Artifacts
```

Examples:

| Change | Earliest restart |
|---|---|
| explanation or display only | none |
| transaction cost, portfolio rule, or rebalance frequency | Research Backtest Preview |
| research period or Provider | Requirements / data readiness |
| Alpha weight | Alpha definition, then Alpha Evaluation or Research Backtest |
| Factor formula | Factor validation, then affected Factor/Alpha Runs |
| Universe rule | Universe Snapshot |

Requirements must be recompiled when dependencies change, but already prepared
data may be reused when it still covers the new Requirement Set.

## Automatic Requirement Data Maintenance

Do not ask the user to click a preparation button or manually start a normal
Requirement download. The backend scans all Requirement Library assets and
active Research RequirementSets, then creates bounded idempotent provider tasks
for supported gaps. Research and Library pages are read-only monitors for this
maintenance path.

Treat `QUEUED`, `PREPARING`, and `CHECKING` as normal progress. Ask for human
input only when a terminal `FAILED` or `UNAVAILABLE` result exposes a genuine
contract choice or a material scope change; a missing percentage or an active
download is not `NEED_HUMAN`.

A direct Provider probe or shell exception is not Project data state. Report a
Project as data-blocked only when it has a non-empty RequirementSet and its
DataTube status rows or controlled task records contain concrete `FAILED` or
`UNAVAILABLE` evidence. `queue_depth == 0` with zero Project tasks means data
preparation has not started, not that downloads are rate-limited.

## NEED_HUMAN

Do not ask for routine choices, confirmation of an Agent proposal, or permission
to run another in-scope backtest. Pause only for one of these stable reasons:

```text
AMBIGUOUS_INTENT
AMBIGUOUS_CONTEXT
MATERIAL_SCOPE_CHANGE
LIMIT_EXTENSION_REQUIRED
CROSS_RESEARCH_BOUNDARY
```

The question must explain the blocked decision in plain language and be
answerable in one short response. Persist it in the Session; do not store it
only in chat.

## Monitoring

Update the Session state before each material phase:

```text
BRIEFING | PLANNING | BUILDING | PREPARING_DATA | PREVIEWING
| RUNNING | EVALUATING | ITERATING | NEED_HUMAN | PAUSED
| BLOCKED | COMPLETED | FAILED | CANCELLED
```

AgentMonitor must make visible:

- what the Agent is doing now and why
- the active hypothesis and intervention set
- original baseline and current branch head
- Run/runtime usage and limits
- completed iteration decisions and warnings
- the exact question when human input is genuinely required

## Data Preparation Stall Detection

Treat `QUEUED`, `PREPARING`, and `CHECKING` as normal progress — do not enter
`NEED_HUMAN` for an active download or a missing percentage.

However, a worker can become permanently IDLE if its task queue is saturated
with historical non-READY tasks. Use the worker-status endpoint to distinguish
normal progress from a genuine stall:

```powershell
# Check OpenBB (equity) worker
python scripts/datatube_client.py get /api/research/data/providers/openbb/worker-status
# Check Binance backfill worker
python scripts/datatube_client.py get /api/research/data/backfill/binance/worker-status
```

Response fields that matter:

| Field | Meaning |
|---|---|
| `queue_depth` | Tasks currently in READY state waiting for this worker |
| `oldest_ready_age_seconds` | Age of the oldest READY task; helps identify stuck tasks |
| `counts.READY` | Should be 0 when the worker has recently run |
| `counts.RUNNING` | Should be 1 while the worker is active |

**Stall thresholds:**
- `oldest_ready_age_seconds > 600` (10 min) with `counts.RUNNING == 0` → worker is stalled.
- `queue_depth == 0` and no RUNNING task → worker has no work; preparation may already be complete or tasks not yet created.
- `queue_depth > 0` but no change for 10 min → call the worker-start endpoint once:

```powershell
python scripts/datatube_client.py post /api/research/data/providers/openbb/worker/start
python scripts/datatube_client.py post /api/research/data/backfill/binance/worker/start
```

If after one restart the queue does not drain within 15 min, enter
`NEED_HUMAN(BLOCKED)` with the `queue_depth`, `oldest_ready_age_seconds`, and
`counts` values visible in the session state. Do not invent other workarounds.

## Asset Class Capability Matrix

Check the live matrix before starting a new study; it is generated from backend
code and reflects actual provider availability:

```powershell
python scripts/datatube_client.py get /api/agent/capabilities?section=research
```

Key constraints from the current matrix:

| Provider | Asset class | Supported frequencies | FACTOR_EVALUATION | ALPHA_EVALUATION | RESEARCH_BACKTEST |
|---|---|---|---|---|---|
| BINANCE | crypto_spot | 1m … 1d | ✓ | ✓ | ✓ |
| POLYMARKET | polymarket_binary | 1m … 1d | ✓ | ✓ | ✗ |
| OPENBB | equity | **1d only** | ✓ | ✓ | ✗ |

`RESEARCH_BACKTEST` supports `crypto_spot` only in v1. Attempting it with equity
or Polymarket instruments raises an error at Preview creation time.

## Downstream Research Calls

> **Do not call `research-project-create` directly.** That CLI command calls
> `POST /api/agent/research/projects`, which creates a bare project and returns
> `next_required_action: HUMAN_CREATE_PROJECT_RESEARCH_GRANT`. A human must
> then create a Grant before any write can proceed — the agent cannot do this.
> Use `research-start` instead: it atomically creates the project, plan, and
> a fully-authorized `FULL_RESEARCH` Grant in one call.

Pass `session_id` on every Project write. The server resolves its internal
research capacity; do not pass or expose a Grant ID.

```powershell
python scripts/datatube_client.py research-universe-create <project_id> --data universe.json
python scripts/datatube_client.py research-definition-create <project_id> --data factor.json
python scripts/datatube_client.py research-requirements-compile <project_id> --data requirements.json
python scripts/datatube_client.py research-preview-create <project_id> --data preview.json
python scripts/datatube_client.py research-run-create <project_id> --data run.json
python scripts/datatube_client.py research-run-execute <project_id> --data session.json
```

### Removing a Component from a Project

These commands unbind/unpin a Universe, Factor, Alpha, or Requirement from the
current Project. They mirror the human UI's "Remove from Research" action —
the underlying definition or Library asset is never deleted, and history
(previous Runs, Previews, artifacts) is preserved unchanged.

```powershell
python scripts/datatube_client.py research-unpin <project_id> <slot_key> --data '{"expected_definition_id": "<factor_or_alpha_definition_id>"}'
python scripts/datatube_client.py research-universe-unbind <project_id> <universe_id>
python scripts/datatube_client.py research-universe-ref-remove <project_id>
python scripts/datatube_client.py research-requirement-remove <project_id> <ref_id>
```

- `research-unpin` removes a Factor or Alpha `project_definition_ref` by
  `slot_key` (e.g. `factor:MyFactor`, `alpha:MyAlpha`). `expected_definition_id`
  is required and must match the currently pinned definition, or the call
  fails with `RESEARCH_REFERENCE_STALE`. Removing a Factor that a pinned Alpha
  still depends on fails with `FACTOR_REFERENCE_IN_USE`; unpin or replace the
  Alpha first.
- `research-universe-unbind` removes a shared-Universe binding (multi-binding
  Research). If the removed binding was `PRIMARY`, another bound Universe is
  promoted automatically; if none remain, the Project has no Universe.
- `research-universe-ref-remove` clears the legacy single-Universe reference
  used by Projects that predate shared-Universe bindings. It has no arguments
  beyond `project_id`.
- `research-requirement-remove` removes one Requirement item from the Project.
  It only works on Requirements sourced from the Library; a Project-derived
  Requirement without a `library_asset_id` must be changed at its source
  instead — the call raises an error if attempted.

### Archiving a Library Asset

```powershell
python scripts/datatube_client.py research-library-archive <project_id> <library_asset_id>
```

Soft-archives a published Universe, Factor, or Alpha asset in the Global
Library (Requirements use `research-requirement-remove`/the library
Requirements endpoints instead). This is the reverse of publishing, not a
hard delete: history, lineage, and every immutable Run/Bundle that already
used the asset are preserved unchanged. `project_id` only names the Grant
that authorizes the call — the asset itself is not owned by that Project.
Archiving fails if the asset is still referenced by any active Research
(`research_count > 0`); unbind or unpin it from every Project first with
`research-unpin` / `research-universe-unbind` / `research-universe-ref-remove`.

Before creating a Preview, verify that its payload matches the selected Run
product. Reject an Alpha Evaluation payload that depends on portfolio or
execution specs for its meaning; use `RESEARCH_BACKTEST` instead. Never report
Total Return, CAGR, Sharpe, fees, trades, equity, or drawdown from an
`ALPHA_EVALUATION` result.

## Hard Boundaries

- Never enlarge the server-created Session policy.
- Never publish a Project definition to the Global Library.
- Never delete Manifest, Artifact, Bundle, Run, audit, or lineage history.
- Never overwrite a validated definition; create a new immutable version.
- Never bypass point-in-time, availability-time, quality, Provider, or range checks.
- Never enable Paper, Live, or real trading.
- Do not auto-chain research into strategy creation without an explicit current
  user request.
- For US pre-market market-cap research, do not freeze a formal Factor Bundle
  when shares outstanding or market cap is only a current snapshot. A current
  shares proxy may be reported as exploratory evidence, never as PIT-valid
  Factor Evaluation input.

## Closeout

### Completion Evidence Gates

Use the narrowest truthful completion label:

| Label | Required evidence |
|---|---|
| Definition layer complete | Validated and pinned Factor/Alpha IDs and counts. |
| Research skeleton complete | Bound Universe ID, immutable Universe Snapshot ID, and non-empty RequirementSet ID in addition to definitions. |
| Data preparation blocked | RequirementSet ID plus concrete DataTube row/task IDs in `FAILED` or `UNAVAILABLE`. |
| Evaluation ready | A ready Preview ID with the intended Run type. |
| Evaluation complete | Run ID, terminal status, schema, metrics, warnings, and Artifact IDs. |

Never say “project setup complete” when only definitions exist. Never claim that
a script or artifact was saved until its path has been verified in the current
workspace. Separate intended next steps from persisted DataTube state.

Report the Session, Project, original baseline, current branch head, accepted and
rejected iterations, Run type and schema, final metrics, warnings, Benchmark
materialization status, and remaining research capacity. Group metrics by their
owning product; never mix signal-evaluation statistics with portfolio-backtest
statistics under one Alpha result.

Always close research-only work with:

```text
No strategy was created or submitted. No virtual or live trade was executed.
```
