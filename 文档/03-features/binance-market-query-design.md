# Binance 市场查询设计与落地记录

本文记录扩展标的时，新增「币安市场查询」模块的产品和工程边界。目标不是马上接入真实交易，而是先建立统一的市场发现入口：用户可以查询 Binance 上可用的 crypto、衍生品、RWA 股票代币和股票相关标的，并把其中一项绑定到策略 leg、watchlist 或图表 overlay。

## 2026-06-09 落地记录

本次已把「Binance市场查询」做进首页，位置参考现有「Polymarket 市场查询」模块，但按 Binance 标的类型拆成独立 tab。

### 本次新增能力

| 区域 | 状态 | 说明 |
|---|---|---|
| Spot | 已落地 | 查询 Binance spot `exchangeInfo`，支持 `q`、`quote`、`status`、`margin`、`limit`、`refresh` |
| Derivatives | 已接 UI 和 API，当前网络 degraded | 已预留 USD-M、COIN-M、Options 查询；本地网络访问 futures/options API 返回 `451` 时会显示 degraded 诊断 |
| Stock Tokens | 已落地 | 查询 Binance Web3 / Ondo tokenized stock 列表，支持 ticker、token symbol、chain、status |
| Stocks / Equity | 已落地基础版 | 复用现有 Finnhub 股票行情配置，作为普通 equity 查询入口，不与股票代币混用 |

### 涉及文件

| 文件 | 改动 |
|---|---|
| `services/binance_market_service.py` | 新增 Binance 市场查询 service，统一四类市场的搜索、缓存、字段规范化和 degraded 状态 |
| `app.py` | 新增 `GET /api/binance/markets/search` 路由 |
| `templates/index.html` | 首页新增 `Binance市场查询` 面板和四个 tab：`Spot` / `Derivatives` / `Stock Tokens` / `Stocks / Equity` |
| `static/app.js` | 新增 tab 切换、表单提交、分类表格渲染、meta 状态展示 |
| `static/styles.css` | 新增 Binance 查询表单、tab、能力标签、instrument 显示样式 |
| `文档/README.md` | 在 03-features 文档列表里挂载本文 |

### 当前接口

当前已实现一个统一搜索接口：

```http
GET /api/binance/markets/search
```

常见调用：

```http
GET /api/binance/markets/search?category=crypto_spot&q=BTC&quote=USDT&status=TRADING&limit=30
GET /api/binance/markets/search?category=crypto_derivatives&q=BTC&subtype=all&status=TRADING&limit=30
GET /api/binance/markets/search?category=rwa_stock_token&q=AAPL&chain_id=56&status=ACTIVE&limit=30
GET /api/binance/markets/search?category=equity&q=NVDA&limit=30
```

响应统一包含：

| 字段 | 说明 |
|---|---|
| `ok` | 请求是否正常完成 |
| `count` | 本次返回条数 |
| `data` | 规范化后的市场列表 |
| `meta.category` | 查询类别 |
| `meta.source` | 实际数据源 |
| `meta.source_status` | `ok` / `degraded` |
| `meta.cache_status` | `fresh` / `hit` / `stale`，部分数据源返回 |
| `meta.errors` | 降级或失败诊断 |

### 页面行为

- 首页新增 `Binance市场查询` 面板。
- 四个 tab 分开渲染，不把 spot、合约、股票代币和普通股票混在一个表里。
- Spot 默认筛选 `status=TRADING`，默认展示 `BTC/ETH/SOL` 等常见 quote 下的交易对。
- Derivatives 默认查 `TRADING`，但当前地区受限时显示 `DEGRADED` 和 Binance `451` 诊断。
- Stock Tokens 默认筛选 `ACTIVE`，展示 `Ticker`、`Token`、`Chain`、`Contract`、`Instrument`。
- Stocks / Equity 显示 Finnhub quote/profile2 的基础行情；没有 Finnhub key 时仍可展示本地配置 symbol，但状态会降级。

### 验证记录

