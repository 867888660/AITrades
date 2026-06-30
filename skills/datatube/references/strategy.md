# Strategy Workflow

Use this reference to create or revise strategy drafts and submit them for human
confirmation.

## Hard Boundary

The agent may create drafts, run risk checks, run simulations, and submit a
draft. The agent must stop at:

```text
WAITING_HUMAN_CONFIRM
```

Never approve, reject, request changes, place orders, change budgets, or change
permissions.

## Preflight

1. Check runtime health.
2. Read capabilities.
3. Confirm the needed capabilities are allowed:

```text
market.search
strategy.draft.create
risk.check
strategy.simulate
strategy.submit
```

4. Write activity with `workflow_id`:

```text
D_MARKET_TO_STRATEGY_DRAFTS
E_SINGLE_EVENT_STRATEGY_DRAFT
```

## Draft Inputs

A draft must include:

```text
name
strategy_code
thesis
markets
budget
execution_rules
exit_rules
params
risk_notes
agent_report
```

`agent_report` must include:

```json
{
  "strategy_reason": "",
  "market_observation": "",
  "parameter_rationale": "",
  "risk_control": "",
  "human_review_focus": ""
}
```

## Data Freshness

Before creating or submitting a draft, re-read current market data. Do not rely
only on a prior research summary or chat context.

Use:

```bash
python scripts/datatube_client.py market-search --q "<topic>" --limit 20
python scripts/datatube_client.py binance-search --q BTC --category crypto_spot --limit 10
```

## API Order

Use DataTube controlled APIs:

```text
POST /api/agent/strategy-drafts
POST /api/agent/strategy-drafts/<draft_id>/risk-check
POST /api/agent/strategy-drafts/<draft_id>/simulate
POST /api/agent/strategy-drafts/<draft_id>/submit
```

The helper can call arbitrary paths when needed:

```bash
python scripts/datatube_client.py post /api/agent/strategy-drafts --data '{...}'
```

## Stop After Submit

After submit succeeds:

1. Capture the `approval_id` or draft status if returned.
2. Summarize the draft, market direction, budget, risk result, simulation result,
   and human review focus.
3. State that the draft is waiting for human confirmation.
4. Stop all write actions.

Closeout phrase:

```text
Submitted to WAITING_HUMAN_CONFIRM. I will not approve or execute it.
```
