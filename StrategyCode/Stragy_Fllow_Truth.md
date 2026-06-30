# Stragy_Fllow_Truth 策略说明

`Stragy_Fllow_Truth.py` 是一个面向 Polymarket 二元事件的事实跟随策略。它适合用户已经对某个方向有独立判断，希望用盘口价格、MACD 动量、到期时间和短期冲击保护来自动管理仓位的场景。

核心原则：

```text
fair_price 决定值不值得买；
MACD 决定现在能不能买、能不能加、该不该继续拿；
时间和短期大跌保护决定什么时候必须收手或停止自动抄底；
止损、止盈、追踪止盈负责退出。
```

## 1. 仓位语义

新版策略不再要求用户填写 `max_position_pct`。用户在策略层或 leg 层填写的“占用资金/预算”就是最大可使用资金。

策略输出的 `target_pct` 统一表示：

```text
target_pct = 占用资金使用比例
```

例如：

```text
占用资金 = 100 USDC
target_pct = 0.35
实际目标投入 = 100 * 0.35 = 35 USDC
```

旧版 `max_position_pct` 仍被代码兼容读取，但它只作为老策略的隐藏上限，不建议新策略继续填写。

## 2. 核心价格变量

用户先选择事实方向：

```text
FactSide = Yes 或 No
```

然后填写自己认为该方向的真实概率：

```text
fair_price = 用户主观公平价格
```

策略使用两个边际：

```text
edge_to_buy  = fair_price - ask
edge_to_hold = fair_price - bid
```

- `edge_to_buy`：用于判断是否开仓或加仓。
- `edge_to_hold`：用于判断持仓是否已经失去估值边际。

例子：

```text
fair_price = 0.58
ask = 0.48
bid = 0.47

edge_to_buy  = 0.58 - 0.48 = 0.10
edge_to_hold = 0.58 - 0.47 = 0.11
```

## 3. 入场仓位计算

新版用三个参数把旧版隐含的 ramp 写清楚：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `entry_edge` | `0.05` | 开始允许买入的最低安全边际 |
| `full_entry_edge` | `0.16` | 达到该边际时，基础目标仓位升至 100% 占用资金 |
| `starter_position_ratio` | `0.25` | 刚达到 `entry_edge` 时的试仓比例 |

基础目标仓位公式：

```text
if edge_to_buy < entry_edge:
    edge_target = 0
else:
    progress = (edge_to_buy - entry_edge) / (full_entry_edge - entry_edge)
    progress = clamp(progress, 0, 1)

    edge_target = starter_position_ratio
                  + progress * (1 - starter_position_ratio)
```

例子：

```text
fair_price = 0.58
entry_edge = 0.06
full_entry_edge = 0.16
starter_position_ratio = 0.25
```

| ask | edge_to_buy | edge_target |
| ---: | ---: | ---: |
| `0.52` | `0.06` | `25%` |
| `0.47` | `0.11` | `62.5%` |
| `0.42` | `0.16` | `100%` |

## 4. MACD 动量状态

MACD 是策略核心，不只是止盈过滤器。它同时参与：

- 开仓
- 加仓
- 止盈时是否继续持有
- 下跌时是否禁止自动抄底

策略读取：

```text
macd_hist
macd_hist_slope = macd_hist - previous_macd_hist
```

动量状态：

| 状态 | 条件 | 行为含义 |
| --- | --- | --- |
| `strong_up` | `macd_hist >= momentum_hist_min` 且 `macd_hist_slope >= momentum_slope_min` | 上涨动量强，允许正常开仓/加仓；盈利时优先继续持有 |
| `weak_up` | `macd_hist > 0` 但不满足强动量 | 趋势仍偏正；开仓/加仓允许大部分仓位，止盈时可部分落袋 |
| `recovery_up` | MACD 仍偏弱，但短期 bid 已明显抬高或重新创新高，且没有触发大跌保护 | 视为价格先于 MACD 恢复；盈利时优先持有并用追踪止盈保护 |
| `neutral` | MACD 不强且未明确转弱 | 方向不清，只允许小仓观察 |
| `flat` | `macd_hist <= 0`，但近期 bid 未从短期高点跌破 `shock_drop_pct` | 视为横盘吸筹，不再因为 MACD 滞后把仓位压到极低 |
| `down` | `macd_hist <= 0`，且近期 bid 已从短期高点回撤达到 `shock_drop_pct`，或仍在大跌冷却期 | 视为事实面可能恶化，禁止自动开仓/加仓 |
| `missing` | MACD 数据缺失 | 按缺失 MACD 系数保守处理 |

MACD 的内部参数不再建议用户逐项设置。策略默认使用快速事件参数 `6/13/5`，并把旧版过低的入场系数限制在合理下限，避免出现“有明显折价但只建 10%”的问题。

MACD 修正公式：

```text
momentum_adjusted_target = edge_target * entry_momentum_multiplier
```

默认乘数：

