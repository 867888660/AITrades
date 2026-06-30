# EventGraph Agent SOP 优化设计

更新日期：2026-06-29

> 状态：已被 2026-06-30 的外部 Agent 写入架构取代。新方向取消内置 EventGraph agent，改为外部 agent 通过受控 API 读取 EventGraph、提交 change request / patch，经系统校验和人工或受信规则审核后写入正式图谱。新设计见 [EventGraph 外部 Agent 写入架构设计](./eventgraph-external-agent-write-design.md)。

本文基于当前 `derived_preview` 版 EventGraph、新闻 observation 库、AgentMonitor 和 `agent_runs / agent_run_steps / agent_audit_events` 调用链，设计下一阶段 EventGraph 的目标形态：让系统始终能回答“谁在做什么、为什么做、做到了哪一步、产生了什么提案、谁审核后写入正式图谱”。

核心目标：

```text
系统发现异常
  -> 内置 Agent 调查 Candidate
  -> 生成 Report / Proposal
  -> 人类审核
  -> 写入正式 EventGraph
  -> 外接 Agent 和策略系统只使用可追溯知识
```

## 1. 当前状态判断

当前实现已经有几个很好的基础：

- `services/event_news_service.py` 已经把新闻写入 `Data/EventGraph.db`，有 `event_graph_observations`、`event_graph_events`、`event_graph_event_observations`、`event_graph_refresh_runs`。
- `services/event_graph_service.py` 可以从 Polymarket 市场、新闻 derived event 和关键词资产规则即时生成 Event / Finance / Signal 图谱。
- `services/agent_interface_service.py` 已有 `agent_runs`、`agent_run_steps`、`agent_audit_events`，并且 `/api/agent/*` 成功或失败调用都能进入审计链。
- `templates/agent_monitor.html` 与 `static/agent_monitor.js` 已经把 AgentMonitor 拆成总览、内置 Agent、外接 Agent、审计日志四个视图。

但当前仍有几个结构性缺口：

- EventGraph 返回的 `source` 仍是 `derived_preview`，Polymarket 与新闻事件多为即时派生，不能等同正式事实库。
- `SIGNAL` 仍作为独立节点显示，容易把指标、异常和事实节点混在一起。
- Agent Run 现在主要是 API 调用日志，缺少业务级 `outcome`、Candidate / Proposal / Review 关联和固定 SOP step 语义。
- 内置 Agent 没有独立任务队列，只能在 AgentMonitor 里从 audit 记录中筛出“看起来像内置调查”的活动。
- 外接 Agent 与内置 Agent 的边界需要更硬：外接 Agent 可以查询和提交变更请求，但不能直接调用内置 Agent 或修改正式图谱。

## 2. 目标架构

EventGraph 不是新闻库、扫描器或策略执行器，而是可追溯知识层。

数据主线固定为：

```text
Observation
  -> Anomaly
  -> Candidate
  -> Investigation
  -> Proposal
  -> Human Review
  -> Graph Core
```

每一层只承担自己的职责：

| 层级 | 含义 | 谁产生 | 是否正式图谱 |
| --- | --- | --- | --- |
| Observation | 系统看到的原始事实片段，例如新闻、市场快照、公告 | 确定性采集程序 | 否 |
| Anomaly | 量化异常，例如概率跳变、成交量异常、价格冲击 | 确定性程序 | 否 |
| Candidate | 值得调查的候选任务 | 确定性匹配程序 | 否 |
| Investigation | 内置 Agent 调查过程和报告 | 内置 Agent | 否 |
| Proposal | Event / Edge / Finance / Merge / Archive 变更提案 | 内置 Agent 或受控外接 Agent | 否 |
| Human Review | 人类批准、拒绝、要求修改或带修改批准 | 人类 | 否 |
| Graph Core | 正式 Event、Finance、Edge、EventExpression | Review apply 程序 | 是 |

