from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Tuple


def _payload_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


@dataclass(frozen=True)
class BacktestExecutionSpec:
    """Execution semantics that make a research result comparable."""

    signal_generation: str = "BAR_CLOSE"
    order_submission: str = "NEXT_BAR_OPEN"
    fill_price_rule: str = "NEXT_OPEN_PLUS_SLIPPAGE"
    cash_constraint: str = "STRICT"
    fee_model: str = "FIXED_BPS"
    slippage_model: str = "FIXED_BPS"
    portfolio_input: str = "TARGET_WEIGHT"
    allow_short: bool = False
    allow_leverage: bool = False
    fee_bps: float = 2.0
    slippage_bps: float = 10.0
    missing_price_policy: str = "FAIL_RUN"
    quantity_rounding: str = "FRACTIONAL"
    minimum_notional_policy: str = "IGNORE"
    target_equity_reference: str = "EXECUTION_OPEN_PRE_TRADE"
    sell_before_buy: bool = True
    random_seed: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.fee_bps)) or float(self.fee_bps) < 0:
            raise ValueError("fee_bps must be a finite non-negative number")
        if not math.isfinite(float(self.slippage_bps)) or float(self.slippage_bps) < 0:
            raise ValueError("slippage_bps must be a finite non-negative number")
        if self.missing_price_policy.upper() not in {"FAIL_RUN", "SKIP_ORDER", "DEFER_TO_NEXT_BAR"}:
            raise ValueError("invalid missing_price_policy")

    @classmethod
    def from_payload(cls, payload: Dict[str, Any] | None) -> "BacktestExecutionSpec":
        payload = payload if isinstance(payload, dict) else {}
        defaults = cls()
        return cls(
            signal_generation=str(payload.get("signal_generation") or defaults.signal_generation).upper(),
            order_submission=str(payload.get("order_submission") or defaults.order_submission).upper(),
            fill_price_rule=str(payload.get("fill_price_rule") or defaults.fill_price_rule).upper(),
            cash_constraint=str(payload.get("cash_constraint") or defaults.cash_constraint).upper(),
            fee_model=str(payload.get("fee_model") or defaults.fee_model).upper(),
            slippage_model=str(payload.get("slippage_model") or defaults.slippage_model).upper(),
            portfolio_input=str(payload.get("portfolio_input") or defaults.portfolio_input).upper(),
            allow_short=_payload_bool(payload.get("allow_short"), defaults.allow_short),
            allow_leverage=_payload_bool(payload.get("allow_leverage"), defaults.allow_leverage),
            fee_bps=float(payload.get("fee_bps", defaults.fee_bps)),
            slippage_bps=float(payload.get("slippage_bps", defaults.slippage_bps)),
            missing_price_policy=str(payload.get("missing_price_policy") or defaults.missing_price_policy).upper(),
            quantity_rounding=str(payload.get("quantity_rounding") or defaults.quantity_rounding).upper(),
            minimum_notional_policy=str(payload.get("minimum_notional_policy") or defaults.minimum_notional_policy).upper(),
            target_equity_reference=str(payload.get("target_equity_reference") or defaults.target_equity_reference).upper(),
            sell_before_buy=_payload_bool(payload.get("sell_before_buy"), defaults.sell_before_buy),
            random_seed=int(payload.get("random_seed", defaults.random_seed)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_generation": self.signal_generation,
            "order_submission": self.order_submission,
            "fill_price_rule": self.fill_price_rule,
            "cash_constraint": self.cash_constraint,
            "fee_model": self.fee_model,
            "slippage_model": self.slippage_model,
            "portfolio_input": self.portfolio_input,
            "allow_short": self.allow_short,
            "allow_leverage": self.allow_leverage,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "missing_price_policy": self.missing_price_policy,
            "quantity_rounding": self.quantity_rounding,
            "minimum_notional_policy": self.minimum_notional_policy,
            "target_equity_reference": self.target_equity_reference,
            "sell_before_buy": self.sell_before_buy,
            "random_seed": self.random_seed,
        }


# Public name used by the research platform. The legacy name remains available
# for compatibility with earlier DataTube integrations.
ExecutionSpec = BacktestExecutionSpec


@dataclass(frozen=True)
class BacktestCapabilities:
    """What a provider actually supports, as opposed to what V1 wants."""

    provider: str
    provider_version: str
    asset_classes: Tuple[str, ...]
    supports_multi_leg: bool
    supports_target_position: bool
    supports_target_weight: bool
    supports_cross_sectional_alpha: bool
    supported_signal_generation: Tuple[str, ...]
    supported_order_submission: Tuple[str, ...]
    supported_fill_price_rules: Tuple[str, ...]
    supported_fee_models: Tuple[str, ...]
    supported_slippage_models: Tuple[str, ...]
    supports_dataset_manifest_pin: bool
    supports_mixed_source: bool
    supports_short: bool
    supports_leverage: bool
    supported_missing_price_policies: Tuple[str, ...] = ("FAIL_RUN",)
    supports_fractional_quantity: bool = True
    supports_exchange_rounding: bool = False
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "asset_classes": list(self.asset_classes),
            "supports_multi_leg": self.supports_multi_leg,
            "supports_target_position": self.supports_target_position,
            "supports_target_weight": self.supports_target_weight,
            "supports_cross_sectional_alpha": self.supports_cross_sectional_alpha,
            "supported_signal_generation": list(self.supported_signal_generation),
            "supported_order_submission": list(self.supported_order_submission),
            "supported_fill_price_rules": list(self.supported_fill_price_rules),
            "supported_fee_models": list(self.supported_fee_models),
            "supported_slippage_models": list(self.supported_slippage_models),
            "supports_dataset_manifest_pin": self.supports_dataset_manifest_pin,
            "supports_mixed_source": self.supports_mixed_source,
            "supports_short": self.supports_short,
            "supports_leverage": self.supports_leverage,
            "supported_missing_price_policies": list(self.supported_missing_price_policies),
            "supports_fractional_quantity": self.supports_fractional_quantity,
            "supports_exchange_rounding": self.supports_exchange_rounding,
            "notes": list(self.notes),
        }


