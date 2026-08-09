# DataTube Research 当前系统解析

> 解析范围：当前仓库中的 Research Workspace、Universe、Factor、Alpha、Requirement、Data、Run 与 Research Backtest 实现。
>
> 代码与运行态复核日期：2026-08-04。
>
> 本文的目标不是解释量化研究的一般概念，而是说明这个项目现在有哪些对象、这些对象如何连接、运行时究竟冻结什么、哪些能力已经进入正式执行链，以及当前实现仍存在哪些结构性边界。

## 1. 结论先行

当前 Research 不是一个从页面直接调用回测函数的简单流程，而是一个由五个层面组成的可审计研究系统：

1. **工作区与引用层**：Research Project 组织当前研究上下文；Universe、Factor、Alpha、Requirement 可以是当前 Research 的本地对象，也可以引用 Library 中的指定版本。
2. **定义与编译层**：Universe 被解析为成员快照；Factor 被编译为类型化计算图；Alpha 固定引用 Factor 版本；RequirementCompiler 将各模块的数据依赖编译成不可变 RequirementSet。
3. **数据与 Manifest 层**：Catalog 描述逻辑数据集，Parquet 保存物理数据，Manifest 固定一次可验证的数据版本，Resolver 将 RequirementSet 确定性绑定到精确 Manifest。
4. **预览与冻结层**：Run Inputs Preview 对 Definition、Data、Authorization、Execution 四个维度做 Readiness 检查；创建 Run 时再次校验，并原子写入 Frozen Bundle、预算预留、QUEUED Run 与 Outbox。
5. **执行与产物层**：Formal Research Worker 只消费 Frozen Bundle 中固定的输入，计算 Factor、Alpha、Evaluation、Portfolio 与 Backtest Artifact，并记录 Lineage。

系统最核心的设计不是“能算一个指标”，而是：

```text
定义可版本化
→ 数据可冻结
→ 运行前可解释地阻断
→ 运行输入不可变
→ 结果可追溯
→ Agent 只能在服务端固定的 Research Session 边界内操作
```

Research Agent 支持从自然语言目标 START，或从 Session、Project、Run、Preview、Bundle、Factor、Alpha RESUME。用户不需要创建或管理 Grant；服务端创建固定研究额度，Agent 后续只传 `session_id`。Agent 当前允许创建项目级 Universe、Snapshot、Definition、Requirement、Preview 和 Run，但明确禁止增加 Session 额度、发布到全局 Library、删除研究历史以及启用实盘。

## 2. 代码架构与职责分布

Research 的业务逻辑主要集中在 `services/data_platform`，`app.py` 提供 Human UI 与 Agent API 两套入口。

| 模块 | 主要实现 | 当前职责 |
| --- | --- | --- |
| Research Session / Context | `research_agent_session.py`、`research_context_resolver.py` | START/RESUME、Brief、状态、基线、迭代、恢复上下文与固定研究额度 |
| Research Project / 内部授权 | `research_control_plane.py`、`research_agent_authorization.py` | Project、内部兼容授权、预算、任务与安全边界 |
| Shared Universe | `shared_universe_service.py` | 稳定 Universe 身份、不可变 Revision、Resolution、项目 Binding 与共享影响 |
| Formal Universe | `universe_service.py` | 供正式执行链消费的 UniverseDefinition 与 UniverseSnapshot |
| Factor Draft | `factor_draft.py`、`factor_formula.py` | 可保存的不完整草稿、公式编译、校验、不可变 Definition 创建 |
| Factor Engine v4 | `factor_engine_v4.py` | 类型化 AST、多 Input、混合频率显式对齐、时间序列与横截面计算 |
| Factor Preview | `factor_preview.py` | 固定 Snapshot、时间范围、Manifest 与代码身份，计算真实预览值 |
| Factor / Alpha Registry | `definition_registry.py` | 不可变 Definition、版本、状态、Project 引用、Library 关系 |
| Alpha | `factor_alpha.py` | Factor 组件变换、加权合成、横截面覆盖、排名与百分位 |
| Requirement Workspace | `requirement_workspace_service.py` | 用户可编辑的共享 Requirement、Research 引用与数据状态 |
| Requirement Compiler | `requirement_compiler.py` | 将 Factor、Universe、Evaluation、Backtest 与 Manual 依赖编译为 RequirementSet |
| Catalog / Manifest | `catalog_service.py`、`canonical_bars.py`、`data_client.py` | 数据集目录、内容寻址 Parquet、Manifest 提交与物理校验 |
| Manifest Resolver | `manifest_resolver.py` | 将有效 Requirement 精确绑定到可验证 Manifest |
| Run Preview | `run_preview_service.py`、`run_contracts.py` | 四维 Readiness、稳定原因码、closure 与 preview fingerprint |
| Frozen Run | `research_run_service.py` | 原子创建 Run、冻结 Bundle、幂等、预算预留、Worker Lease |
| Portfolio / Backtest | `portfolio.py`、`research_backtest.py` | Alpha 到目标权重、NEXT_BAR_OPEN 回测、订单、净值与指标 |
| Artifact / Lineage | `artifact_service.py`、`provenance_service.py` | 产物物化、依赖关系、内容哈希、Pin 与审计 |
| HTTP API / UI | `app.py`、`research_workspace_simple.js` | Research 工作台、本机 Human API、受控 Agent API |

从结构上看，项目正在同时保留“产品层的新对象模型”和“正式执行层的兼容对象模型”。最明显的是 Universe 与 Requirement：

- 产品层使用稳定的 Shared Universe 和可复用 Requirement；
- Formal Run 仍以 `universe_snapshot_id` 和编译后的 `requirement_set_id` 为执行合同。

这不是重复实现，而是一层适配：产品需要可共享、可编辑、可查看影响的对象；执行层需要不可变、可哈希、可冻结的输入。

## 3. Research Project：研究上下文，不是结果容器

Research Project 是当前研究的组织边界。项目本身保存标题、目标、修订号、状态以及各类引用，但不会把所有对象内容复制到 Project 记录中。

当前 UI 的一级入口为：

```text
Research / Library / Runs / Data Catalog / Agent Monitor / Approvals / Settings
```

单个 Research 内部为：

```text
Overview / Universe / Factor / Alpha / Data / Strategy / Runs
```

其中 Strategy Tab 是产品预留面，不属于本文描述的 Formal Research Run 闭环。Research 结果不会自动创建 Strategy，也不会触发 Virtual 或 Real Execution。