禁止路径：

```text
新闻标题
  -> 直接写正式 Event

Polymarket 异常
  -> 直接写正式 Edge

外接 Agent 研究结论
  -> 直接覆盖 Graph Core
```

允许路径：

```text
Observation / Anomaly
  -> Candidate
  -> 内置 Agent Run
  -> Proposal
  -> Human Review
  -> Graph Core 新版本
```

## 3. 正式图谱对象

正式图谱只保留四类核心对象。

### 3.1 Event

Event 表示可以定义、跟踪、判断状态的现实事件。

关键字段：

```text
event_id
title
event_type = ATOMIC | COMPOSITE
lifecycle = EMERGING | ACTIVE | STABILIZING | RESOLVED | ARCHIVED
verification_status = HUMAN_VERIFIED | DISPUTED | OUTDATED
time_window_start
time_window_end
definition
origin_run_id
origin_proposal_id
current_version
created_at_utc
updated_at_utc
```

`lifecycle` 描述事件发展阶段，`verification_status` 描述知识可信程度，不要混用。

### 3.2 Finance

Finance 表示金融市场中可观察、可交易或可度量的对象。

类型固定为：

```text
ASSET        BTC、原油、黄金
VARIABLE     BTC price、funding rate、oil price、US rates
INSTRUMENT   BTCUSDT perpetual、ETF、期货
MARKET       Polymarket 具体 condition / market
```

关键字段：

```text
finance_id
finance_type
label
venue
symbol
external_id
verification_status
origin_run_id
origin_proposal_id
current_version
```

### 3.3 Edge

Edge 分三类，必须限制允许连接的节点类型。

| relation_class | relation_type | 允许连接 |
| --- | --- | --- |
| LOGICAL | EQUAL / IMPLIES / DISJOINT / OVERLAP | Event -> Event |
| IMPACT | POSITIVE_IMPACT / NEGATIVE_IMPACT / ASSOCIATED | Event -> Event, Event -> Finance, Finance -> Finance |
| MAPPING | DIRECTLY_PRICES / TRACKS / HEDGES / EXPOSED_TO | 固定语义方向 |

MAPPING 方向固定：

```text
Finance MARKET     -- DIRECTLY_PRICES -> Event
Finance INSTRUMENT -- TRACKS          -> Finance ASSET / VARIABLE
Finance INSTRUMENT -- HEDGES          -> Event / Finance
Finance INSTRUMENT -- EXPOSED_TO      -> Event / Finance
```

Edge 必带解释字段：

```text
confidence
strength
expected_lag
mechanism
supporting_evidence_json
alternative_explanations_json
origin_run_id
origin_proposal_id
current_version
```

### 3.4 EventExpression

`AND / OR / NOT / DIFFERENCE` 不作为 Edge，而作为 Event 定义表达式。

```text
expression_id
operator = AND | OR | NOT | DIFFERENCE
input_event_ids_json
output_event_id
origin_run_id
origin_proposal_id
```

图谱默认不显示运算节点，只有点击“事件定义”时展开表达式。

## 4. Signal 与 Anomaly 的位置

Signal 不再作为默认图谱节点，而是节点指标、时间序列或异常徽标。

迁移方向：

```text
SIGNAL node
  -> node_metrics
  -> market_anomalies
  -> finance_observations
```

显示方式：

- 节点外圈热度。
- 节点徽标，例如 `probability_jump`、`volume_spike`。
- 详情侧栏指标卡。
- 时间序列图。
- Event Radar 里的异常列表。

只有当某个 Signal 自身可以定义真假状态时，才允许转成 Event Proposal。例如：

```text
BTC realized volatility above 90-day 99th percentile
```

这类对象可以成为“状态事件”，但仍要走 Proposal 和 Review。

## 5. 角色边界

### 5.1 System Task

确定性系统任务不属于 Agent Workflow。

建议 workflow_id：

