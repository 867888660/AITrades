from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
import statistics
from array import array
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple


EVALUATION_ENGINE_VERSION = "evaluation-engine.v2"
EVALUATION_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    material = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return _percentile_sorted(ordered, probability)


def _percentile_sorted(
    ordered: Sequence[float], probability: float
) -> float | None:
    if not ordered:
        return None
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _average_ranks(values: Mapping[str, float], *, descending: bool = False) -> Dict[str, float]:
    ordered = sorted(values, key=lambda item: (values[item], item), reverse=descending)
    result: Dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        average = ((start + 1) + end) / 2.0
        for index in range(start, end):
            result[ordered[index]] = average
        start = end
    return result


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_sum = sum((item - left_mean) ** 2 for item in left)
    right_sum = sum((item - right_mean) ** 2 for item in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator > 0 else None


def _rank_correlation(left: Mapping[str, float], right: Mapping[str, float], minimum_size: int) -> float | None:
    instruments = sorted(set(left) & set(right))
    if len(instruments) < minimum_size:
        return None
    left_ranks = _average_ranks({item: left[item] for item in instruments})
    right_ranks = _average_ranks({item: right[item] for item in instruments})
    return _pearson([left_ranks[item] for item in instruments], [right_ranks[item] for item in instruments])


def _quantile_groups(values: Mapping[str, float], count: int) -> Dict[str, int]:
    ordered = sorted(values, key=lambda item: (values[item], item))
    size = len(ordered)
    return {
        instrument_id: min(count, int(index * count / size) + 1)
        for index, instrument_id in enumerate(ordered)
    } if size else {}


def _summarize_ic(
    spec: "EvaluationSpec", ic_series: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    ic_summary: dict[str, Any] = {}
    rank_ic_summary: dict[str, Any] = {}
    for horizon in spec.horizons:
        for field, target in (("ic", ic_summary), ("rank_ic", rank_ic_summary)):
            values = [
                float(item[field])
                for item in ic_series
                if int(item.get("horizon_bars") or 0) == horizon
                and _finite(item.get(field)) is not None
            ]
            mean = statistics.fmean(values) if values else None
            std = statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None
            target[str(horizon)] = {
                "count": len(values),
                "mean": mean,
                "std": std,
                "icir": mean / std if mean is not None and std and std > 0 else None,
                "t_stat": (
                    mean / (std / math.sqrt(len(values)))
                    if mean is not None and std and std > 0 and len(values) > 1
                    else None
                ),
                "positive_rate": (
                    sum(item > 0 for item in values) / len(values) if values else None
                ),
            }
    return ic_summary, rank_ic_summary


@dataclass(frozen=True)
class EvaluationSpec:
    horizons: Tuple[int, ...] = (1, 6, 24)
    quantile_count: int = 5
    minimum_cross_section_size: int = 4
    top_n: int = 2
    evaluation_dimension: str = "CROSS_SECTIONAL"
    ic_methods: Tuple[str, ...] = ("PEARSON", "SPEARMAN")
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    return_definition: str = "NEXT_BAR_OPEN_TO_HORIZON_CLOSE"
    retain_observations: bool = True
    engine_version: str = EVALUATION_ENGINE_VERSION
    code_hash: str = EVALUATION_CODE_HASH

    def __post_init__(self) -> None:
        normalized = tuple(sorted({int(item) for item in self.horizons}))
        if not normalized or normalized[0] < 1:
            raise ValueError("evaluation horizons must be positive")
        if normalized != self.horizons:
            object.__setattr__(self, "horizons", normalized)
        if self.quantile_count < 2:
            raise ValueError("quantile_count must be at least 2")
        if self.minimum_cross_section_size < 1:
            raise ValueError("minimum_cross_section_size must be at least 1")
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        dimension = str(self.evaluation_dimension or "").strip().upper()
        if dimension != "CROSS_SECTIONAL":
            raise ValueError("formal Factor/Alpha Run v1 currently supports CROSS_SECTIONAL evaluation")
        object.__setattr__(self, "evaluation_dimension", dimension)
        methods = tuple(dict.fromkeys(str(item).strip().upper() for item in self.ic_methods if str(item).strip()))
        if not methods:
            raise ValueError("ic_methods must contain at least one method")
        unsupported = sorted(set(methods) - {"PEARSON", "SPEARMAN"})
        if unsupported:
            raise ValueError(f"unsupported IC methods: {unsupported}")
        object.__setattr__(self, "ic_methods", methods)
        if self.return_definition != "NEXT_BAR_OPEN_TO_HORIZON_CLOSE":
            raise ValueError("unsupported future-return definition")
        for value, name in ((self.fee_bps, "fee_bps"), (self.slippage_bps, "slippage_bps")):
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be finite and non-negative")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizons": list(self.horizons),
            "quantile_count": self.quantile_count,
            "minimum_cross_section_size": self.minimum_cross_section_size,
            "top_n": self.top_n,
            "evaluation_dimension": self.evaluation_dimension,
            "ic_methods": list(self.ic_methods),
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "return_definition": self.return_definition,
            "retain_observations": self.retain_observations,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _canonical_hash(self.to_dict())


@dataclass(frozen=True)
class EvaluationResult:
    evaluation_type: str
    summary: Dict[str, Any]
    observations: Tuple[Dict[str, Any], ...]
    ic_series: Tuple[Dict[str, Any], ...] = ()
    group_return_series: Tuple[Dict[str, Any], ...] = ()
    stability_series: Tuple[Dict[str, Any], ...] = ()

    def artifact_rows(self) -> list[dict[str, str]]:
        rows = [{"record_type": "SUMMARY", "payload_json": json.dumps(self.summary, ensure_ascii=False, sort_keys=True)}]
        for record_type, records in (
            ("OBSERVATION", self.observations),
            ("IC", self.ic_series),
            ("GROUP_RETURN", self.group_return_series),
            ("STABILITY", self.stability_series),
        ):
            rows.extend(
                {"record_type": record_type, "payload_json": json.dumps(item, ensure_ascii=False, sort_keys=True)}
                for item in records
            )
        return rows


class FutureReturnBuilder:
    def __init__(self, bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]]):
        self._bars: dict[str, list[Mapping[str, Any]]] = {}
        for instrument_id, raw_rows in bars_by_instrument.items():
            rows = raw_rows if isinstance(raw_rows, list) else list(raw_rows)
            rows.sort(key=lambda item: str(item.get("bar_start_time") or item.get("event_time")))
            seen: set[datetime] = set()
            write_index = 0
            for row in rows:
                start = _parse_time(row.get("bar_start_time") or row.get("event_time"))
                if start in seen:
                    raise ValueError(f"duplicate bar for future returns: {instrument_id} {start.isoformat()}")
                seen.add(start)
                open_price = _finite(row.get("open"))
                close_price = _finite(row.get("close"))
                if (
                    open_price is None
                    or close_price is None
                    or open_price <= 0
                    or close_price <= 0
                ):
                    # Archive datasets legitimately contain non-tradable rows
                    # (for example a CRSP security/date without a quoted
                    # open/close).  They cannot define a forward return, but
                    # they must not invalidate every other security in a
                    # cross-sectional evaluation.  Excluding the row keeps
                    # horizons expressed in valid tradable bars and causes an
                    # observation with no future path to be omitted naturally.
                    continue
                rows[write_index] = row
                write_index += 1
            del rows[write_index:]
            self._bars[str(instrument_id)] = rows

    def build(self, instrument_id: str, available_time: str, horizon: int) -> dict[str, Any] | None:
        rows = self._bars.get(str(instrument_id), [])
        entry_index = bisect_left(
            rows,
            str(available_time),
            key=lambda row: str(row.get("bar_start_time") or row.get("event_time")),
        )
        exit_index = entry_index + int(horizon) - 1
        if entry_index >= len(rows) or exit_index >= len(rows):
            return None
        entry_price = float(rows[entry_index]["open"])
        exit_price = float(rows[exit_index]["close"])
        return {
            "future_return": exit_price / entry_price - 1.0,
            "return_start_time": str(
                rows[entry_index].get("bar_start_time")
                or rows[entry_index].get("event_time")
            ),
            "return_end_time": str(
                rows[exit_index].get("bar_end_time")
                or rows[exit_index].get("available_time")
                or rows[exit_index].get("bar_start_time")
                or rows[exit_index].get("event_time")
            ),
            "entry_price": entry_price,
            "exit_price": exit_price,
        }


