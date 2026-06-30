# Polymarket DataTube — 工作台文档

---

## 一、项目概述

Polymarket DataTube 是一个面向 Polymarket 预测市场的策略监控与可视化终端。

核心功能：
- 实时采集 Polymarket 市场 WebSocket 行情，写入本地 SQLite
- 策略仓位、盈亏、均价的持续追踪与图表展示
- 多市场对比（主市场 + 观察市场）
- 外部行情 overlay（加密货币 / 美股）
- 工作台预设管理、策略参数编辑
- 加密货币（Binance 优先、CoinGecko 价格 fallback）与股票（Finnhub）实时报价采集
- 策略注册表 CRUD、策略代码参数自动识别、虚拟盘调度与运行审计

运行方式：Flask HTTP 服务，监听 `127.0.0.1:5001`，启动时同步拉起 WebSocket 行情同步线程、外部行情采集线程与虚拟盘调度线程。

当前工作台入口只有一个：`templates/strategy_workspace.html`。页面加载 `workspace_v3.css`、`strategy_workspace_v2.js` 和 `workspace_v3_patch.js`，其中 V2 脚本仍是核心逻辑，V3 文件负责当前视觉和展示层补丁。

---

## 二、目录结构

```
polymarket_datatube/
├── app.py                          # Flask 主入口，所有 HTTP 路由
├── main.py                         # WebSocket 行情同步主逻辑
├── config.json                     # 外部 API 配置（Gamma、Data API 地址等）
├── web_settings.json               # 运行时可编辑的 Web 设置
├── requirements-web.txt            # Web 层基础依赖；实时 WS 链路还需 websockets / websockets_proxy / tqdm / pymongo
│
├── services/
│   ├── config_loader.py            # 配置加载、路径解析
│   ├── polymarket_service.py       # 策略详情、市场搜索、持仓、概览
│   ├── strategy_registry_service.py # strategy_registry + strategy_legs CRUD
│   ├── strategy_data_source.py      # 统一策略数据源、虚拟盘 DDL
│   ├── strategy_chart_service.py   # 全量图表数据（/chart，初始化/重载）
│   ├── strategy_chart_delta_service.py  # 增量图表数据（/chart-delta）
│   ├── strategy_workspace_service.py    # 工作台初始化数据（/workspace）
│   ├── strategy_event_service.py   # 策略事件列表
│   ├── strategy_audit_store.py     # 策略 tick / event / action 审计
│   ├── strategy_stats_store.py     # 策略指标 DB 路径解析与快照读取
│   ├── strategy_profit_engine.py   # 仓位计算、盈亏引擎
│   ├── strategy_settings_service.py # 策略参数 schema 与更新
│   ├── order_store.py              # 实盘订单状态机与订单事件
│   ├── virtual_runner.py           # 虚拟盘定时调度
│   ├── virtual_execution.py        # 虚拟成交与账户/持仓/订单写入
│   ├── virtual_context_builder.py  # 策略 UseData 注入
│   ├── workspace_preset_service.py # 工作台预设 CRUD
│   ├── ws_market_sync_service.py   # WebSocket 行情同步服务封装
│   ├── realtime_collector.py       # 加密 / 股票实时报价采集线程
│   ├── polymarket_dictionary_service.py  # 市场字典刷新与状态
│   ├── market_deltas_cleanup.py    # market_deltas 保留期清理
│   ├── backtest_service.py         # 回测占位服务
│   ├── crypto_service.py           # Binance 报价拉取
│   ├── finance_service.py          # Finnhub 报价拉取
│   ├── http_client.py              # 共享 requests Session
│   └── sqlite_store.py             # SQLite 宽表写入工具
│
├── templates/
│   ├── index.html                  # 首页（策略列表 / 概览）
│   ├── strategy_workspace.html     # 策略工作台主页面
│   ├── watchlist.html              # 自选列表
│   └── settings.html               # 设置页
│
├── static/
│   ├── strategy_workspace_v2.js     # 工作台核心逻辑：状态、请求、图表、设置、预设
│   ├── workspace_v3_patch.js        # 工作台 V3 展示层补丁：布局、事件流、summary、右侧面板
│   ├── workspace_v3.css             # 工作台 V3 视觉样式
│   ├── polymarket_links.js          # Polymarket 链接构造
│   └── styles.css                   # 首页和通用样式
│
├── Data/
│   ├── polymarket_realtime.db      # WebSocket 实时行情（主库）
│   ├── PolyMarketMonitoring.db     # 策略注册表、策略腿、虚拟盘、审计表；旧监控表保留为历史/fallback
│   ├── PolyMarketOrderList.db      # 本地订单记录
│   ├── PolyMarketDictionary.db     # 市场字典缓存
│   └── market_data.db              # 历史行情备用库
│
└── strategy_metrics_dbs/           # 每个策略独立的指标 DB（按 condition_id 命名）
```