本次已执行：

```powershell
python -m py_compile app.py services\binance_market_service.py
node --check static\app.js
```

并用 Flask test client 验证四类接口：

| category | 结果 |
|---|---|
| `crypto_spot` | 返回数据；本地网络主域名 `api.binance.com` 返回 `451`，已 fallback 到 `data-api.binance.vision`，因此 meta 为 `degraded` |
| `crypto_derivatives` | 接口可用；当前网络 futures/options API 返回 `451`，因此返回空列表并标记 `degraded` |
| `rwa_stock_token` | 返回 `AAPLon` / `NVDAon` 等股票代币记录 |
| `equity` | 返回 Finnhub / 本地配置股票记录 |

应用内 Browser 对 `127.0.0.1:5010` 有安全策略拦截，因此本次视觉验收使用本地 HTTP 和静态 DOM 检查替代。

## 设计目标

- 给用户一个统一入口搜索 Binance 相关市场，而不是手工记忆 `BTCUSDT`、`AAPLon` 这类代码。
- 查询结果必须能转换成统一的 `instrument_id`，以便接入现有多资产策略框架。
- 首阶段以只读行情和元数据为主，不承诺下单、保证金、合约风控或股票交易权限。
- 接口返回要区分「资产类型」和「筛选维度」，避免 UI 分类过碎。
- 所有市场列表都按接口动态刷新并缓存，不能把当前数量或 symbol 列表写死进代码。

## 推荐分类

概念上可以分成 5 类，其中前 4 类是资产/市场类型，第 5 类是查询筛选维度。

| 类别 | 建议 key | 说明 | 第一阶段 |
|---|---|---|---|
| Crypto Spot 现货 | `crypto_spot` | Binance 现货交易对，例如 `BTCUSDT`、`ETHUSDC`、`SOLFDUSD` | 优先落地 |
| Crypto Margin 杠杆能力 | `crypto_margin` | 现货交易对上的 margin 能力标记，例如 `isMarginTradingAllowed` | 作为 Spot 过滤器 |
| Derivatives 衍生品 | `crypto_derivatives` | USD-M、COIN-M futures/perps、options | 只读发现，第二阶段 |
| RWA / Stock Tokens 股票代币 | `rwa_stock_token` | Web3/Ondo 股票代币，例如 `AAPLon`、`NVDAon`、`SPYon` | 只读发现，第二阶段 |
| Quote / Stable / Fiat 分组 | `quote_filter` | `USDT`、`USDC`、`FDUSD`、`BTC`、`ETH`、`BNB`、`EUR`、`TRY` 等 | 全局过滤器 |

UI 主分类建议使用 4 个 tab：

1. `Spot`
2. `Derivatives`
3. `Stock Tokens`
4. `Stocks / Equity`

其中 `Margin`、`Quote`、`Settlement Asset`、`Chain`、`Status` 都作为 tab 内过滤器。这样用户看到的是清晰的市场类型，筛选时仍然能快速缩小结果。

## 数据源规划

### Spot

Spot 第一阶段复用当前 `crypto_service.py` 的 Binance symbol 习惯。

- 市场列表：`/api/v3/exchangeInfo`
- 行情：`/api/v3/ticker/24hr`
- 网络限制：当前主域名可能返回 `451`，查询服务应支持切换到 `data-api.binance.vision` 这类只读 market-data 域名。
- fallback：现有 CoinGecko fallback 只适合价格兜底，不适合生成完整 Binance 市场列表。

Spot 查询结果应该优先展示：

| 字段 | 说明 |
|---|---|
| `symbol` | Binance symbol，例如 `BTCUSDT` |
| `base_asset` | 基础资产，例如 `BTC` |
| `quote_asset` | 报价资产，例如 `USDT` |
| `status` | Binance 返回的交易状态 |
| `is_spot_trading_allowed` | 是否允许现货交易 |
| `is_margin_trading_allowed` | 是否允许杠杆交易 |
| `price` | 最新价格，可来自 ticker |
| `change_percent_24h` | 24h 涨跌幅 |
| `volume_24h_base` | 24h base 成交量 |
| `volume_24h_quote` | 24h quote 成交额 |