```text
strong_up -> 1.00
weak_up   -> 至少 0.75
recovery_up -> weak_up_entry_multiplier
neutral   -> 至少 0.70
flat      -> 至少 0.70
missing   -> 至少 0.50
down      -> 0.00
```

因此，MACD 现在只在两种地方强约束：价格实际出现短期大跌时禁止自动抄底；盈利后价格仍在走强时帮助继续持有。若只是 MACD 稍负但价格横盘且仍低于 `fair_price`，策略会继续按折价吸筹逻辑加仓。

## 5. 短期大跌保护

事件市场里，短期大跌往往意味着事实面变化，而不一定是“更便宜”。策略会记录最近窗口内的最高事实方向 bid：

```text
recent_peak_bid
```

回撤计算：

```text
recent_drop_from_peak = 1 - current_bid / recent_peak_bid
```

如果：

```text
recent_drop_from_peak >= shock_drop_pct
且 MACD 不是 strong_up
```

则触发短期大跌保护：

```text
禁止开仓
禁止加仓
允许减仓、止损、止盈
```

相关参数：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `shock_lookback_minutes` | `30` | 短期 peak bid 回看窗口 |
| `shock_drop_pct` | `0.15` | 从短期 peak bid 回撤达到该比例时触发保护 |
| `shock_cooldown_minutes` | `60` | 触发后禁止自动开仓/加仓的冷却时间 |

## 6. 到期时间管理

Polymarket 的事件越接近截止日期，价格越可能快速接近 0 或 1。临近到期时不能只按 `fair_price - ask` 机械加仓。

策略使用两个时间参数：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `de_risk_start_days` | `5.0` | 距离到期少于该天数时开始线性降风险 |
| `no_add_days` | `1.0` | 距离到期少于该天数时禁止自动开仓/加仓 |
| `min_time_position_ratio` | `0.0` | 时间降风险时保留的最低系数 |

时间降风险系数：

```text
time_multiplier = clamp(days_to_end / de_risk_start_days,
                        min_time_position_ratio,
                        1)
```

时间修正：

```text
time_adjusted_target = momentum_adjusted_target * time_multiplier
```

如果：

```text
days_to_end <= no_add_days
```

则：

```text
不允许自动开仓/加仓
只允许减仓或退出
```

注意：如果市场标题、选项日期和系统传入的 `EndTime/day_to_end` 冲突，应先修复市场日期数据，避免错误触发到期降风险。

## 7. 最终目标仓位

正常开仓/加仓时，目标仓位可以理解为：

```text
target_pct =
    edge_target
    * entry_momentum_multiplier
    * time_multiplier
    * risk_scale
```

并受以下限制：

```text
force_flat=True          -> target_pct = 0
manual_pause_open=True   -> 禁止开仓/加仓
machine_state=stop_loss_locked -> 禁止开仓/加仓，等待人工切回 auto 等状态
shock_block_active=True  -> 禁止开仓/加仓
days_to_end <= no_add_days -> 禁止开仓/加仓
cooldown_active=True     -> 禁止开仓/加仓
```

已有仓位时，普通估值逻辑不会因为目标下降就立刻卖出；真正减仓主要来自止损、止盈、边际退出、到期降风险、追踪止盈或人工控制。

## 8. 退出条件

退出优先级：

1. `force_flat=True`：强制清仓。
2. 报价无效：保持当前仓位。
3. 止损：直接清仓，不受 MACD 保护。
4. 追踪止盈：进入利润保护后，从 `peak_bid` 回撤过大则清仓。
5. MACD 破坏：进入利润保护后，若状态进入 `down` 才清仓；单纯横盘偏弱的 `flat` 不触发这一条。
6. 止盈触发：根据 MACD 决定持有、部分止盈或全平。
7. 边际退出：根据 MACD 决定持有、降到核心仓或全平。
8. 到期降风险：按时间系数降低目标仓位。

## 9. 止损

止损比例：

```text
pnl_pct = bid / entry_price - 1
```

如果：

```text
pnl_pct <= -stop_loss_pct
```

则：

```text
target_pct = 0
```

止损不受 MACD 保护。

止损清仓成功后，策略会把机器状态写为：

```text
machine_state = stop_loss_locked
```

该状态会阻止后续自动开仓/加仓。用户需要在工作台或首页把 State 手动切回 `auto` 等非锁定状态后，策略才会重新允许按估值边际入场。

## 10. 止盈和 MACD 动量持有

止盈触发：

```text
pnl_pct >= take_profit_pct
```

触发后进入利润保护状态 `profit_protected=True`。

处理方式：

| MACD 状态 | 行为 |
| --- | --- |
| `strong_up` | 不卖，进入 `momentum_hold_take_profit`，继续持有 |
| `recovery_up` 或价格继续创新高 | 不卖，进入 `price_recovery_hold_take_profit`，用追踪止盈保护利润 |
| `weak_up` | 第一次卖出 `partial_take_profit_ratio` |
| `flat` | 只执行一次部分止盈，不再直接全卖 |
| `down/missing/disabled` | 全仓止盈 |

