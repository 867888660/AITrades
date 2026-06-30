# 策略代码规范 v1

本文定义 `StrategyCode/` 目录中策略文件的输入、运行数据命名和动作输出协议。目标是让策略代码只负责交易逻辑，系统负责数据标准化、参数注入、数量换算、审计和执行。

## 1. 核心目标

策略代码只负责两件事：

```text
1. 声明 Inputs，让系统和 UI 知道策略需要哪些参数
2. 根据 UseData 输出 FunctionJson 动作
```

系统负责：

```text
1. 标准化 UseData 命名
2. 注入期权价格、期权数量、持仓、预算、时间等数据
3. 执行 FunctionJson 中的 BUY / SELL / SETPOS / CANCEL / WAKE 等动作
```

核心分层：

```text
Inputs        策略参数声明
UseData       标准化运行时事实数据
FunctionJson  策略输出的动作意图
```

## 2. 策略文件必须包含

每个策略文件必须包含：

```python
OutPutNum = 2
InPutNum = N

Inputs = [...]
Outputs = [...]

FunctionIntroduction = "..."

def run_node(node):
    ...
    return Outputs
```

固定输出：

```text
Output1: FunctionJson  String
Output2: CodeIsOk      Boolean
```

统一使用 `CodeIsOk`，不再使用 `IsCodeOk`。

## 3. Inputs 规范

`Inputs` 只声明策略参数。

`Input1` 固定为 `UseData`。

后续输入为策略自定义参数：

```python
Inputs[0]["name"] = "UseData"
Inputs[0]["Kind"] = "String"

Inputs[1]["name"] = "AnchorCompany"
Inputs[1]["Kind"] = "String"

Inputs[2]["name"] = "RankPosition"
Inputs[2]["Kind"] = "Num"
```

策略参数示例：

```text
AnchorCompany
RankPosition
FactSide
risk
start_day
hedge_sell_bias
trend_sell_bias
core_sell_bias
```

盘口、持仓、预算、期权数量、价格、结束时间等运行数据，不放在 `Inputs` 里，统一从 `UseData` 读取。

## 4. UseData 总体命名规则

`UseData` 使用标准化扁平字段。

核心格式：

```text
L{leg}_{Side}_{Field}
```

其中：

```text
L{leg}  表示第几条 leg，从 L0 开始
Side    只能是 Yes 或 No
Field   使用 PascalCase
```

示例：

```text
L0_Yes_AskPrice
L0_Yes_BidPrice
L0_Yes_PositionQty
L0_No_PositionQty
L1_Yes_AskPrice
```

策略代码只需要读取：

```text
LegCount
```

用于判断当前有几条 leg。

示例：

```text
LegCount = 1
```

单腿策略默认使用 `L0`。

## 5. UseData 全局字段

```text
SchemaVersion
NowTime
RunMode
StrategyId
StrategyName
StrategyBankroll
LegCount
```

说明：

```text
SchemaVersion      UseData 版本，例如 1.0
NowTime            当前运行时间
RunMode            Virtual / Real / Backtest
StrategyId         策略 ID
StrategyName       策略名称
StrategyBankroll   策略总资金
LegCount           当前策略 leg 数量
```

## 6. UseData 每条 leg 字段

```text
L{n}_ConditionId
L{n}_LegUid
L{n}_LegKind
L{n}_AssetClass
L{n}_Venue
L{n}_Symbol
L{n}_InstrumentId
L{n}_MarketTitle
L{n}_MarketStatus
L{n}_BudgetCap
L{n}_ConfiguredBudgetCap
L{n}_BudgetMin
L{n}_EndTime
L{n}_DayToEnd
L{n}_HourToEnd
```

`MarketStatus` 可选值：

```text
open
closed
resolved
unknown
```

## 7. UseData 每条 leg + side 字段

```text
L{n}_{Side}_TokenId

L{n}_{Side}_AskPrice
L{n}_{Side}_BidPrice
L{n}_{Side}_LastPrice

L{n}_{Side}_BestAskQty
L{n}_{Side}_BestBidQty
L{n}_{Side}_AskLevels
L{n}_{Side}_BidLevels
L{n}_{Side}_AskDepthQty
L{n}_{Side}_BidDepthQty
L{n}_{Side}_AskDepthNotional
L{n}_{Side}_BidDepthNotional

L{n}_{Side}_PositionQty
L{n}_{Side}_PositionAvgPrice
L{n}_{Side}_PositionCost
L{n}_{Side}_PositionValueBid

L{n}_{Side}_OpenBuyQty
L{n}_{Side}_OpenSellQty
L{n}_{Side}_AvailableSellQty

L{n}_{Side}_DataStatus
L{n}_{Side}_LastUpdateAgeSec

L{n}_{Side}_MACD
L{n}_{Side}_MACDSignal
L{n}_{Side}_MACDHist
L{n}_{Side}_MACDHistPrev
L{n}_{Side}_MACDHistSlope
L{n}_{Side}_MACDSampleCount
```

