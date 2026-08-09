import ast
import datetime as _dt
import difflib
import json
import math
import re

# ============================================================
# Multi-leg Stock Market-Cap Rank Value Strategy for Polymarket
# ------------------------------------------------------------
# 设计目标：
# 1) 一条 leg = 一个 Polymarket binary market / condition_id
# 2) Yes / No 是同一条 leg 内的两个 side，不拆成两条 leg
# 3) 每条 leg 同时评估 Buy Yes 与 Buy No
# 4) 先做交易价值过滤，再做排名概率定价，再做组合预算分配
# 5) 输出 FunctionJson: actions / metrics / print / state_updates
# ============================================================

# ===== 节点定义 =====
OutPutNum = 2
InPutNum = 14

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
        "Kind": "String",
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

# ===== 参数默认值 =====
ParamsSchema = {
    "UniverseSymbols": {
        "type": "string",
        "default": "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA",
        "label": "排名宇宙",
    },
    "TargetRanks": {
        "type": "string",
        "default": "1,2,3",
        "label": "目标排名集合",
    },
    "ModelAnnualVol": {
        "type": "number",
        "default": 0.35,
        "min": 0.01,
        "max": 2.0,
        "label": "默认年化波动率",
    },
    "PairwiseCorr": {
        "type": "number",
        "default": 0.65,
        "min": -0.5,
        "max": 0.99,
        "label": "默认股票相关性",
    },
    "ShockFloor": {
        "type": "number",
        "default": 0.015,
        "min": 0.0,
        "max": 0.20,
        "label": "概率模型最小冲击尺度",
    },
    "OpenEdge": {
        "type": "number",
        "default": 0.045,
        "min": 0.0,
        "max": 1.0,
        "label": "开仓最小 Edge",
    },
    "AddEdge": {
        "type": "number",
        "default": 0.075,
        "min": 0.0,
        "max": 1.0,
        "label": "加仓 Edge",
    },
    "ReduceEdge": {
        "type": "number",
        "default": 0.015,
        "min": -1.0,
        "max": 1.0,
        "label": "减仓 Edge",
    },
    "CloseEdge": {
        "type": "number",
        "default": -0.005,
        "min": -1.0,
        "max": 1.0,
        "label": "清仓 Edge",
    },
    "SwitchEdge": {
        "type": "number",
        "default": 0.10,
        "min": 0.0,
        "max": 1.0,
        "label": "换边 Edge",
    },
    "MaxSpread": {
        "type": "number",
        "default": 0.045,
        "min": 0.0,
        "max": 1.0,
        "label": "最大买卖价差",
    },
    "MaxEntryPrice": {
        "type": "number",
        "default": 0.985,
        "min": 0.01,
        "max": 1.0,
        "label": "最高买入价格",
    },
    "MinAnnualizedRoi": {
        "type": "number",
        "default": 0.20,
        "min": -1.0,
        "max": 20.0,
        "label": "最低年化期望收益",
    },
    "MinAskDepthNotional": {
        "type": "number",
        "default": 5.0,
        "min": 0.0,
        "label": "最小卖盘深度金额",
    },
    "MinDaysToOpen": {
        "type": "number",
        "default": 0.25,
        "min": 0.0,
        "label": "最小开仓剩余天数",
    },
    "MaxDataAgeSec": {
        "type": "number",
        "default": 600.0,
        "min": 0.0,
        "label": "行情最大延迟秒数",
    },
    "MaxPerLegPct": {
        "type": "number",
        "default": 0.18,
        "min": 0.0,
        "max": 1.0,
        "label": "单 leg 最高仓位",
    },
    "MinOpenPct": {
        "type": "number",
        "default": 0.04,
        "min": 0.0,
        "max": 1.0,
        "label": "最低开仓仓位",
    },
    "MaxStepPct": {
        "type": "number",
        "default": 0.08,
        "min": 0.0,
        "max": 1.0,
        "label": "单轮最大调仓幅度",
    },
    "MaxTotalExposurePct": {
        "type": "number",
        "default": 0.80,
        "min": 0.0,
        "max": 3.0,
        "label": "组合总风险上限",
    },
    "MaxCompanyExposurePct": {
        "type": "number",
        "default": 0.32,
        "min": 0.0,
        "max": 3.0,
        "label": "单公司风险上限",
    },
    "MaxRankExposurePct": {
        "type": "number",
        "default": 0.40,
        "min": 0.0,
        "max": 3.0,
        "label": "单排名风险上限",
    },
    "MaxSideExposurePct": {
        "type": "number",
        "default": 0.65,
        "min": 0.0,
        "max": 3.0,
        "label": "Yes/No 单侧风险上限",
    },
    "CooldownMinutes": {
        "type": "number",
        "default": 30.0,
        "min": 0.0,
        "label": "调仓冷却分钟",
    },
    "DebugTopN": {
        "type": "integer",
        "default": 12,
        "min": 0,
        "max": 100,
        "label": "Print 展示 leg 数",
    },
}

ControlsSchema = {
    "manual_pause_open": {"type": "bool", "default": False, "label": "暂停开新仓"},
    "force_flat": {"type": "bool", "default": False, "label": "强制清仓"},
    "risk_scale": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0, "label": "目标仓位缩放"},
    "debug_raw_inputs": {"type": "bool", "default": False, "label": "打印原始 UseData"},
}

RuntimeStateSchema = {
    "last_signal": {"type": "string", "default": "none", "label": "上次信号"},
    "last_action_at": {"type": "string", "default": None, "label": "上次动作时间"},
    "last_selected": {"type": "string", "default": "", "label": "上次选中腿"},
    "last_candidate_count": {"type": "integer", "default": 0, "label": "上次候选数"},
}

StateMachineSchema = {
    "default": "auto",
    "label": "Strategy State",
    "states": [
        {"value": "auto", "label": "Auto"},
        {"value": "risk_off", "label": "Risk Off"},
        {"value": "holding", "label": "Holding"},
        {"value": "cooldown", "label": "Cooldown"},
        {"value": "manual_review", "label": "Manual Review"},
        {"value": "stop_loss_locked", "label": "Stop Loss Locked"},
    ],
}

FunctionIntroduction = (
    "组件功能：MultiLeg_Stock_Rank_Value（Polymarket 市值排名 1/2/3 多腿双边价值策略）。\n\n"
    "核心逻辑：\n"
    "1. 一条 leg = 一个 Polymarket binary market / condition_id；YES/NO 是同一 leg 的两个 side。\n"
    "2. 自动读取 LegCount 与 L{n}_Yes/No_* 标准 UseData 字段。\n"
    "3. 从 leg 参数、标题或市场字段解析 company 与 rank。\n"
    "4. 使用市值 McapUsd_* 构造排名概率模型，分别计算 Yes fair value 与 No fair value。\n"
    "5. 先过滤流动性、价差、高价、年化收益，再进行组合仓位分配。\n"
    "6. 支持买 Yes、买 No、减仓、清仓、换边、冷却时间、强制清仓。\n\n"
    "```yaml\n"
    "inputs:\n"
    "  - name: UseData\n"
    "    type: string\n"
    "    required: true\n"
    "    description: 系统注入的标准运行时数据，包含 LegCount、L{n}_{Side}_AskPrice/BidPrice/PositionQty、McapUsd_* 等\n"
    "  - name: UniverseSymbols\n"
    "    type: string\n"
    "    required: false\n"
    "    description: 参与市值排名建模的股票代码，逗号分隔，例如 AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA\n"
    "  - name: TargetRanks\n"
    "    type: string\n"
    "    required: false\n"
    "    description: 策略允许交易的排名集合，默认 1,2,3\n"
    "  - name: ModelAnnualVol\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 未提供单股 IV/年化波动率时的默认年化波动率\n"
    "  - name: PairwiseCorr\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 市值模拟中默认股票相关性，用于 pairwise rank probability\n"
    "  - name: OpenEdge\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 开仓所需的最小 model fair value - effective ask\n"
    "  - name: AddEdge\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 加仓所需 edge\n"
    "  - name: ReduceEdge\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 已持仓 side 的持有 edge 低于该值时减仓\n"
    "  - name: CloseEdge\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 已持仓 side 的持有 edge 低于该值时清仓\n"
    "  - name: SwitchEdge\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 反向 side edge 超过该值才允许换边\n"
    "  - name: MaxSpread\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 允许开仓的最大 bid/ask spread\n"
    "  - name: MaxEntryPrice\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 最高买入价格；高于该值的 Yes/No 默认不新开\n"
    "  - name: MinAnnualizedRoi\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 最低年化期望收益，避免高胜率低收益腿\n"
    "  - name: MaxPerLegPct\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 单个 leg 相对于 L{n}_BudgetCap 的最高目标仓位\n"
    "  - name: MaxTotalExposurePct\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 策略总风险预算比例，基于 StrategyBankroll 估算组合上限\n"
    "  - name: CooldownMinutes\n"
    "    type: number\n"
    "    required: false\n"
    "    description: 每个 leg 调仓后的冷却时间，冷却期内不加仓/不开仓，但允许减仓清仓\n"
    "outputs:\n"
    "  - name: FunctionJson\n"
    "    type: string\n"
    "    description: actions/metrics/print/state_updates\n"
    "  - name: CodeIsOk\n"
    "    type: boolean\n"
    "    description: 代码是否成功运行\n"
    "```\n"
)