class FactorEvaluator:
    def evaluate(
        self,
        *,
        spec: EvaluationSpec,
        factor_values_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        universe_snapshot: Any | None = None,
    ) -> EvaluationResult:
        members = set(getattr(universe_snapshot, "actual_instrument_ids", ()) or factor_values_by_instrument.keys())
        universe_snapshot_id = str(getattr(universe_snapshot, "universe_snapshot_id", "") or "")
        from .universe_service import UniverseMembershipIndex

        membership_index = UniverseMembershipIndex(universe_snapshot)
        future = FutureReturnBuilder(bars_by_instrument)
        values: list[float] | array[float] = [] if spec.retain_observations else array("d")
        total_rows = 0
        total_rows_by_instrument: dict[str, int] = {}
        valid_rows_by_instrument: dict[str, int] = {}
        factor_by_time: dict[str, dict[str, float]] = {}
        available_by_time: dict[str, str] = {}
        for instrument_id in sorted(members):
            for row in factor_values_by_instrument.get(instrument_id, []):
                as_of = str(row.get("factor_as_of_time") or row.get("available_time") or row.get("event_time") or "").strip()
                if membership_index.dynamic and (
                    not as_of or not membership_index.contains(instrument_id, as_of)
                ):
                    continue
                total_rows += 1
                total_rows_by_instrument[instrument_id] = total_rows_by_instrument.get(instrument_id, 0) + 1
                value = _finite(row.get("value"))
                if value is None:
                    continue
                available = str(row.get("available_time") or as_of).strip()
                if not as_of or not available:
                    continue
                valid_rows_by_instrument[instrument_id] = valid_rows_by_instrument.get(instrument_id, 0) + 1
                if spec.retain_observations:
                    factor_by_time.setdefault(as_of, {})[instrument_id] = value
                    previous = available_by_time.get(as_of)
                    if previous is None or _parse_time(available) > _parse_time(previous):
                        available_by_time[as_of] = available
                values.append(value)

        if spec.retain_observations:
            cross_sections: Iterable[tuple[str, dict[str, float], str]] = (
                (as_of, factor_by_time[as_of], available_by_time[as_of])
                for as_of in sorted(factor_by_time, key=_parse_time)
            )
            cross_section_count = len(factor_by_time)
        else:
            def instrument_values(
                instrument_id: str,
            ) -> Iterable[tuple[str, str, float, str]]:
                for row in factor_values_by_instrument.get(instrument_id, []):
                    value = _finite(row.get("value"))
                    if value is None:
                        continue
                    as_of = str(
                        row.get("factor_as_of_time")
                        or row.get("available_time")
                        or row.get("event_time")
                        or ""
                    ).strip()
                    available = str(row.get("available_time") or as_of).strip()
                    if (
                        as_of and available
                        and (
                            not membership_index.dynamic
                            or membership_index.contains(instrument_id, as_of)
                        )
                    ):
                        yield as_of, instrument_id, value, available

            merged = heapq.merge(*(
                instrument_values(instrument_id)
                for instrument_id in sorted(members)
            ))

            def streaming_cross_sections() -> Iterable[tuple[str, dict[str, float], str]]:
                for as_of, items in itertools.groupby(merged, key=lambda item: item[0]):
                    cross_section: dict[str, float] = {}
                    available = ""
                    for _, instrument_id, value, item_available in items:
                        cross_section[instrument_id] = value
                        if not available or item_available > available:
                            available = item_available
                    yield as_of, cross_section, available

            cross_sections = streaming_cross_sections()
            cross_section_count = 0

        observations: list[dict[str, Any]] = []
        ic_series: list[dict[str, Any]] = []
        group_series: list[dict[str, Any]] = []
        stability_series: list[dict[str, Any]] = []
        rank_turnovers: list[float] = []
        previous_percentiles: dict[str, float] | None = None
        for as_of, cross_section, available_time in cross_sections:
            if not spec.retain_observations:
                cross_section_count += 1
            active_members = members
            if membership_index.dynamic:
                active_members = membership_index.active_at(as_of)
                cross_section = {
                    instrument_id: value
                    for instrument_id, value in cross_section.items()
                    if instrument_id in active_members
                }
            if len(cross_section) < spec.minimum_cross_section_size:
                continue
            ranks = _average_ranks(cross_section)
            percentiles = {item: ranks[item] / len(ranks) for item in ranks}
            cross_values = list(cross_section.values())
            stability_series.append({
                "as_of_time": as_of,
                "cross_section_mean": statistics.fmean(cross_values),
                "cross_section_std": statistics.pstdev(cross_values) if len(cross_values) > 1 else 0.0,
                "instrument_count": len(cross_values),
                "universe_coverage": len(cross_values) / max(1, len(active_members)),
            })
            if previous_percentiles is not None:
                common = set(previous_percentiles) & set(percentiles)
                if common:
                    rank_turnovers.append(statistics.fmean(abs(percentiles[item] - previous_percentiles[item]) for item in common))
            previous_percentiles = percentiles
            groups = _quantile_groups(cross_section, spec.quantile_count)
            for horizon in spec.horizons:
                returns: dict[str, float] = {}
                rows_for_time: list[dict[str, Any]] = []
                for instrument_id, factor_value in cross_section.items():
                    future_row = future.build(instrument_id, available_time, horizon)
                    if future_row is None:
                        continue
                    returns[instrument_id] = float(future_row["future_return"])
                    observation = {
                        "instrument_id": instrument_id,
                        "as_of_time": as_of,
                        "available_time": available_time,
                        "horizon_bars": horizon,
                        "factor_value": factor_value,
                        "factor_percentile": percentiles[instrument_id],
                        "quantile": groups[instrument_id],
                        "universe_snapshot_id": universe_snapshot_id,
                        **future_row,
                    }
                    if spec.retain_observations:
                        observations.append(observation)
                    rows_for_time.append(observation)
                instruments = sorted(set(cross_section) & set(returns))
                pearson_ic = (
                    _pearson(
                        [cross_section[item] for item in instruments],
                        [returns[item] for item in instruments],
                    )
                    if "PEARSON" in spec.ic_methods and len(instruments) >= spec.minimum_cross_section_size
                    else None
                )
                rank_ic = (
                    _rank_correlation(cross_section, returns, spec.minimum_cross_section_size)
                    if "SPEARMAN" in spec.ic_methods
                    else None
                )
                if pearson_ic is not None or rank_ic is not None:
                    ic_series.append({
                        "as_of_time": as_of,
                        "horizon_bars": horizon,
                        "ic": pearson_ic,
                        "rank_ic": rank_ic,
                        "instrument_count": len(returns),
                    })
                for group in range(1, spec.quantile_count + 1):
                    group_returns = [item["future_return"] for item in rows_for_time if item["quantile"] == group]
                    if group_returns:
                        group_series.append({
                            "as_of_time": as_of,
                            "horizon_bars": horizon,
                            "group": group,
                            "mean_return": statistics.fmean(group_returns),
                            "instrument_count": len(group_returns),
                        })

        summary = self._factor_summary(
            spec,
            values,
            total_rows,
            ic_series,
            group_series,
            stability_series,
            rank_turnovers,
            total_rows_by_instrument,
            valid_rows_by_instrument,
            cross_section_count,
        )
        summary.update({
            "evaluation_type": "FACTOR_EVALUATION",
            "evaluation_spec": spec.to_dict(),
            "evaluation_spec_hash": spec.spec_hash,
            "universe_snapshot_id": universe_snapshot_id,
        })
        return EvaluationResult(
            evaluation_type="FACTOR_EVALUATION",
            summary=summary,
            observations=tuple(observations),
            ic_series=tuple(ic_series),
            group_return_series=tuple(group_series),
            stability_series=tuple(stability_series),
        )

    @staticmethod
    def _factor_summary(
        spec: EvaluationSpec,
        values: Sequence[float],
        total_rows: int,
        ic_series: list[dict[str, Any]],
        group_series: list[dict[str, Any]],
        stability_series: list[dict[str, Any]],
        rank_turnovers: list[float],
        total_rows_by_instrument: dict[str, int],
        valid_rows_by_instrument: dict[str, int],
        cross_section_count: int,
    ) -> dict[str, Any]:
        mean = statistics.fmean(values) if values else None
        std = statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None
        outlier_ratio = 0.0
        if values and std and std > 0 and mean is not None:
            outlier_ratio = sum(abs(item - mean) > 5 * std for item in values) / len(values)
        ordered_values = sorted(values)
        ic_summary, rank_ic_summary = _summarize_ic(spec, ic_series)
        group_summary: dict[str, Any] = {}
        for horizon in spec.horizons:
            by_group = {
                str(group): [item["mean_return"] for item in group_series if item["horizon_bars"] == horizon and item["group"] == group]
                for group in range(1, spec.quantile_count + 1)
            }
            means = {group: statistics.fmean(items) if items else None for group, items in by_group.items()}
            low = means.get("1")
            high = means.get(str(spec.quantile_count))
            monotonic_values = [
                (float(group), value)
                for group, value in means.items()
                if value is not None
            ]
            group_summary[str(horizon)] = {
                "mean_returns": means,
                "high_minus_low": high - low if high is not None and low is not None else None,
                "monotonicity": (
                    _pearson(
                        [item[0] for item in monotonic_values],
                        [item[1] for item in monotonic_values],
                    )
                    if len(monotonic_values) > 1 else None
                ),
            }
        cross_means = [item["cross_section_mean"] for item in stability_series]
        lag_one = _pearson(cross_means[:-1], cross_means[1:]) if len(cross_means) > 2 else None
        coverage_by_instrument = {
            instrument_id: {
                "total_rows": total,
                "valid_rows": valid_rows_by_instrument.get(instrument_id, 0),
                "coverage": valid_rows_by_instrument.get(instrument_id, 0) / total if total else 0.0,
            }
            for instrument_id, total in sorted(total_rows_by_instrument.items())
        }
        eligible_cross_sections = len(stability_series)
        diagnostics: list[dict[str, Any]] = []
        if eligible_cross_sections == 0:
            diagnostics.append({
                "code": "INSUFFICIENT_CROSS_SECTION",
                "severity": "BLOCKED",
                "message": (
                    f"No timestamp met minimum_cross_section_size={spec.minimum_cross_section_size}; "
                    "cross-sectional IC and quantile results are unavailable."
                ),
            })
        elif eligible_cross_sections < 20:
            diagnostics.append({
                "code": "LOW_CROSS_SECTION_SAMPLE",
                "severity": "WARNING",
                "message": f"Only {eligible_cross_sections} eligible cross-sections were evaluated.",
            })
        if eligible_cross_sections and not any(
            item["count"] for item in (*ic_summary.values(), *rank_ic_summary.values())
        ):
            diagnostics.append({
                "code": "IC_UNAVAILABLE",
                "severity": "WARNING",
                "message": "Eligible timestamps exist, but fewer than two instruments have valid forward returns for IC.",
            })
        if total_rows and len(values) / total_rows < 0.8:
            diagnostics.append({
                "code": "LOW_FACTOR_COVERAGE",
                "severity": "WARNING",
                "message": "Valid Factor coverage is below 80%.",
            })
        return {
            "product_run_type": "FACTOR_RUN",
            "evaluation_dimension": spec.evaluation_dimension,
            "total_rows": total_rows,
            "valid_rows": len(values),
            "coverage": len(values) / total_rows if total_rows else 0.0,
            "missing_rate": 1.0 - len(values) / total_rows if total_rows else 1.0,
            "mean": mean,
            "std": std,
            "quantiles": {
                str(int(p * 100)): _percentile_sorted(ordered_values, p)
                for p in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
            },
            "outlier_ratio_5sigma": outlier_ratio,
            "average_rank_turnover": statistics.fmean(rank_turnovers) if rank_turnovers else None,
            "time_stability": {
                "cross_section_mean_std": statistics.pstdev(cross_means) if len(cross_means) > 1 else 0.0 if cross_means else None,
                "cross_section_mean_lag1_correlation": lag_one,
            },
            "coverage_by_instrument": coverage_by_instrument,
            "cross_section_count": cross_section_count,
            "eligible_cross_section_count": eligible_cross_sections,
            "ic": ic_summary,
            "rank_ic": rank_ic_summary,
            "quantile_returns": group_summary,
            "diagnostics": diagnostics,
        }


