# Factor Editor 分步实施与验收

更新时间：2026-07-25

## 实施原则

每个步骤必须同时提供：

1. 一个范围明确、可以单独使用或验证的产品增量。
2. 稳定的自动化测试和可重复执行的验收命令。
3. 本轮修改文件清单。
4. 明确的通过标准、已知限制和下一步边界。
5. 不修改 Research Grant、预算、权限、策略或交易状态。

## 路线图

| 步骤 | 用户结果 | 状态 |
| --- | --- | --- |
| 1 | 不完整 Factor Draft、fingerprint 与不可变 Definition 生命周期基础 | 已验收 |
| 2A | 统一 `Input → Formula → Output` 布局骨架与 Draft 校验接入 | 已完成 |
| 2B | Input 变量列表、具名 Parameters、受控 Formula 与服务端 FactorSpec 编译 | 已完成 |
| 2C | 产品层修正：自动备份、单一 Save Factor、用户化 Checks、折叠高级细节 | 已完成 |
| 4A | Engine v4 类型化计算图、嵌套/组合、多 Input 和真实执行黄金路径 | 已完成 |
| 4B | 混合频率 Alignment、Conditional 与更完整函数集合 | 已完成 |
| 3 | 必需的可复现 Preview Values、Context、结果与 stale fingerprint | 本轮完成 |
| 5 | Cross-sectional / Hybrid Factor 与 Universe Snapshot 语义 | 待开始 |
| 6 | 完整 Values、Analysis、Dependencies 结果页 | 待开始 |

## 步骤 1：Factor Draft / Validate 契约

### 用户可以验证的行为

- 只填写 Factor 名称的不完整 Draft 可以保存。
- Validation API 会返回稳定的字段路径、错误代码和错误数量。
- 补齐 Input、Formula 和 Output 后，Draft 可以通过 Definition Checks。
- 浏览器持有旧 fingerprint 时不能更新或推进最新 Draft。
- 不可变 `VALIDATED` Factor Definition 仍是生命周期终点。
- 步骤 1 当时允许通过检查的备份直接保存为不可变 Definition；该早期契约已由步骤 3
  的 `Save Draft → Run Preview → Validate Factor` 门禁取代。

### 自动化验收

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.unit.test_factor_draft `
  tests.unit.test_factor_draft_api -v
```

通过标准：

```text
Ran 7 tests
OK
```

完整 Research 回归：

```powershell
.venv\Scripts\python.exe `
  .agents\skills\datatube\scripts\research_workspace_test.py `
  --mode suite `
  --repo G:\linkO\TempFiles\polymarket_datatube
```

通过标准：

```text
Repository discovery          PASS
JavaScript syntax             PASS
Python compile                PASS
Unit tests                    PASS
Integration tests             PASS
Failure injection tests       PASS
```

### 新增本地 API

```text
GET  /api/research/factor-drafts
POST /api/research/factor-drafts
PUT  /api/research/factor-drafts/{draft_id}
GET  /api/research/factor-drafts/{draft_id}/validation
POST /api/research/factor-drafts/validation
POST /api/research/factor-drafts/{draft_id}/validate
```

这些接口供本机 Human Research UI 使用，不属于 Agent 写接口，不创建或修改 Project Research Grant。

### 当前限制

- 步骤 1 只建立 Draft/Validate 后端契约，尚未替换现有 Factor 编辑界面。
- 编译目标仍为 `factor-engine.v3`：单 Input、单 Operator、`TIME_SERIES`。
- 多 Input、通用 DSL、跨频率对齐和横截面计算将在后续步骤加入。

## 步骤 2A：统一 Factor 编辑页面骨架

> 历史验收记录：本节描述 2A 当时的页面。当前产品验收以步骤 2C 为准。

### 用户可以验证的行为

- Research 的 Factor 页面只提供一个混合编辑器，不再切换 UI、Script 或 Split 模式。
- 编辑器左侧按 `Definition → Input → Formula → Output` 排列，右侧持续显示服务端 Formula Checks。
- Input、Function、参数或 Frequency 变化后，Controlled Formula、输出类型、单位和评估频率立即更新。
- 合法文档显示 `Ready`；该阶段曾直接开放 `Validate Factor`。
- 清空 Factor 名称后显示 `FACTOR_NAME_REQUIRED`，`Validate Factor` 立即禁用。
- `Save Draft` 允许保存未完成工作。
- 旧 fingerprint 更新或验证仍由步骤 1 的后端契约阻止。
- 视口宽度小于 1240px 时，编辑区与 Formula Checks 自动改为单列。

### 手工验收路径

1. 打开 `http://127.0.0.1:5001/research`。
2. 进入任一 Research，选择 `Factor`。
3. 点击 `Create in Research`。
4. 确认上下文栏包含 Universe、Draft State 和 Engine。
5. 确认右侧状态为 `Ready`，错误数为 0。
6. 清空 Name，确认状态变为 `Needs attention`、出现 `FACTOR_NAME_REQUIRED`，且 Validate 按钮不可用。
7. 恢复 Name，确认状态重新变为 `Ready`。
8. 点击关闭即可；无需为了验收保存 Draft。

