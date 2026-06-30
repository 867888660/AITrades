# Strategy Ledger 与强制平仓

日期：2026-05-23

本文记录交易账本页、Debug 诊断面板和 Dashboard 手动平仓入口的当前实现与边界。

## 页面入口

页面：

```text
GET /ledger
```

接口：

```text
GET /api/ledger?limit=100
POST /api/registry/strategies/<id>/force-flat
```

Dashboard 侧边栏新增 `Ledger` 入口。策略监控表每行新增 `平仓` 按钮，当前按钮调用 `force-flat` 接口。

## Ledger 数据范围

`/api/ledger` 聚合以下账本事实：

| 区域 | 数据源 | 说明 |
|---|---|---|
| 策略 | `strategy_registry` | 策略名称、代码、状态、资金 |
| Leg 归属 | `strategy_legs` | `leg_uid`、`leg_kind`、`asset_class`、`venue`、`symbol`、`instrument_id`、Polymarket token |
| 虚拟挂单 | `strategy_virtual_open_orders` | open / partially_filled 虚拟挂单 |
| 虚拟持仓 v1 | `strategy_virtual_positions` | 旧 Polymarket 二元市场持仓，按 `strategy_id + leg_index + side` |
| 虚拟持仓 v2 | `strategy_virtual_positions_v2` | 多资产持仓，按 `strategy_id + instrument_id + side` |
| 虚拟成交 | `strategy_virtual_orders` / `strategy_virtual_orders_v2` | 最近成交与尝试记录 |
| 实盘活跃订单 | `orders` | 只统计 `created/submitted/open/partially_filled/cancel_requested` |
| 实盘子仓 | `strategy_real_positions` | 系统可归因的真实策略子仓 |
| 未归属仓位 | `unassigned_positions` | 不能自动归入任何策略的仓位 |

## 多资产展示口径

Ledger 的 `当前仓位` 使用统一多资产列：

```text
Mode / Strategy / Asset / Venue / Symbol / Instrument / Side /
Qty / Avg / Cost / Market Value / Unrealized / Realized / Source / Updated
```

兼容规则：

- Polymarket v1 虚拟持仓从 `strategy_legs` 反查 `asset_class/venue/instrument_id/condition_id/token`。
- v2 多资产持仓直接使用 `strategy_virtual_positions_v2.instrument_id`。
- 实盘子仓使用 `strategy_real_positions.token_id/outcome`。
- 未归属仓位使用 `unassigned_positions.token_id/outcome`。

`Leg 归属` 不再只展示 Polymarket 字段，而是使用：

```text
Strategy / Leg / Leg UID / Asset / Venue / Symbol / Instrument / Polymarket Tokens / Budget
```

`leg_index` 是内部 0-based index；UI 中展示为 `L1/L2/...`。

## Debug 面板

Debug 面板用于快速定位账本断点，不做任何清理或迁移。当前检查项：

| 检查 | 含义 |
|---|---|
| Leg UID 完整性 | `strategy_legs.leg_uid` 是否缺失 |
| 虚拟挂单归属 | 虚拟 open order 是否能映射到当前策略 leg |
| 待撤 reduce-only 卖单 | 强平前必须先撤的 reduce-only SELL |
| 实盘子仓归属 | `strategy_real_positions` 是否能映射到 `strategy_id + leg_uid` |
| 同 token 多策略 | 多策略是否复用同一个 Polymarket token |
| 未归属实盘仓位 | 是否存在不能自动使用的 `unassigned_positions` |
| 实盘订单映射 | 活跃实盘订单是否缺少 `strategy_id/leg_uid` |
| 历史成交归因（只读） | 历史成交是否缺少 `strategy_id/leg_uid` |

`历史成交归因（只读）` 只显示总数，不展开原始样本，避免把历史 legacy 数据误认为当前错误。它不会删除数据；后续如果要处理，应进入未归属仓位或人工分配流程。

## 强制平仓当前实现

入口：

```text
POST /api/registry/strategies/<id>/force-flat
```

Dashboard 每行 `平仓` 按钮调用该接口。

### Virtual

Virtual 策略当前可执行第一版强平：

- Polymarket binary leg：生成 `CLOSE_ALL`。
- 非 Polymarket 多资产 leg：生成 `SET_TARGET target=0`。
- 执行层会按现有虚拟成交规则卖出仓位。
- 执行完成后策略状态切换为 `Stop`。
- 如果有 reduce-only / take-profit open orders，执行层会先按现有退出保护撤相关挂单。

### Real

Real 策略当前强平被阻断：

```text
Real force-flat is blocked until strategy_real_positions and order attribution are reconciled
```

原因：

- 真实钱包仓位是全局的，不能按 token 总仓直接卖。
- 自动强平只能使用 `strategy_real_positions` 中可归因的策略子仓。
- 历史缺少订单映射或外部手动交易产生的仓位必须进入 `unassigned_positions` 或人工分配流程。

## 删除与平仓的关系

当前已实现独立 `平仓` 按钮。删除按钮尚未自动串联强平流程。

目标语义：

```text
手动平仓 = 保留策略配置，只退出仓位和挂单
删除策略 = 先执行同一套 exit workflow，确认无归属挂单/仓位后再删除策略配置
```

后续实现删除前强平时，不应在删除路由里复制交易逻辑，而应复用 `force-flat / exit workflow`。

## 当前限制

- v1 虚拟 Polymarket 持仓仍主要按 `strategy_id + leg_index + side` 记账；`leg_uid` 在 open order 与新设计中已存在，但历史 v1 position 还需兼容映射。
- Real 自动强平未开放。
- 历史实盘订单缺少归因时只做 Debug 提示，不自动迁移。
- 删除策略尚未强制要求先清仓。