# ===== Inputs/Outputs 声明 =====
for o in Outputs:
    o["Kind"] = "String"
for i in Inputs:
    i["Kind"] = "String"

Inputs[0]["name"] = "UseData"
Inputs[0]["Kind"] = "String"
Inputs[0]["Isnecessary"] = True

_param_names = [
    "UniverseSymbols",
    "TargetRanks",
    "ModelAnnualVol",
    "PairwiseCorr",
    "OpenEdge",
    "AddEdge",
    "ReduceEdge",
    "CloseEdge",
    "SwitchEdge",
    "MaxSpread",
    "MaxEntryPrice",
    "MinAnnualizedRoi",
    "MaxPerLegPct",
]
for idx, name in enumerate(_param_names, start=1):
    Inputs[idx]["name"] = name
    if ParamsSchema.get(name, {}).get("type") in ("number", "integer"):
        Inputs[idx]["Kind"] = "Num"
        Inputs[idx]["Num"] = ParamsSchema[name]["default"]
    else:
        Inputs[idx]["Kind"] = "String"
        Inputs[idx]["Context"] = str(ParamsSchema[name]["default"])
    Inputs[idx]["Isnecessary"] = False

Outputs[0]["name"] = "FunctionJson"
Outputs[0]["Kind"] = "String"
Outputs[1]["name"] = "CodeIsOk"
Outputs[1]["Kind"] = "Boolean"


# ============================================================
# 通用解析与工具函数
# ============================================================

def _norm_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _norm_symbol(s):
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


