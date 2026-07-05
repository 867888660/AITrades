# Research Workflow

Use this reference for news/event research, Polymarket market discovery, and
Binance crypto context.

## Scope

DataTube v1.0 uses:

- Polymarket as the prediction-market and strategy target surface.
- Binance as crypto market data and price/context surface.

Do not imply Binance live trading support in v1.0.

## Preflight

1. Run `python scripts/bootstrap.py status --json`.
2. Read capabilities:

```bash
python scripts/datatube_client.py capabilities
```

3. Write activity for longer workflows:

```bash
python scripts/datatube_client.py activity --workflow-id B_TOPIC_RESEARCH --message "Researching <topic>"
```

## Topic Research

For a user asking about a topic such as BTC, ETH, Fed, oil, election, or a
Polymarket event:

1. Search news when relevant:

```bash
python scripts/datatube_client.py news-search --q "<topic>"
```

2. Search Polymarket:

```bash
python scripts/datatube_client.py market-search --q "<topic>" --sort volume24h --limit 20
```

3. Search Binance for crypto context:

```bash
python scripts/datatube_client.py binance-search --q BTC --category crypto_spot --limit 10
```

4. Summarize:

- what the event or theme is
- relevant Polymarket markets
- relevant Binance instruments
- liquidity, volume, spread, and price context where available
- evidence and contrary evidence
- uncertainty and missing data

## Research Stop Boundary

If the user only asked for research, stop after the report. Do not create or
offer to create a strategy draft.

Say:

```text
This research pass did not create or submit any strategy.
```

Only move to strategy creation when the current user request explicitly asks for
it, for example:

```text
Research this and then create a strategy draft.
```

## Output Shape

Prefer concise, decision-useful research:

```text
Topic:
Polymarket candidates:
Binance context:
Key evidence:
Contrary evidence:
Uncertainties:
No strategy was created or submitted.
```