说明：

```text
AskPrice              当前最优卖价
BidPrice              当前最优买价
LastPrice             最新成交价
BestAskQty            当前最优卖价数量
BestBidQty            当前最优买价数量
AskLevels             当前可见卖盘档位列表，按价格从低到高排序，元素格式为 {"price": 0.34, "qty": 34}
BidLevels             当前可见买盘档位列表，按价格从高到低排序，元素格式为 {"price": 0.33, "qty": 20}
AskDepthQty           当前返回订单簿卖盘总数量
BidDepthQty           当前返回订单簿买盘总数量
AskDepthNotional      当前返回订单簿卖盘总名义金额，sum(price * qty)
BidDepthNotional      当前返回订单簿买盘总名义金额，sum(price * qty)
PositionQty           当前持有期权数量
PositionAvgPrice      当前持仓平均成本
PositionCost          当前持仓成本金额
PositionValueBid      按 BidPrice 估算的可卖出价值
OpenBuyQty            未完成买单数量
OpenSellQty           未完成卖单数量
AvailableSellQty      可卖数量，通常等于 PositionQty - OpenSellQty
DataStatus            ok / stale / missing
LastUpdateAgeSec      数据距当前时间的秒数
MACD                  基于该 side 近期可卖 bid 历史计算的 MACD 值
MACDSignal            MACD 信号线
MACDHist              MACD histogram，等于 MACD - MACDSignal
MACDHistPrev          上一根 MACD histogram
MACDHistSlope         MACDHist - MACDHistPrev
MACDSampleCount       本次 MACD 计算使用的有效价格样本数
```

Polymarket binary 的 MACD 由 `virtual_context_builder.build_use_data()` 从本地 `market_deltas` 最近盘口历史计算，并把当前 CLOB `/book` bid 作为最后一个样本补入。周期默认遵循策略参数中的 `macd_fast` / `macd_slow` / `macd_signal`，没有声明这些参数时使用 `6 / 13 / 5`。单腿策略还会得到兼容别名，例如 `Yes_MACDHist`、`Yes_MACDHistPrev`、`No_MACDHist`。

## 8. 外部行情字段

外部行情继续使用独立命名：

```text
Price_NVDA
McapUsd_NVDA
Change24h_NVDA
Vol24hUsd_NVDA
FdvUsd_NVDA
```

## 9. 数量、价格、金额、比例命名规则

字段后缀必须遵守：

```text
Qty        期权数量
Price      期权价格
Pct        比例，范围 0~1
Cost       成本金额
Value      当前估值金额
Notional   名义金额
Budget     预算金额
```

禁止在新策略中使用语义模糊字段：

```text
Yes_Now_Pos
No_Now_Pos
Yes_now_Qty
No_now_Qty
Yes_now_ask
No_now_ask
```

推荐使用：

```text
L0_Yes_PositionQty
L0_No_PositionQty
L0_Yes_AskPrice
L0_No_AskPrice
```

## 10. metrics 字段规范

策略通过 `metrics` 字典声明需要在工作台面板展示的指标。

核心原则：

```text
1. metrics 中出现的所有 key 都会在工作台 STRATEGY METRICS 面板展示，无后端过滤
2. 不需要展示的调试信息不要放进 metrics，放到 print 或其他字段
3. 数值型指标显示为副图曲线，状态型指标显示为 State Lanes 色带
```

示例：

```python
metrics = {
    "gap_up": gap_up,
    "gap_down": gap_down,
    "hold_buffer_yes": hold_buffer_yes,
    "recover_buffer_no": recover_buffer_no,
    "day_to_end": day_to_end,
    "yes_full_on": YES_FULL_ON,
    "yes_off": YES_OFF,
    "no_full_on": NO_FULL_ON,
    "no_off": NO_OFF,
    "decision": decision,
    "fact_state": fact_state,
    "pos_state": pos_state,
    "target_state": target_state,
    "next_state": next_state,
}
```

类型推断规则：

