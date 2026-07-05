# Safety Rules

These rules apply to SKILL.md, helper scripts, future MCP tools, and runtime API
design.

## Never Do

```text
self-authorize
self-approve
modify budgets
modify permissions
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
```

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