浏览器验收截图：

- `.datatube/factor-step2-ready.png`
- `.datatube/factor-step2-error.png`
- `.datatube/factor-step2-responsive.png`

### 自动化验收

```powershell
.venv\Scripts\python.exe `
  .agents\skills\datatube\scripts\research_workspace_test.py `
  --mode all `
  --repo G:\linkO\TempFiles\polymarket_datatube
```

2026-07-25 实际结果：

```text
Online and repository checks  16 / 16 PASS
Unit tests                     117 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
Browser console                 0 errors / warnings
Factor Draft count after QA     未发生变化
```

### 本步修改范围

- `static/research_workspace_simple.js`：统一编辑器、实时检查、Draft 保存与 Validate 交互。
- `static/research_workspace_simple.css`：68/32 桌面布局、粘性检查区和响应式单列布局。
- `app.py`：增加未保存文档的只读 Validation 接口。
- `tests/unit/test_factor_draft_api.py`：覆盖未保存文档检查。
- `tests/unit/test_research_workspace_api.py`：锁定编辑器文案、结构和布局契约。
- `.agents/skills/datatube/scripts/research_workspace_test.py`：避免把全局 `Backtests` 导航误判为工作区中的 `Backtest` 功能。

### 当前限制与下一步

- `VALUE PREVIEW` 当前明确标记为 `Connected in Step 3`，本步不伪造任何 Factor Value。
- 步骤 3 将固定 Universe Snapshot、时间范围和引擎版本，生成可复现 Preview，并为结果加入 stale fingerprint。
- 本步没有创建或提交 Strategy，也没有执行虚拟或真实交易。

## 步骤 2B：受控 Formula 编辑与服务端编译

> 历史验收记录：本节保留 2B 的编译模型和 Capability 边界。其可见生命周期、技术信息位置和按钮语义已由步骤 2C 替代。

### 用户可以验证的行为

- Input 是变量列表卡片；页面保留 `+ Add Input`，同时明确显示 Engine v3 的 `1 / 1 Inputs` Capability 限制。
- Parameter 是具名记录，例如 `window = 20 bars`，不再是 Function 下拉框的隐式 Window。
- Formula 是可编辑的受控代码区域，例如 `rolling_std(price, window)`。
- Function 下拉框只执行 `Insert Function`，其选择状态不再代表 Formula 本体。
- 每次编辑都把完整文档发给服务端编译器；前端不解析或模拟 Factor DSL 支持。
- 右侧展示服务端生成的完整 FactorSpec，可核对：
  - `formula.input`
  - `formula.operator`
  - `formula.window`
  - `dimension`
  - `frequency`
  - `minimum_observations`
  - `engine_version`
- 嵌套表达式返回稳定错误 `FACTOR_V3_NESTED_EXPRESSION_UNSUPPORTED`。
- 算术或组合表达式返回稳定错误 `FACTOR_V3_COMPOSITION_UNSUPPORTED`。
- 多于一个 Input 返回稳定错误 `FACTOR_V3_INPUT_LIMIT`。
- 检查通过后服务端返回 `can_compile=true`、`can_preview=true`、`can_save_factor=true`。
- Human Save Factor API 不要求 Preview fingerprint。

### 当时 Engine v3 Formula 语法

允许：

```text
rolling_std(price, window)
pct_change(price, window)
ma_crossover(price, fast_window, slow_window)
```

不允许：

```text
rolling_std(pct_change(price, window), window)
pct_change(price, window) + rolling_std(price, window)
rolling_std(price, 20)
```

数值必须先声明为具名 Parameter，不能直接写入 Formula。

### 手工验收路径

1. 打开 Research 的 Factor 页面，点击 `Create in Research`。
2. 确认 Input 显示为 `price` 变量卡片，并显示 `1 / 1 Inputs · Engine v3 capability limit reached`。
3. 确认 Parameter 显示为 `window = 20 bars`。
4. 直接编辑 Formula 为 `rolling_std(price, window)`。
5. 确认右侧状态为 `Ready to save`，主页面显示 Resolved Formula、Required History、Formula Meaning 和人类可读 Output。
6. 改为 `rolling_std(pct_change(price, window), window)`。
7. 确认主页面显示稳定的友好 Capability 说明；错误代码仅出现在折叠的 Advanced Details/接口诊断中。
8. 用 Function 辅助区选择 Rolling Standard Deviation 并点击 `Insert Function`，确认编辑器内容恢复为合法源码。
9. 确认 `Preview Values` 是次级按钮，`Save Factor` 始终可点击。
10. 关闭编辑器，不需要保存。

浏览器验收截图：

- `.datatube/factor-step2b-compiled.png`
- `.datatube/factor-step2b-formula-editor.png`
- `.datatube/factor-step2b-capability-error.png`

### 自动化验收

```powershell
.venv\Scripts\python.exe `
  .agents\skills\datatube\scripts\research_workspace_test.py `
  --mode all `
  --repo G:\linkO\TempFiles\polymarket_datatube
```