def _stable_json_dumps(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        return json.dumps({"error": "json_serialize_failed"}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class _UseDataProxy:
    def __init__(self, raw: dict):
        self._raw = raw or {}
        self._norm_to_key = {}
        for k in self._raw.keys():
            nk = _norm_key(k)
            if nk and nk not in self._norm_to_key:
                self._norm_to_key[nk] = k

    def _resolve(self, key):
        if key in self._raw:
            return key
        nk = _norm_key(key)
        return self._norm_to_key.get(nk)

    def get(self, key, default=None):
        real = self._resolve(key)
        if real is None:
            return default
        return self._raw.get(real, default)

    def to_dict(self):
        return dict(self._raw)


def _parse_value(text):
    if text is None:
        return None
    if isinstance(text, (int, float, bool, list, dict)):
        return text
    s = str(text).strip()
    if s == "":
        return ""
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    num_s = s.replace(",", "").replace("_", "")
    if re.fullmatch(r"[+\-]?\d+", num_s):
        try:
            return int(num_s)
        except Exception:
            pass
    if re.fullmatch(r"[+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?", num_s):
        try:
            return float(num_s)
        except Exception:
            pass
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            return json.loads(s)
        except Exception:
            try:
                return ast.literal_eval(s)
            except Exception:
                return s
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            return ast.literal_eval(s)
        except Exception:
            return s[1:-1]
    return s


def _parse_kv_text(raw: str):
    out = {}
    for line_raw in raw.splitlines():
        line = line_raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace("：", ":").replace("＝", "=")
        if "=" in line:
            k, v = line.split("=", 1)
        elif ":" in line:
            k, v = line.split(":", 1)
        else:
            continue
        key = k.strip()
        if key:
            out[key] = _parse_value(v.strip())
    return out


def _parse_usedata(raw):
    if isinstance(raw, dict):
        return raw, None
    if raw is None:
        return None, "UseData 为空"
    if not isinstance(raw, str):
        return None, f"UseData 类型不支持：{type(raw)}"
    s = raw.strip()
    if not s:
        return None, "UseData 为空字符串"
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj, None
    except Exception:
        pass
    kv = _parse_kv_text(s)
    if kv:
        return kv, None
    return None, "UseData 解析失败（需要 JSON 对象或 key=value 文本）"


def _to_float(v, default=0.0):
    try:
        if v is None:
            return float(default)
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, str):
            s = v.strip().replace(",", "").replace("_", "")
            if not s:
                return float(default)
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
            return float(s)
        return float(v)
    except Exception:
        return float(default)


def _to_int(v, default=0):
    try:
        if v is None:
            return int(default)
        if isinstance(v, str):
            s = v.strip().replace(",", "").replace("_", "")
            if not s:
                return int(default)
            return int(float(s))
        return int(v)
    except Exception:
        return int(default)


def _to_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return bool(default)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _clamp(x, lo=0.0, hi=1.0):
    x = _to_float(x, lo)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return float(x)


def _as_dict(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        parsed = _parse_value(v)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        parsed = _parse_value(v)
        if isinstance(parsed, list):
            return parsed
    return []


def _csv_symbols(v):
    if isinstance(v, list):
        raw = v
    else:
        raw = re.split(r"[,，\s]+", str(v or ""))
    out = []
    for x in raw:
        sx = _norm_symbol(x)
        if sx:
            out.append(sx)
    return list(dict.fromkeys(out))


def _csv_ints(v):
    if isinstance(v, list):
        raw = v
    else:
        raw = re.split(r"[,，\s]+", str(v or ""))
    out = []
    for x in raw:
        n = _to_int(x, None)
        if n is not None and n > 0:
            out.append(n)
    return list(dict.fromkeys(out))


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _parse_ts(s):
    if not s:
        return None
    txt = str(s).strip()
    if not txt:
        return None
    # 支持 ISO / Z / 常见空格格式
    try:
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(txt)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(str(s), fmt)
        except Exception:
            continue
    return None


def _iso_now_from_usedata(usedata):
    for k in ("NowTime", "query_time", "query_time_beijing", "ts_utc", "timestamp"):
        v = usedata.get(k, None)
        if v:
            return str(v)
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _minutes_after(ts_str, minutes):
    base = _parse_ts(ts_str) or _dt.datetime.utcnow()
    try:
        return (base + _dt.timedelta(minutes=float(minutes))).replace(microsecond=0).isoformat()
    except Exception:
        return None


def _is_after_or_equal(now_str, until_str):
    if not until_str:
        return True
    now = _parse_ts(now_str)
    until = _parse_ts(until_str)
    if not now or not until:
        return True
    # naive/aware 混用时转 naive 比较
    if now.tzinfo is not None:
        now = now.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    if until.tzinfo is not None:
        until = until.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return now >= until


# ============================================================
# 市值与 leg 解析
# ============================================================

_COMPANY_ALIAS = {
    "AAPL": ["AAPL", "APPLE"],
    "MSFT": ["MSFT", "MICROSOFT"],
    "NVDA": ["NVDA", "NVIDIA"],
    "GOOGL": ["GOOGL", "GOOG", "GOOGLE", "ALPHABET"],
    "AMZN": ["AMZN", "AMAZON"],
    "META": ["META", "FACEBOOK", "FB"],
    "TSLA": ["TSLA", "TESLA"],
    "BRK": ["BRK", "BRKB", "BERKSHIRE"],
    "BRKB": ["BRKB", "BRK.B", "BERKSHIRE"],
    "AVGO": ["AVGO", "BROADCOM"],
    "TSM": ["TSM", "TAIWANSEMICONDUCTOR", "TAIWAN SEMICONDUCTOR"],
    "SPCX": ["SPCX", "SPACEX", "SPACE X", "SPACE EXPLORATION TECHNOLOGIES"],
}

_TRADABLE_MARKET_STATUSES = {"", "open", "unknown", "monitoring", "active", "live", "trading"}


def _extract_mcap_usd_map(usedata: _UseDataProxy, universe):
    out = {}
    for k, v in (usedata.to_dict() or {}).items():
        nk = _norm_key(k)
        if nk.startswith("mcapusd"):
            sym = _norm_symbol(nk[len("mcapusd"):])
            if sym:
                out[sym] = _to_float(v, 0.0)
    # 兼容 MarketCapUsd_XXX / Mcap_XXX
    for k, v in (usedata.to_dict() or {}).items():
        nk = _norm_key(k)
        for prefix in ("marketcapusd", "marketcap", "mcap"):
            if nk.startswith(prefix) and len(nk) > len(prefix):
                sym = _norm_symbol(nk[len(prefix):])
                if sym and sym not in out:
                    out[sym] = _to_float(v, 0.0)
    if universe:
        return {s: out[s] for s in universe if s in out and _to_float(out[s], 0.0) > 0}
    return {s: v for s, v in out.items() if _to_float(v, 0.0) > 0}


def _get_param(defaults, params, name):
    if name in params and params.get(name) not in (None, ""):
        return params.get(name)
    return defaults.get(name, {}).get("default")


def _get_leg_raw_params(usedata, leg):
    candidates = [
        f"L{leg}_Params",
        f"L{leg}_ParamsJson",
        f"L{leg}_InputJson",
        f"L{leg}_InstrumentJson",
        f"L{leg}_Metadata",
    ]
    merged = {}
    for k in candidates:
        d = _as_dict(usedata.get(k, {}))
        if d:
            merged.update(d)
    return merged


def _parse_rank_from_text(text):
    s = str(text or "").lower()
    if not s:
        return None

    # 先识别明确序数，避免 "third largest" 被 largest 误判为 Rank 1
    explicit_patterns = [
        (3, [r"\b#\s*3\b", r"\brank\s*3\b", r"\bthird\b", r"\b3rd\b", r"第\s*三", r"第三"]),
        (2, [r"\b#\s*2\b", r"\brank\s*2\b", r"\bsecond\b", r"\b2nd\b", r"第\s*二", r"第二"]),
        (1, [r"\b#\s*1\b", r"\brank\s*1\b", r"\bfirst\b", r"\b1st\b", r"第\s*一", r"第一"]),
    ]
    for rank, pats in explicit_patterns:
        for p in pats:
            if re.search(p, s):
                return rank

    # 没有明确序数时，largest / top 才按 Rank 1 处理
    if re.search(r"\blargest\b|\btop\b|最高市值|最大市值", s):
        return 1
    return None


def _parse_company_from_text(text, universe):
    raw = str(text or "")
    if not raw:
        return None
    norm_text = _norm_symbol(raw)
    candidates = universe or list(_COMPANY_ALIAS.keys())
    hits = []
    for sym in candidates:
        aliases = _COMPANY_ALIAS.get(sym, [sym])
        for ali in aliases:
            na = _norm_symbol(ali)
            if not na:
                continue
            # ticker 需要单词边界；公司名允许规范化包含
            if ali.upper() == sym:
                if re.search(r"(?<![A-Z0-9])" + re.escape(sym) + r"(?![A-Z0-9])", raw.upper()):
                    hits.append(sym)
                    break
            elif na in norm_text:
                hits.append(sym)
                break
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0]
    # 如果多个命中，选择在原文中最早出现的 alias
    if len(hits) > 1:
        best_sym = None
        best_pos = 10**9
        up = raw.upper()
        for sym in hits:
            for ali in _COMPANY_ALIAS.get(sym, [sym]):
                pos = up.find(str(ali).upper())
                if pos >= 0 and pos < best_pos:
                    best_pos = pos
                    best_sym = sym
        return best_sym
    return None


def _extract_leg_identity(usedata, leg, universe, target_ranks, global_anchor=None, global_rank=None):
    leg_params = _get_leg_raw_params(usedata, leg)

    raw_company = (
        usedata.get(f"L{leg}_AnchorCompany", None)
        or usedata.get(f"L{leg}_Company", None)
        or usedata.get(f"L{leg}_Ticker", None)
        or usedata.get(f"L{leg}_Symbol", None)
        or leg_params.get("AnchorCompany")
        or leg_params.get("anchor_company")
        or leg_params.get("Company")
        or leg_params.get("company")
        or leg_params.get("Ticker")
        or leg_params.get("ticker")
    )
    raw_rank = (
        usedata.get(f"L{leg}_RankPosition", None)
        or usedata.get(f"L{leg}_TargetRank", None)
        or usedata.get(f"L{leg}_Rank", None)
        or leg_params.get("RankPosition")
        or leg_params.get("rank_position")
        or leg_params.get("TargetRank")
        or leg_params.get("target_rank")
        or leg_params.get("Rank")
        or leg_params.get("rank")
    )

    title = (
        usedata.get(f"L{leg}_MarketTitle", "")
        or usedata.get(f"L{leg}_Question", "")
        or usedata.get(f"L{leg}_Title", "")
    )

    company = _norm_symbol(raw_company) if raw_company else None
    if company and company.startswith("MCAPUSD"):
        company = _norm_symbol(company[len("MCAPUSD"):])
    if company not in universe:
        parsed_company = _parse_company_from_text(title, universe)
        company = parsed_company or (company if company in universe else None)

    rank = _to_int(raw_rank, 0)
    if rank <= 0:
        rank = _parse_rank_from_text(title) or 0

    if not company and global_anchor and _to_int(usedata.get("LegCount", 1), 1) == 1:
        company = _norm_symbol(global_anchor)
    if rank <= 0 and global_rank and _to_int(usedata.get("LegCount", 1), 1) == 1:
        rank = _to_int(global_rank, 0)

    if target_ranks and rank not in target_ranks:
        # 解析出来的 rank 不在目标范围，仍保留 rank 但后续跳过
        pass

    return company, rank, leg_params


# ============================================================
# 概率模型
# ============================================================

def _company_vol(usedata, sym, default_vol):
    # 优先使用 IV / VolAnnual / RealizedVol 等字段
    keys = [
        f"IV_{sym}", f"ImpliedVol_{sym}", f"VolAnnual_{sym}", f"AnnualVol_{sym}",
        f"RealizedVol_{sym}", f"Sigma_{sym}",
    ]
    for k in keys:
        v = usedata.get(k, None)
        if v not in (None, ""):
            vv = _to_float(v, default_vol)
            if vv > 2.5:  # 可能是百分数 35
                vv = vv / 100.0
            if vv > 0:
                return vv
    return float(default_vol)


def _rank_probability_exact(company, target_rank, mcap_map, usedata, day_to_end, default_vol, pair_corr, shock_floor):
    if company not in mcap_map or _to_float(mcap_map.get(company), 0.0) <= 0:
        return 0.0
    if target_rank < 1:
        return 0.0
    comps = [c for c, v in mcap_map.items() if c != company and _to_float(v, 0.0) > 0]
    if target_rank > len(comps) + 1:
        return 0.0

    tau = max(_to_float(day_to_end, 1.0), 0.05) / 365.0
    vol_i = _company_vol(usedata, company, default_vol)
    m_i = _to_float(mcap_map[company], 0.0)
    ahead_probs = []

    corr = _clamp(pair_corr, -0.95, 0.99)
    for other in comps:
        m_j = _to_float(mcap_map[other], 0.0)
        if m_j <= 0 or m_i <= 0:
            continue
        vol_j = _company_vol(usedata, other, default_vol)
        pair_var = max(vol_i * vol_i + vol_j * vol_j - 2.0 * corr * vol_i * vol_j, 1e-8)
        scale = math.sqrt(pair_var * tau) + max(_to_float(shock_floor, 0.0), 0.0001)
        log_ratio = math.log(m_i / m_j)
        p_i_beats_j = _normal_cdf(log_ratio / scale)
        q_j_ahead = _clamp(1.0 - p_i_beats_j, 0.000001, 0.999999)
        ahead_probs.append(q_j_ahead)

    # Poisson-binomial: exactly target_rank - 1 competitors ahead
    n = len(ahead_probs)
    dist = [0.0] * (n + 1)
    dist[0] = 1.0
    for q in ahead_probs:
        new = [0.0] * (n + 1)
        for k in range(n):
            new[k] += dist[k] * (1.0 - q)
            new[k + 1] += dist[k] * q
        dist = new
    idx = target_rank - 1
    if idx < 0 or idx >= len(dist):
        return 0.0
    return _clamp(dist[idx], 0.0, 1.0)


def _current_rank(company, mcap_map):
    ordered = sorted([(c, _to_float(v, 0.0)) for c, v in mcap_map.items() if _to_float(v, 0.0) > 0], key=lambda x: x[1], reverse=True)
    for i, (c, _) in enumerate(ordered, start=1):
        if c == company:
            return i
    return None


def _rank_gap_info(company, target_rank, mcap_map):
    ordered = sorted([(c, _to_float(v, 0.0)) for c, v in mcap_map.items() if _to_float(v, 0.0) > 0], key=lambda x: x[1], reverse=True)
    rank = None
    mcap = _to_float(mcap_map.get(company), 0.0)
    for i, (c, _) in enumerate(ordered, start=1):
        if c == company:
            rank = i
            break
    above_gap = None
    below_gap = None
    if rank is not None:
        if rank > 1:
            above_gap = (ordered[rank - 2][1] - mcap) / max(mcap, 1e-9)
        if rank < len(ordered):
            below_gap = (mcap - ordered[rank][1]) / max(mcap, 1e-9)
    return {
        "current_rank": rank,
        "above_gap_pct": above_gap,
        "below_gap_pct": below_gap,
        "ordered": ordered,
    }


# ============================================================
# 盘口、交易价值与组合分配
# ============================================================

def _parse_levels(v):
    levels = _as_list(v)
    out = []
    for x in levels:
        if not isinstance(x, dict):
            continue
        price = _to_float(x.get("price", x.get("p", 0.0)), 0.0)
        qty = _to_float(x.get("qty", x.get("size", x.get("q", 0.0))), 0.0)
        if price > 0 and qty > 0:
            out.append({"price": price, "qty": qty})
    return out


def _effective_buy_price(ask_price, ask_levels, target_notional):
    ask = _to_float(ask_price, 0.0)
    if ask <= 0:
        return 0.0, 0.0
    levels = _parse_levels(ask_levels)
    if not levels or target_notional <= 0:
        return ask, ask * _to_float(0 if not levels else sum(x["qty"] for x in levels), 0.0)
    levels = sorted(levels, key=lambda x: x["price"])
    remaining = float(target_notional)
    spent = 0.0
    qty_total = 0.0
    visible = 0.0
    for lv in levels:
        visible += lv["price"] * lv["qty"]
        if remaining <= 0:
            continue
        can_spend = lv["price"] * lv["qty"]
        use_spend = min(remaining, can_spend)
        spent += use_spend
        qty_total += use_spend / max(lv["price"], 1e-9)
        remaining -= use_spend
    if qty_total <= 0:
        return ask, visible
    return spent / qty_total, visible


def _side_snapshot(usedata, leg, side):
    return {
        "ask": _to_float(usedata.get(f"L{leg}_{side}_AskPrice", 0.0), 0.0),
        "bid": _to_float(usedata.get(f"L{leg}_{side}_BidPrice", 0.0), 0.0),
        "last": _to_float(usedata.get(f"L{leg}_{side}_LastPrice", 0.0), 0.0),
        "ask_depth_notional": _to_float(usedata.get(f"L{leg}_{side}_AskDepthNotional", 0.0), 0.0),
        "bid_depth_notional": _to_float(usedata.get(f"L{leg}_{side}_BidDepthNotional", 0.0), 0.0),
        "best_ask_qty": _to_float(usedata.get(f"L{leg}_{side}_BestAskQty", 0.0), 0.0),
        "best_bid_qty": _to_float(usedata.get(f"L{leg}_{side}_BestBidQty", 0.0), 0.0),
        "ask_levels": usedata.get(f"L{leg}_{side}_AskLevels", []),
        "bid_levels": usedata.get(f"L{leg}_{side}_BidLevels", []),
        "qty": _to_float(usedata.get(f"L{leg}_{side}_PositionQty", 0.0), 0.0),
        "avg": _to_float(usedata.get(f"L{leg}_{side}_PositionAvgPrice", 0.0), 0.0),
        "cost": _to_float(usedata.get(f"L{leg}_{side}_PositionCost", 0.0), 0.0),
        "available_sell_qty": _to_float(usedata.get(f"L{leg}_{side}_AvailableSellQty", usedata.get(f"L{leg}_{side}_PositionQty", 0.0)), 0.0),
        "data_status": str(usedata.get(f"L{leg}_{side}_DataStatus", "ok") or "ok").lower(),
        "age_sec": _to_float(usedata.get(f"L{leg}_{side}_LastUpdateAgeSec", 0.0), 0.0),
        "macd_hist": _to_float(usedata.get(f"L{leg}_{side}_MACDHist", 0.0), 0.0),
        "macd_slope": _to_float(usedata.get(f"L{leg}_{side}_MACDHistSlope", 0.0), 0.0),
    }


def _tradeability(side_data, params, day_to_end, intended_notional):
    ask = side_data["ask"]
    bid = side_data["bid"]
    spread = ask - bid if ask > 0 and bid > 0 else 999.0
    eff_ask, visible_notional = _effective_buy_price(ask, side_data.get("ask_levels"), intended_notional)

    max_spread = _to_float(params.get("MaxSpread"), 0.045)
    max_entry_price = _to_float(params.get("MaxEntryPrice"), 0.985)
    min_depth = _to_float(params.get("MinAskDepthNotional"), 5.0)
    min_days = _to_float(params.get("MinDaysToOpen"), 0.25)
    max_age = _to_float(params.get("MaxDataAgeSec"), 600.0)

    reasons = []
    ok = True
    if ask <= 0 or ask >= 1:
        ok = False
        reasons.append("bad_ask")
    if bid <= 0 or bid >= 1:
        ok = False
        reasons.append("bad_bid")
    if spread > max_spread:
        ok = False
        reasons.append(f"spread>{max_spread}")
    if ask > max_entry_price:
        ok = False
        reasons.append(f"ask>{max_entry_price}")
    depth = side_data["ask_depth_notional"] or visible_notional or (side_data["best_ask_qty"] * ask)
    if min_depth > 0 and depth < min_depth:
        ok = False
        reasons.append(f"depth<{min_depth}")
    if day_to_end < min_days:
        ok = False
        reasons.append(f"day_to_end<{min_days}")
    if side_data["data_status"] not in ("ok", "unknown", ""):
        ok = False
        reasons.append("data_not_ok")
    if max_age > 0 and side_data["age_sec"] > max_age:
        ok = False
        reasons.append("stale")
    liquidity_score = 1.0
    if max_spread > 0:
        liquidity_score *= _clamp(1.0 - max(spread, 0.0) / max_spread, 0.05, 1.0)
    if min_depth > 0:
        liquidity_score *= _clamp(depth / (min_depth * 3.0), 0.05, 1.0)
    return {
        "ok": ok,
        "reasons": reasons,
        "spread": spread,
        "effective_ask": eff_ask if eff_ask > 0 else ask,
        "visible_notional": visible_notional,
        "depth": depth,
        "liquidity_score": _clamp(liquidity_score, 0.0, 1.0),
    }


def _annualized_roi(edge, ask, day_to_end):
    ask = _to_float(ask, 0.0)
    d = max(_to_float(day_to_end, 1.0), 0.25)
    if ask <= 0:
        return -999.0
    return (edge / ask) * 365.0 / d


def _confidence_score(mcap_count, required_count, leg_ok=True):
    if not leg_ok:
        return 0.0
    if required_count <= 0:
        return 1.0
    return _clamp(mcap_count / float(required_count), 0.2, 1.0)


def _target_pct_from_edge(edge, params, confidence, liquidity_score):
    open_edge = _to_float(params.get("OpenEdge"), 0.045)
    add_edge = _to_float(params.get("AddEdge"), 0.075)
    max_pct = _to_float(params.get("MaxPerLegPct"), 0.18)
    min_pct = _to_float(params.get("MinOpenPct"), 0.04)
    if edge < open_edge:
        return 0.0
    if add_edge <= open_edge:
        edge_score = 1.0
    else:
        edge_score = _clamp((edge - open_edge) / (add_edge - open_edge), 0.0, 1.0)
    raw = min_pct + (max_pct - min_pct) * edge_score
    return _clamp(raw * confidence * liquidity_score, 0.0, max_pct)


def _move_towards(cur, target, max_step):
    cur = _clamp(cur, 0.0, 10.0)
    target = _clamp(target, 0.0, 10.0)
    step = max(_to_float(max_step, 0.08), 0.0)
    if step <= 0:
        return target
    if target > cur:
        return min(target, cur + step)
    if target < cur:
        return max(target, cur - step)
    return cur


def _pos_pct(cost, budget):
    budget = _to_float(budget, 0.0)
    if budget <= 0:
        return 0.0
    return _clamp(_to_float(cost, 0.0) / budget, 0.0, 10.0)


# ============================================================
# 动作封装
# ============================================================

def _setpos(leg, side, target_pct, desc):
    return {
        "type": "SETPOS",
        "leg": int(leg),
        "side": str(side),
        "target_pct": float(_clamp(target_pct, 0.0, 1.0)),
        "desc": str(desc),
    }


def _close(leg, side, desc):
    return {
        "type": "CLOSE",
        "leg": int(leg),
        "side": str(side),
        "price": "nowprice",
        "desc": str(desc),
    }


def _close_all(leg, desc):
    return {
        "type": "CLOSE_ALL",
        "leg": int(leg),
        "price": "nowprice",
        "desc": str(desc),
    }


def _emit_db_json(print_lines, ts, inputs, actions, calc=None):
    payload = {
        "ts": ts,
        "inputs": inputs if isinstance(inputs, dict) else {},
        "actions": actions if isinstance(actions, list) else [],
        "calc": calc if isinstance(calc, dict) else {},
    }
    print_lines.append("===DB_JSON_BEGIN===")
    print_lines.append(_stable_json_dumps(payload))
    print_lines.append("===DB_JSON_END===")


def _metric_meta(label, panel, unit="", kind="continuous"):
    return {
        "label": str(label),
        "panel": str(panel),
        "unit": str(unit or ""),
        "kind": str(kind or "continuous"),
    }


def _add_metric(metrics, metrics_meta, key, value, label, panel, unit="", kind="continuous"):
    metrics[str(key)] = value
    metrics_meta[str(key)] = _metric_meta(label, panel, unit, kind)


def _base_metrics_meta():
    return {
        "leg_count": _metric_meta("Leg Count", "metric_values", "count"),
        "mcap_count": _metric_meta("MCap Count", "metric_values", "count"),
        "candidate_count": _metric_meta("Candidate Count", "metric_values", "count"),
        "selected_count": _metric_meta("Selected Count", "metric_values", "count"),
        "best_edge": _metric_meta("Best Edge", "leg_edges", "price"),
        "best_yes_edge": _metric_meta("Best Yes Edge", "leg_edges", "price"),
        "best_no_edge": _metric_meta("Best No Edge", "leg_edges", "price"),
        "total_target_cost": _metric_meta("Total Target Cost", "capital", "currency"),
        "total_cap_cost": _metric_meta("Total Cap Cost", "capital", "currency"),
        "risk_scale": _metric_meta("Risk Scale", "metric_values", "ratio"),
        "manual_pause_open": _metric_meta("Manual Pause Open", "metric_states", "", "state"),
        "decision": _metric_meta("Decision", "metric_states", "", "state"),
        "machine_state": _metric_meta("Machine State", "metric_states", "", "state"),
    }


# ============================================================
# 主策略逻辑
# ============================================================

def _merge_params(usedata, explicit_params):
    defaults = {k: v.get("default") for k, v in ParamsSchema.items()}
    params = dict(defaults)

    # UseData["Params"] 优先级低于 node Inputs，便于 UI 保存参数
    ud_params = _as_dict(usedata.get("Params", {}))
    params.update({k: v for k, v in ud_params.items() if v not in (None, "")})

    for k, v in explicit_params.items():
        if v not in (None, ""):
            params[k] = v

    # 类型规范化
    for k, schema in ParamsSchema.items():
        typ = schema.get("type")
        if typ == "number":
            params[k] = _to_float(params.get(k), schema.get("default", 0.0))
        elif typ == "integer":
            params[k] = _to_int(params.get(k), schema.get("default", 0))
        elif typ == "bool":
            params[k] = _to_bool(params.get(k), schema.get("default", False))
        else:
            if params.get(k) is None:
                params[k] = schema.get("default", "")
    return params


def _run_strategy(usedata: _UseDataProxy, explicit_params):
    actions = []
    print_lines = []
    wake_reason = None
    now_ts = _iso_now_from_usedata(usedata)

    params = _merge_params(usedata, explicit_params)
    controls = {k: v.get("default") for k, v in ControlsSchema.items()}
    controls.update(_as_dict(usedata.get("Controls", usedata.get("UserState", {}))))
    runtime = _as_dict(usedata.get("RuntimeState", usedata.get("State", {})))
    machine_state = usedata.get("MachineState", None)
    if not machine_state:
        machine_state = _as_dict(usedata.get("StrategyState", {})).get("state", StateMachineSchema.get("default", "auto"))

    manual_pause_open = _to_bool(controls.get("manual_pause_open"), False)
    force_flat = _to_bool(controls.get("force_flat"), False) or str(machine_state) in ("risk_off", "stop_loss_locked")
    risk_scale = _clamp(controls.get("risk_scale", 1.0), 0.0, 1.0)
    debug_raw_inputs = _to_bool(controls.get("debug_raw_inputs"), False)

    universe = _csv_symbols(params.get("UniverseSymbols"))
    target_ranks = _csv_ints(params.get("TargetRanks"))
    if not target_ranks:
        target_ranks = [1, 2, 3]

    mcap_map = _extract_mcap_usd_map(usedata, universe)
    mcap_count = len(mcap_map)
    required_count = len(universe) if universe else max(mcap_count, 1)
    missing_mcap = [s for s in universe if s not in mcap_map]

    leg_count = _to_int(usedata.get("LegCount", 1), 1)
    if leg_count < 1:
        leg_count = 1

    strategy_bankroll = _to_float(usedata.get("StrategyBankroll", 0.0), 0.0)
    if strategy_bankroll <= 0:
        # 多腿预算可由各 leg budget 派生
        strategy_bankroll = sum(_to_float(usedata.get(f"L{i}_BudgetCap", 0.0), 0.0) for i in range(leg_count))

    global_anchor = explicit_params.get("AnchorCompany", usedata.get("AnchorCompany", None))
    global_rank = explicit_params.get("RankPosition", usedata.get("RankPosition", None))

    print_lines.append(f"now_time={now_ts}")
    print_lines.append(f"leg_count={leg_count} universe={','.join(universe)} target_ranks={','.join(str(x) for x in target_ranks)}")
    print_lines.append(f"mcap_count={mcap_count} missing_mcap={','.join(missing_mcap)}")
    print_lines.append(f"machine_state={machine_state} risk_scale={risk_scale} manual_pause_open={manual_pause_open} force_flat={force_flat}")

    if debug_raw_inputs:
        raw = usedata.to_dict()
        print_lines.append("=== INPUT_PARAMS_BEGIN ===")
        for k in sorted(raw.keys(), key=lambda x: str(x)):
            print_lines.append(f"UseData.{k}={raw.get(k)}")
        print_lines.append("=== INPUT_PARAMS_END ===")

    # 强制平仓：所有 leg CLOSE_ALL
    if force_flat:
        for leg in range(leg_count):
            actions.append(_close_all(leg, "force_flat_or_risk_off | close all Yes/No"))
        metrics = {
            "leg_count": leg_count,
            "candidate_count": 0,
            "selected_count": 0,
            "best_edge": 0.0,
            "total_target_cost": 0.0,
            "decision": "FORCE_FLAT",
            "risk_scale": risk_scale,
            "machine_state": str(machine_state),
        }
        metrics_meta = _base_metrics_meta()
        _emit_db_json(print_lines, now_ts, {"params": params, "controls": controls}, actions, metrics)
        return {
            "schema_version": "1.0",
            "actions": actions,
            "metrics": metrics,
            "metrics_meta": metrics_meta,
            "print": print_lines,
            "wake_reason": wake_reason,
            "state_updates": {
                "last_signal": "force_flat",
                "last_action_at": now_ts if actions else runtime.get("last_action_at"),
                "last_candidate_count": 0,
                "last_selected": "",
            },
        }

    # 没有足够市值数据时只允许保护性减仓/不动
    if mcap_count < 2:
        metrics = {
            "leg_count": leg_count,
            "candidate_count": 0,
            "selected_count": 0,
            "best_edge": 0.0,
            "total_target_cost": 0.0,
            "decision": "HOLD",
            "risk_scale": risk_scale,
            "machine_state": str(machine_state),
        }
        metrics_meta = _base_metrics_meta()
        print_lines.append("decision=HOLD reason=mcap_data_insufficient")
        return {
            "schema_version": "1.0",
            "actions": [],
            "metrics": metrics,
            "metrics_meta": metrics_meta,
            "print": print_lines,
            "wake_reason": wake_reason,
            "state_updates": {"last_signal": "hold", "last_candidate_count": 0, "last_selected": ""},
        }

    # 逐 leg 评估 Yes/No
    evaluated = []
    candidates = []
    best_edge = -999.0
    best_yes_edge = -999.0
    best_no_edge = -999.0

    for leg in range(leg_count):
        company, rank, leg_params = _extract_leg_identity(usedata, leg, universe, target_ranks, global_anchor, global_rank)
        market_status = str(usedata.get(f"L{leg}_MarketStatus", "open") or "open").lower()
        market_title = str(usedata.get(f"L{leg}_MarketTitle", usedata.get(f"L{leg}_Question", "")) or "")
        day_to_end = _to_float(usedata.get(f"L{leg}_DayToEnd", usedata.get("day_to_end", 9999)), 9999.0)
        budget = _to_float(usedata.get(f"L{leg}_BudgetCap", usedata.get(f"L{leg}_ConfiguredBudgetCap", 0.0)), 0.0)
        if budget <= 0 and leg == 0:
            budget = _to_float(usedata.get("BudgetCap", strategy_bankroll), 0.0)

        yes = _side_snapshot(usedata, leg, "Yes")
        no = _side_snapshot(usedata, leg, "No")

        yes_cost = yes["cost"] if yes["cost"] > 0 else yes["qty"] * max(yes["avg"], 0.0)
        no_cost = no["cost"] if no["cost"] > 0 else no["qty"] * max(no["avg"], 0.0)
        yes_pct = _pos_pct(yes_cost, budget)
        no_pct = _pos_pct(no_cost, budget)

        market_status_ok = market_status in _TRADABLE_MARKET_STATUSES
        identity_ok = company in mcap_map and rank in target_ranks and rank > 0
        if not market_status_ok:
            identity_ok = False

        model_prob = 0.0
        gap_info = {}
        if identity_ok:
            model_prob = _rank_probability_exact(
                company=company,
                target_rank=rank,
                mcap_map=mcap_map,
                usedata=usedata,
                day_to_end=day_to_end,
                default_vol=_to_float(params.get("ModelAnnualVol"), 0.35),
                pair_corr=_to_float(params.get("PairwiseCorr"), 0.65),
                shock_floor=_to_float(params.get("ShockFloor"), 0.015),
            )
            gap_info = _rank_gap_info(company, rank, mcap_map)

        confidence = _confidence_score(mcap_count, required_count, identity_ok)

        intended_notional = max(budget * _to_float(params.get("MaxPerLegPct"), 0.18), _to_float(params.get("MinAskDepthNotional"), 5.0))
        yes_tr = _tradeability(yes, params, day_to_end, intended_notional)
        no_tr = _tradeability(no, params, day_to_end, intended_notional)

        fair_yes = model_prob
        fair_no = 1.0 - model_prob
        edge_yes = fair_yes - yes_tr["effective_ask"]
        edge_no = fair_no - no_tr["effective_ask"]
        hold_edge_yes = fair_yes - yes["bid"]
        hold_edge_no = fair_no - no["bid"]
        ann_yes = _annualized_roi(edge_yes, yes_tr["effective_ask"], day_to_end)
        ann_no = _annualized_roi(edge_no, no_tr["effective_ask"], day_to_end)

        best_yes_edge = max(best_yes_edge, edge_yes)
        best_no_edge = max(best_no_edge, edge_no)
        best_edge = max(best_edge, edge_yes, edge_no)

        min_ann = _to_float(params.get("MinAnnualizedRoi"), 0.20)
        yes_ok = identity_ok and yes_tr["ok"] and ann_yes >= min_ann
        no_ok = identity_ok and no_tr["ok"] and ann_no >= min_ann

        side_rows = []
        for side_name, side_data, tr, edge, ann, fair, hold_edge, cur_pct in [
            ("Yes", yes, yes_tr, edge_yes, ann_yes, fair_yes, hold_edge_yes, yes_pct),
            ("No", no, no_tr, edge_no, ann_no, fair_no, hold_edge_no, no_pct),
        ]:
            ok = yes_ok if side_name == "Yes" else no_ok
            target_pct_raw = 0.0
            utility = -999.0
            if ok:
                target_pct_raw = _target_pct_from_edge(edge, params, confidence, tr["liquidity_score"])
                utility = edge * tr["liquidity_score"] * confidence
            side_rows.append({
                "leg": leg,
                "side": side_name,
                "company": company,
                "rank": rank,
                "market_title": market_title,
                "day_to_end": day_to_end,
                "budget": budget,
                "model_prob": model_prob,
                "fair": fair,
                "ask": side_data["ask"],
                "bid": side_data["bid"],
                "effective_ask": tr["effective_ask"],
                "spread": tr["spread"],
                "edge": edge,
                "hold_edge": hold_edge,
                "annualized_roi": ann,
                "tradeable": bool(ok),
                "tradeability_reasons": tr["reasons"],
                "liquidity_score": tr["liquidity_score"],
                "confidence": confidence,
                "target_pct_raw": target_pct_raw,
                "utility": utility,
                "current_pct": cur_pct,
                "current_cost": yes_cost if side_name == "Yes" else no_cost,
                "opposite_pct": no_pct if side_name == "Yes" else yes_pct,
                "current_rank": gap_info.get("current_rank"),
                "above_gap_pct": gap_info.get("above_gap_pct"),
                "below_gap_pct": gap_info.get("below_gap_pct"),
                "identity_ok": identity_ok,
                "market_status": market_status,
                "market_status_ok": market_status_ok,
            })

        # 选择 Yes/No 中 utility 更好的一侧作为候选
        sorted_sides = sorted(side_rows, key=lambda x: x["utility"], reverse=True)
        best_side = sorted_sides[0]
        second_side = sorted_sides[1]
        best_side["other_side_edge"] = second_side["edge"]
        best_side["other_side"] = second_side["side"]

        evaluated.append({
            "leg": leg,
            "company": company,
            "rank": rank,
            "market_title": market_title,
            "day_to_end": day_to_end,
            "budget": budget,
            "model_prob": model_prob,
            "yes": side_rows[0],
            "no": side_rows[1],
            "best": best_side,
            "identity_ok": identity_ok,
            "market_status": market_status,
            "market_status_ok": market_status_ok,
            "yes_pct": yes_pct,
            "no_pct": no_pct,
            "yes_cost": yes_cost,
            "no_cost": no_cost,
        })

        if best_side["tradeable"] and best_side["target_pct_raw"] > 0:
            candidates.append(best_side)

    # 组合分配：同 company / rank / side / total 风险上限
    total_cap_cost = strategy_bankroll * _to_float(params.get("MaxTotalExposurePct"), 0.80) * risk_scale
    company_cap_cost = strategy_bankroll * _to_float(params.get("MaxCompanyExposurePct"), 0.32) * risk_scale
    rank_cap_cost = strategy_bankroll * _to_float(params.get("MaxRankExposurePct"), 0.40) * risk_scale
    side_cap_cost = strategy_bankroll * _to_float(params.get("MaxSideExposurePct"), 0.65) * risk_scale

    # 当前成本也占用风险预算
    used_total = 0.0
    used_company = {}
    used_rank = {}
    used_side = {"Yes": 0.0, "No": 0.0}
    for row in evaluated:
        c = row.get("company") or f"L{row['leg']}"
        r = row.get("rank") or 0
        for side_name, cost in [("Yes", row["yes_cost"]), ("No", row["no_cost"])]:
            cost = _to_float(cost, 0.0)
            used_total += cost
            used_company[c] = used_company.get(c, 0.0) + cost
            used_rank[r] = used_rank.get(r, 0.0) + cost
            used_side[side_name] = used_side.get(side_name, 0.0) + cost

    allocated = {}
    selected = []
    for cand in sorted(candidates, key=lambda x: x["utility"], reverse=True):
        leg = cand["leg"]
        side = cand["side"]
        company = cand["company"] or f"L{leg}"
        rank = cand["rank"] or 0
        budget = _to_float(cand["budget"], 0.0)
        if budget <= 0:
            cand["alloc_reason"] = "budget_zero"
            continue

        # 暂停开仓：如果两边都近似空仓，则不新开
        row = evaluated[leg]
        is_flat = row["yes_pct"] <= 0.001 and row["no_pct"] <= 0.001
        if manual_pause_open and is_flat:
            cand["alloc_reason"] = "manual_pause_open"
            continue

        # 冷却期：允许已有同 side 降风险，但不允许新开/加仓
        cooldown_key = f"cooldown_until_L{leg}"
        cooldown_until = runtime.get(cooldown_key)
        cooldown_open = not _is_after_or_equal(now_ts, cooldown_until)
        if cooldown_open:
            current_pct = cand["current_pct"]
            if cand["target_pct_raw"] > current_pct:
                cand["alloc_reason"] = f"cooldown_until={cooldown_until}"
                continue

        target_cost_raw = budget * cand["target_pct_raw"]
        add_cost = max(0.0, target_cost_raw - cand["current_cost"])

        remaining_total = max(0.0, total_cap_cost - used_total)
        remaining_company = max(0.0, company_cap_cost - used_company.get(company, 0.0))
        remaining_rank = max(0.0, rank_cap_cost - used_rank.get(rank, 0.0))
        remaining_side = max(0.0, side_cap_cost - used_side.get(side, 0.0))

        allowed_add = min(add_cost, remaining_total, remaining_company, remaining_rank, remaining_side)
        final_target_cost = cand["current_cost"] + allowed_add
        final_target_pct = _clamp(final_target_cost / budget if budget > 0 else 0.0, 0.0, _to_float(params.get("MaxPerLegPct"), 0.18))

        # 如果当前没有仓位，且被 cap 裁剪后低于最小开仓，不交易
        if cand["current_pct"] <= 0.001 and final_target_pct < _to_float(params.get("MinOpenPct"), 0.04):
            cand["alloc_reason"] = "below_min_open_after_caps"
            continue

        cand["final_target_pct"] = final_target_pct
        cand["allocated_add_cost"] = allowed_add
        allocated[(leg, side)] = cand
        selected.append(cand)

        used_total += allowed_add
        used_company[company] = used_company.get(company, 0.0) + allowed_add
        used_rank[rank] = used_rank.get(rank, 0.0) + allowed_add
        used_side[side] = used_side.get(side, 0.0) + allowed_add

    # 生成调仓动作
    open_edge = _to_float(params.get("OpenEdge"), 0.045)
    reduce_edge = _to_float(params.get("ReduceEdge"), 0.015)
    close_edge = _to_float(params.get("CloseEdge"), -0.005)
    switch_edge = _to_float(params.get("SwitchEdge"), 0.10)
    max_step = _to_float(params.get("MaxStepPct"), 0.08)
    cooldown_minutes = _to_float(params.get("CooldownMinutes"), 30.0)
    state_updates = {
        "last_signal": "hold",
        "last_candidate_count": len(candidates),
        "last_selected": "",
    }

    selected_keys = set(allocated.keys())

    for row in evaluated:
        leg = row["leg"]
        # 如果身份不清或市场关闭，不新开，但仍可按规则保护性退出
        selected_side = None
        if (leg, "Yes") in selected_keys:
            selected_side = "Yes"
        elif (leg, "No") in selected_keys:
            selected_side = "No"

        yes_row = row["yes"]
        no_row = row["no"]
        yes_pct = row["yes_pct"]
        no_pct = row["no_pct"]

        if selected_side:
            cand = allocated[(leg, selected_side)]
            other = "No" if selected_side == "Yes" else "Yes"
            other_row = no_row if selected_side == "Yes" else yes_row
            current_row = yes_row if selected_side == "Yes" else no_row
            current_pct = yes_pct if selected_side == "Yes" else no_pct
            other_pct = no_pct if selected_side == "Yes" else yes_pct

            # 如果反向仓位存在，先降反向；反向 edge 很差时直接 close，否则用 SETPOS 到 0
            if other_pct > 0.001:
                reason = f"L{leg} switch_or_conflict | target={selected_side} edge={cand['edge']:.4f} | {other}->0"
                if cand["edge"] >= switch_edge:
                    actions.append(_close(leg, other, reason + " | hard switch"))
                else:
                    actions.append(_setpos(leg, other, 0.0, reason))
                # 防止同一轮吃两边流动性：反向仓位明显时先退出，下轮再开新边
                if other_pct > 0.03:
                    state_updates[f"cooldown_until_L{leg}"] = _minutes_after(now_ts, cooldown_minutes)
                    continue

            target_pct = _to_float(cand.get("final_target_pct", cand["target_pct_raw"]), 0.0)
            # 如果已有同向仓位但 edge 不足 add，只维持/小幅靠近；如果 edge 很强才加
            if current_pct > 0.001 and cand["edge"] < _to_float(params.get("AddEdge"), 0.075) and target_pct > current_pct:
                target_pct = current_pct

            next_pct = _move_towards(current_pct, target_pct, max_step)
            if abs(next_pct - current_pct) >= 0.002:
                actions.append(_setpos(
                    leg,
                    selected_side,
                    next_pct,
                    f"L{leg} {row.get('company')} rank{row.get('rank')} | {selected_side} edge={cand['edge']:.4f} fair={cand['fair']:.4f} ask={cand['effective_ask']:.4f} ann={cand['annualized_roi']:.2f} | pct {current_pct:.3f}->{next_pct:.3f}"
                ))
                state_updates[f"cooldown_until_L{leg}"] = _minutes_after(now_ts, cooldown_minutes)
            continue

        # 未入选：管理已有仓位
        for side_name, side_row, cur_pct in [("Yes", yes_row, yes_pct), ("No", no_row, no_pct)]:
            if cur_pct <= 0.001:
                continue
            hold_edge = side_row["hold_edge"]
            if hold_edge < close_edge:
                actions.append(_close(
                    leg,
                    side_name,
                    f"L{leg} close {side_name} | hold_edge={hold_edge:.4f} < close_edge={close_edge:.4f}"
                ))
                state_updates[f"cooldown_until_L{leg}"] = _minutes_after(now_ts, cooldown_minutes)
            elif hold_edge < reduce_edge:
                next_pct = _move_towards(cur_pct, max(0.0, cur_pct * 0.50), max_step)
                actions.append(_setpos(
                    leg,
                    side_name,
                    next_pct,
                    f"L{leg} reduce {side_name} | hold_edge={hold_edge:.4f} < reduce_edge={reduce_edge:.4f} | pct {cur_pct:.3f}->{next_pct:.3f}"
                ))
                state_updates[f"cooldown_until_L{leg}"] = _minutes_after(now_ts, cooldown_minutes)

    if actions:
        state_updates["last_signal"] = "setpos"
        state_updates["last_action_at"] = now_ts
        state_updates["last_selected"] = ",".join([f"L{x['leg']}:{x['side']}:{x['edge']:.3f}" for x in selected[:8]])
    else:
        state_updates["last_signal"] = "hold"

    # metrics：只放聚合信息，避免多 leg 刷屏
    total_target_cost = sum(_to_float(x.get("budget"), 0.0) * _to_float(x.get("final_target_pct", 0.0), 0.0) for x in selected)
    metrics = {
        "leg_count": leg_count,
        "mcap_count": mcap_count,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "best_edge": 0.0 if best_edge == -999.0 else best_edge,
        "best_yes_edge": 0.0 if best_yes_edge == -999.0 else best_yes_edge,
        "best_no_edge": 0.0 if best_no_edge == -999.0 else best_no_edge,
        "total_target_cost": total_target_cost,
        "total_cap_cost": total_cap_cost,
        "risk_scale": risk_scale,
        "manual_pause_open": manual_pause_open,
        "decision": "SETPOS" if actions else "HOLD",
        "machine_state": str(machine_state),
    }
    metrics_meta = _base_metrics_meta()

    for rank_idx, (sym, mcap_value) in enumerate(
        sorted(
            [(s, _to_float(v, 0.0)) for s, v in mcap_map.items() if _to_float(v, 0.0) > 0],
            key=lambda item: item[1],
            reverse=True,
        ),
        start=1,
    ):
        _add_metric(metrics, metrics_meta, f"mcap_usd_{sym}", mcap_value, f"{sym} Market Cap", "market_mcap", "compact_currency")
        _add_metric(metrics, metrics_meta, f"mcap_rank_{sym}", rank_idx, f"{sym} MCap Rank", "leg_rank", "rank")

    for row in evaluated:
        leg = row["leg"]
        company = row.get("company") or f"L{leg}"
        label_base = f"L{leg} {company}"
        yes_row = row["yes"]
        no_row = row["no"]
        current_rank = yes_row.get("current_rank") or no_row.get("current_rank")
        mcap_value = _to_float(mcap_map.get(company), 0.0) if company in mcap_map else None
        _add_metric(metrics, metrics_meta, f"L{leg}_yes_position", row["yes_pct"], f"{label_base} Yes Position", "leg_positions", "ratio")
        _add_metric(metrics, metrics_meta, f"L{leg}_no_position", row["no_pct"], f"{label_base} No Position", "leg_positions", "ratio")
        _add_metric(metrics, metrics_meta, f"L{leg}_yes_cost", row["yes_cost"], f"{label_base} Yes Cost", "capital", "currency")
        _add_metric(metrics, metrics_meta, f"L{leg}_no_cost", row["no_cost"], f"{label_base} No Cost", "capital", "currency")
        _add_metric(metrics, metrics_meta, f"L{leg}_model_prob", row["model_prob"], f"{label_base} Model Prob", "leg_edges", "ratio")
        _add_metric(metrics, metrics_meta, f"L{leg}_yes_edge", yes_row["edge"], f"{label_base} Yes Edge", "leg_edges", "price")
        _add_metric(metrics, metrics_meta, f"L{leg}_no_edge", no_row["edge"], f"{label_base} No Edge", "leg_edges", "price")
        _add_metric(metrics, metrics_meta, f"L{leg}_target_rank", row.get("rank"), f"{label_base} Target Rank", "leg_rank", "rank")
        _add_metric(metrics, metrics_meta, f"L{leg}_current_rank", current_rank, f"{label_base} Current Rank", "leg_rank", "rank")
        _add_metric(metrics, metrics_meta, f"L{leg}_mcap_usd", mcap_value, f"{label_base} Market Cap", "market_mcap", "compact_currency")
        _add_metric(metrics, metrics_meta, f"L{leg}_identity_ok", bool(row.get("identity_ok")), f"{label_base} Identity OK", "metric_states", "", "state")
        _add_metric(metrics, metrics_meta, f"L{leg}_market_status_ok", bool(row.get("market_status_ok")), f"{label_base} Market Status OK", "metric_states", "", "state")

    # Print 摘要
    print_lines.append(f"decision={'SETPOS' if actions else 'HOLD'} actions={len(actions)} candidates={len(candidates)} selected={len(selected)}")
    debug_top_n = _to_int(params.get("DebugTopN"), 12)
    for row in sorted(evaluated, key=lambda x: max(x["yes"]["edge"], x["no"]["edge"]), reverse=True)[:debug_top_n]:
        yes_row = row["yes"]
        no_row = row["no"]
        print_lines.append(
            "LEG_SUMMARY "
            f"L{row['leg']} company={row.get('company')} rank={row.get('rank')} "
            f"cur_rank={yes_row.get('current_rank')} prob={row['model_prob']:.4f} "
            f"Y_edge={yes_row['edge']:.4f} Y_ann={yes_row['annualized_roi']:.2f} Y_ok={yes_row['tradeable']} "
            f"N_edge={no_row['edge']:.4f} N_ann={no_row['annualized_roi']:.2f} N_ok={no_row['tradeable']} "
            f"budget={row['budget']:.2f} posY={row['yes_pct']:.3f} posN={row['no_pct']:.3f}"
        )
        if not row["identity_ok"]:
            print_lines.append(
                f"LEG_SKIP L{row['leg']} identity_or_market_not_ok company={row.get('company')} rank={row.get('rank')} status={row.get('market_status')} title={row.get('market_title')[:120]}"
            )
        else:
            if not yes_row["tradeable"] and yes_row["tradeability_reasons"]:
                print_lines.append(f"LEG_FILTER L{row['leg']} Yes reasons={','.join(yes_row['tradeability_reasons'])}")
            if not no_row["tradeable"] and no_row["tradeability_reasons"]:
                print_lines.append(f"LEG_FILTER L{row['leg']} No reasons={','.join(no_row['tradeability_reasons'])}")

    if selected:
        print_lines.append("SELECTED " + "; ".join([
            f"L{x['leg']} {x['company']} R{x['rank']} {x['side']} edge={x['edge']:.4f} target={x.get('final_target_pct', 0):.3f}"
            for x in selected[:debug_top_n]
        ]))

    _emit_db_json(print_lines, now_ts, {"params": params, "controls": controls}, actions, metrics)

    return {
        "schema_version": "1.0",
        "actions": actions,
        "metrics": metrics,
        "metrics_meta": metrics_meta,
        "print": print_lines,
        "wake_reason": wake_reason,
        "state_updates": state_updates,
    }


def run_node(node):
    try:
        node_inputs = node.get("Inputs") or []
        usedata_raw = node_inputs[0].get("Context") if len(node_inputs) > 0 else None
        usedata_dict, usedata_err = _parse_usedata(usedata_raw)
        if usedata_err:
            out_json = {
                "schema_version": "1.0",
                "actions": [],
                "metrics": {"decision": "ERROR"},
                "print": [f"[UseDataError] {usedata_err}"],
                "wake_reason": None,
            }
            Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
            Outputs[1]["Boolean"] = False
            return Outputs

        explicit_params = {}
        # 从 Inputs 读取参数，名称以 Inputs 声明为准
        for idx in range(1, min(len(node_inputs), len(Inputs))):
            name = Inputs[idx].get("name")
            if not name:
                continue
            val = node_inputs[idx].get("Context")
            if val in (None, "") and node_inputs[idx].get("Num") is not None:
                val = node_inputs[idx].get("Num")
            if val not in (None, ""):
                explicit_params[name] = val

        # 兼容旧单腿参数
        for legacy in ("AnchorCompany", "RankPosition"):
            if legacy not in explicit_params:
                for item in node_inputs[1:]:
                    if item.get("name") == legacy:
                        v = item.get("Context", item.get("Num"))
                        if v not in (None, ""):
                            explicit_params[legacy] = v

        out_json = _run_strategy(_UseDataProxy(usedata_dict), explicit_params)
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = True
        return Outputs
    except Exception as e:
        out_json = {
            "schema_version": "1.0",
            "actions": [],
            "metrics": {"decision": "ERROR"},
            "metrics_meta": {"decision": _metric_meta("Decision", "metric_states", "", "state")},
            "print": [f"[RuntimeError] {type(e).__name__}: {e}"],
            "wake_reason": None,
        }
        Outputs[0]["Context"] = json.dumps(out_json, ensure_ascii=False, indent=2)
        Outputs[1]["Boolean"] = False
        return Outputs