```text
int / float          -> metric_type = "number"，显示为数值曲线
str                  -> metric_type = "text"，若值为有限枚举则显示为 State Lane
bool                 -> metric_type = "bool"
```

禁止放入 metrics 的内容：

```text
ts / 时间戳           已由系统自动记录
anchor_company       策略参数，不是运行指标
current_rank         调试中间值
higher_count         调试中间值
tie_flag             调试中间值
is_target_rank       调试中间值
anchor_mcap          外部数据快照
fact_reason          文本解释，放 print
yes_now_pos          持仓数据，系统已有
no_now_pos           持仓数据，系统已有
rank_ok              数据质量标记，放 print
missing_rank_symbols 数据质量标记，放 print
```

如果需要在面板展示新指标，直接加到 metrics 字典即可，无需修改后端。

## 11. FunctionJson 顶层格式

策略必须输出 JSON 字符串：

```json
{
  "schema_version": "1.0",
  "actions": [],
  "metrics": {},
  "print": [],
  "wake_reason": null
}
```

字段说明：

```text
schema_version   FunctionJson 协议版本
actions          本次运行输出的动作列表
metrics          需要在工作台面板展示的指标字典（见第 10 节）
print            调试、审计、解释信息
wake_reason      唤醒原因，没有则为 null
```

## 11. actions 执行规则

```text
actions 按数组顺序执行
每个 action 必须有 type
leg 默认为 0
side 只能是 Yes / No
```

## 12. BUY 动作

用于买入指定期权数量。

```json
{
  "type": "BUY",
  "leg": 0,
  "side": "Yes",
  "qty": 12,
  "price": "nowprice"
}
```

字段说明：

```text
type    固定为 BUY
leg     第几条 leg，默认 0
side    Yes 或 No
qty     买入期权数量
price   买入价格
```

资金约束：

```text
BUY 是显式数量指令。如果 cash < qty * price + fee，执行层必须写 blocked / insufficient_cash，不允许默认成交，也不自动缩小 qty。
```

`price` 支持三种形式：

```text
null          使用系统默认当前价格
"nowprice"    使用当前盘口价格，BUY 使用 AskPrice
阿拉伯数字     使用指定价格，例如 0.42
```

Virtual 模式下，`price=null` / `"nowprice"` 的 BUY 不再假设最优 ask 有无限数量，而是按 `L{leg}_{Side}_AskLevels` 从低到高扫档成交。例如买入 100 qty，若最低 ask 只有 34 qty，则先成交 34，再继续吃更高 ask，最终订单价格按实际成交 VWAP 写入账本。若可见订单簿不足，允许按可见深度部分成交，并在动作事件中记录 `partial_fill_book_depth`。

当 `price` 是数字时，Virtual 会把它视为 taker limit 边界：BUY 只吃 `price <= limit` 的 ask 档；如果当前 UseData 没有深度列表，则回退到旧的一口价模拟。

示例：

```json
{
  "type": "BUY",
  "leg": 0,
  "side": "Yes",
  "qty": 12,
  "price": null
}
```

```json
{
  "type": "BUY",
  "leg": 0,
  "side": "Yes",
  "qty": 12,
  "price": "nowprice"
}
```

```json
{
  "type": "BUY",
  "leg": 0,
  "side": "Yes",
  "qty": 12,
  "price": 0.42
}
```

## 13. SELL 动作

用于卖出指定期权数量。

```json
{
  "type": "SELL",
  "leg": 0,
  "side": "No",
  "qty": 5,
  "price": "nowprice"
}
```

字段说明：

```text
type    固定为 SELL
leg     第几条 leg，默认 0
side    Yes 或 No
qty     卖出期权数量
price   卖出价格
```

`price` 支持三种形式：

```text
null          使用系统默认当前价格
"nowprice"    使用当前盘口价格，SELL 使用 BidPrice
阿拉伯数字     使用指定价格，例如 0.38
```

Virtual 模式下，SELL 会按 `L{leg}_{Side}_BidLevels` 从高到低扫档成交，成交价同样按 VWAP 入账。数字 `price` 会作为 taker limit 边界：SELL 只吃 `price >= limit` 的 bid 档；可见买盘不足时按可见深度部分成交。

## 14. SETPOS 动作

用于把某个 leg 的某个 side 调整到目标仓位比例。

```json
{
  "type": "SETPOS",
  "leg": 0,
  "side": "Yes",
  "target_pct": 0.6
}
```

字段说明：

```text
type          固定为 SETPOS
leg           第几条 leg，默认 0
side          Yes 或 No
target_pct    目标仓位比例，范围 0~1
```

