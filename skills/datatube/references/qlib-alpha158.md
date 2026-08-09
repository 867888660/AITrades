# Qlib Alpha158 Stock Compatibility Workflow

Use this workflow when the user asks to compute, test, cache, verify, or diagnose
Qlib Alpha158 factors for stocks in DataTube.

## Exact Product Name

The currently implemented pack is:

```text
pack_id: qlib.alpha158_without_vwap
display_name: Qlib Alpha158-compatible (VWAP excluded)
factor_count: 157
excluded_factors: [VWAP0]
is_standard_alpha158: false
frequency: 1d
required_fields: [open, high, low, close, volume]
minimum_history_bars: 60
```

Use the full display name in user-facing reports. Never call it standard
Alpha158, never calculate a typical-price proxy and label it VWAP, and never
silently fill VWAP from close, OHLC averages, turnover estimates, or another
date. A future standard pack must have a different pack identity and require
provider-reported or otherwise explicitly governed real VWAP.

## Architecture Boundary

The workflow is deliberately narrow:

```text
DataTube READY equity bars.v1 Manifests
  -> temporary isolated Qlib file-provider data
  -> Qlib 0.9.7 Alpha158 expressions with VWAP0 removed
  -> 157-column factors.parquet
  -> immutable factor cache manifest
```

Qlib is only the expression engine. It must not download Yahoo, Finnhub, or
other market data, choose a Universe, perform a Qlib Workflow/Model/Backtest, or
write DataTube business metadata directly.

The current CLI computes and caches the FactorFrame. It does **not** publish a
Global Library asset and does **not** create a formal DataTube Factor Evaluation
Run. Do not report either object unless a controlled Research API actually
returns its ID. If the user asks for a formal Factor Evaluation after computing
the pack, follow [research-agent-workflow.md](research-agent-workflow.md) and
state that precomputed Qlib pack binding is not yet connected when the live
capability does not advertise it.

## Preflight

Resolve the DataTube runtime root first. It contains all of these paths:

```text
app.py
requirements.txt
requirements-qlib.txt
integrations/qlib/alpha158_import.py
scripts/run_qlib_alpha158.py
scripts/verify_qlib_alpha158.py
```

Then run the normal read-only checks from the skill directory:

```powershell
python scripts/bootstrap.py status --json
python scripts/datatube_client.py capabilities --section research
python scripts/datatube_client.py get /api/research/data/capabilities
```

The live matrix must show equity daily preparation through OpenBB. The normal
current upstream is OpenBB/yfinance. Finnhub quote/profile discovery is not a
substitute for a READY historical `bars.v1` Manifest.

## Find and Verify Input Manifests

Get catalog entries through HTTP; do not query the metadata database:

```powershell
python scripts/datatube_client.py get "/api/research/data/catalog?instrument_id=equity%3Axnas%3AAAPL&data_type=bars&status=READY"
```

Read `latest_manifest_id` from each required stock and verify every Manifest:

```powershell
python scripts/datatube_client.py get /api/research/data/manifests/<manifest_id>
```

Require all inputs to satisfy:

- `status=READY` and `physical_validation.status=PASS`;
- `schema_version=bars.v1`;
- equity instrument identity;
- `frequency=1d`;
- at least 60 unique daily rows per instrument;
- one consistent adjustment policy across all instruments;
- immutable Manifest IDs and hashes.

If the catalog has no READY data, use a normal Research START/Requirement flow
and let backend-owned Requirement maintenance create bounded OpenBB daily tasks.
Do not ask the user to click a manual preparation button. Monitor:

```powershell
python scripts/datatube_client.py get /api/research/data/providers/openbb/worker-status
```

Follow the stall thresholds and one-restart rule in
[research-agent-workflow.md](research-agent-workflow.md). Do not call the raw
OpenBB historical endpoint as a workaround for missing authorized preparation.

## Optional Qlib Runtime

