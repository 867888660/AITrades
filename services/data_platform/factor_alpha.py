from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple
from datetime import datetime, timezone


FACTOR_ENGINE_VERSION = "factor-engine.v3"
ALPHA_ENGINE_VERSION = "alpha-engine.v2"
FACTOR_ALPHA_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SUPPORTED_FACTOR_OPERATORS = {
    "pct_change",
    "difference",
    "ratio",
    "rolling_mean",
    "rolling_std",
    "rolling_return_std",
    "ema",
    "ma_crossover",
}


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_hash(value: Mapping[str, Any]) -> str:
    material = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bar_start_time(row: Mapping[str, Any]) -> str:
    value = str(row.get("bar_start_time") or row.get("event_time") or row.get("open_time_utc") or "").strip()
    if not value:
        raise ValueError("bar requires bar_start_time or event_time")
    return value


def _bar_end_time(row: Mapping[str, Any]) -> str:
    return str(row.get("bar_end_time") or row.get("available_time") or _bar_start_time(row)).strip()


def _available_time(row: Mapping[str, Any]) -> str:
    return str(row.get("available_time") or _bar_end_time(row)).strip()


@dataclass(frozen=True)
class FactorSpec:
    name: str
    version: str
    operator: str
    input_field: str = "close"
    window: int = 1
    minimum_observations: int | None = None
    missing_policy: str = "STRICT"
    parameters: Dict[str, Any] = field(default_factory=dict)
    frequency: str = ""
    dimension: str = "TIME_SERIES"
    time_alignment_policy: str = "BAR_END_AVAILABLE_TIME"
    available_after: str = "BAR_CLOSE"
    allow_incomplete_bar: bool = False
    output_unit: str = "RATIO"
    output_direction: str = "NO_PREDEFINED_DIRECTION"
    engine_version: str = FACTOR_ENGINE_VERSION
    code_hash: str = FACTOR_ALPHA_CODE_HASH

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("factor name and version are required")
        if self.window < 1:
            raise ValueError("factor window must be positive")
        if self.operator not in SUPPORTED_FACTOR_OPERATORS:
            raise ValueError(f"unsupported factor operator: {self.operator}")
        if self.missing_policy.upper() not in {"STRICT", "SKIP"}:
            raise ValueError(f"unsupported factor missing policy: {self.missing_policy}")
        if self.dimension.upper() != "TIME_SERIES":
            raise ValueError("FactorEngine v2 supports TIME_SERIES Factor calculations")
        if self.available_after.upper() != "BAR_CLOSE":
            raise ValueError("FactorEngine v2 supports BAR_CLOSE availability")
        if self.minimum_observations is not None and int(self.minimum_observations) < 1:
            raise ValueError("minimum_observations must be positive")
        if (
            self.minimum_observations is not None
            and self.operator in {"rolling_mean", "rolling_std", "ema"}
            and int(self.minimum_observations) > self.window
        ):
            raise ValueError("minimum_observations cannot exceed window for rolling operators")
        if self.operator == "ma_crossover":
            raw_fast_window = self.parameters.get("fast_window")
            if isinstance(raw_fast_window, bool):
                raise ValueError("ma_crossover fast_window must be an integer")
            try:
                fast_window = int(raw_fast_window)
            except (TypeError, ValueError) as exc:
                raise ValueError("ma_crossover requires integer parameters.fast_window") from exc
            if fast_window < 1:
                raise ValueError("ma_crossover fast_window must be positive")
            if fast_window >= self.window:
                raise ValueError("ma_crossover fast_window must be smaller than slow window")

    @property
    def required_observations(self) -> int:
        if self.operator == "ma_crossover":
            requested = int(self.minimum_observations or 0)
            return max(self.window + 1, requested)
        if self.minimum_observations is not None:
            return max(1, int(self.minimum_observations))
        return self.window + 1 if self.operator in {"pct_change", "difference", "ratio", "rolling_return_std"} else self.window

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "frequency": self.frequency,
            "formula": {
                "operator": self.operator,
                "input": self.input_field,
                "window": self.window,
                "parameters": self.parameters,
            },
            "dimension": self.dimension,
            "minimum_observations": self.required_observations,
            "missing_policy": self.missing_policy.upper(),
            "time_alignment_policy": self.time_alignment_policy,
            "available_after": self.available_after.upper(),
            "allow_incomplete_bar": self.allow_incomplete_bar,
            "output_unit": self.output_unit,
            "output_direction": self.output_direction,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _canonical_hash(self.to_dict())


