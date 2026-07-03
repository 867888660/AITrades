# EventGraph

更新日期：2026-06-30

本文记录 EventGraph 页面、接口、新闻 observation 库以及当前已落地的 Graph Core 受控写入能力。EventGraph 现在包含两层：一层是 `derived_preview`，用于从 Polymarket 市场、新闻和信号即时派生可交互图谱；另一层是 Graph Core，用于保存经校验和审批策略处理后的正式 Event / Finance / Edge / Expression。

`derived_preview` 仍然存在，Polymarket 事件和新闻事件仍可能是系统派生对象；Graph Core 对象则必须通过外部 agent change request、系统校验和 Settings 中的 EventGraph 审批模式写入，并带有版本历史。默认模式为人工审核；设置为 `trusted_all` 时，通过校验且当前 apply 引擎支持的 change request 会由 system actor 自动 approve/apply。

## 功能定位

EventGraph 用来把原本分散在市场列表、新闻事件、资产行情和 agent 分析里的信息放到同一个图结构中。

第一版重点回答：

- 当前有哪些高热度事件。
- 哪些 Polymarket 市场直接定价或映射到同一个事件。
- 事件可能影响哪些金融资产、宏观变量或风险因子。
- 热度来自哪些可量化市场指标。
- 人和 agent 如何围绕同一张图查询、解释和继续扩展。

它不负责：

- 自动采集完整新闻全文。
- 让 agent 绕过校验和审阅直接修改正式事实库。
- 对事件结果、资产涨跌或交易方向做确定性预测。

## 页面入口

页面：

```text
GET /event-graph
GET /eventgraph
```

导航已接入 Dashboard、Ledger、AgentMonitor、Watchlist、Settings 等页面的侧边栏。

前端文件：

```text
templates/event_graph.html
static/event_graph.js
static/styles.css
static/echarts.min.js
```

后端文件：

```text
app.py
services/event_graph_service.py
services/event_news_service.py
services/polymarket_service.py
```

## 当前数据流

```text
EventGraph 页面
  -> static/event_graph.js
  -> GET /api/event-graph
  -> services/event_graph_service.build_event_graph()
  -> services.polymarket_service.search_markets()
  -> active market snapshot / process cache / Dictionary DB / Gamma fallback
```

分类芯片：

```text
EventGraph 页面
  -> GET /api/event-graph/categories
  -> services.event_graph_service.get_event_graph_categories()
  -> services.polymarket_service.list_market_categories()
```

新闻事件摄取：

```text
后台定时任务 / 手动 API / agent API
  -> services.event_news_service.refresh_news()
  -> Google News RSS / BBC RSS / Google News Search RSS
  -> Data/EventGraph.db
      -> event_graph_observations
      -> event_graph_events
      -> event_graph_event_observations
      -> event_graph_refresh_runs
  -> /api/event-graph 合入 NEWS_EVENT_DB 节点
```

Graph Core 受控写入：

```text
外部 agent
  -> POST /api/agent/event-graph/patches/validate
  -> POST /api/agent/event-graph/change-requests
  -> Settings 审批模式：人工 approve / reject / request-changes，或 system trusted auto approve/apply
  -> POST /api/event-graph/change-requests/{request_id}/apply
  -> Data/EventGraph.db
      -> graph_events / graph_event_versions
      -> graph_finance_nodes / graph_finance_versions
      -> graph_edges / graph_edge_versions
      -> graph_expressions / graph_expression_versions
  -> /api/event-graph 合入 GRAPH_CORE 节点和关系
```

正式入口仍是：

```text
http://127.0.0.1:5001/event-graph
http://127.0.0.1:5001/agent-monitor
```

默认分类筛选为：

```text
Politics,Crypto,Elections,Economics,Geopolitics,World
```

这样可以避免体育市场在默认视图里占据过多图谱空间。清空分类后可以查看全部可见市场。

## 图谱对象模型

### 节点类型

| 类型 | 含义 | 当前来源 |
| --- | --- | --- |
| `EVENT` | 系统派生出的 canonical event bucket | Polymarket `event_slug` / `EventID` / question fallback |
| `FINANCE` | 金融产品、市场、资产或风险变量 | Polymarket condition、关键词资产映射 |
| `SIGNAL` | 可量化热度或异常信号 | 市场成交量、流动性、价格变化等聚合指标 |

