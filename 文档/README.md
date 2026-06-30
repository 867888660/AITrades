# Polymarket DataTube 文档入口（补充）

## 2026-06-29 历史数据工作台与回测报告

- 新增专题文档：[历史数据工作台与回测报告](03-features/history-workspace-backtest.md)。
- 记录 Binance K 线按用户选择时间窗口分页补齐、`coverage.segments` 合并语义、补齐后前端回写搜索结果覆盖区间、Backtest Report 缺数据时由用户确认下载并纳入进度条。
- 维护相关功能时，优先阅读该文档，再查看 `services/history_data_service.py`、`static/history_workspace.js` 和 `static/backtest_report.js`。

---

# Polymarket DataTube 文档入口

这个目录按阅读目的组织文档：先看全局，再看使用方式，需要改功能时看功能设计，需要查字段或历史决策时看参考与决策记录。

## 最近同步

- 2026-05-23：
  - **Polymarket Dictionary 刷新优化**：首页字典刷新默认只抓 active 市场，避免每次手动更新都翻扫 closed 历史市场；刷新开始会清理过期记录，插入前也会再次按 `endDate` 过滤过期市场，并在状态面板展示 `过期跳过`。详见 [system-overview.md](01-overview/system-overview.md#25-首页期权字典模块补充)。
  - **Strategy Ledger 与强制平仓入口**：新增 `/ledger` 交易账本页和 `/api/ledger` 聚合接口，统一展示 Virtual / Real / Unassigned 的挂单、仓位、leg 归属与 Debug 检查；仓位表已改为多资产口径（Asset / Venue / Symbol / Instrument / Side）。Dashboard 策略行新增 `平仓` 按钮，Virtual 策略可执行第一版 force-flat，Real 策略在真实子账本归因完成前阻断。详见 [strategy-ledger-and-force-flat.md](03-features/strategy-ledger-and-force-flat.md)。
- 2026-05-21：
  - **市场查询搜索优化**：`search_markets()` 改为多关键词分词 AND 匹配（空格分隔，噪声词自动过滤）；类别字段支持 `/` `,` `;` 分隔的多类别 OR 匹配；新增 `_search_dictionary_db()` 直接对 Dictionary DB 做 SQL LIKE 查询，覆盖 22000+ 条记录；本地结果不足时 fallback 到 Gamma API slug 精确查找。修复前搜索 "US x Iran" 返回 0 条，修复后返回 30+ 条相关市场。
- 2026-05-21（原有）：
  - **策略状态分层、默认值与 UI**：策略代码可声明 `ParamsSchema` / `ControlsSchema` / `RuntimeStateSchema`，系统按“默认值 + override”合并注入 UseData；Dashboard 策略表新增 `State` 弹窗，可编辑 Controls override、查看 effective value，并在 Stop 状态维护 RuntimeState。详见 [strategy-state-store.md](03-features/strategy-state-store.md)。
- 2026-05-19：
  - **多资产策略架构骨架**：策略系统新增 `strategy_state` 持久状态表、`strategy_legs` 通用标的字段、UseData v2 结构化上下文、`FunctionJson.state_updates` 写回，以及 `ORDER` / `SET_TARGET` / `SET_BINARY_TARGET` 动作兼容。详见 [multi-asset-strategy-architecture.md](03-features/multi-asset-strategy-architecture.md)。
- 2026-05-16：
  - **工作台图表插针修复**：`strategy_chart_service.py` 新增二元报价清洗，`price_snapshot`、`print` bid fallback、详情快照与本地实时库中的 `<= 0` 或 `> 1` bid/ask/last 不再视为真实价格，而是按缺价处理；避免 Virtual tick 暂时缺盘口时把 `0.0` 画成价格插针，并防止 Virtual PnL 被同一异常行拉低。
- 2026-05-15：
  - **Virtual PnL 口径修正**：工作台与 Dashboard 的 Virtual `strategy_pnl` 改为账户总盈亏 `equity - initial_cash`，包含已实现价差、已付手续费和当前持仓按可卖出价估值的浮盈亏；避免反复买卖后平仓显示 PnL=0 的误导。`strategy_virtual_account.equity/unrealized_pnl` 在虚拟执行后刷新。
- 2026-05-13：
  - **事件流 Actions / Trades 语义收敛**：工作台 Actions 改为读取 `strategy_action_events` 审计表，展示策略原始动作及执行状态；Trades 改为只展示 `strategy_virtual_orders.status='filled'` 的实际成交。`blocked` / `failed` 订单尝试回到 Actions 里显示，不再混入 Trades。
  - **工作台图表 print 展示语义**：`print` 事件继续由 chart 接口返回并显示在 Events 面板；主图 `markLine` 不再绘制 `print` 竖线，只保留 action / trade / error / settings 等关键事件标记。前端图表结构签名纳入 `event_timeline`，并更新 `strategy_workspace_v2.js` 静态资源版本号，避免浏览器继续使用旧逻辑。
  - **Crypto 行情鲁棒性**：`crypto_service.py` 在 Binance 报价返回 `451` 或网络失败时，会按 symbol 推导 base/quote 并使用 CoinGecko `/coins/markets` 拉取价格、市值、FDV 与成交量字段；只有 Binance 与 CoinGecko 均不可用时才进入 stale 历史兜底。
  - **行情时间语义**：`last_run_at` 表示最近刷新尝试时间，`ts_utc` 表示当前数据本身时间。前端 Live Feed 与系统状态卡必须同时展示 Refresh / Data；`stale=true` 或 `status=degraded` 时不能标记为“稳定运行”。
- 2026-05-12：
  - **策略价格源一致性**：策略监控、策略工作台、Virtual `UseData` 的当前盘口优先使用 CLOB `/book`；本地 `markets_state` / `market_deltas` 作为 WS/REST fallback 后的实时副本；Gamma / 字典快照只做元数据和最后兜底。
  - **本地 WS 过期诊断**：`/api/health` 新增 `ws_market_sync.monitor` 运行态说明，排障时看 `last_subscribe_at`、`last_msg_at`、`last_update_at`、`last_ws_error`、`token_count`，不能只看 `thread_alive`。
  - **主动市场缓存防过期误用**：`polymarket_active_markets_cache.json` 增加新鲜度约束，旧缓存文件不能在读取时被伪装成当前行情。
- 2026-05-10：
  - **事件流 Trades 标签**：`strategy_event_service.py` 新增从 `strategy_virtual_orders` 读取订单记录并作为 `event_type=trade` 事件返回；后续语义已收敛为只展示 filled 成交，blocked / failed 归入 Actions 审计。
  - **事件流渲染稳定性**：前端引入 `_fullEventsList` 全局缓存，`appendWorkspaceEvent`（SSE 推送）合并进完整列表而非替换，消除 Trades 标签闪现消失问题；签名去重按当前过滤器计算，避免 print 事件 ts 变化触发无效重渲染。
  - **PNL 价格来源修复**：`_load_latest_condition_snapshot` 补加新鲜度过滤（`_WS_FRESHNESS_SECONDS=180`）；`workspace_fast_path` 改为传入 `_match_strategy_market` 结果，工作台与 Dashboard PNL 使用同一市场快照。
  - **metrics 字段规范**：`strategy_metric_store.py` 移除 `_CATALOG_HIDE_BY_DEFAULT` 黑名单；策略代码 `metrics` 字典中出现的所有 key 直接展示，无后端过滤。
  - **Virtual 模式持仓**：`strategy_profit_engine` 和 `_build_strategy_leg_snapshot` 均从 `strategy_virtual_positions` 读取持仓，Dashboard Legs Snapshot / Exposure / PNL 数据源统一。
- 2026-05-07：补充策略工作台与策略监控首页的 `Stop` / `Virtual` / `Real` 状态切换一致性、图表稀疏行情兜底、Print 合并展示、以及图表副指标清理说明。

## 推荐阅读顺序

1. [系统总览](01-overview/system-overview.md)
   - 适合先理解项目整体结构、核心入口、数据存储、接口和运行链路。

2. [工作台使用指南](02-usage/workspace-guide.md)
   - 适合了解策略工作台页面、状态切换、图表交互、Print 展示、预设、回测预留和关键后端文件。

3. [策略系统设计](03-features/strategy-system.md)
   - 适合修改策略注册、策略腿、状态机、虚拟盘和运行系统前阅读。

4. [工作台技术总结](01-overview/workspace-technical-summary.md)
   - 适合查工作台相关 API、服务、数据库和图表架构的更细说明。

## 目录说明

```text
文档/
  README.md
  01-overview/    # 项目整体说明、工作台技术总览
  02-usage/       # 启动、使用、操作类指南
  03-features/    # 具体功能设计说明
  04-reference/   # 外部 payload、字段、样本等参考材料
  05-decisions/   # 架构评审、阶段性决策记录
```

## 文档清单

### 01-overview

- [system-overview.md](01-overview/system-overview.md)
  - 原 `总结文档.md`。项目完整说明，内容最全，适合作为系统知识库。

- [workspace-technical-summary.md](01-overview/workspace-technical-summary.md)
  - 原 `工作台总结文档.md`。工作台相关技术总结，包含 API、服务、数据库和图表架构。

### 02-usage

- [workspace-guide.md](02-usage/workspace-guide.md)
  - 原 `WORKSPACE_GUIDE.md`。工作台功能和交互说明。

### 03-features

- [strategy-code-spec.md](03-features/strategy-code-spec.md)
  - 策略代码规范，定义 `Inputs`、`UseData` 标准命名、`FunctionJson` 多动作协议、`LegCount` 和第一阶段落地范围。
  - 固定 LegsSchema 设计，定义策略代码如何声明固定数量、固定类型的 `polymarket_binary` / `crypto_spot` / `equity` legs。
- [strategy-system.md](03-features/strategy-system.md)
  - 原 `Stragy文档.md`。策略交易管理系统、数据库、API、状态机和虚拟盘说明。

- [multi-asset-strategy-architecture.md](03-features/multi-asset-strategy-architecture.md)
  - 多资产策略架构升级说明，定义 Params / UseData / State 边界、通用标的字段、UseData v2、通用动作协议与 v2 虚拟账本。
- [strategy-ledger-and-force-flat.md](03-features/strategy-ledger-and-force-flat.md)
  - 交易账本页、Debug 诊断、Virtual/Real/Unassigned 多资产仓位展示，以及 Dashboard `平仓` 按钮和当前 force-flat 边界。
- [strategy-state-store.md](03-features/strategy-state-store.md)
  - 策略状态分层说明，定义 Params / UserState / RuntimeState / SystemState 边界、状态 API、Dashboard State 弹窗和审计规则。

- [filtering-strategy.md](03-features/filtering-strategy.md)
  - 原 `Filtering_Strategy.md`。过滤策略说明。

- [strategy-monitor-home-ui.md](03-features/strategy-monitor-home-ui.md)
  - 原 `策略监控首页UI补充.md`。策略监控首页 UI 字段和交互补充说明。

### 04-reference

- [gamma-payload-reference.md](04-reference/gamma-payload-reference.md)
  - 原 `iran_market_full_dump.md`。Gamma payload 字段参考和样本说明。

### 05-decisions

- [architecture-review.md](05-decisions/architecture-review.md)
  - 原 `ARCHITECTURE_REVIEW.md`。架构评审和后续方向。

- [workspace-performance-notes.md](05-decisions/workspace-performance-notes.md)
  - 2026-05-08 新增。记录策略工作台高频刷新、外部行情 overlay、多线条图表下的卡顿风险、已落地优化和后续排查顺序。
- [architecture-review.pdf](05-decisions/architecture-review.pdf)
  - 原 `ARCHITECTURE_REVIEW.pdf`。历史 PDF 版本。

## 命名约定

- 文件名统一使用英文小写 kebab-case，例如 `strategy-system.md`。
- 正文标题和内容可以继续使用中文。
- 一个文档尽量只回答一个主要问题：是什么、怎么用、怎么改、查什么、为什么这样设计。
- 新增文档优先放入已有分类；如果只是临时记录，先写清楚日期和用途，再考虑是否归档。
## 2026-05-13 图表价格渲染一致性同步

- 策略工作台图表首屏 `/chart` 与运行态 `/chart-delta` 已统一价格行级 carry-forward 口径，避免首屏 `Yes Ask` 稀疏、等待增量后才连续的错觉。
- Virtual tick 会持久化策略当次实际收到的 `price_snapshot`，包括 `yes_bid` / `yes_ask` / `no_bid` / `no_ask`。
- Chart 不再从 `No_bid` 反推 `Yes Ask`；缺 ask 时宁可显示缺口，也不生成合成假价污染 PnL。
- `0.0`、负数、`> 1` 的二元市场 bid/ask/last 都按缺价处理，不能进入 chart rows；如果策略 print 中出现 `Yes_bid=0.0` 或 `No_bid=0.0`，它表示本轮未拿到有效盘口，不是官网真实成交价格。
- PnL 图表列只使用同一行可见真实 ask 计算，避免 ask/bid 不变但 PnL 锯齿。
- `stats` 增量会随同返回本次 PnL 对齐使用的主市场 `market_0_*` 价格字段，前端在 patch PnL / Qty / Avg 时同步 patch 主市场价格线，避免 tooltip 显示旧 ask 但 PnL 已更新的短暂错位。
- 排查红线断点时优先看 Debug 中的 `market_0_yes_ask=finite/rows gaps=...` 与 `[WS] chart:normalize-rows`。
## 2026-05-15 参数 UI 与 UseData 自动填入

- [strategy-parameter-ui.md](03-features/strategy-parameter-ui.md)
  - Dashboard / Workspace 策略参数说明、UseData 自动填入、draft UseData 与 `start_day` 初始化规则。