2026-07-25 实际结果：

```text
Online and repository checks  16 / 16 PASS
Unit tests                     119 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
Browser console                 0 errors / warnings
Browser QA Draft writes         0
```

### 本步修改范围

- `services/data_platform/factor_formula.py`：受控 Formula AST 解析、Engine v3 Capability 诊断与 FactorSpec 编译。
- `services/data_platform/factor_draft.py`：接入编译结果与不可变 Definition 保存。
- `services/data_platform/definition_registry.py`：公开 Input、Parameter、Formula 和保存流程 Capability。
- `static/research_workspace_simple.js`：变量列表、具名 Parameter、代码编辑器、Function 插入助手和 FactorSpec 面板。
- `static/research_workspace_simple.css`：变量卡片、Parameter 卡片、代码编辑器和编译结果样式。
- `app.py` 与相关单元测试：公开编译结果和 Save Factor 响应。

## 步骤 2C：产品层修正

### 当前用户可见契约

- 顶部只显示 `Create Factor`、`Universe · N Instruments` 和以下三种用户状态：
  - `Unsaved changes`
  - `Changes backed up`
  - `Factor saved`
- Version 不作为可编辑字段；系统使用隐藏 revision/version 管理不可变版本。
- 编辑变化会在 1.4 秒 debounce 后自动备份；不完整内容和编译错误都可以备份。
- 页面只有一个主动作 `Save Factor`。它始终可点击：
  - 有错误时保留内容、备份变化、解释问题并标记第一个相关字段。
  - 无错误时创建 `VALIDATED` 不可变 Factor Definition，并固定到当前 Research。