### 3.1 Project revision 的作用

`research_projects.revision` 不只是展示版本号，它参与 Run Inputs Preview 的 Definition Closure。以下行为会推动项目修订或使下游对象失效：

- 修改或切换 Primary Universe；
- 更新共享 Universe 的当前 Revision；
- 添加、替换或移除 Factor / Alpha 引用；
- Requirement 重新编译；
- 其他会改变正式输入闭包的操作。

创建正式 Run 时，服务端会比较 Preview 中的 `project_version` 与数据库当前 revision。两者不同即返回 `PREVIEW_STALE`，不能用旧 Preview 创建新 Run。

### 3.2 Research 对象与 Library 对象

当前项目坚持两个不同身份：

```text
Research 工作对象
→ Validate
→ Publish
→ 新的不可变 Library Asset
```

发布不会把 Research 对象原地变成 Library 对象。Library 中的指定版本被其他 Research 引用后保持只读；后续发布新版本不会自动升级已有引用。

对于 Agent，边界更严格：

- Agent 可以创建 `library_scope=PROJECT` 的 Factor / Alpha Definition；
- Agent 可以将其固定到当前 Project；
- Agent 不能发布到 Global Library；
- Agent 不能扩大 Research Session Policy；用户可在 AgentMonitor 暂停或继续 Session。
- 内部兼容授权由服务端创建且不通过 Agent API 暴露。

## 4. Universe：当前实现实际上有两层

### 4.1 产品层：Shared Universe

当前 Research Workspace 的主要 Universe 模型由 `SharedUniverseService` 提供。它的核心身份是稳定的 `universe_id`，内容变化通过不可变 Revision 表达。

```text
universe_id
  ├─ revision_id v1
  │    └─ resolution_id v1
  ├─ revision_id v2
  │    └─ resolution_id v2
  └─ Project Bindings
       ├─ Research A / PRIMARY
       └─ Research B / REFERENCE
```

当前支持三种产品级 Universe：

| 类型 | 定义方式 | Resolution 结果 |
| --- | --- | --- |
| `instrument_set` | 明确的 Instrument 成员 | `instrument_ids` |
| `composite_set` | 对其他 Universe 做 `union`、`intersection`、`difference` | 合并后的 `instrument_ids` |
| `multi_leg_set` | Manual、Cartesian Product、Unordered Combination 或 Permutation | `instrument_tuples` 与扁平化 `instrument_ids` |

Multi-leg 组合支持：

```text
manual
cartesian_product
unordered_combination
permutation
```

并提供以下保护：

- 是否允许同一 Instrument 出现在多个 Leg；
- 是否将反向组合视为同一组合；
- `max_combinations` 上限；
- 全局硬上限 `100,000`；
- 循环 Universe 引用检测；
- Source Universe 类型与成员范围检查。

### 4.2 Revision、共享编辑与项目 Binding

Shared Universe 更新不是覆盖旧内容，而是：

```text
校验 expected_current_revision_id
→ 检查其他 Research 的共享使用关系
→ 创建新 revision_id
→ 重新 Resolution
→ 创建兼容 Snapshot
→ 更新所有活跃 Binding
→ 标记 Requirements stale
→ 增加受影响 Project revision
```

这带来两项重要语义：

1. **并发保护**：编辑者必须提交自己打开时看到的 `expected_current_revision_id`。如果期间 Universe 已变化，系统返回 `UNIVERSE_REVISION_CONFLICT`。
2. **共享影响保护**：如果同一 Universe 正被其他 Research 使用，更新前必须显式确认，否则返回 `UNIVERSE_SHARED_EDIT_CONFIRMATION_REQUIRED`。

Research 与 Universe 的关系保存在 Binding 中：

- `PRIMARY`：当前 Research 的主 Universe，决定默认 Snapshot 与数据范围；
- `REFERENCE`：项目可查看或复用，但不作为默认主集合；
- 移除 Binding 不删除 Universe，也不改变历史 Run；
- 切换 Primary 会推动 Project revision，并使依赖当前 Universe 的 Requirements 重新检查。

### 4.3 正式执行层：UniverseDefinition / Snapshot

Formal Research Run 尚未直接以 Shared Universe Revision 为唯一执行输入。`SharedUniverseService` 每次 Resolution 后会创建一个兼容的、不可变的 `STATIC_LIST` UniverseDefinition 与 UniverseSnapshot。

因此当前实际链路为：

```text
Shared Universe
→ immutable Revision
→ Resolution（成员或组合）
→ legacy-compatible STATIC_LIST Definition
→ Universe Snapshot
→ Formal Run
```

底层 `UniverseService` 仍支持：

```text
STATIC_LIST
TOP_N_BY_TURNOVER
```

但当前 Shared Universe 产品模型主要覆盖 Instrument Set、Composite Set 与 Multi-leg Set。`TOP_N_BY_TURNOVER` 仍属于底层兼容能力，并未等价地成为 Shared Universe 编辑器中的动态规则类型。

### 4.4 当前 Multi-leg 的真实边界

这是当前项目中需要特别说明的一点：

- Shared Universe 能够生成并保存 `instrument_tuples`；
- Run closure 也能保存 `resolved_instrument_tuples`；
- 但 Formal Executor 读取数据后，主要使用的是扁平化 `actual_instrument_ids`；
- Alpha、PortfolioEngine 与 ResearchBacktestProvider 当前仍按单 Instrument 分数和目标权重运行。

因此当前 Multi-leg 能力已经解决“组合集合的定义、版本和冻结”，但尚未完成真正的：

- Pair Spread 计算；
- Tuple 级 Factor / Alpha；
- Leg 间约束；
- 组合级目标权重；
- 多腿同步成交与组合 PnL 会计。

换句话说，当前不能仅因为 Universe 中出现 Pair / Multi-leg Set，就把回测解释成真正的多腿策略回测。

## 5. Factor：当前项目中成熟度最高的研究定义

Factor 已经从简单的单算子配置演进为完整的 Draft、编译、真实 Preview 和不可变 Definition 生命周期。

### 5.1 用户生命周期

当前真实门禁是：

```text
Save Draft
→ Run Preview
→ Validate Factor
→ 可选 Publish to Library
```

各阶段职责不同：

