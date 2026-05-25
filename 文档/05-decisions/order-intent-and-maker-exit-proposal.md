# Order Intent 与 Maker 止盈挂单改造提案

日期：2026-05-23
状态：待审阅，不代表已实现

## 背景

当前策略系统主要用 `SETPOS` 表达交易意图：

```json
{"type": "SETPOS", "side": "Yes", "target_pct": 0.5}
```

这种模型适合表达“目标仓位是多少”，但不适合表达订单簿层面的意图，例如：

- 入场后立即预挂止盈卖单。
- 止盈单必须是 maker / post-only，不能直接吃单。
- 止盈单只能减仓，不能在仓位变化后留下超额卖单。
- 止损、强平、边际退出触发时，必须先撤销未成交止盈单，再执行风险退出。
- 模拟盘需要真实复现挂单、部分成交、撤单、订单状态变化，而不是所有订单立即 filled。

因此，策略系统需要在现有 `SETPOS` 之上增加“订单意图层”。旧策略继续使用目标仓位模型，新策略可以选择输出限价挂单、撤单和替换挂单动作。

## 外部接口依据

Polymarket CLOB API 文档中与本改造相关的能力包括：

- `POST /order`：提交订单，支持 `orderType` 和 `postOnly`。
- `DELETE /order`：按订单 id 撤单。
- `DELETE /orders`：批量撤单。
- `GET /data/orders`：查询用户订单，可按市场、asset id、状态等过滤。
- `GET /book`：查询订单簿，返回 bid/ask、tick size、minimum order size 等交易约束。
- `GET /tick-size`：查询 token 的最小价格跳动。
- User WebSocket：推送用户订单和成交事件，用于同步真实订单状态。

重要语义：

- `postOnly=true` 表示订单必须添加流动性，不能立即成交。
- `postOnly` 只适合 `GTC` / `GTD` 类型订单。
- maker rebate 只有在订单先挂在簿上、随后被其他 taker 吃掉时才可能获得。
- Polymarket API 未必提供原生 `reduce_only` 字段，因此 reduce-only 必须由本系统在下单前、同步后和退出前自行约束。

## 设计目标

1. 向后兼容：现有 `SETPOS`、`BUY`、`SELL` 策略不需要修改。
2. 支持预挂止盈：策略入场后可提前挂 post-only sell，等待市场成交。
3. 支持风险退出安全：止损、强平、清仓、边际退出前自动撤销相关 reduce-only 挂单。
4. 支持模拟盘：Virtual 模式中订单不再只能立即成交，也可以保持 open、partial、canceled、expired。
5. 支持真实盘：Real 模式通过 CLOB API 下单、撤单、同步订单和成交。
6. 保持策略代码简单：策略负责表达交易意图，后台负责订单生命周期和安全约束。
7. 支持同标的多策略：多个策略、多个 leg 可以指向同一个 `condition_id` / `token_id`，系统必须能区分归属，不能只靠策略设计规避。

非目标：

- 不在本次设计中实现跨市场做市策略。
- 不把 maker rebate 作为确定收益计入策略 PnL，只作为后续统计字段。
- 不要求所有策略都使用订单意图层。旧策略继续可用。

## 标的归属与同 token 冲突

同一个 Polymarket token 可能同时出现在多个地方：

```text
Strategy A / Leg 0 / Yes token = T1
Strategy B / Leg 0 / Yes token = T1
Strategy C / Leg 2 / Yes token = T1
Manual wallet holding / token = T1
```

这不是异常场景，而是策略系统必须支持的基本场景。不能要求用户永远避免同标的复用。真正需要避免的是“只按 token_id 合并仓位或订单”，因为这样会把不同策略的仓位、止盈单、止损退出混在一起。

### 核心原则

真实钱包持仓是全局的，策略持仓是分账的：

```text
wallet_position(token_id=T1) = 1000 shares

strategy_position(strategy_id=A, leg_uid=A0, token_id=T1) = 300 shares
strategy_position(strategy_id=B, leg_uid=B0, token_id=T1) = 500 shares
unassigned_position(token_id=T1)                         = 200 shares
```

系统内部所有自动交易、止盈挂单、止损退出都必须基于策略子账本，而不是基于钱包总持仓。

### 不可变 leg 身份

`leg_index` 只能作为展示顺序和策略数组位置，不能作为长期归因主键。原因：