class AlphaEvaluator:
    def evaluate(
        self,
        *,
        spec: EvaluationSpec,
        alpha_signals: Sequence[Mapping[str, Any]],
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        universe_snapshot: Any | None = None,
    ) -> EvaluationResult:
        members = set(getattr(universe_snapshot, "actual_instrument_ids", ()) or bars_by_instrument.keys())
        universe_snapshot_id = str(getattr(universe_snapshot, "universe_snapshot_id", "") or "")
        future = FutureReturnBuilder(bars_by_instrument)
        observations: list[dict[str, Any]] = []
        ic_series: list[dict[str, Any]] = []
        group_series: list[dict[str, Any]] = []
        stability_series: list[dict[str, Any]] = []
        # Large cross-sectional Alpha studies can contain tens of millions of
        # scores. Keep aggregate inputs compact when row-level observations are
        # disabled instead of retaining one Python float object per score.
        all_scores: list[float] | array[float] = (
            [] if spec.retain_observations else array("d")
        )
        membership_turnovers: list[float] = []
        previous_scores: dict[str, float] | None = None
        previous_top: set[str] | None = None
        for signal in sorted((dict(item) for item in alpha_signals), key=lambda item: _parse_time(item.get("available_time") or item.get("as_of_time"))):
            as_of = str(signal.get("as_of_time") or "").strip()
            available = str(signal.get("available_time") or as_of).strip()
            raw = signal.get("raw_scores") or signal.get("scores")
            scores = {
                str(item): value
                for item, raw_value in (raw.items() if isinstance(raw, Mapping) else [])
                if item in members and (value := _finite(raw_value)) is not None
            }
            if len(scores) < spec.minimum_cross_section_size:
                continue
            all_scores.extend(scores.values())
            if previous_scores is not None:
                stability = _rank_correlation(previous_scores, scores, spec.minimum_cross_section_size)
                if stability is not None:
                    stability_series.append({"as_of_time": as_of, "rank_stability": stability})
            previous_scores = scores
            ordered = sorted(scores, key=lambda item: (-scores[item], item))
            top = set(ordered[:spec.top_n])
            bottom = set(ordered[-spec.top_n:])
            turnover = 1.0 if previous_top is None else 1.0 - len(top & previous_top) / max(1, len(top))
            if previous_top is not None:
                membership_turnovers.append(turnover)
            previous_top = top
            for horizon in spec.horizons:
                returns: dict[str, float] = {}
                # Future-return payloads are only required for the optional
                # row-level artifact. Aggregate-only evaluation must not keep
                # a second dict per instrument and horizon.
                future_rows: dict[str, dict[str, Any]] | None = (
                    {} if spec.retain_observations else None
                )
                for instrument_id in scores:
                    future_row = future.build(instrument_id, available, horizon)
                    if future_row is not None:
                        returns[instrument_id] = float(future_row["future_return"])
                        if future_rows is not None:
                            future_rows[instrument_id] = future_row
                if len(returns) < spec.minimum_cross_section_size:
                    continue
                instruments = sorted(set(scores) & set(returns))
                pearson_ic = (
                    _pearson(
                        [scores[item] for item in instruments],
                        [returns[item] for item in instruments],
                    )
                    if "PEARSON" in spec.ic_methods
                    else None
                )
                rank_ic = (
                    _rank_correlation(scores, returns, spec.minimum_cross_section_size)
                    if "SPEARMAN" in spec.ic_methods
                    else None
                )
                if pearson_ic is not None or rank_ic is not None:
                    ic_series.append({
                        "as_of_time": as_of,
                        "horizon_bars": horizon,
                        "ic": pearson_ic,
                        "rank_ic": rank_ic,
                        "instrument_count": len(instruments),
                    })
                market_return = statistics.fmean(returns.values())
                regime = "BULL" if market_return > 0.001 else "BEAR" if market_return < -0.001 else "SIDEWAYS"
                top_returns = [returns[item] for item in top if item in returns]
                bottom_returns = [returns[item] for item in bottom if item in returns]
                top_mean = statistics.fmean(top_returns) if top_returns else None
                bottom_mean = statistics.fmean(bottom_returns) if bottom_returns else None
                estimated_cost = turnover * (spec.fee_bps + spec.slippage_bps) / 10_000.0
                group_series.append({
                    "as_of_time": as_of,
                    "horizon_bars": horizon,
                    "top_mean_return": top_mean,
                    "bottom_mean_return": bottom_mean,
                    "long_short_spread": top_mean - bottom_mean if top_mean is not None and bottom_mean is not None else None,
                    "estimated_top_return_after_cost": top_mean - estimated_cost if top_mean is not None else None,
                    "membership_turnover": turnover,
                    "market_return": market_return,
                    "market_regime": regime,
                })
                if future_rows is not None:
                    ranks = _average_ranks(scores, descending=True)
                    for instrument_id, score in scores.items():
                        if instrument_id not in future_rows:
                            continue
                        observations.append({
                            "instrument_id": instrument_id,
                            "as_of_time": as_of,
                            "available_time": available,
                            "horizon_bars": horizon,
                            "score": score,
                            "rank": ranks[instrument_id],
                            "selected_top": instrument_id in top,
                            "selected_bottom": instrument_id in bottom,
                            "universe_snapshot_id": universe_snapshot_id,
                            **future_rows[instrument_id],
                        })

        summary = self._alpha_summary(
            spec, all_scores, ic_series, group_series, stability_series, membership_turnovers
        )
        summary.update({
            "evaluation_type": "ALPHA_EVALUATION",
            "evaluation_spec": spec.to_dict(),
            "evaluation_spec_hash": spec.spec_hash,
            "universe_snapshot_id": universe_snapshot_id,
        })
        return EvaluationResult(
            evaluation_type="ALPHA_EVALUATION",
            summary=summary,
            observations=tuple(observations),
            ic_series=tuple(ic_series),
            group_return_series=tuple(group_series),
            stability_series=tuple(stability_series),
        )

    @staticmethod
    def _alpha_summary(
        spec: EvaluationSpec,
        scores: Sequence[float],
        ic_series: list[dict[str, Any]],
        group_series: list[dict[str, Any]],
        stability_series: list[dict[str, Any]],
        membership_turnovers: list[float],
    ) -> dict[str, Any]:
        decay: dict[str, Any] = {}
        regimes: dict[str, dict[str, Any]] = {}
        for horizon in spec.horizons:
            rows = [item for item in group_series if item["horizon_bars"] == horizon]
            spreads = [item["long_short_spread"] for item in rows if item["long_short_spread"] is not None]
            top_before = [item["top_mean_return"] for item in rows if item["top_mean_return"] is not None]
            top_after = [item["estimated_top_return_after_cost"] for item in rows if item["estimated_top_return_after_cost"] is not None]
            decay[str(horizon)] = {
                "count": len(rows),
                "top_mean_return": statistics.fmean(top_before) if top_before else None,
                "top_mean_return_after_cost": statistics.fmean(top_after) if top_after else None,
                "long_short_spread": statistics.fmean(spreads) if spreads else None,
            }
            regimes[str(horizon)] = {}
            for regime in ("BULL", "BEAR", "SIDEWAYS"):
                regime_returns = [item["top_mean_return"] for item in rows if item["market_regime"] == regime and item["top_mean_return"] is not None]
                regimes[str(horizon)][regime] = {
                    "count": len(regime_returns),
                    "top_mean_return": statistics.fmean(regime_returns) if regime_returns else None,
                }
        stability = [item["rank_stability"] for item in stability_series]
        ic_summary, rank_ic_summary = _summarize_ic(spec, ic_series)
        diagnostics: list[dict[str, Any]] = []
        if not group_series:
            diagnostics.append({
                "code": "INSUFFICIENT_ALPHA_SAMPLE",
                "severity": "BLOCKED",
                "message": "No Alpha timestamp had enough instruments and future returns for evaluation.",
            })
        elif len(group_series) < 20:
            diagnostics.append({
                "code": "LOW_ALPHA_SAMPLE",
                "severity": "WARNING",
                "message": f"Only {len(group_series)} Alpha horizon observations were evaluated.",
            })
        if group_series and not any(
            item["count"] for item in (*ic_summary.values(), *rank_ic_summary.values())
        ):
            diagnostics.append({
                "code": "IC_UNAVAILABLE",
                "severity": "WARNING",
                "message": "Eligible Alpha timestamps exist, but cross-sectional IC is unavailable.",
            })
        # Sort once for every reported percentile. The previous implementation
        # sorted the complete score vector five times.
        ordered_scores = sorted(scores)
        return {
            "product_run_type": "ALPHA_RUN",
            "score_count": len(scores),
            "score_mean": statistics.fmean(scores) if scores else None,
            "score_std": statistics.pstdev(scores) if len(scores) > 1 else 0.0 if scores else None,
            "score_quantiles": {
                str(int(p * 100)): _percentile_sorted(ordered_scores, p)
                for p in (0.05, 0.25, 0.5, 0.75, 0.95)
            },
            "average_rank_stability": statistics.fmean(stability) if stability else None,
            "average_membership_turnover": statistics.fmean(membership_turnovers) if membership_turnovers else None,
            "ic": ic_summary,
            "rank_ic": rank_ic_summary,
            "holding_period_decay": decay,
            "regime_performance": regimes,
            "diagnostics": diagnostics,
        }