新闻事件节点的 `source_type` 为 `NEWS_RSS` 或 `NEWS_EVENT_DB`，详情里会保留 observation、来源、发布时间和 URL。

节点通用字段：

```json
{
  "id": "evt_xxx",
  "type": "EVENT",
  "label": "Iran Israel ceasefire",
  "subtitle": "Politics, World",
  "heat": 72.4,
  "status": "ACTIVE",
  "verification_status": "SYSTEM_DERIVED",
  "source_type": "POLYMARKET_EVENT",
  "details": {}
}
```

重要约定：

- `verification_status=SYSTEM_DERIVED` 表示系统自动派生，尚未人工确认。
- `verification_status=AUTO_COLLECTED` 表示来自市场源的自动采集对象，例如 Polymarket market node。
- `source=derived_preview` 表示本次返回是即时预览，不是持久图数据库快照。

### 边类型

| `relation_type` | `relation_class` | 含义 |
| --- | --- | --- |
| `DIRECTLY_PRICES` | `MAPPING` | Polymarket 市场直接定价或映射到某个派生事件 |
| `ASSOCIATED` | `IMPACT` | 热度信号或关键词资产与事件有关联 |

边通用字段：

```json
{
  "id": "edge_xxx",
  "source": "fin_pm_xxx",
  "target": "evt_xxx",
  "relation_type": "DIRECTLY_PRICES",
  "relation_class": "MAPPING",
  "confidence": 0.82,
  "strength": "HIGH",
  "reason": "Polymarket market condition is mapped to the derived event bucket.",
  "verification_status": "SYSTEM_DERIVED",
  "source_type": "EVENT_GRAPH_PREVIEW"
}
```

## API

### `GET /api/event-graph`

查询并返回图谱。

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `q` / `query` | 空 | 关键词，传给现有 Polymarket 市场搜索。 |
| `category` | 空 | 类别筛选，支持逗号分隔。 |
| `sort` | `volume24h` | 排序字段，透传给市场搜索。 |
| `order` | `desc` | 排序方向。 |
| `limit` | `80` | 市场候选数量，后端限制为 10 到 180。 |
| `refresh` | `0` | 为 `1/true/yes/on` 时强制刷新市场缓存。 |

返回结构：

```json
{
  "ok": true,
  "source": "derived_preview",
  "generated_at": "2026-06-25T00:00:00Z",
  "query": {
    "q": "",
    "category": "Politics,Crypto",
    "sort": "volume24h",
    "order": "desc",
    "limit": 80
  },
  "summary": {
    "events": 17,
    "finance_nodes": 58,
    "signals": 17,
    "edges": 121,
    "markets": 50,
    "max_heat": 86.4
  },
  "nodes": [],
  "edges": [],
  "event_rankings": []
}
```

`event_rankings` 是按热度排序后的 Event 节点前 30 条，供左侧榜单和 agent 快速扫描使用。

### `GET /api/event-graph/categories`

返回当前可见 Polymarket 市场类别计数。

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `limit` | `120` | 返回类别数量，后端限制为 1 到 240。 |

返回结构：

```json
{
  "ok": true,
  "data": [
    { "name": "Politics", "count": 684 },
    { "name": "Crypto", "count": 147 }
  ]
}
```

### 新闻事件 API