```text
SYS_NEWS_LEADERBOARD_REFRESH
SYS_POLYMARKET_ANOMALY_SCAN
SYS_BINANCE_ANOMALY_SCAN
SYS_INVESTIGATION_CANDIDATE_BUILD
SYS_HEAT_DECAY
SYS_DUPLICATE_DETECTION
```

职责：

- 抓取新闻、公告、行情。
- 计算异常。
- 做时间、实体、主题匹配。
- 创建 Candidate。
- 更新 heat 与指标。
- 去重与归档低质量候选。

### 5.2 内置 Agent

内置 Agent 只消费 Candidate，不做无目标全局搜索。

建议 workflow_id：

```text
INT_EVENT_INVESTIGATION
```

职责：

- 调查 Candidate。
- 阅读相关 Observation / Anomaly。
- 检索支持证据和反证。
- 判断多个 observation 是否属于同一 canonical event。
- 映射相关 Finance。
- 生成 Edge 解释和替代解释。
- 保存 investigation report。
- 创建 graph proposal。

不能做：

- 直接修改 Graph Core。
- 跳过 Proposal。
- 批准自己的 Proposal。
- 直接启动外接 Agent 或策略实盘。

### 5.3 外接 Agent

外接 Agent 是 EventGraph 的受控消费者和变更请求提交者。

建议 workflow_id：

```text
EXT_GRAPH_RESEARCH
EXT_TOPIC_RESEARCH
EXT_CHANGE_REQUEST
EXT_EVENT_TO_STRATEGY_DRAFT
EXT_STRATEGY_REVIEW
```

可以做：

- 查询正式 EventGraph。
- 查询 Observation / Anomaly / Run Trace。
- 研究事件和策略代码匹配。
- 创建策略草案。
- 提交 Graph Change Request。

不能做：

- 直接调用内置 Agent。
- 直接修改正式 EventGraph。
- 直接批准 Proposal。
- 直接执行实盘。

### 5.4 Human Review

人类是正式知识写入的控制点。

可以做：

- 批准 Proposal。
- 拒绝 Proposal。
- 要求修改。
- 带修改批准。
- 归档或标记 disputed。

审批结果不等于 Agent 技术执行状态。一个 run 可以 `status=SUCCEEDED`，但 `outcome=REJECTED`。

## 6. Agent Run 调用链设计

当前表已经能记录 run、step、audit。下一阶段重点不是继续堆日志，而是让一个业务流程共用一个 `run_id`。

### 6.1 Run 状态

技术状态：

```text
CREATED
RUNNING
WAITING_HUMAN
SUCCEEDED
FAILED
CANCELLED
```

业务结果：

```text
APPROVED
APPROVED_WITH_CHANGES
REJECTED
NO_ACTION
INSUFFICIENT_EVIDENCE
FALSE_MATCH
PARTIAL
SUPERSEDED
```

建议给 `agent_runs` 增加字段：

```text
outcome
progress_percent
active_step_code
parent_run_id
requested_by_type
requested_by_id
related_candidate_id
related_proposal_id
related_strategy_id
```

如果不想继续扩大 `agent_runs`，也可以新增通用关联表：

```text
agent_run_links(
  run_id,
  object_type,
  object_id,
  relation_type,
  created_at_utc
)
```

推荐使用 `agent_run_links`，因为一个 run 可能同时关联 Candidate、Report、Proposal、Event、Edge、Strategy Draft。

### 6.2 Step 状态

Step 状态：

```text
PENDING
RUNNING
WAITING_EXTERNAL
WAITING_HUMAN
SUCCEEDED
FAILED
SKIPPED
```

建议给 `agent_run_steps` 增加字段：

```text
step_code
step_title
sequence
attempt
input_refs_json
output_refs_json
evidence_refs_json
tool_calls_json
started_at_utc
finished_at_utc
duration_ms
error_json
```

