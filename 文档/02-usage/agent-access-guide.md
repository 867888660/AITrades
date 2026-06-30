# Agent 操作手册

更新日期：2026-06-30

这是外部 agent 接入本系统时唯一需要优先阅读的手册。本文同时包含标准 workflow、接口入口、权限边界、EventGraph 新闻事件操作、策略草案与审批流程。

如果你是 agent，请只把本文作为操作入口；`agent-interface-design.md` 是开发者架构设计，不是日常执行手册。

## 0. Agent 定位

agent 在本系统中的职责是：

```text
观察 -> 检索 -> 归纳 -> 提案 -> 风控/模拟 -> 提交人工确认
```

agent 不能做：

```text
自我授权
自我审批
绕过风控
直接实盘执行
修改自己的权限
把 SYSTEM_DERIVED 事件当成人工核验事实
在权限不足时改走非受控接口绕过限制
```

系统分工：

```text
agent 负责研究、检索、整理、草案、解释、提交
系统负责权限、风控、审计、冻结快照和执行边界
人类负责确认、资金、权限、批准、拒绝和最终执行开关
```

## 1. 连接入口

默认服务地址：

```text
http://127.0.0.1:5001
```

常用页面：

| 页面 | URL | 用途 |
| --- | --- | --- |
| Dashboard | `/` | 总览、市场查询、策略监控入口 |
| EventGraph | `/event-graph` | 事件图谱、新闻事件和市场信号 |
| AgentMonitor | `/agent-monitor` | agent 总览、EventGraph 变更、外接 Agent、审计日志 |
| Settings | `/settings` | 人类设置 agent 权限、预算、单笔上限 |
| Strategy Workspace | `/strategies/<strategy_id>/workspace` | 单个策略工作台 |
| Ledger | `/ledger` | 订单、仓位、账本诊断 |
| Watchlist | `/watchlist` | 浏览器本地自选市场 |

agent 的受控接口都在：

```text
/api/agent/*
```

普通 EventGraph 查询接口也存在，但外部 agent 应优先使用 `/api/agent/event-graph/*`，这样权限和审计才一致。

所有 POST / PATCH / DELETE 请求使用：

```text
Content-Type: application/json
```

标准响应：

```json
{
  "ok": true,
  "data": {}
}
```

错误响应：

```json
{
  "ok": false,
  "error": "agent capability disabled by settings: event.news.search"
}
```

## 2. 身份与权限

默认 agent 身份：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant"
}
```

GET 接口通常会默认使用这个身份。写接口必须显式带上它。

每次任务第一步必须调用：

```text
GET /api/agent/capabilities
```

必须检查：

- `enabled` 是否为 `true`。
- `allow` 里是否包含本任务需要的 capability。
- `deny` 里是否禁止目标动作。
- `limits.max_strategy_budget_usdc` 单策略资金上限。
- `limits.max_single_order_usdc` 单笔资金上限。
- `limits.max_daily_spend_usdc` 单日资金上限。
- `limits.require_human_approval` 是否要求人工审批。
- `market_query_capabilities` 市场查询能力。
- `strategy_observation_capabilities` 策略观察能力。
- `event_graph_capabilities` EventGraph 与新闻事件能力。
- `strategy_submission_template` 策略提交说明模板。

权限不足时，agent 只能报告权限不足，不能绕过设置端。

## 3. 通用任务骨架

所有任务都按下面三段执行：

```text
Preflight
  -> Workflow Body
  -> Closeout
