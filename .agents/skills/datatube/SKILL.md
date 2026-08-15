---
name: datatube
description: >
  Install, start, repair, operate, and test DataTube v1.0, a local Polymarket,
  crypto, and US-equity research/strategy workflow. Use for setup and status;
  managed history storage, archive coverage, migration, and sharing; Research
  UI/API and regression tests; Factor Evaluation, Alpha Evaluation, formal
  Research Backtest, Universe v2 authoring and PIT membership evaluation, and
  legacy Hybrid Run inspection; Polymarket/Binance
  research, controlled US-equity preparation and pre-market snapshots, and Qlib
  Alpha158-compatible factor computation; EventGraph changes; strategy drafts,
  risk checks, simulations, and human-approval handoff; historical backtests,
  optimization, history, approvals, status, and Agent audits. Trigger examples
  include "test DataTube", "migrate/share history data", "research BTC markets",
  "create a strategy draft", "run a backtest", "run Qlib Alpha158 on stocks",
  "review approvals", and "DataTube status".
---

# DataTube

DataTube v1.0 is a local workflow surface for Polymarket, crypto, and controlled
US-equity research. Keep the user experience simple: install or start the runtime
when needed, then work through controlled local APIs.

## Golden Rules

- Treat `scripts/bootstrap.py` as the setup control plane. It must work even when
  the runtime, MCP, or app server is unavailable.
- Use controlled HTTP APIs only. Do not read or modify databases directly for
  business workflows.
- Never read, print, or ask for private keys or secrets.
- Never approve, reject, request changes, change budgets, change permissions,
  or execute live trades as the agent.
- Strategy work must stop after `WAITING_HUMAN_CONFIRM`. Summarize the draft,
  risk check, and simulation result, then wait for a human.
- Research must not automatically create a strategy unless the current user
  request explicitly asks to research and then create a strategy draft.
- A user's research request is sufficient to begin Research Alignment, but it
  is not by itself permission to START or RESUME a research Session. Before
  START or RESUME, show the complete final research plan, give the user a clear
  opportunity to modify any inferred or recommended value, and require explicit
  confirmation. Do not ask the user to create or manage a Grant. The backend
  creates fixed, bounded research capacity and the Agent may not enlarge it.
- Ask the user only for unresolved semantic ambiguity, an ambiguous resume
  anchor, a material scope change, a limit extension, or crossing from research
  into strategy/live execution. Routine research choices are Agent decisions,
  but inferred and recommended research values remain proposals until they have
  been shown in the final pre-start review and explicitly confirmed by the user.
- A missing resume anchor is `RESEARCH_RESUME_ANCHOR_NOT_FOUND`, not ambiguity.
  Do not create or answer a `NEED_HUMAN` question when there are no candidates.
- In Research Agent mode, behave as a researcher: guide the user on asset type,
  period, Universe eligibility/selection, controls, and evaluation meaning.
  Never expose or operate RequirementSet, Manifest, Preview, Bundle, Provider
  task, worker, source code, or infrastructure repair surfaces.
- A missing Universe is material research ambiguity. Recommend a defensible
  Universe and explain the tradeoff before START; do not silently choose one.
- Keep the Universe v2 product surface to `STATIC`, `DYNAMIC`, and `COMPOSITE`.
  A Dynamic Universe is exactly Base, Filter, Rank, Select, and Rebalance;
  never invent field-specific Universe types or arbitrary filter expressions.
- Read `universe_capabilities` before authoring a Dynamic Universe. Distinguish
  `field_registry` (valid authoring contracts) from
  `dynamic_point_in_time_filters` and `field_execution_status` (what the
  current formal pipeline can actually evaluate).
- Formal equity PIT filters currently include `market_cap_usd`, `roe_ttm`,
  `pe_ttm`, `pb_mrq`, and `fcf_yield_ttm`. Treat ratio thresholds as decimals,
  require frozen valuation/SEC fundamentals Manifests declared by the field
  contract, and preserve `LATEST_AVAILABLE` plus `EXCLUDE` for missing inputs.
- `REQUIRES_FROZEN_DATA` is not resolved membership. Never persist, bind, or
  report a Dynamic Universe as evaluated until frozen point-in-time evidence
  produces a Membership Timeline. Preserve
  `DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION` instead of falling back to a
  static or all-eligible list.
- Universe Rebalance means membership reconstitution. Do not confuse it with a
  Portfolio rebalance or infer same-close execution from a close-time decision.
- Keep status semantics disjoint. `COMPLETE` can support a research decision;
  `INVALID` rejects the Candidate; `SYSTEM_BLOCKED` means no research result;
  Session `BLOCKED` is a recoverable container state; `NEED_HUMAN` is reserved
  for an unresolved material user choice. Never write `NEED_HUMAN(BLOCKED)`.