`capability` 仍保留用于权限和审计，`step_code` 用于 SOP 展示。

### 6.3 run_id 传递规则

所有 Agent 接口支持：

```text
X-Agent-Run-Id: run_xxx
```

或请求体：

```json
{
  "run_id": "run_xxx",
  "workflow_id": "INT_EVENT_INVESTIGATION",
  "step_id": "step_xxx"
}
```

规则：

- 没有 `run_id`：只允许外接 Agent 单次读接口自动创建临时 run。
- 内置 Agent 写接口必须传入由后端创建的 `run_id`。
- 传入有效 `run_id`：在原 run 下创建或更新 step。
- run 已结束：默认拒绝追加，除非显式 `resume=true` 且写入 resume audit。
- `agent_kind` 或 `workflow_id` 不匹配：拒绝追加。
- 同一 `step_code` 重试时增加 `attempt`，不要覆盖原失败记录。

## 7. 内置 Agent 标准 SOP

`INT_EVENT_INVESTIGATION` 使用固定步骤，让人类始终知道内置 Agent 在哪一环。

| 顺序 | step_code | 目的 | 产物 |
| --- | --- | --- | --- |
| 1 | LOAD_CANDIDATE | 读取 Candidate 和关联输入 | candidate snapshot |
| 2 | READ_OBSERVATIONS | 读取新闻、公告、市场 observation | observation refs |
| 3 | READ_ANOMALIES | 读取 Polymarket / Binance 异常 | anomaly refs |
| 4 | SEARCH_SUPPORTING_EVIDENCE | 检索支持证据和反证 | evidence bundle |
| 5 | IDENTIFY_CANONICAL_EVENT | 判断 canonical event、别名、时间窗 | canonical event draft |
| 6 | CHECK_DUPLICATES | 查找是否已有 Event 或 Proposal | duplicate decision |
| 7 | MAP_FINANCE | 映射 MARKET / ASSET / VARIABLE / INSTRUMENT | finance mapping draft |
| 8 | PROPOSE_EDGES | 生成 LOGICAL / IMPACT / MAPPING edge draft | edge draft |
| 9 | WRITE_INVESTIGATION_REPORT | 保存调查报告 | investigation report |
| 10 | CREATE_GRAPH_PROPOSAL | 创建 proposal 和 proposal items | proposal |
| 11 | WAIT_HUMAN_REVIEW | 等待人类审核 | review pending |
| 12 | APPLY_OR_CLOSE | 应用、拒绝或关闭 | graph version / outcome |

### 7.1 每步必须展示的信息

AgentMonitor 的 run detail 应显示：

```text
step_code
step_title
status
started_at
finished_at
duration
input_refs
output_refs
evidence_refs
error
audit_events
```

用户看到的不是一串无意义日志，而是：

```text
正在读取 Candidate
正在读取 12 条新闻 observation
正在检查 3 个市场异常
正在检索官方来源
正在判断是否已有 canonical event
正在生成 proposal
等待人工审核
```

### 7.2 SOP 停止条件

内置 Agent 必须能以业务结果停止：

| 停止点 | outcome | 说明 |
| --- | --- | --- |
| Candidate 质量不足 | INSUFFICIENT_EVIDENCE | observation/anomaly 不够 |
| 新闻与市场不匹配 | FALSE_MATCH | 时间或实体关联失败 |
| 已有正式 Event 覆盖 | NO_ACTION | 可补充 observation，但不建新 event |
| 部分 item 可提案 | PARTIAL | 例如 Event 可提案，Impact Edge 不足 |
| Proposal 被拒绝 | REJECTED | 技术 run 仍可 SUCCEEDED |
| 带修改批准 | APPROVED_WITH_CHANGES | 写入 review diff |

## 8. 数据表建议

### 8.1 Observation 与 Anomaly

现有 `event_graph_observations` 可继续保留，但建议统一命名或加 view。

新增：

