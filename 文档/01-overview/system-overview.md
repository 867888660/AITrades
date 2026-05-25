# DataTube Python 系统完整说明文档

## 1. 文档目的

本文档用于说明当前主应用链路中的 Python 文件、前端文件和关键存储的功能、上下游关系、数据流向与职责边界。

这份说明文档的目标不是只解释某几个接口，而是回答下面四个问题：

1. 这个项目整体是怎么跑起来的。
2. `main.py` 和 `app.py` 分别承担什么角色。
3. 当前主应用链路里的每一个核心 `py` 文件到底负责什么。
4. 这些模块之间是如何串联的。

### 文档范围说明

本文档重点覆盖的是当前正在运行的主系统，也就是：

* 根目录下的 `app.py`
* 根目录下的 `main.py`
* `services/`
* `templates/`
* `static/`

仓库里仍然存在一些辅助脚本、实验文件和另一套示例/历史工程，例如：

* `test.py`、`test2.py`、`test_ws.py`
* `fetch_examples.py`、`dump_target.py`
* `参考/`
* `project/`

这些文件目前**不属于主运行链路**，因此本文档只在后文的“未纳入主链路的代码”小节中做范围说明，不逐个展开。

---

## 2. 系统总体结构

当前项目本质上由两条主线组成：

* **实时采集线**：从外部 API / WebSocket 获取市场与行情数据，写入本地数据库。
* **Web 展示线**：从本地数据库和服务层读取数据，对外提供页面、API 和 SSE。

可以把它理解成下面这张总图：

```text
外部数据源
├─ Polymarket Gamma API
├─ Polymarket CLOB WebSocket
├─ Polymarket positions API
├─ Binance API
├─ CoinGecko API
└─ Finnhub API

        ↓

[实时采集线]
main.py
├─ 拉活跃市场
├─ 计算候选订阅集合
├─ 建立 WebSocket 订阅
└─ 写入实时市场库

realtime_collector.py
├─ 拉 Crypto 行情
├─ 拉 Finance 行情
└─ 写入行情宽表库

        ↓

[本地数据库]
├─ 行情 SQLite
├─ 本地订单 SQLite
├─ 策略配置 / 监控 SQLite
├─ 实时市场 SQLite
└─ 策略统计 SQLite 目录

        ↓

[服务层]
config_loader.py
crypto_service.py
finance_service.py
http_client.py
polymarket_service.py
strategy_chart_service.py
strategy_event_service.py
strategy_profit_engine.py
strategy_settings_service.py
strategy_stats_store.py
strategy_workspace_service.py
workspace_preset_service.py
ws_market_sync_service.py
backtest_service.py
sqlite_store.py

        ↓

[Web 展示线]
app.py
├─ 页面路由
├─ API 路由
└─ SSE 实时推送
```

---

## 3. 五类核心存储

整个系统围绕 5 类本地存储运行：

### 3.1 行情 SQLite

用途：保存 Crypto / Stock 宽表快照。

典型文件：

* `realtime_collector.py`
* `sqlite_store.py`
* `strategy_chart_service.py`

### 3.2 本地订单 SQLite

用途：保存本地下单记录与持仓回补信息。

典型文件：

* `strategy_profit_engine.py`
* `polymarket_service.py`

### 3.3 策略配置 / 监控 SQLite

用途：保存当前策略注册表、策略腿、虚拟盘表、运行审计表，以及旧监控表历史数据。当前主路径以 `strategy_registry` + `strategy_legs` 为策略配置中心；`polyMarket_Monitoring` / `polyMarket_Monitor` 仅作为历史存档和兼容 fallback。

典型文件：

* `strategy_data_source.py`
* `strategy_registry_service.py`
* `polymarket_service.py`
* `strategy_profit_engine.py`
* `main.py`

### 3.4 实时市场 SQLite

用途：保存 Polymarket 实时盘口快照与增量流水。

核心表：

* `markets_state`
* `market_deltas`

典型文件：

* `main.py`
* `ws_market_sync_service.py`
* `polymarket_service.py`
* `strategy_event_service.py`
* `strategy_chart_service.py`

### 3.5 策略统计 SQLite 目录

用途：每条策略维护一个独立 `.db`，保存策略统计快照与历史。

典型文件：

* `strategy_stats_store.py`
* `polymarket_service.py`

---

## 4. 两个总入口文件

## 4.1 `app.py`

### 角色

`app.py` 是整个项目的 **Web 入口**。

### 主要职责

* 创建 Flask 应用。
* 注册页面路由。
* 注册 REST API。
* 注册 SSE 推送接口。
* 启动 `ws_market_sync_service`。
* 启动 `realtime_collector`。
* 启动 `virtual_runner`，运行 `state=Virtual` 的策略虚拟盘循环。

### 它不负责什么

* 不直接写复杂 SQL。
* 不直接做市场同步。
* 不直接写图表采样逻辑。
* 不直接处理 WebSocket 原始消息。

### 它向下串联的模块

* `config_loader.py`：读取和保存 Web 设置。
* `crypto_service.py`：按需返回 Crypto 报价。
* `finance_service.py`：按需返回 Finance 报价。
* `backtest_service.py`：返回回测占位信息。
* `polymarket_service.py`：市场搜索、策略列表、策略详情、持仓、概览。
* `realtime_collector.py`：启动行情采集线程。
* `strategy_chart_service.py`：返回图表数据。
* `strategy_chart_delta_service.py`：返回分流式增量图表数据。
* `strategy_event_service.py`：返回策略事件。
* `strategy_registry_service.py`：提供 `strategy_registry` + `strategy_legs` CRUD 与策略代码输入解析。
* `strategy_settings_service.py`：更新策略设置。
* `strategy_data_source.py`：统一策略读写、虚拟盘表 DDL 与持仓字段回写。
* `strategy_workspace_service.py`：拼装工作台完整数据。
* `virtual_runner.py` / `virtual_execution.py` / `virtual_context_builder.py`：虚拟盘调度、成交与 UseData 注入。
* `ws_market_sync_service.py`：启动 WebSocket 市场同步线程。
* `workspace_preset_service.py`：保存/读取/删除工作台预设。

### 路由分组

* 页面：`/`、`/settings`、`/watchlist`、`/strategies/<row_id>/workspace`
* 健康检查：`/api/health`
* 配置：`/api/settings`
* 概览：`/api/overview`
* 市场：`/api/polymarket/markets`、`/resolve`
* 持仓：`/api/polymarket/holdings`
* 策略：`/api/polymarket/strategies`、`/<row_id>`、`/<row_id>/workspace`
* 图表：`/api/polymarket/strategies/<row_id>/chart`
* 增量图表：`/api/polymarket/strategies/<row_id>/chart-delta`
* 事件：`/api/polymarket/strategies/<row_id>/events`
* 回测：`/api/polymarket/strategies/<row_id>/backtest`、`/backtest/results`
* 预设：`/api/polymarket/workspace-presets`、`/api/polymarket/workspace-presets/<preset_id>`
* 策略注册：`/api/registry/strategies`、`/api/registry/strategies/<strategy_id>`、`/api/registry/strategies/<strategy_id>/state`、`/api/registry/strategies/<strategy_id>/legs`
* 策略代码：`/api/strategy-codes`、`/api/strategy-codes/<code_name>/inputs`
* 虚拟盘：`/api/virtual/strategies/<strategy_id>/account`、`/positions`、`/orders`、`/events`、`/ticks`、`/reset`
* 实时状态：`/api/realtime/state`、`/api/realtime/crypto`、`/api/realtime/finance`
* 直连报价：`/api/crypto/quotes`、`/api/finance/quotes`
* 实时流：`/api/live/strategies`、`/api/live/strategies/<row_id>/workspace`

### 总结

`app.py` 的本质是：

> 接收前端请求 → 调用 service → 返回页面/API/SSE。

---

## 4.2 `main.py`

### 角色

`main.py` 是整个项目的 **Polymarket 实时采集入口**。

### 主要职责

* 读取 `config.json` 和部分 Web 设置。
* 拉取全量活跃市场。
* 计算高概率 / 低概率候选。
* 拉取钱包持仓并转成候选。
* 从统一策略数据源生成 `StrategyMonitoring` 候选（新注册表优先，旧监控表 fallback）。
* 创建 `WebSocketMonitor`。
* 将实时市场数据写入 SQLite / MongoDB。

### 主要阶段

1. **全量市场拉取**：`fetch_all_active_markets()`
2. **概率过滤**：`local_probability_filter()`
3. **持仓匹配**：`fetch_my_holdings()`
4. **策略监控候选加载**：`load_strategy_monitoring_candidates()`
5. **实时监控运行**：`WebSocketMonitor.run()`

### `WebSocketMonitor` 的作用

它是 `main.py` 的核心类，负责：

* 构造 `token_map`
* 初始化 MongoDB / SQLite schema
* 按 token 分片建立 WebSocket 连接
* 消费消息队列
* 更新本地 orderbook
* 将当前状态 upsert 到 `markets_state`
* 将变化写入 `market_deltas`
* 定期 watchdog 重扫候选集合

### 它不负责什么

* 不负责页面。
* 不负责 Flask 路由。
* 不负责前端展示结构。
* 不负责工作台最终拼装。

### 总结

`main.py` 的本质是：

> 拉外部市场 → 订阅实时盘口 → 写本地实时库。

---

## 5. `app.py` 和 `main.py` 的桥接关系

二者不是简单的相互调用关系，而是通过 `ws_market_sync_service.py` 连接。

### 调用链

```text
app.py
  └─ ws_market_sync.start()
       └─ ws_market_sync_service.py
            └─ import main as ws_main
                 ├─ fetch_all_active_markets()
                 ├─ local_probability_filter()
                 ├─ fetch_my_holdings()
                 ├─ load_strategy_monitoring_candidates()
                 └─ WebSocketMonitor(...).run()
```

也就是说：

* `app.py` 是 Web 层入口。
* `main.py` 是实时采集入口。
* `ws_market_sync_service.py` 是它们之间的桥。

