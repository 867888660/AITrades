# Local US-equity archive integration

DataTube keeps the downloaded source archives immutable and stores all derived
market data under an explicit external data root. The managed root used for the
current workstation is:

```text
E:\DataTubeHistoricalData
```

No canonical market data is copied into the repository. DataTube's metadata DB
contains only catalog, provenance, checksum, and Manifest records; every
Manifest partition uses an absolute E-drive URI. Legacy immutable Manifests may
retain their original URI; the managed history path resolver maps those paths to
verified copies under the active root without rewriting lineage.

The source archive copy is under:

```text
E:\DataTubeHistoricalData\sources\01-BaiduNetdiskDownload
```

## Inventory

```powershell
python scripts\us_equity_archive.py inventory `
  --source-root E:\DataTubeHistoricalData\sources\01-BaiduNetdiskDownload `
  --data-root E:\DataTubeHistoricalData\sources\01-BaiduNetdiskDownload\.datatube
```

The command reads ZIP central directories without extracting their contents and
writes `inventory.json`. It routes each source family independently:

- daily `*stocks.csv`: eligible for canonical `bars.v1` import;
- CRSP CIZ daily: supported by the PERMNO-aware point-in-time normalizer;
- daily `*options.csv`: held for a future `option_chain.eod` contract;
- quarterly `*_option_chain.txt`: held for the same options contract.

## Import selected daily symbols

Exchange identity is explicit because the daily snapshot files do not carry an
exchange column. Do not guess it in automated jobs.

```powershell
python scripts\us_equity_archive.py import-daily `
  --source-root E:\DataTubeHistoricalData\sources\01-BaiduNetdiskDownload `
  --data-root E:\DataTubeHistoricalData\platform `
  --symbol AAPL --venue AAPL=XNAS `
  --symbol IBM  --venue IBM=XNYS
```

Optional `--start-date` and `--end-date` values use `YYYY-MM-DD`. Each selected
symbol becomes one immutable READY `bars.v1` Manifest with:

- D+1 00:00 UTC availability (a conservative after-close boundary);
- `adjustment=NONE` because the downloaded snapshots do not declare a governed
  split/dividend adjustment policy;
- content-addressed Zstandard Parquet partitions;
- SHA-256, row-count, schema, time-range, and provenance validation;
- `point_in_time_policy=AS_OF`.

## Managed coverage

The Settings page and Agent must keep two coverage concepts separate:

- `GET /api/history/storage/coverage` describes raw copied archives;
- `GET /api/research/data/catalog` describes READY normalized Manifests.

The daily snapshot inventory contains 5,944 trading-date entries across 200 ZIP
archives from 2002-05-01 through 2025-12-31. A nine-symbol
`LOCAL/DAILY_SNAPSHOTS` Catalog group is only an explicitly imported subset, not
the full archive. Similarly, collection-level CRSP or SEC Manifests must not be
reported as one ticker.

## Share package

The privacy-safe daily-snapshot upload view is:

```text
E:\DataTubeHistoricalData\share\DataTube-US-Equity-Daily-2002-2025
```

It contains 200 hard-linked ZIPs, a filtered inventory, file list, verifier,
reader, and recipient instructions. Upload that directory as-is instead of
recompressing the full managed root. Exclude workspaces, strategy history,
metadata databases, logs, staging, partial downloads, and secrets. CRSP and
FirstRate archives remain excluded unless redistribution rights are confirmed.

## History Backtest leg

History cases can pin an imported Manifest without copying its rows into the
history SQLite DB:

```json
{
  "source": "datatube_manifest",
  "manifest_id": "manifest_...",
  "instrument_id": "equity:XNAS:AAPL",
  "symbol": "AAPL",
  "asset_class": "equity",
  "venue": "XNAS",
  "interval": "1d"
}
```

A single Manifest leg can run a legacy StrategyCode replay. Multiple equity
Manifest legs can run a published Library Alpha replay. Every execution verifies
the immutable Manifest and physical Parquet files first. These History runs are
local historical replays; they are distinct from formal Manifest-pinned Research
Backtests. The formal Research Backtest provider now accepts pinned daily equity
Manifests with the same next-bar-open, fee, slippage, and no-silent-fill contract
used by the research engine.

## CRSP CIZ point-in-time normalization

The CIZ 2.0 normalizer uses the field dictionary shipped with the local archive.
It streams the inner CSV directly from ZIP, so the roughly 60 GB expanded CSV
does not need to be materialized just to run a bounded acceptance batch:

```powershell
python scripts\us_equity_archive.py normalize-crsp-csv `
  --zip E:\DataTubeHistoricalData\staging\crsp_ciz_2025\crsp_ciz_csv.zip `
  --data-root E:\DataTubeHistoricalData\staging\crsp_ciz_2025\acceptance `
  --metadata-db E:\DataTubeHistoricalData\staging\crsp_ciz_2025\acceptance\metadata.db `
  --max-rows 10000
```

The command creates independent immutable datasets:

- `security_master.v1`: stable `security_id=crsp:permno:<PERMNO>`, PERMCO,
  dated ticker/CUSIP aliases, exchange and validity intervals;
- `bars_daily.v2`: daily OHLCV, returns, CRSP adjustment fields, and nullable
  turnover/trade count (unknown values are never replaced with zero);
- `equity_valuation_daily.v1`: market capitalization and shares outstanding;
- `corporate_actions.v1`: dated distributions, adjustment factors and confirmed
  daily delisting events.

All four pass a structural quality gate before commit. Full-archive execution
should omit `--max-rows` only in a controlled batch window with adequate memory;
the current normalizer streams ZIP input but still materializes the selected
batch while building content-addressed monthly Parquet partitions.

## SEC point-in-time fundamentals

Link a CRSP identity to its ten-digit SEC CIK, then normalize Company Facts:

```powershell
python scripts\us_equity_archive.py link-cik `
  --security-id crsp:permno:14593 --cik 0000320193

python scripts\us_equity_archive.py normalize-sec `
  --companyfacts companyfacts\CIK0000320193.json `
  --submissions submissions\CIK0000320193.json `
  --data-root E:\DataTubeHistoricalData
```

`fundamentals_pit.v1` preserves period end, filing/acceptance timestamp, form,
accession, concept, unit and value. `fundamentals_derived.v1` exposes logical
as-of fields; a TTM value is emitted only when four discrete quarters exist.
The field resolver selects only checksum-verified manifests with an explicit
point-in-time policy, and historical Universes resolve membership from Security
Master validity intervals rather than today's ticker list.

## Important data boundaries

- The snapshot OHLCV series is usable for immediate bar-based research, but its
  corporate-action adjustment semantics are not documented. It must remain
  `adjustment=NONE`.
- CRSP is the identity and daily-market-data source for survivorship-aware
  universes, stable PERMNO identity, delistings, returns, price factors, and
  historical exchange membership. Do not collapse it to current ticker identity.
- Options data must not be represented as `bars.v1`. Its contract must preserve
  underlying, contract identity, expiry, strike, call/put, quote time, bid/ask,
  sizes, open interest, volume, IV, and Greeks.
