# Managed History Storage

Use this workflow for the History Data Settings page, E-drive migration,
coverage diagnosis, archive normalization, and share-package preparation.

## Contents

- [Source of Truth](#source-of-truth)
- [Coverage Semantics](#coverage-semantics)
- [Configure or Migrate](#configure-or-migrate)
- [Sharing](#sharing)
- [UI Contract](#ui-contract)
- [Closeout](#closeout)

## Source of Truth

Discover the configured root through controlled APIs; do not infer it from old
Manifest paths:

```powershell
Invoke-RestMethod http://127.0.0.1:5001/api/history/storage
Invoke-RestMethod http://127.0.0.1:5001/api/history/storage/coverage
Invoke-RestMethod http://127.0.0.1:5001/api/research/data/catalog
```

The current workstation uses `E:\DataTubeHistoricalData`. A root is active only
when `.datatube-history-root.json` is `READY`. The managed layout is:

```text
workspace/           History workspace DB
platform/            metadata, canonical data, research artifacts, and logs
strategy-history/    local History/Strategy replay databases
sources/             immutable copied source archives
imported/            copied legacy external Catalog storage
staging/             bounded normalization work
share/               optional privacy-safe upload views
```

Do not read or modify SQLite databases directly. Use controlled APIs and service
resolvers. Immutable Manifest rows may retain legacy absolute paths; the managed
path-alias resolver maps them to copied locations without rewriting lineage.

## Coverage Semantics

Always report raw inventory and normalized Catalog data separately:

- `/api/history/storage/coverage` reports copied source inventories with
  `RAW_ARCHIVE` status. It answers what files actually exist.
- `/api/research/data/catalog` reports registered READY Manifests. It answers
  what DataTube can currently pin for controlled research or replay.
- Never describe a selected-symbol Manifest subset as the full local archive.
- `LOCAL/DAILY_SNAPSHOTS` imports only explicitly requested symbols and venues.
  The snapshot CSV has no exchange field; never label every raw ticker `XNAS`.
- For local daily snapshots, a Catalog end boundary at D+1 is availability/bar
  end semantics. Display the underlying last trading date when describing raw
  file coverage.
- CRSP and SEC full-market long tables may use one collection-level
  `instrument_id`. Do not report that as one equity ticker.
- Keep raw options as option-chain archives. Never convert them silently to
  `bars.v1`.

When coverage looks implausibly small, compare the archive inventory summary,
one representative daily CSV row count, and the READY Catalog instrument list
before proposing an import.

## Configure or Migrate

The Settings UI intentionally exposes one `History Data` root. For a requested
migration:

1. `POST /api/history/storage/inspect` with the absolute target root and any
   explicit source roots.
2. Confirm target, source list, byte count, file count, and free-space result.
3. `POST /api/history/storage/normalize` only when the user asked to migrate.
4. Poll `/api/history/storage/jobs/{job_id}` until terminal.
5. Preserve source data. The workflow is copy, checksum verification, marker
   commit, and safe runtime restart; it is not a move or delete operation.
6. After restart, verify `/api/history/storage`, `/coverage`, and Catalog reads.

Do not start another normalization job while one is running. Do not treat a
configured root as active before the READY marker and runtime path agree.

## Sharing

Prefer a data-only folder over compressing the complete managed root. Existing
ZIP and Parquet files gain little from recompression, and a second giant archive
requires comparable free disk space.

The current prepared daily-snapshot package is:

```text
E:\DataTubeHistoricalData\share\DataTube-US-Equity-Daily-2002-2025
```

It is an NTFS hard-link view of 200 source ZIPs, so it consumes negligible
additional data blocks while upload clients read normal files. Upload the folder
as-is. Do not modify a ZIP through the share view because the source and share
paths reference the same file content.

A reusable share package should contain:

```text
daily-snapshots/
README.md
FILES.csv
INVENTORY.json
verify_archives.ps1
read_snapshot.py
UPLOAD_THIS_FOLDER.txt
```

Exclude by default:

```text
workspace/
strategy-history/
platform/metadata/*.db
platform/logs/
staging/
*.part
config and secret files
```

Do not include CRSP, FirstRate, or another licensed archive in a public package
unless the user has confirmed redistribution rights. After preparation, verify
archive count and sizes, run the supplied verifier, and read one representative
date/symbol through the supplied reader. Report logical upload size separately
from the negligible local hard-link overhead.

## UI Contract

The History Data page has one directory input and two coverage sections:

- **Raw archives**: actual inventory types, raw date ranges, snapshot/archive
  counts, and scan failures.
- **Normalized / backtest-ready**: READY Catalog groups, rows, datasets, and
  truthful subset or collection-level labels.

Page load and coverage inspection are read-only. They must never trigger import,
normalization, Manifest rewrites, strategy creation, or trading.

## Closeout

Report the active root, raw archive summary, READY subset summary, any package
path created, validation performed, and exclusions. State that storage/share
work has no Research Project/Session phase and creates no strategy or trade.