- 用户可能调整 leg 顺序。
- 用户可能把某个 leg 的市场换成另一个市场。
- 同一个策略内可能有多个 leg 指向同一个 token。
- 历史订单和新订单都叫 `leg_index=0` 时，无法判断是不是同一个标的槽位。

因此需要新增不可变 `leg_uid`：

```text
strategy_id + leg_uid + token_id + outcome
```

`leg_uid` 在 leg 创建时生成，之后即使 `leg_index` 改变也不变。若用户明确替换 leg 的标的身份，应生成新的 `leg_uid`，旧 leg 的虚拟持仓和挂单必须进入归档、清理或人工迁移流程。

### 订单归因规则

真实盘成交回报不能靠 `token_id` 猜归属。必须在下单时建立映射：

```text
client_order_id / clob_order_id
  -> strategy_id
  -> leg_uid
  -> leg_index_snapshot
  -> token_id
  -> outcome
  -> client_order_tag
```

成交同步时，通过 `clob_order_id` 或 `client_order_id` 回查本地映射，再更新对应策略子账本。只有这样，同一个 token 的多个策略订单才不会互相污染。

### 无法自动归因的持仓

以下来源不能假装知道属于哪个策略：

- 系统上线前已有的钱包持仓。
- 用户在外部手动交易产生的持仓。
- 缺少 `client_order_id` 映射的历史订单。
- CLOB 回报中找不到本地订单映射的成交。

这些仓位必须进入：

```text
unassigned_position
manual_assignment_required
```

后续可提供 UI 让用户把未归属仓位分配给某个策略/leg。未分配之前，自动策略不能默认使用这些仓位作为自己的可卖数量。

### Virtual 与 Real 的差异

Virtual 模式天然更容易分账，因为虚拟持仓由系统自己写入。即便多个策略使用同一个 token，也应按 `strategy_id + leg_uid + outcome` 分开记账。

Real 模式更复杂，因为钱包只有全局净持仓。Real 模式必须同时维护：

```text
wallet_positions            # 钱包真实总仓
strategy_real_positions     # 系统可归因的策略子仓
unassigned_positions        # 无法归因或手工持仓
```

一致性检查：

```text
sum(strategy_real_positions[token_id]) + unassigned_positions[token_id]
    <= wallet_positions[token_id]
```

如果本地策略子仓合计大于钱包真实仓位，必须暂停该 token 的自动卖出并进入 reconcile 状态。

### 第一版真实盘限制

在 `leg_uid`、订单映射和策略子账本完成前，不应开放真实盘多策略共享同 token 的自动交易。

第一版 Real 模式建议限制为：

- 允许检测同 token 多策略，但默认标记为 `shared_instrument_risk`。
- 只允许系统可归因订单更新策略子仓。
- 只允许按策略子仓执行 reduce-only 卖出。
- 未归属仓位不得自动用于任何策略止盈或止损。
- 若发现同 token 多策略且缺少完整归因字段，真实下单应 blocked，并提示需要完成账本迁移。

## 动作协议扩展

### 保留旧动作

以下动作继续有效：

```text
SETPOS
SET_BINARY_TARGET
SET_TARGET
BUY
SELL
BUY_NOTIONAL
SELL_NOTIONAL
CLOSE
CLOSE_ALL
```

旧动作仍由执行层转换为立即成交或目标仓位调整。

### 新增 `PLACE_ORDER`

用于表达限价挂单。

