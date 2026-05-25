# Strategy Workspace Performance Notes

> Updated: 2026-05-08

本文记录策略工作台在高频刷新、外部行情 overlay、多线条图表场景下的性能约束和优化原则。

## 背景

工作台页面会同时处理几类高频数据：

- Polymarket 主市场价格：默认 2s 增量刷新。
- 策略统计：默认 5s 增量刷新。
- 观察市场：默认 10s 增量刷新。
- 外部行情 overlay：默认 20s 增量刷新。
- 策略事件流：默认 15s 刷新。

当一次性勾选多个 crypto / finance overlay、打开较多图例线条、并保持 Debug 面板持续输出时，浏览器主线程容易出现卡顿。严重时会表现为页面长时间无响应，甚至让整台电脑看起来像卡死。

## 主要风险点

### 1. Debug 日志膨胀

早期实现会把每次请求的完整 URL、请求参数和返回结构写入 Debug 面板。

在工作台图表请求里，URL 可能包含：

- `series_style_json`
- `market_targets_json`
- overlay symbols / fields
- 多个 stream cursor

这些字段会让 URL 变得非常长。如果每 2s 都把完整 URL 写入 DOM，并保留数百条日志，Debug 面板本身会成为性能热点。

### 2. 增量刷新重复处理整张表

常规增量数据通常是按时间追加的，但旧逻辑会在每次增量到达时：

- clone 全量 chart payload
- merge 全部 rows
- sort 全部 rows
- forward-fill 全部字段
- 再交给 ECharts patch

这对 1000+ rows、十几条 series 的场景不算灾难，但在 2s 刷新、Debug 膨胀、overlay 增多时会叠加成明显压力。

### 3. 不必要的 MACD 重算

MACD 只和启用 MACD 的 base series 相关。若本次增量 stream 没有更新任何启用 MACD 的线条，就不应重算全部 MACD overlay 列。

## 已落地优化

### Debug 输出摘要化

文件：`static/strategy_workspace_v2.js`

关键函数：

- `compactDebugUrl`
- `compactFetchDebug`
- `pushDebug`

规则：

- Debug 面板不再保存完整长 URL，只保留 path、query 前缀和 query 字符数。
- 单条 Debug 文本有最大长度保护。
- Debug 总行数从 300 降为 160。
- 浏览器 console 也不再在成功请求里打印完整返回数据，只打印状态和顶层 keys。

这不会影响数据采集、接口请求或图表渲染，只减少前端日志 DOM 和 console 压力。

### 增量请求不携带样式 JSON

文件：`static/strategy_workspace_v2.js`

关键函数：

- `buildChartRequestParams(options = {})`
- `loadChart`
- `loadChartDelta`

规则：

- 全量 `/chart` 请求继续携带 `series_style_json`，因为后端需要用它生成完整 series metadata 和指标列。
- 增量 `/chart-delta` 请求不携带 `series_style_json`，因为 delta 服务只需要 symbols、fields、market targets、range 和 cursor。

这样不会降低刷新频率，也不会减少数据处理量，只减少高频请求的 URL 体积。

### 增量 rows 追加快路径

文件：`static/strategy_workspace_v2.js`

关键函数：

- `mergeDeltaPoints`
- `loadChartDelta`

规则：

- 如果新 points 的时间戳位于当前 rows 尾部或等于最后一个 bucket，则只做尾部 merge / append。
- 只有遇到乱序点或历史修正点时，才回退到完整 merge + sort + forward-fill。
- `loadChartDelta` 不再无条件深拷贝整个 chart payload，而是复用静态结构，仅在 rows 发生变化时生成新 rows。

这保持了数据语义不变，但减少了常规刷新路径的 CPU 和内存分配。

### MACD 按需重算

文件：`static/strategy_workspace_v2.js`

关键函数：

- `streamsRequireMacdRecompute`
- `recomputeMacdOverlayColumns`

规则：

- 只有本次 delta 影响到启用 MACD 的 base series 时，才重算 MACD overlay 列。
- 未启用 MACD、或本次 stream 与 MACD 线无关时跳过。

## 不应牺牲的部分

为保证数据处理效率和策略观察体验，以下部分不建议为了降卡顿而随意降低：

- 主市场价格 2s 增量刷新。
- 策略统计 5s 增量刷新。
- overlay 数据本身的采集和入库频率。
- 后端 chart / chart-delta 的 cursor 机制。
- ECharts 图例的点击隐藏/显示能力。

优先优化前端无效开销，而不是降低数据质量或刷新频率。

## 后续排查建议

若仍出现明显卡顿，建议按以下顺序排查：

1. 看 Debug 面板里的 `query_chars`、`rows`、`series`、`total_ms`。
2. 如果 `rows` 长期超过 1400，检查前端采样和后端 range 是否失效。
3. 如果 `series` 过多，优先确认是否有大量隐藏 overlay 仍参与渲染。
4. 如果 `render_ms` 升高，重点看 ECharts option 的 series 数和 legend 数量。
5. 如果 `fetch_ms` 升高，重点查 SQLite 查询和外部行情表索引。
6. 如果浏览器内存持续上涨，优先检查 Debug 日志、chart payload 缓存和事件流列表。

## 验证

本次前端变更至少需要通过：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check static\strategy_workspace_v2.js
```

如修改后端 chart 服务，还应额外执行：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m py_compile services\strategy_chart_service.py services\strategy_chart_delta_service.py
```