CURRENT_HISTORY_BACKTEST_CAPABILITIES = BacktestCapabilities(
    provider="history_data_service",
    provider_version="local-v1",
    asset_classes=("crypto_spot", "polymarket_binary"),
    supports_multi_leg=True,
    supports_target_position=True,
    supports_target_weight=False,
    supports_cross_sectional_alpha=False,
    supported_signal_generation=("BAR_CLOSE",),
    supported_order_submission=("CURRENT_BAR_CLOSE",),
    supported_fill_price_rules=("CURRENT_CLOSE",),
    supported_fee_models=("FIXED_BPS", "POLYMARKET_BINARY_RATE"),
    supported_slippage_models=("NONE",),
    supports_dataset_manifest_pin=False,
    supports_mixed_source=False,
    supports_short=False,
    supports_leverage=False,
    notes=(
        "Existing replay runs StrategyCode actions against local history data.",
        "Binance multi-leg alignment is supported, but cross-sectional Alpha output is not a provider input.",
        "Current execution uses the current row close; it does not implement NEXT_BAR_OPEN_PLUS_SLIPPAGE.",
    ),
)


def audit_execution_spec(
    spec: BacktestExecutionSpec,
    capabilities: BacktestCapabilities = CURRENT_HISTORY_BACKTEST_CAPABILITIES,
) -> Dict[str, Any]:
    issues: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []

    def require(value: str, supported: Iterable[str], code: str, label: str) -> None:
        supported_values = tuple(supported)
        if value not in supported_values:
            issues.append({
                "code": code,
                "field": label,
                "requested": value,
                "supported": ", ".join(supported_values) or "none",
            })

    require(spec.signal_generation, capabilities.supported_signal_generation, "SIGNAL_GENERATION_UNSUPPORTED", "signal_generation")
    require(spec.order_submission, capabilities.supported_order_submission, "ORDER_SUBMISSION_UNSUPPORTED", "order_submission")
    require(spec.fill_price_rule, capabilities.supported_fill_price_rules, "FILL_PRICE_UNSUPPORTED", "fill_price_rule")
    require(spec.fee_model, capabilities.supported_fee_models, "FEE_MODEL_UNSUPPORTED", "fee_model")
    require(spec.slippage_model, capabilities.supported_slippage_models, "SLIPPAGE_MODEL_UNSUPPORTED", "slippage_model")
    if spec.allow_short and not capabilities.supports_short:
        issues.append({"code": "SHORT_UNSUPPORTED", "field": "allow_short", "requested": "true", "supported": "false"})
    if spec.allow_leverage and not capabilities.supports_leverage:
        issues.append({"code": "LEVERAGE_UNSUPPORTED", "field": "allow_leverage", "requested": "true", "supported": "false"})
    if spec.portfolio_input == "TARGET_WEIGHT" and not capabilities.supports_target_weight:
        issues.append({"code": "TARGET_WEIGHT_UNSUPPORTED", "field": "portfolio_input", "requested": spec.portfolio_input, "supported": "TARGET_POSITION"})
    if spec.portfolio_input == "CROSS_SECTIONAL_ALPHA" and not capabilities.supports_cross_sectional_alpha:
        issues.append({"code": "CROSS_SECTIONAL_ALPHA_UNSUPPORTED", "field": "portfolio_input", "requested": spec.portfolio_input, "supported": "TARGET_POSITION"})
    require(
        spec.missing_price_policy,
        capabilities.supported_missing_price_policies,
        "MISSING_PRICE_POLICY_UNSUPPORTED",
        "missing_price_policy",
    )
    if spec.quantity_rounding == "FRACTIONAL" and not capabilities.supports_fractional_quantity:
        issues.append({"code": "FRACTIONAL_QUANTITY_UNSUPPORTED", "field": "quantity_rounding", "requested": "FRACTIONAL", "supported": "EXCHANGE"})
    if spec.quantity_rounding == "EXCHANGE" and not capabilities.supports_exchange_rounding:
        issues.append({"code": "EXCHANGE_ROUNDING_UNSUPPORTED", "field": "quantity_rounding", "requested": "EXCHANGE", "supported": "FRACTIONAL"})

    if not capabilities.supports_dataset_manifest_pin:
        warnings.append({
            "code": "DATASET_MANIFEST_NOT_PINNED",
            "message": "The current provider reads its existing history store and cannot yet freeze a Data Platform manifest.",
        })
    return {
        "ok": not issues,
        "status": "READY" if not issues else "BLOCKED",
        "provider": capabilities.provider,
        "provider_version": capabilities.provider_version,
        "execution_spec": spec.to_dict(),
        "issues": issues,
        "warnings": warnings,
    }


