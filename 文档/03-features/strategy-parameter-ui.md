# 策略参数 UI 与 UseData 自动填入

本文记录 Dashboard 与 Strategy Workspace 中策略参数表单的行为约定。

## 目标

策略参数可能很多，用户容易忘记每个参数的意义，也容易把运行时字段手动填错。当前设计把参数说明和可自动填入的运行数据统一到前后端协议中：

- 参数含义来自策略代码的 `FunctionIntroduction`。
- 默认值来自策略代码的 `Inputs` / `FunctionIntroduction`。
- 当前运行值来自 `UseData`。
- Dashboard 新增策略时可以用 draft UseData 预览可用字段。

## 参数说明

后端接口：

```text
GET /api/strategy-codes/<code_name>/inputs
```

该接口读取策略文件中的 `Inputs`，并解析 `FunctionIntroduction` 中的 YAML 风格 `inputs` 区块。前端使用返回的 `description` 在参数名旁展示 `?` 提示。

推荐策略代码在 `FunctionIntroduction` 中写清楚：

```yaml
inputs:
  - name: start_day
    type: number
    required: true
    description: 起始剩余天数，用于 time_ratio=day_to_end/start_day
```

如果策略代码同时声明了 `Default`、`Num` 或 `Context`，Dashboard 和 Workspace 会把它作为默认参数同步到输入框。

## UseData 自动填入按钮

前端只在 `UseData` 中找到非空匹配值时才显示刷新按钮。空字符串、`null`、`undefined` 不算有效值。

按钮匹配逻辑：

1. 先按参数原名匹配，例如 `start_day`。
2. 再按别名匹配，例如 `StartDay`、`day_to_end`、`L0_DayToEnd`。
3. 最后按规范化名称匹配，例如去掉大小写和下划线后比较。

当前常用别名：

```text
start_day  -> start_day, StartDay, day_to_end, L0_DayToEnd, DayToEnd
Enddate    -> Enddate, L0_EndTime, end_date, EndTime
BudgetCap  -> BudgetCap, L0_BudgetCap
```

有 UseData 时，前端会自动填入空字段；用户已经手动填写的字段不会被自动覆盖。用户仍可点击刷新按钮手动覆盖当前字段。

### input_json 回声不算可刷新值

`UseData` 的 Tier 5 会把 `strategy_registry.input_json` 中保存过的用户参数也注入进去，作为策略运行时的兼容字段。例如已保存策略可能同时在 UseData 中看到：

```text
FactSide
risk
hedge_sell_bias
trend_sell_bias
core_sell_bias
start_day
```

这些字段里有一部分只是用户已经保存过的参数，不是运行时事实数据。如果前端按参数原名直接匹配，就会导致所有参数都显示刷新按钮，看起来像是 UseData 都能提供当前值。

因此，Workspace 设置页的按钮显示规则是：

- 不把参数原名本身当作可刷新 UseData。
- 不使用规范化名称回退匹配参数原名。
- 只使用明确的运行时别名 / 派生字段匹配。

例如：

```text
FactSide      -> 不显示按钮，除非后续定义了明确运行时别名
risk          -> 不显示按钮，除非后续定义了明确运行时别名
start_day     -> 可以显示按钮，因为它能匹配 StartDay / day_to_end / L0_DayToEnd
```

这个规则避免把 `input_json` 中的旧参数值误判为“可从 UseData 刷新的当前值”。

## Dashboard 新增策略与 draft UseData

新增策略还没有 `strategy_id`，所以不能调用已保存策略的 UseData 接口。

为了解决这个问题，Dashboard 使用 draft 接口：

```text
POST /api/polymarket/strategies/usedata/draft
```

请求体来自当前弹窗表单，包括：

```json
{
  "strategy_name": "...",
  "strategy_code": "Stragy_Fllow_Truth",
  "state": "Virtual",
  "condition_id": "...",
  "strategy_bankroll": 100,
  "input_json": {}
}
```

draft 接口会根据 `condition_id` 解析市场，补齐 `end_date`、`yes_token`、`no_token`，然后调用和真实运行一致的 `build_use_data()` 生成临时 UseData。

重要限制：

- 新增策略没有 `strategy_id` 时，必须先填写或自选 `Condition ID`。
- 没有 `Condition ID` 时，后端无法知道市场结束时间，也就无法计算 `day_to_end` / `start_day`。
- 前端在没有 UseData 源时会隐藏所有刷新按钮，并提示先填写 `Condition ID`。

## start_day 规则

`start_day` 是策略参数，但系统也会在 UseData 中提供同名字段。

生成规则在 `services/virtual_context_builder.py`：

```text
如果 input_json 已保存 start_day:
    UseData.start_day = input_json.start_day
否则:
    UseData.start_day = UseData.day_to_end
```

同时写入：

```text
start_day
StartDay
```

这样做的原因是：

- 新增策略时，`start_day` 可以从当前剩余天数初始化。
- 保存后，用户确认的 `start_day` 会固定下来。
- 策略运行时不会每轮把 `start_day` 重置为新的 `day_to_end`，避免 `time_ratio = day_to_end / start_day` 长期接近 1。

## 相关文件

后端：

- `services/strategy_registry_service.py`：解析策略代码输入和 `FunctionIntroduction`。
- `services/strategy_settings_service.py`：把策略代码参数合并到 Workspace settings schema。
- `services/strategy_workspace_service.py`：提供已保存策略 UseData 与 draft UseData。
- `services/virtual_context_builder.py`：组装标准 UseData，并注入 `start_day` / `StartDay`。
- `app.py`：暴露 UseData 接口。

前端：

- `static/app.js`：Dashboard 新增/编辑策略弹窗、draft UseData、自动填入、按钮显示控制。
- `static/strategy_workspace_v2.js`：Workspace 设置页参数说明和 UseData 填入。
- `static/styles.css`：Dashboard 参数按钮和提示样式。
- `static/workspace_v3.css`：Workspace 参数按钮和提示样式。

关键实现点：

- Dashboard 新增策略没有 `strategy_id`，会使用 draft UseData；没有 `Condition ID` 时不能生成 draft UseData。
- Workspace 已保存策略有真实 UseData，但其中包含 Tier 5 `input_json` 回声；按钮显示时必须调用“不包含参数原名”的匹配模式，避免所有已保存参数都显示刷新按钮。
- `start_day` 是例外中的标准用法：它虽然也是参数，但 UseData 会额外提供 `StartDay` / `day_to_end` 这类运行时别名，所以可以显示刷新按钮。

## 维护注意

- 修改 `static/app.js` 后，需要同步更新 `templates/index.html` 中的静态资源 query version。
- 修改 `static/strategy_workspace_v2.js` 后，需要同步更新 `templates/strategy_workspace.html` 中的静态资源 query version。
- 不要让 `.strategy-param-autofill { display: ... }` 覆盖 `[hidden]`。如果按钮默认隐藏，CSS 必须保留：

```css
#strategyDynamicInputs .strategy-param-autofill[hidden] {
  display: none;
}
```
