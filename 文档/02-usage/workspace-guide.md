# Strategy Workspace Guide

`State Lanes`、`Strategy Metrics`、策略状态机与 `stop_loss_locked` 的准确定义见 [state-lanes-and-metrics.md](../03-features/state-lanes-and-metrics.md)。

> 2026-05-05 补充：策略监控首页、Legs Snapshot 粒度、Recent Action 滚动展示、以及 Polymarket grouped market 外链规则，详见 [strategy-monitor-home-ui.md](../03-features/strategy-monitor-home-ui.md)。
> 2026-05-07 补充：工作台状态切换、图表稀疏数据兜底、Print 去重展示、图表副指标范围与策略监控页保持一致。

## 目标

策略工作台现在既可以围绕监控策略查看图表，也可以临时切换到任意 Polymarket 期权进行历史观察。

本次升级重点包括：

- 弱网下优先使用本地历史和市场快照，不再因为 Gamma 接口波动就整页失效。
- 支持按 `conditionId`、`tokenId`、关键词搜索并点击选择任意期权。
- 时间范围支持快捷范围、自定义起止时间和拖动缩放。
- 图表支持系列样式编辑，以及 `MA` / `MACD` 技术指标参数配置。
- 工作台左上角支持和策略监控首页一致的 `Stop` / `Virtual` / `Real` mode 切换，并可查看/手动切换策略状态机 state。
- 当 24h/6h 范围内只有很少市场历史点时，图表会使用前序快照补一个区间起点，避免“接口成功但图上看不到线”。
- 支持命名工作台预设的保存、加载和删除。
- 为回测系统预留了 UI 和 API 契约。
- 图表区域支持拖拽手柄调整高度（420px ~ 1400px），高度持久化到 localStorage。
- 图表数据支持 Delta Stream 增量刷新，按分流间隔局部更新，避免全量重载。
- 副图指标选择器新增 Strategy Metrics（数值型）和 State Lanes（状态型），由后端 `metric_catalog` 动态声明。
- 工作台通过 SSE 实时接收策略摘要和事件追加推送。
- 多 Leg 策略图表使用预定义颜色对（8 组 yes/no 配色），自动按 leg_index 分配。
- 图表工具栏新增间隔选择器（5s / 30s / 1m / 5m），控制采样粒度。
- 图表上方新增 Legs Bar 策略汇总区，展示 Bankroll、PnL、Mode、State 和主市场盘口/持仓快照。

## 页面入口

- 页面路由：`/strategies/<row_id>/workspace`
- 页面模板：`templates/strategy_workspace.html`
- 页面脚本：`static/strategy_workspace_v2.js`
- V3 样式：`static/workspace_v3.css`
- V3 补丁层：`static/workspace_v3_patch.js`
- 图表接口：`/api/polymarket/strategies/<row_id>/chart`
- 增量图表接口：`/api/polymarket/strategies/<row_id>/chart-delta`
- 工作台 SSE：`/api/live/strategies/<row_id>/workspace`

当前没有单独的“另一个工作台页面”。实际工作台仍是 `strategy_workspace.html`，只是加载顺序为：

```text
strategy_workspace.html
  -> workspace_v3.css
  -> strategy_workspace_v2.js
  -> workspace_v3_patch.js
```

其中 `strategy_workspace_v2.js` 负责核心状态、请求、图表和保存逻辑；`workspace_v3_patch.js` 负责 V3 布局和若干展示层覆盖；`workspace_v3.css` 负责当前工作台视觉样式。

## Mode 与 State 切换

工作台标题区的 `Mode` 控件与策略监控首页 `Mode` 列使用同一套逻辑：

```text
Stop | Virtual | Real
```

- 数据源是 `strategy_registry.mode`，不是旧的 `IsVirtual`。
- 保存接口是 `PATCH /api/registry/strategies/<row_id>/mode`。
- 请求体示例：`{ "mode": "Virtual" }`。
- 后端只校验目标值必须属于 `Stop` / `Virtual` / `Real`，不会自动迁移仓位、订单或 PnL。
- 前端负责高风险切换确认，工作台与策略监控首页文案和行为应保持一致。

切换语义：

- `Stop`：策略不参与虚拟盘调度，也不代表实盘自动动作。
- `Virtual`：由 `VirtualRunner` 调度虚拟盘逻辑，写入虚拟账户、虚拟订单和虚拟事件。
- `Real`：代表实盘模式入口；从 `Virtual` 切到 `Real` 不会把虚拟仓位迁移到真实账户。

工作台标题区和参数面板还提供独立的 `State` 控件：

