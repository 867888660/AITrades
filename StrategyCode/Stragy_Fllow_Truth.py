import ast
import json
import re
from datetime import datetime, timedelta, timezone


OutPutNum = 2
InPutNum = 11

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


ParamsSchema = {
    "FactSide": {
        "type": "enum",
        "values": ["Yes", "No"],
        "default": "Yes",
        "label": "事实方向",
        "description": "策略认为最终会成真的一侧。",
    },
    "fair_price": {
        "type": "number",
        "default": 0.65,
        "min": 0.0001,
        "max": 0.9999,
        "label": "公平价格",
        "description": "你对事实方向的主观真实概率或估值。",
    },
    "entry_edge": {
        "type": "number",
        "default": 0.05,
        "min": 0.0,
        "max": 1.0,
        "label": "开仓边际",
        "description": "fair_price - ask 达到该值才允许开仓或加仓。",
    },
    "exit_edge": {
        "type": "number",
        "default": 0.01,
        "min": -1.0,
        "max": 1.0,
        "label": "离场边际",
        "description": "fair_price - bid 低于该值时退出事实方向仓位。",
    },
    "max_position_pct": {
        "type": "number",
        "default": 0.5,
        "min": 0.0,
        "max": 1.0,
        "label": "最大仓位",
        "description": "事实方向最大目标仓位比例。",
    },
    "stop_loss_pct": {
        "type": "number",
        "default": 0.25,
        "min": 0.0,
        "max": 1.0,
        "label": "止损比例",
        "description": "按入场价或持仓均价计算的最大可承受亏损。",
    },
    "take_profit_pct": {
        "type": "number",
        "default": 0.4,
        "min": 0.0,
        "max": 10.0,
        "label": "止盈比例",
        "description": "按入场价或持仓均价计算的目标收益。",
    },
    "take_profit_order_mode": {
        "type": "enum",
        "values": ["trigger_exit", "maker_post_only_buy", "maker_post_only_sell", "disabled"],
        "default": "trigger_exit",
        "label": "止盈订单模式",
        "description": "trigger_exit 保持旧逻辑；maker_post_only_buy 按止盈价挂反方向买单；maker_post_only_sell 挂事实方向卖单。",
    },
    "take_profit_order_tif": {
        "type": "enum",
        "values": ["GTC", "GTD"],
        "default": "GTC",
        "label": "止盈订单 TIF",
        "description": "预挂 maker 止盈单的 time-in-force。",
    },
    "take_profit_reprice_threshold": {
        "type": "number",
        "default": 0.01,
        "min": 0.0,
        "max": 1.0,
        "label": "止盈改单阈值",
        "description": "已有止盈单价格与目标价差超过该值时替换挂单。",
    },
    "take_profit_min_qty": {
        "type": "number",
        "default": 1.0,
        "min": 0.0,
        "label": "止盈最小数量",
        "description": "可卖数量低于该值时不预挂止盈单。",
    },
    "de_risk_days": {
        "type": "number",
        "default": 1.0,
        "min": 0.0,
        "label": "到期降风险天数",
        "description": "距离结束不足该天数时线性降低目标仓位。",
    },
    "cooldown_seconds": {
        "type": "integer",
        "default": 600,
        "min": 0,
        "label": "冷却秒数",
        "description": "调仓后禁止再次开仓或加仓的时间。",
    },
    "min_target_delta": {
        "type": "number",
        "default": 0.03,
        "min": 0.0,
        "max": 1.0,
        "label": "最小调仓差",
        "description": "目标仓位变化小于该值时不发出普通调仓动作。",
    },
}

ControlsSchema = {
    "manual_pause_open": {
        "type": "bool",
        "default": False,
        "label": "暂停开新仓",
        "description": "只阻止新开仓或加仓，不阻止止盈止损和平仓。",
    },
    "force_flat": {
        "type": "bool",
        "default": False,
        "label": "强制清仓",
        "description": "立即把事实方向与反方向目标仓位都设为 0。",
    },
    "risk_scale": {
        "type": "number",
        "default": 1.0,
        "min": 0.0,
        "max": 1.0,
        "label": "风险缩放",
        "description": "临时按比例缩放目标仓位。",
    },
    "debug_raw_inputs": {
        "type": "bool",
        "default": False,
        "label": "打印原始输入",
        "description": "在 FunctionJson.print 中输出原始参数和 UseData 字段。",
    },
}