- **Save Draft**：保存当前 Input、Parameter、Formula 与 Output。即使文档不完整或编译失败，也允许作为备份保存。
- **Run Preview**：固定 Snapshot、时间范围、Manifest 与代码身份，计算真实 Factor Values。
- **Validate Factor**：只有当前 Draft fingerprint 对应一条仍有效的 READY Preview 时才允许。
- **Publish**：将已经验证的 Research Definition 发布为独立的不可变 Library Asset。

### 5.2 Draft 与不可变 Definition

Factor Draft 是可变工作对象，支持自动备份和 optimistic fingerprint 校验。Validated Factor Definition 是不可变对象，身份至少包括：

```text
definition_id
name
version
state
spec
spec_hash
engine_version
code_hash
owner_project_id
library_scope
```

同名同版本但 `spec_hash` 不同会被拒绝。代码变化会改变 `code_hash`，因此不会把旧引擎产物错误复用为新产物。

Project 对 Factor 的引用模式为：

- `TRACK_DRAFT`：用于编辑跟踪，不能进入正式 Run；
- `PINNED`：固定 Definition ID、Version 与 Spec Hash，可以进入 Run closure。

### 5.3 Factor Engine v4

新 Draft 默认编译到 `factor-engine.v4`。已有 `factor-engine.v3` Definition 保留原身份和执行语义，不做静默迁移。Formal Worker 会根据 Definition 中保存的 `engine_version` 选择 v4 或兼容 v3 引擎。

Engine v4 的核心不是“增加几个函数”，而是把公式编译为类型化计算图：

```text
Input Variables
→ Parsed AST
→ Type / Unit / Frequency Validation
→ Required History
→ Typed Graph Spec
→ Deterministic Execution
```

当前支持：

- 最多 8 个 Input；
- Input 独立声明 Dataset、Field 与 Frequency；
- 具名正整数 Bar Parameter；
- 嵌套函数；
- `+`、`-`、`*`、`/`；
- 时间序列函数；
- 横截面函数；
- Conditional；
- 显式混合频率 Alignment；
- 输出 Type、Unit、Dimension 与 Required History 推导。

当前主要函数类别：

| 类别 | 示例 |
| --- | --- |
| Over Time | `time.pct_change`、`time.mean`、`time.std`、`time.ema`、`time.rank`、`time.zscore` |
| Across Universe | `universe.rank`、`universe.percentile`、`universe.zscore`、`universe.demean` |
| Alignment | `align.asof`、`align.forward_fill` |
| Conditional | `greater`、`less`、`equal`、`where`、`fill_null`、逻辑运算 |
| Math | `safe_divide`、`abs` 与四则运算 |

编译器会把 Factor 分类为：

```text
TIME_SERIES
CROSS_SECTIONAL
HYBRID
```

例如：

```text
universe.rank(time.pct_change(price, lookback))
```

会先对每个 Instrument 计算时间收益，再在相同 evaluation time 对当前 Universe 做横截面排名。

### 5.4 混合频率不是隐式 Forward Fill

不同频率 Input 不能直接做算术或比较，必须显式写出：

```text
align.asof(source, reference)
align.forward_fill(source, reference)
```

执行时只允许选择：

```text
source.available_time <= reference.available_time
```

的最新源值。这一约束同时进入：

- Formula 编译；
- Required History 推导；
- RequirementCompiler；
- Preview Manifest 绑定；
- Formal Worker 的 Input Binding。

未显式对齐的混频公式稳定返回：

```text
FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED
```

### 5.5 Factor Preview 是真实研究证据

Factor Preview 不是前端模拟，也不是用几行示例数据“试算”。每条 `factor_preview.v1` 固定：

```text
Draft ID / Draft fingerprint
Universe Snapshot ID / Universe fingerprint / 完整成员
UTC start / end
每个 Input × Instrument 的 Dataset / Field / Frequency / Manifest
Manifest ID / Manifest hash
Engine version / code hash
Factor spec hash
Preview fingerprint
真实 Factor Values 与统计摘要
```

Preview 执行前会验证 Parquet 文件大小、SHA-256、Schema、Row Count、完整 Bar 和 `available_time`。当前单次 Preview 最多 31 天、最多 20,000 条结果。

以下变化都会使旧 Preview 不能用于 Validate：

- Draft 内容或 fingerprint 变化；
- Preview 时间范围变化；
- Primary Universe Snapshot 变化；
- Manifest ID 或 Manifest Hash 变化；
- Engine Code Hash 变化；
- Factor Spec Hash 变化。

需要特别区分：

| 对象 | 用途 | 是否创建正式 Run |
| --- | --- | --- |
| Factor Preview | 验证一个 Factor Draft 的真实值与数据闭包 | 否 |
| Run Inputs Preview | 验证整个 Project 的 Definition、Data、Authorization、Execution closure | 否 |
| Formal Research Run | 冻结项目输入并产出正式 Artifact / Metrics / Lineage | 是 |

## 6. Alpha：固定 Factor 身份的组合层

Alpha 的职责是组合已经验证并固定的 Factor，而不是按名称临时查找 Factor。

Alpha Definition 的每个组件会保存：

```text
factor_definition_id
factor_version
factor_spec_hash
factor_name
weight
transform
ascending
```

这意味着 Factor 后续发布新版本，不会悄悄改变已有 Alpha。

### 6.1 当前计算语义

AlphaEngine 在每个 `as_of_time` 上：

1. 收集每个组件的 Factor Values；
2. 限制到当前 Universe Snapshot 成员；
3. 对多个组件取共同存在的 Instrument 交集；
4. 检查 `minimum_cross_section_size` 与 `minimum_coverage`；
5. 对每个组件执行 `RAW` 或 `CS_RANK` 变换；
6. 乘以权重并求和得到 `raw_scores`；
7. 计算最终 `ranks` 与 `percentiles`；
8. 将各组件最晚的 `available_time` 作为 Alpha 可用时间。

输出显式区分：

```text
raw_scores
ranks
percentiles
coverage
available_time
universe_snapshot_id
```

同值排名使用平均名次逻辑，不通过 Instrument ID 人为打散成不同经济排名。

### 6.2 与 Factor v4 横截面函数的关系

Factor v4 已经支持 `universe.rank`、`universe.zscore` 等横截面函数，因此 Factor 和 Alpha 的能力存在一部分重叠。

当前合理分工应是：