- Treat Library compatibility conservatively. Missing requested asset-class or
  frequency metadata is `UNKNOWN`, never `COMPATIBLE` or automatically selectable.
- Do not infer a deadlock from elapsed time, memory use, or a later status edit.
  Require the persisted phase timeline and terminal Run evidence. Treat raw
  archive coverage, READY Catalog coverage, warmup coverage, and evaluation
  coverage as four different claims.
- Researcher mode never repairs infrastructure. An explicit user request to
  diagnose or change the backend selects an engineering/review workflow; this
  does not turn a blocked Experiment into research evidence.
- A public `SYSTEM_BLOCKED` issue is not proof of a global outage. In an explicit
  engineering workflow, inspect that issue and keep maintenance scoped to the
  affected Research/Session before drawing a system-wide conclusion.
- In an explicit engineering workflow, a persisted `PREPARING_DATA` Experiment
  must map to active preparation or a short post-commit readiness check. Drain
  provider workers independently of global Requirement scans, and isolate
  Experiment advancement so one large universe cannot block unrelated work.
- Use Binance in v1.0 as market data and crypto context. Do not imply Binance
  live trading support.
- Keep formal Research Run products distinct: Factor Evaluation measures a
  feature, Alpha Evaluation measures predictive signals, and Research Backtest
  owns portfolio construction, execution, costs, equity, and drawdown. Never
  report backtest performance as an Alpha Evaluation metric.
- Label immutable historical `ALPHA_EVALUATION` Runs that contain portfolio or
  backtest artifacts as `Legacy Hybrid Run`; preserve them without treating
  their schema as the current Alpha Evaluation contract.
- Never synthesize or guess VWAP for Qlib. The current stock MVP is exactly
  `Qlib Alpha158-compatible (VWAP excluded)`, contains 157 factors, and has
  `is_standard_alpha158=false`. Do not shorten that label to standard Alpha158.

## Runtime First

Resolve bundled script paths relative to this `SKILL.md`; do not assume the
repository root contains `scripts/bootstrap.py`.

Before any business workflow:

```bash
python scripts/bootstrap.py status --json
```

If the runtime is missing, unhealthy, or not started:

```bash
python scripts/bootstrap.py ensure --json
python scripts/bootstrap.py start --json
```

If the skill was installed standalone and the runtime source is not adjacent,
derive the repository clone URL from the user's install URL when possible and
pass it explicitly:

```bash
python scripts/bootstrap.py ensure --repo-url https://github.com/867888660/AITrades.git --json
```

Read [references/setup.md](references/setup.md) for install, start, stop,
repair, and GitHub publishing details.

## Workflow Router

Choose one workflow family from the user's request:

- **Setup**: install, start, stop, status, repair, update, port conflicts, or
  broken dependencies. Use [references/setup.md](references/setup.md).
- **History Storage**: configure or migrate the managed history root, distinguish
  raw archive coverage from READY Catalog data, diagnose misleading coverage,
  or prepare a privacy-safe share package. Use
  [references/history-storage.md](references/history-storage.md).
- **Research**: news/event research, Polymarket market discovery, Binance crypto
  context, BTC/ETH/SOL market scans, controlled US-equity pre-market market-cap
  ranking snapshots, or event-to-market analysis. Use
  [references/research.md](references/research.md).
- **Research Agent**: start a research project from a natural-language goal or
  iterate through falsifiable experiments. Read
  [references/research-alignment.md](references/research-alignment.md) first.
  After the backend Alignment is READY, follow the pre-start review and user
  confirmation gate in that reference, then read
  [references/RESEARCH_PROGRAM.md](references/RESEARCH_PROGRAM.md) and
  [references/research-agent-workflow.md](references/research-agent-workflow.md),
  then load only the routed reference:
  [Universe](references/research-universe-experiment.md),
  [Factor](references/research-factor-experiment.md),
  [Alpha](references/research-alpha-experiment.md), or
  [Portfolio Evidence](references/research-portfolio-evidence.md), then
  [Iterate](references/research-iterate.md). Read
  [Strategy Handoff](references/research-strategy-handoff.md) only when the user
  explicitly asks to cross from research into a Strategy draft.
  Once this route is selected, do not load Setup, Qlib diagnostics, Inspection,
  Research Workspace Test, or downstream engineering references.
- **Qlib Alpha158**: compute the current 157-factor, no-VWAP stock compatibility
  pack from READY DataTube equity daily Manifests, verify its immutable cache,
  or diagnose its optional Python runtime. Use
  [references/qlib-alpha158.md](references/qlib-alpha158.md).
