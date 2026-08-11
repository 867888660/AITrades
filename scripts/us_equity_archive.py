from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from services.data_platform.store import DataPlatformStore  # noqa: E402
from services.data_platform.us_equity_archive import (  # noqa: E402
    DailySnapshotEquityImporter,
    scan_us_equity_archive,
    write_archive_inventory,
)
from services.data_platform.crsp_ciz import CrspCizNormalizer  # noqa: E402
from services.data_platform.crsp_bulk_import import run_crsp_import_job  # noqa: E402
from services.data_platform.equity_security_master import EquitySecurityMasterService  # noqa: E402
from services.data_platform.sec_pit import SecPointInTimeNormalizer  # noqa: E402
from services.data_platform.sec_bulk_import import run_sec_bulk_import_job  # noqa: E402


def _venue_map(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"venue must use SYMBOL=VENUE: {value}")
        symbol, venue = value.split("=", 1)
        symbol = symbol.strip().upper()
        venue = venue.strip().upper()
        if not symbol or not venue:
            raise ValueError(f"venue must use SYMBOL=VENUE: {value}")
        result[symbol] = venue
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventory and import local US-equity history into DataTube")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="scan ZIP central directories without extracting data")
    inventory.add_argument("--source-root", required=True)
    inventory.add_argument("--data-root", required=True)
    inventory.add_argument("--no-write", action="store_true")

    daily = subparsers.add_parser("import-daily", help="import selected daily-snapshot symbols as bars.v1")
    daily.add_argument("--source-root", required=True)
    daily.add_argument("--data-root", required=True)
    daily.add_argument("--symbol", action="append", required=True)
    daily.add_argument("--venue", action="append", default=[], help="explicit SYMBOL=XNAS/XNYS/XASE/ARCX/US")
    daily.add_argument("--start-date", default="")
    daily.add_argument("--end-date", default="")
    daily.add_argument("--metadata-db", default="", help="optional DataTube metadata DB path")

    crsp = subparsers.add_parser("normalize-crsp-csv", help="normalize a CRSP CIZ 2.0 CSV and commit PIT datasets")
    crsp_source = crsp.add_mutually_exclusive_group(required=True)
    crsp_source.add_argument("--csv")
    crsp_source.add_argument("--zip", help="stream the CSV directly from a ZIP archive")
    crsp.add_argument("--zip-entry", default="", help="required only when ZIP contains multiple CSV entries")
    crsp.add_argument("--data-root", required=True)
    crsp.add_argument("--metadata-db", default="")
    crsp.add_argument("--max-rows", type=int, default=0, help="bounded acceptance run; 0 reads the full CSV")
    crsp.add_argument("--dataset-prefix", default="crsp:ciz")

    cik = subparsers.add_parser("link-cik", help="link a stable CRSP security_id to an SEC CIK")
    cik.add_argument("--security-id", required=True)
    cik.add_argument("--cik", required=True)
    cik.add_argument("--metadata-db", default="")

    sec = subparsers.add_parser("normalize-sec", help="normalize one SEC Company Facts JSON and commit PIT facts")
    sec.add_argument("--companyfacts", required=True)
    sec.add_argument("--submissions", default="")
    sec.add_argument("--data-root", required=True)
    sec.add_argument("--metadata-db", default="")
    sec.add_argument("--dataset-prefix", default="sec:companyfacts")

    bulk = subparsers.add_parser("run-crsp-import-job", help="run or resume one persistent CRSP full-import job")
    bulk.add_argument("--job-id", required=True)
    bulk.add_argument("--metadata-db", default="")
    bulk.add_argument("--chunk-rows", type=int, default=250000)

    sec_bulk = subparsers.add_parser("run-sec-bulk-import-job", help="run or resume one persistent SEC bulk PIT import job")
    sec_bulk.add_argument("--job-id", required=True)
    sec_bulk.add_argument("--metadata-db", default="")
    sec_bulk.add_argument("--target-rows", type=int, default=250000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "inventory":
            result = scan_us_equity_archive(args.source_root)
            if not args.no_write:
                result["inventory_path"] = str(write_archive_inventory(result, args.data_root))
        elif args.command == "import-daily":
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else None
            result = DailySnapshotEquityImporter(
                args.source_root,
                args.data_root,
                store=store,
            ).import_symbols(
                args.symbol,
                venues=_venue_map(args.venue),
                start_date=args.start_date,
                end_date=args.end_date,
            )
        elif args.command == "normalize-crsp-csv":
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else DataPlatformStore()
            normalizer = CrspCizNormalizer(store, output_root=Path(args.data_root).expanduser().resolve() / "canonical")
            normalized = (
                normalizer.normalize_zip(
                    args.zip,
                    entry_name=args.zip_entry,
                    max_rows=max(0, args.max_rows),
                )
                if args.zip
                else normalizer.normalize_csv(args.csv, max_rows=max(0, args.max_rows))
            )
            result = normalizer.commit(normalized, dataset_prefix=args.dataset_prefix)
            result["source_row_count"] = normalized["source_row_count"]
        elif args.command == "run-crsp-import-job":
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else DataPlatformStore()
            result = run_crsp_import_job(store, args.job_id, chunk_rows=max(10_000, args.chunk_rows))
        elif args.command == "run-sec-bulk-import-job":
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else DataPlatformStore()
            result = run_sec_bulk_import_job(store, args.job_id, target_rows=max(25_000, args.target_rows))
        elif args.command == "link-cik":
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else DataPlatformStore()
            result = EquitySecurityMasterService(store).link_cik(args.security_id, args.cik)
        else:
            store = DataPlatformStore(Path(args.metadata_db).expanduser().resolve()) if args.metadata_db else DataPlatformStore()
            normalizer = SecPointInTimeNormalizer(store, output_root=Path(args.data_root).expanduser().resolve() / "canonical")
            normalized = normalizer.normalize_files(
                args.companyfacts,
                submissions_path=args.submissions or None,
            )
            result = {
                "fundamentals_pit": normalizer.commit(normalized, dataset_prefix=args.dataset_prefix),
                "fundamentals_derived": normalizer.commit_derived(normalized, dataset_prefix=args.dataset_prefix),
            }
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