RuntimeStateSchema = {
    "last_signal": {
        "type": "string",
        "default": "none",
        "label": "上次信号",
        "description": "策略最近一次决策标签。",
    },
    "last_target": {
        "type": "number",
        "default": 0.0,
        "label": "上次目标仓位",
        "description": "事实方向最近一次计算得到的目标仓位。",
    },
    "last_action_at": {
        "type": "string",
        "default": None,
        "label": "上次动作时间",
        "description": "最近一次发出 SETPOS 的时间。",
    },
    "entry_price": {
        "type": "number",
        "default": None,
        "label": "入场价格",
        "description": "开仓时记录的事实方向入场价格。",
    },
    "entry_side": {
        "type": "string",
        "default": None,
        "label": "入场方向",
        "description": "entry_price 对应的方向。",
    },
    "cooldown_until": {
        "type": "string",
        "default": None,
        "label": "冷却结束时间",
        "description": "冷却期结束时间；冷却期内只允许减仓或清仓。",
    },
}

FunctionIntroduction = (
    "组件功能：Stragy_Fllow_Truth（简化版）。\n\n"
    "核心逻辑：用户给出事实方向和公平价格，策略只回答三个问题：\n"
    "1. 当前 ask 是否比公平价格便宜到足够开仓；\n"
    "2. 当前 bid 是否已经失去持仓边际；\n"
    "3. 是否触发止盈、止损、到期降风险、暂停开仓或强制清仓。\n\n"
    "输出统一 FunctionJson，动作使用 SETPOS(side, target_pct)。"
)


def _set_input(index, name, kind, default=None, description=""):
    item = Inputs[index]
    item["name"] = name
    item["Kind"] = kind
    item["Isnecessary"] = True
    item["IsLabel"] = False
    item["Description"] = description
    item["Default"] = default
    if kind == "Num":
        item["Num"] = default
    elif default is not None:
        item["Context"] = default


for output in Outputs:
    output["Kind"] = "String"

_set_input(0, "UseData", "String", None, "JSON dict or key=value text.")
_set_input(1, "FactSide", "String", ParamsSchema["FactSide"]["default"], "Yes or No.")
_set_input(2, "fair_price", "Num", ParamsSchema["fair_price"]["default"], "Subjective fair price.")
_set_input(3, "entry_edge", "Num", ParamsSchema["entry_edge"]["default"], "Entry edge.")
_set_input(4, "exit_edge", "Num", ParamsSchema["exit_edge"]["default"], "Exit edge.")
_set_input(5, "max_position_pct", "Num", ParamsSchema["max_position_pct"]["default"], "Max target position.")
_set_input(6, "stop_loss_pct", "Num", ParamsSchema["stop_loss_pct"]["default"], "Stop loss from entry.")
_set_input(7, "take_profit_pct", "Num", ParamsSchema["take_profit_pct"]["default"], "Take profit from entry.")
_set_input(8, "de_risk_days", "Num", ParamsSchema["de_risk_days"]["default"], "Linear de-risk window.")
_set_input(9, "cooldown_seconds", "Num", ParamsSchema["cooldown_seconds"]["default"], "Cooldown after action.")
_set_input(10, "min_target_delta", "Num", ParamsSchema["min_target_delta"]["default"], "Minimum rebalance delta.")

Outputs[0]["name"] = "FunctionJson"
Outputs[0]["Kind"] = "String"
Outputs[0]["Description"] = "Unified action JSON."
Outputs[1]["name"] = "CodeIsOk"
Outputs[1]["Kind"] = "Boolean"
Outputs[1]["Description"] = "Whether this run completed successfully."


def _schema_defaults(schema):
    return {key: spec.get("default") for key, spec in schema.items() if isinstance(spec, dict)}


def _norm_key(key):
    if key is None:
        return ""
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