---

## 6. 每个 Python 文件的完整功能说明

下面按文件逐个说明。

---

## 6.1 `config_loader.py`

### 功能定位

统一处理配置文件、Web 设置、路径归一化与旧字段兼容。

### 主要职责

* 读取 `config.json`
* 保存 `config.json`
* 提取默认钱包地址
* 读取 `web_settings.json`
* 保存 `web_settings.json`
* 规范化列表型配置（钱包、symbols、keys）
* 规范化 SQLite 路径和目录路径
* 兼容旧字段 `strategy_option_sqlite_db_path`
* 提供 `get_market_realtime_db_path()` 给其他模块统一读取实时市场库路径

### 它解决的问题

* 各模块不需要自己拼路径。
* 旧配置字段还能被兼容读取。
* 新保存时自动清理遗留字段。
* 所有数据库文件和目录在加载时就确保存在。

### 下游依赖者

几乎所有服务层模块都会依赖它，尤其是：

* `app.py`
* `main.py`
* `ws_market_sync_service.py`
* `realtime_collector.py`
* `polymarket_service.py`
* `strategy_workspace_service.py`
* `strategy_stats_store.py`

### 总结

它是整个系统的 **配置与路径基座**。

---

## 6.2 `http_client.py`

### 功能定位

统一 HTTP Session 与重试策略。

### 主要职责

* 创建带有 retry 的 `requests.Session`
* 设置连接池大小
* 设置统一 `User-Agent`
* 允许读取系统代理环境变量
* 提供统一 `get_timeout()`

### 好处

* 减少各 service 重复创建 Session
* 统一超时和重试策略
* 减少网络抖动导致的失败

### 被谁使用

* `crypto_service.py`
* `finance_service.py`
* `polymarket_service.py`
* `strategy_profit_engine.py`

### 总结

它是整个系统的 **HTTP 访问公共底座**。

---

## 6.3 `crypto_service.py`

### 功能定位

负责 Crypto 行情抓取和宽表行生成。

### 主要职责

* 解析 symbol 输入：`parse_symbols()`
* 优先从 Binance 拉现货 24h 行情
* Binance 报价失败（例如地域限制 `451`）时，按 symbol 推导 base/quote，并用 CoinGecko `/coins/markets` 作为价格 fallback
* Binance `exchangeInfo` 仅用于补充 base/quote 资产；该接口失败不能阻断价格或基本面 fallback
* 将 base symbol 映射到 CoinGecko ID
* 从 CoinGecko 拉价格、市值、FDV、流通量等基本面数据
* 返回统一结构的行情 payload
* 将 payload 展开成宽表 row：`build_crypto_snapshot_row()`

### 输出特征

返回的数据包括：

* price
* vol_24h_base
* vol_24h_quote
* market_cap_usd
* fdv_usd
* circulating_supply
* total_supply
* max_supply

### 被谁使用

* `app.py`：直接返回报价接口
* `realtime_collector.py`：定时采集并落库

### 总结

它是系统的 **Crypto 行情适配器**。

---

## 6.4 `finance_service.py`

### 功能定位

负责股票/金融标的行情抓取和宽表行生成。

### 主要职责

* 解析 symbol 输入
* 解析 / 读取 Finnhub API key
* 调用 Finnhub `/quote`
* 调用 Finnhub `/stock/profile2`
* 组织成统一 payload
* 生成宽表 row：`build_finance_snapshot_row()`

### 输出特征

返回的数据包括：

* price
* change
* change_percent
* high / low / open / previous_close
* company_name
* exchange
* currency
* market_cap_usd

### 被谁使用

* `app.py`
* `realtime_collector.py`

### 总结

它是系统的 **Finance 行情适配器**。

---

## 6.5 `sqlite_store.py`

### 功能定位

负责把宽表快照写入 SQLite。

### 主要职责

* 初始化元数据表 `__meta_cols`
* 初始化业务表
* 动态补列
* 记录每个列的上次值
* 只在有变化时插入一行
* 返回写入结果（inserted / skipped）

### 设计特点

这不是简单的“全量重复插入”，而是一个**变化驱动写入器**：

* 如果一行中所有列都和上次一样，则跳过。
* 如果有变化，则只记录变化点，并更新元数据。

### 被谁使用

* `realtime_collector.py`

### 总结

它是系统的 **宽表落库引擎**。

---

## 6.6 `realtime_collector.py`

### 功能定位

负责定时采集 Crypto / Finance 行情并写入行情宽表库。

### 主要职责

* 周期性读取 Web 设置
* 调 `fetch_crypto_quotes()`
* 调 `fetch_finance_quotes()`
* 将结果转为宽表 row
* 调 `write_wide_snapshot()` 写入 `Crypto` / `Stock` 表
* 内存里保存当前采集状态
* 当本次失败时，尝试读取最近历史快照作为 stale fallback

### 状态管理特征

它不只是写库，也会维护运行时状态：

* `ok`
* `stale`
* `fallback_source`
* `status`
* `last_run_at`
* `ts_utc`

`last_run_at` 表示 collector 最近一次刷新尝试时间；`ts_utc` 表示当前展示数据本身的时间。两者不能混用。若本次抓取失败但历史快照可用，状态应为 `status=degraded`、`stale=true`，并保留失败原因与 `fallback_source`。

### 对外暴露

* `collector.start()`
* `collector.is_running()`
* `collector.get_state()`

### 被谁启动

* `app.py`

### 总结

它是系统的 **外部行情采集器**。

---

## 6.7 `polymarket_service.py`

### 功能定位

这是当前项目最核心的业务拼装模块。

### 它解决的问题

把分散在多个地方的数据，拼成前端需要的策略/市场/持仓结构。

### 主要职责

1. **市场能力**

   * 拉活跃市场
   * 缓存市场
   * 本地快照回退
   * 市场搜索 `search_markets()`：多关键词分词 AND 匹配，类别支持多选 OR，同时查活跃市场缓存 + Dictionary DB（22000+ 条），本地不足时 fallback 到 Gamma API slug 精确查找
   * 市场解析 `resolve_market_selection()`

2. **持仓能力**

   * 拉钱包持仓 `fetch_wallet_positions()`
   * 统一持仓结构

3. **概览能力**

   * 汇总市场数、持仓数、策略运行数、总盈亏 `get_overview()`

4. **策略注册 / 监控能力**

   * 优先读取 `strategy_registry` + `strategy_legs`
   * 新表无数据时 fallback 到旧监控表
   * 旧表路径中自动发现监控表名、补齐可编辑字段列
   * 将策略配置和 market 进行匹配
   * 从 Gamma 反查补齐 yes/no token
   * 结合实时库与持仓信息重建策略详情

5. **策略详情能力**

   * `fetch_strategy_monitoring()`：返回策略列表
   * `fetch_strategy_detail()`：返回单策略详情
   * `update_strategy_detail()`：优先更新 `strategy_registry` 与 `input_json`，旧表仅作兼容回退

6. **图表默认配置与能力声明**

   * `get_strategy_chart_defaults()`
   * `get_strategy_chart_capabilities()`

7. **工作台辅助能力**

   * `build_workspace_market_detail()`

### 它依赖的关键模块

* `config_loader.py`
* `http_client.py`
* `strategy_profit_engine.py`
* `strategy_stats_store.py`
* 实时市场 SQLite
* 策略配置 / 监控 SQLite

### 为什么它很重要

因为前端看到的“策略详情”不是单一来源，而是它拼出来的：

* 策略注册表 / 策略腿
* 旧监控表 fallback
* 实时盘口
* 当前持仓
* 统计历史
* 市场基础信息

### 总结

它是系统的 **Polymarket 业务总装配器**。

---

## 6.8 `strategy_profit_engine.py`

### 功能定位

负责根据远程钱包持仓、本地订单记录和策略字段，计算策略仓位比例、资金规模与盈亏。当前主路径从统一数据源读取策略，并把持仓缓存和利润写回新表。

### 主要职责

* 从 `strategy_data_source.list_strategies()` 读取策略与腿配置
* 从 Polymarket positions API 拉远程持仓
* 从本地订单库读取成交记录
* 对缺失远程持仓时做本地订单回补
* 计算 yes/no 持仓比例 `calculate_position_pcts()`
* 解析/推导策略资金规模 `resolve_strategy_bankroll()`
* 将持仓字段写回 `strategy_legs`，将 `realized_profit` 写回 `strategy_registry`
* 提供 `apply_live_position_overrides()` 给 `polymarket_service.py`

### 它解决的问题

* 策略表里的字段并不一定足够真实。
* 远程持仓接口可能失败。
* 本地订单库可以作为补丁来源。
* 盈亏必须实时重新计算，而不能只看表里旧字段。

### 被谁使用

* `polymarket_service.py`

### 总结

它是系统的 **策略仓位与盈亏计算引擎**。

---

## 6.9 `strategy_stats_store.py`

### 功能定位

为每一条策略维护独立统计数据库，并保存快照与历史。

### 主要职责

* 计算策略统计库目录
* 根据 `Translation + row_id` 生成安全文件名
* 初始化 `strategy_stats` / `strategy_stats_history`
* 为单条策略持久化最新快照 `persist_strategy_row_stat()`
* 批量同步所有策略快照 `sync_all_strategy_stats()`
* 从历史中读取最近有效仓位快照 `load_latest_valid_position_snapshot()`

### 设计特点

每条策略一个独立 `.db`，优点是：

* 彼此隔离
* 便于单策略调试
* 图表历史和 fallback 更容易做

### 被谁使用

* `polymarket_service.py`
* `strategy_workspace_service.py`
* `strategy_chart_service.py`

### 总结

它是系统的 **策略统计持久化层**。

---

## 6.10 `strategy_event_service.py`

### 功能定位

统一管理策略事件流。

### 主要职责

* 初始化 `strategy_workspace_events.db`
* 创建表 `strategy_events`
* 记录工作台/设置类事件 `record_strategy_event()`
* 读取工作台本地事件 `_workspace_events()`
* 读取实时市场库中的 `market_deltas`，提取与当前策略相关的监控事件 `_monitoring_events()`
* 对外统一输出 `list_strategy_events()`