---

## 三、启动流程

```
app.py __main__
  ├── ws_market_sync.start()     # 后台线程：WebSocket 行情同步
  ├── collector.start()          # 后台线程：加密 / 股票报价采集
  ├── virtual_runner.start()     # 后台线程：Virtual 策略沙盒运行与虚拟成交
  └── app.run(127.0.0.1:5001)
```

`ws_market_sync` 内部调用 `main.py`，拉取全量活跃市场后按概率过滤、持仓过滤、策略监控过滤，建立 WebSocket 订阅，将行情写入 `polymarket_realtime.db`。

`virtual_runner` 每轮读取 `strategy_registry.mode = 'Virtual'` 的策略，组装 `UseData`，调用 `参考/SandboxRun.py` 执行策略代码，并将 FunctionJson 动作落到虚拟盘表与审计表。

---

## 四、HTTP API 一览

### 页面路由

| 路径 | 说明 |
|------|------|
| `GET /` | 首页（策略列表 / 概览） |
| `GET /settings` | 设置页 |
| `GET /watchlist` | 自选列表 |
| `GET /strategies/<row_id>/workspace` | 策略工作台 |

### 系统 / 设置

| 路径 | 说明 |
|------|------|
| `GET /api/health` | 服务健康状态 |
| `GET /api/settings` | 读取 web_settings.json |
| `POST /api/settings` | 保存 web_settings.json |