- Factor：表达一个可独立复用、可单独 Preview 和 Evaluation 的信号；
- Alpha：固定多个 Factor 版本，并进行组合级的标准化、方向和权重管理。

如果 Factor 本身已经输出 Percentile Rank，而 Alpha 组件又使用 `CS_RANK`，就可能发生二次排名。系统不会自动判断这是否符合研究意图，设计 Alpha 时需要显式检查 Factor 的 Output Unit 与组件 Transform。

### 6.3 当前 Alpha 的产品差距

Alpha 已有不可变 Definition、Project Pin、正式计算与 Alpha Evaluation，但其编辑与预览生命周期没有 Factor 那么完整：

- Factor 已有独立 Draft、真实 Preview Values 与 Preview fingerprint 门禁；
- Alpha 主要依赖 Definition 校验和 Formal Alpha Evaluation；
- 当前没有与 Factor Preview 完全对称的 Alpha Draft Values Preview 工作流。

因此，当前系统成熟度表现为：Factor Authoring 最完整，Alpha Execution 已落地，但 Alpha Authoring 仍相对简单。

## 7. Requirement：必须区分“编辑对象”和“编译产物”

当前项目里名为 Requirement 的对象实际分成两类。

### 7.1 用户编辑的共享 Requirement

`RequirementWorkspaceService` 将 Requirement 作为 Library 拥有的共享对象。Research 保存对它的引用，而不是在每个项目里复制无身份 JSON。

一个用户 Requirement 包含：

```text
Target
  ├─ SPECIFIC_UNIVERSE
  └─ MANUAL_INSTRUMENTS

Scope
  ├─ Provider / Gateway
  ├─ Market / Asset Type
  └─ Instruments

Time
  ├─ FIXED
  ├─ FIXED_START_LATEST_END
  ├─ LATEST_AVAILABLE
  ├─ ROLLING
  └─ LIVE

Data
  ├─ Dataset Type
  ├─ Frequency
  ├─ Fields
  └─ Delivery Mode

Advanced
  ├─ Point-in-time
  ├─ Available-time
  ├─ Adjustment
  ├─ Quality Policy
  ├─ Provider Policy
  └─ Gap Policy
```

共享语义为：

- 直接编辑共享 Requirement 会影响所有引用它的 Research；
- `Save As` 创建新的 Library Requirement，并只替换当前 Research 的引用；
- `Duplicate` 创建新的共享对象；
- 从 Research 移除引用不会删除 Library 对象；
- Derived Requirement 不能直接删除，必须修改其来源 Definition。

### 7.2 RequirementSet：运行使用的编译合同

`RequirementSet` 不是用户直接编辑的对象。它是 `RequirementCompiler v2` 根据项目当前闭包生成的不可变产物。

编译输入包括：

- Factor 的每个 Input；
- Universe 数据依赖；
- Evaluation 数据依赖；
- Backtest 数据依赖；
- Manual Requirement；
- 当前 Universe Snapshot、时间范围和数据政策上下文。

对于 Factor Engine v4，编译器会遍历每个 Input，而不是只读取第一个字段。混合频率 Factor 会产生按频率分离的 Requirement，并使用每个 Input 的 `required_history` 计算预热起点。

编译链为：

```text
Factor / Universe / Evaluation / Backtest / Manual Sources
→ Declared Requirements
→ Semantic Merge
→ DataRequirement
→ Immutable RequirementSet
→ Dependency Links
```

合并键包括：

```text
target_type
instrument_ids
data_type
frequency
lookback_unit
refresh_mode
adjustment
time_semantics
point_in_time_policy
quality_policy
source_policy
```

只有这些语义兼容的需求才能合并字段、最早历史起点与最大预热观测数。

相同编译输入产生相同 fingerprint 和 RequirementSet；输入变化会创建新版本，旧版本变为 `SUPERSEDED`，不会被覆盖。

### 7.3 Universe 更新后的 Requirement 行为

Shared Universe 创建新 Revision 时，会把相关 Binding 标记为 `requirements_stale_at`。随后 Requirement Workspace 会做 Reconcile：

- 比较旧成员与当前成员；
- 检查 Requirement 是否确实指向当前 Universe；
- 检查 Provider、资产类型和数据能力是否兼容；
- 兼容时可重新编译 RequirementSet；
- 不兼容时显示 `FAILED` 或 `UNAVAILABLE`，并给出需调整的 Provider / Contract 原因。

这里需要区分两种状态：

- **Data Status / Coverage**：面向用户，说明字段、频率、时间范围和数据准备程度；
- **Run Readiness / DATA dimension**：正式运行门禁，还会检查精确 Manifest、Provider、PIT、available-time、质量、Gap 和物理完整性。

看到 UI 显示“Data Ready”并不自动意味着某个旧 Run Preview 仍然有效。

## 8. Data 与 Manifest：系统可复现性的基础

### 8.1 三层身份

当前数据链使用三个不同层次：

| 层次 | 含义 |
| --- | --- |
| Dataset Catalog | 逻辑数据集身份、Instrument、Provider、Frequency、Fields、状态与覆盖 |
| Parquet Object | 实际数据内容，使用内容寻址文件名 |
| Dataset Manifest | 固定一次数据版本、分区、Checksum、Schema、Row Count 与时间范围 |

物理对象使用类似以下路径：

```text
.../year=YYYY/month=MM/objects/sha256-<content_hash>.parquet
```

提交过程为：

```text
临时写入
→ 关闭文件
→ 质量检查
→ SHA-256
→ 原子 Rename
→ Manifest Commit
→ Catalog READY
```

旧文件不会被新内容静默覆盖。

### 8.2 FrozenManifestData 校验

Formal Preview、Factor Preview 和 Worker 都依赖物理校验。主要检查：

- Manifest 状态为 `READY`；
- 文件存在；
- 文件大小与 SHA-256 匹配；
- Parquet 内嵌 Schema Version 匹配；
- Row Count 与时间范围匹配；
- 必需字段存在；
- Bar 为 `COMPLETE`；
- `available_time >= bar_end_time`。

如果 Bundle 中引用的 Manifest 后续被损坏，`verify_bundle` 会把 Bundle 标记为：

```text
integrity_status = DAMAGED
reuse_status = PROHIBITED
reuse_reason_code = MANIFEST_DAMAGED
```

历史记录仍存在，但不允许再次执行。

### 8.3 Deterministic Manifest Resolver

Resolver 不只是找“最新文件”，而是按 Requirement 逐项筛选：