```text
market_observations
market_anomalies
finance_observations
node_metrics
```

`market_anomalies` 建议字段：

```text
anomaly_id
finance_id
source_type = POLYMARKET | BINANCE
anomaly_type
severity
observed_at_utc
window_seconds
metric_name
metric_value
baseline_value
percentile
raw_json
created_at_utc
```

### 8.2 Candidate

```text
investigation_candidates
  candidate_id
  candidate_type = MATCHED_NEWS_MARKET | UNEXPLAINED_MARKET_MOVE | NEWS_WITHOUT_MARKET_REACTION
  status = READY | CLAIMED | RUNNING | WAITING_HUMAN | CLOSED
  priority
  title
  summary
  reason
  created_by = system
  assigned_run_id
  latest_report_id
  latest_proposal_id
  created_at_utc
  updated_at_utc
```

```text
investigation_candidate_inputs
  candidate_id
  input_type = observation | anomaly | finance | event | market
  input_id
  weight
  reason
  created_at_utc
```

### 8.3 Investigation Report

```text
investigation_reports
  report_id
  candidate_id
  run_id
  workflow_id
  conclusion = PLAUSIBLE_LINK | INSUFFICIENT_EVIDENCE | FALSE_MATCH | DUPLICATE | NO_ACTION
  confidence
  canonical_event_json
  finance_mapping_json
  edge_drafts_json
  supporting_evidence_json
  alternative_explanations_json
  report_text
  created_at_utc
```

### 8.4 Proposal 与 Review

```text
graph_proposals
  proposal_id
  candidate_id
  report_id
  run_id
  proposal_type = EVENT_PROPOSAL | EDGE_PROPOSAL | FINANCE_MAPPING_PROPOSAL | MERGE_PROPOSAL | ARCHIVE_PROPOSAL | MIXED
  status = DRAFT | PENDING_REVIEW | APPROVED | APPROVED_WITH_CHANGES | REJECTED | SUPERSEDED
  title
  summary
  created_by_type
  created_by_id
  created_at_utc
  updated_at_utc
```

```text
graph_proposal_items
  item_id
  proposal_id
  item_type = event | finance | edge | expression | merge | archive
  action = create | update | merge | archive
  target_id
  payload_json
  diff_json
  confidence
  evidence_refs_json
  status = PENDING | APPROVED | CHANGED | REJECTED
```

```text
graph_reviews
  review_id
  proposal_id
  run_id
  reviewer_type = human
  reviewer_id
  decision = APPROVE | APPROVE_WITH_CHANGES | REJECT | REQUEST_CHANGES
  item_decisions_json
  note
  created_at_utc
```

### 8.5 Graph Core

```text
events
event_versions
event_aliases
event_expressions

finance_nodes
finance_versions

edges
edge_versions

graph_snapshots
```

正式对象都保存：

```text
origin_run_id
origin_proposal_id
origin_review_id
current_version
verification_status
created_at_utc
updated_at_utc
```

## 9. API 设计

### 9.1 系统任务 API

```text
POST /api/event-graph/system/news-refresh
POST /api/event-graph/system/polymarket-anomaly-scan
POST /api/event-graph/system/binance-anomaly-scan
POST /api/event-graph/system/candidates/build
```

这些接口的 actor 是 `system`，写入 system run 或普通 job log，不进入内置 Agent SOP。

### 9.2 Candidate API

```text
GET  /api/event-graph/candidates
GET  /api/event-graph/candidates/{candidate_id}
POST /api/event-graph/candidates/{candidate_id}/claim
POST /api/event-graph/candidates/{candidate_id}/close
```

### 9.3 内置 Agent API

```text
POST /api/internal/event-graph/investigations/start
POST /api/internal/event-graph/runs/{run_id}/steps/{step_code}/start
POST /api/internal/event-graph/runs/{run_id}/steps/{step_code}/finish
POST /api/internal/event-graph/runs/{run_id}/reports
POST /api/internal/event-graph/runs/{run_id}/proposals
```

