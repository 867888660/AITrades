# 多资产策略架构升级说明

本文记录 2026-05-19 之后策略系统从 Polymarket 专用策略，向多资产策略引擎演进的落地约定。目标不是一次性推倒重写，而是在保留旧策略兼容性的前提下，引入更专业的量化系统边界。

## 核心边界

策略运行时的数据分为三类：

| 类型 | 存储位置 | 含义 |
|---|---|---|
| Params | `strategy_registry.input_json` / `strategy_legs.params_json` | 用户配置参数，低频修改 |
| UseData | `virtual_context_builder.build_use_data()` 每轮生成 | 本轮运行看到的市场、组合、时间与参数快照，只读 |
| State | `strategy_state` | 策略自己的持久状态，高频读写 |

`UseData` 不再承担持久化职责。策略如果需要保存 `last_signal`、`cooldown_until`、`entry_reason`、`regime` 等内部状态，应通过 `FunctionJson.state_updates` 输出，由 runner 写入 `strategy_state`。

## 新增策略状态表

```sql
CREATE TABLE strategy_state (
    strategy_id INTEGER NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    key TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT 'null',
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(strategy_id, namespace, key)
);
```

策略输出示例：

```json
{
  "schema_version": "1.1",
  "actions": [],
  "metrics": {},
  "print": ["hold"],
  "state_updates": {
    "last_signal": "hold",
    "cooldown_until": null
  }
}
```

下一轮运行时，`UseData["State"]` 会包含已保存状态。

## 通用标的字段

`strategy_legs` 继续作为策略绑定标的的表，但新增通用字段：

| 字段 | 含义 |
|---|---|
| `asset_class` | `polymarket_binary` / `crypto_spot` / `equity` / `equity_option` 等 |
| `venue` | 交易或行情来源，例如 `polymarket`、`binance`、`finnhub`、`opra` |
| `symbol` | 标准代码，例如 `BTCUSDT`、`NVDA` |
| `instrument_id` | 稳定标的 ID，例如 `crypto:binance:BTCUSDT` |
| `instrument_json` | 资产类别专用 metadata |

旧 Polymarket 字段 `condition_id`、`yes_token`、`no_token` 继续保留。旧策略仍然可以读 `L0_Yes_AskPrice` 等 legacy 字段。

## UseData v2

新的 `UseData` 同时包含结构化字段和 legacy 扁平字段：

```json
{
  "SchemaVersion": "2.0",
  "LegacySchemaVersion": "1.0",
  "Params": {},
  "State": {},
  "Portfolio": {
    "cash": 1000,
    "equity": 1000,
    "realized_pnl": 0,
    "unrealized_pnl": 0,
    "total_fees_paid": 0
  },
  "Instruments": [
    {
      "index": 0,
      "instrument_id": "poly:condition:0x...",
      "asset_class": "polymarket_binary",
      "quote": {
        "yes_bid": 0.41,
        "yes_ask": 0.43,
        "no_bid": 0.56,
        "no_ask": 0.58
      },
      "position": {
        "yes_qty": 10,
        "no_qty": 0
      }
    }
  ],
  "L0_Yes_AskPrice": 0.43
}
```

新策略推荐优先读取：

```python
instrument = usedata["Instruments"][0]
state = usedata.get("State", {})
params = usedata.get("Params", {})
```

旧策略可继续读取：

```python
usedata["L0_Yes_AskPrice"]
usedata["Yes_now_ask"]
```

## 通用动作协议

旧动作继续支持：

```json
{"type": "SETPOS", "leg": 0, "side": "Yes", "target_pct": 0.6}
```

新增动作：

```json
{"type": "SET_BINARY_TARGET", "instrument": 0, "outcome": "Yes", "target_pct": 0.6}
```

用于非 Polymarket 标的：

```json
{"type": "ORDER", "instrument": 1, "side": "BUY", "qty": 0.1, "price": "market"}
```

```json
{"type": "SET_TARGET", "instrument": 1, "target": 0.3, "target_type": "notional_pct"}
```

`SET_TARGET` 当前按 `budget_cap * target` 计算目标名义金额。非 Polymarket 虚拟成交会写入 v2 虚拟账本。

## v2 虚拟账本

新增三张表：

| 表 | 用途 |
|---|---|
| `strategy_virtual_positions_v2` | 按 `strategy_id + instrument_id + side` 记录多资产持仓 |
| `strategy_virtual_orders_v2` | 记录多资产虚拟订单 |
| `strategy_cash_ledger` | 预留现金流水账本 |

旧 Polymarket 虚拟盘仍写入 `strategy_virtual_positions` / `strategy_virtual_orders`，以保持工作台和图表兼容。多资产动作先写 v2 表。

## Ledger 多资产展示

交易账本页 `/ledger` 已使用多资产统一展示口径，避免把仓位页绑定在 Polymarket Yes/No 字段上。

`当前仓位` 展示列：

```text
Mode / Strategy / Asset / Venue / Symbol / Instrument / Side /
Qty / Avg / Cost / Market Value / Unrealized / Realized / Source / Updated
```

兼容规则：

- `strategy_virtual_positions`：旧 Polymarket v1 仓位，从 `strategy_legs` 补齐 `asset_class/venue/instrument_id/condition_id/token`。
- `strategy_virtual_positions_v2`：多资产虚拟仓位，直接使用 `instrument_id + asset_class + side`。
- `strategy_real_positions`：真实盘可归因策略子仓。
- `unassigned_positions`：未归属仓位，只展示，不自动归入任何策略。

`Leg 归属` 展示列：

```text
Strategy / Leg / Leg UID / Asset / Venue / Symbol / Instrument / Polymarket Tokens / Budget
```

其中 `Polymarket Tokens` 仅对 `polymarket_binary` 有意义；crypto / equity / option 等标的主要依赖 `venue + symbol + instrument_id`。

## 当前落地范围

已经落地：

- `strategy_state` 表和读写服务。
- `strategy_legs` 通用标的字段。
- `UseData` v2 结构：`Params`、`State`、`Portfolio`、`Instruments`。
- `FunctionJson.state_updates` 写回。
- `SET_BINARY_TARGET`、`SET_TARGET`、`ORDER` 动作解析。
- 非 Polymarket 虚拟订单与持仓写入 v2 表。
- `/api/virtual/strategies/<id>/positions` 与 `/orders` 返回 `data_v2`。
- `/ledger` 与 `/api/ledger` 已按多资产口径聚合 Virtual / Real / Unassigned 账本。
- Dashboard `平仓` 按钮当前可对 Virtual 策略执行多资产 force-flat：Polymarket 用 `CLOSE_ALL`，非 Polymarket 用 `SET_TARGET target=0`。

仍需后续完成：

- Dashboard / Workspace 的多资产标的编辑 UI。
- 股票期权真实行情 adapter 与 greeks 字段。
- 实盘执行 adapter 的多资产路由。
- 回测引擎统一读取 v2 ledger。
- 工作台图表对非 Polymarket 标的的专用展示。
- Real force-flat 的订单归因、实盘子账本 reconcile 和删除前强平串联。
