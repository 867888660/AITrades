from __future__ import annotations

import hashlib
import json
import math
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .backtest_contract import BacktestCapabilities, BacktestExecutionSpec, audit_execution_spec
from .portfolio import PortfolioSpec


RESEARCH_BACKTEST_ENGINE_VERSION = "research-backtest.v2"
RESEARCH_BACKTEST_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


RESEARCH_BACKTEST_CAPABILITIES = BacktestCapabilities(
    provider="research_backtest_v2",
    provider_version="2",
    asset_classes=("crypto_spot",),
    supports_multi_leg=True,
    supports_target_position=True,
    supports_target_weight=True,
    supports_cross_sectional_alpha=True,
    supported_signal_generation=("BAR_CLOSE",),
    supported_order_submission=("NEXT_BAR_OPEN",),
    supported_fill_price_rules=("NEXT_OPEN_PLUS_SLIPPAGE",),
    supported_fee_models=("FIXED_BPS",),
    supported_slippage_models=("FIXED_BPS",),
    supports_dataset_manifest_pin=True,
    supports_mixed_source=False,
    supports_short=False,
    supports_leverage=False,
    supported_missing_price_policies=("FAIL_RUN",),
    supports_fractional_quantity=True,
    supports_exchange_rounding=False,
    notes=(
        "Deterministic target-weight simulator for strictly aligned OHLC bars.",
        "Signals execute at the first bar open strictly after signal available_time.",
        "Right-censored signals after the final executable open are skipped and counted.",
        "Missing execution prices fail the run; no silent forward fill is used.",
        "The provider is separate from the legacy StrategyCode replay.",
    ),
)