- 数据源是 `strategy_state.namespace = machine` 的 `state` 键。
- 保存接口是 `PATCH /api/registry/strategies/<row_id>/state-store/machine`。
- 请求体示例：`{ "values": { "state": "manual_review" }, "replace": false }`。
- 选项来自策略代码 `StateMachineSchema`；未声明时使用 `auto / idle / holding / cooldown / manual_review / stop_loss_locked` 默认集合。
- `State` 不决定虚拟盘/实盘调度，调度仍只看 `mode`。

颜色约定：

- `Stop`：灰色 / slate，表示停用或静默。
- `Virtual`：蓝色。
- `Real`：绿色。

## 数据来源

### 1. 策略上下文

策略详情、策略设置、事件流仍然来自当前 `row_id` 对应的监控策略。

### 2. 市场历史

图表历史优先来自本地 SQLite：

- 实时市场库：`market_realtime_db_path`
- 策略统计库目录：`strategy_metrics_db_dir`
- 外部 overlay：`sqlite_db_path`

### 3. 市场发现与弱网降级

市场搜索和解析走三层降级：

1. 进程内缓存
2. 本地市场快照 `polymarket_active_markets_cache.json`
3. Gamma 在线解析

如果只是看历史图表，只要已有 `conditionId` 或 token，工作台会尽量直接走本地历史，不依赖在线全量市场列表。

### 4. 稀疏历史兜底

`market_deltas` 里某些市场可能在所选范围内只有 0 到 1 个 bucket。此时不是图表接口失败，而是历史样本不足。

当前处理规则：

- 后端 `strategy_chart_service._load_price_samples()` 会查找 `from_ts` 之前最近的 market snapshot，并在区间起点补一个 carry-forward 点。
- 前端使用接口返回的 `meta.from` / `meta.to` 作为横轴范围，不再只依赖 `rows` 自身的最小最大时间。
- 当 `rows < 2` 时，前端会强制显示 symbol，避免单点数据被折线样式隐藏。
- 页面 meta 会提示“历史样本不足，仅显示当前快照”。

## 任意期权加载

图表面板顶部新增了“市场选择器”，支持 4 种方式：

- 直接粘贴 `conditionId`
- 直接粘贴 `tokenId`
- 输入关键词搜索
- 在结果列表中点击“加载图表”

点击“回到策略期权”会恢复为当前策略默认监控的期权。

## 时间范围与图表交互

### 快捷范围

支持常用快捷按钮：

- `6h`
- `24h`
- `72h`
- `7d`

### 自定义时间

如果填写 `From` 和 `To`，图表请求会优先使用绝对时间，不再使用快捷范围。

### 拖动缩放

图表底部保留了 ECharts 的 `dataZoom.slider`，可直接拖动观察任意子区间。

如果图表只显示一个点或只有极短线段，优先检查 Debug 中的 `row_count` / `history_price_points`。只要接口返回 `ok=true` 且 `row_count` 很小，问题通常是本地行情历史稀疏，而不是前端没有加载。

### 间隔选择器

图表工具栏新增了采样间隔下拉：

- `5s`
- `30s`
- `1m`（默认）
- `5m`

间隔决定后端 `strategy_chart_service` 对 `market_deltas` 做 bucket 聚合时的粒度。短间隔适合观察盘口微结构，长间隔适合看趋势。

### 图表高度拖拽

图表区域底部有一个拖拽手柄 `[data-chart-resize-handle]`，支持 pointer 拖动调整图表高度：

- 最小高度：420px
- 最大高度：1400px
- 拖动过程中实时 resize ECharts 实例
- 松手后持久化到 `localStorage(workspaceChartHeight)`
- 拖动时 body 添加 `chart-resizing` class 防止文本选中

### Delta Stream 增量刷新

工作台不再每次都全量重载图表。`strategy_workspace_v2.js` 维护了一套分流增量机制：

```text
price          每 2s 刷新盘口价格序列
stats          每 5s 刷新策略统计序列（仓位/均价/PnL）
metrics        每 5s 刷新 strategy_metric_events 序列
watch_markets  每 10s 刷新追踪市场序列
overlay        每 20s 刷新外部行情 overlay 序列
```

增量请求走 `/api/polymarket/strategies/<row_id>/chart-delta`，只返回指定 stream 的最新数据点，前端 merge 到现有 chart payload 中，避免全量重绘。

`stats` stream 虽然语义上刷新仓位、均价和 PnL，但响应点会同时携带本次 PnL 对齐使用的主市场价格字段，例如 `market_0_yes_ask` / `market_0_yes_bid` / `market_0_no_ask` / `market_0_no_bid`。前端收到 `stats` patch 时，也会同步更新主市场价格线，避免出现 tooltip 仍显示上一条 ask、但 `strategy_pnl` 已来自新统计点的短暂错位。

当增量数据触发 MACD 参数变化时，会自动重算受影响的派生序列。