这些接口应强制：

- `actor_type=internal_agent`
- `workflow_id=INT_EVENT_INVESTIGATION`
- 必须绑定 `candidate_id`
- 必须写 `agent_run_links`

### 9.4 Review API

```text
GET  /api/event-graph/proposals
GET  /api/event-graph/proposals/{proposal_id}
POST /api/event-graph/proposals/{proposal_id}/approve
POST /api/event-graph/proposals/{proposal_id}/approve-with-changes
POST /api/event-graph/proposals/{proposal_id}/reject
POST /api/event-graph/proposals/{proposal_id}/request-changes
```

审批 API 的 actor 必须是 `human` 或 `admin`，不能是 `agent` 或 `internal_agent`。

### 9.5 外接 Agent API

```text
GET  /api/agent/event-graph/core
GET  /api/agent/event-graph/events
GET  /api/agent/event-graph/finance
GET  /api/agent/event-graph/edges
GET  /api/agent/event-graph/observations
GET  /api/agent/event-graph/anomalies
GET  /api/agent/event-graph/runs/{run_id}/trace
POST /api/agent/event-graph/change-requests
```

外接 Agent 的 `change-requests` 只生成 Proposal 或 Candidate，不直接进入 Graph Core。

## 10. UI 优化

### 10.1 Event Radar

发现区，不展示为正式知识。

内容：

- 新闻榜。
- Polymarket 异常。
- Binance 异常。
- 新 Candidate。
- 最近关闭的 false match / insufficient evidence。

操作：

- 查看 observation。
- 查看 anomaly。
- 创建或重新生成 Candidate。
- 启动内置调查。

### 10.2 Investigation Queue

内置 Agent 任务队列。

列表字段：

```text
Candidate
Type
Priority
Status
Assigned Run
Active Step
Outcome
Latest Report
Latest Proposal
Updated At
```

详情页分区：

```text
Candidate Inputs
Agent Run Timeline
Evidence
Investigation Report
Proposal Items
Human Review
```

### 10.3 EventGraph

正式图谱页面应分模式：

```text
事件结构
影响网络
金融映射
```

显示规则：

- 默认只显示 `HUMAN_VERIFIED` 或显式选择的 candidate/proposal overlay。
- Signal 变为节点指标，不默认显示气泡节点。
- 详情页必须显示 Trace Card。

Trace Card：

```text
Source Layer
Candidate
Run
Report
Proposal
Review
Graph Version
Audit Events
```

### 10.4 Agent Runs

独立页面或 AgentMonitor 子页。

列表字段：

```text
Run ID
Agent Kind
Workflow
Status
Outcome
Active Step
Progress
Candidate
Proposal
Started At
Duration
Errors
```

Run Detail 用时间线展示 SOP step，而不是只显示 JSON。

### 10.5 AgentMonitor

现有四个 tab 可以保留，但内置 Agent tab 要从“审计日志筛选”升级为“调查队列加 Run Trace”：

```text
总览
  -> 待审核 Proposal
  -> 正在运行的内置调查
  -> 外接策略审批
  -> 最近失败步骤

内置 Agent
  -> Investigation Queue
  -> Run Timeline
  -> Report / Proposal

外接 Agent
  -> 策略草案
  -> 待人工确认
  -> Change Request

审计日志
  -> 全量 audit，可按 run_id / agent_kind / workflow_id 过滤
```

## 11. Edge 和 Proposal 校验规则

Proposal 入库前必须做确定性校验：

- `relation_class` 和 `relation_type` 匹配。
- source / target 节点类型合法。
- MAPPING edge 方向合法。
- EventExpression operator 合法。
- confidence 在 0 到 1。
- impact edge 必须有 `mechanism` 和 `alternative_explanations`。
- proposal item 必须引用 evidence refs。
- merge proposal 必须保留 source ids、target id、理由和回滚信息。
- archive 不物理删除，只改 lifecycle 或 status。

