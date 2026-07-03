# EventGraph Workflow

Use this reference for EventGraph read/search/patch validation/change-request
workflows.

EventGraph has three semantic layers:

- `derived_preview`: automatic discovery from news, markets, prices, and heat
  signals. Treat it as unverified.
- `Reasoning Layer`: scenario, impact, causal, market-move, and evidence
  hypotheses. Use it for LLM-style "what happens if..." analysis.
- `Graph Core`: approved, versioned Event / Finance / Edge / Expression objects.
  Only strict, stable knowledge should be used as a formal premise.

## Boundary

Agent workflows may:

- read graph summaries, news-derived events, observations, and core graph data
- search news and write observations/derived events through controlled APIs
- validate proposed graph patches
- submit change requests for later review

Agent workflows must not:

- call human approve/reject/request-changes endpoints
- apply change requests to the core graph
- write EventGraph databases directly
- mark derived news facts as human verified
- encode scenario/impact/causal hypotheses as strict `IMPLIES`

## Relation Classes

Use `LOGICAL` only for strict truth-space relations:

```text
EQUAL
IMPLIES
DISJOINT
OVERLAP
```

These relations may participate in automatic inference, so they have high
pollution risk if wrong.

Use reasoning classes for non-deterministic analysis:

```text
IMPACT:
  POSITIVE_IMPACT
  NEGATIVE_IMPACT
  INCREASES_PROBABILITY
  DECREASES_PROBABILITY

CAUSAL:
  CAUSES
  CONTRIBUTES_TO
  RISK_CHANNEL

SCENARIO:
  ASSUMES
  CONDITIONAL_ON
  LEADS_TO

MARKET_MOVE:
  ODDS_MOVED_WITH
  PRICE_MOVED_WITH
  VOLUME_SPIKE_WITH
  LIQUIDITY_MOVED_WITH

EVIDENCE:
  REPORTED_BY
  SUPPORTED_BY
  CONTRADICTED_BY
  OBSERVED_IN
```

Reasoning edges do not participate in strict logical inference. Include
`confidence`, `mechanism`, `time_horizon`, `assumptions`, and
`evidence_refs` or `evidence_summary` whenever possible.

Example: do not write:

```text
Hormuz closed IMPLIES Brent > 100
```

Instead write:

```json
{
  "action": "edge_create",
  "source_id": "evt_hormuz_closed_7d",
  "target_id": "fin_brent_oil",
  "relation_type": "INCREASES_PROBABILITY",
  "confidence": 0.62,
  "mechanism": "Supply disruption risk premium",
  "time_horizon": "1-4 weeks",
  "assumptions": ["closure persists"],
  "evidence_summary": "News and futures curve move support the hypothesis"
}
```

## Expression Boundary

Expressions are combination event definitions, not predictions or causal
claims. Create them only when the user, a strategy, or an existing market needs
that combination.

```text
C = AND(A, B)
```

After the expression itself is approved, system-derived edges such as
`C IMPLIES A` and `C IMPLIES B` may be generated automatically. Those edges are
definition expansions, not new LLM judgments.

## Risk Routing

Use this default routing:

- Low risk: observations, market snapshots, heat signals, evidence links.
- Medium risk: clear threshold `IMPLIES`, same-outcome-space `DISJOINT`, market
  mappings.
- High risk: `EQUAL`, `OVERLAP`, event merge/archive, expression creation,
  cross-platform equivalence, causal/scenario/impact hypotheses.

When a patch creates reasoning edges, submit it as a change request with
evidence and uncertainty. Do not present it as a verified fact.

## Commands

Health and capabilities:

```bash
python scripts/bootstrap.py status --json
python scripts/datatube_client.py capabilities
python scripts/datatube_client.py event-status
```

Read graph data:

```bash
python scripts/datatube_client.py event-graph --q BTC --limit 5
python scripts/datatube_client.py event-events --q BTC --limit 10
python scripts/datatube_client.py event-observations --q BTC --limit 10
python scripts/datatube_client.py event-core --kind events --limit 20
python scripts/datatube_client.py event-core --kind finance --limit 20
python scripts/datatube_client.py event-core --kind edges --limit 20
python scripts/datatube_client.py event-core --kind expressions --limit 20
```

Search news:

```bash
python scripts/datatube_client.py news-search --q BTC --limit-per-source 3
```

Validate a patch:

```bash
python scripts/datatube_client.py event-patch-validate --data "{\"change_type\":\"tag_add\",\"patch\":{\"items\":[{\"action\":\"tag_add\",\"event_id\":\"event_id\",\"tag\":\"candidate\",\"confidence\":0.5}]}}"
```

Validate a reasoning edge:

```bash
python scripts/datatube_client.py event-patch-validate --data "{\"patch\":{\"items\":[{\"action\":\"edge_create\",\"source_id\":\"evt_hormuz_closed_7d\",\"target_id\":\"fin_brent_oil\",\"relation_type\":\"INCREASES_PROBABILITY\",\"confidence\":0.62,\"mechanism\":\"Supply disruption risk premium\",\"time_horizon\":\"1-4 weeks\",\"assumptions\":[\"closure persists\"],\"evidence_summary\":\"News and futures curve move support the hypothesis\"}]}}"
```

Submit a change request only when the user asked for a proposed graph change:

```bash
python scripts/datatube_client.py event-change-request --data "{\"change_type\":\"tag_add\",\"title\":\"Propose tag\",\"reason\":\"Evidence summary here\",\"patch\":{\"items\":[{\"action\":\"tag_add\",\"event_id\":\"event_id\",\"tag\":\"candidate\",\"confidence\":0.5}]}}"
```

Review pending requests:

```bash
python scripts/datatube_client.py event-change-requests --status PENDING --limit 20
python scripts/datatube_client.py event-change-request-detail <request_id>
```

## Closeout

Report counts, request IDs, validation status, risk level, and whether human
review or apply is still required. For any change request, say clearly that it
has not been approved or applied by the agent.