### Live SSE 实时推送

工作台启动后会建立 EventSource 连接到 `/api/live/strategies/<row_id>/workspace`，接收两类事件：

- `summary`：每 3 秒推送策略摘要快照（盘口、仓位、PnL、价格来源），前端用于更新 Legs Bar 和 header 状态。
- `event_append`：当检测到最新事件变化时追加一条，前端直接 append 到事件流列表。

该 SSE 不是直接转发底层 WebSocket，而是 Flask 对现有 service 的二次封装轮询。连接断开后 EventSource 自动重连。

## Legs Bar 策略汇总

图表上方的 `#workspaceLegsBar` 区域由 `workspace_v3_patch.js` 的 `renderSummary()` 渲染，展示：

- 策略汇总卡片：Bankroll、PnL（正负着色）、State
- 主市场卡片：Yes Bid/Ask、No Bid/Ask、Yes Qty @ Avg、No Qty @ Avg

Legs Bar 随 Live SSE 的 `summary` 事件实时更新，不需要等待图表全量刷新。

## 多 Leg 图表颜色

多 Leg 策略的图表使用 8 组预定义颜色对，按 `leg_index % 8` 自动分配：

```text
Leg 0: Yes #22d3ee / No #0e7490
Leg 1: Yes #a78bfa / No #6d28d9
Leg 2: Yes #f59e0b / No #b45309
Leg 3: Yes #34d399 / No #047857
Leg 4: Yes #fb7185 / No #be123c
Leg 5: Yes #60a5fa / No #1d4ed8
Leg 6: Yes #f472b6 / No #be185d
Leg 7: Yes #c4b5fd / No #7c3aed
```

同一 leg 的 Yes 和 No 线使用同色系的亮/暗变体，便于视觉关联。

## 事件流副图

图表底部新增了 Event Timeline 副图面板，将事件按类型分为 5 条水平泳道，以散点形式展示在时间轴上：

| 泳道 | 类型 | 颜色 |
|---|---|---|
| 0 | Print | `#94a3b8`（灰蓝） |
| 1 | Actions | `#60a5fa`（蓝色） |
| 2 | Trades | `#22d3ee`（青色） |
| 3 | Errors | `#f43f5e`（红色） |
| 4 | Settings | `#a78bfa`（紫色） |

特性：

- 当 `payload.events` 有数据时自动追加 `event_timeline` 面板，无需后端额外声明。
- Y 轴标签显示类型名称，X 轴与主图共享时间范围和 dataZoom。
- hover 散点显示事件类型、时间和内容摘要。
- 与主图上的 markLine 事件标记互补：markLine 标记关键事件位置，副图展示完整事件分布密度。

## 图表样式与技术指标

### 系列样式

每条线都可以单独调整：

- 颜色
- 线宽
- 线型
- 是否平滑
- 是否显示点位

这些样式既可即时生效，也可以保存进工作台预设。

### 可选副图指标

工作台图表的副图指标分为三组：

**内置策略运行线**（固定可选）：

```text
yes_position
no_position
yes_qty
no_qty
yes_avg
no_avg
strategy_pnl
```

**Strategy Metrics**（动态，由策略代码 `metrics` 字典声明）：

策略代码 `metrics` 字典中的所有 key 都会自动出现在工作台面板和副图选择器中，无后端过滤。数值型指标前端使用 `metric:<key>` 格式标识，例如 `metric:gap_up`、`metric:day_to_end`。策略只需在 `metrics` 中添加或移除 key 即可控制面板展示内容。

**State Lanes**（动态，由策略代码 `metrics` 字典中的状态型字段声明）：

策略代码写入的状态型指标（如 `decision`、`regime`）以离散色带形式展示在副图底部。前端使用 `metric_state:<key>` 格式标识。

`initial_capital`、`profit_roll_ratio`、`realized_profit`、`strategy_bankroll` 属于策略设置或保留字段，不再作为图表可选线展示。它们仍可出现在设置表单、摘要或后续资金逻辑里，但不应该占据图表副图空间。

### 技术指标

当前工作台预置了两组指标：

- `MA`
  - 可启用/停用
  - 可编辑窗口天数
- `MACD`
  - 可启用/停用
  - 可编辑 `fast` / `slow` / `signal`

指标配置通过图表接口下发，并作为预设的一部分保存。

默认视觉规则：

- 外部行情 overlay、派生 MACD 线和过多辅助线默认隐藏，避免首屏图例过密。
- 右侧“图表”面板优先展示当前可见或主要 series。
- 隐藏的 series 仍保留样式状态，可通过图例或面板重新开启。

## 事件流与 Print 展示

工作台事件流默认展示策略事件和虚拟盘事件，包括：

```text
print | action | error | settings | trade
```

