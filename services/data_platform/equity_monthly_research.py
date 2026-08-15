from __future__ import annotations

import math
import hashlib
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from services.history_storage_service import resolve_managed_history_path

from .backtest_contract import BacktestExecutionSpec
from .canonical_dataset import CanonicalDatasetCommitter
from .catalog_service import DatasetCatalogService
from .equity_security_master import EquitySecurityMasterService
from .portfolio import PortfolioSpec
from .provenance_service import ManifestProvenanceService
from .research_backtest import (
    RESEARCH_BACKTEST_CAPABILITIES,
    RESEARCH_BACKTEST_CODE_HASH,
    RESEARCH_BACKTEST_ENGINE_VERSION,
    ResearchBacktestProvider,
    ResearchBacktestResult,
    _canonical_hash,
)
from .store import BASE_DIR, DataPlatformStore
from .universe_service import UniverseService


PANEL_SCHEMA_VERSION = "equity_research_monthly.v5"
BENCHMARK_SCHEMA_VERSION = "equity_research_benchmark_daily.v1"
PANEL_SOURCE_VERSION = "grandma-us-bridge.v3"
EQUITY_MONTHLY_RESEARCH_ENGINE_VERSION = "equity-monthly-research.v3"
EQUITY_MONTHLY_RESEARCH_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CORE_SOURCE_MANIFESTS = {
    "bars": "manifest_e09c749f2a624566b75feb11055c13a4",
    "valuation": "manifest_e395aa5149134addbf4d4ec256e78518",
    "corporate_actions": "manifest_6b1d8dec58874d8d83d71fba58b80e7e",
    "fundamentals": "manifest_d751093fbe444f5ea2400b3dea905753",
}
FLOW_CONCEPTS = {
    "net_income_ttm": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow_ttm": ("NetCashProvidedByUsedInOperatingActivities",),
}
EQUITY_CONCEPTS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _duration_days(row: Mapping[str, Any]) -> int | None:
    start = _as_date(row.get("period_start"))
    end = _as_date(row.get("period_end"))
    return (end - start).days if start and end and end >= start else None