```json
{
  "type": "PLACE_ORDER",
  "leg": 0,
  "leg_uid": "leg_01HX...",
  "outcome": "Yes",
  "side": "SELL",
  "qty": 100,
  "price": 0.56,
  "order_type": "GTC",
  "post_only": true,
  "reduce_only": true,
  "client_order_tag": "take_profit",
  "replace_policy": "same_tag",
  "reason": "preplaced_take_profit"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `leg` | 策略 leg index，默认 0。 |
| `leg_uid` | 不可变 leg 身份。新策略和后台生成动作应提供；旧策略缺失时由执行层按当前 leg 映射补齐。 |
| `outcome` | 二元市场方向，`Yes` 或 `No`。 |
| `side` | 交易方向，`BUY` 或 `SELL`。 |
| `qty` | 订单数量。 |
| `price` | 限价价格，必须按 tick size 对齐。 |
| `order_type` | `GTC` / `GTD` / `FAK` / `FOK`。post-only 只允许 `GTC` / `GTD`。 |
| `post_only` | 是否强制 maker-only。 |
| `reduce_only` | 系统内部语义，只允许减少已有仓位。 |
| `client_order_tag` | 策略内订单标签，例如 `take_profit`。 |
| `replace_policy` | 同标签已有订单时如何处理，建议支持 `same_tag` / `keep_existing` / `cancel_then_place`。 |
| `reason` | 审计用途。 |

### 新增 `CANCEL_ORDER`

用于撤销指定订单或同标签订单。

```json
{
  "type": "CANCEL_ORDER",
  "leg": 0,
  "leg_uid": "leg_01HX...",
  "outcome": "Yes",
  "client_order_tag": "take_profit",
  "reason": "stop_loss"
}
```

### 新增 `REPLACE_ORDER`

用于替换同标签订单。执行层可以实现为 cancel-then-place。

```json
{
  "type": "REPLACE_ORDER",
  "leg": 0,
  "leg_uid": "leg_01HX...",
  "outcome": "Yes",
  "side": "SELL",
  "qty": 120,
  "price": 0.58,
  "order_type": "GTC",
  "post_only": true,
  "reduce_only": true,
  "client_order_tag": "take_profit",
  "reason": "avg_price_changed"
}
```

## Follow Truth 的预挂止盈流程

`follow truth` 不应等 `pnl_pct >= take_profit_pct` 后才卖出，而应在持仓建立后预挂止盈单。

推荐流程：

```text
1. 策略发现有入场边际，输出 SETPOS 增仓。
2. 执行层成交后更新 qty / avg_price。
3. 下一轮或同轮后处理阶段计算止盈价：
   take_profit_price = avg_price * (1 + take_profit_pct)
4. 按 tick size 对齐止盈价。
5. 对当前可卖数量挂 PLACE_ORDER SELL post_only reduce_only。
6. 若继续加仓，按新的 avg_price 和 qty 替换止盈单。
7. 若止盈单成交，订单同步层更新仓位，不再额外触发 SETPOS 清仓。
```

示例：

```text
买入 Yes 100 @ 0.40
take_profit_pct = 0.40
止盈价 = 0.56
后台挂 SELL Yes 100 @ 0.56, GTC, post_only, reduce_only, tag=take_profit
```

## 止损与撤单安全

提前挂止盈后，止损触发会产生订单竞态：

```text
已有 open take_profit sell
市场下跌触发 stop_loss
系统需要卖出止损
```

如果不先撤止盈单，可能出现：

- 止损卖出成交后，原止盈单仍挂着。
- 止盈单随后成交，导致超额卖出或失败订单。
- 系统仓位、现金、审计事件出现错位。

因此，撤单保护必须放在交易系统中，而不是要求每个策略手写。

### 后台默认规则

当执行层发现某个动作会降低仓位时：

```text
1. 查找同 strategy_id、leg、outcome 的 open reduce-only 订单。
2. 如果动作原因是 stop_loss / force_flat / edge_exit / close：
   撤销全部相关 reduce-only 订单。
3. 等待撤单确认或进入状态同步。
4. 重新读取最新持仓和 open orders。
5. 只对剩余可卖数量执行风险退出。
```

### 风险动作优先级

| 动作/原因 | 是否先撤止盈单 | 后续行为 |
| --- | --- | --- |
| `stop_loss` | 是 | 撤单后按可卖数量退出。 |
| `force_flat` | 是 | 撤全部相关挂单，尽快清仓。 |
| `edge_exit` | 是 | 撤单后退出，可按策略配置选择 maker/taker。 |
| `de_risk` | 视减仓幅度 | 缩小或撤销超额 reduce-only 订单。 |
| 普通加仓 | 否 | 更新或补挂止盈单。 |
| 普通减仓 | 是，至少缩单 | 避免 open sell qty 超过持仓。 |

### 撤单竞态处理

撤单请求发出后，真实交易系统可能遇到：

- 撤单成功。
- 撤单失败。
- 订单已经成交。
- 订单部分成交后剩余部分撤销。
- API 超时但订单实际已撤或已成交。

执行层必须使用状态机处理，而不是只看单次 API 返回。

推荐状态流：

```text
open -> cancel_requested -> canceled
open -> partially_filled -> cancel_requested -> canceled
open -> filled
cancel_requested -> unknown -> sync_required
unknown -> open / canceled / filled / partially_filled
```

对于 `unknown`，执行层不应盲目按旧持仓卖满仓，应先同步订单和仓位。

## Reduce-only 约束

`reduce_only` 是系统内部约束，必须在三处执行：

1. 下单前：

```text
max_sell_qty = position_qty - existing_open_reduce_sell_qty
place_qty = min(requested_qty, max_sell_qty)
```

2. 仓位变化后：

```text
if open_reduce_sell_qty > position_qty:
    cancel_or_replace_reduce_only_orders()
