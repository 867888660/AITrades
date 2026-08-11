from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    CrspCizNormalizer,
    DataPlatformStore,
    EquityFieldResolver,
    EquitySecurityMasterService,
    FrozenManifestData,
    FundamentalPointInTimeView,
    HistoricalEquityUniverseService,
    RESEARCH_BACKTEST_CAPABILITIES,
    ResearchBacktestProvider,
    SecPointInTimeNormalizer,
)


class EquityPointInTimePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DataPlatformStore(self.root / "metadata.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def crsp_rows() -> list[dict[str, object]]:
        return [
            {
                "permno": 10001, "permco": 9001, "yyyymmdd": 20250102,
                "ticker": "OLD", "cusip9": "123456789", "issuernm": "Example Inc",
                "securitynm": "Example Common", "securitytype": "EQTY",
                "sharetype": "COM", "shareclass": "A", "primaryexch": "Q",
                "securitybegdt": "2024-01-01", "securityenddt": "",
                "secinfostartdt": "2024-01-01", "secinfoenddt": "2025-01-02",
                "dlyopen": 10, "dlyhigh": 12, "dlylow": 9, "dlyclose": 11,
                "dlyvol": 100, "dlynumtrd": "", "dlycap": 1100, "shrout": 100,
                "dlyret": 0.1, "dlyretx": 0.1,
            },
            {
                "permno": 10001, "permco": 9001, "yyyymmdd": 20250103,
                "ticker": "NEW", "cusip9": "123456789", "issuernm": "Example Inc",
                "securitynm": "Example Common", "securitytype": "EQTY",
                "sharetype": "COM", "shareclass": "A", "primaryexch": "Q",
                "securitybegdt": "2024-01-01", "securityenddt": "",
                "secinfostartdt": "2025-01-03", "secinfoenddt": "",
                "dlyopen": 11, "dlyhigh": 13, "dlylow": 10, "dlyclose": 12,
                "dlyvol": 120, "dlyprcvol": "", "dlycap": 1200, "shrout": 100,
                "dlyorddivamt": 0.25, "distype": "CD", "disexdt": "2025-01-03",
                "disdeclaredt": "2024-12-20",
            },
        ]

    def test_crsp_identity_quality_manifests_universe_and_resolver(self) -> None:
        normalizer = CrspCizNormalizer(self.store, output_root=self.root / "canonical")
        normalized = normalizer.normalize_rows(self.crsp_rows())
        self.assertEqual("PASS", normalized["quality"]["status"])
        self.assertIsNone(normalized["outputs"]["bars"][0]["turnover"])
        self.assertIsNone(normalized["outputs"]["bars"][0]["trade_count"])

        master = EquitySecurityMasterService(self.store)
        old = master.resolve("TICKER", "OLD", as_of="2025-01-02")
        new = master.resolve("TICKER", "NEW", as_of="2025-01-03")
        self.assertEqual("crsp:permno:10001", old[0]["security_id"])
        self.assertEqual("crsp:permno:10001", new[0]["security_id"])
        self.assertEqual([], master.resolve("TICKER", "NEW", as_of="2025-01-02"))
        self.assertEqual(1, len(master.list_active(as_of="2024-06-01")))

        committed = normalizer.commit(normalized, dataset_prefix="fixture:crsp")
        self.assertEqual(
            {"security_master", "bars", "valuation", "corporate_actions"},
            set(committed["datasets"]),
        )
        for dataset in committed["datasets"].values():
            self.assertEqual(
                "PASS",
                FrozenManifestData(self.store, dataset["manifest"]["manifest_id"]).verify()["status"],
            )
        resolution = EquityFieldResolver(self.store).resolve(["close", "market_cap", "cash_dividend"])
        self.assertEqual("READY", resolution["status"])
        self.assertEqual(3, len(resolution["manifest_ids"]))

        universe = HistoricalEquityUniverseService(self.store).create_snapshot(
            name="US Equity PIT Fixture", as_of="2025-01-03T00:00:00+00:00"
        )
        self.assertEqual(1, universe["eligible_count"])
        self.assertEqual(
            ("equity:CRSP:10001",), universe["snapshot"].actual_instrument_ids
        )
        self.assertIn("equity", RESEARCH_BACKTEST_CAPABILITIES.asset_classes)
        bars = {
            "equity:CRSP:10001": [
                {
                    "event_time": "2025-01-02T00:00:00+00:00",
                    "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100,
                },
                {
                    "event_time": "2025-01-03T00:00:00+00:00",
                    "open": 11, "high": 13, "low": 10, "close": 12, "volume": 120,
                },
            ]
        }
        backtest = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=[
                {"as_of_time": "2025-01-02T00:00:00+00:00", "weights": {"equity:CRSP:10001": 1.0}}
            ],
            dataset_manifest_ids=[committed["datasets"]["bars"]["manifest"]["manifest_id"]],
            universe_snapshot_ids=[universe["snapshot"].universe_snapshot_id],
        )
        self.assertEqual(1, backtest.metrics["rebalance_count"])
        self.assertEqual("equity:CRSP:10001", backtest.orders[0]["instrument_id"])

    def test_sec_filing_availability_prevents_lookahead(self) -> None:
        normalizer = CrspCizNormalizer(self.store, output_root=self.root / "canonical")
        normalizer.normalize_rows(self.crsp_rows())
        EquitySecurityMasterService(self.store).link_cik("crsp:permno:10001", "1234")
        companyfacts = {
            "cik": 1234,
            "entityName": "Example Inc",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenue",
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-10-01", "end": "2024-12-31",
                                    "val": 100, "accn": "0001", "fy": 2024,
                                    "fp": "Q4", "form": "10-Q", "filed": "2025-02-10",
                                }
                            ]
                        },
                    }
                }
            },
        }
        submissions = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0001"],
                    "acceptanceDateTime": ["2025-02-10T16:30:00-05:00"],
                }
            }
        }
        sec = SecPointInTimeNormalizer(self.store, output_root=self.root / "canonical")
        normalized = sec.normalize_companyfacts(companyfacts, submissions=submissions)
        row = normalized["rows"][0]
        self.assertEqual("2025-02-10T21:30:00+00:00", row["available_time"])
        self.assertNotIn(
            "revenue", FundamentalPointInTimeView.as_of(normalized["rows"], "2025-02-10T21:29:59+00:00")
        )
        self.assertEqual(
            100, FundamentalPointInTimeView.as_of(normalized["rows"], "2025-02-10T21:30:00+00:00")["revenue"]
        )
        committed = sec.commit(normalized, dataset_prefix="fixture:sec")
        self.assertEqual("PASS", FrozenManifestData(self.store, committed["manifest"]["manifest_id"]).verify()["status"])
        derived = sec.commit_derived(normalized, dataset_prefix="fixture:sec")
        self.assertEqual(
            "PASS", FrozenManifestData(self.store, derived["manifest"]["manifest_id"]).verify()["status"]
        )
        resolution = EquityFieldResolver(self.store).resolve(["revenue"])
        self.assertEqual("READY", resolution["status"])
        self.assertEqual("fundamentals_derived", resolution["resolved"]["revenue"]["data_type"])


if __name__ == "__main__":
    unittest.main()