新闻事件先进入 observation 层，再聚合成 derived event。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/event-graph/news/status` | 查看新闻事件库、数据源、刷新记录和后台线程状态。 |
| `POST` | `/api/event-graph/news/refresh` | 抓取全球热门新闻 RSS 并写入 `EventGraph.db`。 |
| `POST` | `/api/event-graph/news/search` | 按 `q` 检索当下新闻并写入 `EventGraph.db`。 |
| `GET` | `/api/event-graph/events` | 查询新闻事件库里的 derived events。 |
| `GET` | `/api/event-graph/observations` | 查询原始新闻 observations，可按 `event_id` 过滤。 |

agent 受控入口：

| 方法 | 路径 | capability |
| --- | --- | --- |
| `GET` | `/api/agent/event-graph` | `event.read` |
| `GET` | `/api/agent/event-graph/news/status` | `event.read` |
| `GET` | `/api/agent/event-graph/events` | `event.read` |
| `GET` | `/api/agent/event-graph/observations` | `event.read` |
| `POST` | `/api/agent/event-graph/news/refresh` | `event.news.refresh` |
| `POST` | `/api/agent/event-graph/news/search` | `event.news.search` |

Graph Core agent 受控入口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/agent/event-graph/patches/validate` | 校验 EventGraph patch，返回风险、错误、警告和 target refs。 |
| `POST` | `/api/agent/event-graph/change-requests` | 提交变更请求，默认进入 `PENDING` 或 `NEEDS_CHANGES`。 |
| `GET` | `/api/agent/event-graph/change-requests` | 查看变更请求列表。 |
| `GET` | `/api/agent/event-graph/change-requests/{request_id}` | 查看单个变更请求详情。 |
| `GET` | `/api/agent/event-graph/core` | 查询 Graph Core 当前态汇总。 |
| `GET` | `/api/agent/event-graph/core/events` | 查询正式 events。 |
| `GET` | `/api/agent/event-graph/core/finance` | 查询正式 finance nodes。 |
| `GET` | `/api/agent/event-graph/core/edges` | 查询正式 edges。 |
| `GET` | `/api/agent/event-graph/core/expressions` | 查询正式 expressions。 |
| `GET` | `/api/agent/event-graph/core/versions` | 查询 event / finance / edge / expression 的版本历史。 |

人类审核入口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/event-graph/change-requests/{request_id}/approve` | 批准变更请求。 |
| `POST` | `/api/event-graph/change-requests/{request_id}/reject` | 拒绝变更请求。 |
| `POST` | `/api/event-graph/change-requests/{request_id}/request-changes` | 要求外部 agent 修改。 |
| `POST` | `/api/event-graph/change-requests/{request_id}/apply` | 应用到 Graph Core，并写版本表。 |

`GET /api/agent/capabilities` 会返回 `event_graph_capabilities`，供外部 agent 发现这些入口。

## 热度计算

当前热度是启发式评分，范围约为 1 到 100。

单个 Polymarket market 的 heat 由以下字段组合：

- `volume_24h`
- `volume`
- `liquidity`
- `abs(price_change_24h)`
- active bonus
- spread penalty

事件 heat 取该事件 bucket 内最高 market heat，再叠加少量 market 数量 bonus。

新闻事件 heat 由 RSS 排名、新闻发布时间、来源数量和 observation 数量共同决定。它适合做“当下值得看什么”的排序，不应直接当作事实可信度或交易方向。

这个指标适合做排序和交互焦点，不适合当作交易信号直接使用。后续接入 Stock、Binance、新闻事件后，应把热度拆成多种 signal，例如：

```text
market_heat
news_heat
asset_volatility_heat
social_attention_heat
liquidity_shock
price_dislocation
```

## 去重与大事件聚合

当前聚合优先级：

1. `event_slug`
2. `EventID`
3. 规范化后的 market question

这样可以尽量把同一 Polymarket event 下的多个子市场合并成一个更大的 `EVENT` 节点，而不是每个 market 都生成一个事件。

当前瓶颈：

- 不同数据源之间还没有统一 canonical event ID。
- question fallback 仍可能把相似但不完全相同的问题拆成多个事件。
- 关键词资产映射只是弱关联，不等同于因果关系。
- 缺少人工确认、别名表和历史合并记录。

后续建议新增：

```text
event_canonical_id
event_aliases
event_merge_history
event_source_observations
event_versions
```

AI 可以参与候选合并，但正式合并应保留：

- 原始观察记录。
- 合并前后的 ID。
- 合并原因。
- actor。
- 时间戳。
- 可回滚版本。

## 人与 Agent 交互边界

当前页面是图谱查询界面。Agent 可以读取 `/api/event-graph` 或 `/api/agent/event-graph` 的结果，用于：

- 扫描高热度事件。
- 找出事件关联市场。
- 读取 `details.top_markets` 作为进一步策略草案输入。
- 解释某个 Event 的热度来源。
- 发现可能相关的资产或风险变量。

agent 不能直接写 Graph Core 数据表，也不能自我审批。当前开放的是受控写入闭环：

- `event.news.refresh`：刷新全球热门新闻，写入 observation / derived event cache。
- `event.news.search`：按关键词检索新闻，写入 observation / derived event cache。
- `event.graph.patch.validate`：校验 patch。
- `event.graph.change_request`：提交 Graph Core 变更请求。

Graph Core apply 默认由 human/admin 审阅后触发；当 Settings 的 EventGraph 审批模式为 `trusted_all` 时，通过校验的 change request 可由 `system` actor 自动 approve/apply。写入时会保存 current table 和 version table，并保留 `request_id`、`run_id`、actor、before、after、patch item。

当前系统分成两层：

```text
EventGraph Core
  - 存储、版本、查询、回滚、审计

