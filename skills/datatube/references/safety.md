# Safety Rules

These rules apply to SKILL.md, helper scripts, future MCP tools, and runtime API
design.

## Never Do

```text
enlarge or bypass a server-created Research Session policy
modify research limits or permissions through Agent calls
publish Project definitions to the Global Library
delete Research Manifest, Artifact, Bundle, Run, audit, or lineage history
overwrite a validated Research definition version
submit raw orders
execute live trades
read or print private keys
bypass /api/agent/*
write directly to runtime databases for business workflows
treat SYSTEM_DERIVED as HUMAN_VERIFIED
continue writing after WAITING_HUMAN_CONFIRM
auto-chain research into strategy without explicit current-user authorization
store handoff state only in chat context
```

## Backend Must Enforce

Every write path should be checked by the DataTube backend, not only by prompts:

```text
agent enabled
capability allowed
deny not hit
policy not expired
market allowed
amount limits
human approval requirement
self-approval prevention
latest draft version
handoff validity
handoff consume state
idempotency key
audit write success
Research Session active and not paused
Agent write carries a Session ID whose project_id matches the target Project
START idempotency key cannot create duplicate Projects or accept a different Brief
RESUME preserves the approved Brief unless the request explicitly changes scope
research operation / Provider / Universe / time range in scope
Research Run budget reservation
current authorization on Bundle reuse
```

The backend may translate a local user's START or RESUME request into internal,
bounded execution authorization for compatibility with the formal Run pipeline.
This is not a user-facing approval step and must never be exposed as a Grant
workflow. Only the backend creates it; the Agent receives and uses `session_id`.

## Secrets

Do not include local secrets in GitHub releases or Skill assets:

```text
config.json
web_settings.json
web_settings.secrets.json
.datatube_secret.key
wallet keys
exchange API secrets
database files with private activity
```

Use example configs and the local Settings page.

## Binance Boundary

DataTube v1.0 uses Binance for market data, discovery, and crypto context. Do
not describe Binance order execution as supported unless the runtime exposes a
separate human-approved execution path in a later version.