def _preferred_rows(
    rows: Iterable[Mapping[str, Any]], concepts: Sequence[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    priority = {name: index for index, name in enumerate(concepts)}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        unit = _clean(row.get("unit")).upper()
        if unit and unit != "USD":
            continue
        concept = _clean(row.get("concept"))
        if concept not in priority or _finite(row.get("value")) is None:
            continue
        key = (_clean(row.get("period_start")), _clean(row.get("period_end")))
        if not key[1]:
            continue
        previous = result.get(key)
        if previous is None or priority[concept] < priority[_clean(previous.get("concept"))]:
            result[key] = row
    return result


def _flow_ttm(rows: Iterable[Mapping[str, Any]], concepts: Sequence[str]) -> float | None:
    facts = list(_preferred_rows(rows, concepts).values())
    annual = [
        row for row in facts
        if _clean(row.get("form")).upper() in {"10-K", "10-K/A"}
        and (_duration_days(row) or 0) in range(300, 401)
    ]
    if not annual:
        return None
    annual.sort(key=lambda row: _clean(row.get("period_end")))
    base = annual[-1]
    annual_end = _as_date(base.get("period_end"))
    annual_value = _finite(base.get("value"))
    if annual_end is None or annual_value is None:
        return None
    interim = [
        row for row in facts
        if _clean(row.get("form")).upper() in {"10-Q", "10-Q/A"}
        and (_as_date(row.get("period_end")) or date.min) > annual_end
        and 60 <= (_duration_days(row) or 0) <= 300
    ]
    if not interim:
        return annual_value
    interim.sort(key=lambda row: (_clean(row.get("period_end")), _duration_days(row) or 0))
    current = interim[-1]
    current_end = _as_date(current.get("period_end"))
    current_duration = _duration_days(current)
    current_value = _finite(current.get("value"))
    if current_end is None or current_duration is None or current_value is None:
        return annual_value
    comparable = []
    for row in facts:
        end = _as_date(row.get("period_end"))
        duration = _duration_days(row)
        if end is None or duration is None:
            continue
        age = (current_end - end).days
        if 330 <= age <= 400 and abs(duration - current_duration) <= 20:
            comparable.append(row)
    if not comparable:
        return None
    comparable.sort(key=lambda row: _clean(row.get("period_end")))
    prior_value = _finite(comparable[-1].get("value"))
    return annual_value + current_value - prior_value if prior_value is not None else None


def project_fundamental_metric_events(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create combined PIT metric events using annual-plus-YTD TTM arithmetic.

    Facts are applied only when their SEC filing/acceptance `available_time` is
    reached.  A 10-Q TTM value is prior annual + current YTD - comparable
    prior-year YTD; a 10-K supplies the new annual TTM directly.
    """
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            _clean(row.get("instrument_id")),
            _clean(row.get("available_time")),
            _clean(row.get("period_end")),
            _clean(row.get("period_start")),
            _clean(row.get("concept")),
            _clean(row.get("accession_number")),
            _clean(row.get("taxonomy")),
            _clean(row.get("unit")),
            _clean(row.get("value")),
        ),
    )
    output: list[dict[str, Any]] = []
    cursor = 0
    state: dict[str, dict[str, dict[tuple[str, str], dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    previous: dict[str, tuple[float | None, float | None, float | None]] = {}
    while cursor < len(ordered):
        instrument_id = _clean(ordered[cursor].get("instrument_id"))
        available_time = _clean(ordered[cursor].get("available_time"))
        batch: list[dict[str, Any]] = []
        while cursor < len(ordered) and (
            _clean(ordered[cursor].get("instrument_id")) == instrument_id
            and _clean(ordered[cursor].get("available_time")) == available_time
        ):
            batch.append(ordered[cursor])
            cursor += 1
        for row in batch:
            concept = _clean(row.get("concept"))
            key = (_clean(row.get("period_start")), _clean(row.get("period_end")))
            if concept in FLOW_CONCEPTS["net_income_ttm"]:
                state[instrument_id]["net_income_ttm"][key] = row
            if concept in FLOW_CONCEPTS["operating_cash_flow_ttm"]:
                state[instrument_id]["operating_cash_flow_ttm"][key] = row
            if concept in EQUITY_CONCEPTS:
                state[instrument_id]["shareholders_equity"][key] = row
        net_income = _flow_ttm(
            state[instrument_id]["net_income_ttm"].values(),
            FLOW_CONCEPTS["net_income_ttm"],
        )
        cash_flow = _flow_ttm(
            state[instrument_id]["operating_cash_flow_ttm"].values(),
            FLOW_CONCEPTS["operating_cash_flow_ttm"],
        )
        equity_facts = list(
            _preferred_rows(
                state[instrument_id]["shareholders_equity"].values(), EQUITY_CONCEPTS
            ).values()
        )
        equity_facts.sort(key=lambda row: _clean(row.get("period_end")))
        equity = _finite(equity_facts[-1].get("value")) if equity_facts else None
        current = (net_income, cash_flow, equity)
        if current == previous.get(instrument_id) or all(value is None for value in current):
            continue
        previous[instrument_id] = current
        output.append({
            "instrument_id": instrument_id,
            "event_time": available_time,
            "available_time": available_time,
            "net_income_ttm": net_income,
            "operating_cash_flow_ttm": cash_flow,
            "shareholders_equity": equity,
        })
    return output


class EquityMonthlyResearchMaterializer:
    """Materialize a compact, immutable PIT panel for Grandma US research."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.provenance = ManifestProvenanceService(store)

    def _record_derived_provenance(
        self,
        *,
        manifest_id: str,
        dataset_id: str,
        metadata: Mapping[str, Any],
        operation: str,
    ) -> None:
        self.provenance.record(
            manifest_id=manifest_id,
            dataset_id=dataset_id,
            gateway="DATATUBE",
            upstream_provider="derived",
            endpoint="equity_monthly_research.materialize",
            gateway_version=EQUITY_MONTHLY_RESEARCH_ENGINE_VERSION,
            provider_version=PANEL_SOURCE_VERSION,
            request={"operation": operation, "metadata": dict(metadata)},
            source_policy={"mode": "FIXED_MANIFESTS", "providers": ["crsp", "sec"]},
        )

    def _snapshot_legacy_lineage(self, dataset_id: str) -> None:
        entry = self.catalog.get_catalog(dataset_id)
        if entry is None or not entry.latest_manifest_id:
            return
        if self.provenance.get(entry.latest_manifest_id) is not None:
            return
        self._record_derived_provenance(
            manifest_id=entry.latest_manifest_id,
            dataset_id=dataset_id,
            metadata=entry.metadata,
            operation="legacy_catalog_metadata_snapshot",
        )

    def materialize(
        self,
        *,
        project_id: str,
        universe_snapshot_id: str,
        start_date: str,
        end_date: str,
        source_manifest_ids: Mapping[str, str] | None = None,
        minimum_listing_age_days: int = 365,
    ) -> dict[str, Any]:
        sources = {**CORE_SOURCE_MANIFESTS, **dict(source_manifest_ids or {})}
        panel_dataset_id = f"derived:{project_id}:grandma_us_monthly"
        benchmark_dataset_id = f"derived:{project_id}:grandma_us_benchmark_daily"
        # Catalog metadata changes when a new Manifest becomes latest. Preserve
        # the old Manifest's execution lineage before the derived dataset upsert.
        self._snapshot_legacy_lineage(panel_dataset_id)
        self._snapshot_legacy_lineage(benchmark_dataset_id)
        snapshot = UniverseService(self.store).get_snapshot(universe_snapshot_id)
        if snapshot is None:
            raise ValueError("Universe Snapshot not found")
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        if start > end:
            raise ValueError("start_date must not be after end_date")
        manifests = {}
        paths: dict[str, list[str]] = {}
        for name, manifest_id in sources.items():
            manifest = self.catalog.get_manifest(manifest_id)
            if manifest is None or manifest.status != "READY":
                raise ValueError(f"source Manifest is not READY: {manifest_id}")
            manifests[name] = manifest
            paths[name] = [
                str(resolve_managed_history_path(item.file_uri, base_dir=BASE_DIR))
                for item in manifest.partitions
            ]

        intervals = dict(snapshot.selection_inputs.get("membership_intervals") or {})
        members = []
        for instrument_id in snapshot.actual_instrument_ids:
            interval = intervals.get(instrument_id) or {}
            members.append({
                "instrument_id": instrument_id,
                "eligible_from": _clean(interval.get("eligible_from"))[:10] or start.isoformat(),
                "eligible_to": _clean(interval.get("eligible_to"))[:10] or end.isoformat(),
                "listed_from": _clean(interval.get("eligible_from"))[:10] or start.isoformat(),
            })
        definition = UniverseService(self.store).get_definition(snapshot.universe_definition_id)
        if definition and definition.universe_type == "HISTORICAL_EQUITY_PIT":
            master_rows = EquitySecurityMasterService(self.store).list_overlapping(
                start=str(definition.parameters["history_start"]),
                end=str(definition.parameters["history_end"]),
                primary_exchanges=definition.parameters.get("primary_exchanges") or (),
                security_types=definition.parameters.get("security_types") or ("EQTY",),
                share_types=definition.parameters.get("share_types") or ("NS", "COM"),
            )
            listed = {}
            master = EquitySecurityMasterService(self.store)
            for row in master_rows:
                instrument_id = master.instrument_id_for_permno(row["permno"])
                value = _clean(row.get("valid_from"))[:10]
                if value and (instrument_id not in listed or value < listed[instrument_id]):
                    listed[instrument_id] = value
            for row in members:
                row["listed_from"] = listed.get(row["instrument_id"], row["listed_from"])

        try:
            import duckdb
            import pyarrow as pa
        except ImportError as exc:
            raise RuntimeError("equity research materialization requires duckdb and pyarrow") from exc

        with tempfile.TemporaryDirectory(prefix="datatube-grandma-") as temp_dir:
            con = duckdb.connect()
            con.execute("SET threads=4")
            con.execute("SET memory_limit='10GB'")
            con.execute(f"SET temp_directory='{Path(temp_dir).as_posix()}'")
            con.register("membership", pa.Table.from_pylist(members))
            for name, source_paths in paths.items():
                con.read_parquet(source_paths).create_view(f"src_{name}")
            con.execute(
                """
                CREATE TEMP TABLE monthly_prices AS
                WITH daily AS (
                    SELECT b.instrument_id,
                           CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE) AS trade_date,
                           CAST(b.bar_start_time AS VARCHAR) AS bar_start_time,
                           CAST(b.bar_end_time AS VARCHAR) AS bar_end_time,
                           CAST(b.available_time AS VARCHAR) AS available_time,
                           CAST(b.open AS DOUBLE) AS open,
                           CAST(b.close AS DOUBLE) AS close,
                           CAST(b.volume AS DOUBLE) AS volume,
                           CAST(b.total_return AS DOUBLE) AS total_return,
                           CAST(b.price_adjustment_factor AS DOUBLE) AS price_adjustment_factor,
                           CAST(m.listed_from AS DATE) AS listed_from,
                           avg(abs(CAST(b.close AS DOUBLE))*CAST(b.volume AS DOUBLE)) OVER (
                               PARTITION BY b.instrument_id ORDER BY CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                           ) AS adv20_usd,
                           count(*) OVER (
                               PARTITION BY b.instrument_id ORDER BY CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                           ) AS adv20_count
                    FROM src_bars b
                    JOIN membership m USING (instrument_id)
                    WHERE CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(m.eligible_from AS DATE) AND CAST(m.eligible_to AS DATE)
                      AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(? AS DATE) - INTERVAL 40 DAY AND CAST(? AS DATE)
                      AND b.close IS NOT NULL AND b.volume IS NOT NULL
                ), ranked AS (
                    SELECT *,
                           row_number() OVER (
                        PARTITION BY instrument_id, year(trade_date), month(trade_date)
                        ORDER BY trade_date DESC
                           ) AS month_rank,
                           max(trade_date) OVER (
                        PARTITION BY year(trade_date), month(trade_date)
                           ) AS calendar_month_end
                    FROM daily
                )
                SELECT * EXCLUDE(month_rank, calendar_month_end) FROM ranked
                WHERE month_rank=1 AND trade_date=calendar_month_end
                  AND trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                """,
                [start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()],
            )
            con.execute(
                """
                CREATE TEMP TABLE monthly_valuation AS
                SELECT m.*, max(CAST(v.market_cap AS DOUBLE))*1000.0 AS market_cap_usd
                FROM monthly_prices m
                LEFT JOIN src_valuation v
                  ON v.instrument_id=m.instrument_id
                 AND CAST(substr(CAST(v.event_time AS VARCHAR),1,10) AS DATE)=m.trade_date
                 AND CAST(v.available_time AS VARCHAR)<=m.available_time
                GROUP BY ALL
                """
            )
            con.execute(
                """
                CREATE TEMP TABLE dividend_actions AS
                WITH eligible_actions AS (
                    SELECT a.instrument_id,
                           CAST(substr(CAST(a.event_time AS VARCHAR),1,10) AS DATE) AS action_date,
                           CAST(a.event_time AS VARCHAR) AS event_time,
                           CAST(a.available_time AS VARCHAR) AS available_time,
                           CAST(a.cash_dividend AS DOUBLE) AS cash_dividend
                    FROM src_corporate_actions a
                    JOIN membership m USING (instrument_id)
                    WHERE CAST(a.cash_dividend AS DOUBLE)>0
                      AND CAST(substr(CAST(a.event_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(m.eligible_from AS DATE) - INTERVAL 365 DAY
                              AND CAST(m.eligible_to AS DATE)
                      AND CAST(substr(CAST(a.event_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(? AS DATE) - INTERVAL 365 DAY AND CAST(? AS DATE)
                ), bar_factors AS (
                    SELECT b.instrument_id,
                           CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE) AS bar_date,
                           CAST(b.price_adjustment_factor AS DOUBLE) AS price_adjustment_factor
                    FROM src_bars b
                    JOIN membership m USING (instrument_id)
                    WHERE b.price_adjustment_factor IS NOT NULL
                      AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(m.eligible_from AS DATE) - INTERVAL 365 DAY
                              AND CAST(m.eligible_to AS DATE)
                      AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                          BETWEEN CAST(? AS DATE) - INTERVAL 365 DAY AND CAST(? AS DATE)
                )
                SELECT a.*, b.price_adjustment_factor AS action_price_adjustment_factor
                FROM eligible_actions a
                ASOF LEFT JOIN bar_factors b
                  ON a.instrument_id=b.instrument_id AND a.action_date>=b.bar_date
                """,
                [start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()],
            )
            con.execute(
                """
                CREATE TEMP TABLE monthly_dividends AS
                SELECT m.*,
                       coalesce(sum(CASE WHEN a.cash_dividend>0
                                         THEN a.cash_dividend
                                              * coalesce(m.price_adjustment_factor, 1.0)
                                              / nullif(coalesce(a.action_price_adjustment_factor, 1.0), 0.0)
                                         ELSE 0 END),0.0)
                           AS cash_dividend_365d
                FROM monthly_valuation m
                LEFT JOIN dividend_actions a
                  ON a.instrument_id=m.instrument_id
                 AND a.action_date BETWEEN m.trade_date - INTERVAL 365 DAY AND m.trade_date
                 AND a.available_time<=m.available_time
                GROUP BY ALL
                """
            )
            concepts = [*FLOW_CONCEPTS["net_income_ttm"], *FLOW_CONCEPTS["operating_cash_flow_ttm"], *EQUITY_CONCEPTS]
            placeholders = ",".join("?" for _ in concepts)
            facts = con.execute(
                f"""
                SELECT instrument_id, concept, taxonomy, unit, value, period_start,
                       period_end, form, available_time, accession_number
                FROM src_fundamentals
                WHERE instrument_id IN (SELECT instrument_id FROM membership)
                  AND concept IN ({placeholders})
                  AND upper(unit)='USD'
                  AND CAST(available_time AS VARCHAR)<=?
                ORDER BY instrument_id, available_time, period_end
                """,
                [*concepts, f"{end.isoformat()}T23:59:59+00:00"],
            ).fetch_arrow_table().to_pylist()
            metric_events = project_fundamental_metric_events(facts)
            if metric_events:
                con.register("fundamental_events", pa.Table.from_pylist(metric_events))
                con.execute(
                    """
                    CREATE TEMP TABLE monthly_metrics AS
                    SELECT m.*, f.net_income_ttm, f.operating_cash_flow_ttm,
                           f.shareholders_equity
                    FROM monthly_dividends m
                    ASOF LEFT JOIN fundamental_events f
                      ON m.instrument_id=f.instrument_id
                     AND m.available_time>=CAST(f.available_time AS VARCHAR)
                    """
                )
            else:
                con.execute(
                    """
                    CREATE TEMP TABLE monthly_metrics AS
                    SELECT *, NULL::DOUBLE AS net_income_ttm,
                           NULL::DOUBLE AS operating_cash_flow_ttm,
                           NULL::DOUBLE AS shareholders_equity
                    FROM monthly_dividends
                    """
                )
            panel_table = con.execute(
                """
                WITH screened AS (
                    SELECT *,
                        date_diff('day', listed_from, trade_date) AS listing_age_days,
                        cash_dividend_365d/nullif(close,0) AS dividend_yield,
                        market_cap_usd BETWEEN 100000000.0 AND 5000000000.0
                          AND adv20_count>=20 AND adv20_usd>5000000.0
                          AND date_diff('day', listed_from, trade_date)>=? AS base_eligible
                    FROM monthly_metrics
                ), medians AS (
                    SELECT *, median(CASE WHEN base_eligible THEN dividend_yield END)
                        OVER (PARTITION BY trade_date) AS eligible_dividend_yield_median
                    FROM screened
                )
                SELECT instrument_id,
                       bar_start_time AS event_time, bar_start_time, bar_end_time, available_time,
                       trade_date::VARCHAR AS signal_date, open, close, market_cap_usd,
                       adv20_usd, listing_age_days, cash_dividend_365d, dividend_yield,
                       eligible_dividend_yield_median, net_income_ttm,
                       operating_cash_flow_ttm, shareholders_equity,
                       CASE WHEN base_eligible THEN market_cap_usd END AS size_score,
                       CASE WHEN base_eligible AND cash_dividend_365d>0
                                  AND dividend_yield>eligible_dividend_yield_median
                            THEN market_cap_usd END AS size_div_score,
                       CASE WHEN base_eligible AND net_income_ttm>0
                                  AND operating_cash_flow_ttm>0 AND shareholders_equity>0
                            THEN market_cap_usd END AS size_quality_score,
                       CASE WHEN base_eligible AND close BETWEEN 5.0 AND 30.0
                            THEN market_cap_usd END AS size_price_score,
                       CASE WHEN base_eligible AND cash_dividend_365d>0
                                  AND dividend_yield>eligible_dividend_yield_median
                                  AND net_income_ttm>0 AND operating_cash_flow_ttm>0
                                  AND shareholders_equity>0 AND close BETWEEN 5.0 AND 30.0
                            THEN market_cap_usd END AS grandma_us_score
                FROM medians ORDER BY event_time, instrument_id
                """,
                [int(minimum_listing_age_days)],
            ).fetch_arrow_table()
            benchmark_table = con.execute(
                """
                SELECT min(CAST(b.bar_start_time AS VARCHAR)) AS event_time,
                       max(CAST(b.available_time AS VARCHAR)) AS available_time,
                       avg(CAST(b.total_return AS DOUBLE)) FILTER (
                           WHERE isfinite(CAST(b.total_return AS DOUBLE))
                       ) AS benchmark_return,
                       count(*) FILTER (WHERE isfinite(CAST(b.total_return AS DOUBLE))) AS eligible_count
                FROM src_bars b JOIN membership m USING (instrument_id)
                WHERE CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                      BETWEEN CAST(m.eligible_from AS DATE) AND CAST(m.eligible_to AS DATE)
                  AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                      BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                GROUP BY CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)
                ORDER BY event_time
                """,
                [start.isoformat(), end.isoformat()],
            ).fetch_arrow_table()
            con.close()

        committer = CanonicalDatasetCommitter(self.store)
        benchmark = committer.commit(
            dataset_id=benchmark_dataset_id,
            instrument_id="equity:CRSP:ALL",
            data_type="equity_research_benchmark_daily",
            frequency="1d",
            source="DATATUBE_DERIVED",
            source_version=PANEL_SOURCE_VERSION,
            schema_version=BENCHMARK_SCHEMA_VERSION,
            rows=benchmark_table.to_pylist(),
            point_in_time_policy="AS_OF",
            metadata={
                "project_id": project_id,
                "universe_snapshot_id": universe_snapshot_id,
                "source_manifest_ids": list(sources.values()),
                "benchmark_spec": {"type": "EQUAL_WEIGHT_UNIVERSE", "rebalance_frequency": "DAILY"},
            },
        )
        benchmark_manifest_id = benchmark["manifest"].manifest_id
        self._record_derived_provenance(
            manifest_id=benchmark_manifest_id,
            dataset_id=benchmark_dataset_id,
            metadata=benchmark["catalog"].metadata,
            operation="materialize_benchmark",
        )
        panel_rows = panel_table.to_pylist()
        if not panel_rows:
            raise ValueError("equity monthly panel contains no real month-end observations")
        panel = committer.commit(
            dataset_id=panel_dataset_id,
            instrument_id="equity:CRSP:ALL",
            data_type="equity_research_monthly",
            frequency="1d",
            source="DATATUBE_DERIVED",
            source_version=PANEL_SOURCE_VERSION,
            schema_version=PANEL_SCHEMA_VERSION,
            rows=panel_rows,
            point_in_time_policy="AS_OF",
            coverage_start_time=f"{start.isoformat()}T00:00:00+00:00",
            coverage_end_time=f"{end.isoformat()}T23:59:59+00:00",
            metadata={
                "project_id": project_id,
                "universe_snapshot_id": universe_snapshot_id,
                "source_manifest_ids": list(sources.values()),
                "source_manifests": sources,
                "benchmark_manifest_id": benchmark_manifest_id,
                "minimum_listing_age_days": int(minimum_listing_age_days),
                "market_cap_usd_range": [100_000_000, 5_000_000_000],
                "minimum_adv20_usd": 5_000_000,
                "signal_frequency": "MONTH_END",
                "declared_coverage": [start.isoformat(), end.isoformat()],
                "pit_ttm_method": "PRIOR_ANNUAL_PLUS_CURRENT_YTD_MINUS_PRIOR_COMPARABLE_YTD",
                "fundamental_unit": "USD",
                "dividend_adjustment_method": (
                    "CRSP_CUMULATIVE_PRICE_FACTOR_TO_SIGNAL_DATE_BASIS_"
                    "ASOF_ACTION_DATE_NO_LOOKAHEAD"
                ),
            },
        )
        self._record_derived_provenance(
            manifest_id=panel["manifest"].manifest_id,
            dataset_id=panel_dataset_id,
            metadata=panel["catalog"].metadata,
            operation="materialize_panel",
        )
        return {
            "panel_dataset_id": panel["dataset_id"],
            "panel_manifest_id": panel["manifest"].manifest_id,
            "panel_row_count": panel["row_count"],
            "benchmark_dataset_id": benchmark["dataset_id"],
            "benchmark_manifest_id": benchmark_manifest_id,
            "benchmark_row_count": benchmark["row_count"],
            "source_manifest_ids": sources,
        }


class EquityMonthlyResearchBacktester:
    """Backtest sparse monthly US-equity targets against pinned CRSP daily rows."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.catalog = DatasetCatalogService(store)

    def simulate(
        self,
        *,
        targets: Sequence[Mapping[str, Any]],
        bars_manifest_id: str,
        benchmark_manifest_id: str,
        initial_cash: float,
        execution_spec: BacktestExecutionSpec,
        portfolio_spec: PortfolioSpec,
        dataset_manifest_ids: Sequence[str],
        universe_snapshot_ids: Sequence[str],
        factor_artifact_ids: Sequence[str],
        alpha_artifact_ids: Sequence[str],
        input_bundle_id: str,
    ) -> ResearchBacktestResult:
        bars_manifest = self.catalog.get_manifest(bars_manifest_id)
        benchmark_manifest = self.catalog.get_manifest(benchmark_manifest_id)
        if bars_manifest is None or benchmark_manifest is None:
            raise ValueError("derived panel source or benchmark Manifest is unavailable")
        intervals = []
        ordered_targets = sorted(targets, key=lambda row: _clean(row.get("available_time")))
        for index, target in enumerate(ordered_targets):
            end = (
                _clean(ordered_targets[index + 1].get("available_time"))[:10]
                if index + 1 < len(ordered_targets)
                else (_as_date(target.get("available_time")) + timedelta(days=40)).isoformat()
            )
            for instrument_id in target.get("selected_instrument_ids") or []:
                intervals.append({
                    "instrument_id": instrument_id,
                    "start_date": _clean(target.get("available_time"))[:10],
                    "end_date": end,
                })
        try:
            import duckdb
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("equity research backtest requires duckdb and pyarrow") from exc
        bars_paths = [str(resolve_managed_history_path(p.file_uri, base_dir=BASE_DIR)) for p in bars_manifest.partitions]
        benchmark_paths = [str(resolve_managed_history_path(p.file_uri, base_dir=BASE_DIR)) for p in benchmark_manifest.partitions]
        con = duckdb.connect()
        con.execute("SET threads=4")
        con.read_parquet(bars_paths).create_view("bars")
        con.register("holding_intervals", pa.Table.from_pylist(intervals or [{
            "instrument_id": "", "start_date": "1900-01-01", "end_date": "1900-01-01"
        }]))
        daily_rows = con.execute(
            """
            SELECT DISTINCT b.instrument_id,
                   CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)::VARCHAR AS trade_date,
                   CAST(b.bar_start_time AS VARCHAR) AS event_time,
                   CAST(b.open AS DOUBLE) AS open, CAST(b.close AS DOUBLE) AS close,
                   CAST(b.total_return AS DOUBLE) AS total_return,
                   CAST(b.price_return AS DOUBLE) AS price_return
            FROM bars b JOIN holding_intervals i
              ON b.instrument_id=i.instrument_id
             AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)>=CAST(i.start_date AS DATE)
             AND CAST(substr(CAST(b.bar_start_time AS VARCHAR),1,10) AS DATE)<=CAST(i.end_date AS DATE)+INTERVAL 7 DAY
            WHERE b.open>0 AND b.close>0
            ORDER BY trade_date, instrument_id
            """
        ).fetch_arrow_table().to_pylist()
        benchmark_rows = pq.read_table(benchmark_paths).to_pylist()
        con.close()
        by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in daily_rows:
            by_date[_clean(row["trade_date"])][_clean(row["instrument_id"])] = row
        dates = sorted({_clean(row.get("event_time"))[:10] for row in benchmark_rows if _clean(row.get("event_time"))})
        target_cursor = 0
        pending = ordered_targets[0] if ordered_targets else None
        cash = float(initial_cash)
        holdings: dict[str, float] = {}
        last_close: dict[str, float] = {}
        equity_curve: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        rebalances: list[dict[str, Any]] = []
        total_fees = total_slippage = total_turnover = 0.0
        missing_total_return_count = price_return_fallback_count = missing_mark_count = 0
        fee_rate = execution_spec.fee_bps / 10_000.0
        slip_rate = execution_spec.slippage_bps / 10_000.0
        for trade_date in dates:
            day = by_date.get(trade_date, {})
            execute = pending is not None and trade_date >= _clean(pending.get("available_time"))[:10]
            required = set(holdings) | set((pending or {}).get("weights") or {})
            if execute and required and not required.issubset(day):
                # A disappearing CRSP security is liquidated at its last marked
                # value; the final CRSP total return was already recognized.
                missing_existing = set(holdings) - set(day)
                for instrument_id in missing_existing:
                    cash += holdings.pop(instrument_id)
                required = set(holdings) | set((pending or {}).get("weights") or {})
                execute = required.issubset(day)
            if execute and pending is not None:
                # Existing positions are marked from the prior close to this
                # execution open exactly once.  Non-rebalance sessions below
                # use CRSP close-to-close total_return instead.
                for instrument_id in list(holdings):
                    row = day.get(instrument_id)
                    previous_close = last_close.get(instrument_id)
                    if row and previous_close and previous_close > 0:
                        holdings[instrument_id] *= float(row["open"]) / previous_close
                equity_open = cash + sum(holdings.values())
                weights = {str(k): float(v) for k, v in dict(pending.get("weights") or {}).items()}
                desired = {instrument_id: equity_open * weight for instrument_id, weight in weights.items()}
                names = sorted(set(holdings) | set(desired))
                before_holdings = dict(holdings)
                event_start = len(orders)
                turnover = sum(abs(desired.get(name, 0.0) - before_holdings.get(name, 0.0)) for name in names)
                fees = turnover * fee_rate
                slippage = turnover * slip_rate
                total_fees += fees
                total_slippage += slippage
                total_turnover += turnover
                post_cost_equity = max(0.0, equity_open - fees - slippage)
                investable = sum(weights.values())
                holdings = {
                    name: post_cost_equity * weight
                    for name, weight in weights.items() if weight > 0
                }
                cash = post_cost_equity * max(0.0, 1.0 - investable)
                for name in names:
                    change = desired.get(name, 0.0) - before_holdings.get(name, 0.0)
                    # Order rows are not used for accounting; they preserve a
                    # deterministic audit trail of target-weight turnover.
                    reference = float(day[name]["open"]) if name in day else float(last_close.get(name) or 1.0)
                    if abs(change) <= 1e-12:
                        continue
                    orders.append(ResearchBacktestProvider._order_row(
                        order_number=len(orders) + 1,
                        event_time=day.get(name, {}).get("event_time") or trade_date,
                        signal_time=_clean(pending.get("as_of_time")),
                        signal_available_time=_clean(pending.get("available_time")),
                        instrument_id=name,
                        side="BUY" if change > 0 else "SELL",
                        quantity=abs(change) / reference,
                        reference_price=reference,
                        fill_price=reference * (1.0 + slip_rate if change > 0 else 1.0 - slip_rate),
                        fee=abs(change) * fee_rate,
                        slippage_cost=abs(change) * slip_rate,
                        target_weight=weights.get(name, 0.0),
                    ))
                rebalances.append({
                    "rebalance_id": f"rebalance_{len(rebalances)+1:06d}",
                    "signal_time": pending.get("as_of_time"),
                    "signal_available_time": pending.get("available_time"),
                    "execution_time": trade_date,
                    "target_weights": weights,
                    "selected_instrument_ids": list(pending.get("selected_instrument_ids") or []),
                    "target_state": pending.get("target_state"),
                    "selection_reason": pending.get("selection_reason"),
                    "order_count": len(orders) - event_start,
                    "buy_scale": 1.0,
                    "equity_at_open": equity_open,
                    "universe_snapshot_id": pending.get("universe_snapshot_id"),
                })
                target_cursor += 1
                pending = ordered_targets[target_cursor] if target_cursor < len(ordered_targets) else None
            # Mark from today's open to close after a rebalance, otherwise use
            # CRSP total return (which includes distributions and delisting).
            rebalance_today = bool(rebalances and rebalances[-1]["execution_time"] == trade_date)
            for instrument_id in list(holdings):
                row = day.get(instrument_id)
                if not row:
                    missing_mark_count += 1
                    continue
                if rebalance_today:
                    holdings[instrument_id] *= float(row["close"]) / float(row["open"])
                else:
                    total_return = _finite(row.get("total_return"))
                    if total_return is None:
                        missing_total_return_count += 1
                        total_return = _finite(row.get("price_return"))
                        if total_return is not None:
                            price_return_fallback_count += 1
                    if total_return is not None and total_return >= -1.0:
                        holdings[instrument_id] *= 1.0 + total_return
                last_close[instrument_id] = float(row["close"])
            equity = cash + sum(holdings.values())
            equity_curve.append({
                "event_time": trade_date + "T21:00:00+00:00",
                "equity": equity,
                "cash": cash,
                "cash_ratio": cash/equity if equity else 0.0,
                "gross_exposure": sum(holdings.values())/equity if equity else 0.0,
                "positions": {},
                "position_values": dict(holdings),
                "position_weights": {k: v/equity for k, v in holdings.items()} if equity else {},
            })
        common_times = tuple(row["event_time"] for row in equity_curve)
        drawdown = ResearchBacktestProvider._drawdown_curve(equity_curve, initial_cash)
        metrics = ResearchBacktestProvider._metrics(
            equity_curve=equity_curve, drawdown_curve=drawdown,
            initial_cash=initial_cash, common_times=common_times, orders=orders,
            rebalance_events=rebalances, total_fees=total_fees,
            total_slippage_cost=total_slippage, total_turnover_notional=total_turnover,
            instrument_count=len({item for target in ordered_targets for item in target.get("selected_instrument_ids") or []}),
        )
        benchmark_equity = initial_cash
        for row in benchmark_rows:
            value = _finite(row.get("benchmark_return"))
            if value is not None and value >= -1:
                benchmark_equity *= 1.0 + value
        execution_payload = execution_spec.to_dict()
        metrics.update({
            "execution_engine": RESEARCH_BACKTEST_CAPABILITIES.provider,
            "engine_version": (
                f"{RESEARCH_BACKTEST_ENGINE_VERSION}+{EQUITY_MONTHLY_RESEARCH_ENGINE_VERSION}"
            ),
            "code_hash": _canonical_hash({
                "research_backtest": RESEARCH_BACKTEST_CODE_HASH,
                "equity_monthly": EQUITY_MONTHLY_RESEARCH_CODE_HASH,
            }),
            "dataset_manifest_ids": list(dataset_manifest_ids),
            "universe_snapshot_ids": list(universe_snapshot_ids),
            "factor_artifact_ids": list(factor_artifact_ids),
            "alpha_artifact_ids": list(alpha_artifact_ids),
            "input_bundle_id": input_bundle_id,
            "portfolio_spec": portfolio_spec.to_dict(),
            "portfolio_spec_hash": portfolio_spec.spec_hash,
            "execution_spec_hash": _canonical_hash(execution_payload),
            "started_at": common_times[0] if common_times else "",
            "completed_at": common_times[-1] if common_times else "",
            "skipped_signal_count": max(0, len(ordered_targets)-len(rebalances)),
            "benchmark_final_equity": benchmark_equity,
            "benchmark_total_return": benchmark_equity/initial_cash-1.0,
            "excess_total_return": metrics["total_return"]-(benchmark_equity/initial_cash-1.0),
            "benchmark_manifest_id": benchmark_manifest_id,
            "missing_total_return_count": missing_total_return_count,
            "price_return_fallback_count": price_return_fallback_count,
            "missing_position_mark_count": missing_mark_count,
        })
        return ResearchBacktestResult(
            execution_spec=execution_payload, metrics=metrics,
            equity_curve=tuple(equity_curve), orders=tuple(orders),
            drawdown_curve=tuple(drawdown), rebalance_events=tuple(rebalances),
            dataset_manifest_ids=tuple(dataset_manifest_ids),
            universe_snapshot_ids=tuple(universe_snapshot_ids),
            factor_artifact_ids=tuple(factor_artifact_ids),
            alpha_artifact_ids=tuple(alpha_artifact_ids), input_bundle_id=input_bundle_id,
        )