### 策略注册 / 策略代码

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/strategy-codes` | 列出 `StrategyCode/` 下可选策略代码 |
| GET | `/api/strategy-codes/<code_name>/inputs` | 读取策略代码 `Inputs` 声明，供弹窗生成参数字段 |
| GET | `/api/registry/strategies` | 列出 `strategy_registry` 策略（含 legs） |
| POST | `/api/registry/strategies` | 创建策略 |
| GET | `/api/registry/strategies/<id>` | 获取单个策略 |
| PUT | `/api/registry/strategies/<id>` | 更新策略基础信息与 input_json |
| PATCH | `/api/registry/strategies/<id>/mode` | 切换 Stop / Virtual / Real |
| PUT | `/api/registry/strategies/<id>/legs` | 替换策略腿 |
| DELETE | `/api/registry/strategies/<id>` | 删除策略，级联删除 legs |

### 策略工作台

| 路径 | 说明 |
|------|------|
| `GET /api/polymarket/strategies/<row_id>/workspace` | 工作台初始化数据 |
| `GET /api/polymarket/strategies/<row_id>/chart` | 全量图表数据（初始化 / 结构重载） |
| `GET /api/polymarket/strategies/<row_id>/chart-delta` | 增量图表数据（运行态轮询） |
| `GET /api/polymarket/strategies/<row_id>/events` | 策略事件列表 |
| `POST /api/polymarket/strategies/<row_id>` | 更新策略参数 |
| `PATCH /api/registry/strategies/<row_id>/mode` | 更新策略运行模式：`Stop` / `Virtual` / `Real` |
| `PATCH /api/registry/strategies/<row_id>/state-store/machine` | 更新策略状态机 state |

### 工作台预设

| 路径 | 说明 |
|------|------|
| `GET /api/polymarket/workspace-presets` | 列出预设 |
| `POST /api/polymarket/workspace-presets` | 保存预设 |
| `GET /api/polymarket/workspace-presets/<preset_id>` | 读取预设 |
| `DELETE /api/polymarket/workspace-presets/<preset_id>` | 删除预设 |

### 市场 / 持仓

| 路径 | 说明 |
|------|------|
| `GET /api/polymarket/market-categories` | 返回 active 市场类别计数，供首页类别多选条使用 |
| `GET /api/polymarket/markets` | 搜索市场；支持 `category` 多选、`sort/order` 后端全量排序 |
| `GET /api/polymarket/dictionary` | 字典状态 |
| `POST /api/polymarket/dictionary/update` | 触发字典刷新 |
| `GET /api/live/polymarket/dictionary` | SSE：字典刷新进度 |
| `GET /api/overview` | 概览（钱包持仓 + collector 状态） |
| `GET /api/polymarket/holdings` | 钱包持仓列表 |
| `GET /api/polymarket/strategies` | 策略列表 |
| `GET /api/polymarket/strategies/<row_id>` | 策略详情 |
| `GET /api/live/strategies/<row_id>/workspace` | SSE：策略工作台实时推送 |

### 实时报价

| 路径 | 说明 |
|------|------|
| `GET /api/realtime/state` | collector 完整状态 |
| `GET /api/realtime/crypto` | 加密报价快照 |
| `GET /api/realtime/finance` | 股票报价快照 |
| `GET /api/crypto/quotes` | 实时拉取 Crypto 报价；Binance 失败时使用 CoinGecko fallback |
| `GET /api/finance/quotes` | 实时拉取 Finnhub 报价 |

`/api/realtime/crypto` 与 `/api/realtime/finance` 同时暴露两个时间字段：`last_run_at` 是最近一次刷新尝试时间，`ts_utc` 是当前数据本身的时间。若 `stale=true` 或 `status=degraded`，表示刷新线程仍在运行但当前展示的是 fallback/历史数据，前端必须明确标注，不能显示为“稳定运行”。

### 虚拟盘

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/virtual/strategies/<id>/account` | 虚拟账户状态（cash / equity / pnl / fees） |
| GET | `/api/virtual/strategies/<id>/positions` | 虚拟持仓列表 |
| GET | `/api/virtual/strategies/<id>/orders` | 虚拟订单流水（支持分页） |
| GET | `/api/virtual/strategies/<id>/events` | 事件流（支持 event_type 过滤） |
| GET | `/api/virtual/strategies/<id>/ticks` | Tick 运行日志列表 |
| POST | `/api/virtual/strategies/<id>/reset` | 重置虚拟账户 |

虚拟盘 API 独立于工作台图表链路，不共享 cursor 机制。

---

## 五、核心服务说明

### strategy_chart_service.py — 全量图表服务

入口：`get_strategy_chart(row_id, args)`

职责：首次加载、时间范围切换、interval 切换、主市场切换、观察市场集合变化、overlay 配置变化、手动全量刷新。

返回结构：`meta` / `panels` / `series` / `rows` / `events`

内置 series：

| key | panel | render | unit |
|-----|-------|--------|------|
| yes_bid / yes_ask / no_bid / no_ask | main | line | price |
| yes_mid / no_mid | main | line | price |
| yes_position / no_position | positions | step | ratio |
| yes_qty / no_qty | sizes | step | qty |
| yes_avg / no_avg | averages | line | price |
| strategy_pnl | pnl | line | currency |

价格数据来源优先级：`polymarket_realtime.db` → `PolyMarketMonitoring.db` → Gamma API

稀疏行情处理：

- `_load_price_samples()` 优先读取 `market_deltas` 的区间内数据。
- 如果区间内样本很少，会额外读取 `from_ts` 之前最近的 yes/no snapshot，并在区间起点生成 carry-forward seed。
- 这样可以让 24h/6h 范围内只有 1 个新 bucket 的市场至少形成可见时间跨度。
- 前端仍以 `meta.from` / `meta.to` 作为横轴范围，并在 `rows < 2` 时强制显示 symbol。

资金字段说明：

- `initial_capital`、`profit_roll_ratio`、`realized_profit`、`strategy_bankroll` 仍属于策略设置、摘要或预留资金逻辑。
- 它们不再作为工作台图表副指标暴露，避免资金组占用图表空间但没有有效时间序列。

---

### strategy_chart_delta_service.py — 增量图表服务

入口：`get_strategy_chart_delta(row_id, args)`

职责：运行态增量更新，按 5 条数据流分段返回。