含义：

```text
目标成本 = L{leg}_BudgetCap * target_pct
当前成本 = L{leg}_{Side}_PositionQty * L{leg}_{Side}_PositionAvgPrice

如果 leg 显式配置了 `budget_cap > 0`，`L{leg}_BudgetCap` 使用该配置值。
如果是单腿策略或 L0，且 `budget_cap = 0`，Virtual 模式下 `L{leg}_BudgetCap` 使用虚拟账户当前权益，首次账户不存在时才回退到 `StrategyBankroll`。
如果是多腿策略的非 L0 leg，`budget_cap = 0` 表示该 leg 没有交易预算；`L{leg}_BudgetCap` 保持 0，`SETPOS` 会跳过加仓。
`L{leg}_ConfiguredBudgetCap` 始终保留用户配置值，策略可以用它区分“显式预算”和“动态预算”。
如果 `StrategyBankroll <= 0` 但 leg 配置了 `budget_cap > 0`，虚拟账户初始化资金必须从所有 leg 的 `budget_cap` 合计派生；不能出现 `L{leg}_BudgetCap > 0` 但 `virtual_account.cash = 0` 的资金错配。

如果目标成本 > 当前成本：
    请求买入数量 = (目标成本 - 当前成本) / 当前 AskPrice
    最大可买数量 = cash / (AskPrice * (1 + fee_rate * (1 - AskPrice)))
    实际买入数量 = min(请求买入数量, 最大可买数量)
    如果实际买入数量低于最小成交量，则跳过并记录 insufficient_cash_for_setpos

如果目标成本 < 当前成本：
    目标数量 = 目标成本 / 当前持仓均价
    卖出数量 = 当前实际 PositionQty - 目标数量

执行层必须使用当前实际 PositionQty / PositionAvgPrice 作为已有仓位，不允许用卖出 BidPrice 反推减仓数量。
执行层必须使用虚拟账户当前 cash 约束 SETPOS 买入，不允许因为目标成本来自 BudgetCap 就透支成交。
```

兼容旧字段：

```json
{
  "type": "SETPOS",
  "leg": 0,
  "side": "Yes",
  "pct": 0.6
}
```

新策略推荐使用 `target_pct`。

## 15. BUY_NOTIONAL 动作

用于按金额买入。

```json
{
  "type": "BUY_NOTIONAL",
  "leg": 0,
  "side": "Yes",
  "notional": 20,
  "price": "nowprice"
}
```

字段说明：

```text
notional   买入金额
price      null / "nowprice" / 阿拉伯数字
```

资金约束：

```text
BUY_NOTIONAL 会按当前 cash 裁剪到最大可买金额；低于最小成交量时跳过并记录 insufficient_cash。
```

## 16. SELL_NOTIONAL 动作

用于按金额卖出。

```json
{
  "type": "SELL_NOTIONAL",
  "leg": 0,
  "side": "No",
  "notional": 10,
  "price": "nowprice"
}
```

## 17. CANCEL 动作

用于撤单。

```json
{
  "type": "CANCEL",
  "leg": 0,
  "side": "Yes",
  "scope": "open_buy"
}
```

`scope` 可选：

```text
all
open_buy
open_sell
side
leg
```

## 18. CLOSE 动作

用于清空某个 side 的仓位。

```json
{
  "type": "CLOSE",
  "leg": 0,
  "side": "Yes",
  "price": "nowprice"
}
```

## 19. CLOSE_ALL 动作

用于清空某条 leg 的 Yes 和 No 仓位。

```json
{
  "type": "CLOSE_ALL",
  "leg": 0,
  "price": "nowprice"
}
```

## 20. WAKE 动作

用于请求未来唤醒。

```json
{
  "type": "WAKE",
  "after_seconds": 300,
  "reason": "wait_for_new_market_data"
}
```

## 21. 动作冲突规则

默认规则：

```text
actions 按数组顺序执行
同一个 leg + side 可以出现多个动作
执行层必须记录完整审计
```

建议策略避免同一轮对同一个 `leg + side` 同时输出：

```text
SETPOS + BUY
SETPOS + SELL
BUY + SELL
```

如果出现，执行层允许执行，但应记录 warning。

## 22. 数据质量规则

策略执行前建议检查：

```text
L{n}_MarketStatus == open
L{n}_{Side}_DataStatus == ok
L{n}_{Side}_AskPrice > 0
L{n}_{Side}_BidPrice > 0
```

如果数据异常，推荐输出空动作：