- `Preview Values` 是独立次级研究动作，不是保存生命周期阶段。
- Formula 主区域只展示用户需要理解的内容：
  - Input binding，例如 `price = Bars.close · 1h`
  - Named Parameter，例如 `window = 20 bars`
  - Editable Formula，例如 `rolling_std(price, window)`
  - Resolved Formula
  - Required History
  - Formula Meaning
- Output 只展示 `Type`、`Unit`、`Evaluation` 和操作符特定的 `Value Meaning`，不要求用户选择“信号方向”。
- 右侧为 `Factor Checks`。编译规范、Engine、fingerprint、spec hash、revision 和 Definition ID 只存在于默认折叠的 `Advanced Details`。
- Engine v4 开始，嵌套和组合表达式会编译成类型化计算图，例如：

```text
universe.rank(time.pct_change(price, window))
```

### 手工验收路径

1. 打开 Research，进入 Factor，点击 `Create in Research`。
2. 确认顶部没有 Engine、fingerprint、Draft State 或 Version 输入框。
3. 确认 Input 映射、`window = 20 bars`、可编辑 Formula、Resolved Formula、Required History 和 Formula Meaning 同时可见。
4. 确认 Output 显示人类可读 Type / Unit / Evaluation / Value Meaning。
5. 确认右侧标题为 `Factor Checks`，Compiled JSON 默认不可见。
6. 展开 `Advanced Details`，核对 Execution Contract、Compiled Specification 和 Audit Information。
7. 把 Formula 改为上面的嵌套表达式，确认右侧仍显示 `Ready to save`，Resolved Formula 展开 Input 和 Parameter。
8. 增加第二个同频 Input 并使用算术组合，确认服务端完成类型与单位检查。
9. 确认底部文案为 `Changes are backed up automatically.`，按钮为 `Preview Values` 和 `Save Factor`。
10. 不点击 `Save Factor` 也可关闭页面；只有停留超过自动备份延迟的编辑才会创建或更新备份。

### 自动化验收

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.unit.test_factor_draft `
  tests.unit.test_factor_draft_api `
  tests.unit.test_research_workspace_api -v
```

当前通过标准：

```text
25 tests
OK
```

2026-07-25 实际全量结果：

```text
Online and repository checks  16 / 16 PASS
Unit tests                     120 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
Browser page console            0 errors / warnings
Browser QA Factor Draft writes  0
```

浏览器验收截图：

- `.datatube/factor-product-corrected.png`
- `.datatube/factor-formula-details.png`
- `.datatube/factor-friendly-error.png`
- `.datatube/factor-advanced-details.png`

### 第 3 步边界

第 3 步才接入：

- 固定 Universe Snapshot。
- Preview 时间范围。
- 已解析的数据 Manifest 集合。
- Engine version 与代码 hash。
- Preview fingerprint、stale 检查和真实 Factor Values。
- Values / Analysis 的空状态、加载、错误与成功结果。

Preview Values 不改变 Factor 的保存资格，也不会替代 `Save Factor`。

## 步骤 4A：Engine v4 类型化计算图

### 本步实际能力

- 新 Factor Draft 由 `factor-engine.v4` 和 `factor_formula.v4` 编译。
- 已存在的 `factor-engine.v3` Definition 保持原版本和原执行语义，不进行静默迁移。
- Formula AST 支持：
  - 嵌套函数；
  - 最多 8 个 Input；
  - `+`、`-`、`*`、`/`；
  - `time.*` 时间序列函数；
  - `universe.rank`、`universe.zscore` 横截面函数；
  - `safe_divide` 和 `abs`。
- 黄金公式：

```text
universe.rank(time.pct_change(price, lookback))
```

- 编译结果包含：
  - 类型化 AST；
  - `TIME_SERIES / CROSS_SECTIONAL / HYBRID` Dimension；
  - 每个 Input 独立的 Required History；
  - Resolved Formula；
  - Formula Meaning 步骤；
  - Output Type / Unit / Evaluation / Value Meaning；
  - 稳定 spec hash 和 Engine code hash。
- `FactorEngineV4` 已真实执行黄金公式：先逐 Instrument 计算时间收益，再在相同 evaluation time 对 Universe 排名。
- Requirements Compiler 会为每个被引用 Input 生成字段与历史依赖，不再只读取第一个 Field。

### 明确未伪装支持的能力

- 4A 只允许同频 Input。
- 不同频率返回稳定错误 `FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED`。
- `Alignment`、`Within Group`、`Conditional` 和 `Financial` 函数属于后续增量。
- 真实 `Preview Values` 仍属于步骤 3，不由编译器伪造。

### 自动化验收

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.unit.test_factor_engine_v4 `
  tests.unit.test_factor_draft `
  tests.unit.test_factor_draft_api `
  tests.unit.test_requirement_compiler `
  tests.unit.test_research_workspace_api -v
```