class ExistingBacktestAdapter:
    """Contract-only adapter for the existing StrategyCode backtest provider.

    It validates a research request and refuses unsupported semantics.  It does
    not silently translate a cross-sectional Alpha into a legacy strategy,
    because that would make the research result irreproducible.
    """

    def __init__(self, capabilities: BacktestCapabilities = CURRENT_HISTORY_BACKTEST_CAPABILITIES):
        self._capabilities = capabilities

    def capabilities(self) -> Dict[str, Any]:
        return self._capabilities.to_dict()

    def validate(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request = request if isinstance(request, dict) else {}
        spec = BacktestExecutionSpec.from_payload(request.get("execution_spec"))
        result = audit_execution_spec(spec, self._capabilities)
        extra_issues = list(result["issues"])
        instruments = request.get("instrument_ids") if isinstance(request.get("instrument_ids"), list) else []
        if len(instruments) > 1 and not self._capabilities.supports_multi_leg:
            extra_issues.append({
                "code": "MULTI_LEG_UNSUPPORTED",
                "field": "instrument_ids",
                "requested": str(len(instruments)),
                "supported": "1",
            })
        if request.get("alpha_output") is not None and not self._capabilities.supports_cross_sectional_alpha:
            extra_issues.append({
                "code": "ALPHA_OUTPUT_UNSUPPORTED",
                "field": "alpha_output",
                "requested": "research_alpha_artifact",
                "supported": "legacy_strategy_code_actions",
            })
        result["issues"] = extra_issues
        result["ok"] = not extra_issues
        result["status"] = "READY" if not extra_issues else "BLOCKED"
        return result

    def submit(self, request: Dict[str, Any]) -> Dict[str, Any]:
        validation = self.validate(request)
        if not validation["ok"]:
            return {
                "accepted": False,
                "status": "BLOCKED",
                "validation": validation,
                "message": "Backtest request was rejected by the execution contract.",
            }
        return {
            "accepted": False,
            "status": "NOT_CONNECTED",
            "validation": validation,
            "message": "Provider validation passed; submission wiring is intentionally not connected yet.",
        }
