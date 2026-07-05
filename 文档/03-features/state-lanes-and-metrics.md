# State Lanes 与 Strategy Metrics 定义

本文定义策略状态机、图表状态带与策略指标的边界。它们名字相近，但职责不同。

## 核心概念

| 名称 | 数据来源 | 存储位置 | 用途 | 是否驱动执行 |
|---|---|---|---|---|
| Mode | 用户选择 | `strategy_registry.mode` | 决定策略是否调度和执行 | 是 |
| Machine State / StrategyState | 用户或策略写入 | `strategy_state(namespace='machine', key='state')` | 表示策略状态机当前状态 | 策略可读取并影响动作 |
| Controls / UserState | 用户写入 | `strategy_state(namespace='user')` | 人工控制开关，如暂停开仓、强制清仓、风险缩放 | 策略可读取并影响动作 |
| RuntimeState | 策略写入 | `strategy_state(namespace='runtime')` | 策略运行记忆，如入场价、冷却时间、峰值价格 | 策略可读取并影响动作 |
| Strategy Metrics | 策略输出 | `strategy_metric_events` | 数值型因子、风险、信号指标的时间序列 | 否，仅用于观察和分析 |
| State Lanes | 策略输出的状态型 metrics | `strategy_metric_events` | 离散状态随时间变化的色带 | 否，仅用于观察和分析 |

`Mode` 只有 `Stop / Virtual / Real`。`Machine State` 来自策略代码的 `StateMachineSchema`，例如 `auto / idle / holding / cooldown / manual_review / stop_loss_locked`。二者必须分开理解：`Mode=Virtual` 表示虚拟盘在运行，`Machine State=stop_loss_locked` 表示策略状态机锁定，运行中也禁止自动重新加仓。

## Strategy Metrics

Strategy Metrics 是策略每次运行输出的 `FunctionJson.metrics`。执行器把它们写入 `strategy_metric_events`。

数值型指标进入 `Strategy Metrics` 面板，常见例子：

```json
{
  "metrics": {
    "fair_price": 0.17,
    "entry_edge": 0.025,
    "pnl_pct": -0.04,
    "drawdown": -0.08,
    "macd_hist": -0.0012,
    "target_position": 0.0
  }
}
```

前端用 `metric:<key>` 标识数值指标，例如 `metric:pnl_pct`。它们应该回答“因子是多少、风险是多少、目标仓位是多少”。

不应该放入 Strategy Metrics 的内容：

- 长文本解释，应放入 `print`
- 时间戳字段，如 `now`、`*_at`、`*_until`
- 账户事实和行情事实，如当前持仓、当前 bid/ask，系统已有专门数据源

## State Lanes

State Lanes 是状态型 metrics 的可视化，不等于 Machine State 本身。它用色带显示状态随时间的变化，适合表达：

```json
{
  "metrics": {
    "decision": "HOLD",
    "signal": "stop_loss_locked",
    "regime": "downtrend",
    "cooldown_active": true,
    "stop_loss_locked": true
  }
}
```

文本型或布尔型 metrics 会被识别为状态型指标，并以前端 `metric_state:<key>` 标识。示例：

- `metric_state:decision`
- `metric_state:signal`
- `metric_state:regime`
- `metric_state:cooldown_active`
- `metric_state:stop_loss_locked`

State Lanes 只负责回放和解释，不直接改变策略状态。真正改变 Machine State 的是 `machine_state_updates` 或用户在 UI/API 中修改 `strategy_state(namespace='machine')`。

## Backtest 派生指标

如果回测 executor 没有写入策略内部 metrics，工作台只能显示最基本的回测派生指标。它们必须放在 `Backtest Metrics` / `Backtest State`，不能放在 `Strategy Metrics` / `State Lanes`：

- `backtest_return`: 当前权益 / 初始权益 - 1
- `backtest_drawdown`: 当前权益 / 历史峰值权益 - 1
- `backtest_position_state`: 从回测仓位推导出的 `Flat / Long / Short`