2026-07-25 实际结果：

```text
Targeted tests                  35 PASS
Repository suite checks         6 / 6 PASS
Unit tests                     125 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
Runtime business writes         0
```

### 真实运行时与浏览器验收

2026-07-25 在重启后的本地运行时完成以下验收：

- 黄金公式 `universe.rank(time.pct_change(price, window))` 返回 `Ready to save`。
- Resolved Formula 为 `universe.rank(time.pct_change(Bars.close @ 1h, 20 bars))`。
- Required History 为 `21 bars`。
- Formula Meaning 明确展示“先计算每个 Instrument 的 20 bars 涨跌幅，再按当前 Universe 排名”两步。
- Output Type / Unit / Evaluation 分别为 `Numeric`、`Percentile rank`、`Every 1h · Bar Close`。
- Advanced Details 中的 Engine / Dimension 分别为 `factor-engine.v4`、`HYBRID`，并展示服务端编译的嵌套 AST。
- 同频双 Input 算术组合通过无持久化校验，Required History 为 `price: 21`、`volume: 1`。
- 不同频双 Input 返回稳定错误 `FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED`。
- 浏览器 Console 为 0 errors / warnings。
- 浏览器验收前后 Factor Draft 都是 5 条，最新 `updated_at` 均为 `2026-07-25T09:28:59+00:00`，因此本次验收没有创建或更新 Draft。

浏览器证据：

- `.datatube/factor-engine-v4-nested.png`
- `.datatube/factor-engine-v4-advanced.png`

## 步骤 4B：显式 Alignment 与 Conditional

### 混合频率执行契约

- 不同频率的序列不能直接参与算术、比较或 Conditional。
- 用户必须显式写出 `align.asof(source, reference)` 或
  `align.forward_fill(source, reference)`。
- 第二个参数提供评估时间轴；执行器对每个评估点只选择
  `source.available_time <= reference.available_time` 的最新源值。
- 每个 Input 在 Formal Run 中按自己的 `field + frequency` 绑定 Frozen Manifest，
  不再把不同频率的 Bars 合并成同一数组。
- Requirement Compiler 为每个频率生成独立 Requirement。
- 对 Alignment 结果继续使用滚动函数时，Required History 会把评估窗口换算成每个
  原始 Input 的观测数量。例如在 1h 时间轴对 1d Input 计算 20-bar rolling mean，
  需要 `price: 20 bars` 与 `daily_input: 2 bars`。

未显式 Alignment 的混合频率公式继续返回：

```text
FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED
```

### 本步新增函数

- Alignment：`align.asof`、`align.forward_fill`。
- Conditional：
  - `greater`、`less`、`equal`
  - `where`
  - `is_null`、`is_finite`、`fill_null`
  - `logical_and`、`logical_or`、`logical_not`
