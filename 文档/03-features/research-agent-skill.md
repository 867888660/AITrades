# Research Agent Skill：START、RESUME 与迭代研究

更新日期：2026-08-14

本文描述 DataTube 已落地的 Research Agent Skill、Research Session 控制面和
AgentMonitor 监控方式。目标是让用户只需要表达研究意图，Agent 负责构建或恢复研究，
并在可解释、可回溯的实验循环中持续改进。

## 0. 2026-08-13：Alignment 与实验研究边界

Research Agent 的正式入口已调整为：

```text
Research Goal
  → Research Alignment
  → AlignedResearchIntent v1
  → Research Contract v2
  → Universe / Factor / Alpha / Portfolio Evidence Experiment
  → ResearchResult
  → KEEP / REJECT / INCONCLUSIVE
  → Learning
```

Alignment 只确定 `QUESTION / STOP_AT / BASE / SCOPE / EVIDENCE`，不创建
Project、Session、Factor、Alpha 或执行对象。当前高层 researcher facade 已分别开放
`UNIVERSE`、`FACTOR`、`ALPHA` 与 `PORTFOLIO_EVIDENCE`。Universe 只产生研究池证据；
Portfolio Evidence 使用正式 `RESEARCH_BACKTEST`，并停止在 Strategy 创建之前。
Alpha Candidate 可显式组合多个 Factor，不再退化为单因子近似。

### 0.1 Universe v2 能力解释

Researcher 看到的 Universe 产品模型已经收敛为：

- `STATIC`：明确 Instrument 列表；
- `DYNAMIC`：固定的 Base、Filter、Rank、Select、Rebalance 五阶段；
- `COMPOSITE`：Universe 间的 Union、Intersection、Difference。

Capability 中的 `field_registry` 是定义/编译能力，不代表对应字段已经接入正式
Research Run。`field_execution_status`、`dynamic_point_in_time_filters` 与
`selection_methods` 才描述当前 Formal Pipeline。当前正式选择仍为
`ALL_ELIGIBLE`，当前正式动态字段为 `market_cap_usd`、`roe_ttm`、
`pe_ttm`、`pb_mrq`、`fcf_yield_ttm`；比例阈值使用小数，且必要输入缺失时排除；
`TOP_N / BOTTOM_N / PERCENTILE / Buffer` 已完成 v2 authoring 和纯 PIT
Membership Engine，但还不是正式 `UNIVERSE_DESIGN` 结果。

Standalone Universe Design 不能冻结所需 Manifest。动态字段 Candidate 必须返回
`DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION`；Shared Preview 返回
`REQUIRES_FROZEN_DATA` 只说明规则已编译，不能解释成动态成员已经解析。
详细合同见 [Universe v2 设计](universe-v2-design.md)。

正式高层接口为：

```http
POST /api/agent/researcher/align
POST /api/agent/researcher/start
POST /api/agent/researcher/resume
GET  /api/agent/researcher/sessions
GET  /api/agent/researcher/sessions/{session_id}
POST /api/agent/researcher/sessions/{session_id}/status
POST /api/agent/researcher/sessions/{session_id}/continue
POST /api/agent/researcher/sessions/{session_id}/need-human
POST /api/agent/researcher/sessions/{session_id}/answer
POST /api/agent/researcher/sessions/{session_id}/experiments
GET  /api/agent/researcher/experiments/{experiment_id}
POST /api/agent/researcher/experiments/{experiment_id}/decide
```

旧 `/api/agent/research/*` Session/Iteration 接口进入正式弃用期，响应包含
`Deprecation`、`Sunset` 与 successor `Link` 标头；低层 Project 工程接口继续用于
Research Workspace，不属于 Researcher Skill。AgentMonitor 的 Research Sessions 页面只使用 researcher facade，默认
只展示研究计划、假设、证据、Decision 和 Learning，不展示内部执行 IR。

## 1. 面向用户的模型

用户只需要两种入口：

```text
START：从一个自然语言目标开始新研究
RESUME：从已有研究对象继续修改和试验
```

例如：

```text
帮我研究 BTC 趋势策略。
```

Agent 会先形成 Research Brief，再创建 Research Session。用户不需要创建 Grant、
选择每一个内部对象或逐次批准回测。