### Margin

Margin 不建议第一阶段单独做成一个资产类。原因是对策略而言，`BTCUSDT` 的价格、base/quote 和大部分元数据仍然来自 spot；margin 更像交易能力和执行方式。

第一阶段只做：

- 在 Spot 查询结果里显示 `Margin` 标签。
- 支持 `margin=1` 过滤。
- 生成 instrument 时仍默认使用 `crypto_spot:binance:BTCUSDT`。

当未来接入真实交易或杠杆账户时，再考虑新增 `crypto_margin:binance:BTCUSDT`。

### Derivatives

衍生品建议统一放在 `Derivatives` tab，下设 subtype：

| subtype | 说明 | instrument 示例 |
|---|---|---|
| `usdm_futures` | USD-M futures/perpetual | `crypto_perp:binance_usdm:BTCUSDT` |
| `coinm_futures` | COIN-M futures/perpetual | `crypto_perp:binance_coinm:BTCUSD_PERP` |
| `options` | Binance options | `crypto_option:binance_options:BTC-20260626-70000-C` |

第一阶段只做市场发现，不进入策略虚拟成交。原因是衍生品需要额外处理合约乘数、到期日、保证金、资金费率、行权价、期权 greeks 等字段。

### RWA / Stock Tokens

股票代币建议单独建 adapter，不要塞进 `crypto_spot`。它们的命名是 `AAPLon`、`NVDAon`、`TSLAon` 这种 token symbol，但经济含义是股票/ETF 暴露，不是普通 crypto pair。

查询结果需要保留链上维度：

| 字段 | 说明 |
|---|---|
| `ticker` | 原股票/ETF ticker，例如 `AAPL` |
| `token_symbol` | 代币 symbol，例如 `AAPLon` |
| `chain_id` | 链 ID，例如 Ethereum、BSC 或其他网络标识 |
| `contract_address` | 合约地址 |
| `multiplier` | token 与底层标的的换算因子 |
| `underlying_type` | `stock` / `etf` / `fund` 等 |
| `price` | 链上或 Binance Web3 返回的价格 |
| `trade_status` | 是否可交易、是否只读展示 |

推荐 instrument_id：

```text
rwa_stock_token:binance_web3:AAPLon:56
rwa_stock_token:binance_web3:NVDAon:1
```

其中最后一段是链 ID，避免同一个 `AAPLon` 在多条链上冲突。

### Stocks / Equity

这里要保持谨慎：普通股票行情和股票代币不是同一件事。

- 当前项目股票行情来自 Finnhub，配置字段是 `finance_symbols`。
- 如果以后接 Binance Stocks，建议作为 `equity` 资产类的一个 venue，而不是和 tokenized stock 混在一起。
- 第一阶段 UI 可以先展示 `Stocks / Equity` tab，但数据源仍标注为 Finnhub 或 future Binance Stocks adapter。

推荐 instrument_id：

```text
equity:finnhub:NVDA
equity:binance_stock:AAPL
```

## 统一返回结构

市场查询接口返回统一结构。搜索列表默认只返回前端和策略需要的规范字段，不直接返回完整原始 payload，避免首页请求过大。后续如需查看 filters、合约规则或链上完整 metadata，应通过详情接口补充。

```json
{
  "instrument_id": "crypto_spot:binance:BTCUSDT",
  "asset_class": "crypto_spot",
  "venue": "binance",
  "market_kind": "spot",
  "symbol": "BTCUSDT",
  "display_symbol": "BTC/USDT",
  "display_name": "Bitcoin / TetherUS",
  "base_asset": "BTC",
  "quote_asset": "USDT",
  "settlement_asset": "USDT",
  "status": "TRADING",
  "capabilities": {
    "spot": true,
    "margin": true,
    "derivatives": false,
    "tokenized_stock": false
  },
  "price": 0,
  "change_percent_24h": null,
  "source": "binance_spot_exchange_info"
}
```