class _UseDataProxy:
    def __init__(self, raw):
        self._raw = raw or {}
        self._norm_map = {}
        for key in self._raw.keys():
            normalized = _norm_key(key)
            if normalized and normalized not in self._norm_map:
                self._norm_map[normalized] = key

    def _resolve_key(self, key):
        if key in self._raw:
            return key
        normalized = _norm_key(key)
        return self._norm_map.get(normalized)

    def get(self, key, default=None):
        real_key = self._resolve_key(key)
        if real_key is None:
            return default
        return self._raw.get(real_key, default)

    def has(self, key):
        return self._resolve_key(key) is not None

    def get_any(self, keys, default=None):
        for key in keys:
            if self.has(key):
                return self.get(key)
        return default

    def to_dict(self):
        return dict(self._raw)


def _parse_value(text):
    if text is None:
        return None
    value = str(text).strip()
    if value == "":
        return ""

    lowered = value.lower()
    if lowered in ("none", "null"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    number_text = value.replace("_", "").replace(",", "")
    if re.fullmatch(r"[+\-]?\d+", number_text):
        try:
            return int(number_text)
        except Exception:
            pass
    if re.fullmatch(r"[+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?", number_text):
        try:
            return float(number_text)
        except Exception:
            pass

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        try:
            return ast.literal_eval(value)
        except Exception:
            return value[1:-1]
    return value


def _parse_kv_text(text):
    out = {}
    if not isinstance(text, str):
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip()
        if key:
            out[key] = _parse_value(value.strip())
    return out


def _parse_usedata(raw):
    if isinstance(raw, dict):
        return raw, None
    if raw is None:
        return None, "UseData is empty."
    if not isinstance(raw, str):
        return None, f"Unsupported UseData type: {type(raw)}"

    text = raw.strip()
    if not text:
        return None, "UseData is an empty string."

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
    except Exception:
        pass

    kv = _parse_kv_text(text)
    if kv:
        return kv, None
    return None, "UseData must be a JSON object or key=value text."


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _merge_non_empty(base, *updates):
    merged = dict(base or {})
    for update in updates:
        if not isinstance(update, dict):
            continue
        for key, value in update.items():
            if value is None or value == "":
                continue
            merged[key] = value
    return merged


def _to_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("_", "")
            if text == "":
                return float(default)
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        return float(value)
    except Exception:
        return float(default)


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return int(default)
        return int(float(value))
    except Exception:
        return int(default)


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _clamp(value, lo, hi):
    return max(float(lo), min(float(hi), float(value)))


def _norm_side(value):
    text = str(value or "").strip().capitalize()
    if text not in ("Yes", "No"):
        return "Yes"
    return text


def _read_input_value(node_inputs, index):
    if len(node_inputs) <= index:
        return None
    item = node_inputs[index] or {}
    value = item.get("Context")
    if (value is None or value == "") and item.get("Num") is not None:
        value = item.get("Num")
    return value


def _stable_json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_price(value):
    return value is not None and 0.0 < float(value) < 1.0


def _side_candidates(side, field):
    suffixes = {
        "ask": ["now_ask", "AskPrice", "ask", "best_ask", "BestAsk", "price_ask"],
        "bid": ["now_bid", "BidPrice", "bid", "best_bid", "BestBid", "price_bid"],
        "qty": ["now_Qty", "Qty", "qty", "PositionQty", "position_qty"],
        "avg": ["now_avgPrice", "AvgPrice", "PositionAvgPrice", "avgPrice", "avg_price", "cost_basis"],
        "open": ["OpenOrdersQty", "open_orders_qty", "open_qty"],
        "open_buy": ["OpenBuyQty", "OpenBuyOrdersQty", "open_buy_qty"],
        "open_sell": ["OpenSellQty", "OpenSellOrdersQty", "open_sell_qty"],
        "available": ["AvailableSellQty", "available_sell_qty"],
        "take_profit_order_qty": ["TakeProfitOrderQty", "take_profit_order_qty"],
        "take_profit_order_price": ["TakeProfitOrderPrice", "take_profit_order_price"],
        "pos": ["Now_Pos", "Pos", "position_pct", "target_pct", "position"],
        "cap": ["MaxPos", "PosCap", "position_cap", "max_position_pct"],
    }.get(field, [field])

    out = []
    for suffix in suffixes:
        out.append(f"{side}_{suffix}")
        out.append(f"L0_{side}_{suffix}")
        out.append(f"{side}_L0_{suffix}")
    return out


def _side_float(usedata, side, field, default=0.0):
    return _to_float(usedata.get_any(_side_candidates(side, field), default), default)


def _position_ratio(usedata, side, side_qty, other_qty):
    explicit = usedata.get_any(_side_candidates(side, "pos"), None)
    if explicit is not None:
        return _clamp(_to_float(explicit, 0.0), 0.0, 1.0), "explicit"

    sq = max(0.0, _to_float(side_qty, 0.0))
    oq = max(0.0, _to_float(other_qty, 0.0))
    if max(sq, oq) <= 1.0:
        return _clamp(sq, 0.0, 1.0), "qty_pct_fallback"
    total = sq + oq
    if total <= 0.0:
        return 0.0, "empty"
    return _clamp(sq / total, 0.0, 1.0), "qty_ratio_fallback"


def _parse_dt(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "missing"):
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except Exception:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt):
    if not isinstance(dt, datetime):
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pick_now(raw_map):
    for key in ("NowTime", "query_time", "query_time_beijing", "ts_utc", "timestamp"):
        parsed = _parse_dt(raw_map.get(key))
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _edge_sized_target(edge, entry_edge, max_position_pct):
    threshold = max(float(entry_edge), 0.0001)
    if edge < threshold:
        return 0.0
    ramp = 0.5 + 0.5 * _clamp((edge - threshold) / threshold, 0.0, 1.0)
    return _clamp(max_position_pct * ramp, 0.0, max_position_pct)


