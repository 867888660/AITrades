# DataTube Research Run 语义与实现基线

状态：已实现基线，并接入 Research Session 与三类正式 Run（2026-08-04）

> Research 是持续演化的定义网络，Run 是对该网络在某一时刻的确定性解析与冻结。
>
> Frozen Bundle 保存历史事实；能否再次执行，由当前完整性、安全策略与授权状态重新决定。

## 正式链路

```text
Library Definitions
        ↓ PINNED
Project Definition Closure
        ↓
Effective RequirementSet
        ↓
Deterministic Manifest Resolver
        ↓
Run Inputs Preview
        ↓ 单一 SQLite 事务
Budget Reservation + Frozen Bundle + QUEUED Run + Outbox
        ↓ 只领取已提交 Run
Formal Research Worker
        ↓
Produced Artifacts + Metrics + Lineage
```

Research 默认 UI 采用清晰的项目流程，项目内正式页面为：

```text
Overview / Universe / Factor / Alpha / Data / Runs
```

Library 通过各页面的 `Select Existing` 打开，不作为第二套主导航。RequirementSet、Resolved Plan、Preview、Bundle 与技术 ID 只在后台或 `Advanced Details` 中出现。

## 产品 Run 边界

正式 Research Worker 支持三种互相分离的产品结果：

```text
FACTOR_EVALUATION  → Factor Run
ALPHA_EVALUATION   → Alpha Run
RESEARCH_BACKTEST  → Research Backtest
```

- Factor Run 结束于 Coverage、Distribution、IC / Rank IC 与 Quantile Return；
- Alpha Run 从 Signal 构建开始，结束于 IC、Rank IC、Decay、Turnover 与 Regime Analysis；
- Research Backtest 从冻结研究输入开始，负责 Portfolio Targets、Positions、Trades、Costs、Equity、Performance 与 Drawdown。

历史 Alpha Run 若已经包含组合与回测 Artifact，继续以 `LEGACY_HYBRID_RUN` 只读展示；新 Alpha Run 不再生成这些结果。完整 MVP 合同、栏目与迁移规则见 [Factor Run、Alpha Run 与 Research Backtest MVP](factor-alpha-run-mvp.md)。

## Readiness

服务端固定返回五层结果：

```text
DEFINITION / DATA / AUTHORIZATION / EXECUTION / OVERALL
```

状态为 `READY / WARNING / BLOCKED / UNKNOWN`，聚合优先级为：

```text
BLOCKED > UNKNOWN > WARNING > READY
```

未执行的维度必须是 `UNKNOWN`。每项检查返回稳定结构：

```json
{
  "code": "WARMUP_NOT_COVERED",
  "dimension": "DATA",
  "status": "BLOCKED",
  "object_ref": "BTCUSDT:1h",
  "required": {},
  "actual": {},
  "remediation_code": "CREATE_BACKFILL_TASK",
  "message": "..."
}
```

UI、API 与 Agent 只依赖 `code` 和 `remediation_code` 执行导航或补救；自然语言不是控制条件。

## Definition 与 Library

Factor/Alpha 定义是不可变版本，状态为：

```text
DRAFT / VALIDATED / SUPERSEDED / ARCHIVED
```

项目引用只有：

```text
PINNED      固定 definition_id + version，可运行
TRACK_DRAFT 跟随草稿，只能编辑
```

Alpha Component 固定：

```text
factor_definition_id + factor_version + factor_spec_hash
```

`factor_name` 只用于展示。Factor/Alpha 编辑器的算子、参数、Source Preview 与 Formula Contract 来自当前真实引擎能力。

修改采用“创建新版本”，不会覆盖旧定义。Impact API 返回 Project 引用、Alpha 下游、历史 Artifact 与历史 Run；历史结果不被改写。

Research 工作对象与 Library 资产使用不同身份：

```text
RESEARCH  当前 Research 内的工作对象；可以创建、修改和验证
LIBRARY   从已验证工作对象发布的不可变版本；可以跨 Research 复用
```

Research 草稿不会显示在 Library。发布会生成新的不可变 Library 资产，并保留来源关系；
不会把原 Research 工作对象改成 Library 对象。其他 Research 默认引用 Library 的指定版本，
只有执行 `Copy and Edit` 才会创建新的 Research 工作对象。

## Research Session 与内部授权

新 Research Agent 流程不采用逐步人工点击，也不要求用户创建或管理 Grant。用户发出
START 或 RESUME 请求后，系统创建持久化 Research Session，并自动生成固定的研究边界：

```text
research_only
allowed_operations
allowed_providers
allowed_instrument_ids / Universe scope
allowed_intervals
time_start / time_end
Run / download / runtime budgets
```

在该范围内，Agent 可自主创建 Universe/Snapshot、Factor、Alpha、项目内
Pin、RequirementSet、Preview、Run，并调用 Formal Worker。后端在每次写入
时重新校验 Capability、Session 状态、Scope、预算与 Policy。