```

3. 风险退出前：

```text
cancel_reduce_only_orders_first()
sync_position()
sell_remaining_position()
```

这条规则在 Virtual 和 Real 模式都必须一致。

对于同 token 多策略，`position_qty` 必须来自策略子账本，而不是钱包总仓：

```text
Virtual position_qty = strategy_virtual_positions(strategy_id, leg_uid, outcome).qty
Real position_qty    = strategy_real_positions(strategy_id, leg_uid, token_id, outcome).qty
```

如果 Real 模式只知道钱包总仓、但不知道策略子仓，reduce-only 不能通过。此时订单应 blocked：

```text
reason = missing_strategy_position_attribution
```

## 模拟盘设计

Virtual 模式不能继续把所有 `ORDER` 都直接写成 `filled`。为了验证预挂止盈，需要新增虚拟挂单簿语义。

### Virtual 订单状态

`strategy_virtual_orders` 建议扩展状态：

```text
open
partially_filled
filled
canceled
expired
blocked
failed
```

当前表的 `status` 只允许 `filled / blocked / failed`，需要迁移。

Virtual 持仓和挂单必须按 `strategy_id + leg_uid + outcome` 隔离。同一个 token 被多个策略或多个 leg 使用时，不允许合并为一条虚拟持仓。

### Virtual 下单逻辑

对于 `PLACE_ORDER`：

```text
if post_only and order would cross current book:
    status = blocked
    reason = post_only_would_cross
elif reduce_only and qty > available_sell_qty:
    qty = available_sell_qty
    if qty <= 0: blocked no_available_position
else:
    status = open