```

### 3.1 Preflight

必调接口：

```text
GET  /api/agent/capabilities
POST /api/agent/activity
GET  /api/agent/dashboard?limit=50
```

建议 agent 在同一个 workflow 内复用同一个 `run_id`，并在每个写请求里带上 `workflow_id`。如果未提供，后端会自动为单次请求生成 `run_id` 和 `step_id`，但多接口 workflow 就无法自然串起来。

推荐上下文字段：

```json
{
  "run_id": "run_<client_generated_id>",
  "workflow_id": "D_MARKET_TO_STRATEGY_DRAFTS",
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant"
}
```

标准步骤：

1. 调用 `/api/agent/capabilities`。
2. 检查 `enabled=true`。
3. 检查本 workflow 所需 capability 是否在 `allow`。
4. 检查 `deny` 是否命中目标动作。
5. 读取预算、单笔、审批、默认扫描分类等限制。
6. 调用 `/api/agent/activity` 写入开始状态。
7. 调用 `/api/agent/dashboard` 读取未处理审批、草案和活动。

活动模板：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant",
  "state": "AI_DRAFTING",
  "message": "开始执行 workflow: <workflow_id> - <short goal>",
  "ref_type": "workflow",
  "ref_id": "<workflow_id>"
}
```

Preflight 停止条件：

- agent 未启用。
- 缺少本 workflow 必需 capability。
- 人类要求的动作在 `deny` 中。
- 任务要求直接实盘执行、直接批准或修改 agent 权限。

### 3.2 Closeout

结束前必须向人类输出：

```text
workflow_id
目标
使用的主要接口
读取/写入的对象数量
生成的 draft / approval / event 数量
失败或跳过原因
需要人类确认的事项
下一步建议
```

如果生成审批单，必须明确：

```text
我已经提交到 WAITING_HUMAN_CONFIRM，不会自己批准。
```

## 4. 标准 Workflow

固定 workflow_id：

| workflow_id | 名称 |
| --- | --- |
| `A_GLOBAL_NEWS_REFRESH` | 全球新闻事件刷新 |
| `B_TOPIC_RESEARCH` | 指定事件/主题研究 |
| `C_DAILY_EVENT_RADAR` | EventGraph 每日巡检 |
| `D_MARKET_TO_STRATEGY_DRAFTS` | 热门市场到策略草案 |
| `E_SINGLE_EVENT_STRATEGY_DRAFT` | 单事件到单策略草案 |
| `F_STRATEGY_HEALTH_CHECK` | 策略运行巡检 |
| `G_APPROVAL_REVIEW` | 审批跟进 |
| `H_ERROR_HANDLING` | 错误处理 |

agent 写 activity 时应把 workflow_id 放进 `ref_id`。

## 4.1 AgentMonitor 与调用链

AgentMonitor 分成四个视图：

| 视图 | 用途 |
| --- | --- |
| 总览 | 聚合待确认策略、策略草案、EventGraph 变更、风控阻断计数。 |
| EventGraph 变更 | 校验 patch、提交 change request、查看并执行 approve / reject / request-changes / apply。 |
| 外接 Agent | 显示外部 agent 的策略审批、策略草案和最近活动。 |
| 审计日志 | 显示完整流水，支持搜索、类别筛选和清除当前筛选。 |

后端会把 agent 调用保存为三层：

```text
agent_runs
  -> agent_run_steps
  -> agent_audit_events
```

每次 `/api/agent/*` 成功或失败都会至少生成一个 run 和一个 step。失败请求也会保存 `status=failed`、`error_json`、`endpoint`、`method`、`status_code`，用于后期 debug。

调试接口：

```text
GET /api/agent/runs?limit=100
GET /api/agent/runs?agent_kind=internal
GET /api/agent/runs?agent_kind=external
GET /api/agent/runs/{run_id}/steps
GET /api/agent/audit?run_id={run_id}
GET /api/agent/audit?agent_kind=internal
```

`agent_kind` 约定：

| agent_kind | 来源 |
| --- | --- |
| `internal` | 历史兼容分组；当前不再建设内置 EventGraph agent。 |
| `external` | 外接 agent，兼容旧 `actor_type=agent`，也支持 `external_agent`。 |
| `human` | 人类审批、修改、拒绝、清理审计等动作。 |
| `system` | 后台系统任务。 |

## 5. Workflow A：全球新闻事件刷新