| stream | 内容 | 刷新频率 |
|--------|------|---------|
| `price` | 主市场 yes/no bid/ask/mid | 2s |
| `stats` | 仓位、qty、avg、pnl | 5s |
| `watch_markets` | 观察市场简化价格 | 10s |
| `overlay` | 外部行情 | 20s |
| `events` | 策略事件 | 15s |

每个流独立 cursor，互不影响。cursor 语义：`ts > cursor`，允许修正最后一个 bucket，返回 `next_cursor`。

---

### strategy_workspace_service.py — 工作台初始化服务

入口：`get_strategy_workspace(row_id, include_events)`

返回：策略详情、settings_schema、chart_defaults、chart_capabilities、market_context、workspace_presets、source_statuses、recent_events（最近 20 条）

模式与状态字段：

- `strategy.mode` 是工作台 header mode select 的唯一来源。
- `strategy.machine_state` / `strategy.state` 表示策略状态机当前 state，选项来自 `StateMachineSchema`。
- 工作台切换 mode 后走 `PATCH /api/registry/strategies/<row_id>/mode`，再重新拉取 `/workspace` 同步 summary、source 与事件状态。
- 工作台切换 state 后走 `PATCH /api/registry/strategies/<row_id>/state-store/machine`，写入 `strategy_state.namespace = machine`。

事件来源：

- 默认事件流以策略事件和虚拟盘事件为主。
- 市场级 `BOOK` 事件不应默认混入工作台事件流，否则会和策略 Print / Action 语义混淆。
- 前端展示层会对连续重复 Print 做合并计数，后端历史数据不因展示合并而删除。
- Actions 事件由 `strategy_event_service.py` 从 `strategy_action_events` 审计表读取并转换，展示策略原始动作及执行状态。
- Trades 事件由 `strategy_event_service.py` 从 `strategy_virtual_orders` 读取并转换，只展示 `filled` 实际成交；`blocked` / `failed` 归入 Actions。
- 前端维护 `_fullEventsList` 全局缓存，SSE 推送（`appendWorkspaceEvent`）合并进完整列表而非替换，消除 Trades 标签闪现消失问题。

---

### polymarket_service.py — 市场与策略数据

主要函数：`fetch_strategy_detail` / `list_market_categories` / `search_markets` / `fetch_wallet_positions` / `get_overview` / `resolve_market_selection`

市场查询：`search_markets()` 先做关键词和类别过滤，再按 `volume24h`、`volume`、`liquidity`、`spread`、`end_date`、`updated_at` 等字段对完整候选集合排序，最后按 `limit` 截断，避免前端只对当前页做局部排序。

缓存：市场列表 60s，CLOB `/book` 盘口 2s，WebSocket 快照 2s（新鲜度阈值 `_WS_FRESHNESS_SECONDS=180`），主动市场快照文件 300s，字典索引 60s，Gamma API 300s，实时仓位 5s（过期后允许使用 60s 内旧值）

策略价格优先级：CLOB `/book` 当前盘口 > 本地实时库 `markets_state` / `market_deltas` > Gamma / 字典快照。`workspace_fast_path` 传入 `_match_strategy_market` 结果，工作台、Dashboard、Legs Snapshot 和当前图表点使用同一当前盘口对象。

本地 WS 健康不能只看线程是否存活。`/api/health` 的 `ws_market_sync.monitor` 暴露 `token_count`、`shard_connected`、`last_subscribe_at`、`last_msg_at`、`last_update_at`、`last_ws_error`、`msg_count`、`update_count`。如果 `thread_alive=true` 但 `last_update_at` 长时间不变，应按本地行情过期处理。WS 握手失败或断线时，监控器会用 CLOB REST `/book` 对当前分片 token 做 fallback 刷新并写入 `book_rest_fallback`。

---

## 六、数据库说明

| 数据库 | 用途 |
|--------|------|
| `polymarket_realtime.db` | WebSocket 实时行情，`market_deltas` 表 |
| `PolyMarketMonitoring.db` | 策略注册、策略腿、虚拟盘表、运行审计表；旧监控表保留为历史与兼容 fallback |
| `PolyMarketOrderList.db` | 新 `orders` / `order_events` 与旧 `polyMarket_OrderList` 共存，用于本地订单与仓位回补 |
| `PolyMarketDictionary.db` | 市场字典缓存 |
| `strategy_metrics_dbs/<id>.db` | 每个策略独立的指标时序数据 |