def _finite_float(value: Any, default: float | None = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        if default is None:
            raise ValueError(f"expected a finite number, got {value!r}")
        return float(default)
    if not math.isfinite(number):
        if default is None:
            raise ValueError(f"expected a finite number, got {value!r}")
        return float(default)
    return number


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    material = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_time(row: Mapping[str, Any]) -> str:
    for key in ("event_time", "bar_start_time", "open_time_utc", "ts_utc", "ts"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    raise ValueError("bar requires event_time, bar_start_time, open_time_utc, or ts_utc")


def _bar_value(row: Mapping[str, Any], key: str) -> float:
    aliases = {
        "open": ("open", "open_price"),
        "high": ("high",),
        "low": ("low",),
        "close": ("close", "close_price"),
        "volume": ("volume", "quote_volume"),
    }
    for alias in aliases[key]:
        if alias in row:
            return _finite_float(row.get(alias), None)
    raise ValueError(f"bar is missing {key}")


def _normalize_bars(
    bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, dict[str, dict[str, float]]]]:
    if not bars_by_instrument:
        raise ValueError("bars_by_instrument cannot be empty")
    instruments = tuple(sorted(str(item).strip() for item in bars_by_instrument if str(item).strip()))
    if not instruments:
        raise ValueError("bars_by_instrument must contain instrument IDs")
    normalized: dict[str, dict[str, dict[str, float]]] = {}
    expected_times: tuple[str, ...] | None = None
    for instrument_id in instruments:
        rows = bars_by_instrument[instrument_id]
        by_time: dict[str, dict[str, float]] = {}
        for row in rows:
            event_time = _event_time(row)
            if event_time in by_time:
                raise ValueError(f"duplicate bar timestamp for {instrument_id}: {event_time}")
            open_price = _bar_value(row, "open")
            high = _bar_value(row, "high")
            low = _bar_value(row, "low")
            close_price = _bar_value(row, "close")
            volume = _bar_value(row, "volume")
            if min(open_price, high, low, close_price) <= 0:
                raise ValueError(f"bar prices must be positive for {instrument_id}: {event_time}")
            if high < low or not low <= open_price <= high or not low <= close_price <= high:
                raise ValueError(f"invalid OHLC range for {instrument_id}: {event_time}")
            if volume < 0:
                raise ValueError(f"bar volume cannot be negative for {instrument_id}: {event_time}")
            by_time[event_time] = {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": volume,
            }
        times = tuple(sorted(by_time))
        if len(times) < 2:
            raise ValueError(f"at least two bars are required for {instrument_id}")
        if expected_times is None:
            expected_times = times
        elif times != expected_times:
            missing = sorted(set(expected_times) - set(times))[:5]
            extra = sorted(set(times) - set(expected_times))[:5]
            raise ValueError(
                f"bar timelines are not strictly aligned for {instrument_id}; missing={missing}, extra={extra}"
            )
        normalized[instrument_id] = by_time
    return instruments, expected_times or (), normalized


def _normalize_signal(signal: Mapping[str, Any], instruments: Iterable[str]) -> tuple[str, str, bool, dict[str, float]]:
    as_of = str(signal.get("as_of_time") or signal.get("as_of") or signal.get("event_time") or "").strip()
    has_explicit_available_time = bool(str(signal.get("available_time") or "").strip())
    available_time = str(signal.get("available_time") or as_of).strip()
    if not as_of or not available_time:
        raise ValueError("alpha signal requires as_of_time and available_time")
    _parse_time(as_of)
    _parse_time(available_time)
    if _parse_time(available_time) < _parse_time(as_of):
        raise ValueError("alpha signal available_time cannot precede as_of_time")
    raw_weights = signal.get("weights")
    if not isinstance(raw_weights, Mapping):
        raise ValueError("alpha signal requires a weights mapping")
    weights = {instrument_id: 0.0 for instrument_id in instruments}
    for instrument_id, value in raw_weights.items():
        instrument_id = str(instrument_id).strip()
        if instrument_id not in weights:
            raise ValueError(f"alpha signal references unknown instrument: {instrument_id}")
        weight = _finite_float(value, None)
        if weight < 0:
            raise ValueError("short weights are not supported by research_backtest_v2")
        if weight > 1 + 1e-9:
            raise ValueError("an individual target weight cannot exceed 100%")
        weights[instrument_id] = weight
    if sum(weights.values()) > 1.0 + 1e-9:
        raise ValueError("target weights cannot exceed 100% gross exposure")
    return as_of, available_time, has_explicit_available_time, weights


@dataclass(frozen=True)
class ResearchBacktestResult:
    execution_spec: Dict[str, Any]
    metrics: Dict[str, Any]
    equity_curve: Tuple[Dict[str, Any], ...]
    orders: Tuple[Dict[str, Any], ...]
    drawdown_curve: Tuple[Dict[str, Any], ...] = ()
    rebalance_events: Tuple[Dict[str, Any], ...] = ()
    dataset_manifest_ids: Tuple[str, ...] = ()
    universe_snapshot_ids: Tuple[str, ...] = ()
    factor_artifact_ids: Tuple[str, ...] = ()
    alpha_artifact_ids: Tuple[str, ...] = ()
    input_artifact_ids: Tuple[str, ...] = ()
    input_bundle_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_spec": dict(self.execution_spec),
            "metrics": dict(self.metrics),
            "equity_curve": [dict(item) for item in self.equity_curve],
            "orders": [dict(item) for item in self.orders],
            "drawdown_curve": [dict(item) for item in self.drawdown_curve],
            "rebalance_events": [dict(item) for item in self.rebalance_events],
            "dataset_manifest_ids": list(self.dataset_manifest_ids),
            "universe_snapshot_ids": list(self.universe_snapshot_ids),
            "factor_artifact_ids": list(self.factor_artifact_ids),
            "alpha_artifact_ids": list(self.alpha_artifact_ids),
            "input_artifact_ids": list(self.input_artifact_ids),
            "input_bundle_id": self.input_bundle_id,
        }