```text
用户目标
  → Research Brief
  → Research Session
  → Project / Universe / Factor / Alpha
  → Requirements / Data / Preview / Bundle / Run
  → Evaluation
  → Hypothesis + Intervention Set
  → KEEP / REJECT / INCONCLUSIVE
```

设计原则：

- **简单操作**：用户描述目标即可开始。
- **可监控**：AgentMonitor 展示当前阶段、假设、基线、额度和问题。
- **易懂**：每轮实验解释“为什么改、改了什么、结果如何”。
- **低打扰**：常规研究选择由 Agent 决定，只有关键歧义才询问用户。
- **研究隔离**：Session 只能进行研究、数据准备、预览和历史回测，不能进入实盘。

## 2. START

START 接受完整或不完整的 Research Brief。最小输入只有 `objective`：

```json
{
  "entry_mode": "START",
  "objective": "建立 BTC 趋势跟随策略"
}
```

系统会补齐可安全推断的默认值：

```yaml
objective: 建立 BTC 趋势跟随策略
instrument_scope: BTCUSDT spot
provider: BINANCE
frequency: 1h
research_period:
  start: 2021-01-01
  end: 当前日期
evaluation_metrics:
  - annualized_return
  - max_drawdown
  - sharpe_ratio
  - turnover
  - cost_adjusted_return
constraints:
  long_only: true
  leverage: false
  max_turnover: null
benchmark: buy_and_hold
iteration_budget:
  max_runs: 10
  max_runtime_minutes: 30
```

这些默认值不是永久策略配置。它们只是当前 Research Session 的研究边界，后续所有
Definition、Requirement、Preview 和 Run 仍保留独立版本与不可变身份。

## 3. RESUME 与 Context Resolver

RESUME 支持以下锚点：

```text
SESSION
PROJECT
RUN
PREVIEW
BUNDLE
FACTOR_DEFINITION
ALPHA_DEFINITION
```

调用 RESUME 时，系统不会只读取锚点 ID，而是由 Context Resolver 恢复研究上下文图：

```text
Project
├── Universe Snapshot
├── Factor Definitions
├── Alpha Definitions
├── Requirement Set
├── Recent Previews
├── Frozen Bundle / Baseline Run
├── Recent Runs
├── Artifacts
└── Experiment History（从 Session 恢复时）
```

如果一个 Factor 或 Alpha 被多个 Project 引用，Resolver 返回 `AMBIGUOUS`，Session
进入 `NEED_HUMAN`，由用户选择要继续的 Project。选择后系统重新解析上下文并继续，
不会丢失对话之外的任务状态。

## 4. Original Baseline 与 Current Branch Head

Session 始终分别保存：

```text
original_baseline_run_id
current_branch_head_run_id
```

- `original_baseline_run_id`：进入 RESUME 时指定或解析到的原始 Run。
- `current_branch_head_run_id`：当前研究分支最近一次 `KEEP` 的候选 Run。

示例：

```text
run_100  Original Baseline
├── run_101  REJECT
└── run_102  KEEP
    ├── run_103  KEEP  ← Current Branch Head
    └── run_104  REJECT
```

`REJECT` 不移动分支头，`KEEP` 只移动 Current Branch Head，永远不会改写 Original
Baseline。这避免多轮实验后“基线”含义发生漂移。

## 5. Research Iteration

每轮实验的最小解释单位是：

```text
Hypothesis + Intervention Set
```

不是机械限制只能改一个字段，而是每轮只验证一个主要假设。例如：

```yaml
hypothesis:
  statement: 降低组合集中度可以改善尾部风险
intervention_set:
  - top_k: 2 -> 5
  - max_position_weight: 0.5 -> 0.2
controlled_variables:
  - universe
  - factor
  - rebalance_frequency
  - transaction_cost
```

这里同时修改两个参数是合理的，因为它们共同实现“降低集中度”这一项干预。Factor
窗口、调仓频率、交易成本和 Universe 同时改变则属于无法解释的混合实验。

Iteration 保存：