这些是兜底指标，不等于策略代码里的完整因子，也不代表策略代码内部的状态机。要获得完整分析，executor 必须在回测每个 bar 或每次决策时保存 `FunctionJson.metrics` 和状态型 metrics。

## 回测 executor 的硬性要求

正常策略工作台的 `Strategy Metrics` / `State Lanes` 来自策略代码输出的 `FunctionJson.metrics`，实时运行时写入 `strategy_metric_events`。回测工作台不能只保存 equity、orders、events，否则副图组选择器只能显示 `Strategy PnL` 或 `Backtest Metrics` / `Backtest State` 兜底项。

回测 executor 每次调用策略代码后，必须把本次 `FunctionJson.metrics` 和可选的 `FunctionJson.metrics_meta` 保存进回测记录：

```json
{
  "meta": {
    "strategy_metrics": {
      "trend_ratio": 0.012,
      "target_position": 1.0,
      "reason": "trend_entry"
    },
    "strategy_metrics_meta": {
      "trend_ratio": {"kind": "continuous", "label": "Trend Ratio", "unit": "ratio"},
      "target_position": {"kind": "continuous", "label": "Target Position", "unit": "ratio"},
      "reason": {"kind": "state", "label": "Decision Reason"}
    }
  }
}
```

前端初始化时必须把 `source=backtest` 与 `run_id` 传给 workspace API。后端再用该 run 的 `strategy_metrics` 构造策略内部 `metric_catalog`。只有来自 `strategy_metrics` 的数值与状态字段才可以进入 `Strategy Metrics` / `State Lanes`；收益率、回撤、仓位状态这类系统推导值只能进入 `Backtest Metrics` / `Backtest State`。

## stop_loss_locked 状态切换

`stop_loss_locked` 是人工确认型保护状态。当前 `Stragy_Fllow_Truth` 的规则如下：

1. 策略读取 `MachineState` 或 `StrategyState.state`。
2. 如果当前状态是 `stop_loss_locked`，策略禁止开仓和加仓，只允许保持或降低风险。
3. 当策略触发 stop loss 且目标仓位降到 `0`，并且本次确实产生调仓动作时，策略输出：

```json
{
  "machine_state_updates": {
    "state": "stop_loss_locked"
  }
}
```

4. `virtual_runner` 写入 machine state。
5. `virtual_execution` 再做一层保护：只要检测到 `stop_loss_locked`，所有 BUY、增加仓位、开仓相关 action 都会被记录为 `blocked`，reason 为 `stop_loss_locked`。
6. 系统不会自动解除该状态。用户必须在 Dashboard 或 Workspace 的 State 下拉框手动切回 `auto`、`idle` 等状态，或通过 state-store API 修改 machine state。

这意味着 `stop_loss_locked` 的语义是：止损已经发生，策略进入人工复核锁定期；复核前不能自动重新开仓。

## 建议策略输出

为了让工作台足够可解释，策略代码至少应输出：

```json
{
  "metrics": {
    "decision": "HOLD",
    "signal": "wait_edge",
    "regime": "flat",
    "fair_price": 0.17,
    "entry_edge": 0.025,
    "target_position": 0.0,
    "pnl_pct": null,
    "drawdown": null,
    "cooldown_active": false,
    "stop_loss_locked": false
  },
  "metrics_meta": {
    "decision": {"kind": "state", "label": "Decision"},
    "signal": {"kind": "state", "label": "Signal"},
    "regime": {"kind": "state", "label": "Regime"},
    "fair_price": {"kind": "continuous", "label": "Fair Price", "unit": "price"},
    "entry_edge": {"kind": "continuous", "label": "Entry Edge", "unit": "price"},
    "target_position": {"kind": "continuous", "label": "Target Position", "unit": "ratio"},
    "cooldown_active": {"kind": "state", "label": "Cooldown"},
    "stop_loss_locked": {"kind": "state", "label": "Stop Loss Locked"}
  }
}
```

这样工作台才能同时显示因子曲线、状态色带和关键风险状态。