适用任务：

```text
刷新全球热门新闻
更新 EventGraph 新闻事件库
看看现在世界上有什么大事
```

目标：把全球热门新闻写入 `Data/EventGraph.db`，形成 observation 和 derived event。

必需 capability：

```text
event.read
event.news.refresh
```

接口顺序：

```text
GET  /api/agent/capabilities
POST /api/agent/activity
POST /api/agent/event-graph/news/refresh
GET  /api/agent/event-graph/news/status
GET  /api/agent/event-graph/events?limit=30
GET  /api/agent/event-graph?include_news=1&news_limit=30
```

请求模板：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant",
  "limit_per_source": 24
}
```

输出模板：

```text
全球新闻刷新完成：
- sources_attempted:
- sources_ok:
- observations_seen:
- observations_inserted:
- events_upserted:
- 当前 EventGraph 新闻事件数:
- 当前 observations 数:

高热度事件：
1. <event title> | heat=<heat> | sources=<n> | observations=<n>
2. ...

注意：
- 这些是 SYSTEM_DERIVED 新闻事件，不是人工核验事实。
- 如果要进入策略流程，需要继续执行 Workflow C / D / E。
```

## 6. Workflow B：指定事件 / 主题研究

适用任务：

```text
检索某个新闻主题
研究某个事件是否正在发酵
查看某个关键词相关的市场和新闻
```

必需 capability：

```text
event.read
event.news.search
market.search
```

输入：

```text
q = 关键词，例如 "Iran ceasefire" / "Fed rate cut" / "Bitcoin ETF"
```

接口顺序：

```text
POST /api/agent/event-graph/news/search
GET  /api/agent/event-graph/events?q=<q>&limit=20
GET  /api/agent/event-graph/observations?q=<q>&limit=30
GET  /api/agent/event-graph?q=<q>&include_news=1&news_limit=20
GET  /api/agent/markets?q=<q>&sort=volume24h&order=desc&limit=30
```

请求模板：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant",
  "q": "<topic>",
  "limit_per_source": 30
}
```

输出模板：

```text
事件研究：<q>

1. 新闻侧
- 新增 observations:
- 主要来源:
- 最新发布时间:
- 代表性标题:

2. EventGraph 侧
- 匹配 derived events:
- 最高 heat:
- 相关 signal:

3. 市场侧
- Polymarket 相关市场数量:
- 最高 24h volume 市场:
- 流动性最高市场:
- spread 风险:

4. 初步判断
- 正在升温 / 已高热 / 信息不足 / 市场未跟随
- 需要继续观察的触发点:

5. 限制
- 新闻事件是 SYSTEM_DERIVED。
- 市场价格不是事实确认。
- 当前不自动创建策略，除非用户要求继续执行 Workflow D 或 E。
```

停止条件：

- `q` 为空。
- 新闻检索失败且没有已有 observations。
- 市场搜索权限关闭时，只输出新闻侧，不输出市场侧。

## 6.1 Workflow B2：提交 EventGraph 变更请求

适用任务：

```text
新增正式 Event
更新 Event / Finance / Edge / Expression
合并重复事件
把事件映射到金融市场或资产
归档错误关系
```

必需 capability：

```text
event.read
event.graph.patch.validate
event.graph.change_request
```

接口顺序：

```text
GET  /api/agent/event-graph/core?q=<topic>
GET  /api/agent/event-graph/core/versions?object_type=<type>&object_id=<id>
POST /api/agent/event-graph/patches/validate
POST /api/agent/event-graph/change-requests
GET  /api/agent/event-graph/change-requests/{request_id}
```

人类审核入口：

```text
POST /api/event-graph/change-requests/{request_id}/approve
POST /api/event-graph/change-requests/{request_id}/reject
POST /api/event-graph/change-requests/{request_id}/request-changes
POST /api/event-graph/change-requests/{request_id}/apply
```

patch item 支持：