- Control Run 与 Candidate Run。
- 假设、证据和 Intervention Set。
- Controlled Variables。
- Change Set 与 Invalidation Plan。
- 修改前后指标与比较结果。
- `KEEP / REJECT / INCONCLUSIVE / NEED_HUMAN` 决策。
- 过拟合、样本不足、换手不稳定等 warnings。

## 6. Invalidation Plan

研究依赖关系为：

```text
Universe Definition
  → Universe Snapshot
  → Factor Definitions
  → Alpha Definition
  → Requirement Set
  → Prepared Data
  → Preview
  → Frozen Input Bundle
  → Run
  → Metrics / Artifacts
```

每次修改计算最早失效节点，只重新执行必要下游：

| 修改内容 | 最早重新执行点 |
|---|---|
| 解释文字或显示方式 | 不产生新 Run |
| 交易成本、调仓频率 | Preview |
| 回测日期、Provider | Requirements / Data Readiness |
| Alpha 权重 | Alpha |
| Factor Formula | Factor Validation |
| Universe 规则 | Universe Snapshot |

Requirements 可以重新编译，但 Prepared Data 只要仍覆盖新 Requirement Set 就可以复用。

## 7. Session 状态机

当前状态：

```text
BRIEFING
PLANNING
BUILDING
PREPARING_DATA
PREVIEWING
RUNNING
EVALUATING
ITERATING
NEED_HUMAN
PAUSED
BLOCKED
COMPLETED
FAILED
CANCELLED
```

Agent 在每个重要阶段前更新 Session 状态。Session、Iteration 和事件流水均持久化，
因此浏览器关闭、聊天中断或 Agent 进程重启后仍可恢复。

### NEED_HUMAN

只有以下稳定原因可以暂停询问用户：

```text
AMBIGUOUS_INTENT
AMBIGUOUS_CONTEXT
MATERIAL_SCOPE_CHANGE
LIMIT_EXTENSION_REQUIRED
CROSS_RESEARCH_BOUNDARY
```

不应询问：

- 是否采用一个常规、可逆的研究默认值。
- 是否运行下一次仍在额度内的回测。
- 是否确认 Agent 自己刚提出的普通实验。
- 每一个 Factor 参数或展示选项。

问题必须简短说明“哪个决定无法安全推断”，并能由用户一次回答解决。

## 8. 研究额度与授权兼容层

新用户流程不包含 `WAITING_GRANT`。用户发出 START 或 RESUME 请求，即表示允许系统在
研究边界内工作。

服务端自动生成固定 Session Policy：

```text
research_only = true
max_runs = 10
max_runtime_seconds = 1800
max_download_bytes = 5 GiB
```

禁止操作：

```text
GLOBAL_PUBLISH
LIVE_STRATEGY_CREATE
LIVE_TRADING
HISTORY_DELETE
```

正式 Preview、Frozen Bundle 与 Run 原有实现仍使用内部 `approval_grants` 和预算预留
保证原子性。Research Session Service 会在服务端生成兼容授权并保存
`internal_grant_id`，但该字段不会通过 Session API 或 AgentMonitor 暴露。Agent 后续只传
`session_id`，服务端负责解析内部授权。

旧的显式 Grant API 暂时保留，供已有调用和回归测试兼容；它不再是新 Skill 的用户流程。

## 9. API

### Session 与上下文

```http
GET  /api/agent/research/sessions
POST /api/agent/research/sessions
GET  /api/agent/research/sessions/{session_id}
GET  /api/agent/research/context?anchor_type=RUN&anchor_id=run_123
```

### 状态与用户交互

```http
POST /api/agent/research/sessions/{session_id}/status
POST /api/agent/research/sessions/{session_id}/continue
POST /api/agent/research/sessions/{session_id}/need-human
POST /api/agent/research/sessions/{session_id}/answer
```

### Iteration

```http
POST /api/agent/research/sessions/{session_id}/iterations
POST /api/agent/research/iterations/{iteration_id}/complete
```

### START 示例

```http
POST /api/agent/research/sessions
Content-Type: application/json

{
  "entry_mode": "START",
  "objective": "建立 BTC 趋势跟随策略",
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant"
}
```

### RESUME 示例