- Over Time：
  - `time.lag`、`time.ratio`、`time.log_return`
  - `time.sum`、`time.median`、`time.min`、`time.max`
  - `time.variance`、`time.rank`、`time.zscore`
- Across Universe：`universe.percentile`、`universe.demean`。
- Math：`safe_divide`、`abs`。

Capability 的 Function Schema 与编译器支持集合由自动化测试强制保持完全一致。

### 验收公式

混合频率：

```text
safe_divide(price, align.asof(daily_volume, price))
```

Conditional：

```text
where(
  greater(price, time.mean(price, window)),
  price,
  time.mean(price, window)
)
```

### 仍未宣称支持

- `resample` 与自定义聚合规则。
- Within Group 与 Financial 函数。
- correlation / covariance / beta。
- 通用数字、百分比或货币 Parameter；当前 Parameter 仍是正整数 bars。
- 真实 Preview Values；仍由步骤 3 接入固定 Snapshot、时间范围、Manifest 和
  Preview fingerprint。

### 4B 自动化与浏览器验收

2026-07-25 实际结果：

```text
Targeted Factor tests           46 PASS
DataTube online/repo checks  16 / 16 PASS
Unit tests                     137 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
JavaScript syntax               PASS
Python compilation              PASS
Runtime business writes        false
Browser console errors           0
```

无持久化校验接口确认：

- `safe_divide(price, daily_volume)` 返回
  `FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED`，不能直接保存。
- `time.mean(align.asof(daily_volume, price), window)` 可以编译和保存，
  Resolved Formula 明确保留 `1d -> 1h` 的显式 as-of 对齐。
- `where(greater(price, price), price, price)` 可以编译和保存，输出类型与单位由
  服务端推断。
- 无持久化校验前后 Draft 数量均为 6。

浏览器确认：

- 编辑器显示 `2 of 8 Inputs configured`，并允许为每个 Input 独立选择 Field 与
  Frequency。
- Function Picker 显示 35 个服务端支持的函数，包括 Alignment 与 Conditional。
- Formula 仍是可直接编辑的受控代码区域；Function Picker 仅用于插入文本。
- Factor Checks 显示 `Evaluation timing is safe`，并说明混合频率必须显式对齐。
- 浏览器 Console 为 0 errors / warnings。

浏览器证据：

- `.datatube/factor-engine-v4-4b-inputs.png`
- `.datatube/factor-engine-v4-4b-formula-picker.png`
- `.datatube/factor-engine-v4-4b-editor.png`

浏览器 QA 过程说明：

- QA 开始时项目已有 5 条 Factor Draft。
- 在检查 `Add Input` 后停留超过 1.4 秒，页面按产品契约自动创建了 1 条备份 Draft，
  Draft 总数变为 6。
- 该写入已如实保留在研究历史中；未删除、未验证、未发布，也未触发 Preview、
  Strategy 或交易流程。

## 步骤 3：真实 Preview Values 与 Validate 门禁

### 用户可见生命周期

Factor 编辑器现在只允许以下顺序：

```text
Save Draft → Run Preview → Validate Factor
```

- `Save Draft` 保存当前 Input、Formula、Parameters 与 Output；有 Definition 错误的内容
  仍可作为 Draft 备份。
- `Run Preview` 只对已保存且可编译的 Draft 开放。
- `Validate Factor` 只在当前 Draft fingerprint 拥有一条仍然有效的 READY Preview 时开放。
- 修改 Draft、Preview 时间范围、当前 Universe Snapshot、Manifest 身份、Engine code hash
  或 Factor spec hash 后，旧 Preview 不能用于 Validate。

### Preview 固定输入

每条 `factor_preview.v1` 历史对象固定：