```text
event_create / event_update / event_archive / event_merge
finance_create / finance_update / finance_archive
edge_create / edge_update / edge_delete / finance_mapping_create
expression_create / expression_update / expression_archive
```

请求模板：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant",
  "run_id": "run_external_xxx",
  "workflow_id": "EXT_GRAPH_CHANGE_REQUEST",
  "change_type": "edge_create",
  "title": "Add event-market mapping",
  "reason": "Why this graph change is needed",
  "evidence_summary": "Sources and human review focus",
  "patch": {
    "items": [
      {
        "action": "edge_create",
        "source_id": "evt_xxx",
        "source_type": "event",
        "target_id": "fin_xxx",
        "target_type": "finance",
        "relation_class": "MAPPING",
        "relation_type": "DIRECTLY_PRICES",
        "confidence": 0.82,
        "mechanism": "Why the market directly prices the event",
        "evidence_refs": []
      }
    ]
  }
}
```

输出要求：

- 必须说明为什么提交 change request。
- 必须列出证据和需要人类重点审核的点。
- 不能把 `PENDING` change request 当作已经写入 Graph Core。
- 只有 `APPLIED` 状态才表示正式图谱已更新。
- 如修改已有对象，先读取 `/core/versions`，避免基于旧事实提交 patch。

## 7. Workflow C：EventGraph 每日巡检

适用任务：

```text
每天定时更新事件库
给我今日全球事件摘要
检查哪些事件可能影响金融资产
```

目标：形成“今日事件雷达”，但不自动生成策略。

必需 capability：

```text
event.read
event.news.refresh
market.search
```

接口顺序：

```text
POST /api/agent/event-graph/news/refresh
GET  /api/agent/event-graph?include_news=1&news_limit=50&limit=80
GET  /api/agent/event-graph/events?limit=50
GET  /api/agent/markets?category=Politics,Crypto,Elections,Economics,Geopolitics,World&sort=volume24h&order=desc&limit=50
```

输出模板：

```text
今日事件雷达：

新闻高热事件：
1. ...

市场高热事件：
1. ...

新闻与市场可能重合：
1. <news event> <-> <market question>

可能影响的资产/变量：
- BTC / ETH / SOL
- US rates
- Gold
- Oil
- US equity index
- Geopolitical risk

建议：
- 仅观察:
- 建议深入研究:
- 可以进入策略扫描:
```

停止条件：

- 该 workflow 不直接创建策略。
- 只有用户明确要求“生成策略草案”时，才能进入 Workflow D 或 E。

## 8. Workflow D：热门市场扫描到策略草案

适用任务：

```text
扫描热门市场并生成策略草案
根据当前事件生成小额策略
把合适机会提交给我确认
```

目标：从事件/市场候选生成策略草案，并提交 `WAITING_HUMAN_CONFIRM`。

必需 capability：

```text
market.hot_scan
strategy.batch.propose
risk.check
strategy.simulate
strategy.submit
```

接口顺序：

```text
GET  /api/agent/capabilities
POST /api/agent/activity
POST /api/agent/market-scan
POST /api/agent/market-scan/propose-strategies
GET  /api/agent/approvals?status=WAITING_HUMAN_CONFIRM&limit=100
```

请求模板：

```json
{
  "actor_type": "agent",
  "actor_id": "agent_strategy_assistant",
  "categories": ["Elections Politics", "World", "Geopolitics"],
  "sorts": ["volume24h", "volume", "liquidity", "spread"],
  "price_filters": {
    "yes_ask": { "min": 0.20, "max": 0.45 },
    "no_bid": { "min": 0.55 }
  },
  "limit": 30,
  "candidate_limit": 20,
  "max_drafts": 5,
  "selection_mode": "yes",
  "budget_usdc": 20,
  "max_single_order_usdc": 5,
  "submit_for_approval": true
}
```

候选过滤标准：

```text
active = true
accepting_orders = true
volume_24h 足够
liquidity 足够
spread 不过大
Ask/Bid 在请求指定范围内
规则清楚
事件不是纯噪声
预算不超限
单笔不超限
```

提交前必须有 `agent_report` 五项：

```text
strategy_reason
market_observation
parameter_rationale
risk_control
human_review_focus
```

输出模板：

```text
策略草案提交流程完成：
- 扫描市场数:
- 候选数:
- 生成 draft 数:
- 风控通过:
- 风控阻断:
- 已提交 WAITING_HUMAN_CONFIRM:

