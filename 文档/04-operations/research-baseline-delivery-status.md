# Research Run 基线交付状态

检查日期：2026-08-04

## 结论

用户冻结的 Research Run 语义基线已经完成，可以作为当前设计与实现基线继续迭代。受控 Agent 写 API 已接入 Research Session：用户通过 START 或 RESUME 表达目标，服务端自动创建固定研究边界，Agent 在范围内完成正式研究对象与 Run；用户无需管理 Grant，越界、额度扩展、全局发布和实盘仍需人工判断或审批。

## 已交付

| 交付项 | 状态 | 验收要点 |
|---|---|---|
| Universe / Factor / Alpha Library | 完成 V2 | 业务摘要、成员/组件、版本化、验证、影响查询；技术 ID 收入 Advanced |
| Project Definition Closure | 完成 V1 | `PINNED / TRACK_DRAFT`，运行只接受固定版本 |
| Requirements | 完成 V1 | Derived / Manual / Effective / Coverage，Resolved Plan 作为高级详情 |
| RequirementSet Library | 完成 V2 | 跨项目版本族、版本切换、完整设置、Coverage、来源/语义/使用关系；编辑生成不可变新版本 |
| Readiness | 完成 V1 | Definition / Data / Authorization / Execution / Overall 与稳定原因码 |
| Manifest Resolver | 完成 V1 | 按 Requirement、Universe、Source Policy 和 Resolver Version 确定性解析 |
| Run Inputs Preview | 完成 V1 | 完整 closure 指纹；相关版本变化后标记 `STALE` |
| Run 创建 | 完成 V1 | 重新校验、预算预留、Bundle、QUEUED Run、Outbox 单事务提交；失败全回滚 |
| Frozen Bundle | 完成 V1 | 不可变、可验证、保存历史授权证据；重放重新检查当前授权 |
| Worker | 完成 V1 | 只领取已提交 Run，只消费 Bundle 固定输入，Definitions 模式产出 Artifact/Metrics/Lineage |
| Factor Run 结果合同 | 完成 V1 | Definition、Universe、Data、Factor Output、Coverage、Distribution、IC/Rank IC、Quantile Return、Diagnostics、Logs |
| Alpha Run 结果合同 | 完成 V2 | Signal、IC/Rank IC、Decay、Turnover、Regime；不再承载 Portfolio 与 Backtest |
| Research Backtest 结果合同 | 完成 V1 | Alpha Lineage、Target、Position、Trade、Cost、Equity、Performance、Drawdown；显式 FLAT 退出 |
| 历史 Alpha Hybrid 兼容 | 完成 V1 | 不改写历史 Artifact；旧组合回测结果以 `LEGACY_HYBRID_RUN` 只读展示 |
| Research UI | 完成 V4 | 一级导航 Research / Library / Runs / Data Catalog / Agent Monitor / Approvals / Settings；单个 Research 使用 Overview / Universe / Factor / Alpha / Data / Strategy / Runs |
| Formula Contract | 完成 V2 | 后端 Capability Schema 提供 Features、Operators/Functions、参数范围、Warmup、PIT 与 Requirement Contract |
| Factor / Alpha Editor | 完成 V3 | Research 工作卡片支持 Save Draft / Validate / Publish to Library；Library 引用只读，Copy and Edit 后才能修改 |
| Run Inputs / Runs UI | 完成 V2 | 普通表单与 Advanced Source Preview 分离；Preview/Frozen Bundle 分离；Run 类型和固定输入清晰展示 |
| Research Session / Context Resolver | 完成 V1 | START/RESUME、七类锚点、Research Brief、固定额度、暂停/继续、低频 NEED_HUMAN |
| Research Iteration | 完成 V1 | Hypothesis + Intervention Set、Invalidation Plan、KEEP/REJECT、Original Baseline / Current Branch Head |
| Research Agent 写 API | 完成 V2 | 正式 Project/Definition/Requirement/Preview/Run 对象；Agent 传 `session_id`，逐次 Scope 与 Capability 校验 |
| 内部授权兼容层 | 完成 V1 | 保留既有预算预留与原子 Run 语义；`internal_grant_id` 不向用户和 AgentMonitor 暴露 |
| AgentMonitor Research Sessions | 完成 V1 | 状态、额度、基线、迭代、NEED_HUMAN、暂停/继续/回答 |
| Research / Library 身份边界 | 完成 V2 | 发布生成独立不可变 Library 资产，不改变 Research 源对象；引用固定指定 Library 版本 |
| Requirement 自动维护 | 完成 V1 | 每 30 秒扫描 Library 与活跃 Research；三类 Provider 任务自动创建、幂等、受预算约束，终态错误可见 |
| Research / Library 实时进度 | 完成 V1 | Live Download 展示阶段、分区、行数、当前范围、ETA 与运行时间；Data 状态页无手动补数按钮 |

## 2026-07-16 验收记录

```text
JavaScript syntax check       PASS
Python compileall             PASS
Unit tests                    61 / 61 PASS
Integration tests             14 / 14 PASS
Atomic rollback injection      1 / 1 PASS
GET /api/health               ok = true
GET /api/agent/capabilities   PASS
GET /api/research/engine-capabilities PASS
GET /api/research/runs        PASS
Local listener                127.0.0.1:5001
```

