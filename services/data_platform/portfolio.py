from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


PORTFOLIO_ENGINE_VERSION = "portfolio-engine.v2"
PORTFOLIO_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    material = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("signal requires as_of_time or available_time")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class PortfolioSpec:
    selection_method: str = "TOP_N"
    top_n: int = 2
    weighting_method: str = "EQUAL_WEIGHT"
    direction: str = "LONG_ONLY"
    rebalance_frequency: str = "DAILY"
    max_position_weight: float = 1.0
    minimum_score: float | None = None
    cash_buffer: float = 0.0
    universe_snapshot_id: str = ""
    engine_version: str = PORTFOLIO_ENGINE_VERSION
    code_hash: str = PORTFOLIO_CODE_HASH

    def __post_init__(self) -> None:
        if self.selection_method.upper() != "TOP_N":
            raise ValueError("PortfolioEngine v2 supports TOP_N selection")
        if self.weighting_method.upper() != "EQUAL_WEIGHT":
            raise ValueError("PortfolioEngine v2 supports EQUAL_WEIGHT")
        if self.direction.upper() != "LONG_ONLY":
            raise ValueError("PortfolioEngine v2 supports LONG_ONLY")
        if self.rebalance_frequency.upper() not in {"DAILY", "EVERY_SIGNAL"}:
            raise ValueError("rebalance_frequency must be DAILY or EVERY_SIGNAL")
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if not 0 <= self.cash_buffer < 1:
            raise ValueError("cash_buffer must be in [0, 1)")
        if self.minimum_score is not None and not math.isfinite(float(self.minimum_score)):
            raise ValueError("minimum_score must be finite when provided")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selection_method": self.selection_method.upper(),
            "top_n": self.top_n,
            "weighting_method": self.weighting_method.upper(),
            "direction": self.direction.upper(),
            "rebalance_frequency": self.rebalance_frequency.upper(),
            "max_position_weight": self.max_position_weight,
            "minimum_score": self.minimum_score,
            "cash_buffer": self.cash_buffer,
            "universe_snapshot_id": self.universe_snapshot_id,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _canonical_hash(self.to_dict())


class PortfolioEngine:
    """Convert Alpha scores/ranks into versioned target-weight decisions."""

    def build_targets(
        self,
        signals: Sequence[Mapping[str, Any]],
        spec: PortfolioSpec,
    ) -> list[Dict[str, Any]]:
        ordered = sorted(
            (dict(signal) for signal in signals),
            key=lambda item: _parse_time(item.get("available_time") or item.get("as_of_time")),
        )
        if spec.rebalance_frequency.upper() == "DAILY":
            daily: dict[str, dict[str, Any]] = {}
            for signal in ordered:
                signal_time = _parse_time(signal.get("available_time") or signal.get("as_of_time"))
                daily[signal_time.date().isoformat()] = signal
            ordered = [daily[key] for key in sorted(daily)]

        result: list[Dict[str, Any]] = []
        for signal in ordered:
            snapshot_id = str(signal.get("universe_snapshot_id") or "")
            if spec.universe_snapshot_id and snapshot_id != spec.universe_snapshot_id:
                raise ValueError("PortfolioSpec universe_snapshot_id does not match Alpha signal")
            raw_scores = signal.get("raw_scores") or signal.get("scores")
            raw_scores = raw_scores if isinstance(raw_scores, Mapping) else {}
            ranked = []
            for instrument_id, raw_score in raw_scores.items():
                score = _finite(raw_score)
                if score is None:
                    continue
                ranked.append((str(instrument_id), score))
            ranked.sort(key=lambda item: (-item[1], item[0]))
            eligible = [
                item
                for item in ranked
                if spec.minimum_score is None or item[1] >= spec.minimum_score
            ]
            selected = eligible[:spec.top_n]
            if selected:
                investable = 1.0 - spec.cash_buffer
                weight = min(investable / len(selected), spec.max_position_weight)
                weights = {instrument_id: weight for instrument_id, _ in selected}
            else:
                # A signal that selects nothing is an explicit all-cash target,
                # not an instruction to preserve the previous holdings.
                weights = {}
            result.append({
                "as_of_time": signal.get("as_of_time"),
                "available_time": signal.get("available_time") or signal.get("as_of_time"),
                "alpha_name": signal.get("alpha_name"),
                "alpha_version": signal.get("alpha_version"),
                "raw_scores": dict(ranked),
                "eligible_scores": dict(eligible),
                "ranks": dict(signal.get("ranks") or {}),
                "percentiles": dict(signal.get("percentiles") or {}),
                "weights": weights,
                "selected_instrument_ids": [instrument_id for instrument_id, _ in selected],
                "target_state": "INVESTED" if selected else "FLAT",
                "selection_reason": "TOP_N_SELECTED" if selected else "NO_ELIGIBLE_INSTRUMENT",
                "universe_snapshot_id": snapshot_id or spec.universe_snapshot_id,
                "portfolio_spec_hash": spec.spec_hash,
                "portfolio_engine_version": spec.engine_version,
            })
        return result