审批 apply 前必须：

- 创建 `graph_snapshots`。
- 写 version 表。
- 写 origin run/proposal/review。
- 更新 proposal item status。
- 更新 run outcome。

## 12. 开发优先级

### P0: 串联 run_id 和 Run Trace

目标：先让“内置 Agent 在干嘛”可见。

任务：

- 给 `agent_runs` 增加 `outcome`、`active_step_code`、`progress_percent`，或新增 `agent_run_links`。
- 内置调查相关调用必须复用同一个 `run_id`。
- `/api/agent/runs/{run_id}/steps` 返回 step_code、业务关联和 audit。
- AgentMonitor 内置 Agent tab 支持点击 run 展开时间线。

验收：

```text
同一 Candidate 从启动调查到创建 Proposal，全程只有一个 run_id。
用户能看到当前停在哪个 SOP step。
失败 step 能看到错误和输入输出摘要。
```

### P1: Candidate 和 Proposal

目标：补齐 Observation 到 Graph Core 中间层。

任务：

- 新增 `investigation_candidates`、`investigation_candidate_inputs`。
- 新增 `investigation_reports`。
- 新增 `graph_proposals`、`graph_proposal_items`、`graph_reviews`。
- Event Radar 可以创建 Candidate。
- 内置 Agent 可以从 Candidate 创建 Report / Proposal。

验收：

```text
新闻/市场异常不会直接写 Event。
每个 Proposal 都能追溯 Candidate、Report、Run。
人类可以批准、拒绝、要求修改。
```

### P2: Signal 迁移和 Anomaly 表

目标：把指标从节点里拿出来。

任务：

- 新增 `market_anomalies`、`node_metrics`、`finance_observations`。
- `event_graph_service.py` 返回 Signal metrics，但默认不生成 SIGNAL 节点。
- UI 用徽标、外圈和指标卡显示 Signal。

验收：

```text
图谱默认只显示 Event 和 Finance。
异常仍能在详情和 Event Radar 中查看。
```

### P3: Graph Core 和版本化

目标：正式图谱与 preview 分离。

任务：

- 新增 `events`、`finance_nodes`、`edges`、versions、aliases、snapshots。
- `/api/event-graph` 增加 `mode=core|preview|overlay`。
- Review apply 写入 Graph Core。

验收：

```text
正式 EventGraph 不再依赖即时 Polymarket search 生成核心节点。
每个正式 Event / Edge 都有 origin_run_id 和 origin_proposal_id。
```

### P4: 内置 Agent Runner

目标：真正 SOP 化运行。

任务：

- 实现 `INT_EVENT_INVESTIGATION` runner。
- 每步开始和结束写 step。
- 支持 resume、retry、cancel。
- LLM 调用只在 evidence、normalization、proposal draft 步骤使用。

验收：

```text
用户不用猜 Agent 在想什么，Run Timeline 可以看到每一步产物。
Agent 技术失败和业务拒绝能清楚区分。
```

### P5: 外接 Agent Change Request

目标：让外接 Agent 能协作但不能越权。

任务：

- 新增 `EXT_CHANGE_REQUEST`。
- 外接 Agent 可提交 Graph Proposal 或 Candidate。
- Change Request 进入人类审核或转内置调查队列。

验收：

```text
外接 Agent 不能直接调用内置 Agent。
外接 Agent 的图谱修改只能成为待审核请求。
```

## 13. 首批 MVP 建议

第一轮不要一次性做完整 Graph Core。先做让流程可见的最小闭环：

```text
1. agent_run_links
2. agent_runs.outcome / active_step_code / progress_percent
3. investigation_candidates
4. investigation_reports
5. graph_proposals / graph_proposal_items
6. AgentMonitor 内置 Agent Run Timeline
7. Event / Edge 详情 Trace Card
```