### 事件来源

有两类：

1. **工作台事件**：例如设置保存、用户操作。
2. **监控事件**：来自 `market_deltas` 的盘口变化。

### 被谁使用

* `app.py`
* `strategy_settings_service.py`
* `strategy_workspace_service.py`
* SSE 实时流

### 总结

它是系统的 **事件聚合与读取层**。

---

## 6.11 `strategy_chart_service.py`

### 功能定位

负责生成策略工作台图表数据。

### 主要职责

* 解析时间范围、间隔、指标参数
* 加载 Polymarket price samples
* 加载策略 stats samples
* 加载 Crypto overlay samples
* 加载 Finance overlay samples
* 合并多来源样本
* 应用 MA / MACD 等指标序列
* 附加样式配置
* 生成最终图表结构 `get_strategy_chart()`

### 它依赖的数据来源

* 实时市场库 `market_deltas`
* 行情宽表库 `Crypto` / `Stock`
* 策略统计库
* `polymarket_service.py` 提供的策略详情和图表能力配置

### 为什么它独立出来

因为图表不是简单查一张表，而是一个多源时序拼装问题。

### 总结

它是系统的 **图表采样与序列拼装引擎**。

---

## 6.12 `strategy_settings_service.py`

### 功能定位

负责策略设置的 schema 生成、payload 清洗和保存。

### 主要职责

* 基于 `STRATEGY_EDITABLE_FIELDS` 生成设置 schema
* 区分字段类型：number / boolean / text
* 校验前端传入 payload
* 调用 `update_strategy_detail()` 更新策略注册表 / input_json，旧监控表仅作兼容回退
* 写一条 `settings_updated` 事件到事件库

### 被谁使用

* `app.py`
* `strategy_workspace_service.py`

### 总结

它是系统的 **策略设置适配层**。

---

## 6.13 `strategy_workspace_service.py`

### 功能定位

负责把一个策略工作台需要的所有数据一次性拼出来。

### 主要职责

* 读取策略详情 `fetch_strategy_detail()`
* 读取图表默认值和能力声明
* 读取设置 schema
* 读取工作台预设
* 读取 backtest 占位信息
* 可选读取最近事件
* 统一输出 `get_strategy_workspace()`
* 附带 source_statuses，说明关键路径是否存在、价格来源和仓位来源是什么

### 为什么需要它

工作台页不是单一接口，而是多个来源的集合页。

### 总结

它是系统的 **工作台首页装配器**。

---

## 6.14 `workspace_preset_service.py`

### 功能定位

负责保存、读取、更新、删除工作台预设。

### 主要职责

* 初始化 `strategy_workspace_presets.db`
* 创建 `workspace_presets` 表
* 规范化 target 结构
* 规范化 config 结构
* 列出全局/策略级预设
* 读取单个预设
* 保存预设（同名可更新）
* 删除预设

### 预设内容包括

* 目标 market / strategy 信息
* chart 配置
* styles 配置
* indicators 配置

### 被谁使用

* `app.py`
* `strategy_workspace_service.py`

### 总结

它是系统的 **工作台预设存储层**。

---

## 6.15 `ws_market_sync_service.py`

### 功能定位

负责把 `main.py` 包装成后台线程服务，供 `app.py` 启动。

### 主要职责

* 解析当前 Web 设置中的实时市场库路径
* 解析策略监控库路径和表名
* 在线程里 `import main as ws_main`
* 用 Web 设置覆盖 `main.py` 的运行时变量
* 调用 `fetch_all_active_markets()`、`local_probability_filter()`、`fetch_my_holdings()`、`load_strategy_monitoring_candidates()`
* 创建 `WebSocketMonitor` 并 `run()`
* 暴露 `start()`、`is_running()`、`get_state()`

### 为什么它存在

因为 `main.py` 原本是独立运行脚本，而 Web 模式需要一个可以被后台线程启动的服务包装器。

### 总结

它是系统里 **连接 Web 层与实时采集层的桥接模块**。

---

## 6.16 `backtest_service.py`

### 功能定位

回测功能占位模块。

### 主要职责

* 返回回测占位配置 `get_backtest_placeholder()`
* 返回“尚未实现”的占位响应 `create_backtest_placeholder()`

### 当前状态

* 已预留接口
* 尚未实现真实回测执行

### 被谁使用

* `app.py`
* `strategy_workspace_service.py`

### 总结

它是系统的 **回测占位层**。

---

## 6.17 `main.py` 中的 `WebSocketMonitor`

虽然它不单独成文件，但它是当前系统最重要的运行类，必须单独说明。

### 核心职责

* 管理 token 优先级与 stream
* 初始化实时市场 SQLite schema
* 维护本地 orderbook
* 消费 WebSocket 数据
* 插入 delta 事件
* upsert 实时状态
* 动态 subscribe / unsubscribe
* 定时 watchdog 重扫
* 处理重连与心跳超时

### 核心表

#### `markets_state`

保存每个 `clobTokenId` 的当前最新状态。

#### `market_deltas`

保存每次变化的流水记录。

### 总结

它是系统的 **实时市场同步内核**。

---

## 7. 模块之间的串联图

## 7.1 Web 展示线

```text
前端页面 / JS
   ↓
app.py
   ├─ settings           → config_loader.py
   ├─ overview           → polymarket_service.py
   ├─ markets/resolve    → polymarket_service.py
   ├─ holdings           → polymarket_service.py
   ├─ strategies         → polymarket_service.py
   ├─ strategy update    → strategy_settings_service.py
   ├─ workspace          → strategy_workspace_service.py
   ├─ chart              → strategy_chart_service.py
   ├─ events             → strategy_event_service.py
   ├─ presets            → workspace_preset_service.py
   ├─ backtest           → backtest_service.py
   └─ live workspace SSE → polymarket_service.py + strategy_event_service.py
```

## 7.2 外部行情线

```text
app.py 启动 collector
   ↓
realtime_collector.py
   ├─ crypto_service.py
   ├─ finance_service.py
   └─ sqlite_store.py
        ↓
行情 SQLite (Crypto / Stock)
```

## 7.3 实时市场线

```text
app.py 启动 ws_market_sync
   ↓
ws_market_sync_service.py
   ↓
main.py
   ├─ fetch_all_active_markets()
   ├─ local_probability_filter()
   ├─ fetch_my_holdings()
   ├─ load_strategy_monitoring_candidates()
   └─ WebSocketMonitor.run()
        ↓
实时市场 SQLite (markets_state / market_deltas)
```

## 7.4 策略业务线

```text
策略配置 / 监控 SQLite + 实时市场 SQLite + 本地订单 SQLite + 策略统计库
   ↓
polymarket_service.py
   ├─ fetch_strategy_monitoring()
   ├─ fetch_strategy_detail()
   ├─ get_overview()
   └─ update_strategy_detail()
```

## 7.5 工作台线

```text
strategy_workspace_service.py
├─ 策略详情              ← polymarket_service.py
├─ 图表默认值/能力       ← polymarket_service.py
├─ 设置schema            ← strategy_settings_service.py
├─ 最近事件              ← strategy_event_service.py
├─ 统计库路径            ← strategy_stats_store.py
├─ 预设                  ← workspace_preset_service.py
└─ 回测占位              ← backtest_service.py
```

---

## 8. 职责边界总结

为了后续维护，建议始终遵守下面的职责边界：

### `app.py`

只负责：

* 路由
* 参数读取
* 调 service
* 返回 JSON / 模板 / SSE

### `main.py`

只负责：

* 外部市场同步
* WebSocket 运行
* 实时库写入

### `polymarket_service.py`

负责：

* 策略、市场、持仓、概览等业务装配

### `strategy_chart_service.py`

负责：

* 图表时序拼装

### `strategy_event_service.py`

负责：

* 事件记录与聚合

### `strategy_stats_store.py`

负责：

* 每策略独立统计库

### `config_loader.py`

负责：

* 设置和路径统一

### `sqlite_store.py`

负责：

* 宽表写入逻辑

---

## 9. 当前项目的核心理解

如果只用一句话概括这套项目：

> 这是一个以 Flask 为展示层、以 Polymarket 实时市场库为底座、以 `strategy_registry` + `strategy_legs` 为当前策略主表、以多种 service 进行拼装的本地量化监控与工作台系统。

更具体一点：

* `main.py` 负责把外部市场“搬进来”。
* `realtime_collector.py` 负责把外部行情“搬进来”。
* `polymarket_service.py` 负责把策略需要的数据“拼起来”。
* `app.py` 负责把这些结果“发给前端”。

---

## 10. 结论

这批 Python 文件不是互相平级的，而是分成四层：

### 第一层：入口层

* `app.py`
* `main.py`

### 第二层：桥接与装配层

* `ws_market_sync_service.py`
* `strategy_workspace_service.py`
* `polymarket_service.py`

### 第三层：功能服务层

* `config_loader.py`
* `http_client.py`
* `crypto_service.py`
* `finance_service.py`
* `realtime_collector.py`
* `sqlite_store.py`
* `strategy_chart_service.py`
* `strategy_event_service.py`
* `strategy_profit_engine.py`
* `strategy_settings_service.py`
* `strategy_stats_store.py`
* `workspace_preset_service.py`

### 第四层：占位/扩展层

* `backtest_service.py`

这就是当前**主应用链路中** Python 文件的职责图谱。

---

## 11. 前端架构说明

前端不是一个单页应用框架，而是 **Flask 模板 + 原生 JavaScript + 统一 CSS** 的结构。

也就是说，当前前端架构不是 React/Vue 那种前后端完全分离模式，而是：

* 后端 `app.py` 负责返回 HTML 模板
* 浏览器加载对应的静态 JS
* 静态 JS 再去请求后端 API / SSE
* 页面最终渲染出表格、图表、工作台和设置页

可以把前端理解成下面这条链：

