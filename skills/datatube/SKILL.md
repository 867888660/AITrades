---
name: datatube
description: >
  Install, start, repair, operate, and automatically test DataTube v1.0, a local
  Polymarket research/strategy workflow with Binance market data. Use for setup
  and status; Research workspace UI/API and regression tests; Factor Evaluation,
  Alpha Evaluation, formal Research Backtest, and legacy Hybrid Run inspection;
  Polymarket/Binance research, controlled US-equity data preparation and
  pre-market ranking snapshots, and Qlib Alpha158-compatible factor computation;
  EventGraph inspection and change requests; strategy drafts, risk checks,
  simulations, and human-approval handoff; historical backtests, batches,
  optimization, and history; approval review, strategy status, and Agent audits.
  Trigger examples include "test DataTube", "test the Research workspace",
  "replace manual Research clicks", "research BTC markets", "create a strategy
  draft", "run a backtest", "run Qlib Alpha158 on stocks", "test Alpha158
  without VWAP", "review approvals", and "DataTube status".
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
- A user's research request is sufficient to start a research-only Session.
  Do not ask the user to create or manage a Grant. The backend creates fixed,
  bounded research capacity and the Agent may not enlarge it.
- Ask the user only for unresolved semantic ambiguity, an ambiguous resume
  anchor, a material scope change, a limit extension, or crossing from research
  into strategy/live execution. Routine research choices are Agent decisions.
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
- **Research**: news/event research, Polymarket market discovery, Binance crypto
  context, BTC/ETH/SOL market scans, controlled US-equity pre-market market-cap
  ranking snapshots, or event-to-market analysis. Use
  [references/research.md](references/research.md).
- **Research Agent**: start a research project from a natural-language goal or
  resume from a Project, Run, Preview, Bundle, Factor, Alpha, or Session anchor;
  then iterate through Project-scoped Universe, Factor, Alpha, Requirements,
  Preview, Frozen Bundle, Factor Evaluation, Alpha Evaluation, and formal
  Research Backtest objects. Use
  [references/research-agent-workflow.md](references/research-agent-workflow.md).
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

## Standard Preflight

For research, strategy, backtest, and review:

1. Confirm runtime health with `bootstrap.py status --json`.
2. Read capabilities with `/api/agent/capabilities`.
3. Check `enabled`, `allow`, `deny`, and `limits`.
4. Create or reuse one `session_id`; pass it on every Research write.
5. Write an activity event before write workflows.
6. Use the API paths described in the relevant reference file.

## Closeout

Always tell the user:

- workflow goal and final state
- important objects read or created
- draft, approval, or handoff IDs if present
- skipped steps and why
- any item needing human confirmation

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
python scripts/datatube_client.py research-projects --limit 100
python scripts/datatube_client.py research-start --data research_brief.json
python scripts/datatube_client.py research-resume RUN run_123
python scripts/datatube_client.py research-session research_session_123
python scripts/datatube_client.py research-definition-create <project_id> --data factor.json
python scripts/backtest_optimizer.py run --spec spec.json --dry-run
python scripts/research_workspace_test.py --mode all --repo <datatube-repo>
```

Read [references/safety.md](references/safety.md) before adding or exposing any
new write path.
