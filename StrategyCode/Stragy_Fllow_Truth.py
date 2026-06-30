import ast
import json
import re
from datetime import datetime, timedelta, timezone


OutPutNum = 2
InPutNum = 33

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
    "full_entry_edge": {
        "type": "number",
        "default": 0.16,
        "min": 0.0001,
        "max": 1.0,
        "label": "满仓边际",
        "description": "fair_price - ask 达到该值时，基础目标仓位升至 100% 占用资金。",
    },
    "starter_position_ratio": {
        "type": "number",
        "default": 0.25,
        "min": 0.0,
        "max": 1.0,
        "label": "试仓比例",
        "description": "刚达到 entry_edge 时，基础目标使用的占用资金比例。",
    },
    "exit_edge": {
        "type": "number",
        "default": 0.01,
        "min": -1.0,
        "max": 1.0,
        "label": "离场边际",
        "description": "fair_price - bid 低于该值时退出事实方向仓位。",
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
    "use_momentum_exit_filter": {
        "type": "bool",
        "default": True,
        "label": "启用动量退出过滤",
        "description": "止盈或边际退出触发时，先用 MACD 动量判断是否继续持有。",
    },
    "macd_fast": {
        "type": "integer",
        "default": 6,
        "min": 2,
        "max": 60,
        "label": "MACD 快线",
        "description": "运行时 UseData MACD 的快线周期；短事件行情默认使用快速参数。",
    },
    "macd_slow": {
        "type": "integer",
        "default": 13,
        "min": 3,
        "max": 120,
        "label": "MACD 慢线",
        "description": "运行时 UseData MACD 的慢线周期，必须大于快线。",
    },
    "macd_signal": {
        "type": "integer",
        "default": 5,
        "min": 2,
        "max": 60,
        "label": "MACD 信号线",
        "description": "运行时 UseData MACD 的信号线周期。",
    },
    "momentum_hist_min": {
        "type": "number",
        "default": 0.003,
        "min": -1.0,
        "max": 1.0,
        "label": "动量柱最小值",
        "description": "MACD histogram 高于该值才认为动量足够强。",
    },
    "momentum_slope_min": {
        "type": "number",
        "default": 0.0005,
        "min": -1.0,
        "max": 1.0,
        "label": "动量柱斜率最小值",
        "description": "MACD histogram 较上一根继续扩大到该幅度，才认为趋势仍在加速。",
    },
    "weak_up_entry_multiplier": {
        "type": "number",
        "default": 0.85,
        "min": 0.0,
        "max": 1.0,
        "label": "弱上涨入场系数",
        "description": "MACD 仍为正但不强时，基础目标仓位乘以该系数。",
    },
    "neutral_entry_multiplier": {
        "type": "number",
        "default": 0.70,
        "min": 0.0,
        "max": 1.0,
        "label": "中性入场系数",
        "description": "MACD 方向不明时，基础目标仓位乘以该系数。",
    },
    "missing_macd_entry_multiplier": {
        "type": "number",
        "default": 0.70,
        "min": 0.0,
        "max": 1.0,
        "label": "缺失 MACD 入场系数",
        "description": "MACD 数据缺失时，基础目标仓位乘以该系数。",
    },
    "momentum_hold_min_seconds": {
        "type": "integer",
        "default": 300,
        "min": 0,
        "label": "动量最短持有秒数",
        "description": "进入动量持有后，在该时间内不会因普通止盈或边际消失退出。",
    },
    "trailing_stop_pct": {
        "type": "number",
        "default": 0.12,
        "min": 0.0,
        "max": 1.0,
        "label": "强动量追踪止盈",
        "description": "利润保护后，强动量状态允许从 peak bid 回撤的比例。",
    },
    "weak_trailing_stop_pct": {
        "type": "number",
        "default": 0.08,
        "min": 0.0,
        "max": 1.0,
        "label": "弱动量追踪止盈",
        "description": "利润保护后，动量不强时允许从 peak bid 回撤的比例。",
    },
    "partial_take_profit_ratio": {
        "type": "number",
        "default": 0.35,
        "min": 0.0,
        "max": 1.0,
        "label": "部分止盈比例",
        "description": "触发止盈但动量仍为正时，首次卖出的当前仓位比例。",
    },
    "core_position_ratio": {
        "type": "number",
        "default": 0.35,
        "min": 0.0,
        "max": 1.0,
        "label": "核心仓保留比例",
        "description": "边际消失但动量仍为正时，保留的当前仓位比例。",
    },
    "de_risk_start_days": {
        "type": "number",
        "default": 5.0,
        "min": 0.0,
        "label": "到期降风险天数",
        "description": "距离结束不足该天数时开始线性降低目标仓位。",
    },
    "no_add_days": {
        "type": "number",
        "default": 1.0,
        "min": 0.0,
        "label": "到期禁止加仓天数",
        "description": "距离结束不足该天数时，只允许减仓或退出，不自动开仓/加仓。",
    },
    "min_time_position_ratio": {
        "type": "number",
        "default": 0.0,
        "min": 0.0,
        "max": 1.0,
        "label": "最低时间仓位系数",
        "description": "到期降风险时保留的最低时间系数；0 表示可随时间降至 0。",
    },
    "shock_lookback_minutes": {
        "type": "number",
        "default": 30.0,
        "min": 0.0,
        "label": "短跌回看分钟",
        "description": "用最近该窗口内的 peak bid 判断是否发生短期大跌。",
    },
    "shock_drop_pct": {
        "type": "number",
        "default": 0.15,
        "min": 0.0,
        "max": 1.0,
        "label": "短跌保护跌幅",
        "description": "从短期 peak bid 回撤达到该比例且 MACD 不强时，禁止自动开仓/加仓。",
    },
    "shock_cooldown_minutes": {
        "type": "number",
        "default": 60.0,
        "min": 0.0,
        "label": "短跌冷却分钟",
        "description": "短期大跌触发后，禁止自动开仓/加仓的冷却时间。",
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

_UI_HIDDEN_PARAMS = {
    "take_profit_order_mode",
    "take_profit_order_tif",
    "take_profit_reprice_threshold",
    "take_profit_min_qty",
    "use_momentum_exit_filter",
    "macd_fast",
    "macd_slow",
    "macd_signal",
    "momentum_hist_min",
    "momentum_slope_min",
    "weak_up_entry_multiplier",
    "neutral_entry_multiplier",
    "missing_macd_entry_multiplier",
    "momentum_hold_min_seconds",
    "weak_trailing_stop_pct",
    "partial_take_profit_ratio",
    "core_position_ratio",
    "min_time_position_ratio",
    "shock_lookback_minutes",
    "shock_cooldown_minutes",
    "cooldown_seconds",
}
for _param_name in _UI_HIDDEN_PARAMS:
    if _param_name in ParamsSchema:
        ParamsSchema[_param_name]["hidden"] = True
        ParamsSchema[_param_name]["internal"] = True

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
    "peak_bid": {
        "type": "number",
        "default": None,
        "label": "持仓最高买价",
        "description": "持仓后观察到的最高 bid，用于追踪止盈。",
    },
    "momentum_hold_since": {
        "type": "string",
        "default": None,
        "label": "动量持有开始时间",
        "description": "触发止盈或边际退出但 MACD 强势时进入动量持有的时间。",
    },
    "profit_protected": {
        "type": "bool",
        "default": False,
        "label": "利润保护状态",
        "description": "是否已经从普通持仓进入止盈后的利润保护状态。",
    },
    "partial_tp_done": {
        "type": "bool",
        "default": False,
        "label": "已部分止盈",
        "description": "是否已经执行过首次部分止盈。",
    },
    "last_macd_hist": {
        "type": "number",
        "default": None,
        "label": "上次 MACD 柱",
        "description": "上一轮策略看到的 MACD histogram，用于缺少 HistPrev 时计算斜率。",
    },
    "recent_peak_bid": {
        "type": "number",
        "default": None,
        "label": "短期最高买价",
        "description": "用于短期大跌保护的窗口内最高事实方向 bid。",
    },
    "recent_peak_bid_at": {
        "type": "string",
        "default": None,
        "label": "短期最高买价时间",
        "description": "recent_peak_bid 对应的观测时间。",
    },
    "shock_cooldown_until": {
        "type": "string",
        "default": None,
        "label": "短跌冷却结束时间",
        "description": "短期大跌触发后禁止自动开仓/加仓到该时间。",
    },
    "last_valid_day_to_end": {
        "type": "number",
        "default": None,
        "label": "上次有效剩余天数",
        "description": "最近一次可信的 day_to_end，用于过滤临时缺失导致的 0 天异常。",
    },
    "last_valid_day_to_end_at": {
        "type": "string",
        "default": None,
        "label": "上次有效剩余天数时间",
        "description": "last_valid_day_to_end 对应的观测时间。",
    },
}

StateMachineSchema = {
    "default": "auto",
    "label": "Strategy State",
    "description": "Human-visible strategy-machine state. It is separate from Stop/Virtual/Real mode.",
    "states": [
        {"value": "auto", "label": "Auto"},
        {"value": "idle", "label": "Idle"},
        {"value": "holding", "label": "Holding"},
        {"value": "cooldown", "label": "Cooldown"},
        {"value": "manual_review", "label": "Manual Review"},
        {"value": "stop_loss_locked", "label": "Stop Loss Locked", "requires_user_ack": True},
    ],
}

FunctionIntroduction = (
    "组件功能：Stragy_Fllow_Truth。\n\n"
    "核心逻辑：用户给出事实方向和公平价格；策略用公平价格计算估值边际，"
    "用 MACD 判断能否开仓/加仓以及盈利后是否继续持有，用到期时间和短期大跌保护控制风险：\n"
    "1. edge_to_buy = fair_price - ask，决定基础目标仓位；\n"
    "2. MACD strong_up/weak_up/flat/down 决定基础仓位乘数；flat 表示动量偏弱但未出现短期大跌；\n"
    "3. 临近到期时线性降风险，极近到期时禁止自动加仓；\n"
    "4. 短期大跌且 MACD 不强时禁止自动开仓/加仓；\n"
    "5. 止盈/边际退出触发时，MACD 强则 momentum_hold，转弱则部分止盈或降核心仓。\n\n"
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
_set_input(5, "max_position_pct", "Num", 1.0, "Legacy cap; prefer strategy budget plus target_pct.")
_set_input(6, "stop_loss_pct", "Num", ParamsSchema["stop_loss_pct"]["default"], "Stop loss from entry.")
_set_input(7, "take_profit_pct", "Num", ParamsSchema["take_profit_pct"]["default"], "Take profit from entry.")
_set_input(8, "de_risk_days", "Num", ParamsSchema["de_risk_start_days"]["default"], "Legacy de-risk window.")
_set_input(9, "cooldown_seconds", "Num", ParamsSchema["cooldown_seconds"]["default"], "Cooldown after action.")
_set_input(10, "min_target_delta", "Num", ParamsSchema["min_target_delta"]["default"], "Minimum rebalance delta.")
_set_input(11, "use_momentum_exit_filter", "Boolean", ParamsSchema["use_momentum_exit_filter"]["default"], "Use MACD to delay exits.")
_set_input(12, "macd_fast", "Num", ParamsSchema["macd_fast"]["default"], "MACD fast period.")
_set_input(13, "macd_slow", "Num", ParamsSchema["macd_slow"]["default"], "MACD slow period.")
_set_input(14, "macd_signal", "Num", ParamsSchema["macd_signal"]["default"], "MACD signal period.")
_set_input(15, "momentum_hist_min", "Num", ParamsSchema["momentum_hist_min"]["default"], "Minimum MACD histogram for strong momentum.")
_set_input(16, "momentum_slope_min", "Num", ParamsSchema["momentum_slope_min"]["default"], "Minimum MACD histogram slope.")
_set_input(17, "momentum_hold_min_seconds", "Num", ParamsSchema["momentum_hold_min_seconds"]["default"], "Minimum momentum hold time.")
_set_input(18, "trailing_stop_pct", "Num", ParamsSchema["trailing_stop_pct"]["default"], "Trailing stop from peak bid in strong momentum.")
_set_input(19, "weak_trailing_stop_pct", "Num", ParamsSchema["weak_trailing_stop_pct"]["default"], "Trailing stop from peak bid when momentum weakens.")
_set_input(20, "partial_take_profit_ratio", "Num", ParamsSchema["partial_take_profit_ratio"]["default"], "Current-position ratio to sell on first partial take-profit.")
_set_input(21, "core_position_ratio", "Num", ParamsSchema["core_position_ratio"]["default"], "Core position ratio kept on edge exit while momentum is positive.")
_set_input(22, "full_entry_edge", "Num", ParamsSchema["full_entry_edge"]["default"], "Edge where base target reaches full budget.")
_set_input(23, "starter_position_ratio", "Num", ParamsSchema["starter_position_ratio"]["default"], "Base target when entry edge is first met.")
_set_input(24, "weak_up_entry_multiplier", "Num", ParamsSchema["weak_up_entry_multiplier"]["default"], "Entry/add multiplier when MACD is weak-up.")
_set_input(25, "neutral_entry_multiplier", "Num", ParamsSchema["neutral_entry_multiplier"]["default"], "Entry/add multiplier when MACD is neutral.")
_set_input(26, "missing_macd_entry_multiplier", "Num", ParamsSchema["missing_macd_entry_multiplier"]["default"], "Entry/add multiplier when MACD is missing.")
_set_input(27, "de_risk_start_days", "Num", ParamsSchema["de_risk_start_days"]["default"], "Days before expiry to start de-risking.")
_set_input(28, "no_add_days", "Num", ParamsSchema["no_add_days"]["default"], "Days before expiry to block opening/increasing.")
_set_input(29, "min_time_position_ratio", "Num", ParamsSchema["min_time_position_ratio"]["default"], "Minimum time de-risk multiplier.")
_set_input(30, "shock_lookback_minutes", "Num", ParamsSchema["shock_lookback_minutes"]["default"], "Recent drop lookback window.")
_set_input(31, "shock_drop_pct", "Num", ParamsSchema["shock_drop_pct"]["default"], "Recent drop threshold.")
_set_input(32, "shock_cooldown_minutes", "Num", ParamsSchema["shock_cooldown_minutes"]["default"], "Recent drop cooldown.")

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


def _optional_float(value):
    if value is None:
        return None
    try:
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("_", "")
            if text == "" or text.lower() in ("none", "null", "missing"):
                return None
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        return float(value)
    except Exception:
        return None


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
        "macd": ["MACD", "macd"],
        "macd_signal": ["MACDSignal", "MACD_signal", "macd_signal"],
        "macd_hist": ["MACDHist", "MACDHistogram", "macd_hist", "macd_histogram"],
        "macd_hist_prev": ["MACDHistPrev", "MACDHistPrevious", "macd_hist_prev", "macd_hist_previous"],
        "macd_hist_slope": ["MACDHistSlope", "macd_hist_slope"],
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


def _side_optional_float(usedata, side, field):
    return _optional_float(usedata.get_any(_side_candidates(side, field), None))


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


def _edge_sized_target(edge, entry_edge, full_entry_edge, starter_position_ratio, position_cap_pct=1.0):
    threshold = max(float(entry_edge), 0.0001)
    full_edge = max(float(full_entry_edge), threshold + 0.0001)
    starter = _clamp(starter_position_ratio, 0.0, 1.0)
    cap = _clamp(position_cap_pct, 0.0, 1.0)
    if edge + 1e-12 < threshold:
        return 0.0
    progress = _clamp((edge - threshold) / (full_edge - threshold), 0.0, 1.0)
    target = starter + progress * (1.0 - starter)
    return _clamp(target * cap, 0.0, cap)


def _apply_de_risk(target, day_to_end, de_risk_start_days, min_time_position_ratio=0.0):
    window = max(0.0, float(de_risk_start_days))
    if window <= 0.0:
        return target, 1.0
    days = _to_float(day_to_end, 9999.0)
    if days >= window:
        return target, 1.0
    floor = _clamp(min_time_position_ratio, 0.0, 1.0)
    multiplier = max(floor, _clamp(days / window, 0.0, 1.0))
    return target * multiplier, multiplier


def _market_closed(status):
    text = str(status or "").strip().lower()
    return text in ("closed", "resolved", "settled", "finalized", "ended", "expired")


def _resolve_day_to_end(usedata, runtime, now_dt, quote_ok):
    raw_value = usedata.get_any(("day_to_end", "days_to_end", "DayToEnd", "L0_DayToEnd"), None)
    raw_days = _optional_float(raw_value)
    end_raw = usedata.get_any(("L0_EndTime", "Enddate", "EndDate", "end_date", "endDate"), None)
    end_dt = _parse_dt(end_raw)
    market_status = str(
        usedata.get_any(("L0_MarketStatus", "MarketStatus", "market_status"), "")
        or ""
    ).strip().lower()
    if end_dt is not None:
        return max(0.0, (end_dt - now_dt).total_seconds() / 86400.0), "end_time", False, market_status
    if raw_days is not None:
        suspicious_zero = raw_days <= 0.0 and quote_ok and not _market_closed(market_status)
        if suspicious_zero:
            last_days = _optional_float(runtime.get("last_valid_day_to_end"))
            last_at = _parse_dt(runtime.get("last_valid_day_to_end_at"))
            if last_days is not None and last_days > 0.0:
                elapsed_days = 0.0
                if last_at is not None:
                    elapsed_days = max(0.0, (now_dt - last_at).total_seconds() / 86400.0)
                return max(0.0, last_days - elapsed_days), "runtime_fallback", True, market_status
            return 9999.0, "missing_fallback", True, market_status
        return max(0.0, raw_days), "raw", False, market_status
    return 9999.0, "missing_fallback", True, market_status


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

    legacy_cap_raw = None
    for source in (data_params, node_params or {}):
        if isinstance(source, dict) and source.get("max_position_pct") not in (None, ""):
            legacy_cap_raw = source.get("max_position_pct")
    legacy_position_cap_pct = 1.0 if legacy_cap_raw is None else _clamp(_to_float(legacy_cap_raw, 1.0), 0.0, 1.0)

    entry_edge = _clamp(_to_float(merged.get("entry_edge"), defaults["entry_edge"]), 0.0, 1.0)
    full_entry_edge = _clamp(
        _to_float(merged.get("full_entry_edge"), defaults["full_entry_edge"]),
        0.0001,
        1.0,
    )
    if full_entry_edge <= entry_edge:
        full_entry_edge = min(1.0, max(entry_edge + 0.0001, entry_edge * 2.0))

    de_risk_start_raw = merged.get("de_risk_start_days")
    if de_risk_start_raw in (None, ""):
        de_risk_start_raw = merged.get("de_risk_days", defaults["de_risk_start_days"])

    out = {
        "FactSide": _norm_side(merged.get("FactSide", defaults["FactSide"])),
        "fair_price": _clamp(_to_float(merged.get("fair_price"), defaults["fair_price"]), 0.0001, 0.9999),
        "entry_edge": entry_edge,
        "full_entry_edge": full_entry_edge,
        "starter_position_ratio": _clamp(
            _to_float(merged.get("starter_position_ratio"), defaults["starter_position_ratio"]),
            0.0,
            1.0,
        ),
        "exit_edge": _clamp(_to_float(merged.get("exit_edge"), defaults["exit_edge"]), -1.0, 1.0),
        "legacy_position_cap_pct": legacy_position_cap_pct,
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
        "use_momentum_exit_filter": _to_bool(
            merged.get("use_momentum_exit_filter"),
            defaults["use_momentum_exit_filter"],
        ),
        "macd_fast": _clamp(_to_int(merged.get("macd_fast"), defaults["macd_fast"]), 2, 60),
        "macd_slow": _clamp(_to_int(merged.get("macd_slow"), defaults["macd_slow"]), 3, 120),
        "macd_signal": _clamp(_to_int(merged.get("macd_signal"), defaults["macd_signal"]), 2, 60),
        "momentum_hist_min": _clamp(
            _to_float(merged.get("momentum_hist_min"), defaults["momentum_hist_min"]),
            -1.0,
            1.0,
        ),
        "momentum_slope_min": _clamp(
            _to_float(merged.get("momentum_slope_min"), defaults["momentum_slope_min"]),
            -1.0,
            1.0,
        ),
        "weak_up_entry_multiplier": _clamp(
            _to_float(merged.get("weak_up_entry_multiplier"), defaults["weak_up_entry_multiplier"]),
            0.75,
            1.0,
        ),
        "neutral_entry_multiplier": _clamp(
            _to_float(merged.get("neutral_entry_multiplier"), defaults["neutral_entry_multiplier"]),
            0.70,
            1.0,
        ),
        "missing_macd_entry_multiplier": _clamp(
            _to_float(merged.get("missing_macd_entry_multiplier"), defaults["missing_macd_entry_multiplier"]),
            0.50,
            1.0,
        ),
        "momentum_hold_min_seconds": max(
            0,
            _to_int(merged.get("momentum_hold_min_seconds"), defaults["momentum_hold_min_seconds"]),
        ),
        "trailing_stop_pct": _clamp(
            _to_float(merged.get("trailing_stop_pct"), defaults["trailing_stop_pct"]),
            0.0,
            1.0,
        ),
        "weak_trailing_stop_pct": _clamp(
            _to_float(merged.get("weak_trailing_stop_pct"), defaults["weak_trailing_stop_pct"]),
            0.07,
            1.0,
        ),
        "partial_take_profit_ratio": _clamp(
            _to_float(merged.get("partial_take_profit_ratio"), defaults["partial_take_profit_ratio"]),
            0.0,
            1.0,
        ),
        "core_position_ratio": _clamp(
            _to_float(merged.get("core_position_ratio"), defaults["core_position_ratio"]),
            0.0,
            1.0,
        ),
        "de_risk_start_days": max(0.0, _to_float(de_risk_start_raw, defaults["de_risk_start_days"])),
        "no_add_days": max(0.0, _to_float(merged.get("no_add_days"), defaults["no_add_days"])),
        "min_time_position_ratio": _clamp(
            _to_float(merged.get("min_time_position_ratio"), defaults["min_time_position_ratio"]),
            0.0,
            1.0,
        ),
        "shock_lookback_minutes": max(
            0.0,
            _to_float(merged.get("shock_lookback_minutes"), defaults["shock_lookback_minutes"]),
        ),
        "shock_drop_pct": _clamp(
            _to_float(merged.get("shock_drop_pct"), defaults["shock_drop_pct"]),
            0.0,
            1.0,
        ),
        "shock_cooldown_minutes": max(
            0.0,
            _to_float(merged.get("shock_cooldown_minutes"), defaults["shock_cooldown_minutes"]),
        ),
        "cooldown_seconds": max(0, _to_int(merged.get("cooldown_seconds"), defaults["cooldown_seconds"])),
        "min_target_delta": _clamp(
            _to_float(merged.get("min_target_delta"), defaults["min_target_delta"]),
            0.0,
            1.0,
        ),
    }
    out["macd_fast"] = int(out["macd_fast"])
    out["macd_slow"] = int(max(out["macd_fast"] + 1, out["macd_slow"]))
    out["macd_signal"] = int(out["macd_signal"])
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


def _build_machine_state(usedata):
    raw_state = usedata.get("MachineState", None)
    if raw_state is None:
        strategy_state = _as_dict(usedata.get("StrategyState", {}))
        raw_state = strategy_state.get("state")
    state = str(raw_state or StateMachineSchema["default"]).strip().lower()
    return state or StateMachineSchema["default"]


def _run_strategy(usedata, node_params):
    raw_map = usedata.to_dict()
    params, raw_params = _build_params(usedata, node_params or {})
    controls = _build_controls(usedata)
    runtime = _build_runtime_state(usedata)
    machine_state = _build_machine_state(usedata)
    stop_loss_locked = machine_state == "stop_loss_locked"
    now_dt = _pick_now(raw_map)
    now_iso = _iso(now_dt)

    fact_side = params["FactSide"]
    opp_side = "No" if fact_side == "Yes" else "Yes"

    fair_price = params["fair_price"]
    entry_edge = params["entry_edge"]
    full_entry_edge = params["full_entry_edge"]
    starter_position_ratio = params["starter_position_ratio"]
    exit_edge = params["exit_edge"]
    legacy_position_cap_pct = params["legacy_position_cap_pct"]
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
    use_momentum_exit_filter = params["use_momentum_exit_filter"]
    macd_fast = params["macd_fast"]
    macd_slow = params["macd_slow"]
    macd_signal_period = params["macd_signal"]
    momentum_hist_min = params["momentum_hist_min"]
    momentum_slope_min = params["momentum_slope_min"]
    weak_up_entry_multiplier = params["weak_up_entry_multiplier"]
    neutral_entry_multiplier = params["neutral_entry_multiplier"]
    missing_macd_entry_multiplier = params["missing_macd_entry_multiplier"]
    momentum_hold_min_seconds = params["momentum_hold_min_seconds"]
    trailing_stop_pct = params["trailing_stop_pct"]
    weak_trailing_stop_pct = params["weak_trailing_stop_pct"]
    partial_take_profit_ratio = params["partial_take_profit_ratio"]
    core_position_ratio = params["core_position_ratio"]
    de_risk_start_days = params["de_risk_start_days"]
    no_add_days = params["no_add_days"]
    min_time_position_ratio = params["min_time_position_ratio"]
    shock_lookback_minutes = params["shock_lookback_minutes"]
    shock_drop_pct = params["shock_drop_pct"]
    shock_cooldown_minutes = params["shock_cooldown_minutes"]
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
    position_cap_pct = min(legacy_position_cap_pct, cap)

    quote_ok = _is_price(ask) and _is_price(bid) and bid <= ask
    day_to_end, day_to_end_source, day_to_end_guarded, market_status = _resolve_day_to_end(
        usedata,
        runtime,
        now_dt,
        quote_ok,
    )
    time_notes = []
    if day_to_end_guarded:
        time_notes.append(
            f"ignored suspicious/missing day_to_end; source={day_to_end_source}, "
            f"effective_day_to_end={day_to_end:.4f}."
        )
    spread = None if not quote_ok else ask - bid
    edge_to_buy = None if not quote_ok else fair_price - ask
    edge_to_hold = None if not quote_ok else fair_price - bid
    macd_value = _side_optional_float(usedata, fact_side, "macd")
    macd_signal_value = _side_optional_float(usedata, fact_side, "macd_signal")
    macd_hist = _side_optional_float(usedata, fact_side, "macd_hist")
    macd_hist_prev = _side_optional_float(usedata, fact_side, "macd_hist_prev")
    if macd_hist_prev is None:
        macd_hist_prev = _optional_float(runtime.get("last_macd_hist"))
    macd_hist_slope = None
    if macd_hist is not None and macd_hist_prev is not None:
        macd_hist_slope = macd_hist - macd_hist_prev
    momentum_available = macd_hist is not None
    momentum_positive = bool(use_momentum_exit_filter and momentum_available and macd_hist > 0.0)
    momentum_strong = bool(
        use_momentum_exit_filter
        and momentum_available
        and macd_hist_slope is not None
        and macd_hist >= momentum_hist_min
        and macd_hist_slope >= momentum_slope_min
    )
    momentum_broken = False
    momentum_down = bool(
        use_momentum_exit_filter
        and momentum_available
        and (
            macd_hist <= 0.0
            or (macd_hist_slope is not None and macd_hist_slope <= -abs(momentum_slope_min))
        )
    )
    if not use_momentum_exit_filter:
        momentum_state = "disabled"
        entry_momentum_multiplier = 1.0
    elif not momentum_available:
        momentum_state = "missing"
        entry_momentum_multiplier = missing_macd_entry_multiplier
    elif momentum_strong:
        momentum_state = "strong_up"
        entry_momentum_multiplier = 1.0
    elif momentum_positive:
        momentum_state = "weak_up"
        entry_momentum_multiplier = weak_up_entry_multiplier
    elif momentum_down:
        momentum_state = "down"
        entry_momentum_multiplier = 0.0
    else:
        momentum_state = "neutral"
        entry_momentum_multiplier = neutral_entry_multiplier

    entry_price = _to_float(runtime.get("entry_price"), 0.0)
    if entry_price <= 0.0:
        entry_price = avg_price
    pnl_pct = None
    if pos > 0.0 and entry_price > 0.0 and _is_price(bid):
        pnl_pct = bid / entry_price - 1.0

    cooldown_until_dt = _parse_dt(runtime.get("cooldown_until"))
    cooldown_active = cooldown_until_dt is not None and now_dt < cooldown_until_dt
    profit_protected = _to_bool(runtime.get("profit_protected"), False)
    partial_tp_done = _to_bool(runtime.get("partial_tp_done"), False)
    momentum_hold_since_dt = _parse_dt(runtime.get("momentum_hold_since"))
    peak_bid = _optional_float(runtime.get("peak_bid"))
    peak_bid_before_update = peak_bid
    position_made_new_high = False
    if pos <= 0.0:
        profit_protected = False
        partial_tp_done = False
        momentum_hold_since_dt = None
        peak_bid = None
    elif quote_ok and _is_price(bid):
        position_made_new_high = peak_bid_before_update is not None and bid > peak_bid_before_update + 1e-12
        peak_bid = max(peak_bid or bid, bid)

    drawdown_from_peak = None
    if pos > 0.0 and peak_bid and peak_bid > 0.0 and _is_price(bid):
        drawdown_from_peak = max(0.0, 1.0 - bid / peak_bid)
    active_trailing_stop_pct = trailing_stop_pct if momentum_strong else weak_trailing_stop_pct
    trailing_stop_triggered = bool(
        profit_protected
        and drawdown_from_peak is not None
        and active_trailing_stop_pct > 0.0
        and drawdown_from_peak >= active_trailing_stop_pct
    )
    momentum_hold_age_seconds = None
    if momentum_hold_since_dt is not None:
        momentum_hold_age_seconds = max(0.0, (now_dt - momentum_hold_since_dt).total_seconds())
    momentum_min_hold_active = bool(
        profit_protected
        and momentum_hold_since_dt is not None
        and momentum_hold_min_seconds > 0
        and momentum_hold_age_seconds is not None
        and momentum_hold_age_seconds < momentum_hold_min_seconds
    )

    last_bid = _optional_float(runtime.get("last_bid"))
    bid_change = None
    if quote_ok and _is_price(bid) and last_bid is not None and last_bid > 0.0:
        bid_change = bid - last_bid
    price_recovery_signal = False
    recent_peak_reset = False
    recent_made_new_high = False
    price_recovery_min_move = 0.01
    recent_peak_bid = _optional_float(runtime.get("recent_peak_bid"))
    recent_peak_bid_at_dt = _parse_dt(runtime.get("recent_peak_bid_at"))
    shock_cooldown_until_dt = _parse_dt(runtime.get("shock_cooldown_until"))
    recent_drop_from_peak = None
    shock_window_seconds = shock_lookback_minutes * 60.0
    if quote_ok and _is_price(bid) and shock_window_seconds > 0:
        recent_peak_expired = (
            recent_peak_bid_at_dt is None
            or (now_dt - recent_peak_bid_at_dt).total_seconds() > shock_window_seconds
        )
        if recent_peak_bid is None or recent_peak_bid <= 0.0 or recent_peak_expired:
            recent_peak_reset = True
            recent_peak_bid = bid
            recent_peak_bid_at_dt = now_dt
        elif bid >= recent_peak_bid:
            recent_made_new_high = bid > recent_peak_bid + 1e-12
            recent_peak_bid = bid
            recent_peak_bid_at_dt = now_dt
        if recent_peak_bid and recent_peak_bid > 0.0:
            recent_drop_from_peak = max(0.0, 1.0 - bid / recent_peak_bid)
        price_recovery_signal = bool(
            not recent_peak_reset
            and (
                recent_made_new_high
                or (bid_change is not None and bid_change >= price_recovery_min_move)
            )
        )
    shock_triggered = bool(
        quote_ok
        and shock_drop_pct > 0.0
        and recent_drop_from_peak is not None
        and recent_drop_from_peak >= shock_drop_pct
        and momentum_state != "strong_up"
    )
    if shock_triggered and shock_cooldown_minutes > 0.0:
        proposed_shock_until = now_dt + timedelta(minutes=shock_cooldown_minutes)
        if shock_cooldown_until_dt is None or proposed_shock_until > shock_cooldown_until_dt:
            shock_cooldown_until_dt = proposed_shock_until
    shock_cooldown_active = shock_cooldown_until_dt is not None and now_dt < shock_cooldown_until_dt
    shock_block_active = shock_triggered or shock_cooldown_active
    macd_flat_without_shock = False
    if momentum_state == "down" and shock_drop_pct > 0.0:
        meaningful_recent_drop = (
            recent_drop_from_peak is not None
            and recent_drop_from_peak >= shock_drop_pct
        )
        if not meaningful_recent_drop and not shock_cooldown_active:
            if price_recovery_signal:
                momentum_state = "recovery_up"
                entry_momentum_multiplier = weak_up_entry_multiplier
            else:
                momentum_state = "flat"
                entry_momentum_multiplier = neutral_entry_multiplier
            momentum_down = False
            macd_flat_without_shock = True
    momentum_broken = bool(use_momentum_exit_filter and momentum_state == "down")
    price_follow_through = bool(
        use_momentum_exit_filter
        and not shock_block_active
        and (position_made_new_high or price_recovery_signal or momentum_state == "recovery_up")
    )

    target = pos
    signal = "hold"
    protective_exit = False
    base_entry_target = None
    momentum_adjusted_entry_target = None
    de_risk_multiplier = 1.0
    notes = list(time_notes)
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
        take_profit_triggered = (
            take_profit_order_mode == "trigger_exit"
            and pos > 0.0
            and pnl_pct is not None
            and take_profit_pct > 0.0
            and pnl_pct >= take_profit_pct
        )
        edge_exit_triggered = pos > 0.0 and edge_to_hold <= exit_edge

        if opposite_take_profit_locked:
            target = pos
            signal = "take_profit_locked"
            notes.append("opposite buy take-profit appears filled; keep locked complete set.")
        elif pos > 0.0 and pnl_pct is not None and stop_loss_pct > 0.0 and pnl_pct <= -stop_loss_pct:
            target = 0.0
            signal = "stop_loss"
            protective_exit = True
        elif pos > 0.0 and trailing_stop_triggered:
            target = 0.0
            signal = "trailing_stop"
            protective_exit = True
            notes.append(
                f"drawdown_from_peak={drawdown_from_peak:.4f} >= trailing_stop={active_trailing_stop_pct:.4f}."
            )
        elif pos > 0.0 and profit_protected and momentum_broken and not momentum_min_hold_active:
            target = 0.0
            signal = "momentum_break"
            protective_exit = True
        elif take_profit_triggered:
            profit_protected = True
            if use_momentum_exit_filter and momentum_hold_since_dt is not None and momentum_min_hold_active:
                target = pos
                signal = "momentum_hold_min_time"
                notes.append("minimum momentum hold window is active.")
            elif use_momentum_exit_filter and (momentum_strong or price_follow_through):
                target = pos
                signal = "momentum_hold_take_profit" if momentum_strong else "price_recovery_hold_take_profit"
                if momentum_hold_since_dt is None:
                    momentum_hold_since_dt = now_dt
                notes.append("take-profit trigger delayed because price is still making highs or recovering.")
            elif (
                use_momentum_exit_filter
                and (momentum_positive or momentum_state == "flat")
                and not partial_tp_done
                and partial_take_profit_ratio > 0.0
            ):
                target = pos * (1.0 - partial_take_profit_ratio)
                signal = "partial_take_profit"
                partial_tp_done = True
                protective_exit = True
                notes.append("take-profit trigger reduced to partial exit because momentum is not confirmed down.")
            else:
                target = 0.0
                signal = "take_profit"
                protective_exit = True
        elif edge_exit_triggered:
            profit_protected = True
            if use_momentum_exit_filter and momentum_hold_since_dt is not None and momentum_min_hold_active:
                target = pos
                signal = "momentum_hold_min_time"
                notes.append("minimum momentum hold window is active.")
            elif use_momentum_exit_filter and (momentum_strong or price_follow_through):
                target = pos
                signal = "momentum_hold_edge_exit" if momentum_strong else "price_recovery_hold_edge_exit"
                if momentum_hold_since_dt is None:
                    momentum_hold_since_dt = now_dt
                notes.append("edge-exit trigger delayed because price is still making highs or recovering.")
            elif use_momentum_exit_filter and (momentum_positive or momentum_state == "flat") and core_position_ratio > 0.0:
                target = pos * core_position_ratio
                signal = "edge_exit_to_core"
                protective_exit = True
                notes.append("edge exit reduced to core position because momentum is not confirmed down.")
            else:
                target = 0.0
                signal = "edge_exit"
                protective_exit = True
        elif pos <= 0.0:
            base_entry_target = _edge_sized_target(
                edge_to_buy,
                entry_edge,
                full_entry_edge,
                starter_position_ratio,
                position_cap_pct,
            )
            momentum_adjusted_entry_target = base_entry_target * entry_momentum_multiplier
            target = momentum_adjusted_entry_target
            if target > 0.0:
                signal = "entry"
            elif base_entry_target > 0.0:
                signal = "wait_momentum"
                notes.append(f"entry blocked or reduced by MACD state={momentum_state}.")
            else:
                signal = "wait_edge"
        else:
            base_entry_target = _edge_sized_target(
                edge_to_buy,
                entry_edge,
                full_entry_edge,
                starter_position_ratio,
                position_cap_pct,
            )
            momentum_adjusted_entry_target = base_entry_target * entry_momentum_multiplier
            candidate = momentum_adjusted_entry_target
            target = max(pos, candidate)
            target = min(target, position_cap_pct)
            if candidate > pos:
                signal = "add_edge"
            elif base_entry_target > pos and entry_momentum_multiplier < 1.0:
                signal = "hold_momentum_block"
                notes.append(f"add blocked or reduced by MACD state={momentum_state}.")
            else:
                signal = "hold_edge"

        if target > 0.0:
            before_de_risk = target
            target, de_risk_multiplier = _apply_de_risk(
                target,
                day_to_end,
                de_risk_start_days,
                min_time_position_ratio,
            )
            if target < before_de_risk:
                signal = "de_risk" if signal in ("entry", "add_edge", "hold_edge", "wait_edge", "wait_momentum", "hold_momentum_block") else signal
                notes.append(f"de-risk multiplier={de_risk_multiplier:.4f}.")
        else:
            de_risk_multiplier = 1.0

        if shock_block_active and target > pos:
            target = pos
            signal = "shock_block_open" if pos <= 0.0 else "shock_block_add"
            notes.append("recent price shock is active; blocked opening or increasing position.")

        if no_add_days > 0.0 and day_to_end <= no_add_days and target > pos:
            target = pos
            signal = "time_no_add"
            notes.append(f"day_to_end={day_to_end:.4f} <= no_add_days={no_add_days:.4f}; blocked opening or increasing position.")

        if controls["manual_pause_open"] and target > pos:
            target = pos
            signal = "manual_pause_open" if pos <= 0.0 else "manual_pause_increase"

        if day_to_end_guarded and day_to_end_source == "missing_fallback" and target > pos:
            target = pos
            signal = "time_data_guard"
            notes.append("time data is unavailable; blocked opening or increasing position.")

        if cooldown_active and target > pos:
            target = pos
            signal = "cooldown"

        if stop_loss_locked and target > pos:
            target = pos
            signal = "stop_loss_locked" if pos <= 0.0 else "stop_loss_locked_no_add"
            notes.append("Stop Loss Locked state is active; manual state change is required before opening or adding.")

    if not quote_ok:
        de_risk_multiplier = 1.0

    target = _clamp(target * controls["risk_scale"], 0.0, position_cap_pct)
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
    machine_state_updates = {}
    if target <= 0.0:
        state_updates["peak_bid"] = None
        state_updates["momentum_hold_since"] = None
        state_updates["profit_protected"] = False
        state_updates["partial_tp_done"] = False
    else:
        if peak_bid is None and quote_ok and _is_price(bid):
            peak_bid = bid
        state_updates["peak_bid"] = float(peak_bid) if peak_bid is not None else None
        state_updates["momentum_hold_since"] = _iso(momentum_hold_since_dt)
        state_updates["profit_protected"] = bool(profit_protected)
        state_updates["partial_tp_done"] = bool(partial_tp_done)
    if recent_peak_bid is not None:
        state_updates["recent_peak_bid"] = float(recent_peak_bid)
        state_updates["recent_peak_bid_at"] = _iso(recent_peak_bid_at_dt)
    if quote_ok and _is_price(bid):
        state_updates["last_bid"] = float(bid)
    state_updates["shock_cooldown_until"] = _iso(shock_cooldown_until_dt)
    if macd_hist is not None:
        state_updates["last_macd_hist"] = float(macd_hist)
    if (
        not day_to_end_guarded
        and day_to_end_source in ("raw", "end_time")
        and day_to_end > 0.0
        and day_to_end < 9999.0
    ):
        state_updates["last_valid_day_to_end"] = float(day_to_end)
        state_updates["last_valid_day_to_end_at"] = now_iso
    if should_set:
        state_updates["last_action_at"] = now_iso
        if cooldown_seconds > 0:
            cooldown_until = now_dt + timedelta(seconds=cooldown_seconds)
            state_updates["cooldown_until"] = _iso(cooldown_until)
        if target <= 0.0:
            state_updates["entry_price"] = None
            state_updates["entry_side"] = None
            if signal == "stop_loss":
                machine_state_updates["state"] = "stop_loss_locked"
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
        "full_entry_edge": full_entry_edge,
        "starter_position_ratio": starter_position_ratio,
        "exit_edge": exit_edge,
        "current_pos": pos,
        "current_pos_source": pos_source,
        "opposite_pos": opp_pos,
        "opposite_pos_source": opp_pos_source,
        "target": target,
        "target_delta": target_delta,
        "min_target_delta": min_target_delta,
        "position_cap_pct": position_cap_pct,
        "legacy_position_cap_pct": legacy_position_cap_pct,
        "base_entry_target": base_entry_target,
        "entry_momentum_multiplier": entry_momentum_multiplier,
        "momentum_adjusted_entry_target": momentum_adjusted_entry_target,
        "risk_scale": controls["risk_scale"],
        "avg_price": avg_price,
        "entry_price": entry_price if entry_price > 0.0 else None,
        "pnl_pct": pnl_pct,
        "take_profit_order_mode": take_profit_order_mode,
        "use_momentum_exit_filter": use_momentum_exit_filter,
        "macd": macd_value,
        "macd_signal": macd_signal_value,
        "macd_hist": macd_hist,
        "macd_hist_prev": macd_hist_prev,
        "macd_hist_slope": macd_hist_slope,
        "macd_fast": macd_fast,
        "macd_slow": macd_slow,
        "macd_signal_period": macd_signal_period,
        "momentum_available": momentum_available,
        "momentum_positive": momentum_positive,
        "momentum_strong": momentum_strong,
        "momentum_broken": momentum_broken,
        "momentum_down": momentum_down,
        "momentum_state": momentum_state,
        "macd_flat_without_shock": macd_flat_without_shock,
        "last_bid": last_bid,
        "bid_change": bid_change,
        "price_recovery_signal": price_recovery_signal,
        "position_made_new_high": position_made_new_high,
        "price_follow_through": price_follow_through,
        "profit_protected": profit_protected,
        "partial_tp_done": partial_tp_done,
        "peak_bid": peak_bid,
        "drawdown_from_peak": drawdown_from_peak,
        "active_trailing_stop_pct": active_trailing_stop_pct,
        "trailing_stop_triggered": trailing_stop_triggered,
        "momentum_hold_since": _iso(momentum_hold_since_dt),
        "momentum_hold_age_seconds": momentum_hold_age_seconds,
        "take_profit_order_price": tp_order_price if tp_order_price > 0 else None,
        "take_profit_order_qty": tp_order_qty,
        "opposite_take_profit_order_price": opp_tp_order_price if opp_tp_order_price > 0 else None,
        "opposite_take_profit_order_qty": opp_tp_order_qty,
        "opposite_take_profit_locked": opposite_take_profit_locked,
        "day_to_end": day_to_end,
        "day_to_end_source": day_to_end_source,
        "day_to_end_guarded": day_to_end_guarded,
        "de_risk_start_days": de_risk_start_days,
        "no_add_days": no_add_days,
        "min_time_position_ratio": min_time_position_ratio,
        "de_risk_multiplier": de_risk_multiplier,
        "recent_peak_bid": recent_peak_bid,
        "recent_peak_bid_at": _iso(recent_peak_bid_at_dt),
        "recent_drop_from_peak": recent_drop_from_peak,
        "shock_lookback_minutes": shock_lookback_minutes,
        "shock_drop_pct": shock_drop_pct,
        "shock_triggered": shock_triggered,
        "shock_cooldown_active": shock_cooldown_active,
        "shock_cooldown_until": _iso(shock_cooldown_until_dt),
        "shock_block_active": shock_block_active,
        "machine_state": machine_state,
        "stop_loss_locked": stop_loss_locked,
        "market_status": market_status,
        "cooldown_active": cooldown_active,
        "cooldown_until": _iso(cooldown_until_dt) if cooldown_until_dt is not None else None,
        "quote_ok": quote_ok,
        "now": now_iso,
    }

    summary = [
        f"[INPUT] FactSide={fact_side}, fair_price={fair_price:.4f}, ask={ask}, bid={bid}",
        f"[EDGE] entry_edge={entry_edge:.4f}, full_entry_edge={full_entry_edge:.4f}, exit_edge={exit_edge:.4f}, edge_to_buy={edge_to_buy}, edge_to_hold={edge_to_hold}",
        f"[POSITION] current={pos:.4f}, opposite={opp_pos:.4f}, target={target:.4f}, cap={position_cap_pct:.4f}",
        f"[MOMENTUM] state={momentum_state}, macd_hist={macd_hist}, slope={macd_hist_slope}, entry_mult={entry_momentum_multiplier:.4f}, peak_bid={peak_bid}, drawdown={drawdown_from_peak}, price_follow={price_follow_through}",
        f"[RISK] pnl_pct={pnl_pct}, day_to_end={day_to_end}, de_risk_mult={de_risk_multiplier:.4f}, shock_active={shock_block_active}, cooldown_active={cooldown_active}, machine_state={machine_state}",
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
        "machine_state_updates": machine_state_updates,
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
            "use_momentum_exit_filter": _read_input_value(node_inputs, 11),
            "macd_fast": _read_input_value(node_inputs, 12),
            "macd_slow": _read_input_value(node_inputs, 13),
            "macd_signal": _read_input_value(node_inputs, 14),
            "momentum_hist_min": _read_input_value(node_inputs, 15),
            "momentum_slope_min": _read_input_value(node_inputs, 16),
            "momentum_hold_min_seconds": _read_input_value(node_inputs, 17),
            "trailing_stop_pct": _read_input_value(node_inputs, 18),
            "weak_trailing_stop_pct": _read_input_value(node_inputs, 19),
            "partial_take_profit_ratio": _read_input_value(node_inputs, 20),
            "core_position_ratio": _read_input_value(node_inputs, 21),
            "full_entry_edge": _read_input_value(node_inputs, 22),
            "starter_position_ratio": _read_input_value(node_inputs, 23),
            "weak_up_entry_multiplier": _read_input_value(node_inputs, 24),
            "neutral_entry_multiplier": _read_input_value(node_inputs, 25),
            "missing_macd_entry_multiplier": _read_input_value(node_inputs, 26),
            "de_risk_start_days": _read_input_value(node_inputs, 27),
            "no_add_days": _read_input_value(node_inputs, 28),
            "min_time_position_ratio": _read_input_value(node_inputs, 29),
            "shock_lookback_minutes": _read_input_value(node_inputs, 30),
            "shock_drop_pct": _read_input_value(node_inputs, 31),
            "shock_cooldown_minutes": _read_input_value(node_inputs, 32),
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