def _apply_de_risk(target, day_to_end, de_risk_days):
    window = max(0.0, float(de_risk_days))
    if window <= 0.0:
        return target, 1.0
    days = _to_float(day_to_end, 9999.0)
    if days >= window:
        return target, 1.0
    multiplier = _clamp(days / window, 0.0, 1.0)
    return target * multiplier, multiplier


def _build_params(usedata, node_params):
    defaults = _schema_defaults(ParamsSchema)
    data_params = _as_dict(usedata.get("Params", {}))
    merged = _merge_non_empty(defaults, data_params, node_params)

    # Backward compatibility with the old rule-tree version.
    has_new_fair_price = data_params.get("fair_price") not in (None, "")
    has_new_fair_price = has_new_fair_price or (node_params or {}).get("fair_price") not in (None, "")
    if not has_new_fair_price:
        legacy_ref = merged.get("FactSide_ref_ask", usedata.get("FactSide_ref_ask", usedata.get("FS_ref_ask", None)))
        if legacy_ref is not None:
            merged["fair_price"] = legacy_ref

    out = {
        "FactSide": _norm_side(merged.get("FactSide", defaults["FactSide"])),
        "fair_price": _clamp(_to_float(merged.get("fair_price"), defaults["fair_price"]), 0.0001, 0.9999),
        "entry_edge": _clamp(_to_float(merged.get("entry_edge"), defaults["entry_edge"]), 0.0, 1.0),
        "exit_edge": _clamp(_to_float(merged.get("exit_edge"), defaults["exit_edge"]), -1.0, 1.0),
        "max_position_pct": _clamp(
            _to_float(merged.get("max_position_pct"), defaults["max_position_pct"]),
            0.0,
            1.0,
        ),
        "stop_loss_pct": _clamp(_to_float(merged.get("stop_loss_pct"), defaults["stop_loss_pct"]), 0.0, 1.0),
        "take_profit_pct": _clamp(
            _to_float(merged.get("take_profit_pct"), defaults["take_profit_pct"]),
            0.0,
            10.0,
        ),
        "take_profit_order_mode": str(
            merged.get("take_profit_order_mode") or defaults["take_profit_order_mode"]
        ).strip().lower(),
        "take_profit_order_tif": str(
            merged.get("take_profit_order_tif") or defaults["take_profit_order_tif"]
        ).strip().upper(),
        "take_profit_reprice_threshold": _clamp(
            _to_float(merged.get("take_profit_reprice_threshold"), defaults["take_profit_reprice_threshold"]),
            0.0,
            1.0,
        ),
        "take_profit_min_qty": max(
            0.0,
            _to_float(merged.get("take_profit_min_qty"), defaults["take_profit_min_qty"]),
        ),
        "de_risk_days": max(0.0, _to_float(merged.get("de_risk_days"), defaults["de_risk_days"])),
        "cooldown_seconds": max(0, _to_int(merged.get("cooldown_seconds"), defaults["cooldown_seconds"])),
        "min_target_delta": _clamp(
            _to_float(merged.get("min_target_delta"), defaults["min_target_delta"]),
            0.0,
            1.0,
        ),
    }
    return out, merged


