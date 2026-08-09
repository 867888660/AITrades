# Inspection Trace Workflow

Use this workflow to inspect Agent, Skill, Tool, state, validation, and artifact
lineage for a DataTube execution. Inspection is read-only for Agents.

## Progressive Inspection

Always expand context in this order:

```text
Trace Summary -> Event Index -> Event Detail -> Dependencies -> Artifact
```

Do not load every Event Detail or raw Artifact by default.

## Common Commands

List recent traces:

```bash
python scripts/datatube_client.py inspection-traces --limit 50
```

Filter by a business object:

```bash
python scripts/datatube_client.py inspection-traces \
  --subject-type research_run --subject-id <run_id>
```

Read a summary and the event index:

```bash
python scripts/datatube_client.py inspection-trace <trace_id>
python scripts/datatube_client.py inspection-events <trace_id> --limit 100
```

Prioritize diagnostics:

```bash
python scripts/datatube_client.py inspection-events <trace_id> --severity error
python scripts/datatube_client.py inspection-events <trace_id> --severity warning
```

Open only relevant events and follow their relations:

```bash
python scripts/datatube_client.py inspection-event <event_id>
python scripts/datatube_client.py inspection-search <trace_id> "missing value IC"
```

## Interpretation Rules

- `parent` means call containment; it is not automatically a data dependency.
- `dependency` means the target consumed or required the source Event.
- `caused_by` records causal triggering; `retry_of` links attempts.
- `event_kind`, `status`, and `severity` are independent dimensions.
- Treat `completeness != complete` or `dropped_event_count > 0` as an evidence gap.
- Input, output, and metadata are redacted and size-bounded summaries.
- References identify business objects; large data stays in the Artifact/Data APIs.
- Record concise decisions, reason codes, and evidence references. Do not request
  or expose private reasoning, secrets, keys, or full hidden model context.

## Reporting

Reference Trace and Event IDs for every finding. Separate verified facts from
inferences and state when evidence is missing. Technical completion is not
business approval, strategy approval, or execution readiness.

Agents must never delete or clear Inspection, audit, Run, Artifact, or lineage
history. Human UI removal is visibility-only; retention owns physical cleanup.
