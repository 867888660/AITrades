# 历史数据工作台与回测报告

本文记录 History Workspace 与 Backtest Report 的第一版数据和回测语义，重点说明历史数据覆盖、补齐、测试样例、回测任务和报告进度。

## 页面入口

- 历史数据工作台：`/history`
- 回测报告页：`/backtests/<run_id>`
- 前端文件：
  - `templates/history_workspace.html`
  - `static/history_workspace.js`
  - `templates/backtest_report.html`
  - `static/backtest_report.js`
- 后端服务：`services/history_data_service.py`
- 本地历史库：`Data/history_workspace.db`

## 核心概念

### 历史自选池

历史自选池保存可复用的历史标的，例如：

- Binance spot K 线标的：`crypto_spot:binance:BTCUSDT`
- Polymarket token 或 condition 标的

自选池只保存“标的身份”和基础元数据，不等于已经下载了所有时间段的数据。

### 回测样例

回测样例是一次测试 legs 的快照，包含：

- `legs`
- `collection_name`
- `data_window`
- `execution_config`
- 创建时的样例名称和更新时间

样例池可以按集合分组。集合用于批量运行同一策略，而不是代表某个具体策略。

### 回测运行

回测运行保存一次具体执行快照，包含：

- 样例快照
- StrategyCode
- 参数
- 测试时间窗口
- 资金曲线
- 订单明细
- 事件日志
- 核心指标

创建 run 后默认进入后台执行，不阻塞前端页面。

## Binance 覆盖与 segments 语义

`get_binance_coverage(symbol, interval)` 返回的是该标的在本地历史库中的全局覆盖，不是当前输入框 From/To 的局部覆盖。

典型返回：

```json
{
  "source": "binance",
  "symbol": "BTCUSDT",
  "interval": "1m",
  "count": 424625,
  "from": "2025-09-07T10:04:00+00:00",
  "to": "2026-06-29T07:08:00+00:00",
  "segments": [
    {
      "count": 424625,
      "from": "2025-09-07T10:04:00+00:00",
      "to": "2026-06-29T07:08:00+00:00"
    }
  ]
}
```

### segment 合并规则

本地 K 线按 `symbol + interval + open_time_ms` 去重保存。

`segments` 按时间连续性计算：

- 如果新下载窗口和旧窗口重叠或连续，会合并成更长的一个 segment。
- 如果新下载窗口和旧窗口中间有明显缺口，会新增一个独立 segment。

例如已有：

```text
2025-09-07 -> 2026-06-29
```

再下载：

```text
2025-01-29 -> 2025-11-05
```

因为两个窗口在 2025-09 到 2025-11 有重叠，最终应合并为：

```text
2025-01-29 -> 2026-06-29
```

只有完全断开的窗口才会显示为多个 segments。

## 历史数据补齐

### 手动补齐

历史工作台的“补齐选中数据”使用当前输入框：

- `历史数据 From`
- `历史数据 To`
- `粒度`

对 Binance 来说，后端使用分页下载完整窗口，而不是只下载 Binance API 单页的 1000 根 K 线。

接口：

```http
POST /api/history/binance/download
```

后端实际调用：

```python
download_binance_klines_range(payload)
```

该函数会从 `start` 游标开始分页请求，直到到达 `end`、没有新数据或达到页数上限。

### 补齐后的刷新

补齐完成后，前端必须同步刷新：

- 顶部 health
- 历史自选池
- 回测样例池
- 当前标的 coverage
- 搜索结果行里的 `history_coverage`
- 数据预览

`static/history_workspace.js` 使用 `applySelectedCoverage()` 把后端返回的新 coverage 写回当前选中标的和搜索结果列表，避免页面继续显示旧时间轴。

## Backtest Report 缺数据处理

回测报告页允许用户直接编辑测试时间窗口并重新运行当前 run。

如果严格窗口内缺少本地 K 线：

1. 后端不会静默使用错误窗口。
2. 后端会把 run 标记为 `failed`。
3. `metrics.download_required = true`。
4. 报告页显示“下载缺失数据并重跑”按钮。
5. 用户点击按钮后，前端重新提交 rerun，并带上：

```json
{
  "auto_download": true
}
```

此时后端才会进入下载阶段，并把下载进度写入同一个回测进度条和事件日志。

## 回测进度

回测任务默认后台执行。报告页轮询当前 run，并显示：

- `progress_percent`
- `progress_stage`
- `progress_message`
- `progress_updated_at`

主要阶段：

| 阶段 | 含义 |
|---|---|
| `queued` | 任务已创建，等待后台执行 |
| `starting` | 后台 worker 已启动 |
| `initializing` | 清理旧报告数据，初始化 run |
| `validated` | 策略和样例兼容性检查通过 |
| `loading_data` | 正在读取本地历史数据 |
| `checking_data` | 正在检查覆盖范围 |
| `downloading_data` | 正在下载缺失 K 线 |
| `data_ready` | 数据已准备好 |
| `strategy_loaded` | StrategyCode 已加载 |
| `running_strategy` | 正在逐 bar 运行策略 |
| `calculating_metrics` | 正在计算指标 |
| `writing_report` | 正在写入资金曲线、订单、事件 |
| `completed` | 回测完成 |
| `failed` | 回测失败 |

## 当前限制

- 第一版真实执行器主要支持单 Binance spot leg。
- Polymarket 历史盘口已纳入历史工作台设计，但真实撮合/逐 tick 回测仍需后续扩展。
- `get_binance_coverage()` 返回全局覆盖；如果未来要展示“当前窗口覆盖率”，应新增独立字段，避免混淆全局 coverage 和请求窗口 coverage。
- 历史工作台和回测报告的中文文案必须保存为 UTF-8，避免出现 mojibake。

## 维护检查

修改相关文件后至少运行：

```powershell
python -m py_compile app.py services\history_data_service.py
node --check static\history_workspace.js
node --check static\backtest_report.js
```

