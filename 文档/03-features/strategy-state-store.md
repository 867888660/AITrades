# Strategy State Store

本文记录策略状态分层与默认值机制。核心原则是：**默认值写在策略代码里，数据库只保存 override**。

## 分层

| 层级 | 存储位置 | 默认值来源 | 谁写入 | 用途 |
|---|---|---|---|---|
| `Params` | `strategy_registry.input_json` / `strategy_legs.params_json` | `ParamsSchema` / 旧 `Inputs` | 用户 | 长期参数，例如阈值、风险预算、窗口长度 |
| `Controls` | `strategy_state.namespace = user` | `ControlsSchema` | 用户 / UI | 人工控制开关，例如暂停开仓、强制清仓、风险缩放 |
| `RuntimeState` | `strategy_state.namespace = runtime` | `RuntimeStateSchema` | 策略代码 | 策略运行记忆，例如 `last_signal`、`entry_price`、`cooldown_until` |
| `SystemState` | `strategy_state.namespace = system` | 系统 | 系统 | 调度器、风控、执行层预留 |
| `Portfolio` / `Instruments` | 系统事实表或实时行情 | 系统 | 系统 | 账户、仓位、行情事实，策略只读 |

兼容规则：

- `UseData["Controls"]` 是推荐新名字。
- `UseData["UserState"]` 暂时等于 `Controls`，兼容上一版策略。
- `UseData["State"]` 暂时等于 `RuntimeState`，兼容旧策略。
- 旧版 `default` namespace 会迁移到 `runtime`。

## 策略代码声明

每个策略文件可以声明三类 schema：

```python
ParamsSchema = {
    "risk": {"type": "number", "default": 0.5, "min": 0, "max": 1}
}

ControlsSchema = {
    "manual_pause_open": {"type": "bool", "default": False},
    "force_flat": {"type": "bool", "default": False},
    "risk_scale": {"type": "number", "default": 1.0, "min": 0, "max": 1}
}

RuntimeStateSchema = {
    "last_signal": {"type": "string", "default": "none"},
    "last_action_at": {"type": "string", "default": None}
}
```

系统运行时按以下规则合并：

```text
effective Params       = ParamsSchema defaults + strategy input_json
effective Controls     = ControlsSchema defaults + strategy_state(user)
effective RuntimeState = RuntimeStateSchema defaults + strategy_state(runtime)
```

数据库只保存用户或策略实际写过的 override。用户点击清空 override 后，会自然回到策略代码默认值。

## UseData

新策略建议这样读取：

```python
params = usedata.get("Params", {})
controls = usedata.get("Controls", usedata.get("UserState", {}))
runtime = usedata.get("RuntimeState", {})

if controls.get("force_flat"):
    return {
        "actions": [{"type": "SET_TARGET", "instrument": 0, "target": 0}],
        "state_updates": {"last_signal": "manual_flat"},
    }
```

策略输出的 `state_updates` 默认写入 `RuntimeState`：

```json
{
  "actions": [],
  "state_updates": {
    "last_signal": "hold",
    "cooldown_until": null
  }
}
```

## API

```text
GET    /api/registry/strategies/<id>/state-store
PATCH  /api/registry/strategies/<id>/state-store/controls
PATCH  /api/registry/strategies/<id>/state-store/user
PATCH  /api/registry/strategies/<id>/state-store/runtime
DELETE /api/registry/strategies/<id>/state-store/controls
DELETE /api/registry/strategies/<id>/state-store/user
DELETE /api/registry/strategies/<id>/state-store/runtime
```

`controls` 和 `user` 指向同一个 namespace。`PATCH` 请求体：

```json
{
  "values": {
    "risk_scale": 0.5
  },
  "replace": true,
  "reason": "manual risk reduction"
}
```

`GET /state-store` 会同时返回：

```json
{
  "user_defaults": {},
  "user_overrides": {},
  "controls": {},
  "runtime_defaults": {},
  "runtime_overrides": {},
  "runtime": {}
}
```

其中 `controls` / `runtime` 是合并后的有效值。

## Dashboard UI

Dashboard 策略表的 `State` 按钮打开状态弹窗：

- 左侧编辑 `Controls` override。
- 右侧编辑 `RuntimeState` override，只有策略 Stop 时可编辑。
- 每侧都会显示 effective value，即默认值 + override。
- 保存时只保存 override，不把默认值写入数据库。
- 清空 override 后回到策略代码默认值。

## 当前策略默认值

两个现有策略已经声明：

- `ControlsSchema`: `manual_pause_open=false`、`force_flat=false`、`risk_scale=1.0`、`debug_raw_inputs=false`
- `RuntimeStateSchema`: `last_signal`、`last_action_at` 等基础记忆字段
- `ParamsSchema`: 与原有 `Inputs` 对齐的参数默认值

两个策略也开始读取基础 Controls：

- `debug_raw_inputs=true` 时才打印完整原始 UseData。
- `manual_pause_open=true` 时，空仓状态不再开新仓。
- `force_flat=true` 时，目标仓位归零。
- `risk_scale` 会缩放目标仓位。
