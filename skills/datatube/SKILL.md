---
name: datatube
description: >
  Install, start, repair, and operate DataTube v1.0, a local research and strategy
  workflow for Polymarket prediction markets plus Binance crypto market data.
  Use when the user asks to install DataTube, start DataTube, fix DataTube,
  research Polymarket/Binance market opportunities, analyze news or event risk,
  generate a strategy draft, run risk checks or simulation, submit a draft for
  human approval, review pending approvals, inspect strategy status, or audit
  agent runs. Trigger examples include "install DataTube", "start DataTube",
  "research BTC Polymarket markets", "create a strategy draft",
  "review pending approvals", "DataTube status", "Polymarket research",
  and "Binance market research".
---

# DataTube

DataTube v1.0 is a local workflow surface for Polymarket strategy research with
Binance crypto market context. Keep the user experience simple: install or start
the runtime when needed, then work through controlled local APIs.

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
- Use Binance in v1.0 as market data and crypto context. Do not imply Binance
  live trading support.

## Runtime First

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
  context, BTC/ETH/SOL market scans, or event-to-market analysis. Use
  [references/research.md](references/research.md).
- **Strategy**: create or revise a strategy draft, run risk checks, simulate,
  submit for human confirmation. Use [references/strategy.md](references/strategy.md).
- **Review**: pending approvals, strategy health, run/step/audit review, failure
  diagnosis, or read-only status reports. Use [references/review.md](references/review.md).

For every non-setup workflow, first check:

```bash
python scripts/datatube_client.py capabilities
python scripts/datatube_client.py dashboard --limit 50
```

## Standard Preflight

For research, strategy, and review:

1. Confirm runtime health with `bootstrap.py status --json`.
2. Read capabilities with `/api/agent/capabilities`.
3. Check `enabled`, `allow`, `deny`, and `limits`.
4. Create or reuse one `run_id` and `workflow_id` for multi-step work.
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

## API Helper

Use `scripts/datatube_client.py` for repeatable local API calls. It is a thin
standard-library client around `http://127.0.0.1:5001`; it does not implement
business policy.

Examples:

```bash
python scripts/datatube_client.py health
python scripts/datatube_client.py market-search --q bitcoin --limit 10
python scripts/datatube_client.py binance-search --q BTC --category crypto_spot --limit 10
python scripts/datatube_client.py approvals --status WAITING_HUMAN_CONFIRM
```

Read [references/safety.md](references/safety.md) before adding or exposing any
new write path.