class ResearchBacktestProvider:
    """Deterministic, auditable, long-only target-weight simulator."""

    def capabilities(self) -> Dict[str, Any]:
        return RESEARCH_BACKTEST_CAPABILITIES.to_dict()

    def validate(self, execution_spec: BacktestExecutionSpec | Mapping[str, Any] | None = None) -> Dict[str, Any]:
        spec = execution_spec if isinstance(execution_spec, BacktestExecutionSpec) else BacktestExecutionSpec.from_payload(execution_spec)
        result = audit_execution_spec(spec, RESEARCH_BACKTEST_CAPABILITIES)
        issues = list(result["issues"])
        if spec.fee_bps < 0 or spec.slippage_bps < 0:
            issues.append({"code": "NEGATIVE_COST", "field": "fee_bps/slippage_bps", "requested": "negative", "supported": ">= 0"})
        if spec.target_equity_reference != "EXECUTION_OPEN_PRE_TRADE":
            issues.append({"code": "TARGET_EQUITY_REFERENCE_UNSUPPORTED", "field": "target_equity_reference", "requested": spec.target_equity_reference, "supported": "EXECUTION_OPEN_PRE_TRADE"})
        if not spec.sell_before_buy:
            issues.append({"code": "BUY_SELL_ORDER_UNSUPPORTED", "field": "sell_before_buy", "requested": "false", "supported": "true"})
        result["issues"] = issues
        result["ok"] = not issues
        result["status"] = "READY" if not issues else "BLOCKED"
        return result

    def simulate(
        self,
        *,
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        alpha_signals: Sequence[Mapping[str, Any]],
        initial_cash: float = 10_000.0,
        fee_bps: float | None = None,
        slippage_bps: float | None = None,
        execution_spec: BacktestExecutionSpec | Mapping[str, Any] | None = None,
        portfolio_spec: PortfolioSpec | Mapping[str, Any] | None = None,
        dataset_manifest_ids: Sequence[str] = (),
        universe_snapshot_ids: Sequence[str] = (),
        factor_artifact_ids: Sequence[str] = (),
        alpha_artifact_ids: Sequence[str] = (),
        input_artifact_ids: Sequence[str] = (),
        input_bundle_id: str = "",
    ) -> ResearchBacktestResult:
        spec = execution_spec if isinstance(execution_spec, BacktestExecutionSpec) else BacktestExecutionSpec.from_payload(execution_spec)
        validation = self.validate(spec)
        if not validation["ok"]:
            raise ValueError(f"unsupported research backtest execution spec: {validation['issues']}")
        initial_cash = _finite_float(initial_cash, None)
        fee_bps = spec.fee_bps if fee_bps is None else _finite_float(fee_bps, None)
        slippage_bps = spec.slippage_bps if slippage_bps is None else _finite_float(slippage_bps, None)
        if initial_cash <= 0 or fee_bps < 0 or slippage_bps < 0:
            raise ValueError("initial_cash must be positive and costs cannot be negative")

        if portfolio_spec is None:
            portfolio_payload: Dict[str, Any] = {}
            portfolio_spec_hash = ""
            cash_buffer = 0.0
        elif isinstance(portfolio_spec, PortfolioSpec):
            portfolio_payload = portfolio_spec.to_dict()
            portfolio_spec_hash = portfolio_spec.spec_hash
            cash_buffer = portfolio_spec.cash_buffer
        else:
            portfolio_payload = dict(portfolio_spec)
            portfolio_spec_hash = _canonical_hash(portfolio_payload)
            cash_buffer = _finite_float(portfolio_payload.get("cash_buffer", 0.0), None)
        if not 0 <= cash_buffer < 1:
            raise ValueError("portfolio cash_buffer must be in [0, 1)")

        instruments, common_times, normalized = _normalize_bars(bars_by_instrument)
        parsed_times = [_parse_time(item) for item in common_times]
        scheduled: dict[int, tuple[str, str, dict[str, float], Mapping[str, Any]]] = {}
        skipped_signal_count = 0
        for signal in alpha_signals:
            as_of, available_time, explicit_available_time, weights = _normalize_signal(signal, instruments)
            # Canonical research signals expose their actual availability. If
            # Bar Close and the next Bar Open share the same timestamp, that
            # open is executable. Legacy signals without available_time are
            # interpreted as current-bar observations and move strictly ahead.
            execution_index = (
                bisect_left(parsed_times, _parse_time(available_time))
                if explicit_available_time
                else bisect_right(parsed_times, _parse_time(available_time))
            )
            if execution_index >= len(common_times):
                skipped_signal_count += 1
                continue
            if execution_index in scheduled:
                raise ValueError(f"multiple alpha signals schedule the same execution bar: {common_times[execution_index]}")
            scheduled[execution_index] = (as_of, available_time, weights, signal)

        cash = initial_cash
        positions = {instrument_id: 0.0 for instrument_id in instruments}
        equity_curve: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        rebalance_events: list[dict[str, Any]] = []
        total_fees = 0.0
        total_slippage_cost = 0.0
        total_turnover_notional = 0.0
        fee_rate = fee_bps / 10_000.0
        slippage_rate = slippage_bps / 10_000.0

        for index, event_time in enumerate(common_times):
            opens = {instrument_id: normalized[instrument_id][event_time]["open"] for instrument_id in instruments}
            closes = {instrument_id: normalized[instrument_id][event_time]["close"] for instrument_id in instruments}
            if index in scheduled:
                signal_time, signal_available_time, weights, raw_signal = scheduled[index]
                equity_at_open = cash + sum(positions[item] * opens[item] for item in instruments)
                if equity_at_open <= 0:
                    raise ValueError(f"equity is not positive at execution time {event_time}")
                target_quantities = {
                    item: (equity_at_open * weights[item]) / opens[item]
                    for item in instruments
                }

                sell_orders: list[tuple[str, float]] = []
                buy_orders: list[tuple[str, float]] = []
                for instrument_id in instruments:
                    delta_quantity = target_quantities[instrument_id] - positions[instrument_id]
                    if delta_quantity < -1e-12:
                        sell_orders.append((instrument_id, min(positions[instrument_id], -delta_quantity)))
                    elif delta_quantity > 1e-12:
                        buy_orders.append((instrument_id, delta_quantity))

                event_order_start = len(orders)
                for instrument_id, quantity in sell_orders:
                    reference_price = opens[instrument_id]
                    fill_price = reference_price * (1.0 - slippage_rate)
                    trade_value = quantity * fill_price
                    fee = trade_value * fee_rate
                    slippage_cost = quantity * (reference_price - fill_price)
                    positions[instrument_id] -= quantity
                    cash += trade_value - fee
                    total_fees += fee
                    total_slippage_cost += slippage_cost
                    total_turnover_notional += trade_value
                    orders.append(self._order_row(
                        order_number=len(orders) + 1,
                        event_time=event_time,
                        signal_time=signal_time,
                        signal_available_time=signal_available_time,
                        instrument_id=instrument_id,
                        side="SELL",
                        quantity=quantity,
                        reference_price=reference_price,
                        fill_price=fill_price,
                        fee=fee,
                        slippage_cost=slippage_cost,
                        target_weight=weights[instrument_id],
                    ))

                minimum_cash = equity_at_open * cash_buffer
                available_cash = max(0.0, cash - minimum_cash)
                required_cash = sum(
                    quantity * opens[instrument_id] * (1.0 + slippage_rate) * (1.0 + fee_rate)
                    for instrument_id, quantity in buy_orders
                )
                buy_scale = min(1.0, available_cash / required_cash) if required_cash > 0 else 1.0
                for instrument_id, desired_quantity in buy_orders:
                    quantity = desired_quantity * buy_scale
                    if quantity <= 1e-12:
                        continue
                    reference_price = opens[instrument_id]
                    fill_price = reference_price * (1.0 + slippage_rate)
                    trade_value = quantity * fill_price
                    fee = trade_value * fee_rate
                    slippage_cost = quantity * (fill_price - reference_price)
                    total_cost = trade_value + fee
                    if total_cost > cash + 1e-8:
                        raise RuntimeError("proportional cash scaling failed")
                    positions[instrument_id] += quantity
                    cash -= total_cost
                    total_fees += fee
                    total_slippage_cost += slippage_cost
                    total_turnover_notional += trade_value
                    orders.append(self._order_row(
                        order_number=len(orders) + 1,
                        event_time=event_time,
                        signal_time=signal_time,
                        signal_available_time=signal_available_time,
                        instrument_id=instrument_id,
                        side="BUY",
                        quantity=quantity,
                        reference_price=reference_price,
                        fill_price=fill_price,
                        fee=fee,
                        slippage_cost=slippage_cost,
                        target_weight=weights[instrument_id],
                    ))
                if cash < -1e-8:
                    raise RuntimeError(f"cash constraint violated at {event_time}: {cash}")
                cash = max(0.0, cash)
                rebalance_events.append({
                    "rebalance_id": f"rebalance_{len(rebalance_events) + 1:06d}",
                    "signal_time": signal_time,
                    "signal_available_time": signal_available_time,
                    "execution_time": event_time,
                    "target_weights": dict(weights),
                    "selected_instrument_ids": list(raw_signal.get("selected_instrument_ids") or [item for item, weight in weights.items() if weight > 0]),
                    "target_state": str(raw_signal.get("target_state") or ("INVESTED" if any(weight > 0 for weight in weights.values()) else "FLAT")),
                    "selection_reason": str(raw_signal.get("selection_reason") or ""),
                    "order_count": len(orders) - event_order_start,
                    "buy_scale": buy_scale,
                    "equity_at_open": equity_at_open,
                    "universe_snapshot_id": str(raw_signal.get("universe_snapshot_id") or ""),
                })

            position_values = {item: positions[item] * closes[item] for item in instruments}
            equity = cash + sum(position_values.values())
            exposure = sum(position_values.values()) / equity if equity > 0 else 0.0
            position_weights = {
                item: position_values[item] / equity if equity > 0 else 0.0
                for item in instruments
            }
            equity_curve.append({
                "event_time": event_time,
                "equity": equity,
                "cash": cash,
                "cash_ratio": cash / equity if equity > 0 else 0.0,
                "gross_exposure": exposure,
                "positions": {item: positions[item] for item in instruments},
                "position_values": position_values,
                "position_weights": position_weights,
            })

        drawdown_curve = self._drawdown_curve(equity_curve, initial_cash)
        metrics = self._metrics(
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            initial_cash=initial_cash,
            common_times=common_times,
            orders=orders,
            rebalance_events=rebalance_events,
            total_fees=total_fees,
            total_slippage_cost=total_slippage_cost,
            total_turnover_notional=total_turnover_notional,
            instrument_count=len(instruments),
        )
        execution_payload = spec.to_dict()
        execution_payload["fee_bps"] = fee_bps
        execution_payload["slippage_bps"] = slippage_bps
        execution_spec_hash = _canonical_hash(execution_payload)
        dataset_manifest_ids = tuple(str(item) for item in dataset_manifest_ids)
        universe_snapshot_ids = tuple(str(item) for item in universe_snapshot_ids)
        factor_artifact_ids = tuple(str(item) for item in factor_artifact_ids)
        alpha_artifact_ids = tuple(str(item) for item in alpha_artifact_ids)
        input_artifact_ids = tuple(str(item) for item in input_artifact_ids)
        input_bundle_id = str(input_bundle_id or "").strip()
        metrics.update({
            "execution_engine": RESEARCH_BACKTEST_CAPABILITIES.provider,
            "engine_version": RESEARCH_BACKTEST_ENGINE_VERSION,
            "code_hash": RESEARCH_BACKTEST_CODE_HASH,
            "random_seed": spec.random_seed,
            "dataset_manifest_ids": list(dataset_manifest_ids),
            "universe_snapshot_ids": list(universe_snapshot_ids),
            "factor_artifact_ids": list(factor_artifact_ids),
            "alpha_artifact_ids": list(alpha_artifact_ids),
            "input_artifact_ids": list(input_artifact_ids),
            "input_bundle_id": input_bundle_id,
            "portfolio_spec": portfolio_payload,
            "portfolio_spec_hash": portfolio_spec_hash,
            "execution_spec_hash": execution_spec_hash,
            "started_at": common_times[0],
            "completed_at": common_times[-1],
            "skipped_signal_count": skipped_signal_count,
        })
        return ResearchBacktestResult(
            execution_spec=execution_payload,
            metrics=metrics,
            equity_curve=tuple(equity_curve),
            orders=tuple(orders),
            drawdown_curve=tuple(drawdown_curve),
            rebalance_events=tuple(rebalance_events),
            dataset_manifest_ids=dataset_manifest_ids,
            universe_snapshot_ids=universe_snapshot_ids,
            factor_artifact_ids=factor_artifact_ids,
            alpha_artifact_ids=alpha_artifact_ids,
            input_artifact_ids=input_artifact_ids,
            input_bundle_id=input_bundle_id,
        )

    @staticmethod
    def _order_row(**payload: Any) -> dict[str, Any]:
        order_number = int(payload.pop("order_number"))
        material = {
            "order_number": order_number,
            **payload,
        }
        order_hash = _canonical_hash(material)
        return {
            "order_id": f"order_{order_hash[:20]}",
            **material,
            "price": material["fill_price"],
            "gross_value": material["quantity"] * material["fill_price"],
            "reason": "target_weight_rebalance",
        }

    @staticmethod
    def _drawdown_curve(
        equity_curve: list[dict[str, Any]],
        initial_cash: float,
    ) -> list[dict[str, Any]]:
        peak = float(initial_cash)
        peak_time: str | None = None
        underwater_bars = 0
        result: list[dict[str, Any]] = []
        for point in equity_curve:
            event_time = str(point.get("event_time") or "")
            equity = _finite_float(point.get("equity"), initial_cash)
            if equity >= peak:
                peak = equity
                peak_time = event_time
                underwater_bars = 0
            else:
                underwater_bars += 1
            result.append({
                "event_time": event_time,
                "equity": equity,
                "peak_equity": peak,
                "peak_time": peak_time,
                "drawdown": equity / peak - 1.0 if peak > 0 else 0.0,
                "underwater_bars": underwater_bars,
            })
        return result

    @staticmethod
    def _metrics(
        *,
        equity_curve: list[dict[str, Any]],
        drawdown_curve: list[dict[str, Any]],
        initial_cash: float,
        common_times: tuple[str, ...],
        orders: list[dict[str, Any]],
        rebalance_events: list[dict[str, Any]],
        total_fees: float,
        total_slippage_cost: float,
        total_turnover_notional: float,
        instrument_count: int,
    ) -> dict[str, Any]:
        returns: list[float] = []
        previous = initial_cash
        for point in equity_curve:
            equity = _finite_float(point["equity"], initial_cash)
            if previous > 0:
                returns.append(equity / previous - 1.0)
            previous = equity
        max_drawdown_row = min(
            drawdown_curve,
            key=lambda item: float(item.get("drawdown") or 0.0),
            default={"drawdown": 0.0, "event_time": None, "peak_time": None, "underwater_bars": 0},
        )
        max_drawdown = float(max_drawdown_row.get("drawdown") or 0.0)
        final_equity = _finite_float(equity_curve[-1]["equity"], initial_cash)
        year_seconds = 365.25 * 24 * 3600
        elapsed_seconds = (
            (_parse_time(common_times[-1]) - _parse_time(common_times[0])).total_seconds()
            if len(common_times) > 1 else 0.0
        )
        # Infer the actual observation frequency from the full calendar span.
        # Median bar spacing incorrectly annualizes weekday-only equity data at
        # 365 observations per year because most adjacent sessions are one day
        # apart and weekends are ignored.
        periods_per_year = (
            (len(common_times) - 1) * year_seconds / elapsed_seconds
            if elapsed_seconds > 0 else 0.0
        )
        volatility = statistics.pstdev(returns) * math.sqrt(periods_per_year) if len(returns) > 1 and periods_per_year else 0.0
        annualized_return = (
            (final_equity / initial_cash) ** (year_seconds / elapsed_seconds) - 1.0
            if final_equity > 0 and elapsed_seconds > 0 else 0.0
        )
        sharpe = (
            statistics.fmean(returns) * periods_per_year / volatility
            if returns and volatility > 0 and periods_per_year else 0.0
        )
        return {
            "initial_cash": initial_cash,
            "final_equity": final_equity,
            "total_return": final_equity / initial_cash - 1.0,
            "annualized_return": annualized_return,
            "observations_per_year": periods_per_year,
            "volatility": volatility,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "max_drawdown_at": max_drawdown_row.get("event_time"),
            "max_drawdown_peak_at": max_drawdown_row.get("peak_time"),
            "max_underwater_bars": max(
                (int(item.get("underwater_bars") or 0) for item in drawdown_curve),
                default=0,
            ),
            "fees": total_fees,
            "slippage_cost": total_slippage_cost,
            "turnover": total_turnover_notional / initial_cash,
            "trade_count": len(orders),
            "rebalance_count": len(rebalance_events),
            "invested_rebalance_count": sum(
                1 for item in rebalance_events if item.get("target_state") == "INVESTED"
            ),
            "flat_rebalance_count": sum(
                1 for item in rebalance_events if item.get("target_state") == "FLAT"
            ),
            "bar_count": len(equity_curve),
            "instrument_count": instrument_count,
            "average_exposure": statistics.fmean(float(item["gross_exposure"]) for item in equity_curve),
            "average_cash_ratio": statistics.fmean(float(item["cash_ratio"]) for item in equity_curve),
            "fractional_quantity": True,
            "exchange_rounding": False,
        }

    def simulate_manifests(
        self,
        *,
        manifests: Sequence[Any],
        alpha_signals: Sequence[Mapping[str, Any]],
        initial_cash: float = 10_000.0,
        fee_bps: float | None = None,
        slippage_bps: float | None = None,
        execution_spec: BacktestExecutionSpec | Mapping[str, Any] | None = None,
        portfolio_spec: PortfolioSpec | Mapping[str, Any] | None = None,
        universe_snapshot_ids: Sequence[str] = (),
        factor_artifact_ids: Sequence[str] = (),
        alpha_artifact_ids: Sequence[str] = (),
        input_artifact_ids: Sequence[str] = (),
    ) -> ResearchBacktestResult:
        """Run only against explicit READY, physically verified manifests."""
        if not manifests:
            raise ValueError("at least one frozen dataset manifest is required")
        bars_by_instrument: dict[str, list[dict[str, Any]]] = {}
        manifest_ids: list[str] = []
        for frozen in manifests:
            manifest_id = str(getattr(frozen, "manifest_id", "") or "").strip()
            if not manifest_id:
                raise ValueError("simulate_manifests requires FrozenManifestData objects")
            if manifest_id in manifest_ids:
                raise ValueError(f"duplicate dataset manifest: {manifest_id}")
            frozen.verify()
            manifest_ids.append(manifest_id)
            for instrument_id, rows in frozen.read_bars_by_instrument().items():
                if instrument_id in bars_by_instrument:
                    raise ValueError(f"duplicate instrument across dataset manifests: {instrument_id}")
                bars_by_instrument[instrument_id] = rows
        return self.simulate(
            bars_by_instrument=bars_by_instrument,
            alpha_signals=alpha_signals,
            initial_cash=initial_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            execution_spec=execution_spec,
            portfolio_spec=portfolio_spec,
            dataset_manifest_ids=manifest_ids,
            universe_snapshot_ids=universe_snapshot_ids,
            factor_artifact_ids=factor_artifact_ids,
            alpha_artifact_ids=alpha_artifact_ids,
            input_artifact_ids=input_artifact_ids,
        )