```http
POST /api/agent/research/sessions
Content-Type: application/json

{
  "entry_mode": "RESUME",
  "anchor_type": "RUN",
  "anchor_id": "run_123",
  "objective": "尝试降低最大回撤",
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant"
}
```

后续 Project 写请求加入：

```json
{
  "session_id": "research_session_123",
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant"
}
```

不需要传 `grant_id`。

## 10. CLI

```powershell
python scripts/datatube_client.py researcher-align --data alignment.json
python scripts/datatube_client.py researcher-start --data start.json
python scripts/datatube_client.py researcher-resume RUN run_123 --data alignment.json
python scripts/datatube_client.py researcher-status <session_id>
python scripts/datatube_client.py researcher-experiment <session_id> --data candidate.json
python scripts/datatube_client.py researcher-result <experiment_id>
python scripts/datatube_client.py researcher-decide <experiment_id> --data decision.json
python scripts/datatube_client.py researcher-pause <session_id>
python scripts/datatube_client.py researcher-continue <session_id>
```

## 11. AgentMonitor

访问：

```text
http://127.0.0.1:5001/agent-monitor
```

`Research Sessions` 页签显示：

- 当前目标、START/RESUME 模式与状态。
- Project、Session 和更新时间。
- 已使用 Run 数与 Session 上限。
- Original Baseline 与 Current Branch Head。
- 每轮 Hypothesis、Control Run、Candidate Run 和决策。
- `NEED_HUMAN` 的具体问题。
- 暂停、继续和回答操作。

## 12. 持久化对象

Migration 21 新增：

```text
research_agent_sessions
research_agent_iterations
research_agent_session_events
```

重要实现文件：

| 文件 | 职责 |
|---|---|
| `services/data_platform/research_agent_session.py` | Brief、Session、状态、Iteration、额度兼容层 |
| `services/data_platform/research_context_resolver.py` | RESUME 锚点解析与上下文图恢复 |
| `services/data_platform/store.py` | Migration 21 |
| `app.py` | Session、Context、Iteration API 与 session_id 授权解析 |
| `templates/agent_monitor.html` | Research Sessions 监控页签 |
| `static/agent_monitor.js` | Session 列表、详情、暂停、继续和回答交互 |
| `.agents/skills/datatube/` | Workspace Skill 入口、工作流和 CLI |
| `skills/datatube/` | 可发布 Skill 副本 |

## 13. 当前实现边界

已经完成：

- START/RESUME 高层接口。
- Research Brief 默认化。
- Context Resolver。
- Session、Iteration、事件持久化。
- Original Baseline / Current Branch Head。
- Invalidation Plan 基础路由。
- 固定研究额度及 `session_id` 到内部授权的映射。
- AgentMonitor Research Sessions 页面。
- DataTube Skill 与 CLI 命令。
- Universe v2 三类型 Schema、Field Registry、Compiler、PIT Membership Engine、
  Shared Dynamic Preview 与 Capability。

高层 Experiment 后端现在自动编译并推进 Universe、Factor、多 Factor Alpha、数据准备、
正式 Run 与 Research Backtest；Skill 只负责假设、Candidate、结果解释和下一轮选择。
内部 Requirement、Manifest、Preview、Bundle、Provider Task 与 Worker 不暴露给研究员。

Universe v2 的正式 `UNIVERSE_EVALUATION`、动态 Shared Binding、Composite
Membership Timeline 尚未接通。在这些对象不存在时，Researcher 不得把
`AUTHORED / COMPILED / REQUIRES_FROZEN_DATA` 描述成
`FROZEN_EVALUATED / BOUND`。

## 14. 安全边界

- Research Run 不等于 Strategy 创建或上线。
- Research Session 不允许创建 Virtual、Paper、Live 或 Real 策略。
- Agent 不能增加 Session 额度、扩大 Provider/Universe/日期范围或发布 Global Library。
- validated Definition 不覆盖，修改必须产生新版本。
- Manifest、Artifact、Bundle、Run、审计和 Lineage 不允许由 Agent 删除。
- Point-in-time、available-time、质量、范围和当前 Bundle 复用授权必须继续校验。

Research-only 任务结束时应明确：

```text
没有创建或提交策略，也没有执行 Virtual 或 Live 交易。
```
