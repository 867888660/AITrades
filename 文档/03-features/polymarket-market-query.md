# Polymarket 市场查询

本文说明 Dashboard 首页 `Polymarket 市场查询` 面板的筛选、排序、接口和维护边界。

## 功能目标

市场查询面板用于快速发现可交易的 Polymarket binary market，并把结果进一步用于：

- 打开 Polymarket 官网市场页。
- 打开本地 Watchlist 页并带入市场参数。
- 复制策略参数 JSON。
- 加入或移出本地自选。

它不是实时盘口监控器。盘口实时性仍以后续 CLOB `/book`、本地 `markets_state` / `market_deltas`、策略工作台快照为准；市场查询结果主要承担发现、筛选和元数据入口职责。

## 页面交互

首页面板由两层组成：

1. 查询工具条
   - `关键词`：按 question、slug、category、conditionId、token、group item title 等字段做多关键词 AND 匹配。
   - `类别`：可手输，也可点击下方类别标签自动填入；多个类别用 `,`、`/`、`;` 分隔。
   - `排序`：选择热门、交易量、流动性、价差、到期、更新时间等排序方式。
   - `方向`：`大到小` 或 `小到大`；选择 `价差最小`、`即将到期` 时前端默认切到 `小到大`。
   - `盘口范围`：按 Yes/No 的 Ask/Bid 最小值和最大值筛选；空值表示不限。价格来自当前缓存/快照，不作为毫秒级实时盘口条件。
   - `条数`：限制返回数量，后端上限 200。
   - `强刷`：带 `refresh=1`，跳过普通缓存路径重新拉取可用市场。

2. 市场类别条
   - 类别从 `/api/polymarket/market-categories` 动态读取，按市场数量降序展示。
   - 点击类别标签会追加或取消选择，并同步到 `类别` 输入框。
   - 类别条为横向滚动，避免把查询工具条撑高或挤乱。

结果表新增排序依据列：

- `24h Vol`
- `Volume`
- `Liquidity`
- `Ends`

这样用户按热门、总交易量、流动性或到期时间排序时，可以直接看到排序原因。

## 后端接口

### `GET /api/polymarket/market-categories`

返回当前可用市场里的类别计数。

查询参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `limit` | `120` | 返回类别数量，上限 500；`0` 表示不截断。 |
| `refresh` | `0` | 为 `1` 时强制刷新活跃市场缓存。 |

返回结构：

```json
{
  "ok": true,
  "count": 3,
  "data": [
    { "name": "Politics", "count": 684 },
    { "name": "Sports", "count": 331 },
    { "name": "Crypto", "count": 147 }
  ]
}
```

### `GET /api/polymarket/markets`

搜索并排序市场。

查询参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `q` | 空 | 关键词。多词按 AND 匹配，常见噪声词会过滤。 |
| `category` | 空 | 类别筛选。支持重复参数，也支持 `,`、`/`、`;` 分隔。多个类别为 OR。 |
| `sort` / `sort_by` | 空 | 排序字段。空、`relevance`、`default` 表示保持原始相关性顺序。 |
| `order` / `sort_dir` | `desc` | 排序方向，`asc` 为小到大，其它值按大到小。 |
| `yes_ask_min` / `yes_ask_max` | 空 | Yes ask 价格范围，0 到 1。兼容别名 `ask_min` / `ask_max`。 |
| `yes_bid_min` / `yes_bid_max` | 空 | Yes bid 价格范围，0 到 1。兼容别名 `bid_min` / `bid_max`。 |
| `no_ask_min` / `no_ask_max` | 空 | No ask 价格范围，0 到 1。 |
| `no_bid_min` / `no_bid_max` | 空 | No bid 价格范围，0 到 1。 |
| `limit` | `60` | 返回数量，后端限制为 1 到 200。 |
| `refresh` | `0` | 为 `1` 时强制刷新市场缓存。 |

返回结构会回显排序参数：

```json
{
  "ok": true,
  "count": 30,
  "sort": "volume24h",
  "order": "desc",
  "data": []
}
```

### Agent 查询入口