```text
Flask 路由(app.py)
   ↓
HTML 模板(index.html / settings.html / watchlist.html / strategy_workspace.html)
   ↓
静态 JS/CSS(app.js / settings.js / watchlist.js / strategy_workspace_v2.js / workspace_v3_patch.js / polymarket_links.js / workspace_v3.css)
   ↓
调用后端 API / SSE
   ↓
渲染页面组件、表格、图表、按钮交互
```

---

## 12. 前端页面层

当前前端主要由 4 个页面模板构成。

### 12.1 `index.html`

#### 功能定位

系统首页，也可以理解为 Dashboard。

#### 主要内容

* 左侧导航栏：Dashboard / Watchlist / Settings
* Hero 区：系统标题、Collector 状态、跳转按钮
* 概览卡片区 `overviewCards`
* Crypto Live Feed 面板
* Finance Live Feed 面板
* 我的仓位面板
* 系统状态面板
* 策略监控面板
* 市场查询相关区域

#### 对应脚本

* `app.js`
* `polymarket_links.js`
* `styles.css`

#### 页面作用

这是整个系统的 **总览页**，负责把：

* 概览数据
* 实时行情数据
* 持仓数据
* 策略监控数据
* 市场搜索结果

集中展示出来。

---

### 12.2 `settings.html`

#### 功能定位

系统设置中心。

#### 主要内容

* API & 数据源 tab
* 钱包 & 策略 tab
* 刷新 & 采集 tab
* 表单字段包括：

  * Finnhub API keys
  * CoinGecko API key/header
  * 钱包地址
  * 行情 SQLite 路径
  * 本地订单数据库路径
  * 策略监控数据库路径
  * 实时市场 SQLite 路径
  * 策略监控表名
  * 策略统计库目录
  * symbols 与刷新周期等

#### 对应脚本

* `settings.js`
* `styles.css`

#### 页面作用

这是整个系统的 **配置入口页**，保存之后会直接驱动后端配置与采集行为。

---

### 12.3 `watchlist.html`

#### 功能定位

自选市场页。

#### 主要内容

* 左侧导航栏
* Hero 区
* 自选市场表格 `watchlistTable`
* 元信息 `watchlistMeta`
* 刷新按钮 `watchlistRefreshBtn`

#### 对应脚本

* `polymarket_links.js`
* `watchlist.js`
* `styles.css`

#### 页面作用

这是一个 **浏览器本地自选页**。

注意：
它不是后端数据库驱动，而是基于浏览器本地 `localStorage` 保存自选市场。

---

### 12.4 `strategy_workspace.html`

#### 功能定位

单策略工作台页。

#### 主要内容

* 左侧导航与页面标题
* 状态选择器 `workspaceStateSelect`（`Stop` / `Virtual` / `Real`）
* 工作台摘要区 `workspaceSummary`
* 图表面板 `workspaceCharts`
* 市场选择器
* 追踪市场区 `workspaceTrackedMarkets`
* 市场搜索结果区 `workspaceMarketResults`
* 预设管理区
* 设置表单区 `workspaceSettingsForm`
* 事件流区 `workspaceEvents`
* source 状态区 `workspaceSources`
* 回测占位区 `workspaceBacktest`

#### 对应脚本

* `strategy_workspace_v2.js`
* `workspace_v3_patch.js`
* `polymarket_links.js`
* `workspace_v3.css`

#### 页面作用

这是整个系统最复杂的页面，是 **策略详情、图表、事件、设置、预设与市场扩展选择的聚合工作台**。

---

## 13. 前端静态脚本层

## 13.1 `app.js`

### 功能定位

首页 Dashboard 的主控制脚本。

### 主要职责

* 获取首页 DOM 元素
* 定义格式化函数（数字、百分比、PnL、时间等）
* 渲染概览卡片 `renderCards()`
* 渲染通用表格 `renderTable()`
* 拉取 `/api/overview`
* 拉取 Crypto / Finance / holdings / markets / strategies 接口
* 渲染系统状态区 `renderSystemStatus()`
* 管理首页自动刷新
* 管理策略列表实时流与表格刷新动画
* 结合 `window.PolyMarketUi` 进行市场链接和自选行为

### 它和后端的关系

它是首页所有后端接口的直接消费者，对应后端主要是：

* `/api/overview`
* `/api/realtime/crypto`
* `/api/realtime/finance`
* `/api/polymarket/holdings`
* `/api/polymarket/markets`
* `/api/polymarket/strategies`
* 以及首页用到的实时流接口

### 总结

它是首页的 **页面控制器 + 数据渲染器**。

---

## 13.2 `settings.js`

### 功能定位

设置页控制脚本。

### 主要职责

* 控制 tab 切换
* 从 `/api/settings` 拉取设置
* 将设置填充到表单 `fillForm()`
* 收集表单数据并构造 payload
* POST 到 `/api/settings`
* 渲染保存状态信息

### 它和后端的关系

只围绕两个接口工作：

* `GET /api/settings`
* `POST /api/settings`

### 总结

它是设置页的 **表单适配与提交控制器**。

---

## 13.3 `polymarket_links.js`

### 功能定位

Polymarket 市场链接与本地自选功能公共脚本。

### 主要职责

* 规范化市场对象 `normalizeWatchlistMarket()`
* 构造市场 identity key
* 生成 Polymarket 外链 `buildPolymarketUrl()`
* 读写本地 watchlist（`localStorage`）
* 判断市场是否已加入自选
* 提供 add/remove/toggle watchlist 方法
* 通过 `window.PolyMarketUi` 暴露给其他页面脚本使用

### 被谁使用

* `app.js`
* `watchlist.js`
* `strategy_workspace_v2.js`

### 总结

它是整个前端的 **Polymarket 市场公共工具模块**。

---

## 13.4 `watchlist.js`

### 功能定位

自选页控制脚本。

### 主要职责

* 渲染 watchlist 表格
* 从 query string 中读取市场并加入自选
* 构造内部 watchlist 跳转链接
* 调用 `window.PolyMarketUi` 读写 localStorage 中的自选列表
* 提供删除、打开外链、聚焦当前市场等交互

### 数据来源

不是后端数据库，而是：

* `localStorage`
* URL query 参数

### 总结

它是自选页的 **本地状态控制器**。

---

## 13.5 `strategy_workspace_v2.js`

### 功能定位

策略工作台主控制脚本，是当前前端最核心、最复杂的 JS 文件。

### 主要职责

1. **页面状态管理**

   * `workspaceState`
   * `trackedMarkets`
   * `selectedOverlayState`
   * `seriesStyleState`
   * `chartLegendSelectedState`
   * 自动刷新状态与定时器
   * 工作台 header 状态选择器与策略状态同步

2. **基础格式化与状态持久化**

   * 数字/时间格式化
   * 本地 localStorage 状态读写
   * 图表显示模式保存
   * legend 显隐状态保存

3. **工作台数据加载**

   * 拉取 `/api/polymarket/strategies/<row_id>/workspace`
   * 渲染 header、summary、sources、backtest、settings 等
   * 同步 `strategy_registry.state` 到 header 状态 select

4. **图表功能**

   * 组织图表请求参数
   * 拉取 `/api/polymarket/strategies/<row_id>/chart`
   * 维护 ECharts 实例
   * 管理图表模式、series style、legend 状态、overlay 和指标
   * 处理稀疏历史样本：使用 `meta.from` / `meta.to` 定轴，`rows < 2` 时显示 symbol
   * 默认隐藏过多 overlay、派生 MACD 与辅助 series，避免首屏图例拥挤

5. **市场选择器功能**

   * 使用策略默认市场
   * condition_id / token_id / keyword 搜索市场
   * 添加/移除 trackedMarkets

6. **预设功能**

   * 保存/加载/删除 workspace preset
   * 同步目标 market、图表配置、样式和指标

7. **设置功能**

   * 渲染 settings schema
   * 提交策略设置保存

8. **事件流与自动刷新**

   * 拉取事件流
   * 管理自动刷新定时器
   * 在 chart / workspace / events 之间做分频刷新
   * 对连续重复 Print 做前端合并计数，保留最新时间

### 它和后端的关系

它是工作台页几乎所有后端能力的直接消费者，核心接口包括：

* `/api/polymarket/strategies/<row_id>/workspace`
* `/api/polymarket/strategies/<row_id>/chart`
* `/api/polymarket/strategies/<row_id>/events`
* `/api/polymarket/strategies/<row_id>`（更新设置）
* `/api/registry/strategies/<row_id>/state`（更新 `Stop` / `Virtual` / `Real`）
* `/api/polymarket/workspace-presets`
* `/api/polymarket/markets`
* `/api/polymarket/markets/resolve`
* 以及工作台对应的 live SSE

### 总结

它是前端的 **工作台总控脚本**。

---

## 14. 样式层：`styles.css`

### 功能定位

统一整个系统的视觉样式。

### 主要职责

* 定义全局主题变量（深色主题）
* 布局骨架：`.app-shell`、`.sidebar`、`.page`
* Hero 区样式
* 卡片、面板、按钮、badge、表单、tab 等通用组件样式
* Settings 页玻璃质感布局
* Dashboard / Watchlist / Workspace 共用基础样式

### 架构意义

它不是某个页面专属样式，而是整个系统的 **统一视觉层**。

---

## 14.1 工作台 V3 样式与补丁层

当前策略工作台没有更换模板，仍然是 `templates/strategy_workspace.html`。

加载顺序：

```text
workspace_v3.css
strategy_workspace_v2.js
workspace_v3_patch.js
```

职责边界：

* `strategy_workspace_v2.js`：核心状态、API 请求、图表渲染、状态切换、设置保存、预设保存。
* `workspace_v3_patch.js`：V3 展示层覆盖，包括 summary 布局、事件流合并、右侧面板和部分 DOM 重绘。
* `workspace_v3.css`：当前工作台视觉样式，包括布局、滚动条、图表面板、状态 select 和右侧抽屉。