Keep Qlib out of the main DataTube `.venv`. From the runtime root, use an
isolated environment:

```powershell
python -m venv .qlib-venv
.\.qlib-venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-qlib.txt
```

On macOS/Linux:

```bash
python3 -m venv .qlib-venv
.qlib-venv/bin/python -m pip install -r requirements.txt -r requirements-qlib.txt
```

Never print environment variables or configuration files while diagnosing an
installation. `pyqlib==0.9.7` is the frozen compatibility version. A version
upgrade requires the real verification command below; a returned factor count
other than 157 is a contract change, not a warning to ignore.

## Compute

Run from the DataTube runtime root. Repeat `--manifest-id` or use a
comma-separated list:

```powershell
.\.qlib-venv\Scripts\python.exe scripts\run_qlib_alpha158.py `
  --manifest-id manifest_a,manifest_b `
  --input-bundle-id artifact_bundle_id `
  --start-time 2021-01-01 `
  --end-time 2025-12-31
```

Omit `--input-bundle-id` only when the input was selected directly from verified
Manifests and no Frozen Input Bundle exists. Do not invent a Bundle ID.

The default immutable output is:

```text
storage/factor_cache/qlib/alpha158_without_vwap/{cache_id}/
  factors.parquet
  manifest.json
```

The cache identity includes the input Manifest IDs and hashes, optional Bundle
ID, requested range, adjustment policy, Qlib version, importer version, and pack
identity. A second identical call should return `cache_hit=true`. `--force`
recomputes to check determinism; it must not overwrite an existing cache.

## Verify

After install, or after changing Qlib/importer/data conversion code, run:

```powershell
.\.qlib-venv\Scripts\python.exe scripts\verify_qlib_alpha158.py
```

The expected result is JSON containing:

```text
status: PASS
qlib_version: 0.9.7
factor_count: 157
cache_hit_verified: true
```

The verifier uses temporary synthetic equity `bars.v1` data, exercises the real
Qlib expression engine, checks that `VWAP0` is absent and rolling factors such as
`MA60` materialize, checks the immutable cache hit, and removes its temporary
data. It does not create a Research Session, Strategy, order, or trade.

## Failure Routing

| Failure | Meaning | Action |
|---|---|---|
| Manifest missing/not READY | Data preparation incomplete | Monitor Requirement maintenance and OpenBB worker. |
| Physical validation failure | Frozen input is corrupt or changed | Stop; report the Manifest and validation error. Never bypass checksum checks. |
| Fewer than 60 bars | Insufficient lookback | Prepare a wider historical range. |
| Mixed adjustment policies | Incomparable inputs | Re-resolve Manifests with one explicit adjustment policy. |
| Optional Qlib runtime missing | Local dependency not installed | Install the isolated `requirements-qlib.txt` environment. |
| Factor count is not 157 | Qlib compatibility contract changed | Stop and report the installed Qlib version. |
| Existing cache fails verification | Immutable cache damage | Stop and report the cache ID/path. Do not delete or overwrite it. |
| Recompute hash differs | Non-deterministic output | Stop and preserve both identities for diagnosis. |
| User requires standard Alpha158 | Real VWAP is required | Explain the current limitation; do not synthesize VWAP. |

Warmup nulls in long-window factors are expected near the beginning of a
series. They are not permission to backfill future values or relax point-in-time
semantics.

## Closeout

Report:

- the exact compatibility pack name and `is_standard_alpha158=false`;
- Qlib and importer versions;
- input Manifest IDs, or their count when a compact report is requested;
- adjustment policy and requested date range;
- cache ID, cache hit state, factor count, row count, and output path;
- whether the real verifier passed;
- that Library publication and formal Factor Evaluation were not created unless
  controlled APIs returned those object IDs;
- remaining VWAP limitation.

Always close this research-only workflow with:

```text
No strategy was created or submitted. No virtual or live trade was executed.
```