```json
{
  "schema_version": "1.0",
  "actions": [],
  "print": ["data not ok, skip"],
  "wake_reason": null
}
```

## 23. 兼容旧字段

系统短期可以继续输出旧字段：

```text
Yes_now_ask
Yes_now_bid
No_now_ask
No_now_bid
Yes_now_Qty
No_now_Qty
Yes_now_avgPrice
No_now_avgPrice
BudgetCap
```

但新策略不推荐使用。

旧字段对应关系：

```text
Yes_now_ask        -> L0_Yes_AskPrice
Yes_now_bid        -> L0_Yes_BidPrice
No_now_ask         -> L0_No_AskPrice
No_now_bid         -> L0_No_BidPrice
Yes_now_Qty        -> L0_Yes_PositionQty
No_now_Qty         -> L0_No_PositionQty
Yes_now_avgPrice   -> L0_Yes_PositionAvgPrice
No_now_avgPrice    -> L0_No_PositionAvgPrice
BudgetCap          -> L0_BudgetCap
```

## 24. 推荐策略代码风格

策略代码中建议封装读取函数：

```python
def get_ud(usedata, key, default=None):
    return usedata.get(key, default)

def get_price(usedata, leg, side, field):
    return usedata.get(f"L{leg}_{side}_{field}", 0)

def get_qty(usedata, leg, side):
    return usedata.get(f"L{leg}_{side}_PositionQty", 0)
```

动作输出建议封装：

```python
def Buy(leg, side, qty, price="nowprice"):
    return {
        "type": "BUY",
        "leg": int(leg),
        "side": side,
        "qty": float(qty),
        "price": price,
    }

def Sell(leg, side, qty, price="nowprice"):
    return {
        "type": "SELL",
        "leg": int(leg),
        "side": side,
        "qty": float(qty),
        "price": price,
    }

def SetPos(leg, side, target_pct):
    return {
        "type": "SETPOS",
        "leg": int(leg),
        "side": side,
        "target_pct": float(target_pct),
    }
```

## 25. 最小策略输出示例

```json
{
  "schema_version": "1.0",
  "actions": [
    {
      "type": "BUY",
      "leg": 0,
      "side": "Yes",
      "qty": 12,
      "price": "nowprice"
    }
  ],
  "print": ["buy 12 Yes at nowprice"],
  "wake_reason": null
}
```

## 26. 第一阶段落地范围

第一阶段建议实现：

```text
1. UseData 新增标准字段
2. 新策略只使用 L{leg}_{Side}_{Field}
3. 增加 LegCount
4. FunctionJson 支持 schema_version
5. BUY / SELL 的 price 支持 null / "nowprice" / 数字
6. SETPOS 支持 target_pct，同时兼容 pct
7. Output2 统一为 CodeIsOk
8. 保留旧字段兼容
```

第一阶段不强制实现：

```text
BUY_NOTIONAL
SELL_NOTIONAL
CANCEL
CLOSE
CLOSE_ALL
WAKE 的真实调度
复杂执行约束
```

但这些动作名称和字段先保留在规范中，方便后续扩展。
## 2026-05-15 补充：策略参数 UI

Dashboard 与 Strategy Workspace 会读取策略代码 `Inputs` 和 `FunctionIntroduction` 来生成参数表单。

详细规则见 [strategy-parameter-ui.md](strategy-parameter-ui.md)，包括：

- 参数名旁 `?` 提示如何从 `FunctionIntroduction` 解析。
- 默认参数如何从 `Default` / `Num` / `Context` 同步。
- UseData 自动填入按钮什么时候显示。
- 新增策略时 draft UseData 为什么必须依赖 `Condition ID`。
- `start_day` 如何从 `UseData.day_to_end` 初始化，并在保存后固定为用户参数。

## 2026-05-19 补充：UseData v2 与多资产动作

策略系统开始支持多资产上下文。旧的 `L{leg}_{Side}_{Field}` 字段继续保留，新策略推荐优先使用结构化字段：

```python
params = usedata.get("Params", {})
state = usedata.get("State", {})
instruments = usedata.get("Instruments", [])
portfolio = usedata.get("Portfolio", {})
```

`UseData["Instruments"]` 中每个元素代表一个策略绑定标的，包含：

```json
{
  "index": 0,
  "instrument_id": "crypto:binance:BTCUSDT",
  "leg_kind": "spot",
  "asset_class": "crypto_spot",
  "venue": "binance",
  "symbol": "BTCUSDT",
  "budget_cap": 100,
  "configured_budget_cap": 100,
  "quote": {"bid": 65000, "ask": 65010, "last": 65005},
  "position": {"qty": 0.1, "avg_price": 62000}
}
```