def _build_controls(usedata):
    controls = _merge_non_empty(
        _schema_defaults(ControlsSchema),
        _as_dict(usedata.get("UserState", {})),
        _as_dict(usedata.get("Controls", {})),
    )
    return {
        "manual_pause_open": _to_bool(controls.get("manual_pause_open"), False),
        "force_flat": _to_bool(controls.get("force_flat"), False),
        "risk_scale": _clamp(_to_float(controls.get("risk_scale"), 1.0), 0.0, 1.0),
        "debug_raw_inputs": _to_bool(controls.get("debug_raw_inputs"), False),
    }


def _build_runtime_state(usedata):
    return _merge_non_empty(
        _schema_defaults(RuntimeStateSchema),
        _as_dict(usedata.get("State", {})),
        _as_dict(usedata.get("RuntimeState", {})),
    )


def _run_strategy(usedata, node_params):
    raw_map = usedata.to_dict()
    params, raw_params = _build_params(usedata, node_params or {})
    controls = _build_controls(usedata)
    runtime = _build_runtime_state(usedata)
    now_dt = _pick_now(raw_map)
    now_iso = _iso(now_dt)

    fact_side = params["FactSide"]
    opp_side = "No" if fact_side == "Yes" else "Yes"

    fair_price = params["fair_price"]
    entry_edge = params["entry_edge"]
    exit_edge = params["exit_edge"]
    max_position_pct = params["max_position_pct"]
    stop_loss_pct = params["stop_loss_pct"]
    take_profit_pct = params["take_profit_pct"]
    take_profit_order_mode = params["take_profit_order_mode"]
    if take_profit_order_mode == "maker_post_only":
        take_profit_order_mode = "maker_post_only_buy"
    if take_profit_order_mode not in ("trigger_exit", "maker_post_only_buy", "maker_post_only_sell", "disabled"):
        take_profit_order_mode = "trigger_exit"
    take_profit_order_tif = params["take_profit_order_tif"]
    if take_profit_order_tif not in ("GTC", "GTD"):
        take_profit_order_tif = "GTC"
    take_profit_reprice_threshold = params["take_profit_reprice_threshold"]
    take_profit_min_qty = params["take_profit_min_qty"]
    de_risk_days = params["de_risk_days"]
    cooldown_seconds = params["cooldown_seconds"]
    min_target_delta = params["min_target_delta"]

    ask = _side_float(usedata, fact_side, "ask", None)
    bid = _side_float(usedata, fact_side, "bid", None)
    opp_bid = _side_float(usedata, opp_side, "bid", 0.0)
    qty = _side_float(usedata, fact_side, "qty", 0.0)
    opp_qty = _side_float(usedata, opp_side, "qty", 0.0)
    avg_price = _side_float(usedata, fact_side, "avg", 0.0)
    leg_uid = str(usedata.get_any(("L0_LegUid", "LegUid"), "") or "").strip()
    available_sell_qty = _side_float(usedata, fact_side, "available", qty)
    tp_order_qty = _side_float(usedata, fact_side, "take_profit_order_qty", 0.0)
    tp_order_price = _side_float(usedata, fact_side, "take_profit_order_price", 0.0)
    opp_open_buy_qty = _side_float(usedata, opp_side, "open_buy", 0.0)
    opp_tp_order_qty = _side_float(usedata, opp_side, "take_profit_order_qty", 0.0)
    opp_tp_order_price = _side_float(usedata, opp_side, "take_profit_order_price", 0.0)
    tp_order_status = str(
        usedata.get_any(
            (
                f"L0_{fact_side}_TakeProfitOrderStatus",
                f"{fact_side}_TakeProfitOrderStatus",
            ),
            "",
        )
        or ""
    ).strip().lower()
    opp_tp_order_status = str(
        usedata.get_any(
            (
                f"L0_{opp_side}_TakeProfitOrderStatus",
                f"{opp_side}_TakeProfitOrderStatus",
            ),
            "",
        )
        or ""
    ).strip().lower()
    pos, pos_source = _position_ratio(usedata, fact_side, qty, opp_qty)
    opp_pos, opp_pos_source = _position_ratio(usedata, opp_side, opp_qty, qty)
    cap = _clamp(_side_float(usedata, fact_side, "cap", 1.0), 0.0, 1.0)
    max_position_pct = min(max_position_pct, cap)
    day_to_end = _to_float(usedata.get_any(("day_to_end", "days_to_end", "DayToEnd"), 9999.0), 9999.0)

    quote_ok = _is_price(ask) and _is_price(bid) and bid <= ask
    spread = None if not quote_ok else ask - bid
    edge_to_buy = None if not quote_ok else fair_price - ask
    edge_to_hold = None if not quote_ok else fair_price - bid

    entry_price = _to_float(runtime.get("entry_price"), 0.0)
    if entry_price <= 0.0:
        entry_price = avg_price
    pnl_pct = None
    if pos > 0.0 and entry_price > 0.0 and _is_price(bid):
        pnl_pct = bid / entry_price - 1.0

    cooldown_until_dt = _parse_dt(runtime.get("cooldown_until"))
    cooldown_active = cooldown_until_dt is not None and now_dt < cooldown_until_dt

    target = pos
    signal = "hold"
    protective_exit = False
    notes = []
    opposite_take_profit_locked = (
        take_profit_order_mode == "maker_post_only_buy"
        and qty > 0.0
        and opp_qty >= max(0.0, qty - max(take_profit_min_qty, qty * 0.01))
    )

    if controls["force_flat"]:
        target = 0.0
        signal = "force_flat"
        protective_exit = True
    elif not quote_ok:
        target = pos
        signal = "invalid_quote"
        notes.append("quote is missing or crossed; keep current target.")
    else:
        if opposite_take_profit_locked:
            target = pos
            signal = "take_profit_locked"
            notes.append("opposite buy take-profit appears filled; keep locked complete set.")
        elif pos > 0.0 and pnl_pct is not None and stop_loss_pct > 0.0 and pnl_pct <= -stop_loss_pct:
            target = 0.0
            signal = "stop_loss"
            protective_exit = True
        elif (
            take_profit_order_mode == "trigger_exit"
            and pos > 0.0
            and pnl_pct is not None
            and take_profit_pct > 0.0
            and pnl_pct >= take_profit_pct
        ):
            target = 0.0
            signal = "take_profit"
            protective_exit = True
        elif pos > 0.0 and edge_to_hold <= exit_edge:
            target = 0.0
            signal = "edge_exit"
            protective_exit = True
        elif pos <= 0.0:
            target = _edge_sized_target(edge_to_buy, entry_edge, max_position_pct)
            signal = "entry" if target > 0.0 else "wait_edge"
        else:
            candidate = _edge_sized_target(edge_to_buy, entry_edge, max_position_pct)
            target = max(pos, candidate)
            target = min(target, max_position_pct)
            signal = "hold_edge"

        if target > 0.0:
            before_de_risk = target
            target, de_risk_multiplier = _apply_de_risk(target, day_to_end, de_risk_days)
            if target < before_de_risk:
                signal = "de_risk" if signal in ("entry", "hold_edge", "wait_edge") else signal
                notes.append(f"de-risk multiplier={de_risk_multiplier:.4f}.")
        else:
            de_risk_multiplier = 1.0

        if controls["manual_pause_open"] and target > pos:
            target = pos
            signal = "manual_pause_open" if pos <= 0.0 else "manual_pause_increase"

        if cooldown_active and target > pos:
            target = pos
            signal = "cooldown"

    if not quote_ok:
        de_risk_multiplier = 1.0

    target = _clamp(target * controls["risk_scale"], 0.0, max_position_pct)
    if controls["risk_scale"] < 1.0 and target < pos and signal in ("entry", "hold_edge", "wait_edge"):
        signal = "risk_scale_reduce"

    target_delta = abs(target - pos)
    opp_target = opp_pos if take_profit_order_mode == "maker_post_only_buy" and opp_pos > 0.0 and not controls["force_flat"] else 0.0
    opp_delta = abs(opp_target - opp_pos)
    force_action = protective_exit and (pos > 0.0 or opp_pos > 0.0)
    should_set = force_action or target_delta >= min_target_delta or opp_delta >= min_target_delta

    actions = []

    def set_pos(side, pct, desc):
        action = {
            "type": "SETPOS",
            "side": _norm_side(side),
            "target_pct": float(_clamp(pct, 0.0, 1.0)),
            "leg": 0,
            "desc": desc,
            "reason": signal,
        }
        if leg_uid:
            action["leg_uid"] = leg_uid
        actions.append(action)

    if should_set:
        set_pos(fact_side, target, f"Set {fact_side} target to {target:.4f} ({signal}).")
        set_pos(opp_side, opp_target, f"Keep opposite side {opp_side} flat.")

    if (
        take_profit_order_mode in ("maker_post_only_buy", "maker_post_only_sell")
        and quote_ok
        and not protective_exit
        and pos > 0.0
        and take_profit_pct > 0.0
        and not opposite_take_profit_locked
    ):
        base_price = avg_price if avg_price > 0.0 else entry_price
        tp_price = _clamp(round(base_price * (1.0 + take_profit_pct), 3), 0.001, 0.999)
        if take_profit_order_mode == "maker_post_only_buy":
            order_outcome = opp_side
            order_side = "BUY"
            order_price = _clamp(round(1.0 - tp_price, 3), 0.001, 0.999)
            order_qty = max(0.0, qty - opp_qty - opp_open_buy_qty)
            active_tp = opp_tp_order_status in ("open", "partially_filled") and opp_tp_order_qty > 0.0
            existing_price = opp_tp_order_price
            existing_qty = opp_tp_order_qty
            reduce_only = False
            desc_side = f"BUY {opp_side}"
        else:
            order_outcome = fact_side
            order_side = "SELL"
            order_price = tp_price
            order_qty = max(0.0, min(qty, available_sell_qty))
            active_tp = tp_order_status in ("open", "partially_filled") and tp_order_qty > 0.0
            existing_price = tp_order_price
            existing_qty = tp_order_qty
            reduce_only = True
            desc_side = f"SELL {fact_side}"
        needs_reprice = active_tp and abs(existing_price - order_price) > take_profit_reprice_threshold
        needs_resize = active_tp and abs(existing_qty - order_qty) > max(take_profit_min_qty, order_qty * 0.01)
        if base_price > 0.0 and order_qty >= take_profit_min_qty and (not active_tp or needs_reprice or needs_resize):
            order_action = {
                "type": "REPLACE_ORDER" if active_tp else "PLACE_ORDER",
                "leg": 0,
                "outcome": order_outcome,
                "side": order_side,
                "qty": float(order_qty),
                "price": float(order_price),
                "order_type": take_profit_order_tif,
                "post_only": True,
                "reduce_only": reduce_only,
                "client_order_tag": "take_profit",
                "replace_policy": "same_tag",
                "reason": "preplaced_take_profit",
                "desc": f"Pre-place maker take-profit {desc_side} {order_qty:.4f} @ {order_price:.3f}.",
            }
            if leg_uid:
                order_action["leg_uid"] = leg_uid
            actions.append(order_action)
            notes.append(
                f"maker take-profit {desc_side} price={order_price:.3f}, qty={order_qty:.4f}, "
                f"fact-side target sell price={tp_price:.3f}."
            )

    if controls["debug_raw_inputs"]:
        notes.append("raw_params=" + _stable_json_dumps(raw_params))
        notes.append("raw_usedata=" + _stable_json_dumps(raw_map))

    cooldown_until = None
    state_updates = {
        "last_signal": signal,
        "last_target": float(target),
    }
    if should_set:
        state_updates["last_action_at"] = now_iso
        if cooldown_seconds > 0:
            cooldown_until = now_dt + timedelta(seconds=cooldown_seconds)
            state_updates["cooldown_until"] = _iso(cooldown_until)
        if target <= 0.0:
            state_updates["entry_price"] = None
            state_updates["entry_side"] = None
        elif target > pos and quote_ok:
            state_updates["entry_price"] = float(ask)
            state_updates["entry_side"] = fact_side
    elif pos <= 0.0:
        state_updates["entry_price"] = None
        state_updates["entry_side"] = None

    decision = "SETPOS" if should_set else "HOLD"
    metrics = {
        "decision": decision,
        "signal": signal,
        "fact_side": fact_side,
        "opp_side": opp_side,
        "fair_price": fair_price,
        "ask": ask,
        "bid": bid,
        "opp_bid": opp_bid,
        "spread": spread,
        "edge_to_buy": edge_to_buy,
        "edge_to_hold": edge_to_hold,
        "entry_edge": entry_edge,
        "exit_edge": exit_edge,
        "current_pos": pos,
        "current_pos_source": pos_source,
        "opposite_pos": opp_pos,
        "opposite_pos_source": opp_pos_source,
        "target": target,
        "target_delta": target_delta,
        "min_target_delta": min_target_delta,
        "max_position_pct": max_position_pct,
        "risk_scale": controls["risk_scale"],
        "avg_price": avg_price,
        "entry_price": entry_price if entry_price > 0.0 else None,
        "pnl_pct": pnl_pct,
        "take_profit_order_mode": take_profit_order_mode,
        "take_profit_order_price": tp_order_price if tp_order_price > 0 else None,
        "take_profit_order_qty": tp_order_qty,
        "opposite_take_profit_order_price": opp_tp_order_price if opp_tp_order_price > 0 else None,
        "opposite_take_profit_order_qty": opp_tp_order_qty,
        "opposite_take_profit_locked": opposite_take_profit_locked,
        "day_to_end": day_to_end,
        "de_risk_days": de_risk_days,
        "cooldown_active": cooldown_active,
        "cooldown_until": _iso(cooldown_until_dt) if cooldown_until_dt is not None else None,
        "quote_ok": quote_ok,
        "now": now_iso,
    }

    summary = [
        f"[INPUT] FactSide={fact_side}, fair_price={fair_price:.4f}, ask={ask}, bid={bid}",
        f"[EDGE] entry_edge={entry_edge:.4f}, exit_edge={exit_edge:.4f}, edge_to_buy={edge_to_buy}, edge_to_hold={edge_to_hold}",
        f"[POSITION] current={pos:.4f}, opposite={opp_pos:.4f}, target={target:.4f}, max={max_position_pct:.4f}",
        f"[RISK] pnl_pct={pnl_pct}, day_to_end={day_to_end}, risk_scale={controls['risk_scale']:.4f}, cooldown_active={cooldown_active}",
        f"[DECISION] {decision}, signal={signal}",
    ]
    if notes:
        summary.extend("[NOTE] " + note for note in notes)

    return {
        "schema_version": "2.0",
        "actions": actions,
        "metrics": metrics,
        "print": summary,
        "wake_reason": None,
        "state_updates": state_updates,
    }