待你确认：
1. <approval_id> - <strategy name> - budget=<...> - single_order=<...>
2. ...

我不会批准自己的策略，也不会实盘执行。
```

停止条件：

- 风控失败时不能提交审批。
- `submit_for_approval=false` 时只保留草案，不进入审批。
- 审批单生成后 agent 必须停止等待人类。

## 9. Workflow E：单事件到单策略草案

适用任务：

```text
围绕这个事件帮我做一个策略草案
这个 Polymarket 市场怎么看，给我一个草案
```

目标：围绕单个事件/市场创建一个可审阅策略草案。

必需 capability：

```text
event.read
market.search
strategy.draft.create
risk.check
strategy.simulate
strategy.submit
```

接口顺序：

```text
POST /api/agent/event-graph/news/search        # 如果输入是新闻事件
GET  /api/agent/event-graph?q=<q>
GET  /api/agent/markets?q=<q>&sort=volume24h&order=desc&yes_ask_min=0.20&yes_ask_max=0.45&limit=20
POST /api/agent/strategy-drafts
POST /api/agent/strategy-drafts/<draft_id>/risk-check
POST /api/agent/strategy-drafts/<draft_id>/simulate
POST /api/agent/strategy-drafts/<draft_id>/submit
```

单草案必须包含：

```text
name
strategy_code
thesis
agent_report
markets
budget
execution_rules
exit_rules
params
risk_notes
```

输出模板：

```text
单事件策略草案：
- 事件:
- 新闻证据:
- 选择市场:
- 方向:
- fair_price:
- entry_edge:
- 预算:
- 单笔:
- 风控结果:
- 模拟结果:
- approval_id:

需要你确认：
- 事件理解是否正确。
- 市场问题和方向是否正确。
- AgentMonitor 中 `Side Bid/Ask`、`All Bid/Ask` 是否支持该入场价。
- fair_price 是否符合你的主观判断。
```

停止条件：

- 找不到明确市场。
- 市场规则不清楚。
- fair_price 无法解释。
- 风控失败。

## 10. Workflow F：策略运行巡检

适用任务：

```text
帮我看看今天策略运行情况
检查哪些策略异常
总结当前 PnL / Action / State
```

目标：读取策略运行态，生成巡检报告，不直接改策略。

必需 capability：

```text
strategy.read_all
strategy.detail.read
strategy.workspace.read
strategy.events.read
strategy.state.read
pnl.read             # 如果要输出收益字段
```

接口顺序：

```text
GET /api/agent/strategies?limit=100
GET /api/agent/strategies/<strategy_id>
GET /api/agent/strategies/<strategy_id>/workspace?include_events=1
GET /api/agent/strategies/<strategy_id>/events?limit=50
GET /api/agent/strategies/<strategy_id>/state
```

输出模板：

```text
策略巡检：

整体：
- 策略数:
- Virtual:
- Real:
- Stop:
- auto:
- stop_loss_locked:

收益：（仅 pnl.read=true）
- 总 PnL:
- 最大正贡献:
- 最大负贡献:

异常：
- 无盘口:
- 风控阻断:
- 连续 HOLD:
- stop_loss_locked:
- stale data:

