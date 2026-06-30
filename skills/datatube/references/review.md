# Review Workflow

Use this reference for read-only status, approvals, strategy health, run/step
trace, audit review, and failure diagnosis.

## Read-Only Rule

The v1.0 review workflow is read-only. Do not call:

```text
approval.approve
approval.reject
approval.request_changes
strategy.pause
strategy.resume
strategy.cancel
execution.apply
audit.clear
admin.policy.set
```

## Common Commands

Health and capabilities:

```bash
python scripts/datatube_client.py health
python scripts/datatube_client.py capabilities
```

Pending approvals:

```bash
python scripts/datatube_client.py approvals --status WAITING_HUMAN_CONFIRM --limit 50
```

Drafts:

```bash
python scripts/datatube_client.py drafts --limit 50
```

Audit and runs, using direct GET:

```bash
python scripts/datatube_client.py get /api/agent/audit?limit=100
python scripts/datatube_client.py get /api/agent/runs?limit=100
```

Strategy state:

```bash
python scripts/datatube_client.py get /api/agent/strategies?limit=100
python scripts/datatube_client.py get /api/agent/strategies/<strategy_id>/events?limit=50
python scripts/datatube_client.py get /api/agent/strategies/<strategy_id>/state
```

## Interpretation

Technical success does not mean business approval. Distinguish:

- workflow ran successfully
- draft created
- risk check passed
- simulation completed
- approval is waiting
- human approved or rejected

## Closeout

List pending items that need human action. Include IDs and links when available.
Avoid certainty about profit, outcome, or execution readiness.
