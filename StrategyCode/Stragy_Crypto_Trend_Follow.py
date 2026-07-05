import json
from math import isfinite


OutPutNum = 2
InPutNum = 10

Outputs = [
    {
        "Num": None,
        "Kind": None,
        "Boolean": False,
        "Id": f"Output{i + 1}",
        "Context": None,
        "name": f"OutPut{i + 1}",
        "Link": 0,
        "Description": "",
    }
    for i in range(OutPutNum)
]

Inputs = [
    {
        "Num": None,
        "Kind": None,
        "Id": f"Input{i + 1}",
        "Context": None,
        "Isnecessary": True,
        "name": f"Input{i + 1}",
        "Link": 0,
        "IsLabel": False,
    }
    for i in range(InPutNum)
]

NodeKind = "Normal"
Lable = [{"Id": "Label1", "Kind": "None"}]


LegsSchema = [
    {
        "name": "Crypto trend instrument",
        "label": "Crypto Spot",
        "leg_index": 0,
        "asset_class": "crypto_spot",
        "venue": "binance",
        "leg_kind": "external_price_series",
        "purpose": "Trade or simulate a single Binance spot symbol with trend-following signals.",
        "required": True,
    }
]


ParamsSchema = {
    "fast_window": {
        "type": "integer",
        "default": 20,
        "min": 2,
        "label": "Fast EMA window",
        "description": "Short lookback used for trend detection.",
    },
    "slow_window": {
        "type": "integer",
        "default": 60,
        "min": 3,
        "label": "Slow EMA window",
        "description": "Long lookback used for trend detection.",
    },
    "entry_z": {
        "type": "number",
        "default": 0.002,
        "min": 0.0,
        "label": "Entry trend threshold",
        "description": "Open long when fast EMA is above slow EMA by this ratio.",
    },
    "exit_z": {
        "type": "number",
        "default": 0.0005,
        "min": 0.0,
        "label": "Exit trend threshold",
        "description": "Exit when fast EMA premium falls below this ratio.",
    },
    "target_position": {
        "type": "number",
        "default": 1.0,
        "min": 0.0,
        "max": 1.0,
        "label": "Target position",
        "description": "Fraction of test capital to hold while trend is active.",
    },
    "stop_loss_pct": {
        "type": "number",
        "default": 0.04,
        "min": 0.0,
        "label": "Stop loss",
        "description": "Exit if price falls this much from entry.",
    },
    "trailing_stop_pct": {
        "type": "number",
        "default": 0.08,
        "min": 0.0,
        "label": "Trailing stop",
        "description": "Exit if price falls this much from peak after entry.",
    },
    "initial_cash": {
        "type": "number",
        "default": 10000,
        "min": 1,
        "label": "Initial cash",
        "description": "Starting account value used by the local backtest runner.",
    },
    "fee_bps": {
        "type": "number",
        "default": 2,
        "min": 0,
        "label": "Fee bps",
        "description": "Trading fee in basis points charged by the local backtest runner.",
    },
}


Inputs[0]["name"] = "UseData"
Inputs[0]["Kind"] = "String"
Inputs[0]["Isnecessary"] = True
for _idx, _name in enumerate(ParamsSchema.keys(), start=1):
    Inputs[_idx]["name"] = _name
    Inputs[_idx]["Kind"] = ParamsSchema[_name]["type"]
    Inputs[_idx]["Isnecessary"] = False
    Inputs[_idx]["Context"] = ParamsSchema[_name].get("default")


def _to_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        number = float(value)
        return number if isfinite(number) else default
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _parse_usedata(raw):
    if isinstance(raw, dict):
        return raw, None
    text = str(raw or "").strip()
    if not text:
        return {}, None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}, None
    except Exception as exc:
        return {}, str(exc)


def _read_input(node_inputs, index, default=None):
    if len(node_inputs) <= index:
        return default
    item = node_inputs[index] or {}
    value = item.get("Context")
    if value in (None, ""):
        value = item.get("Num", default)
    return value