修改规则：涉及业务状态、请求参数、chart option、状态切换时优先改 `strategy_workspace_v2.js`；涉及排版和视觉时优先改 `workspace_v3.css`；涉及 V3 展示覆写时再改 `workspace_v3_patch.js`。

---

## 15. 前后端如何串联

前后端串联不是抽象概念，而是下面这条清晰链路：

```text
Flask(app.py)
   ├─ 返回 index.html              → 浏览器执行 app.js
   ├─ 返回 settings.html           → 浏览器执行 settings.js
   ├─ 返回 watchlist.html          → 浏览器执行 watchlist.js + polymarket_links.js
   └─ 返回 strategy_workspace.html → 浏览器执行 strategy_workspace_v2.js + workspace_v3_patch.js + polymarket_links.js
```

然后浏览器中的 JS 再去请求后端 API：

```text
app.js
   ├─ /api/overview
   ├─ /api/realtime/crypto
   ├─ /api/realtime/finance
   ├─ /api/polymarket/holdings
   ├─ /api/polymarket/markets
   └─ /api/polymarket/strategies

settings.js
   └─ /api/settings

strategy_workspace_v2.js
   ├─ /api/polymarket/strategies/<row_id>/workspace
   ├─ /api/polymarket/strategies/<row_id>/chart
   ├─ /api/polymarket/strategies/<row_id>/events
   ├─ /api/registry/strategies/<row_id>/state
   ├─ /api/polymarket/markets
   ├─ /api/polymarket/markets/resolve
   ├─ /api/polymarket/workspace-presets
   └─ 对应 live SSE
```

这说明当前项目的前端不是独立服务，而是 **由 Flask 直接托管模板和静态文件，并通过 API/SSE 与后端服务层交互**。

---

## 16. 全局视角下的完整架构图

把前端也补进去后，整个系统可以画成下面这样：

```text
【浏览器前端】
HTML 模板 + JS + CSS
├─ index.html + app.js
├─ settings.html + settings.js
├─ watchlist.html + watchlist.js + polymarket_links.js
└─ strategy_workspace.html + workspace_v3.css + strategy_workspace_v2.js + workspace_v3_patch.js + polymarket_links.js

        ↓ 通过 API / SSE

【Flask 门面层】
app.py
├─ 页面路由
├─ JSON API
└─ SSE 实时流

        ↓ 调 service

【业务服务层】
config_loader.py
polymarket_service.py
strategy_workspace_service.py
strategy_chart_service.py
strategy_event_service.py
strategy_settings_service.py
workspace_preset_service.py
backtest_service.py
crypto_service.py
finance_service.py

        ↓ 读写本地库 / 触发后台采集

【后台采集层】
realtime_collector.py
ws_market_sync_service.py
   └─ main.py / WebSocketMonitor

        ↓

【本地数据层】
行情 SQLite
本地订单 SQLite
策略配置 / 监控 SQLite
实时市场 SQLite
策略统计 SQLite 目录

        ↓

【外部数据源】
Gamma API
Polymarket CLOB WS
Polymarket positions API
Binance
CoinGecko
Finnhub
```

---

## 17. 新增的全局理解结论

把前端加进来以后，整个项目最准确的理解应该是：

> 这是一个由 Flask 托管前端页面与后端接口、由后台线程持续采集行情和实时盘口、由本地 SQLite 作为统一数据底座、最终在 Dashboard / Watchlist / Workspace 三类页面中完成展示与交互的本地市场监控与策略工作台系统。

其中：

* Dashboard 负责总览
* Settings 负责配置
* Watchlist 负责浏览器本地自选
* Workspace 负责单策略工作台
* `app.py` 负责页面与 API 门面
* `main.py` 负责 Polymarket 实时市场写库
* `realtime_collector.py` 负责 Crypto / Finance 行情写库
* `services/*.py` 负责拼装与业务处理
* 前端 JS 负责消费 API 和 SSE，并完成最终渲染

这就是加入前端视角后的完整全局图。

---

## 18. `app.py` 的完整接口清单

上文的路由分组已经足够理解结构，但如果要做联调、排障或补前端功能，更实用的是把接口按类型完整列出来。

### 18.1 页面接口

* `GET /`
* `GET /settings`
* `GET /watchlist`
* `GET /strategies/<row_id>/workspace`

### 18.2 系统与配置接口

* `GET /api/health`：返回 Flask 侧健康状态、collector 是否运行、`ws_market_sync` 当前状态。
* `GET /api/settings`：读取当前 `web_settings.json` 归一化后的结果。
* `POST /api/settings`：保存 Web 设置，并返回保存后的标准化结果。

### 18.3 Dashboard / 行情接口

* `GET /api/overview`
* `GET /api/realtime/state`
* `GET /api/realtime/crypto`
* `GET /api/realtime/finance`
* `GET /api/crypto/quotes`
* `GET /api/finance/quotes`

其中：

* `/api/realtime/*` 读的是 `collector` 的内存态。
* `/api/*/quotes` 是按请求即时向外部行情源取数。

### 18.4 Polymarket 市场与策略接口

* `GET /api/polymarket/markets`
* `GET /api/polymarket/markets/resolve`
* `GET /api/polymarket/holdings`
* `GET /api/polymarket/strategies`
* `GET /api/polymarket/strategies/<row_id>`
* `POST /api/polymarket/strategies/<row_id>`
* `GET /api/polymarket/strategies/<row_id>/workspace`
* `GET /api/polymarket/strategies/<row_id>/chart`
* `GET /api/polymarket/strategies/<row_id>/events`

### 18.5 工作台预设与回测接口

* `GET /api/polymarket/workspace-presets`
* `POST /api/polymarket/workspace-presets`
* `GET /api/polymarket/workspace-presets/<preset_id>`
* `DELETE /api/polymarket/workspace-presets/<preset_id>`
* `GET /api/polymarket/strategies/<row_id>/backtest`
* `POST /api/polymarket/strategies/<row_id>/backtest`
* `GET /api/polymarket/strategies/<row_id>/backtest/results`

需要特别注意：

* 回测接口目前仍是占位接口，真实执行尚未落地。
* 预设接口既支持全局预设，也支持绑定具体策略的预设。

### 18.6 SSE 接口

* `GET /api/live/strategies`
* `GET /api/live/strategies/<row_id>/workspace`

---

## 19. SSE 事件约定

当前前端除了普通 API，还依赖两个 SSE 通道。

### 19.1 首页策略流：`/api/live/strategies`

事件类型：

* `rows`：推送轻量策略列表，供首页监控表格增量刷新。
* `error`：推送服务端错误信息。

推送特点：

* 约每 5 秒刷新一次。
* 服务端会把 `fetch_strategy_monitoring()` 的结果裁成轻量字段后再推送。

### 19.2 工作台策略流：`/api/live/strategies/<row_id>/workspace`

事件类型：

* `summary`：推送策略摘要快照，例如盘口、仓位、PnL、价格来源。
* `event_append`：当检测到最新事件变化时，追加一条事件。
* `error`：推送服务端错误信息。

推送特点：

* 约每 3 秒轮询一次策略详情和最近事件。
* 该接口不是直接转发底层 WebSocket，而是用 Flask SSE 对现有 service 做二次封装。

---

## 20. 启动方式与运行链路

### 20.1 Web 模式

最常用的启动入口是：

* `python app.py`

其运行顺序可以理解为：

1. 创建 Flask 应用
2. 启动 `ws_market_sync.start()`
3. 启动 `collector.start()`
4. 启动 `virtual_runner.start()`
5. 在 `127.0.0.1:5001` 提供页面、API 和 SSE

也就是说，Web 模式会同时拉起：

* Flask 门面层
* 行情采集线程
* Polymarket 实时市场同步线程
* Virtual 策略沙盒调度与虚拟成交线程

### 20.2 实时采集单跑模式

如果只想跑 Polymarket 实时市场采集，可以直接运行：

* `python main.py`

其运行顺序是：

1. 拉全量活跃市场
2. 做概率过滤
3. 加入持仓候选
4. 加入策略监控候选
5. 启动 `WebSocketMonitor.run()`

### 20.3 浏览器前端的实际访问方式

前端并不是独立 dev server，而是由 Flask 托管：

* 浏览器访问页面路由
* 页面加载 `static/*.js`
* JS 再调用 Flask 暴露的 API / SSE

---

## 21. 配置来源与优先级

这个项目有两层配置来源：

### 21.1 `config.json`

主要承担：

* API 基础地址
* WebSocket 参数
* 默认数据库配置
* 过滤阈值
* MongoDB / SQLite 基础开关

### 21.2 `web_settings.json`

主要承担：

* 页面设置页保存的路径类配置
* 钱包列表
* symbols 列表
* 刷新周期
* API key
* 策略监控表名等运行时设置

### 21.3 实际优先级

运行时通常是：

1. 先读取 `config.json`
2. 再读取 `web_settings.json`
3. 用 `web_settings.json` 覆盖 Web 模式下更贴近用户操作的路径与运行参数

因此：

* `main.py` 虽然有自己的 `config.json` 入口，但在 Web 模式下会被 `config_loader.py` 和 `ws_market_sync_service.py` 覆盖关键路径。
* 真正影响当前 Web 系统读写位置的，通常是设置页保存出来的 `web_settings.json`。

### 21.4 配置模块的额外职责

`config_loader.py` 不只是“读配置”，还负责：

* 相对路径转绝对路径
* 自动创建目录 / 文件
* 列表字段清洗
* 旧字段 `strategy_option_sqlite_db_path` 的兼容读取

---

## 22. 实时市场 SQLite 最小 schema 约定

对维护者来说，最关键的本地库是实时市场 SQLite。当前至少要知道两张核心表的职责和最小字段。

### 22.1 `markets_state`

用途：

* 保存每个 `clobTokenId` 的最新快照

最关键字段：

* `clobTokenId`
* `condition_id`
* `stream`
* `question`
* `category`
* `target_option_json`
* `target_price`
* `market_json`
* `raw_clob_json`
* `depth_metrics_json`
* `updated_at_utc`
* `status`
* `outcome_side`
* `now_bid`
* `now_ask`
* `best_bid`
* `best_ask`
* `last_price`
* `spread_c`