- Instrument；
- Data Type；
- Frequency；
- Fields；
- 请求时间范围；
- Warmup；
- Provider / Source Policy；
- Adjustment；
- Point-in-time Policy；
- Available-time Semantics；
- Quality Policy；
- Known Gaps；
- Manifest 物理完整性。

多个候选满足条件时，再按 Source Preference、Quality、Gap 数量和 Manifest Version 进行稳定排序。输出是精确的 `manifest_id` 集合和每项 Requirement 的 Binding。

如果结果存在歧义、缺字段、时间范围不足、Provider 不匹配或文件损坏，Resolver 不会猜测，而是生成稳定原因码，例如：

```text
FIELD_NOT_COVERED
FREQUENCY_MISMATCH
REQUESTED_RANGE_NOT_COVERED
WARMUP_NOT_COVERED
PROVIDER_MISMATCH
PIT_POLICY_MISMATCH
KNOWN_GAPS
MANIFEST_DAMAGED
RESOLVER_AMBIGUOUS
```

### 8.4 自动维护与 Backfill 边界

数据缺口不能由 Research Run 临时联网补齐。后台 Requirement 维护器每 30 秒扫描 Requirement Library 与活跃 Research，把受支持的缺口转换为独立、受控、幂等的 Binance、OpenBB Equity Daily 或 Polymarket 数据任务。任务包含固定范围、预算、Lease、重试和审计；系统维护使用隐藏项目与内部授权标记，不借用用户 Grant 或扩大 Research Session Policy。

Research 与 Library 页面只读取维护状态和实时进度，不通过页面刷新、切换 Tab 或按钮创建任务。正常缺口使用 `QUEUED / PREPARING / CHECKING`；只有 Provider / Contract 不支持或任务终态失败才使用 `UNAVAILABLE / FAILED`。详细语义见 [Requirement 数据自动维护与实时进度](04-operations/requirement-data-maintenance.md)。

这保证了：

```text
Run 只消费已经提交的数据
而不是在计算过程中改变自己的输入
```

## 9. Run Inputs Preview：完整项目的运行前证明

正式 Run 当前支持三种类型：

```text
FACTOR_EVALUATION
ALPHA_EVALUATION
RESEARCH_BACKTEST
```

三者不是同一结果页的不同名称：Factor Evaluation 结束于因子预测能力与分组表现；Alpha Evaluation 结束于 Signal 的 IC/Rank IC、Decay、Turnover 与 Regime；只有 Research Backtest 负责 Portfolio、Execution、Position、Trade、Cost、Equity、Performance 与 Drawdown。历史上同时包含 Alpha Evaluation 和 Backtest Artifact 的 Run 继续以 `LEGACY_HYBRID_RUN` 只读展示。完整栏目见 [Factor Run、Alpha Run 与 Research Backtest MVP](03-features/factor-alpha-run-mvp.md)。

Preview 对四个维度生成 Readiness：

| 维度 | 核心检查 |
| --- | --- |
| DEFINITION | Project revision、Universe Snapshot、RequirementSet、Factor / Alpha 是否 VALIDATED 且 PINNED、Spec Hash 是否一致 |
| DATA | Requirement 是否解析到精确 Manifest，字段、频率、范围、PIT、质量、Gap 与物理文件是否满足 |
| AUTHORIZATION | Grant 是否存在、ACTIVE、未过期，Run Type、Universe、Requirement 与预算是否在 Scope 内 |
| EXECUTION | Run Type、Engine Version、Code Hash、Evaluation / Portfolio / Execution Spec 是否被当前 Worker 支持 |

状态为：

```text
READY / WARNING / BLOCKED / UNKNOWN
```

Overall 采用最严重状态聚合。系统同时返回稳定的 `reason_code` 和 `remediation_code`，使 UI 或 Agent 能知道下一步应该重新编译 Requirement、创建 Backfill、更新授权还是重新生成 Preview。

### 9.1 Preview fingerprint

Preview fingerprint 由四个 closure 共同生成：

```text
Definition Closure
  project_version
  universe_definition / snapshot
  factor definitions
  alpha definitions
  requirement_set_id

Data Resolution Closure
  exact manifest IDs
  resolver version
  source selection policy version

Execution Closure
  evaluation / portfolio / execution spec hash
  engine version
  code hash
  readiness rule version

Authorization Closure
  grant_id
  grant_version
  policy_version
```

因此 Preview 不是“保存过的表单”，而是一次完整输入解析的哈希证明。

## 10. Run 创建：原子冻结，不接受过期 Preview

提交创建 Run 时，服务端不会直接信任之前的 Preview，而是在同一 SQLite 事务中重新执行关键检查：

1. Project revision 是否变化；
2. RequirementSet 是否已被 supersede；
3. Universe Snapshot 是否仍匹配；
4. Project Definition Refs 是否仍是相同的 VALIDATED / PINNED 版本；
5. Manifest 状态与 Manifest Hash 是否变化；
6. Engine / Code identity 是否完整；
7. Grant / Policy Version 是否变化；
8. Grant 是否 ACTIVE、未过期且覆盖 Run Type、RequirementSet 和 Universe；
9. 当前预算是否足够；
10. `idempotency_key` 是否被用于不同的 Preview fingerprint。

通过后，在一个事务内写入：

```text
Budget Reservation
+ Frozen Research Bundle
+ RESEARCH_INPUT_BUNDLE Artifact
+ QUEUED Research Run
+ Outbox Event
```

任意一步失败，整个事务回滚，不会留下“有 Run 没 Bundle”或“扣了预算没 Run”的中间状态。

### 10.1 Frozen Bundle

Frozen Bundle 固定：

- Run Type 与 Input Mode；
- Exact Manifest IDs；
- Universe Snapshot；
- Shared Universe ID / Revision / Resolution 与 Instrument Tuples；
- Factor Definition ID / Version / Spec Hash；
- Alpha Definition ID / Version / Spec Hash；
- RequirementSet；
- Evaluation / Portfolio / Execution Spec Hash；
- Engine / Code / Resolver / Policy Version；
- 历史授权证据；
- Canonical Payload Hash。

Bundle 生命周期固定为 `FROZEN`，但完整性和复用状态可以变化：

```text
integrity_status = UNKNOWN / VERIFIED / DAMAGED
reuse_status = ALLOWED / PROHIBITED
```