Agent 使用同一套市场查询与盘口范围过滤能力：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/agent/markets` | Agent 市场搜索；支持 `q`、`category/categories`、排序和盘口范围参数。 |
| `POST` | `/api/agent/market-scan` | 多排序热门扫描；支持平铺盘口参数或 `price_filters` JSON。 |
| `POST` | `/api/agent/market-scan/propose-strategies` | 扫描并生成草案；草案市场快照会保存 Yes/No bid/ask。 |

Agent GET 参数与 `/api/polymarket/markets` 保持一致；POST JSON 也可以写成：

```json
{
  "category": "Crypto",
  "sorts": ["volume24h", "liquidity"],
  "price_filters": {
    "yes_ask": { "min": 0.20, "max": 0.45 },
    "no_bid": { "min": 0.55 }
  },
  "limit": 30
}
```

AgentMonitor 的审批详情中会显示 `Side Bid/Ask` 与 `All Bid/Ask`，用于复核 agent 筛选和入场价依据。

## 排序字段

排序发生在后端：先完成关键词、类别和盘口价格范围筛选，再对完整候选集合排序，最后按 `limit` 截断。不要在前端只对当前 30 条结果排序，否则会得到局部热门而不是真正热门。

| 前端选项 | `sort` 值 | 排序数据来源 | 默认方向 |
| --- | --- | --- | --- |
| 24小时热门 | `volume24h` | `volume_24h` / `volume24hr` / `volume24hrClob` | `desc` |
| 总交易量 | `volume` | `volume` / `volumeNum` / `volumeClob` | `desc` |
| 流动性最高 | `liquidity` | `liquidity` / `liquidityNum` / `liquidityClob` | `desc` |
| 价差最小 | `spread` | `spread` | `asc` |
| 即将到期 | `end_date` | `end_date` / `endDate` / `umaEndDate` | `asc` |
| 最新更新 | `updated_at` | `updated_at` / `updatedAt` | `desc` |
| 24小时变化 | `price_change_24h` | `price_change_24h` / `oneDayPriceChange` 的绝对值 | `desc` |
| 最近成交价 | `last_trade_price` | `last_trade_price` / `lastTradePrice` | `desc` |

缺失排序值的市场会排在有值市场之后，并保持原始相对顺序。

## 类别匹配规则

类别解析函数支持：

```text
Politics,Crypto
Politics / Sports / Crypto
Politics;Sports
```

类别之间是 OR；关键词 token 之间是 AND。

类别匹配采用完整词/完整类别匹配，避免 `Politics` 误匹配到 `Geopolitics`。例如：

- 选择 `Politics`：匹配 `Politics`。
- 选择 `Elections`：可匹配 `Elections`、`World Elections`、`Global Elections`。
- 选择 `Politics`：不匹配 `Geopolitics`。

## 数据流

```text
templates/index.html
  -> static/app.js
  -> GET /api/polymarket/market-categories
  -> GET /api/polymarket/markets
  -> services/polymarket_service.py
      -> _known_markets()
      -> active market snapshot / process cache
      -> Dictionary DB fallback
      -> Gamma text search fallback
```

主要前端函数：

- `loadMarketCategories()`
- `renderMarketCategoryChips()`
- `setMarketCategories()`
- `syncMarketCategoryChips()`
- `loadMarkets()`
- `renderMarketMeta()`
- `renderMarketSearchTable()`

主要后端函数：

- `list_market_categories()`
- `search_markets()`
- `_parse_categories()`
- `_category_matches()`
- `_market_sort_value()`
- `_sort_market_results()`

## 维护注意事项

- 新增排序字段时，同时更新：
  - `services/polymarket_service.py` 的 `_market_sort_value()`。
  - `static/app.js` 的 `MARKET_SORT_LABELS`。
  - `templates/index.html` 的排序下拉。
  - 本文档的排序字段表。
- 如果 Gamma payload 字段名变化，优先在 `_with_strategy_param_fields()` 或 `_normalize_market()` 中补规范字段，避免前端直接依赖 raw 字段。
- 新增盘口范围字段时，同时更新 `app.py` 的接口参数、`services/polymarket_service.py` 的 `_market_price_value()` / `_market_matches_price_filters()`、`templates/index.html` 的范围输入、`static/app.js` 的结果列和本文档。
- Agent 侧也要同步更新 `services/agent_interface_service.py` 的 `MARKET_QUERY_CAPABILITIES` / `_agent_price_filters()`，以及 `static/agent_monitor.js` 的审批详情展示。
- 类别标签只代表当前缓存/快照中可见的 active markets，不是 Polymarket 全站永久分类全集。
- `强刷` 会增加远端请求成本，默认查询应优先使用进程缓存和本地快照。