- Draft ID 与 Draft fingerprint；
- Universe Snapshot ID、Universe fingerprint 与完整 Instrument 成员；
- UTC start / end 时间范围；
- 每个 Input / Instrument 的 Dataset、Field、Frequency 与 Manifest；
- Manifest ID 与 Manifest hash；
- Engine version、Engine code hash 与 Factor spec hash；
- Preview fingerprint；
- 真实 Factor values、覆盖率、均值、标准差、分布与每个 Instrument 的摘要。

Preview 只读取 READY Manifest，并在计算前校验每个 Parquet 分区的文件大小、SHA-256、
Schema、row count、完整 bar 与 `available_time` 语义。

### 新增本地 API

```text
GET  /api/research/factor-drafts/{draft_id}/preview-context
POST /api/research/factor-drafts/{draft_id}/previews
GET  /api/research/factor-drafts/{draft_id}/previews/latest
GET  /api/research/factor-previews/{preview_id}
POST /api/research/factor-drafts/{draft_id}/validate
```

`validate` 现在必须同时提交：

```text
expected_fingerprint
preview_id
preview_fingerprint
```

### 自动化验收

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.unit.test_factor_draft `
  tests.unit.test_factor_draft_api `
  tests.unit.test_factor_preview `
  tests.unit.test_research_workspace_api -v
```

2026-07-25 实际结果：

```text
Targeted Factor / UI tests      29 PASS
DataTube online/repo checks  16 / 16 PASS
Unit tests                     140 PASS
Integration tests              15 PASS
Failure injection tests         1 PASS
JavaScript syntax               PASS
Python compilation              PASS
Browser console errors           0
```

自动化覆盖：

- 真实 Canonical Bars Parquet Manifest 读取与物理校验；
- 真实 Factor Engine v4 values 计算；
- 相同 Preview closure 返回同一 Preview ID；
- Draft 修改后旧 Preview 稳定返回 stale；
- Manifest 时间范围不足时稳定阻断；
- 没有当前 Preview 时不能 Validate；
- Preview 与最终 immutable Factor Definition 的审计关联。

### 当前 Research 的真实运行时验收

使用浏览器 QA Draft `factor_draft_53c2fa37d7d449fabe47fee3bfb809a7` 完成：

```text
Preview ID        factor_preview_3203720428ff4bd58a02ed4411bc111d
Preview status    READY
Preview fingerprint
                  cb36878652b00b643cdb6cd05d89f5d64227dca5c50e002b58de6cc0f06bfa08
Universe Snapshot universe_snapshot_b8b8f70c06f094e7c0d4e336
Manifest          manifest_78d91069c0254bbf985d064ad0aa3a56
UTC range         2026-07-17T13:59:59.999000+00:00
               → 2026-07-24T13:59:59.999000+00:00
Value rows        169
Valid values      169
Coverage          100%
Latest value      2.118736887865033
```

- Preview 完成前 `can_validate=false`；完成后 `can_validate=true`。
- 浏览器显示真实 Latest Value、Coverage、Mean 与 Std Dev。
- Preview evidence 展示完整 fingerprint、Snapshot、Manifest、Engine 与 spec hash。
- 浏览器中修改 Preview 时间范围后，状态立即变为 `STALE`，`Validate Factor` 被禁用。
- 本轮没有点击 `Validate Factor`，因此没有创建或固定新的 Factor Definition。

浏览器证据：

- `.datatube/factor-step3-values-and-fingerprint.png`
- `.datatube/factor-step3-preview-evidence.png`
- `.datatube/factor-step3-preview-stale.png`

### 当前限制与下一步边界

- Preview 单次最多 31 天、20,000 条结果，避免编辑器承担正式长周期 Run。
- 当前编辑器展示汇总与 latest cross-section；完整时间序列图、分布图、
  Dependencies 和导出属于步骤 6。
- Within Group、Financial、correlation / covariance / beta 仍未宣称支持。
- Formal Research Run、Strategy 与交易生命周期不由 Factor Preview 自动触发。