为兼容既有 Preview → Frozen Bundle → Run 的原子预算与授权闭包，后端仍会创建内部
`approval_grants` 记录。该记录只属于实现层，不通过 Session API 或 AgentMonitor 暴露；
Agent 后续只传 `session_id`。人工保留研究目标、关键歧义判断、额度扩展、全局 Library
发布及 Paper/Live/真实交易审批，并可以从 AgentMonitor 暂停或继续 Session。

旧显式 Grant API 暂时保留兼容，但不是新 Skill 的用户工作流。详细设计见
[Research Agent Skill](research-agent-skill.md)。

## Effective Requirements

后台 Requirements 仍明确拆分：

```text
Derived / Manual / Effective / Coverage
```

Derived 来自固定定义并锁定；Manual 是可编辑补充；运行只消费不可变 Effective RequirementSet。Coverage 不是 Data Ready 的同义词。

## Deterministic Manifest Resolver

Resolver 版本：`deterministic_manifest_resolver.v1`。

选择顺序：

```text
Requirement + Universe Snapshot
→ instrument / data type / frequency
→ Source Policy
→ fields / range / warmup
→ provider / adjustment / PIT / available time
→ quality / gaps
→ READY / physical checksum / Parquet schema
→ versioned deterministic rank
→ exact Manifest IDs
```

“最新 READY Manifest”只允许作为所有语义条件通过后的末级排序因素，不能作为选择规则。

## Run Inputs Preview

Preview 是持久化的动态解析结果，不是正式 Artifact。指纹由四个闭包组成：

```text
definition_closure
data_resolution_closure
execution_closure
authorization_closure
```

覆盖 Project Revision、Universe Definition/Version/Snapshot、Factor/Alpha ID/Version/Spec Hash、RequirementSet、Exact Manifest IDs、Evaluation/Portfolio/Execution Spec Hash、Engine/Code Hash、Session 解析出的内部授权/Policy、Resolver、Source Policy 与 Readiness Rule 版本。

任一相关身份变化，旧 Preview 在 Run 创建时返回 `PREVIEW_STALE`。无关 Library 新版本不会影响已 PINNED 闭包。

## Frozen Bundle

只有 Run Input Bundle 使用 `FROZEN`：

```text
lifecycle_status = FROZEN
integrity_status = VERIFIED | UNKNOWN | DAMAGED
reuse_status     = ALLOWED | PROHIBITED
```

新定义或新 Manifest 不会令历史 Bundle 失去意义。物理损坏会拒绝读取。

Bundle 保存历史授权证据：

```text
grant_id
grant_version
scope_snapshot
policy_version
authorization_check_result
```

这些兼容字段只回答“当时为什么允许创建”。Replay 或新 Run 必须重新检查当前 Session、内部授权、Policy、Run Type、RequirementSet 与 Universe Snapshot Scope；历史授权绝不继承。

Bundle 支持两种输入合同：

```text
DEFINITIONS
  factor_definition_id + version + spec_hash
  alpha_definition_id + version + spec_hash

PRECOMPUTED_ARTIFACTS
  input_factor_artifact_ids
  input_alpha_artifact_ids
```

两者都固定 Exact Manifest、Universe、Requirement、Execution Specs、Engine 与 Code 身份。Run 完成后另行记录 `produced_*_artifact_ids`。

## 原子 Run 创建

正式接口：

```text
POST /api/research/runs
preview_id + preview_fingerprint + idempotency_key
```

在同一 `BEGIN IMMEDIATE` 事务内完成：

```text
Revalidate identities and current authorization
→ Reserve Budget
→ Insert canonical Frozen Bundle v2
→ Insert Bundle Artifact and lineage
→ Create QUEUED Run
→ Write Outbox
```

任一步失败均不留下 Bundle、Run 或预算占用。文件导出、网络下载与 Worker 执行不进入事务。

幂等硬约束：

```text
same idempotency_key + same preview_fingerprint = same Run
same idempotency_key + different fingerprint   = IDEMPOTENCY_KEY_CONFLICT
```

## Worker

Formal Worker：

- 只领取事务提交后的 `QUEUED` Run；
- 使用租约、心跳字段与最大尝试次数；
- 只消费 Bundle 中固定的输入；
- 不查询 Catalog 重新选择 Manifest；
- 不扩大 Universe、时间范围、数据源或 Session Policy；
- Definitions 模式可执行 Factor Evaluation、Alpha Evaluation 与 Research Backtest；
- Factor/Alpha Evaluation 只产出预测性评估；只有 `RESEARCH_BACKTEST` 产出 Portfolio 与 Backtest Artifact；
- 产出 Factor/Alpha/Evaluation/Portfolio/Backtest Artifact、指标与完整 Lineage；
- 最终失败释放预算，成功消费预算。