External Agent / CLI Ingestion
  - 定时采集
  - 清洗候选
  - 去重建议
  - 关系建议
  - 生成 patch/proposal
```

写操作走 change request，而不是让 agent 直接覆盖正式图谱：

```text
agent 生成 event_patch
  -> 系统校验 patch
  -> 系统保存为 pending change request
  -> Settings 审批模式决定人工 review 或 system trusted auto apply
  -> apply 到 Graph Core 新版本
  -> 保留 before / after / patch item
```

当前已支持：

- Event：`create / update / archive / merge`。
- Finance：`create / update / archive`。
- Edge：`create / update / delete`，delete 实际归档。
- Expression：`create / update / archive`。
- 删除改为 archive。
- 合并保留 merge history，并重定向相关 edge。
- EventGraph 详情面板可加载 Version History。

## CLI 与内置 Agent 的取舍

当前实现保留页面、REST API、AgentMonitor 审计和外部 agent change request 工作台，不再建设内置 EventGraph 调查 agent。

推荐方向：

- 系统核心只提供稳定查询和写入能力。
- CLI 封装低风险能力，便于 Codex、脚本、自动任务和人类复用。
- 外接 agent 走 `/api/agent/*` 受控接口，权限、风控和审计边界不变。
- 人类通过 AgentMonitor 审核并应用 Graph Core 变更。

这样后续无论使用 Codex、独立 agent、定时任务，还是手动脚本，都走同一套能力边界。

候选 CLI 形态：

```bash
eventgraph query --category Politics,Crypto --sort volume24h --limit 80 --json
eventgraph event get --id evt_xxx --json
eventgraph proposal create --from-news news.json --json
eventgraph proposal apply --id prop_xxx --snapshot --json
```

## 外部 Agent 与 AgentMonitor

当前路线不再建设内置 EventGraph investigation agent。外部 agent、Codex、脚本或人类都通过同一套受控接口完成研究和变更请求。

外部 agent 可以做：

```text
读取 EventGraph derived preview
读取 Graph Core 当前态和版本历史
检索新闻 observation / derived event
生成 patch
提交 change request
解释证据和人审重点
```

外部 agent 不能做：

```text
直接写 Graph Core 表
自我 approve / apply
跳过 patch validation
把 SYSTEM_DERIVED 对象当成人工核验事实
绕过审计修改高风险关系
```

所有受控调用仍写入调用链：

```text
agent_runs
  -> agent_run_steps
  -> agent_audit_events
```

AgentMonitor 中的显示边界：

- 总览：显示待确认策略、草案、EventGraph 变更和风控阻断计数。
- EventGraph 变更：提交 patch、校验、查看 change request、approve / reject / request-changes / apply。
- 外接 Agent：显示外部策略 agent 的最近活动、草案和审批。
- 审计日志：保留完整流水，支持搜索、类别筛选、`run_id` 追踪和清除当前筛选。

## 当前边界与后续路线

已完成：

- EventGraph 页面。
- ECharts 图谱交互。
- 分类筛选、关键词查询、排序、数量限制、强刷。
- Event / Finance / Signal 三类节点。
- Event 排行榜。
- 节点详情、来源、热度指标、top markets、关系列表。
- REST API。
- 新闻事件库 `Data/EventGraph.db`。
- 全球热门新闻 RSS 刷新。
- 指定关键词新闻搜索摄取。
- `/api/agent/event-graph/*` 受控 agent 入口。
- AgentMonitor EventGraph 变更 / 外接 Agent / 审计日志分栏。
- Agent run/step/audit 调用链基础表与读取接口。
- 设置页 LLM provider/model/API key 配置。
- Graph Core：`graph_events`、`graph_finance_nodes`、`graph_edges`、`graph_expressions` 及对应 version 表。
- 外部 agent change request 校验、提交、人审和 apply。
- `event_merge`、edge 重定向、expression 写入和 Version History 查询。

待做：

- 把 Stock、Binance、宏观日历和社媒也接入 observation 层。
- 更强 canonical event ID、alias 搜索和冲突检测。
- 回滚 / revert UI。
- CLI ingestion。
- 多来源 signal 权重。
- 关系置信度校准。
- 时间维度：事件热度随时间增加、衰减、反转和归档。
- 更强新闻源：GDELT、NewsAPI、Bing News、Reuters/Bloomberg/Factiva 等付费或授权终端。

## 验证记录

本次实现后做过以下验证：

```powershell
python -m py_compile app.py services\event_graph_service.py
node --check static\event_graph.js
```

Flask test client 验证过：

```text
GET /event-graph
GET /api/event-graph
GET /api/event-graph/categories
GET /api/event-graph/news/status
GET /api/event-graph/events
```

示例筛选：

```text
/api/event-graph?limit=50&sort=volume24h&category=Politics,Crypto,Elections,Economics,Geopolitics,World
```

当时返回摘要：

```json
{
  "edges": 121,
  "events": 17,
  "finance_nodes": 58,
  "markets": 50,
  "max_heat": 86.4,
  "signals": 17
}
```

## 2026-07-01 逻辑关系更新

本次补充了 EventGraph 的逻辑候选层，但仍保持两层结构：

- `derived_preview`：展示系统派生的热门事件、Polymarket 市场、新闻事件、信号和逻辑候选，统一视为 `UNVERIFIED / DERIVED_PREVIEW`。
- `Graph Core`：只保存通过 patch validation、change request 和审批策略处理后的 Event / Finance / Edge / Expression。

新增结构化事件语义字段：

```text
subject
predicate
object
comparator
threshold
unit
time_window_start
time_window_end
jurisdiction
resolution_rule
resolution_source
outcome_space_id
```

`services/event_graph_logic.py` 负责确定性逻辑候选生成，不引入内置 LLM agent。当前自动候选范围收敛为：

- `IMPLIES`：同一 family 内的阈值/区间包含，例如 BTC >= 150000 implies BTC >= 100000。
- `DISJOINT`：同一 single-winner outcome_space 内只能有一个 outcome 成立。
- `EQUAL`：结构化语义相同的候选，只作为 proposal，仍需人工审核。

`OVERLAP` 暂不自动生成；AND / OR / NOT / DIFFERENCE 作为 Expression 按需创建。Expression 获批写入后，系统自动派生 `SYSTEM_DERIVED` 逻辑边：

- AND output implies each input。
- OR each input implies output。
- NOT input disjoint output。
- DIFFERENCE output implies left and disjoint right。

Patch validation 现在会检查逻辑冲突和表达式形状，包括：

- `EQUAL` 与 `DISJOINT` 同时作用于同一 pair 时阻断。
- 双向 `IMPLIES` 提醒应考虑合并为 `EQUAL`。
- `A IMPLIES B`、`B IMPLIES C` 且 `A DISJOINT C` 时阻断。
- `NOT` 输入数量不为 1、`DIFFERENCE` 输入数量不为 2、缺少 output event 时阻断。

审批策略边界：

- Settings 仍可配置 EventGraph approval mode。
- 新闻/普通低风险变更可以按 policy 决定是否 auto apply。
- 逻辑关系和 Expression 在当前版本即使配置 `trusted_all` 也不会自动 apply，必须人工确认后进入 Graph Core。

页面更新：

- EventGraph 顶部新增 Relation Mode，可按 All / Logic / Impact / Mapping / Evidence / Expression 查看。
- 详情面板展示结构化 Semantic 字段。
- Summary 增加 Logic candidate 统计。

## 2026-07-02 Reasoning Layer 更新

上一版把 EventGraph 的重点放在 strict logic 上，但这还不能覆盖“大模型推演对话”的真实使用方式。比如用户问：

```text
如果霍尔木兹海峡继续关闭，会怎么样？
```

这类问题通常不是严格逻辑。它更像：

```text
Scenario:
  Hormuz remains closed

Reasoning / Impact:
  Hormuz closure -> oil supply risk up
  oil supply risk up -> Brent risk premium up
  Brent risk premium up -> inflation expectation up
  inflation expectation up -> Fed cut probability down
```

这些关系不能写成 `IMPLIES`，因为它们不是必然推出；它们必须带置信度、时间范围、机制、证据和不确定性。

### 污染正式知识推理的定义

“污染”不是指信息本身可能出错，而是指某条写入 Graph Core 的关系会被后续系统当作稳定前提使用：

```text
用于自动传递
用于自动推导
用于节点合并
用于去重和互斥判断
用于策略筛选或风控过滤
```

因此，污染风险按关系是否会变成“硬前提”划分：

| 风险层级 | 定义 | 处理方式 |
| --- | --- | --- |
| 低风险 | 不参与正式推理，只是 observation / snapshot / heat / evidence | 可自动写入或自动 apply |
| 中风险 | 规则清楚但可能需要抽查，如阈值包含、同 outcome_space 互斥 | 批量确认、抽查后自动 |
| 高风险 | 会影响合并、互斥、跨平台等价、Expression 定义或因果推演 | 人工确认或高权限确认 |
| 禁止硬逻辑化 | LLM 主观推演、概率影响、宏观传导链 | 只能进入 Reasoning Layer，不能写成 LOGICAL |

### 关系分层

EventGraph 现在按 relation_class 区分不同语义，不再把所有关系塞进 `LOGICAL`。

| relation_class | 含义 | 是否参与严格推导 |
| --- | --- | --- |
| `LOGICAL` | 严格真假逻辑，如 `EQUAL` / `IMPLIES` / `DISJOINT` / `OVERLAP` | 是 |
| `MAPPING` | 市场、资产、事件的映射，如 `DIRECTLY_PRICES` | 否 |
| `EVIDENCE` | 证据支持或反驳，如 `SUPPORTED_BY` / `CONTRADICTED_BY` | 否 |
| `IMPACT` | 方向性影响，如 `POSITIVE_IMPACT` / `INCREASES_PROBABILITY` | 否 |
| `CAUSAL` | 机制假设，如 `RISK_CHANNEL` / `CONTRIBUTES_TO` | 否 |
| `SCENARIO` | 场景推演链，如 `ASSUMES` / `CONDITIONAL_ON` / `LEADS_TO` | 否 |
| `MARKET_MOVE` | 市场异动观察，如 odds / price / volume move | 否 |

严格规则：

```text
LOGICAL 才允许自动真假推导。
Reasoning / Impact / Scenario 只能表达概率、方向、机制或观察。
任何 LLM 推演关系不得写成 IMPLIES，除非有明确可验证的集合包含规则。
```

### Expression 的精确定义

Expression 是“组合事件定义”，不是预测、不是影响关系、不是因果关系。

例如：

```text
A = BTC > 100000
B = Fed 2026 至少降息 2 次
C = AND(A, B)
```

这只表示：

```text
C 成立，当且仅当 A 与 B 同时成立。
```

因此 Expression 获批后，系统可以机械派生：

```text
C IMPLIES A
C IMPLIES B
```

派生边不是新的判断，不需要逐条人工确认；需要确认的是 Expression 本身是否值得进入正式图谱。

禁止把下面这种推演写成 Expression：

```text
Hormuz closed AND Brent > 100
```

除非用户明确需要创建这个组合假设，或 Polymarket 已经有对应组合市场，或策略研究明确依赖该组合条件。

### 霍尔木兹示例应如何建模

错误建模：

```text
Hormuz closed IMPLIES Brent > 100
```

正确建模：

```text
S = Hormuz remains closed for 7 days

S POSITIVE_IMPACT Oil supply risk
Oil supply risk POSITIVE_IMPACT Brent price
Brent price INCREASES_PROBABILITY Inflation expectation up
Inflation expectation up DECREASES_PROBABILITY Fed rate cut probability
```

这些边必须带：

```text
confidence
time_horizon
mechanism
assumptions
evidence_refs / evidence_summary
source_agent
run_id
```

这种 Reasoning Layer 可以被 agent 和人类用于推演、解释和策略研究，但不得参与 strict logic 的自动传递。
