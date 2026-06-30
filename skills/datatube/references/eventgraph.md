# EventGraph Workflow

Use this reference for EventGraph read/search/patch validation/change-request
workflows.

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