默认不把市场级 `BOOK` 事件混入工作台事件流。`BOOK` 属于盘口/市场数据事件，只有明确请求市场事件时才应显示。

Print 展示规则：

- 相同 `type + mode + content` 的连续重复 Print 会合并显示。
- 合并后的计数显示在右侧，时间显示最新更新时间。
- 页面只加载最近窗口内的事件用于避免卡顿；旧事件不应因为 UI 去重而从数据库删除。
- 虚拟盘 reset 不应清空历史 Print，除非后续提供明确的清理入口。

## 工作台预设

### 存储位置

预设使用独立 SQLite：

- `strategy_workspace_presets.db`

### 可保存内容

预设会保存：

- 当前目标市场
- 时间范围
- 主图/副图配置
- Overlay 选择
- 图表外观模式
- 系列样式
- 指标参数

### 作用范围

支持两类预设：

- `strategy`：仅当前策略可见
- `global`：全局通用

## 回测预留

工作台右侧已新增“回测预留”面板，当前只提供占位结构，不执行真实回测。

已预留接口：

- `GET /api/polymarket/strategies/<row_id>/backtest`
- `POST /api/polymarket/strategies/<row_id>/backtest`
- `GET /api/polymarket/strategies/<row_id>/backtest/results`

后续接入正式回测时，建议直接复用：

- 当前市场上下文
- 时间窗口
- 工作台预设
- 技术指标参数

## 关键后端文件

- `app.py`
- `services/polymarket_service.py`
- `services/strategy_chart_service.py`
- `services/strategy_chart_delta_service.py`
- `services/strategy_workspace_service.py`
- `services/strategy_metric_store.py`
- `services/strategy_event_service.py`
- `services/workspace_preset_service.py`
- `services/backtest_service.py`

## 后续建议

- 把工作台预设加入导入/导出能力，便于跨环境迁移。
- 回测落地时，优先让回测任务直接引用工作台预设，避免重复配置。
- 如果后续还要扩充技术指标，建议把指标定义抽成统一 registry，而不是继续堆在前端脚本里。
## 图表价格渲染与 Debug 排查

2026-05-13 后，工作台首屏全量图表和后续增量刷新使用同一套行级 carry-forward 口径：

- `/chart` 首屏返回后，前端会先执行 `normalizeChartPayloadRows()`，把已有的上一条有效价格延续到后续行，再进入缓存和渲染。
- `/chart-delta` 增量 patch 的 `mergeDeltaPoints()` 也会延续上一条有效价格，因此首屏和增量不应再出现同一时间窗内点数口径不一致。
- `stats` 增量 patch 会与本次 PnL 计算用到的主市场价格同批对齐；如果 PnL 变化但 tooltip 里的 ask 看似不变，优先同时打开 `Yes Qty` / `Yes Avg` / `No Qty` / `No Avg` 检查持仓和均价是否变化，而不是只看 `Yes Ask`。
- 价格延续只用于展示同一条已知报价在时间轴上的持续状态；它不是用 `No_bid` 推导 `Yes Ask`，也不是用 `last_price` 冒充 ask。
- 如果真实 ask 缺失，系统不会从 `No_bid` 合成 `Yes Ask`。缺失历史可以在 Debug 中看到，但不会污染 PnL。
- 如果图上出现从正常价格瞬间打到 0 的“插针”，优先检查 chart payload 里是否存在 `market_0_yes_bid=0`、`market_0_yes_ask=0`、`market_0_no_bid=0` 或 `market_0_no_ask=0`。当前后端会把这些 0 报价过滤为缺价；若仍出现，说明有新的路径绕过了 `strategy_chart_service._safe_binary_quote()`。

Debug 中重点看：

```text
market_0_yes_bid=有效点/总行数 gaps=...
market_0_yes_ask=有效点/总行数 gaps=...
market_0_no_bid=有效点/总行数 gaps=...
market_0_no_ask=有效点/总行数 gaps=...
```

如果首屏出现 `[WS] chart:normalize-rows`，说明前端发现全量 payload 里有稀疏行，并已在渲染前按同一 carry-forward 规则归一化。正常情况下，归一化后的 `yes_ask` / `no_ask` gaps 应明显下降，且不需要等待下一轮 delta 才连续。

注意：`print` 文本里的 `Yes_bid=... No_bid=...` 只用于策略解释和历史 bid 辅助，不代表完整盘口。工作台图表的 ask 线应来自真实 `price_snapshot`、`market_deltas` / `markets_state` 或 CLOB `/book` 快照，不允许用 `1 - No_bid` 生成。

注意：`print` 中的 `Yes_bid=0.0` / `No_bid=0.0` 表示策略当轮未取得有效盘口，不是官网真实走势。它应被当作缺价过滤，而不是画到 0。
