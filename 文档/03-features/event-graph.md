# EventGraph

更新日期：2026-06-30

本文记录 EventGraph 页面、接口、新闻 observation 库以及当前已落地的 Graph Core 受控写入能力。EventGraph 现在包含两层：一层是 `derived_preview`，用于从 Polymarket 市场、新闻和信号即时派生可交互图谱；另一层是 Graph Core，用于保存经校验和人审后的正式 Event / Finance / Edge / Expression。

`derived_preview` 仍然存在，Polymarket 事件和新闻事件仍可能是系统派生对象；Graph Core 对象则必须通过外部 agent change request、系统校验、人工审核和 apply 流程写入，并带有版本历史。

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
  -> 人类 approve / reject / request-changes
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

Graph Core apply 只能由 human/admin 审阅后触发。写入时会保存 current table 和 version table，并保留 `request_id`、`run_id`、actor、before、after、patch item。

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
  -> 人类 approve / reject / request-changes
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