Binance 在本系统中仍只作为行情数据来源，不产生实盘交易动作。

## API

主要 v2 API：

```text
GET/POST /api/research/definitions
POST     /api/research/definitions/{id}/validate
GET      /api/research/definitions/{id}/impact
GET/PUT  /api/research/projects/{id}/definition-refs
POST     /api/research/manifest-resolver/resolve
GET/POST /api/research/run-input-previews
GET/POST /api/research/runs
GET      /api/research/frozen-bundles/{id}
POST     /api/research/frozen-bundles/{id}/verify
POST     /api/research/run-worker/run-once

GET      /api/agent/research/projects
POST     /api/agent/research/projects
GET/POST /api/agent/research/sessions
GET      /api/agent/research/sessions/{session_id}
GET      /api/agent/research/context
POST     /api/agent/research/sessions/{session_id}/status
POST     /api/agent/research/sessions/{session_id}/continue
POST     /api/agent/research/sessions/{session_id}/need-human
POST     /api/agent/research/sessions/{session_id}/answer
POST     /api/agent/research/sessions/{session_id}/iterations
POST     /api/agent/research/iterations/{iteration_id}/complete
POST     /api/agent/research/projects/{id}/universes
POST     /api/agent/research/projects/{id}/definitions
POST     /api/agent/research/projects/{id}/requirement-sets
POST     /api/agent/research/projects/{id}/backfill-tasks
POST     /api/agent/research/projects/{id}/run-input-previews
POST     /api/agent/research/projects/{id}/runs
POST     /api/agent/research/projects/{id}/run-worker/run-once
```

旧 Resolved Plan 与 Input Bundle v1 API 仅保留兼容，不再是正式 UI 主链路。

## 基线完成范围

| 能力 | 状态 | 边界 |
|---|---|---|
| Global/Project Library 与 Project Definition Ref | 已完成 V1 | Agent 只创建 `PROJECT` 对象；`GLOBAL` 发布人工审批 |
| Effective RequirementSet、Library 与五层 Readiness | 已完成 V3 | Library 保留跨项目版本族；编辑重新编译新版本；后端自动维护三类历史数据合同并提供实时进度 |
| Deterministic Manifest Resolver | 已完成 V1 | Worker 运行时不得重新选 Manifest |
| Preview 指纹与 STALE 判定 | 已完成 V1 | Definition/Data/Execution/Authorization closure 任一变化即失效 |
| Run 原子创建、幂等与 Outbox | 已完成 V1 | Human UI 或持有有效 Research Session 的 Agent 可创建 |
| Research Session、Context Resolver 与 Agent 写 API | 已完成 V1 | START/RESUME、固定额度、逐次后端校验；暂停、越界和预算不足稳定阻断 |
| Research Iteration 与 Invalidation Plan | 已完成 V1 | Hypothesis + Intervention Set；KEEP 仅移动 Current Branch Head |
| Frozen Bundle 完整性与授权证据 | 已完成 V1 | 重放必须重新检查当前复用授权 |
| Formal Research Worker | 已完成 V1 | 执行 Definitions 输入模式；预计算 Artifact 输入模式尚未开放 |
| Factor / Alpha / Research Backtest 结果合同 | 已完成 V2 | 预测性评估与组合回测分离；旧 Alpha Hybrid 只读兼容；复用 Artifact 仍可由 Produced Artifact IDs 解析 |
| 简化 Research UI | 已完成 V4 | 英文 Overview/Universe/Factor/Alpha/Data/Strategy/Runs；Data/Library 展示自动维护与 Live Download；无手动补数按钮 |

## 验收证据

- 61 个单元测试通过；
- 14 个集成测试通过；
- 1 个 Manifest 不可变故障注入测试通过；
- 新增原子故障用例证明 Bundle Artifact 阶段失败时预算、Bundle、Run 全部回滚；
- 新增端到端用例证明 Preview → Frozen Bundle → QUEUED → Worker → Artifact → SUCCEEDED；
- 全局导航不再在中等宽度折叠为不可识别的圆点；窄屏使用可横向滚动的文字导航；
- Overview / Universe / Factor / Alpha / Data / Runs 可自由切换；
- Header 不再显示前端推断的 `Overall UNKNOWN`；Overall 只来自服务器 Preview 聚合；
- JavaScript 语法检查通过，UI/API 合约测试覆盖导航和 Formula Capability Schema。

## 安全边界

- Research Run 不等于 Strategy 上线；
- 不创建、不提交、不执行任何实盘交易；
- Agent 不得自批、扩权、提高 Session 额度或绕过当前授权检查；
- 数据缺口只能通过独立、受控、可审计 Backfill Task 补齐；
- 支持的历史数据缺口由后端 Requirement 维护器自动创建任务；页面轮询只读状态，不负责触发任务；
- 所有写接口继续受本机访问限制与服务端校验保护。