---

## 七、价格字段说明

Polymarket 为二元市场（Yes / No），价格范围 0–1。

| 字段 | 说明 |
|------|------|
| `yes_bid` / `yes_ask` | Yes 边最优买价 / 卖价 |
| `no_bid` / `no_ask` | No 边最优买价 / 卖价（= 1 - yes_ask / yes_bid） |
| `yes_mid` / `no_mid` | 中间价（(bid+ask)/2，或 last_price 兜底） |

二元互补规则由 `_fill_binary_complements()` 自动填充缺失边。

---

## 八、配置文件说明

### config.json

```json
{
  "api": { "gamma_base": "https://gamma-api.polymarket.com" },
  "holdings": { "data_api": "https://data-api.polymarket.com" }
}
```

### web_settings.json（运行时可编辑）

| 字段 | 说明 |
|------|------|
| `strategy_monitoring_db_path` | 策略监控 DB 路径 |
| `market_realtime_db_path` | 实时行情 DB 路径 |
| `order_list_db_path` | 订单 DB 路径 |
| `polymarket_dictionary_db_path` | 市场字典 DB 路径 |
| `strategy_metrics_db_dir` | 策略指标 DB 目录 |
| `crypto_symbols` | Crypto 采集 symbol 列表；默认按 Binance symbol 写法，Binance 不可用时按 base asset 映射到 CoinGecko |
| `finance_symbols` | Finnhub 采集 symbol 列表 |
| `finnhub_api_keys` / `active_finnhub_api_key` | Finnhub API Key 列表与当前启用 Key |
| `wallet_addresses` | 默认钱包地址列表 |
| `include_crypto_fundamentals` | 是否补充 CoinGecko 基本面字段；Binance 报价失败时 CoinGecko 也作为价格 fallback |

---

## 九、工作台图表架构

### 核心设计

**初始化全量加载 + 运行态分层增量更新 + 结构变化时受控重载**

- `/workspace`：工作台基础信息，一次性加载
- `/chart`：全量图表数据，仅在初始化或结构变化时调用
- `/chart-delta`：运行态增量，5 条数据流独立轮询

初始化保护：

- 页面启动阶段先拉 `/workspace`，拿到 market context、默认 sub metrics 与 source 状态后再拉 `/chart`。
- 自动刷新在 `boot_ready=true` 后才启动，避免 workspace 与 chart 首屏请求互相 abort。
- 同一时刻只允许一个 chart 请求在途；自动刷新遇到 in-flight 请求会 skip。

### 全量重载 vs 增量 patch

触发全量重载的情况：时间范围切换、interval 改变、切换主市场、增减观察市场、overlay 配置变化、series 结构变化。

其余情况（新数据点到达、最后 bucket 修正）均走增量 patch。

稀疏样本不是结构错误：当 `row_count` 或 `history_price_points` 为 1 时，应该优先检查本地 `market_deltas` 是否缺历史，而不是判断为前端渲染失败。

### 多市场规则

- 任何时刻只允许一个主市场，负责完整价格、策略统计、主指标
- 观察市场只展示简化价格线（`yes_mid`），不影响主市场刷新
- 主市场切换后 cursor 失效，触发一次全量重载

### 前端状态分类

| 状态类型 | 内容 |
|---------|------|
| 页面基础状态 | `workspaceState`、`trackedMarkets`、overlay 选择 |
| 图表结构状态 | panel 列表、series 定义、axis 配置、series key 映射 |
| 图表数据状态 | 各 series 数据点、各流 cursor、最后 bucket 时间戳 |

结构状态变化 → 全量重载；数据状态变化 → patch。

### Events 面板与主图事件标记

工作台图表对事件采用“双通道展示”：

- 主图 `markLine` 只画低频、需要和价格走势对齐的事件，例如 action、trade、error、settings。
- Events 面板保留完整事件点，包括高频 `print`。

`print` 不再画在价格主图上，但仍然属于 chart payload 的 `events`。因此后端 `/chart?include_events=1` 不应过滤 `print`；过滤只发生在前端构造主图 `markLine` 时。Events 面板的 scatter series 必须直接读取 `payload.events`，否则会出现“主图干净了，但 Events 面板也没了”的误判。