理解方式：

* 它是一张“最新状态表”。
* 同一个 `clobTokenId` 只保留当前一条最新记录。

### 22.2 `market_deltas`

用途：

* 保存实时变化流水

最关键字段：

* `id`
* `timestamp`
* `stream`
* `condition_id`
* `clobTokenId`
* `event_type`
* `payload_json`
* `reason`
* `outcome_side`
* `now_bid`
* `now_ask`
* `best_bid`
* `best_ask`
* `last_price`
* `spread_c`

理解方式：

* 它是一张“历史事件表”。
* 图表、事件流、追溯分析更多依赖它，而不是只依赖 `markets_state`。

### 22.3 两张表的关系

可以把它们理解成：

* `markets_state` = 当前最新状态
* `market_deltas` = 历史变化轨迹

前者适合实时展示，后者适合历史采样、事件提取和回放。

---

## 23. 未纳入主链路的代码

为了避免“文档说完整，但仓库里还有别的文件”的歧义，这里单独说明。

### 23.1 辅助脚本

例如：

* `test.py`
* `test2.py`
* `test_ws.py`
* `fetch_examples.py`
* `dump_target.py`

这些更像调试、试验、抓样本或临时验证脚本，不属于主系统运行所必需的链路。

### 23.2 `参考/`

这一目录更接近历史参考脚本或能力来源样例，不参与当前 Flask 主系统直接运行。

### 23.3 `project/`

仓库里还存在另一套结构化应用雏形：

* `project/run.py`
* `project/app/blueprints/*`
* `project/app/services/*`

它更像另一条试验性或迁移中的实现方向，而不是当前 `app.py + services/ + templates/ + static/` 这条主应用链路本身。

因此，阅读和维护当前系统时，应优先以本文前面描述的主链路为准。

---

## 24. 常见排障思路

### 24.1 首页有页面但没有数据

优先检查：

* `/api/health` 是否返回 `ok`
* `collector_running` 是否为真
* `ws_market_sync` 状态是否正常
* `web_settings.json` 中数据库路径是否可写

### 24.2 策略详情有基础信息但没有实时盘口

优先怀疑：

* `market_realtime_db_path` 是否配置错
* `main.py` / `ws_market_sync_service.py` 是否真的在写 `markets_state`
* `strategy_legs` 或旧监控表 fallback 中的 `condition_id` / token 绑定是否缺失

### 24.3 工作台事件流为空

优先检查：

* `strategy_event_service.py` 的本地事件库是否可写
* 实时市场库中的 `market_deltas` 是否有对应策略相关记录
* 工作台页订阅的 SSE 是否建立成功

### 24.4 图表有框架但没有序列

优先检查：

* `market_deltas` 是否有时间序列数据
* 行情宽表库 `Crypto` / `Stock` 是否存在对应 symbol
* 策略统计库目录下是否已经产生对应策略 `.db`

### 24.5 设置保存了但行为没变化

优先检查：

* 设置是否真的写入 `web_settings.json`
* 后台线程是否只在启动时读取了某些配置
* 当前观察的问题属于 Flask 进程内状态、collector 状态，还是 `main.py` 采集状态

---

## 25. 首页期权字典模块补充

新增了一条围绕 `Polymarket Dictionary` 的首页链路，用于把全市场字典抓取、落库和更新进度展示接入当前主系统。

### 25.1 新增设置项

`web_settings.json` / 设置页中新增：

* `polymarket_dictionary_db_path`

该字段默认指向项目根目录下的 `Data/PolyMarketDictionary.db`，并支持在设置页中以相对路径形式填写。

### 25.2 新增服务层

新增文件：

* `services/polymarket_dictionary_service.py`

职责：

* 读取 `polymarket_dictionary_db_path`
* 初始化 `polyMarket_Dictionary` 表和唯一索引
* 在每次刷新开始前先清理历史脏数据
* 按页抓取 Polymarket markets 接口
* 补抓 event tags / market tags，生成 `Subject`
* 将新市场写入字典库
* 在内存中维护更新状态、日志和统计信息

当前字典刷新流程偏向首页快速可用，不再默认全量扫历史市场：

* 按 `endDate` 删除已经到期的期权记录
* 按 `condition_id` 去重，仅保留同一 `condition_id` 的最早一条记录
* 清理完成后再创建/复用唯一索引，避免旧库中已有重复数据时索引创建失败
* 若本次确实清理了过期或重复数据，会在刷新日志中记录删除数量
* 默认只抓取 `closed=false` 的 active 市场，避免每次手动刷新都翻扫 closed 历史市场导致更新很慢
* 即使远端返回了过期市场，插入前也会再次按 `endDate` 过滤，并在状态中累计 `skipped_expired`
* 若需要重建历史归档，可在服务层打开 `REFRESH_CLOSED_MARKETS` 后执行低频全量刷新；不建议作为首页按钮的默认行为

### 25.3 新增接口

`app.py` 新增了 3 个与首页字典模块配套的接口：

* `GET /api/polymarket/dictionary`
  返回当前字典数量、数据库路径、文件时间、最近日志和运行状态。
* `POST /api/polymarket/dictionary/update`
  启动后台更新线程，不阻塞首页请求。
* `GET /api/live/polymarket/dictionary`
  通过 SSE 持续推送字典更新状态，供首页实时显示更新进展。

### 25.4 首页前端改动

首页 `templates/index.html` 和 `static/app.js` 新增了“Polymarket 期权字典”面板，支持：

* 展示字典总数量
* 展示字典数据库路径
* 手动触发更新
* 实时显示当前阶段、页数、抓取量、新增量和日志输出
* 实时显示跳过过期市场数量，便于判断远端返回数据是否含有历史噪声

因此，这个模块现在已经属于主链路的一部分，而不再只是 `参考/Normal_PolyMarketToSQLite.py` 里的离线参考能力。

---

## 26. 首页策略链路轻量化改造

这一轮调试最终确认：首页卡顿的根因并不只是远端仓位接口，还包括首页策略表在加载阶段误走了重型策略构建路径。

### 26.1 这次真实定位出的慢点

调试日志表明，首页首屏曾先后卡在以下几层：

* `fetch_active_markets()` 远端活跃市场拉取
* `_enrich_monitoring_row_tokens()` 逐行走 Gamma 反查市场身份
* `_get_live_position_cache()` 远端钱包持仓接口
* `_build_strategy_item()` 内部调用 `_select_strategy_ws_snapshot()`，触发对 `markets_state` 的重型读取

也就是说，首页最初并不是“轻量状态页”，而是在首屏阶段串行做了多种重操作。

### 26.2 首页现在的目标原则

首页和策略表现在遵循一个明确原则：

* 先显示本地可得数据
* 先返回页面需要的基础结构
* 不阻塞等待远端仓位
* 不阻塞等待 websocket 实时快照
* 不阻塞等待 Gamma 身份补全

真正的详情、工作台、图表仍然允许走重路径，但首页不再承担这些成本。

### 26.3 市场身份确认的新优先级

`services/polymarket_service.py` 中，策略市场身份确认已经改成如下优先顺序：

* 活跃市场内存 / 本地 snapshot
* `Polymarket Dictionary`
* Gamma 反查（仅兜底）

其中 `Polymarket Dictionary` 已被接入 `_load_strategy_market_index()`，可直接利用：

* `condition_id`
* `yes_token`
* `no_token`
* `question`
* `Subject`

来补齐完整二元市场身份。

### 26.4 首页仓位读取的新优先级

首页和策略列表不再把远端钱包仓位当作首屏强依赖，而是优先采用本地来源：

1. `strategy_monitoring` 表中的本地字段：
   * `Yes_now_qty`
   * `No_now_qty`
   * `Yes_avg_cost`
   * `No_avg_cost`
2. 本地订单库 `PolyMarket_OrderList.db`
   * 聚合 `buy_qty / sell_qty / buy_cost / sell_cost / trades`
   * 仅在监控表本地仓位为空时作为估算兜底
3. 本地策略快照库 `strategy_metrics_dbs/*.db`
   * 使用最近一次有效持仓快照作为回退来源
4. 远端仓位接口
   * 不再阻塞首页首屏
   * 通过后台异步刷新更新缓存

因此首页现在允许先显示本地仓位，再等待后续远端同步结果覆盖。

### 26.5 首页策略表与详情页的正式分流

这是本次最关键的结构调整。

`_build_strategy_item()` 现在区分两种模式：

* `include_realtime_prices=False`
  * 用于首页 `overview`
  * 用于首页策略表 `strategies`
  * 用于 `live_strategies()` 的 SSE 轻量列表流
  * 不再触发 websocket 实时快照重建
* `include_realtime_prices=True`
  * 用于 `fetch_strategy_detail()`
  * 用于工作台、图表等重交互页面
  * 仍然允许读取 websocket 实时快照

换句话说：首页策略表已经从“重型策略构建”里正式拆了出来。

### 26.6 远端仓位现在如何异步刷新

`_get_live_position_cache()` 已调整为：

* 首页默认 `allow_remote_positions=False`
* 首屏先返回本地仓位 / 本地订单估算 / 历史快照
* 同时后台线程触发远端仓位刷新
* 刷新成功后写回 `_LIVE_POS_CACHE`
* 后续首页轮询或 SSE 自然会拿到更新后的仓位结果

对应新增日志：

* `[SV][live_pos] async_refresh ...`

这表示远端仓位已经从“阻塞首屏”改成“后台刷新缓存”。

### 26.7 这次改造后的结果含义

首页如果仍出现问题，应该优先按下面顺序判断：

* 若 `fetch_active_markets` 很慢，说明市场缓存未命中
* 若 `index=...` 很慢，说明活跃市场索引或 Dictionary 索引构建异常
* 若 `live_ctx=...` 很慢但 `allow_remote_positions=False`，需要检查是否仍有旧进程或旧逻辑在运行
* 若 `overview/strategies` 仍长时间不返回，则重点检查是否还有首页路径误用了 `include_realtime_prices=True`