股票代币示例：

```json
{
  "instrument_id": "rwa_stock_token:binance_web3:AAPLon:56",
  "asset_class": "rwa_stock_token",
  "venue": "binance_web3",
  "market_kind": "stock_token",
  "symbol": "AAPLon",
  "display_symbol": "AAPL on BSC",
  "underlying_symbol": "AAPL",
  "chain_id": "56",
  "contract_address": "0x...",
  "status": "ACTIVE",
  "capabilities": {
    "spot": false,
    "margin": false,
    "derivatives": false,
    "tokenized_stock": true
  },
  "source": "binance_web3_tokenized_securities"
}
```

## API

### 当前已实现：搜索市场

```http
GET /api/binance/markets/search?category=crypto_spot&q=btc&quote=USDT&margin=1&limit=50
```

常用参数：

| 参数 | 说明 |
|---|---|
| `category` | `crypto_spot` / `crypto_derivatives` / `rwa_stock_token` / `equity` |
| `q` | 搜索词，匹配 symbol、base、quote、ticker、display name |
| `quote` | Spot quote asset 过滤，例如 `USDT`、`USDC`、`FDUSD` |
| `subtype` | Derivatives 子类型：`all` / `usdm_futures` / `coinm_futures` / `options` |
| `settlement` | 衍生品结算资产过滤 |
| `margin` | 是否只看支持 margin 的 spot |
| `chain_id` | 股票代币链过滤，例如 `1`、`56`、`CT_501` |
| `status` | `TRADING` / `ACTIVE` / `QUOTE_READY` 等 |
| `limit` | 返回数量，默认 50，最大 200 |
| `refresh` | 是否绕过缓存刷新 |

### 预留：查询分类

```http
GET /api/binance/markets/categories
```

返回：

```json
{
  "ok": true,
  "data": [
    {"key": "crypto_spot", "label": "Spot"},
    {"key": "crypto_derivatives", "label": "Derivatives"},
    {"key": "rwa_stock_token", "label": "Stock Tokens"},
    {"key": "equity", "label": "Stocks / Equity"}
  ]
}
```

### 预留：查看详情

```http
GET /api/binance/markets/detail?instrument_id=crypto_spot:binance:BTCUSDT
```

详情接口用于展示完整原始 payload、交易规则、filters、链上合约信息或衍生品合约规格。

## 前端交互

入口可以放在首页市场查询或策略工作台的「添加标的」区域。

推荐布局：

- 顶部 tab：`Spot`、`Derivatives`、`Stock Tokens`、`Stocks / Equity`
- 搜索框：支持 `btc`、`BTCUSDT`、`AAPL`、`AAPLon`
- 快捷过滤：`USDT`、`USDC`、`FDUSD`、`BTC`、`ETH`、`BNB`
- 高级过滤：`Margin`、`Status`、`Chain`、`Settlement`
- 表格列：`Symbol`、`Type`、`Base/Underlying`、`Quote/Settlement`、`Status`、`24h`、`Capabilities`、`Source`
- 行操作：`加入自选`、`加入图表`、`绑定到 Leg`

UI 文案要避免暗示所有标的都可交易。只读发现阶段可以使用状态标签：

- `行情可用`
- `可绑定策略`
- `仅元数据`
- `需权限`
- `地区受限`

## 缓存和新鲜度

Binance 市场列表变化频率低于价格行情，应分开缓存。

| 数据 | 建议缓存 |
|---|---|
| Spot exchangeInfo | 5-30 分钟 |
| Spot ticker/24hr | 5-15 秒 |
| Derivatives exchangeInfo | 5-30 分钟 |
| Stock token list | 5-30 分钟 |
| Stock token price | 5-30 秒 |

缓存文件或表中必须记录：

- `source`
- `fetched_at_utc`
- `ttl_seconds`
- `status`
- `error`
- `data_count`