Bundle 中保存的 Grant 只说明“当时为什么允许创建这个 Run”。Replay 或新 Run 必须重新检查当前授权，不能继承历史授权。

## 11. Formal Research Worker：只消费已提交的 Bundle

Worker 只领取：

```text
QUEUED
或 Lease 已过期的 RUNNING
```

Run 状态流为：

```text
QUEUED
→ RUNNING
→ SUCCEEDED

失败且未超过 max_attempts：
RUNNING → QUEUED

最终失败：
RUNNING → FAILED
```

Worker 通过 Lease Owner、Lease Expiry 与 Heartbeat 防止多个执行器同时完成同一个 Run。成功后预算从 `RESERVED` 变为 `CONSUMED`；最终失败则释放 Reservation。

### 11.1 当前执行顺序

Formal Executor 的 Definitions 模式按以下顺序执行：

```text
Verify Frozen Bundle
→ 打开并物理校验所有 Manifest
→ 按 Universe Snapshot 限制 Instrument
→ 按 Factor Input 的 Dataset / Field / Frequency 精确绑定数据
→ 执行 Factor Engine v4 或兼容 v3
→ Materialize Factor Artifacts
→ 执行 Alpha Engine
→ Materialize Alpha Artifacts
→ 根据 Run Type 执行：
   ├─ Factor Evaluation
   ├─ Alpha Evaluation
   └─ Portfolio + Research Backtest
→ Materialize Result Artifacts
→ 写入 Metrics 与 Lineage
```

当前 Formal Worker 只执行：

```text
input_mode = DEFINITIONS
```

`PRECOMPUTED_ARTIFACTS` 已进入 Bundle 合同，但 Worker 尚未实现该输入模式。遇到该模式必须明确阻断，不能伪装执行成功。

## 12. Evaluation：检验信号，不等于回测

### 12.1 Future Return 定义

Factor / Alpha Evaluation 使用统一的 Future Return：

```text
信号在 available_time = T 可用
→ 从第一根 bar_start >= T 的未来 Bar Open 建仓
→ 到第 h 根未来 Bar Close 结束
```

形成信号的当前 Close 不会被混入未来收益。

### 12.2 Factor Evaluation

主要输出：

- Coverage / Missing Rate；
- Mean / Std / Quantiles / 5-Sigma Outlier；
- 横截面均值稳定性；
- Lag-1 Correlation；
- Average Rank Turnover；
- Rank IC Mean / Std / Positive Rate；
- Quantile Returns；
- High-Low Spread。

### 12.3 Alpha Evaluation

主要输出：

- Score Distribution；
- 分 Horizon Pearson IC / Spearman Rank IC 的 Count、Mean、Std、ICIR、t-stat 与 Positive Rate；
- Consecutive Rank Stability；
- Top / Bottom Return；
- Long-Short Spread；
- Top-N Membership Turnover；
- 多 Horizon Holding-period Decay；
- Fee + Slippage 后估计收益；
- Bull / Bear / Sideways Regime 表现。

Evaluation 回答的是“信号与未来收益之间是否存在稳定关系”；Backtest 回答的是“把该信号经过组合与执行规则后，账户路径会怎样”。两者不能互相替代。

## 13. Portfolio 与 Research Backtest

### 13.1 PortfolioEngine v2

当前 PortfolioSpec 固定支持：

```text
selection_method = TOP_N
weighting_method = EQUAL_WEIGHT
direction = LONG_ONLY
rebalance_frequency = DAILY / EVERY_SIGNAL
```

附加参数：

```text
top_n
max_position_weight
minimum_score
cash_buffer
universe_snapshot_id
```

`DAILY` 模式会在同一 UTC 日期内保留最后一个可用信号，再生成一次当日目标组合。选中的 Instrument 采用等权；若 `max_position_weight` 限制导致权重和低于可投资比例，剩余部分保留为现金。

每个 Signal timestamp 都产生一个 Portfolio Target。存在合格标的时写入 `INVESTED / TOP_N_SELECTED`；没有 score 达到 `minimum_score` 时写入显式空 weights 的 `FLAT / NO_ELIGIBLE_INSTRUMENT`，从而在下一根可执行 Bar 卖出已有持仓。跳过该 timestamp 或沿用上一期 Target 都是错误语义。

### 13.2 ResearchBacktestProvider v2

当前执行合同为：

```text
signal_generation = BAR_CLOSE
order_submission = NEXT_BAR_OPEN
fill_price = NEXT_OPEN_PLUS_SLIPPAGE
fee_model = FIXED_BPS
slippage_model = FIXED_BPS
missing_price = FAIL_RUN
fractional_quantity = true
exchange_rounding = false
target_equity_reference = EXECUTION_OPEN_PRE_TRADE
sell_before_buy = true
```

实际执行规则：

1. 信号在 `available_time` 后生效；
2. 使用严格晚于 `available_time` 的第一根 Bar Open 执行；
3. 先卖后买；
4. 买入滑点提高成交价，卖出滑点降低成交价；
5. 买卖均按成交额收取 Fee；
6. 目标仓位以执行时开盘、交易前账户权益计算；
7. 现金不足时所有买单按同一比例缩减；
8. 不允许负权重、Short 或 Leverage；
9. 不允许无意负现金。

### 13.3 严格数据对齐

回测要求所有 Instrument 拥有完全一致的 Bar 时间线。发现以下情况会直接失败：

- 重复时间戳；
- 某个 Instrument 缺 Bar；
- 时间线额外多 Bar；
- OHLC 非正数或不满足 High / Low 区间；
- Volume 为负；
- Signal 引用了未知 Instrument；
- 执行价格缺失。

系统不会：

- 静默取时间交集；
- Forward Fill 缺失价格；
- 用当前 Close 代替下一根 Open；
- 自动忽略缺失 Instrument。

### 13.4 当前回测能力边界

Research Backtest 当前适合：

- Crypto Spot；
- 严格对齐的 OHLC Bar；
- 横截面 Alpha；
- Long-only Target Weight；
- 固定 BPS Fee / Slippage；
- 分数持仓。

当前不支持：

- Short；
- Leverage；
- Mixed-source Portfolio；
- Exchange Tick / Step / Min-notional；
- 真实交易所 rounding；
- 订单簿冲击模型；
- 部分成交与撤单；
- 借贷、Funding、Margin；
- 股票交易日历和公司行动会计；
- Polymarket 二元合约专属结算会计；
- Tuple-aware Pair / Multi-leg Portfolio。

