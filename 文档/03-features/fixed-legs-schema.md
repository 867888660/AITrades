# 固定 LegsSchema 设计

本文约定 Dashboard / Workspace 中的策略腿 UI。结论：`leg` 是策略代码声明的固定标的槽位，和 `Inputs` 一样由策略文件定义，不是用户在 UI 中任意追加的 `condition_id` 列表。

## 语义

每条 `leg` 对应一个 strategy-bound instrument slot。策略运行时可以稳定读取：

```python
usedata["LegCount"]
usedata["L0_ConditionId"]
usedata["L1_AssetClass"]
usedata["Instruments"][0]
usedata["Instruments"][1]
```

因此 leg 数量和 index 语义必须稳定。改变 leg 数量属于修改策略代码 schema，而不是运行时配置。

## 策略声明

策略文件可以声明 `LegsSchema`：

```python
LegsSchema = [
    {
        "name": "target_market",
        "label": "Leg 1",
        "purpose": "Primary Polymarket market traded by the strategy.",
        "asset_class": "polymarket_binary",
        "venue": "polymarket",
        "required": True,
    },
    {
        "name": "hedge_crypto",
        "label": "Leg 2",
        "purpose": "External crypto signal or hedge instrument.",
        "asset_class": "crypto_spot",
        "venue": "binance",
        "symbol": "BTCUSDT",
        "required": False,
    },
    {
        "name": "reference_equity",
        "label": "Leg 3",
        "purpose": "Reference equity used by the strategy.",
        "asset_class": "equity",
        "venue": "US",
        "symbol": "NVDA",
        "required": False,
    },
]
```

可选别名：`InstrumentsSchema`、`InstrumentSchema`。如果策略没有声明，系统默认生成 1 条：

```python
{
    "label": "Leg 1",
    "asset_class": "polymarket_binary",
    "venue": "polymarket",
    "required": True,
}
```

## UI 规则

- Dashboard 选择策略代码后读取 `LegsSchema`。
- UI 渲染固定数量的 legs，不提供自由 `+ Leg`。
- `polymarket_binary` 显示 `condition_id` 和自选按钮。
- `crypto_spot` / `equity` 显示 `venue + symbol`。
- 每条 leg 可填写 `budget_cap`，未来可按 `params_schema` 扩展腿级参数。
- 保存时写入 `strategy_legs`；`UseData["LegCount"]` 等于 schema 定义数量。
- 修改 leg 身份字段会清理该策略虚拟盘状态，避免旧持仓错配到新标的。

## 当前落地

- `strategy_schema_service.get_strategy_code_schemas()` 返回 `legs` schema。
- Dashboard 新增 / 编辑策略弹窗根据 `legs` schema 渲染固定腿。
- 旧策略保持单腿 Polymarket 默认行为。