class FactorEngine:
    """Deterministic time-series Factor engine with explicit availability semantics."""

    def compute(
        self,
        spec: FactorSpec,
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> Dict[str, list[Dict[str, Any]]]:
        result: Dict[str, list[Dict[str, Any]]] = {}
        for instrument_id, raw_rows in bars_by_instrument.items():
            rows = sorted(raw_rows, key=_bar_start_time)
            values: list[float | None] = []
            output: list[Dict[str, Any]] = []
            seen_times: set[str] = set()
            previous_ema: float | None = None
            for index, row in enumerate(rows):
                start_time = _bar_start_time(row)
                if start_time in seen_times:
                    raise ValueError(f"duplicate bar timestamp for {instrument_id}: {start_time}")
                seen_times.add(start_time)
                bar_status = str(row.get("bar_status") or "COMPLETE").upper()
                if bar_status != "COMPLETE" and not spec.allow_incomplete_bar:
                    raise ValueError(f"incomplete bar is not allowed for {instrument_id}: {start_time}")
                end_time = _bar_end_time(row)
                available_time = _available_time(row)
                if _parse_time(end_time) < _parse_time(start_time):
                    raise ValueError(f"bar_end_time precedes bar_start_time for {instrument_id}: {start_time}")
                if _parse_time(available_time) < _parse_time(end_time):
                    raise ValueError(f"available_time precedes bar_end_time for {instrument_id}: {start_time}")
                current = _finite_float(row.get(spec.input_field))
                values.append(current)
                if spec.operator == "ema":
                    value = self._calculate_ema(spec, values, index, previous_ema)
                    previous_ema = value
                else:
                    value = self._calculate(spec, values, index)
                output.append({
                    "instrument_id": str(instrument_id),
                    "event_time": start_time,
                    "bar_start_time": start_time,
                    "bar_end_time": end_time,
                    "factor_as_of_time": available_time,
                    "available_time": available_time,
                    "factor_name": spec.name,
                    "factor_version": spec.version,
                    "value": value,
                    "quality_status": "PASS" if value is not None else "WARMUP",
                })
            result[str(instrument_id)] = output
        return result

    @staticmethod
    def _calculate(spec: FactorSpec, values: list[float | None], index: int) -> float | None:
        required = spec.required_observations
        if index + 1 < required:
            return None
        current = values[index]
        if current is None:
            return None
        window = spec.window
        if spec.operator == "pct_change":
            previous = values[index - window]
            return None if previous in (None, 0) else current / previous - 1.0
        if spec.operator == "difference":
            previous = values[index - window]
            return None if previous is None else current - previous
        if spec.operator == "ratio":
            previous = values[index - window]
            return None if previous in (None, 0) else current / previous
        if spec.operator == "rolling_return_std":
            price_window = values[index - window:index + 1]
            if any(value in (None, 0) for value in price_window):
                return None
            returns = [
                float(price_window[item]) / float(price_window[item - 1]) - 1.0
                for item in range(1, len(price_window))
            ]
            return statistics.pstdev(returns) if len(returns) > 1 else 0.0
        if spec.operator == "ma_crossover":
            fast_window = int(spec.parameters["fast_window"])
            previous_slow = values[index - window:index]
            current_slow = values[index - window + 1:index + 1]
            previous_fast = values[index - fast_window:index]
            current_fast = values[index - fast_window + 1:index + 1]
            crossover_windows = (
                previous_slow,
                current_slow,
                previous_fast,
                current_fast,
            )
            if any(any(value is None for value in item) for item in crossover_windows):
                return None
            previous_difference = (
                statistics.fmean(float(value) for value in previous_fast)
                - statistics.fmean(float(value) for value in previous_slow)
            )
            current_difference = (
                statistics.fmean(float(value) for value in current_fast)
                - statistics.fmean(float(value) for value in current_slow)
            )
            if previous_difference <= 0.0 < current_difference:
                return 1.0
            if previous_difference >= 0.0 > current_difference:
                return -1.0
            return 0.0
        window_values = values[index - window + 1:index + 1]
        if spec.missing_policy.upper() == "STRICT" and any(value is None for value in window_values):
            return None
        numeric = [float(value) for value in window_values if value is not None]
        if len(numeric) < required:
            return None
        if spec.operator == "rolling_mean":
            return statistics.fmean(numeric)
        if spec.operator == "rolling_std":
            return statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
        raise ValueError(f"unsupported factor operator: {spec.operator}")

    @staticmethod
    def _calculate_ema(
        spec: FactorSpec,
        values: list[float | None],
        index: int,
        previous_ema: float | None,
    ) -> float | None:
        if index + 1 < spec.required_observations:
            return None
        current = values[index]
        if current is None:
            return None
        alpha = 2.0 / (spec.window + 1.0)
        if previous_ema is not None:
            return alpha * current + (1.0 - alpha) * previous_ema
        seed_window = values[index - spec.window + 1:index + 1]
        if spec.missing_policy.upper() == "STRICT" and any(value is None for value in seed_window):
            return None
        numeric = [float(value) for value in seed_window if value is not None]
        if len(numeric) < spec.required_observations:
            return None
        return statistics.fmean(numeric)


@dataclass(frozen=True)
class AlphaComponent:
    factor_name: str
    weight: float
    transform: str = "CS_RANK"
    ascending: bool = True


@dataclass(frozen=True)
class AlphaSpec:
    name: str
    version: str
    components: Tuple[AlphaComponent, ...]
    minimum_coverage: float = 1.0
    universe_snapshot_id: str = ""
    minimum_cross_section_size: int = 1
    missing_policy: str = "EXCLUDE"
    rank_method: str = "AVERAGE"
    output_scale: str = "PERCENTILE"
    engine_version: str = ALPHA_ENGINE_VERSION
    code_hash: str = FACTOR_ALPHA_CODE_HASH

    def __post_init__(self) -> None:
        if not self.name or not self.version or not self.components:
            raise ValueError("alpha name, version, and components are required")
        if not 0 < self.minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be in (0, 1]")
        if self.minimum_cross_section_size < 1:
            raise ValueError("minimum_cross_section_size must be positive")
        if self.rank_method.upper() != "AVERAGE":
            raise ValueError("AlphaEngine v2 supports AVERAGE tie ranking")
        if self.output_scale.upper() != "PERCENTILE":
            raise ValueError("AlphaEngine v2 supports PERCENTILE output scale")
        if self.missing_policy.upper() != "EXCLUDE":
            raise ValueError("AlphaEngine v2 supports EXCLUDE missing policy")
        for component in self.components:
            if component.transform.upper() not in {"RANK", "CS_RANK", "RAW"}:
                raise ValueError(f"unsupported alpha transform: {component.transform}")
            if not math.isfinite(float(component.weight)):
                raise ValueError(f"alpha component weight must be finite: {component.factor_name}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "components": [
                {
                    "factor_name": item.factor_name,
                    "weight": item.weight,
                    "transform": "CS_RANK" if item.transform.upper() == "RANK" else item.transform.upper(),
                    "ascending": item.ascending,
                }
                for item in self.components
            ],
            "universe_snapshot_id": self.universe_snapshot_id,
            "minimum_coverage": self.minimum_coverage,
            "minimum_cross_section_size": self.minimum_cross_section_size,
            "missing_policy": self.missing_policy.upper(),
            "rank_method": self.rank_method,
            "output_scale": self.output_scale,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _canonical_hash(self.to_dict())


class AlphaEngine:
    """Cross-sectional Alpha engine; raw score and rank remain separate."""

    def build_signals(
        self,
        spec: AlphaSpec,
        factor_values: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
        *,
        universe_snapshot: Any | None = None,
    ) -> list[Dict[str, Any]]:
        snapshot_id = str(getattr(universe_snapshot, "universe_snapshot_id", "") or "")
        if spec.universe_snapshot_id and snapshot_id != spec.universe_snapshot_id:
            raise ValueError("AlphaSpec universe_snapshot_id does not match the supplied snapshot")
        universe_members = set(getattr(universe_snapshot, "actual_instrument_ids", ()) or ())
        factor_maps: Dict[str, Dict[str, Dict[str, float]]] = {}
        available_maps: Dict[str, Dict[str, str]] = {}
        all_times: set[str] = set()
        for component in spec.components:
            if component.factor_name not in factor_values:
                raise ValueError(f"missing Factor output: {component.factor_name}")
            values_by_instrument = factor_values[component.factor_name]
            current: Dict[str, Dict[str, float]] = {}
            current_available: Dict[str, str] = {}
            for instrument_id, rows in values_by_instrument.items():
                if universe_members and instrument_id not in universe_members:
                    continue
                for row in rows:
                    value = _finite_float(row.get("value"))
                    as_of_time = str(
                        row.get("factor_as_of_time") or row.get("available_time") or row.get("event_time") or ""
                    ).strip()
                    available_time = str(row.get("available_time") or as_of_time).strip()
                    if value is not None and as_of_time:
                        if instrument_id in current.setdefault(as_of_time, {}):
                            raise ValueError(f"duplicate Factor value for {component.factor_name} {instrument_id} {as_of_time}")
                        current[as_of_time][str(instrument_id)] = value
                        previous_available = current_available.get(as_of_time)
                        if previous_available is None or _parse_time(available_time) > _parse_time(previous_available):
                            current_available[as_of_time] = available_time
                        all_times.add(as_of_time)
            factor_maps[component.factor_name] = current
            available_maps[component.factor_name] = current_available

        signals: list[Dict[str, Any]] = []
        for as_of_time in sorted(all_times, key=_parse_time):
            component_values: list[tuple[AlphaComponent, Dict[str, float]]] = []
            for component in spec.components:
                values = factor_maps[component.factor_name].get(as_of_time, {})
                if universe_members:
                    values = {item: value for item, value in values.items() if item in universe_members}
                component_values.append((component, values))
            if not component_values:
                continue
            instruments = set(component_values[0][1])
            for _, values in component_values[1:]:
                instruments &= set(values)
            denominator = len(universe_members) if universe_members else max((len(values) for _, values in component_values), default=0)
            coverage = len(instruments) / max(1, denominator)
            if len(instruments) < spec.minimum_cross_section_size or coverage < spec.minimum_coverage:
                continue

            raw_scores = {instrument_id: 0.0 for instrument_id in sorted(instruments)}
            for component, values in component_values:
                scoped = {instrument_id: values[instrument_id] for instrument_id in instruments}
                transformed = self._transform(scoped, component.transform, ascending=component.ascending)
                for instrument_id in raw_scores:
                    raw_scores[instrument_id] += float(component.weight) * transformed[instrument_id]
            ranks, percentiles = self._rank_scores(raw_scores)
            available_candidates = [
                available_maps[component.factor_name].get(as_of_time, as_of_time)
                for component in spec.components
            ]
            available_time = max(available_candidates, key=_parse_time)
            signals.append({
                "as_of_time": as_of_time,
                "available_time": available_time,
                "alpha_name": spec.name,
                "alpha_version": spec.version,
                "raw_scores": raw_scores,
                "scores": raw_scores,
                "ranks": ranks,
                "percentiles": percentiles,
                "coverage": coverage,
                "quality_status": "PASS",
                "universe_snapshot_id": snapshot_id or spec.universe_snapshot_id,
            })
        return signals

    @staticmethod
    def _transform(values: Mapping[str, float], transform: str, *, ascending: bool = True) -> Dict[str, float]:
        if transform.upper() == "RAW":
            return dict(values)
        ordered = sorted(values.items(), key=lambda item: (item[1], item[0]), reverse=not ascending)
        count = len(ordered)
        if count <= 1:
            return {instrument_id: 1.0 for instrument_id in values}
        result: Dict[str, float] = {}
        start = 0
        while start < count:
            end = start + 1
            while end < count and ordered[end][1] == ordered[start][1]:
                end += 1
            average_rank = ((start + 1) + end) / 2.0
            percentile = average_rank / count
            for index in range(start, end):
                result[ordered[index][0]] = percentile
            start = end
        return result

    @staticmethod
    def _rank_scores(scores: Mapping[str, float]) -> tuple[Dict[str, float], Dict[str, float]]:
        ordered = sorted(scores, key=lambda item: (-scores[item], item))
        count = len(ordered)
        ranks: Dict[str, float] = {}
        start = 0
        while start < count:
            end = start + 1
            while end < count and scores[ordered[end]] == scores[ordered[start]]:
                end += 1
            average_rank = ((start + 1) + end) / 2.0
            for index in range(start, end):
                ranks[ordered[index]] = average_rank
            start = end
        percentiles = {
            instrument_id: (count - rank + 1) / count
            for instrument_id, rank in ranks.items()
        }
        return ranks, percentiles

    @staticmethod
    def top_n_equal_weight(
        signals: Sequence[Mapping[str, Any]],
        *,
        top_n: int,
        max_position_weight: float = 1.0,
    ) -> list[Dict[str, Any]]:
        if top_n < 1:
            raise ValueError("top_n must be positive")
        max_position_weight = float(max_position_weight)
        if max_position_weight <= 0:
            raise ValueError("max_position_weight must be positive")
        result = []
        for signal in signals:
            scores = signal.get("raw_scores") or signal.get("scores")
            scores = scores if isinstance(scores, Mapping) else {}
            ranked = sorted(
                ((str(instrument_id), _finite_float(score) or 0.0) for instrument_id, score in scores.items()),
                key=lambda item: (-item[1], item[0]),
            )
            selected = ranked[:top_n]
            if not selected:
                continue
            base_weight = min(1.0 / len(selected), max_position_weight)
            result.append({
                **dict(signal),
                "scores": dict(ranked),
                "weights": {instrument_id: base_weight for instrument_id, _ in selected},
                "selected_instrument_ids": [instrument_id for instrument_id, _ in selected],
            })
        return result