### 26.8 当前架构上的最终结论

目前系统已形成如下职责边界：

* 首页：
  * 轻量、本地优先、异步补齐
* 策略详情：
  * 可接受较重的实时价格和实时仓位处理
* 工作台 / 图表：
  * 可接受 websocket snapshot、事件流、图表样本拼装等重逻辑

因此以后若继续扩展首页功能，原则上应坚持：

* 首页只消费轻量聚合结果
* 不把详情页级别的重计算直接挂到首页首屏

---

## 27. 订单状态机与新订单存储层

### 27.1 新增文件

* `services/order_store.py`

### 27.2 OrderStatus 枚举（10 个状态）

正向链路：`created → submitted → open → partially_filled → filled → reconciled`

异常/取消链路：`open / partially_filled → cancel_requested → cancelled`、`submitted / open → failed`、`open → expired`

### 27.3 状态转移矩阵

严格定义每个状态允许流向的下一状态集合，通过 `_TRANSITIONS` 字典维护。

对外暴露两个校验函数：

* `can_transition(current, target) -> bool`：判断转移是否合法
* `is_terminal(status) -> bool`：判断是否为终态（`cancelled / failed / expired / reconciled`）

### 27.4 数据库表结构

**`orders` 表**主要字段：

* 标识：`order_id`（UUID hex）、`client_order_id`（唯一）、`remote_order_id`、`tx_hash`
* 关联：`strategy_id`、`condition_id`、`token_id`
* 方向：`side`（BUY/SELL）、`direction`（yes/no）
* 状态：`status`
* 价格/数量：`price`、`qty`、`filled_qty`、`avg_fill_price`、`remaining_qty`
* 时间戳：`created_at`、`submitted_at`、`opened_at`、`filled_at`、`cancelled_at`、`reconciled_at`、`updated_at`

**`order_events` 审计表**：每次 `transition_order()` 调用都写入一条记录，字段包括 `from_status`、`to_status`、`filled_qty`、`message`。

两张表与旧 `polyMarket_OrderList` 共存于同一个 SQLite 文件（路径由 `order_list_db_path` 配置项决定）。

### 27.5 CRUD 接口

* `create_order()`：创建订单，初始状态为 `created`
* `transition_order()`：状态转移，非法转移抛 `ValueError`，同时写审计事件
* `get_order()` / `get_order_by_client_id()`：按 ID 查单条
* `list_orders()`：按 strategy_id / token_id / status 过滤
* `list_open_orders()`：返回所有非终态订单
* `get_order_events()`：返回某订单的完整审计链

### 27.6 aggregate_filled_by_token()

为 profit engine 提供与旧接口兼容的聚合结果，格式：

```python
{token_id: {buy_qty, sell_qty, buy_cost, sell_cost, trades}}
```

只聚合 `filled / reconciled / partially_filled` 状态的订单。

### 27.7 migrate_legacy_orders()

一次性将旧 `polyMarket_OrderList` 表的记录导入新 `orders` 表：

* 旧表保留不动
* 按 `IsSuccess` / `tx_hash_hint` 判断映射为 `filled` 或 `failed`
* 用 `legacy_{token[:16]}_{nowtime}` 作为 `client_order_id`，保证幂等（重复执行自动跳过）
* 返回 `{migrated, skipped, error}` 统计

### 27.8 strategy_profit_engine.py 适配

`fetch_local_order_map()` 现在采用双层优先级：

1. 调用 `order_store.aggregate_filled_by_token()`，若新表有数据则直接返回
2. 新表为空时自动 fallback 到旧 `polyMarket_OrderList` 的原有聚合逻辑

上层调用方（`polymarket_service.py` 等）无需感知这一变化，接口签名和返回格式保持不变。

---

## 28. 双中心问题修复与统一数据源层

### 28.1 背景

系统中同时存在三个策略数据中心：

| 数据源 | 角色 |
|--------|------|
| `strategy_registry` + `strategy_legs` | 新策略注册中心（CRUD 入口） |
| `monitoring` / `polyMarket_Monitoring` | 旧监控表（读取路径） |
| `strategy_monitoring_compat` | 兼容视图（无消费者，形同虚设） |

旧读取路径（profit engine、chart service、WS 订阅、workspace）全部绕过新表，导致写入新表的数据对这些服务不可见。

### 28.2 新增文件

**`services/strategy_data_source.py`** — 统一读写层，所有消费方通过此模块访问策略数据。

主要接口：

| 函数 | 说明 |
|------|------|
| `connect(readonly=False)` | 返回连接，自动建表、自动 ALTER 添加持仓字段 |
| `list_strategies(state_filter=None)` | 返回所有策略及其 legs |
| `get_strategy(strategy_id)` | 返回单个策略及其 legs |
| `get_all_tokens()` | 返回所有 Virtual/Real 策略的 token 列表，供 WS 订阅 |
| `batch_update_positions(updates)` | 批量写持仓数据回 `strategy_legs` |
| `update_leg_positions(...)` | 单条 leg 持仓更新 |
| `update_strategy_profit(strategy_id, profit)` | 写 `realized_profit` 回 `strategy_registry` |
| `strategy_to_flat_dict(strategy)` | 将新表结构转换为旧代码兼容的 flat dict |
| `list_strategies_flat()` | 返回 flat dict 列表，供旧消费方直接使用 |

### 28.3 strategy_legs 新增持仓字段

通过 `_migrate_position_columns()` 幂等 ALTER，对已有表自动添加：

| 字段 | 类型 | 说明 |
|------|------|------|
| `yes_qty` | REAL DEFAULT 0 | Yes 方向持仓量 |
| `no_qty` | REAL DEFAULT 0 | No 方向持仓量 |
| `yes_avg_cost` | REAL | Yes 均价（策略买入成本） |
| `no_avg_cost` | REAL | No 均价 |
| `yes_current_price` | REAL | Yes 最新价（快照） |
| `no_current_price` | REAL | No 最新价 |
| `unrealized_pnl` | REAL DEFAULT 0 | 未实现盈亏 |
| `position_source` | TEXT DEFAULT '' | 数据来源标记 |
| `position_updated_at` | TEXT | 持仓数据最后更新时间 |

每次 `compute_and_persist_strategy_profit()` 运行后，持仓数据写回 `strategy_legs`，作为离线兜底缓存。

### 28.4 各服务改造

**`strategy_profit_engine.py`**

`compute_and_persist_strategy_profit()` 改为从 `strategy_data_source.list_strategies()` 读取，计算完成后通过 `batch_update_positions()` 写回持仓，通过 `update_strategy_profit()` 写回 `realized_profit`。不再直接操作旧监控表。

**`polymarket_service.py`**

- `_load_strategy_monitoring_rows()` 入口加新路径分支：新表有数据时优先走 `strategy_data_source`，旧表作为 fallback。
- `fetch_strategy_detail()` 入口加新路径分支：优先从 `strategy_data_source.get_strategy()` 读取，旧路径作为 fallback。

**`main.py`**

- `_resolve_existing_monitoring_table()` 优先返回 `strategy_registry`（有数据时）。
- `load_strategy_monitoring_from_db()` 优先从 `strategy_data_source.get_all_tokens()` 读取 token 列表，旧表作为 fallback。

**`ws_market_sync_service.py`**

无需改动，调用链已通过 `main.py` 的改造自动走新路径。

### 28.5 持仓数据的语义说明

当前 `strategy_legs` 上的持仓字段存储的是**钱包级总持仓**（`position_source = 'wallet_api'`），不是策略级归因持仓。

这意味着：如果多个策略持有同一个 token，各策略的 `yes_qty` 会读到相同的钱包总量。这是当前的已知限制。

未来如需策略级归因，需要在订单录入时记录 `strategy_id`，并从 `order_store` 按策略聚合计算持仓量和成本价（路径 2，待实现）。

### 28.6 兼容视图状态

`strategy_monitoring_compat` 视图仅在 `migrate_to_strategy_tables.py` 中创建，无任何业务代码消费，不建议扩大依赖。迁移完成后可直接废弃。

---

## 29. 策略状态机与虚拟盘表

### 29.1 状态机

`strategy_registry.state` 三态：`Stop / Virtual / Real`。

切换规则：
- 所有状态两两可切
- 监控首页 `Mode` 列与工作台 header 状态 select 使用同一套逻辑
- Virtual ↔ Real、Stop -> Real、Real -> Stop 等高风险切换需前端弹确认框，仓位、订单、PnL 不迁移
- `VirtualRunner` 只调度 `state=Virtual` 的策略

后端校验在 `strategy_registry_service.update_strategy_state()` 中执行，当前只校验目标状态必须属于 Stop / Virtual / Real，非法枚举返回 HTTP 400。当前源码的 `strategy_registry` 建表定义没有 `ever_real` 字段；历史文档中“进入 Real 后永久置灰 Virtual”的描述不再代表当前实现。

### 29.2 前端状态控件

策略监控首页和工作台都使用三态 select：
- 策略监控首页：`Mode` 列 `.state-select`
- 策略工作台：header 中的 `#workspaceStateSelect`
- 当前状态通过 `state-Stop` / `state-Virtual` / `state-Real` class 着色
- 切换成功后刷新对应页面数据，确保 summary、事件流和 source 状态同步

颜色：Real=浅绿，Virtual=浅蓝，Stop=灰色 / slate。

### 29.3 虚拟盘数据表

五张表在 `services/strategy_data_source.py` 的 `_DDL_VIRTUAL` 中定义，每次 `connect()` 幂等建表，与现有表共存于同一 SQLite 文件。

| 表名 | 说明 |
|---|---|
| `strategy_virtual_account` | 每策略一行，记录虚拟现金/权益/PnL/累计手续费 |
| `strategy_virtual_positions` | 按 `(strategy_id, leg_index, side)` 唯一，平均成本法 |
| `strategy_virtual_orders` | 虚拟订单流水，含手续费字段，status=filled/blocked/failed |
| `strategy_virtual_events` | 策略事件流（actions/print/error/settle），支持相邻去重 |
| `strategy_virtual_ticks` | 每次调度运行日志，记录 FunctionJson 原始输出与执行结果 |