```

Virtual 中的 post-only 挂单不应立即成交，即使价格接近市场，也必须先进入 `open`。

### Virtual 成交模拟

每个 tick 后，执行层或订单同步层检查 open orders：

```text
SELL order filled when best_bid >= order.price
BUY order filled when best_ask <= order.price
```

初期可以全量成交，后续再扩展为按盘口数量部分成交：

```text
fill_qty = min(remaining_qty, available_opposite_book_qty)
```

费用和 rebate：

- taker fee 继续按现有 `_calc_fee()` 估算。
- maker order 成交时 fee 记为 0。
- maker rebate 不建议立即计入 PnL，可单独记录 `estimated_maker_rebate` 或留空，避免把不确定分配当作确定收益。

### Virtual 撤单

`CANCEL_ORDER` 在 Virtual 中更新订单状态：

```text
open / partially_filled -> canceled
filled -> no-op, reason=already_filled
canceled -> no-op
```

撤单后必须重新计算 open sell qty 和 available sell qty。

## 真实盘设计

Real 模式需要新增 `PolymarketOrderManager`，职责包括：

1. 从 strategy leg 找到 token id。
2. 查询或缓存 tick size、minimum order size、订单簿。
3. 对价格和数量做合法化。
4. 构造签名订单。
5. 调用 CLOB `POST /order`。
6. 调用 CLOB 撤单接口。
7. 通过 `GET /data/orders` 和 User WebSocket 同步状态。
8. 将真实订单状态写回本地订单表和审计事件。

真实盘必须避免由策略代码直接访问私钥或 API key。策略只输出 FunctionJson，签名和认证由后台执行层处理。

### Real post-only 行为

下单前应先用本地 orderbook 预检：

```text
SELL post-only price <= best_bid: 不提交，标记 blocked/post_only_would_cross
BUY post-only price >= best_ask: 不提交，标记 blocked/post_only_would_cross
```

即使预检通过，提交到 CLOB 后仍可能因行情变化被拒绝。执行层应记录失败原因，并允许下一轮策略重新报价。

### Real 归因与共享标的限制

真实盘下单前必须完成归因检查：

```text
1. action 必须能解析到 strategy_id + leg_uid + token_id + outcome。
2. reduce-only SELL 必须能读取 strategy_real_positions 中的策略子仓。
3. 同 token 存在多个策略时，不能用 wallet position 代替策略子仓。
4. 找不到订单归因映射的成交只能进入 unassigned_positions。
```

如果任一条件不满足，真实下单应被阻止：

```text
status = blocked
reason = attribution_required
```

这条限制比“尽快下单”更重要。因为在同 token 多策略场景下，错误卖出其他策略的仓位，比错过一次止盈更严重。

## 数据库修改建议

### 新增 `strategy_order_intents`

记录策略最近一次表达的订单意图，便于幂等处理。

```text
id
strategy_id
leg_uid
leg_index
leg_index_snapshot
condition_id
token_id
outcome
side
price
qty
order_type
post_only
reduce_only
client_order_tag
replace_policy
reason
raw_action_json
status
created_at_utc
updated_at_utc
```

### 新增或扩展 `strategy_open_orders`

统一 Virtual / Real 的 open order 视图。

```text
id
strategy_id
mode
leg_uid
leg_index
leg_index_snapshot
condition_id
token_id
outcome
side
price
qty
filled_qty
remaining_qty
order_type
post_only
reduce_only
client_order_tag
client_order_id
clob_order_id
status
last_error
created_at_utc
updated_at_utc
```

### 新增 `strategy_real_positions`

记录真实盘中“系统可归因”的策略子仓。它不是钱包总仓，而是由本系统订单成交、或用户手工分配后形成的策略账本。

```text
id
strategy_id
leg_uid
leg_index_snapshot
condition_id
token_id
outcome
qty
avg_price
cost
realized_pnl
source
updated_at_utc
UNIQUE(strategy_id, leg_uid, token_id, outcome)
```

`source` 建议支持：

```text
system_order
manual_assignment
reconcile_adjustment
```

### 新增 `unassigned_positions`

记录真实钱包中无法归因给策略的持仓。

```text
id
wallet_address
condition_id
token_id
outcome
qty
avg_price
source
reason
updated_at_utc
```

这张表用于承接历史持仓、外部手工交易和缺失订单映射的成交。未归属仓位不得被策略自动止盈或止损使用。

### 扩展 `strategy_legs`

新增不可变身份字段：

```text
leg_uid
```

迁移规则：

```text
1. 现有 leg 若缺少 leg_uid，按 strategy_id + leg_index 生成一次性 uid。
2. 后续调整 leg_index 不改变 leg_uid。
3. 若用户替换 condition_id / yes_token / no_token，应生成新的 leg_uid，旧 leg 状态进入归档或清理流程。
```

### 扩展真实订单表 `orders`

当前真实订单表已有 `strategy_id`、`condition_id`、`token_id`、`client_order_id`、`remote_order_id`，但缺少 leg 归属。需要新增：

```text
leg_uid
leg_index_snapshot
outcome
client_order_tag
post_only
reduce_only
liquidity_role
raw_order_json
```

真实成交同步必须优先通过 `remote_order_id` 或 `client_order_id` 找到本地订单，再回写对应 `strategy_real_positions`。不能只按 `token_id` 找策略。

### 扩展 `strategy_virtual_orders`

当前 Virtual 成交表偏“成交记录”，未来需要兼容挂单状态。建议新增字段：

```text
leg_uid
leg_index_snapshot
condition_id
token_id
remaining_qty
filled_qty
order_type
post_only
reduce_only
client_order_tag
external_order_id
updated_at_utc
```

并扩展 `status` 枚举。

如果担心影响现有 Trades 展示，也可以保留 `strategy_virtual_orders` 作为成交/尝试记录，另建 `strategy_virtual_open_orders` 管挂单状态。

## 执行层改造点

### `virtual_execution.py`

需要支持：

- `PLACE_ORDER`
- `CANCEL_ORDER`
- `REPLACE_ORDER`
- open order 状态更新
- reduce-only 约束
- cancel-before-risk-exit
- maker 成交 fee=0
- `leg_uid` 归因；旧动作缺少 `leg_uid` 时按当前 `strategy_id + leg_index` 补齐，但补齐结果必须写入审计事件。
- 同 token 多 leg 时，所有持仓、挂单、撤单都按 `leg_uid` 隔离。

### `virtual_runner.py`

每轮执行顺序建议调整为：

```text
1. 同步上一轮 open orders 的成交/撤单状态。
2. 构建最新 UseData，包含 open order qty。
3. 运行策略代码，得到 FunctionJson。
4. 执行风险优先动作。
5. 执行普通仓位动作。
6. 执行或更新订单意图。
7. 写入 state_updates / metrics / events。
```

### `virtual_context_builder.py`

UseData 需要提供更完整的挂单信息：

```text
L0_Yes_OpenBuyQty
L0_Yes_OpenSellQty
L0_Yes_OpenReduceSellQty
L0_Yes_AvailableSellQty
L0_Yes_TakeProfitOrderPrice
L0_Yes_TakeProfitOrderQty
L0_Yes_TakeProfitOrderStatus
```

这样策略可以知道是否已经有止盈单，不会重复输出无限多挂单。

UseData 还需要暴露 leg 的不可变身份，供策略输出订单动作时引用：

```text
L0_LegUid
L0_Yes_TokenId
L0_No_TokenId
```

旧策略可以继续只使用 `leg=0`；新策略或后台后处理生成的订单意图应带上 `leg_uid`，避免 leg 重排后订单归属漂移。

## 策略代码责任边界

策略应该负责：

- 判断是否开仓、加仓、减仓、止损、止盈。
- 输出目标仓位或订单意图。
- 决定止盈价、止损线、最大仓位等策略参数。

策略不应该负责：

- 私钥签名。
- CLOB 下单认证。
- 撤单重试。
- 订单状态同步。
- 部分成交后的持仓核对。
- stop-loss 前必须撤哪个真实订单。

这些必须由后台执行层统一处理。

## Follow Truth 参数建议

新增参数：

```text
take_profit_order_mode: disabled | trigger_exit | maker_post_only
take_profit_order_tif: GTC | GTD
take_profit_order_ttl_seconds: 0
take_profit_reprice_threshold: 0.01
take_profit_min_qty: 1
```

语义：

- `disabled`：不启用止盈。
- `trigger_exit`：保持当前逻辑，到止盈线后 `SETPOS=0`。
- `maker_post_only`：持仓建立后提前挂 post-only 止盈卖单。

默认建议初期仍为 `trigger_exit`，避免改变老策略行为。用户确认后再对单个策略启用 `maker_post_only`。

## 审计与 UI

Actions 面板应展示策略意图：

```text
PLACE_ORDER SELL Yes qty=100 @0.56 post_only reduce_only tag=take_profit [open]
CANCEL_ORDER tag=take_profit [canceled]
SETPOS Yes target=0 stop_loss [filled]
```

Trades 面板只展示实际成交：

```text
SELL Yes qty=100 @0.56 maker [filled]
SELL Yes qty=40 @0.38 taker stop_loss [filled]
```

订单状态建议新增 Open Orders 视图，至少包含：

- tag
- side/outcome
- price
- qty / filled / remaining
- status
- age
- post-only / reduce-only
- last error

### Ledger 与 Debug 当前落地

2026-05-23 已新增独立 `/ledger` 页面和 `/api/ledger` 聚合接口，用于展示：

- Virtual open orders。
- Real active orders。
- Virtual v1 / v2 positions。
- `strategy_real_positions`。
- `unassigned_positions`。
- `strategy_legs` 多资产归属。
- Debug 检查项。

Debug 当前只读，不做迁移或清理。`历史成交归因（只读）` 只显示缺少 `strategy_id/leg_uid` 的历史成交总数，不展开样本，避免误以为这些 legacy 成交属于当前可自动处理错误。

Dashboard 已新增 `平仓` 按钮。当前 `Virtual` 可执行第一版 force-flat；`Real` 在策略子账本和订单归因闭环前阻断。

## 实施阶段

### 阶段 1：协议和文档

- 明确 FunctionJson v2.1 订单动作协议。
- 明确策略与执行层职责边界。
- 明确 `leg_uid`、策略子账本、真实订单归因映射。
- 不改真实交易。

### 阶段 2：Leg 身份与账本迁移

- 为 `strategy_legs` 补齐不可变 `leg_uid`。
- 为 Virtual 表补齐 `leg_uid` 或建立兼容视图。
- 扩展真实订单表，保存 `leg_uid`、`client_order_tag`、`reduce_only` 等归因字段。
- 新增 `strategy_real_positions` 和 `unassigned_positions` 设计。
- 在真实盘未完成归因前，对共享 token 自动交易保持 blocked。

### 阶段 3：Virtual open order

- 新增 Virtual 挂单状态。
- 支持 `PLACE_ORDER` / `CANCEL_ORDER` / `REPLACE_ORDER`。
- 支持 stop-loss 前自动撤 reduce-only。
- 支持 open order 在 tick 中成交。
- 验证同 token 多策略和同策略多 leg 的 Virtual 分账。
- 已新增 `/ledger` Debug 诊断，用于检查虚拟挂单归属、待撤 reduce-only、同 token 多策略等风险。

### 阶段 3b：Dashboard Force Flat

- 已新增 Dashboard `平仓` 按钮和 `POST /api/registry/strategies/<id>/force-flat`。
- Virtual 第一版会生成退出动作：Polymarket binary 使用 `CLOSE_ALL`，多资产使用 `SET_TARGET target=0`。
- 执行后策略切到 `Stop`。
- Real force-flat 当前 blocked，等待 `strategy_real_positions` 和订单归因 reconcile 完成。

### 阶段 4：Follow Truth 预挂止盈

- 新增参数 `take_profit_order_mode`。
- 在 Virtual 模式先验证。
- 图表和事件中展示预挂止盈订单生命周期。

### 阶段 5：Real OrderManager

- 接入 Polymarket CLOB 下单/撤单/查单。
- 接入 User WebSocket 或轮询同步。
- 做真实盘小额灰度。
- 真实盘第一版只允许有完整 `client_order_id -> strategy_id + leg_uid` 映射的订单自动更新策略子账本。

### 阶段 6：Rebate 与报表

- 标记 maker/taker 成交。
- 统计 maker 成交额、fee-equivalent、可能的 rebate 估算。
- 不把未到账 rebate 计入确定 PnL。

## 验收用例

1. 旧策略只输出 `SETPOS`，行为不变。
2. Virtual 开仓后能预挂止盈单，订单状态为 `open`。
3. 市场上行到止盈价，Virtual 止盈单成交，fee=0，仓位归零。
4. 市场下行触发止损，后台先撤止盈单，再卖出剩余仓位。
5. 止盈单部分成交后触发止损，后台只卖剩余仓位。
6. 仓位增加后，止盈单数量和价格按新均价替换。
7. 仓位减少后，open reduce-only sell qty 不超过当前持仓。
8. post-only 订单若会 crossing，不提交或标记 blocked。
9. API 撤单状态 unknown 时，系统进入 sync_required，不盲目继续卖满仓。
10. Actions 显示订单意图，Trades 只显示实际成交。
11. 两个 Virtual 策略交易同一个 token，各自持仓、止盈单、止损退出互不影响。
12. 同一个策略两个 leg 指向同一个 token，只要 `leg_uid` 不同，也必须分开记账。
13. Real 模式中，同 token 多策略共享时，reduce-only 只能卖出本策略子仓数量，不能使用钱包总仓。
14. Real 成交回报找不到本地订单映射时，进入 `unassigned_positions`，不自动归入任何策略。
15. 如果策略子仓合计大于钱包真实仓位，暂停该 token 自动卖出并进入 reconcile 状态。

## 待确认问题

1. `follow truth` 默认是否继续 `trigger_exit`，只在用户开启后使用 `maker_post_only`？
2. Virtual 模拟成交初期是否允许“价格触达即全量成交”，还是第一版就按盘口数量部分成交？
3. `strategy_virtual_orders` 是直接迁移为订单生命周期表，还是新增 `strategy_virtual_open_orders` 降低对现有 Trades 的影响？
4. Real 模式第一版是否只允许 post-only reduce-only 止盈单，不开放普通 maker 做市单？
5. UI 是否需要单独 Open Orders 标签页，还是先放在 Actions 面板里展示？
6. 现有 `strategy_legs` 补齐 `leg_uid` 后，用户替换 leg 标的时是默认清理旧状态，还是提供手工迁移入口？
7. 历史真实订单缺少 leg 归因时，是否全部进入 `unassigned_positions`，由用户手工分配？
8. Real 模式发现共享 token 但缺少完整归因字段时，是全局禁止该 token 自动交易，还是只禁止卖出类动作？