策略持久状态不要写入 `UseData` 或全局变量。策略需要保存状态时，在 `FunctionJson` 中输出：

```json
{
  "state_updates": {
    "last_signal": "risk_on",
    "cooldown_until": null
  }
}
```

下一轮运行时这些状态会出现在 `UseData["RuntimeState"]`。`UseData["State"]` 暂时保留为 `RuntimeState` 的兼容别名。

如果策略需要读取人工可切换的状态机状态，应读取：

```python
strategy_state = usedata.get("StrategyState", {})
machine_state = strategy_state.get("state") or usedata.get("MachineState") or "auto"
```

`StrategyState` 存在 `strategy_state.namespace = machine`，和 `Stop / Virtual / Real` 的运行 `mode` 是两个概念。

如果策略需要读取用户人工干预，例如暂停开仓、强制平仓、风险缩放，应读取：

```python
user_state = usedata.get("UserState", {})
runtime = usedata.get("RuntimeState", {})

if user_state.get("manual_pause_open"):
    return {"actions": [], "print": ["manual pause open"]}
```

`UserState` 由 Dashboard 的 `State` 弹窗或状态 API 写入；策略代码不要通过 `state_updates` 修改 `UserState`。

新增动作：

```json
{"type": "SET_BINARY_TARGET", "instrument": 0, "outcome": "Yes", "target_pct": 0.6}
```

```json
{"type": "ORDER", "instrument": 1, "side": "BUY", "qty": 0.1, "price": "market"}
```

```json
{"type": "SET_TARGET", "instrument": 1, "target": 0.3, "target_type": "notional_pct"}
```

兼容规则：

- `SETPOS` / `BUY` / `SELL` 仍按 Polymarket Yes/No 语义执行。
- `SET_BINARY_TARGET` 是 `SETPOS` 的多资产命名版本，用于 Polymarket 二元 outcome。
- `ORDER` 和 `SET_TARGET` 用于非 Polymarket 标的，虚拟盘结果写入 `strategy_virtual_positions_v2` / `strategy_virtual_orders_v2`。
- 新策略不要依赖 Python 模块全局变量保存状态；每轮运行都应视为无状态函数调用，持久状态通过 `state_updates` 显式写回。

## 2026-05-21 补充：Schema 默认值与 Controls

策略文件可以声明：

```python
ParamsSchema = {}
ControlsSchema = {}
RuntimeStateSchema = {}
StateMachineSchema = {
    "default": "auto",
    "states": [
        {"value": "auto", "label": "Auto"},
        {"value": "holding", "label": "Holding"},
        {"value": "cooldown", "label": "Cooldown"},
        {"value": "manual_review", "label": "Manual Review"},
    ],
}
```

默认值写在策略代码里，数据库只保存 override。运行时：

```text
UseData["Params"]       = ParamsSchema defaults + input_json
UseData["Controls"]     = ControlsSchema defaults + strategy_state(user)
UseData["RuntimeState"] = RuntimeStateSchema defaults + strategy_state(runtime)
UseData["StrategyState"] = {"state": StateMachineSchema default + strategy_state(machine).state}
```

兼容别名：

```text
UseData["UserState"] = UseData["Controls"]
UseData["State"]     = UseData["RuntimeState"]
UseData["MachineState"] = UseData["StrategyState"]["state"]
```

推荐策略读取：

```python
controls = usedata.get("Controls", usedata.get("UserState", {}))
if controls.get("manual_pause_open"):
    return {"actions": [], "print": ["manual pause open"]}
```

## 2026-05-21 补充：LegsSchema

策略代码可以像声明 `Inputs` 一样声明固定腿：

```python
LegsSchema = [
    {"label": "Leg 1", "asset_class": "polymarket_binary", "venue": "polymarket", "required": True},
    {"label": "Leg 2", "asset_class": "crypto_spot", "venue": "binance", "symbol": "BTCUSDT", "required": False},
]
```

`LegsSchema` 的数量就是策略的固定 leg 数量。Dashboard / Workspace 不提供自由增删 leg；如果策略需要新增或删除 leg，应修改策略代码声明。未声明 `LegsSchema` 的旧策略默认使用 1 条 `polymarket_binary` leg。

更多 UI 和字段规则见 [fixed-legs-schema.md](fixed-legs-schema.md)。