部分止盈公式：

```text
target_pct = current_position * (1 - partial_take_profit_ratio)
```

例子：

```text
current_position = 60%
partial_take_profit_ratio = 35%

target_pct = 60% * (1 - 0.35) = 39%
```

即卖出当前仓位的 35%，不是卖出总资金的 35%。

## 11. 边际退出

边际退出触发：

```text
edge_to_hold <= exit_edge
```

也就是：

```text
fair_price - bid <= exit_edge
```

处理方式：

| MACD 状态 | 行为 |
| --- | --- |
| `strong_up` | 不卖，进入 `momentum_hold_edge_exit` |
| `recovery_up` 或价格继续创新高 | 不卖，进入 `price_recovery_hold_edge_exit` |
| `weak_up` | 降到核心仓 `core_position_ratio` |
| `flat` | 降到核心仓 `core_position_ratio` |
| `down/missing/disabled` | 全部退出 |

核心仓公式：

```text
target_pct = current_position * core_position_ratio
```

## 12. 追踪止盈

持仓后策略记录最高事实方向 bid：

```text
peak_bid = max(previous_peak_bid, current_bid)
```

回撤：

```text
drawdown_from_peak = 1 - current_bid / peak_bid
```

利润保护后，如果：

```text
drawdown_from_peak >= active_trailing_stop_pct
```

则退出。

追踪止盈阈值：

```text
MACD strong_up -> trailing_stop_pct
其他状态       -> weak_trailing_stop_pct
```

## 13. 用户参数

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `FactSide` | `Yes` | 认为最终会成真的方向 |
| `fair_price` | `0.65` | 用户主观公平价格 |
| `entry_edge` | `0.05` | 最低开仓安全边际 |
| `full_entry_edge` | `0.16` | 满预算使用时需要的安全边际 |
| `starter_position_ratio` | `0.25` | 初次达到入场边际时的试仓比例 |
| `exit_edge` | `0.01` | 持仓边际低于该值时考虑退出 |
| `stop_loss_pct` | `0.25` | 止损比例 |
| `take_profit_pct` | `0.40` | 进入利润保护的收益阈值，不等于立刻全卖 |
| `trailing_stop_pct` | `0.12` | 利润保护后允许从最高 bid 回撤的比例 |
| `de_risk_start_days` | `5.0` | 到期降风险开始天数 |
| `no_add_days` | `1.0` | 到期禁止自动加仓天数 |
| `shock_drop_pct` | `0.15` | 短期大跌触发阈值 |
| `min_target_delta` | `0.03` | 普通调仓最小仓位差 |

以下参数仍被代码兼容，但默认在界面隐藏：MACD 快慢线、MACD 阈值、各类动量乘数、部分止盈比例、核心仓比例、止盈挂单细节、短跌回看窗口、冷却时间等。隐藏它们是为了避免用户每次都要调一堆细枝末节，同时保留老配置可读取。

## 14. Controls

| 控制项 | 默认值 | 说明 |
| --- | ---: | --- |
| `manual_pause_open` | `False` | 暂停开新仓或加仓，但不阻止止盈、止损和平仓 |
| `force_flat` | `False` | 强制把事实方向和反方向目标仓位都设为 0 |
| `risk_scale` | `1.0` | 临时缩放目标仓位，范围 `0-1` |
| `debug_raw_inputs` | `False` | 输出原始参数和 UseData 字段，便于排查 |

## 15. RuntimeState

`RuntimeState` 是策略自动维护的运行记忆，不建议用户频繁手动修改。

关键字段：

| 状态 | 说明 |
| --- | --- |
| `entry_price` | 最近一次开仓或加仓记录的入场价 |
| `peak_bid` | 持仓后观察到的最高事实方向 bid，用于追踪止盈 |
| `momentum_hold_since` | MACD 强势导致延迟退出的开始时间 |
| `profit_protected` | 是否已经进入利润保护状态 |
| `partial_tp_done` | 是否已经执行过首次部分止盈 |
| `last_macd_hist` | 上一轮 MACD histogram，用于计算斜率 |
| `recent_peak_bid` | 短期大跌保护用的窗口内最高 bid |
| `recent_peak_bid_at` | `recent_peak_bid` 对应时间 |
| `shock_cooldown_until` | 短期大跌保护冷却结束时间 |
| `last_valid_day_to_end` | 最近一次可信剩余天数 |
| `last_valid_day_to_end_at` | 最近一次可信剩余天数的观测时间 |

## 16. 输出格式

策略输出统一为 FunctionJson：

```json
{
  "schema_version": "2.0",
  "actions": [
    {
      "type": "SETPOS",
      "side": "Yes",
      "target_pct": 0.35,
      "leg": 0,
      "desc": "Set Yes target to 0.3500 (entry)."
    }
  ],
  "metrics": {},
  "print": [],
  "wake_reason": null,
  "state_updates": {},
  "machine_state_updates": {}
}
```