虽然 Capability 中保留了 multi-leg / multi-asset 表达能力，但当前 Formal Portfolio 和 Backtest 的核心输入仍是 `instrument_id → target_weight`。不能把它解释为已经完成组合级多腿撮合和 PnL。

### 13.5 回测指标

当前输出：

```text
initial_cash
final_equity
total_return
annualized_return
volatility
sharpe
max_drawdown
fees
slippage_cost
turnover
trade_count
rebalance_count
invested_rebalance_count
flat_rebalance_count
bar_count
instrument_count
average_exposure
average_cash_ratio
```

指标解释需要注意：

- 年化因子根据时间线相邻 Bar 的中位间隔推导，并按 365.25 天年化；
- Sharpe 使用零无风险利率，没有单独的基准或现金收益；
- Turnover 是累计实际成交金额除以初始资金，不是日均换手；
- Final Equity 使用每根 Bar Close 标记持仓；
- 指标只对当前数据窗口、费用、滑点、执行和组合假设有效。

## 14. Artifact 与 Lineage

Formal Run 可能产出：

```text
FACTOR_VALUES
FACTOR_EVALUATION
ALPHA_VALUES
ALPHA_EVALUATION
PORTFOLIO_TARGETS
BACKTEST_ORDERS
POSITION_SERIES
EQUITY_SERIES
DRAWDOWN_SERIES
BACKTEST_RESULT
RESEARCH_INPUT_BUNDLE
```

Artifact 身份包含：

- Spec Hash；
- Engine Version；
- Code Hash；
- 输入 Manifest；
- Universe Snapshot；
- 父 Artifact；
- Created By Run；
- Content Hash 与 Schema Version。

Artifact Materializer 使用内容哈希和幂等写入，避免相同计算因并发而产生不一致结果。`artifact_dependencies` 保存依赖边，`artifact_pins` 防止正式 Run 输入或发布结果失去历史可访问性。内容相同而复用既有 Artifact 时，Run 在 `output.produced_*_artifact_ids` 中保存引用；结果读取必须同时解析该引用和 `created_by_run_id`，不能因为 Artifact 由旧 Run 创建而显示空结果。

因此一个 Backtest Result 可以追溯为：

```text
Backtest Result
← Orders / Portfolio Targets
← Alpha Artifact
← Factor Artifacts
← Factor / Alpha Definitions
← Universe Snapshot
← Dataset Manifests
← Frozen Bundle
← Run / Grant / Policy Evidence
```

## 15. Human API 与 Agent API

### 15.1 本机 Human API

本机 UI 主要使用 `/api/research/*`。关键入口包括：

```text
GET/POST /api/research/projects
GET/POST /api/research/definitions
POST     /api/research/definitions/{id}/validate
GET/POST /api/research/factor-drafts
POST     /api/research/factor-drafts/{id}/previews
POST     /api/research/factor-drafts/{id}/validate
GET      /api/research/data/requirement-sets
POST     /api/research/projects/{id}/requirements/compile
POST     /api/research/manifest-resolver/resolve
POST     /api/research/projects/{id}/run-input-previews
POST     /api/research/runs
GET      /api/research/runs/{run_id}
GET      /api/research/frozen-bundles/{bundle_id}
POST     /api/research/frozen-bundles/{bundle_id}/verify
POST     /api/research/run-worker/run-once
```

Shared Universe 和 Requirement Workspace 另有独立的创建、更新、复制、Binding、Publish、Usage 与 History API。

### 15.2 Agent API

Agent 使用 `/api/agent/research/*`，不能绕过受控入口直接写数据库。每次 Project 写入都需要：

- 全局 `research.*` capability；
- 有效且未暂停的 Research Session；
- Operation 在 allowed_operations 中；
- Provider、Universe、Frequency 与时间范围在 Scope 中；
- 预算充足；
- Session Policy 与内部授权有效；
- 幂等键与审计写入成功。

当前受控 Agent 流程为：

```text
START Research Brief 或 RESUME Anchor
→ Research Session / Context Resolver
→ Project
→ Universe
→ Snapshot
→ Factor / Alpha Definition
→ Validate
→ Pin
→ Requirements
→ Coverage / Preview
→ Frozen Bundle + Run
→ Worker
→ Evaluation / Iteration
```

Agent 只使用 `session_id`，不能增加 Session 额度，也不能将 Project Definition 发布到 Global Library。旧显式 Grant API 只保留兼容。完整说明见 [Research Agent Skill](03-features/research-agent-skill.md)。

## 16. 已验证基线应如何理解

Phase 0B 使用：

```text
BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT / XRPUSDT
5 × 2160 根完整 1h Bar
momentum_20
volatility_20
0.7 × cs_rank(momentum_20) - 0.3 × cs_rank(volatility_20)
Top 2 / Equal Weight / Long Only
每日调仓
下一根 1h Bar Open
fee = 10 bps
slippage = 5 bps
```

验证结果：

| 项目 | 结果 |
| --- | --- |
| Alpha 横截面 | 2140 |
| 实际再平衡 | 89 |
| 出现过的 Top-2 组合 | 19 |
| 成交 | 278 |
| 相同输入确定性重放 | 通过 |
| Manifest / Universe / Factor / Alpha / Portfolio / Backtest Lineage | 通过 |
| 样本总收益 | 约 -18.65% |

这条基线证明：

- 基础横截面 Research 链可以端到端运行；
- 时间语义、成本、订单、现金和谱系可以确定性重放；
- 相同冻结输入不会产生不同结果。

它不等于以下能力已经被同一条 Phase 0B 基线完整覆盖：

- Factor Engine v4 的全部 35 个函数；
- 混合频率 Alignment；
- Polymarket price history；
- Shared Universe 的 Composite / Multi-leg；
- Tuple-aware Portfolio；
- `PRECOMPUTED_ARTIFACTS` Worker；
- 股票和 Polymarket 专属回测会计。

同时，约 `-18.65%` 的样本收益说明该测试的目的确实是验证系统，不是证明策略有效。

## 17. 当前实现的主要优点

### 17.1 定义身份完整

Factor 与 Alpha 不依赖名称查找，Definition ID、Version、Spec Hash、Engine Version 和 Code Hash 都进入正式闭包。

### 17.2 数据不是“路径字符串”