def _ema(values, window):
    if not values:
        return None
    alpha = 2.0 / (max(1, window) + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def _price_series(usedata):
    raw = usedata.get("closes") or usedata.get("close_series") or usedata.get("CloseSeries") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    values = []
    for value in raw if isinstance(raw, list) else []:
        number = _to_float(value, None)
        if number is not None and number > 0:
            values.append(number)
    close = _to_float(usedata.get("close", usedata.get("Close", None)), None)
    if close is not None and close > 0:
        values.append(close)
    return values


def _run_strategy(usedata, params):
    fast_window = _to_int(params.get("fast_window"), 20)
    slow_window = _to_int(params.get("slow_window"), 60)
    entry_z = _to_float(params.get("entry_z"), 0.002)
    exit_z = _to_float(params.get("exit_z"), 0.0005)
    target_position = max(0.0, min(1.0, _to_float(params.get("target_position"), 1.0)))
    stop_loss_pct = _to_float(params.get("stop_loss_pct"), 0.04)
    trailing_stop_pct = _to_float(params.get("trailing_stop_pct"), 0.08)

    prices = _price_series(usedata)
    close = prices[-1] if prices else _to_float(usedata.get("close"), 0.0)
    fast = _ema(prices[-max(fast_window * 4, fast_window):], fast_window) if prices else None
    slow = _ema(prices[-max(slow_window * 4, slow_window):], slow_window) if prices else None
    current_pos = _to_float(usedata.get("position", usedata.get("Position", 0.0)), 0.0)
    entry_price = _to_float(usedata.get("entry_price", usedata.get("EntryPrice", close)), close)
    peak_price = _to_float(usedata.get("peak_price", usedata.get("PeakPrice", close)), close)

    trend_ratio = ((fast - slow) / slow) if fast is not None and slow not in (None, 0) else 0.0
    pnl_pct = ((close - entry_price) / entry_price) if entry_price else 0.0
    drawdown_pct = ((peak_price - close) / peak_price) if peak_price else 0.0

    target = current_pos
    reason = "hold"
    if fast is None or slow is None or close <= 0:
        target = 0.0
        reason = "missing_price_history"
    elif current_pos <= 0 and trend_ratio >= entry_z:
        target = target_position
        reason = "trend_entry"
    elif current_pos > 0 and trend_ratio <= exit_z:
        target = 0.0
        reason = "trend_exit"
    elif current_pos > 0 and pnl_pct <= -abs(stop_loss_pct):
        target = 0.0
        reason = "stop_loss"
    elif current_pos > 0 and drawdown_pct >= abs(trailing_stop_pct):
        target = 0.0
        reason = "trailing_stop"

    actions = []
    if abs(target - current_pos) > 1e-6:
        actions.append({
            "type": "SET_TARGET",
            "asset_class": "crypto_spot",
            "venue": "binance",
            "target_position": target,
            "reason": reason,
        })

    decision = "SET_TARGET" if actions else "HOLD"
    position_state = "flat" if abs(current_pos) <= 1e-9 else "long"
    trend_state = "uptrend" if trend_ratio >= entry_z else ("exit_zone" if trend_ratio <= exit_z else "neutral")
    risk_state = reason if reason in {"stop_loss", "trailing_stop", "missing_price_history"} else "normal"
    metrics = {
        "close": close,
        "fast_ema": fast,
        "slow_ema": slow,
        "trend_ratio": trend_ratio,
        "pnl_pct": pnl_pct,
        "drawdown_pct": drawdown_pct,
        "target_position": target,
        "decision": decision,
        "reason": reason,
        "position_state": position_state,
        "trend_state": trend_state,
        "risk_state": risk_state,
    }
    metrics_meta = {
        "close": {"kind": "continuous", "label": "Close", "unit": "price", "panel": "metric_values"},
        "fast_ema": {"kind": "continuous", "label": "Fast EMA", "unit": "price", "panel": "metric_values"},
        "slow_ema": {"kind": "continuous", "label": "Slow EMA", "unit": "price", "panel": "metric_values"},
        "trend_ratio": {"kind": "continuous", "label": "Trend Ratio", "unit": "ratio", "panel": "metric_values"},
        "pnl_pct": {"kind": "continuous", "label": "PnL %", "unit": "ratio", "panel": "metric_values"},
        "drawdown_pct": {"kind": "continuous", "label": "Drawdown %", "unit": "ratio", "panel": "metric_values"},
        "target_position": {"kind": "continuous", "label": "Target Position", "unit": "ratio", "panel": "metric_values"},
        "decision": {"kind": "state", "label": "Decision", "panel": "metric_states"},
        "reason": {"kind": "state", "label": "Decision Reason", "panel": "metric_states"},
        "position_state": {"kind": "state", "label": "Position State", "panel": "metric_states"},
        "trend_state": {"kind": "state", "label": "Trend State", "panel": "metric_states"},
        "risk_state": {"kind": "state", "label": "Risk State", "panel": "metric_states"},
    }

    return {
        "schema_version": "1.0",
        "actions": actions,
        "metrics": metrics,
        "metrics_meta": metrics_meta,
        "print": [
            f"close={close}",
            f"fast_ema={fast} slow_ema={slow} trend_ratio={trend_ratio}",
            f"position={current_pos} target={target} reason={reason}",
        ],
        "wake_reason": None,
    }


def run_node(node):
    try:
        node_inputs = node.get("Inputs") or []
        usedata_raw = _read_input(node_inputs, 0, "{}")
        usedata, err = _parse_usedata(usedata_raw)
        if err:
            out_json = {"actions": [], "print": [f"[UseDataError] {err}"], "wake_reason": None}
            Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
            Outputs[1]["Boolean"] = False
            return Outputs

        params = {}
        for index, name in enumerate(ParamsSchema.keys(), start=1):
            params[name] = _read_input(node_inputs, index, ParamsSchema[name].get("default"))
        out_json = _run_strategy(usedata, params)
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = True
        return Outputs
    except Exception as exc:
        out_json = {"actions": [], "print": [f"[RuntimeError] {type(exc).__name__}: {exc}"], "wake_reason": None}
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = False
        return Outputs
