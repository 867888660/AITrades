# Virtual Bankroll 与虚拟账户现金同步修复

日期：2026-05-15

## 背景

策略工作台的 `Strategy Bankroll` 表示策略分配给虚拟执行层使用的资金。`SETPOS` 动作会按以下公式换算目标金额：

```text
target_cost = L{leg}_BudgetCap * target_pct
```

当 leg 的 `budget_cap` 为 0 时，`UseData` 构建层会回退到虚拟账户当前权益；只有账户尚未创建时，才使用 `strategy_bankroll` 初始化 sizing。这样亏损后再次 `SETPOS` 不会继续按初始本金买入。

补充修正：`SETPOS` 的减仓数量必须从当前实际持仓推导，而不是用卖出价反推。

```text
current_cost = PositionQty * PositionAvgPrice
requested_buy_qty = (target_cost - current_cost) / ask_price
max_buy_qty       = cash / (ask_price * (1 + fee_rate * (1 - ask_price)))
buy_qty           = min(requested_buy_qty, max_buy_qty)
sell_qty = PositionQty - target_cost / PositionAvgPrice
```

`target_pct` 和 `Yes_Now_Pos` 表示“持仓成本占预算比例”。图表 `yes_position` / `no_position` 表示当前资金占用比例：

```text
allocation_base = cash + yes_qty * yes_avg_price + no_qty * no_avg_price
yes_position = yes_qty * yes_avg_price / allocation_base
no_position  = no_qty  * no_avg_price  / allocation_base
```

补充资金约束：`SETPOS` 的目标成本来自 `BudgetCap`，但成交前必须受虚拟账户当前 `cash` 约束；现金不足时只能买到最大可支付数量，低于最小成交量则跳过，不能透支默认成交。

补充并发约束：虚拟盘 runner 必须对同一 `strategy_id` 使用数据库锁。同一策略同一时间只能有一个 tick 执行，避免多个 app/runner 进程同时读取旧持仓并重复成交。

## 问题现象

Row 39 策略在工作台中已经把 `Strategy Bankroll` 保存为 `100`，但 Actions 面板仍然出现：

```text
SETPOS Yes target=15% qty=0.00 @0.015 [skipped] insufficient_cash_for_setpos
```

Trades 面板没有成交。数据库检查显示：

```text
strategy_registry.strategy_bankroll = 100
strategy_legs.budget_cap = 0
strategy_virtual_account.initial_cash = 0
strategy_virtual_account.cash = 0
```

也就是说，策略配置已经变为 100，但虚拟账户仍停留在第一次以 0 初始化出来的现金状态。

## 根因

`strategy_bankroll` 保存路径只更新了 `strategy_registry`，没有同步已有的 `strategy_virtual_account`。

执行层 `_upsert_account()` 的语义是“没有账户行时才初始化”：

```text
如果 strategy_virtual_account 不存在：
    initial_cash = strategy_bankroll
    cash = strategy_bankroll
如果已经存在：
    不修改 initial_cash / cash
```

因此，一旦某个策略第一次以 `strategy_bankroll=0` 跑过，后续即使用户在 UI 中把 Bankroll 改成 100，虚拟账户的 `cash` 仍然是 0，执行层会继续判定没有可用现金。

## 修复内容

新增非破坏式同步函数：

- `services/virtual_execution.py`
  - 新增 `sync_virtual_account_bankroll(strategy_id, initial_cash)`
  - 若虚拟账户不存在：创建账户，`initial_cash = cash = bankroll`
  - 若虚拟账户已存在：按 `new_initial_cash - old_initial_cash` 的差额调整 `cash`
  - 保留已有 positions、orders、realized_pnl、fees，不做 reset

接入两个保存入口：

- `services/polymarket_service.py`
  - 工作台 `/api/polymarket/strategies/<row_id>` 保存 `strategy_bankroll` 后同步虚拟账户。
- `services/strategy_registry_service.py`
  - Registry 更新策略时，如果 payload 包含 `strategy_bankroll`，也同步虚拟账户。

## 同步语义

同步采用差额调整，而不是直接覆盖现金：