正式 Run 固定 Manifest，并在读取时重新校验物理文件。数据被篡改后不会继续得到看似正常的结果。

### 17.3 Preview 不是审批

Factor Preview、Run Inputs Preview、Grant 与 Formal Run 分工明确，避免把“看过结果”误认为“授权执行”。

### 17.4 预算与 Run 原子提交

预算预留、Bundle、Run 和 Outbox 在一个事务内生成，故障注入不会暴露半成品。

### 17.5 Human 与 Agent 边界在后端执行

Agent 权限不是依赖提示词约束，而是 Capability、Grant、Scope、Budget、Expiry、Idempotency 与 Audit 的后端校验。

## 18. 当前实现的主要结构性缺口

### 18.1 Universe 产品模型领先于执行模型

Shared Universe 已支持 Composite 和 Multi-leg，但 Formal Alpha / Portfolio / Backtest 仍主要消费扁平成员列表。下一步如果要支持配对、篮子或跨源组合，应把 `instrument_tuples` 提升为 Factor、Alpha、Portfolio 和 Accounting 的一级输入。

### 18.2 Factor Authoring 领先于 Alpha Authoring

Factor 已有 Draft、编译、真实 Preview 和 Stale 门禁；Alpha 仍缺少对称的组合 Preview、组件贡献分析和组合依赖可视化。

### 18.3 Requirement 命名容易混淆

“Library Requirement”“Project Requirement Ref”“DataRequirement”“RequirementSet”是四种不同对象。UI 已经尝试隐藏技术细节，但系统文档和 API 调用必须继续明确对象类型，避免把编辑对象当成运行合同。

### 18.4 Formal Worker 只有 Definitions 模式

Bundle Contract 已能表达 Precomputed Artifacts，但当前 Worker 不能执行。这限制了离线预计算、缓存复用与外部计算引擎接入。

### 18.5 Research Backtest 是受限会计模型

当前引擎适合验证 Long-only 横截面研究，不适合直接承载交易所级撮合、Short / Leverage、多腿组合或 Polymarket 结算语义。

### 18.6 自动维护已接通，统一 DAG 仍未完成

Requirement 自动维护器已统一扫描 Library 与 Research，并向 Binance、OpenBB 和 Polymarket 专用 Worker 创建受限任务。尚未完成的是把 Coverage 检查、Provider Backfill、Manifest Commit、Preview 与 Formal Run 合并为一个通用 Research DAG Orchestrator；当前 Provider Worker 与 Formal Research Worker 仍分别执行。

### 18.7 Evaluation 与经济有效性仍需外部判断

系统可以保证结果可复现，但不能保证 Factor 有经济意义。当前真实五标的样本的 Rank IC 与成本后收益都提示信号边际较弱，研究者仍需处理样本外验证、稳健性、过拟合与市场结构变化。

## 19. 建议的后续演进顺序

按当前架构，合理的增量顺序是：

1. **Alpha Authoring 完整化**：增加 Alpha Draft、真实 Preview、组件贡献、相关性与 Stale 门禁。
2. **Tuple-aware Research**：让 Multi-leg Universe 的 tuple 进入 Factor、Alpha、Portfolio 与 PnL，而不是只保存元数据。
3. **Artifact Input Mode**：实现 Formal Worker 的 `PRECOMPUTED_ARTIFACTS`，支持安全缓存与外部引擎产物。
4. **统一 Task DAG**：在现有 Requirement 自动维护器之上，把检查、Provider Backfill、Manifest Commit、Preview 和 Formal Run 接入统一调度。
5. **资产类别会计拆分**：分别实现 Crypto、Equity 与 Polymarket 的交易日历、rounding、费用和结算合同。
6. **结果分析层**：补全 Factor / Alpha Values、分布、Dependencies、Lineage 和 Run Comparison 页面。

这些工作应继续遵循当前系统已经建立的原则：

```text
先定义合同
→ 再暴露 Capability
→ 再实现执行
→ 再加入 UI
→ 最后用真实数据和故障注入验收
```

## 20. 总结

当前 DataTube Research 已经不是一个实验性指标计算页面，而是一个具备以下能力的研究控制面：

- 可共享、可修订、可冻结的 Universe；
- 类型化、支持多 Input 与显式时间对齐的 Factor Engine；
- 固定 Factor 身份的 Alpha 组合；
- 用户 Requirement 到不可变 RequirementSet 的依赖编译；
- Requirement Library 与活跃 Research 的自动扫描、受限历史数据维护和实时进度；
- Catalog、内容寻址数据与可物理验证 Manifest；
- 四维 Readiness 与稳定阻断原因；
- 原子 Frozen Bundle 与 Run 生命周期；
- 受 Lease、预算和授权控制的 Formal Worker；
- 可追溯的 Artifact、Metrics 与 Lineage；
- Human 与 Agent 分离的后端权限边界。

它当前最适合的用途是：在严格冻结的数据和执行假设下，进行可复现的单标的或横截面 Long-only 研究。

它当前还不应被描述为：通用多资产回测平台、真正的 Pair / Multi-leg 执行引擎、交易所级模拟器，或可自动转入实盘的策略系统。

本文仅解析 Research 系统，没有创建或提交 Strategy，也没有执行 Virtual 或 Live Trade。

## 参考实现

- [Shared Universe](../services/data_platform/shared_universe_service.py)
- [Formal Universe Snapshot](../services/data_platform/universe_service.py)
- [Factor Draft](../services/data_platform/factor_draft.py)
- [Factor Preview](../services/data_platform/factor_preview.py)
- [Factor Engine v4](../services/data_platform/factor_engine_v4.py)
- [Factor / Alpha Registry](../services/data_platform/definition_registry.py)
- [Alpha Engine](../services/data_platform/factor_alpha.py)
- [Requirement Workspace](../services/data_platform/requirement_workspace_service.py)
- [Requirement Compiler](../services/data_platform/requirement_compiler.py)
- [Requirement Maintenance](../services/data_platform/requirement_maintenance_service.py)
- [Manifest Resolver](../services/data_platform/manifest_resolver.py)
- [Run Contracts](../services/data_platform/run_contracts.py)
- [Run Preview](../services/data_platform/run_preview_service.py)
- [Run Service / Worker](../services/data_platform/research_run_service.py)
- [Portfolio Engine](../services/data_platform/portfolio.py)
- [Research Backtest](../services/data_platform/research_backtest.py)
- [HTTP API](../app.py)