需要人类处理：
1. ...
```

停止条件：

- `pnl.read=false` 时不能输出 PnL / ROI / profit / fee。
- 巡检 workflow 不直接暂停、删除、平仓或改 state。

## 11. Workflow G：审批跟进

适用任务：

```text
看看有哪些 agent 草案等我确认
列出待审批策略
解释某个 approval
```

目标：整理待确认事项，帮助人类审批。

必需 capability：

```text
approval.status
audit.read
```

接口顺序：

```text
GET /api/agent/approvals?status=WAITING_HUMAN_CONFIRM&limit=100
GET /api/agent/approvals/<approval_id>
GET /api/agent/audit?limit=100
```

输出模板：

```text
待人工确认：

1. <approval_id>
- 策略:
- 市场:
- 方向:
- 预算:
- 单笔:
- 风控:
- 模拟:
- agent 理由:
- 人类重点检查:

建议处理：
- 可考虑批准:
- 建议要求修改:
- 建议拒绝:

注意：最终 approve / reject / request-changes 必须由人类执行。
```

停止条件：

- agent 不能调用 approve / reject / request-changes。
- 如果审批已过期或状态不是 `WAITING_HUMAN_CONFIRM`，必须重新读取最新详情。

## 12. Workflow H：错误与权限不足处理

适用任务：

```text
任何 workflow 中遇到错误
```

标准处理：

1. 记录失败接口、HTTP 状态、错误消息。
2. 判断是否是权限不足。
3. 判断是否是网络/外部源失败。
4. 判断是否是风控失败。
5. 不绕过。
6. 给人类一个可执行建议。

输出模板：

```text
workflow 失败：
- workflow_id:
- step:
- endpoint:
- error:
- 已完成步骤:
- 未完成步骤:

判断：
- 权限不足 / 外部源失败 / 风控失败 / 数据为空 / 参数错误

建议：
- 打开 Settings 中的 <permission>
- 修改预算或单笔
- 换关键词重新搜索
- 等待外部源恢复
```

## 13. 接口速查

### 能力与活动

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agent/capabilities` | 读取权限、限制、模板、能力 |
| GET | `/api/agent/dashboard?limit=50` | AgentMonitor 总览 |
| GET | `/api/agent/activity?limit=50` | agent 活动列表 |
| POST | `/api/agent/activity` | 写入 agent 当前活动 |
| GET | `/api/agent/audit?limit=100` | 审计日志 |
| GET | `/api/agent/runs?limit=100` | agent workflow/run 列表 |
| GET | `/api/agent/runs/{run_id}/steps` | 单个 run 的步骤、审计详情 |

### EventGraph 与新闻事件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agent/event-graph` | 查询图谱，包含新闻事件和市场事件 |
| GET | `/api/agent/event-graph/news/status` | 新闻事件库状态 |
| POST | `/api/agent/event-graph/news/refresh` | 刷新全球新闻 |
| POST | `/api/agent/event-graph/news/search` | 按 `q` 检索新闻 |
| GET | `/api/agent/event-graph/events` | 查询 derived news events |
| GET | `/api/agent/event-graph/observations` | 查询新闻 observations |

### 市场查询

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agent/market-categories` | 市场类别 |
| GET | `/api/agent/markets` | 按 q/category/sort/order/limit 查市场 |
| GET | `/api/agent/markets/resolve` | 解析 condition_id / slug |
| POST | `/api/agent/market-scan` | 多排序热门扫描 |
| POST | `/api/agent/market-scan/propose-strategies` | 扫描并生成策略草案 |

`/api/agent/markets` 和 `/api/agent/market-scan` 都支持盘口范围过滤：

```text
yes_ask_min / yes_ask_max
yes_bid_min / yes_bid_max
no_ask_min / no_ask_max
no_bid_min / no_bid_max
```

POST JSON 也可使用：

```json
{
  "price_filters": {
    "yes_ask": { "min": 0.20, "max": 0.45 },
    "no_bid": { "min": 0.55 }
  }
}
```

这些范围来自当前缓存/快照 bid/ask，用于筛候选；审批页 AgentMonitor 会显示 `Side Bid/Ask` 和 `All Bid/Ask` 供人工复核。

### 策略观察

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agent/strategies?limit=100` | 策略列表 |
| GET | `/api/agent/strategies/<strategy_id>` | 策略详情 |
| GET | `/api/agent/strategies/<strategy_id>/workspace` | 策略工作台数据 |
| GET | `/api/agent/strategies/<strategy_id>/usedata` | 策略 UseData |
| GET | `/api/agent/strategies/<strategy_id>/events?limit=50` | Action / trade / print 日志 |
| GET | `/api/agent/strategies/<strategy_id>/state` | machine / runtime / user / system state |