如果 Binance 主域名返回 `451` 或网络失败，接口应明确返回 degraded 状态，并说明是否使用了 fallback 数据源。

## 与策略系统的关系

市场查询只负责发现和选择标的。真正进入策略后，仍然要落到 `strategy_legs` 的通用字段：

| 字段 | Spot 示例 | Stock Token 示例 |
|---|---|---|
| `asset_class` | `crypto_spot` | `rwa_stock_token` |
| `venue` | `binance` | `binance_web3` |
| `symbol` | `BTCUSDT` | `AAPLon` |
| `instrument_id` | `crypto_spot:binance:BTCUSDT` | `rwa_stock_token:binance_web3:AAPLon:56` |
| `instrument_json` | base/quote、filters | ticker、chain、contract、multiplier |

`virtual_context_builder` 后续可以按 `asset_class + venue` 选择 quote adapter：

- `crypto_spot + binance`：读取现有 crypto collector。
- `rwa_stock_token + binance_web3`：读取股票代币 adapter。
- `equity + finnhub`：读取现有 finance collector。
- `crypto_derivatives + binance_*`：等第二阶段 quote adapter。

## 分阶段落地

### 第 1 阶段：Spot 查询

- 状态：已完成基础查询。
- 已新增 Binance market query service。
- 已支持 Spot `exchangeInfo` 搜索、quote 过滤、margin 标签、status 过滤。
- 已用 `/api/v3/ticker/24hr` mini ticker 补充 `price`、24h 涨跌幅和成交量。
- 未完成：从结果直接绑定到 strategy leg、watchlist 或 chart overlay。

### 第 2 阶段：Stock Tokens 只读查询

- 状态：已完成基础查询。
- 已新增 Binance Web3/RWA adapter。
- 已支持 ticker、token symbol、chain、contract 查询。
- 已生成 `rwa_stock_token:binance_web3:<symbol>:<chain_id>` instrument。
- 未完成：加入 watchlist、图表 overlay、策略 leg。

### 第 3 阶段：Derivatives 只读查询

- 状态：已接 UI 和 API，当前网络 degraded。
- 已新增 USD-M、COIN-M、Options metadata adapter。
- 已返回 subtype、合约类型、结算资产、状态和 instrument 字段。
- 当前本地网络访问 futures/options API 会返回 `451`，所以页面可显示 degraded 诊断。
- 暂不做虚拟成交，避免误用 spot 估值逻辑。

### 第 4 阶段：多资产策略接入

- 状态：未完成。
- `strategy_legs` 后续支持从 Binance 查询结果直接填充。
- `UseData["Instruments"]` 后续注入对应行情字段。
- chart overlay 后续支持不同资产类字段选择。

### 第 5 阶段：交易执行评估

- 状态：未开始。
- 单独评估 Binance spot、margin、futures、stock token 的下单权限和风控。
- 不和市场查询混在同一次改造里。

## 风险和边界

- Binance 主 API 可能因地区返回 `451`，市场查询需要把数据源和降级状态展示出来。
- 股票代币、股票交易和普通股票行情不是同一类产品，UI 和数据模型不能混用。
- Margin 和 derivatives 涉及杠杆、强平、资金费率、保证金模式，不应复用 spot 虚拟成交。
- 当前项目的 CoinGecko fallback 不等于 Binance 市场列表，只能作为部分价格兜底。
- 所有数量、状态、交易权限都必须按查询时接口返回为准，文档中的 symbol 只是示例。

## 当前建议结论

第一版不要做成「币安全部市场大杂烩」。建议先落地：

```text
Spot tab
  - q 搜索
  - quote 过滤
  - margin 标签
  - status 过滤
  - add to leg / overlay

Stock Tokens tab
  - ticker 搜索
  - chain 过滤
  - contract 详情
  - readonly watchlist
```

这样能最快扩展现有 `crypto_spot` 能力，同时给股票代币留出正确的数据模型。