最小闭环：

```text
人工或系统创建 Candidate
  -> 启动 INT_EVENT_INVESTIGATION run
  -> 写 12 个 SOP step
  -> 保存 report
  -> 创建 proposal
  -> 人类拒绝或批准
  -> run.status=SUCCEEDED
  -> run.outcome=APPROVED / REJECTED / INSUFFICIENT_EVIDENCE
```

这时即使 Graph Core 还没完全拆出来，系统也已经能回答：

```text
内置 Agent 正在调查什么？
调查到了哪一步？
用了哪些 observation / anomaly？
结论是什么？
生成了什么 proposal？
谁审核了？
为什么写入或没有写入？
```

## 14. 示例

### 14.1 系统发现

```text
News Observation:
  Reuters / BBC / Google News 出现霍尔木兹海峡紧张局势相关报道

Polymarket Anomaly:
  相关市场 1 小时概率上涨 14%

Binance Anomaly:
  原油相关 proxy / crypto beta 出现波动异常
```

系统创建：

```text
candidate_id = cand_hormuz_001
candidate_type = MATCHED_NEWS_MARKET
status = READY
```

### 14.2 内置 Agent 调查

```text
run_id = run_hormuz_001
agent_kind = internal
workflow_id = INT_EVENT_INVESTIGATION
```

SOP 结果：

```text
LOAD_CANDIDATE               SUCCEEDED
READ_OBSERVATIONS            SUCCEEDED
READ_ANOMALIES               SUCCEEDED
SEARCH_SUPPORTING_EVIDENCE   SUCCEEDED
IDENTIFY_CANONICAL_EVENT     SUCCEEDED
CHECK_DUPLICATES             SUCCEEDED
MAP_FINANCE                  SUCCEEDED
PROPOSE_EDGES                SUCCEEDED
WRITE_INVESTIGATION_REPORT   SUCCEEDED
CREATE_GRAPH_PROPOSAL        SUCCEEDED
WAIT_HUMAN_REVIEW            WAITING_HUMAN
```

生成 Proposal：

```text
EVENT_PROPOSAL:
  霍尔木兹海峡严重航运中断风险上升

FINANCE_MAPPING_PROPOSAL:
  Polymarket 相关市场
  原油价格变量
  能源相关资产

EDGE_PROPOSAL:
  Polymarket MARKET DIRECTLY_PRICES Event
  Event POSITIVE_IMPACT crude oil risk
```

### 14.3 人类审核

```text
批准 Event
批准 DIRECTLY_PRICES
将 POSITIVE_IMPACT confidence 从 0.82 改为 0.68
```

系统写入：

```text
proposal.status = APPROVED_WITH_CHANGES
run.status = SUCCEEDED
run.outcome = APPROVED_WITH_CHANGES
event.origin_run_id = run_hormuz_001
edge.origin_run_id = run_hormuz_001
```

以后点击这条 Edge，可以追溯：

```text
Edge
  -> Proposal item
  -> Graph review
  -> Investigation report
  -> Candidate
  -> Observation / Anomaly
  -> Agent Run
  -> 每个 Step / Audit / Error
```

## 15. 结论

EventGraph 下一阶段的关键不是让 Agent 更“自由”，而是让 Agent 更“有轨道”。

最终形态应是：

```text
确定性程序负责发现和排队
内置 Agent 负责按 SOP 调查和提案
人类负责确认正式知识
EventGraph 负责保存可追溯知识
外接 Agent 负责使用知识和提交受控请求
策略系统负责在风控与审批后使用知识
```

`agent_runs / agent_run_steps / agent_audit_events` 已经提供了执行链骨架。下一步只要把 Candidate、Report、Proposal、Review 和 Graph Core 都挂到同一个 `run_id` 上，EventGraph 就会从“图上有什么”升级成“知识如何产生、为何可信、谁确认过”的系统。