### 草案与审批

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/agent/strategy-drafts?limit=100` | 草案列表 |
| POST | `/api/agent/strategy-drafts` | 创建草案 |
| GET | `/api/agent/strategy-drafts/<draft_id>` | 草案详情 |
| PATCH | `/api/agent/strategy-drafts/<draft_id>` | 修改未提交草案 |
| DELETE | `/api/agent/strategy-drafts/<draft_id>` | 删除未提交草案 |
| POST | `/api/agent/strategy-drafts/<draft_id>/risk-check` | 风控检查 |
| POST | `/api/agent/strategy-drafts/<draft_id>/simulate` | 模拟 |
| POST | `/api/agent/strategy-drafts/<draft_id>/submit` | 提交人工确认 |
| GET | `/api/agent/approvals` | 审批列表 |
| GET | `/api/agent/approvals/<approval_id>` | 审批详情 |

人类审批接口：

```text
POST /api/approvals/<approval_id>/approve
POST /api/approvals/<approval_id>/reject
POST /api/approvals/<approval_id>/request-changes
```

这些接口不是给 agent 自己批准用的。agent 不应以 `actor_type=agent` 调用审批完成动作。

## 14. 权限名称速查

| 设置页权限 | capability | 影响 |
| --- | --- | --- |
| 读取市场与审批状态 | `market.read` | 读市场、审批状态 |
| 搜索市场 | `market.search` | `/api/agent/markets` |
| 热门市场扫描 | `market.hot_scan` | `/api/agent/market-scan` |
| 查看全部策略列表 | `strategy.read_all` | 策略列表 |
| 查看策略详情 | `strategy.detail.read` | 单策略详情 |
| 查看策略工作台 / UseData | `strategy.workspace.read` / `strategy.usedata.read` | 工作台和 UseData |
| 查看策略 Action 日志 | `strategy.events.read` | events |
| 查看策略 State | `strategy.state.read` | state |
| 批量生成策略草案 | `strategy.batch.propose` | 扫描并生成草案 |
| 新增策略草案 | `strategy.draft.create` | 创建草案 |
| 修改策略草案 | `strategy.draft.update` | 修改草案 |
| 删除未提交草案 | `strategy.draft.delete` | 删除未提交草案 |
| 执行风控检查 | `risk.check` | risk-check |
| 执行策略模拟 | `strategy.simulate` | simulate |
| 提交人工确认 | `strategy.submit` | submit |
| 读取收益 | `pnl.read` | PnL / ROI / profit / fee |
| 读取审计日志 | `audit.read` | audit |
| 读取 EventGraph / 新闻事件 | `event.read` | EventGraph、events、observations |
| 刷新全球新闻事件 | `event.news.refresh` | 全球新闻 RSS refresh |
| 检索指定新闻事件 | `event.news.search` | 指定关键词新闻 search |

## 15. 最小接入检查清单

agent 开始工作前：

```text
[ ] base URL 是 http://127.0.0.1:5001
[ ] GET /api/agent/capabilities 成功
[ ] enabled=true
[ ] 所需 capability 在 allow 中
[ ] deny 中没有命中目标动作
[ ] 知道 max_strategy_budget_usdc
[ ] 知道 max_single_order_usdc
[ ] 知道 require_human_approval
```

agent 创建策略前：

```text
[ ] 已读取市场规则和盘口
[ ] 已写 agent_report 五个字段
[ ] 预算未超限
[ ] 单笔未超限
[ ] 已准备 risk-check
[ ] 已准备 simulate
[ ] 只提交 WAITING_HUMAN_CONFIRM
```

agent 汇报前：

```text
[ ] 说明执行了哪个 workflow
[ ] 说明用了哪些接口
[ ] 说明哪些权限限制生效
[ ] 说明哪些策略需要人类确认
[ ] 说明哪些结果是 SYSTEM_DERIVED
[ ] 不输出无权限字段
[ ] 不承诺确定性收益
```

## 16. 常见错误

### `capability disabled`

说明 Settings 里对应权限关闭。agent 应报告权限不足，不要尝试其他绕过路径。

### `risk check failed`

说明草案违反资金、市场、滑点、订单类型或其他限制。agent 应修改草案参数，或请求人类调整设置。

### `SELF_APPROVAL_FORBIDDEN`

说明 agent 试图批准自己提交的策略。正确做法是等待人类在 AgentMonitor 操作。

### `approval is not waiting for human confirm`

说明审批单状态已经变化，agent 应重新读取审批详情。

### `q is required`

说明新闻检索或主题研究缺少关键词。agent 应向用户请求明确事件/主题。

### 外部新闻源失败

说明 RSS / Search 源暂时不可用。agent 应报告外部源失败，并可建议稍后重试或换关键词。

## 17. 当前实现边界

已经落地：

- `/api/agent/capabilities`
- `/api/agent/activity`
- `/api/agent/event-graph`
- `/api/agent/event-graph/news/refresh`
- `/api/agent/event-graph/news/search`
- `/api/agent/event-graph/events`
- `/api/agent/event-graph/observations`
- `/api/agent/event-graph/patches/validate`
- `/api/agent/event-graph/change-requests`
- `/api/agent/event-graph/change-requests/{request_id}`
- `/api/agent/event-graph/core`
- `/api/agent/event-graph/core/events`
- `/api/agent/event-graph/core/finance`
- `/api/agent/event-graph/core/edges`
- `/api/agent/event-graph/core/expressions`
- `/api/agent/event-graph/core/versions`
- `/api/event-graph/change-requests/{request_id}/approve`
- `/api/event-graph/change-requests/{request_id}/reject`
- `/api/event-graph/change-requests/{request_id}/request-changes`
- `/api/event-graph/change-requests/{request_id}/apply`
- `/api/agent/market-scan`
- `/api/agent/market-scan/propose-strategies`
- `/api/agent/strategy-drafts/*`
- `/api/agent/approvals/*`
- `/api/agent/audit`
- `/api/agent/runs`
- `/api/agent/runs/{run_id}/steps`
- AgentMonitor 四视图：总览、EventGraph 变更、外接 Agent、审计日志
- agent run/step/audit 三层持久化，失败请求也会入库
- Graph Core 受控写入：Event / Finance / Edge / Expression current table 和 version table
- EventGraph Version History 查询

还没有落地：

- Event rollback UI。
- 并发冲突检测 / expected_version。
- 新闻可信度评分。
- 多 agent 分工调度。
- 独立 `agentctl` CLI。

当前 agent 写操作主要是：

```text
新闻 observation/event derived cache
EventGraph change request
Graph Core apply after human approval
策略 draft
审批 request
activity / audit
```

正式事实确认、资金确认和执行确认仍由人类负责。

## 18. 一句话执行顺序

agent 接入本系统时，默认按这个顺序工作：

```text
capabilities
  -> activity
  -> event/news/market/strategy observation
  -> create draft or batch propose
  -> risk-check
  -> simulate
  -> submit
  -> WAITING_HUMAN_CONFIRM
  -> 等待人类在 AgentMonitor 审批
```

agent 能完成研究和提案闭环，但不能越过人类确认和后端风控。