```text
cash_delta = new_initial_cash - old_initial_cash
cash = cash + cash_delta
initial_cash = new_initial_cash
```

这样可以保留已经发生的交易影响。例如：

```text
old_initial_cash = 100
cash = 72
new_initial_cash = 150
cash_delta = 50
new_cash = 122
```

如果直接把 `cash` 覆盖成 150，会抹掉已有成交、手续费和盈亏对现金的影响；所以这里不能这么做。

## 验证结果

修复后对 Row 39 手动执行同步：

```text
strategy_bankroll 100.0
sync result: {status: synced, initial_cash: 100.0, cash_delta: 100.0}
```

随后虚拟调度产生真实成交：

```text
strategy_virtual_orders:
id=72 BUY  YES qty=1000.00 price=0.015 status=filled
id=73 SELL YES qty=1000.00 price=0.014 status=filled
```

Actions 中也出现 filled：

```text
BUY  YES qty=1000.00 @0.015 filled
SELL YES qty=1000.00 @0.014 filled
```

这说明原先“有 Action 没有 Trades”的原因已经从资金同步问题中解除。后续是否买入或卖出，由策略目标仓位变化决定。

## 注意事项

- 修改 `strategy_bankroll` 不再需要用户手动 reset 虚拟账户。
- 修改 leg 的 `budget_cap` 或市场身份字段仍遵循原有规则。
- 如果需要彻底清空历史持仓、订单和 tick，仍然使用 `/api/virtual/strategies/<id>/reset`。
- Trades 仍然只展示 `strategy_virtual_orders.status='filled'` 的订单；被跳过或失败的执行结果仍然属于 Actions 审计。

## 2026-05-22 补充：Leg BudgetCap 派生虚拟资金

### 新问题现象

Row 45 出现了另一种“有 Action 没有 Trades”的资金错配：

```text
strategy_registry.strategy_bankroll = 0
strategy_legs.budget_cap = 100
strategy_virtual_account.initial_cash = 0
strategy_virtual_account.cash = 0
```

策略按 `L0_BudgetCap = 100` 计算目标仓位：

```text
SETPOS No target=15% @0.2900
```

但执行层只用 `strategy_bankroll` 初始化虚拟账户，导致账户现金仍为 0，最终写入：

```text
[skipped] insufficient_cash_for_setpos
```

这不是合理的风控跳过，而是配置资金与虚拟账户资金没有对齐。

### 根因

新建或保存策略时，UI 允许把资金填在 leg 的 `budget_cap` 中；但 `strategy_registry.strategy_bankroll` 仍可能保持 0。

执行层原先只把 `strategy_bankroll` 传给 `_upsert_account()`，没有在 bankroll 为 0 时回退到 leg 的 `ConfiguredBudgetCap`。于是产生了矛盾：

```text
Sizing 使用 leg budget_cap = 100
Cash 约束使用 virtual_account.cash = 0
```

### 修复内容

- `services/virtual_execution.py`
  - 新增 `_derive_initial_cash(strategy_bankroll, use_data)`。
  - 优先使用显式 `strategy_bankroll`。
  - 如果 `strategy_bankroll <= 0`，回退到所有 `L{n}_ConfiguredBudgetCap` 的合计。
  - `_upsert_account()` 对已有账户也做差额同步，避免已存在的 0 cash 账户永久卡住。
- `services/strategy_registry_service.py`
  - 创建策略时，如果没有显式 `strategy_bankroll`，从 leg `budget_cap` 派生。
  - 更新策略且 payload 包含 legs 时，如果 bankroll 为空或 0，也从 leg `budget_cap` 派生并同步虚拟账户。
- `app.py`
  - `/api/virtual/strategies/<id>/reset` 在 `strategy_bankroll <= 0` 时回退到 legs 的 `budget_cap` 合计。

### 当前验证

对 Row 45 执行非破坏式同步后：

```text
strategy_registry.strategy_bankroll = 100
strategy_virtual_account.initial_cash = 100
strategy_virtual_account.cash = 100
strategy_legs.budget_cap = 100
```

后续 tick 会用 100 的虚拟现金约束 `SETPOS`，不再因为账户初始化为 0 而跳过。