def run_node(node):
    try:
        node_inputs = node.get("Inputs") or []
        usedata_raw = _read_input_value(node_inputs, 0)
        usedata_dict, err = _parse_usedata(usedata_raw)
        if err:
            out_json = {
                "schema_version": "2.0",
                "actions": [],
                "print": [f"[UseDataError] {err}"],
                "wake_reason": None,
            }
            Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
            Outputs[1]["Boolean"] = False
            return Outputs

        node_params = {
            "FactSide": _read_input_value(node_inputs, 1),
            "fair_price": _read_input_value(node_inputs, 2),
            "entry_edge": _read_input_value(node_inputs, 3),
            "exit_edge": _read_input_value(node_inputs, 4),
            "max_position_pct": _read_input_value(node_inputs, 5),
            "stop_loss_pct": _read_input_value(node_inputs, 6),
            "take_profit_pct": _read_input_value(node_inputs, 7),
            "de_risk_days": _read_input_value(node_inputs, 8),
            "cooldown_seconds": _read_input_value(node_inputs, 9),
            "min_target_delta": _read_input_value(node_inputs, 10),
        }
        out_json = _run_strategy(_UseDataProxy(usedata_dict), node_params)
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = True
        return Outputs
    except Exception as exc:
        out_json = {
            "schema_version": "2.0",
            "actions": [],
            "print": [f"[RuntimeError] {type(exc).__name__}: {exc}"],
            "wake_reason": None,
        }
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = False
        return Outputs