### 29.4 新增字段

`_build_strategy_item()` 返回值至少包含 `state` 与 `strategy_id`，供前端状态 select 正确渲染并调用注册表 API。

### 29.5 策略删除

首页策略表每行操作列新增「删除」按钮：

- **后端**：`DELETE /api/registry/strategies/<strategy_id>` → `strategy_registry_service.delete_strategy()`，同时级联删除 `strategy_legs`。
- **腿替换保护**：`PUT /api/registry/strategies/<strategy_id>/legs` 会比较 `leg_index`、`condition_id`、`yes_token`、`no_token`。如果 leg 身份变化，会清理该策略的 `strategy_virtual_account`、`strategy_virtual_positions`、`strategy_virtual_orders`、`strategy_virtual_events`、`strategy_virtual_ticks`，避免旧 `(strategy_id, leg_index)` 虚拟盘数据错位。
- **前端**：`static/app.js` 中 `strategyTable` 的 click 事件委托，点击后弹确认框，确认后调用 DELETE 接口，成功则刷新策略列表。
- **样式**：`static/styles.css` 中 `.table-link-button.danger-btn`，红色调，与工作台按钮并排显示。

---

## 30. 虚拟盘运行系统

### 30.1 整体架构

```
VirtualRunner（定时轮询，每 N 秒）
    ↓
1. 读取 state=Virtual 的策略（strategy_registry + strategy_legs）
    ↓
2. VirtualContextBuilder 构造 UseData（五层注入）
    ↓
3. 构造 node，调用 SandboxRun.run_node()
   → FunctionJson（actions / print / wake_reason）
    ↓
4. 按 leg 路由，执行虚拟成交（VirtualExecution）
   → 写 virtual_orders / positions / account
    ↓
5. 写事件流 virtual_events（相邻去重）
6. 写 tick 日志 virtual_ticks
```

### 30.2 新增服务文件

| 文件 | 职责 |
|---|---|
| `services/virtual_runner.py` | 调度循环，读取 Virtual 策略，串联各层 |
| `services/virtual_execution.py` | 虚拟成交引擎，写五张虚拟盘表，计算手续费 |
| `services/virtual_context_builder.py` | UseData 上下文注入层（Tier 1~5 组装） |

### 30.3 UseData 上下文注入层

调度器在调用沙盒前，由 `VirtualContextBuilder` 自动组装 `UseData` 字典，按以下优先级注入（高→低）：

```
Tier 1 盘口（per-leg）> Tier 2 预算派生 > Tier 3 时间 > Tier 4 外部行情 > Tier 5 input_json > 策略代码默认值
```

#### Tier 1 — 期权盘口（多腿，`_L{n}` 后缀）

每腿注入一份，`_L0` 同时作为无后缀别名（向后兼容单腿策略）：

```
Yes_now_ask_L{n}              No_now_ask_L{n}
Yes_now_bid_L{n}              No_now_bid_L{n}
Yes_now_Qty_L{n}              No_now_Qty_L{n}
Yes_now_avgPrice_L{n}         No_now_avgPrice_L{n}
Yes_OpenBuyOrdersQty_L{n}     No_OpenBuyOrdersQty_L{n}
Yes_OpenSellOrdersQty_L{n}    No_OpenSellOrdersQty_L{n}
Yes_depth_ask_1c_usd_L{n}     No_depth_ask_1c_usd_L{n}
Yes_depth_bid_1c_usd_L{n}     No_depth_bid_1c_usd_L{n}
Yes_Now_Pos_L{n}              No_Now_Pos_L{n}
Yes_Now_CostPos_L{n}          No_Now_CostPos_L{n}   # qty × avg_price（成本口径）
BudgetCap_L{n}
Enddate_L{n}
day_to_end_L{n}
hour_to_end_L{n}
```

#### Tier 2 — 预算派生（向后兼容旧策略代码）

```
Yes_Max_BudgetCap = leg.budget_cap
No_Max_BudgetCap  = leg.budget_cap
Yes_Min_BudgetCap = 0
No_Min_BudgetCap  = 0
```

#### Tier 3 — 时间（全局）

```
NowTime          # ISO 8601 UTC
Enddate          # 主腿（L0）市场到期时间
day_to_end       # 主腿浮点天数
hour_to_end      # 主腿小时数
```

#### Tier 4 — 外部行情（全局，realtime_collector 注入）

```
Price_{SYMBOL}        # 现价
McapUsd_{SYMBOL}      # 市值
Vol24hUsd_{SYMBOL}    # 24h 成交额（USD）
Change24h_{SYMBOL}    # 24h 涨跌幅（%）
FdvUsd_{SYMBOL}       # 完全稀释市值（Crypto 专用）
```

#### Tier 5 — 用户自定义参数

来自 `strategy_registry.input_json`，按策略代码 `Inputs` 声明的名字取值。

### 30.4 FunctionJson 多腿扩展

`actions` 里加 `leg` 字段，缺省时默认 `leg=0`：

```json
{
  "actions": [
    {"type": "SETPOS", "leg": 0, "side": "Yes", "pct": 0.5},
    {"type": "SETPOS", "leg": 1, "side": "No",  "pct": 0.3}
  ],
  "print": ["..."],
  "wake_reason": null
}
```

执行层按 `leg` 字段路由到对应 `strategy_virtual_positions` 行。

### 30.5 手续费模型

虚拟盘全部按 taker 立即成交：

```
fee = qty × fee_rate × price × (1 - price)
```

| 市场类别 | taker 费率 |
|---|---|
| Crypto | 0.072 |
| Sports | 0.03 |
| Finance / Politics / Tech / Mentions | 0.04 |
| Economics / Culture / Weather / Other | 0.05 |
| Geopolitics | 0 |

执行层支持按 `market_category` 做上述映射；但当前 `virtual_runner.py` 尚未向 `execute_actions()` 传入具体市场类别，未传类别时按默认费率 `0.05` 执行。若后续需要精确费率，需要在策略腿或市场上下文中补充并传递 `market_category`。

`strategy_virtual_orders` 新增字段：`fee_rate`、`gross_notional`、`fee`、`net_cash_change`、`liquidity_role`。

`strategy_virtual_account` 新增字段：`total_fees_paid`。

### 30.6 事件流去重规则

写入 `strategy_virtual_events` 前，查同 `strategy_id + event_type` 的最近一条记录，若 `content_hash`（`sha256(content)[:16]`）相同则跳过，不写入。

### 30.7 虚拟盘 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/virtual/strategies/<id>/account` | 虚拟账户状态 |
| GET | `/api/virtual/strategies/<id>/positions` | 虚拟持仓列表 |
| GET | `/api/virtual/strategies/<id>/orders` | 虚拟订单流水（支持分页） |
| GET | `/api/virtual/strategies/<id>/events` | 事件流（支持 event_type 过滤） |
| GET | `/api/virtual/strategies/<id>/ticks` | Tick 运行日志列表 |
| POST | `/api/virtual/strategies/<id>/reset` | 重置虚拟账户（清空持仓/订单/账户） |

### 30.8 当前盘口一致性与本地 WS 保鲜

策略相关页面和虚拟盘运行时必须共用同一套当前盘口口径：

1. CLOB `/book` 当前 orderbook 是第一价格源，由 `services/clob_orderbook_service.py` 读取 Yes / No token 的 bid、ask 和挂单量。
2. 本地实时库 `markets_state` / `market_deltas` 是 WS 落库后的可观测副本；WS 不可用时，`WebSocketMonitor` 会用 CLOB REST `/book` 对当前分片 token 做 fallback 刷新，并写入 `event_type=book_rest_fallback`。
3. Gamma / 字典快照只负责市场元数据和最后价格兜底，不能覆盖 CLOB 或本地实时库中的当前盘口。

涉及模块：

| 模块 | 一致性要求 |
|------|-----------|
| `services/polymarket_service.py` | `fetch_strategy_detail()` / Dashboard Legs Snapshot 优先读取 CLOB `/book`，`yes_mark` / `no_mark` 与 PNL 使用同一 ask 口径 |
| `services/strategy_workspace_service.py` | `legs_data` 从 `fetch_strategy_detail()` 的 `legs_snapshot` 回填，避免再次读取 `strategy_legs` 缓存价 |
| `services/virtual_context_builder.py` | 组装 `UseData` 时用 CLOB `/book` 覆盖本地 `market_deltas` 的当前盘口 |
| `services/ws_market_sync_service.py` | Web 进程只订阅策略/持仓显式 token，不再混入全市场高低概率扫描 |
| `main.py` / `WebSocketMonitor` | 分片重连后重新订阅当前 token 集；WS 失败时执行 REST fallback 保鲜 |

排查“官网价格与本地价格对不上”时，先看 `/api/health`：

| 字段 | 正常含义 | 异常信号 |
|------|----------|----------|
| `thread_alive` | 线程还在 | 只能说明线程没死，不能说明行情新鲜 |
| `monitor.token_count` | 当前订阅 token 数 | 数量异常巨大通常表示错误混入了全市场扫描 |
| `monitor.last_subscribe_at` | 最近成功发送订阅 | 为空或很旧表示重连后可能没有重新订阅 |
| `monitor.last_msg_at` | 最近收到 WS 消息或 REST fallback 刷新 | 长时间不变表示本地行情过期 |
| `monitor.last_update_at` | 最近写入 `markets_state` / `market_deltas` | 长时间不变表示本地 DB 停在旧盘口 |
| `monitor.last_ws_error` | 最近 WS 错误 | 握手超时、连接关闭等都应结合 REST fallback 观察 |

`polymarket_active_markets_cache.json` 也有新鲜度约束。旧缓存文件不能在读取时刷新内存时间戳；否则会把历史 `outcomePrices` / `bestBid` / `bestAsk` 当成当前价格，造成策略监控、工作台和官网三方错配。