- **EventGraph**: inspect graph/news/event data, run news search, reason about
  strict logic vs scenario/impact relations, validate graph patches, create
  change requests, or review pending graph changes. Use [references/eventgraph.md](references/eventgraph.md).
- **Strategy**: create or revise a strategy draft, run risk checks, simulate,
  submit for human confirmation. Use [references/strategy.md](references/strategy.md).
- **Backtest**: select a registered Strategy backed by legacy StrategyCode or a
  published Library Alpha, create or inspect cases, run single/batch historical
  replays, compare results, optimize parameters, or analyze runtime lineage,
  Strategy Metrics, and State Lanes. Use
  [references/backtest.md](references/backtest.md). For AI parameter sweeps and
  next-round candidate parameters, also use
  [references/backtest-optimization.md](references/backtest-optimization.md).
  This route is for History/Strategy Backtests; a Manifest-pinned formal
  `RESEARCH_BACKTEST` belongs to the Research Agent route.
- **Review**: pending approvals, strategy health, run/step/audit review, failure
  diagnosis, or read-only status reports. Use [references/review.md](references/review.md).
- **Inspection**: inspect Agent-native execution Trace summaries, Event indexes,
  dependencies, warnings/errors, and Artifact references. Use
  [references/inspection.md](references/inspection.md).
- **Research Workspace Test**: automated online smoke checks, frontend contract
  checks, unit/integration/failure-injection suites, or replacing repetitive
  manual clicks. Use
  [references/research-workspace-testing.md](references/research-workspace-testing.md).

For every non-setup workflow, first check:

```bash
# For research workflows use --section research to avoid loading irrelevant
# large sections (strategy_submission_template, event_graph, etc.)
python scripts/datatube_client.py capabilities --section research
python scripts/datatube_client.py dashboard --limit 50
```

For the Research Agent route, use
`python scripts/datatube_client.py capabilities --section researcher` as the
only preflight. Do not load the general dashboard or inspect internal Project
objects before research.

## Standard Preflight

For research, strategy, backtest, and review:

1. Confirm runtime health with `bootstrap.py status --json`.
2. Read capabilities with `/api/agent/capabilities`.
3. Check `enabled`, `allow`, `deny`, and `limits`.
4. Create or reuse one canonical `session_id`; pass it on every Research write.
   Never rely on an implicit latest Grant. Before each write, assert that the
   Session `project_id` equals the target Project.
5. Write an activity event before write workflows.
6. Use the API paths described in the relevant reference file.

For the Research Agent route, the preceding researcher-only preflight and
facade replace this general sequence. Do not inspect Project or execution IR.

## Closeout

Always tell the user:

- workflow goal and final state
- important objects read or created
- the verified Project/Session pair and the highest actually completed research phase
- draft, approval, or handoff IDs if present
- skipped steps and why
- any item needing human confirmation

For Universe work, also report the definition schema, compile state, frozen
evidence state, and highest truthful membership state: authored, compiled,
frozen-evaluated, or bound. Never call an authored/compiled Dynamic Universe
complete membership evidence.

For research-only work, say that no strategy was created or submitted.
For strategy work, say that the draft was submitted to `WAITING_HUMAN_CONFIRM`
and that the agent will not approve or execute it.
For backtest work, say that the run is a local historical replay and does not
approve or execute live trades.

## API Helper

Use `scripts/datatube_client.py` for repeatable local API calls. It is a thin
standard-library client around `http://127.0.0.1:5001`; it does not implement
business policy.

Examples:

```bash
python scripts/datatube_client.py health
python scripts/datatube_client.py market-search --q bitcoin --limit 10
python scripts/datatube_client.py binance-search --q BTC --category crypto_spot --limit 10
python scripts/datatube_client.py event-graph --q BTC --limit 5
python scripts/datatube_client.py event-change-requests --status PENDING
python scripts/datatube_client.py approvals --status WAITING_HUMAN_CONFIRM
python scripts/datatube_client.py strategies --limit 100
python scripts/datatube_client.py strategy 82
python scripts/datatube_client.py inspection-traces --limit 50
python scripts/datatube_client.py inspection-events <trace_id> --severity warning
python scripts/datatube_client.py inspection-event <event_id>
python scripts/datatube_client.py backtest-cases --limit 100
python scripts/datatube_client.py backtest-runs --case-id 47 --limit 20
python scripts/backtest_optimizer.py run --spec spec.json --dry-run
python scripts/research_workspace_test.py --mode all --repo <datatube-repo>
```

Research Agent commands live only in the routed Research Agent references. Do
not mix them with legacy Project, Definition, Requirement, or Run commands.

Read [references/safety.md](references/safety.md) before adding or exposing any
new write path.