前端结构注意点：

- `event_timeline` 面板由 `payload.events` 是否存在有效 `ts` 决定，不由主图可见事件决定。
- 图表结构签名包含 `event_timeline`，保证 print 事件出现/消失时触发全量重建而不是错误地走增量 patch。
- 修改 `strategy_workspace_v2.js` 后需要同步更新 `templates/strategy_workspace.html` 中的静态资源 query version，否则 Flask 进程或浏览器缓存可能继续加载旧逻辑。
## 2026-05-13 Chart 价格序列一致性

### 问题背景

工作台曾出现一种误导性现象：首屏加载时 `Leg 1 Yes Ask` 红线断续，几秒后增量刷新又变成连续线。Debug 证明根因不是 ECharts 渲染慢，而是首屏 `/chart` 与后续 `/chart-delta` 对稀疏价格行的处理不一致：

```text
首屏 renderCharts:done:
market_0_yes_ask finite=304/1036 gaps=40

后续 chart:delta-patch:
market_0_yes_ask finite=972/1036 gaps=0
```

### 当前规则

1. Virtual runner 每个 tick 在 `strategy_virtual_ticks.mode_output` 中写入结构化 `price_snapshot`：

```json
{
  "actions": [],
  "price_snapshot": {
    "yes_bid": 0.82,
    "yes_ask": 0.83,
    "no_bid": 0.17,
    "no_ask": 0.18
  }
}
```

2. `strategy_chart_service._load_strategy_tick_price_samples()` 读取 `price_snapshot` 作为 tick 级真实盘口补充。

3. `function_json.print` 中的 `Yes_bid=... No_bid=...` 只解析为 bid fallback；不得再用 `No_bid` 推导 `Yes Ask = 1 - No_bid`。

4. `strategy_chart_service._safe_binary_quote()` 会过滤 chart 链路中的非法二元报价：`<= 0`、`> 1`、非数字、NaN / Infinity 都按缺价处理。该规则覆盖 `price_snapshot`、`print` bid fallback、详情快照、本地 `market_deltas` / `markets_state` 的 bid / ask / last。

5. `strategy_workspace_v2.js` 在首屏 `cacheChartPayload()` 阶段调用 `normalizeChartPayloadRows()`，对全量 rows 执行与增量合并一致的行级 forward-fill。

6. `mergeDeltaPoints()` 保持增量合并时的同样语义：同一时间轴上，如果新行没有某个字段，则沿用上一行已知值。

7. `_sync_row_pnl_to_visible_prices()` 使用同一行可见 ask 计算 `strategy_pnl`；没有可见 ask 时移除 PnL，而不是用 bid、last 或合成 ask 兜底。

### 安全边界

- 行级 forward-fill 表示“上一条真实盘口在后续 bucket 中仍作为最新已知值展示”，不是生成新报价。
- `0.0` 不是有效二元报价。Virtual tick 或 print 中的 `Yes_bid=0.0` / `No_bid=0.0` 表示缺价，不能进入 chart rows，否则会在价格线和 Virtual PnL 上形成插针。
- 不允许使用 `last_price` 冒充 ask。
- 不允许使用 `No_bid` 反推 `Yes Ask`，除非明确处于受控的二元互补修复场景且 source 被标记；当前 chart 主路径默认禁止。
- 如果 Debug 里 `market_0_yes_ask` 长时间明显少于 `market_0_yes_bid`，优先检查 `price_snapshot`、`market_deltas.now_ask/best_ask`、CLOB `/book` 和本地 WS/REST fallback，而不是先改 ECharts 样式。

### Debug 观测点

前端 Debug 会输出：

- `[WS] chart:normalize-rows`：首屏全量 rows 归一化前后摘要。
- `[WS] renderCharts:done.render_diagnostics`：全量渲染时每条重要线的 `finite/nulls/gaps/max_gap_rows/option_points/connect_nulls`。
- `[WS] chart:delta-patch.render_diagnostics`：增量 patch 后同样指标。

判断标准：首屏归一化后的 `market_0_yes_ask` 与后续 delta 中的 `market_0_yes_ask` 应在同一量级，不能再出现首屏 300 点、delta 后 900 点这种口径跳变。