## 2026-08-01 Research Agent 验收记录

```text
Research Session/Iteration tests       8 / 8 PASS
Legacy Research Agent authorization    PASS
Control Plane/Run/Workspace regression 29 / 29 PASS
DataTube Skill quick validation        PASS（workspace + publish copy）
AgentMonitor JavaScript syntax         PASS
Related Python compile                 PASS
```

## 2026-08-04 Requirement 自动维护验收记录

```text
Research Workspace test runner        PASS
Unit tests                       218 / 218 PASS
Integration tests                 21 / 21 PASS
Failure injection                  1 / 1 PASS
Online GET-only checks                  PASS
Library unreferenced scan              PASS
System maintenance without user Grant  PASS
Research / Library live progress        PASS
Manual status-page download actions     REMOVED
```

## 2026-08-04 Factor / Alpha / Research Backtest 验收记录

```text
Product boundaries                       PASS
Alpha Pearson IC / Spearman Rank IC       PASS
Explicit FLAT target and next-bar exit    PASS
Cached Artifact result resolution         PASS
Four-asset historical replay              PASS
Unit tests                          215 / 215 PASS
Integration tests                    19 / 19 PASS
Failure injection                     1 / 1 PASS
JavaScript syntax                           PASS
Research result UI                          PASS
```

受控 Research Session `research_session_5d4e0194cee84d3f8c5089b4a02908c6` 的最终历史 Hybrid Run 为 `run_93cf2909364941ba86b866a4e525ff5b`。它使用 BTCUSDT、ETHUSDT、BNBUSDT、SOLUSDT 1h 数据，验证了 8,544 Signals/Targets、778 个可执行 FLAT rebalances、485 笔 Trades、费用与滑点对账以及 IC/Rank IC 展示。该结果只接受技术链路；因成本后收益为负且预测边际弱，不进入 Strategy。完整证据见 [Factor Run、Alpha Run 与 Research Backtest MVP](../03-features/factor-alpha-run-mvp.md#2026-08-04-多资产技术验收)。

核心自动化命令：

```powershell
node --check static/research_workspace.js
.\.venv\Scripts\python.exe -m compileall -q app.py services\data_platform
.\.venv\Scripts\python.exe -m unittest discover -s tests\unit
.\.venv\Scripts\python.exe -m unittest discover -s tests\integration
.\.venv\Scripts\python.exe -m unittest discover -s tests\failure_injection
```

## 明确不在本次基线内

- 统一 Research Task DAG Orchestrator，以及与现有 Agent Run 审计模型的完整绑定；
- 通用 `CHECK_DATA → BACKFILL → COMMIT_MANIFEST → PREVIEW → RUN` Task DAG Orchestrator；现有 Requirement 维护器已覆盖三类历史数据 Provider Task，但不是通用 DAG；
- `PRECOMPUTED_ARTIFACTS` 输入模式的 Formal Worker 执行；
- FRED Point-in-time、精确股票交易日历、完整行业/风格中性化、多市场交易规格和复杂交互式 Lineage 图。

这些项目是后续增量，不影响本次冻结基线的确定性 Run 闭环，但不得在 UI、API 或文档中提前标记为可用。

## 安全与操作说明

- 本次检查和文档更新没有创建、提交或批准策略，也没有触发虚拟或实盘交易；
- Binance、OpenBB 与 Polymarket 仍是受控数据来源；数据缺口由后端自动创建独立、可审计的系统维护任务补齐，Run 本身不联网补数；
- Frozen Bundle 中的历史授权证据只解释当时为何允许创建 Run，不能替代 Replay 或新 Run 的当前授权检查；
- Research Agent 写接口已开放，但必须同时通过全局 `research.*` capability 与当前 Research Session；Agent 无权增加 Session 额度。
- 人类可在 AgentMonitor 暂停/继续 Research Session；暂停不改写历史 Bundle、Artifact 或 Run。
- `/research` 默认不显示 Overall、Readiness 原因码、Preview、Bundle 或技术 ID；用户看到 Ready、Queued、Preparing、Checking、Failed、Unavailable 和 Live Download，服务器仍执行完整检查。
- 修改 `definition_registry.py` 后需重启本地 Flask 进程，前端才会获得最新 Formula Contract；静态 CSS/JS 刷新浏览器即可加载。

关联文档：

- [Research Run 语义与实现基线](../03-features/research-run-semantics.md)
- [Factor Run、Alpha Run 与 Research Backtest MVP](../03-features/factor-alpha-run-mvp.md)
- [Research Agent Skill](../03-features/research-agent-skill.md)
- [DataTube Quant Research V1 当前实现](../03-features/quant-research-platform-v1.md)
- [系统概览](../01-overview/system-overview.md)
- [Agent 能力接口设计](../03-features/agent-interface-design.md)
- [Research 工作台操作说明](./research-workspace-operation-guide.md)
- [Requirement 数据自动维护与实时进度](./requirement-data-maintenance.md)
