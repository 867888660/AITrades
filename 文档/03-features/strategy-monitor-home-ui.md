# 策略监控首页 UI 补充说明

更新日期：2026-05-07

本文补充策略监控首页、`Legs Snapshot` 展开区、`Recent Action / Print` 和 Polymarket 外链的约定。后续修改前端 UI 时，以本文的粒度定义为准。

---

## 1. Leg 粒度定义

当前系统中，一条 `strategy_legs` 记录代表一个 Polymarket 二元市场，也就是一个 `condition_id`。

```text
一条 leg = 一个 market / condition_id
YES 和 NO = 同一条 leg 内的两个 side / outcome
```

不要把 YES 和 NO 拆成两条 leg。持仓表才按 side 拆分，例如：

```text
strategy_virtual_positions(strategy_id, leg_index, side)
```

正确层级：

```text
Strategy
  └─ Leg 0 = 一个 Polymarket market / condition_id
       ├─ YES side
       └─ NO side
```

错误理解：

```text
Strategy
  ├─ Leg 0 = YES
  └─ Leg 1 = NO
```

---

## 2. 策略监控首页一级表

一级表只展示策略级信息：

```text
Name | Strategy_Code | Mode | Legs | Bankroll | Exposure | PnL | ROI | Last Action | Updated | Action
```

一级表不展示：

```text
YES_ASK / YES_BID / NO_ASK / NO_BID
YES_QTY / NO_QTY
YES_AVG / NO_AVG
YES_CURRENTPCT / NO_CURRENTPCT
Risk
```

`Direction` 与 `Weight` 已从 `strategy_legs` 删除。当前首页不应展示这两项，也不能把它们当作执行字段使用。

这些都属于 leg 级或工作台级信息。

`Mode` 是策略监控首页的运行状态切换入口；单策略工作台 header 也提供同一逻辑的局部入口：

```text
Stop | Virtual | Real
```

一致性要求：

- 两个入口都读写 `strategy_registry.state`。
- 两个入口都调用 `PATCH /api/registry/strategies/<id>/state`。
- 两个入口都使用相同三态颜色：`Stop` 灰色 / slate，`Virtual` 蓝色，`Real` 绿色。
- 两个入口都只改变策略状态，不自动迁移仓位、订单或 PnL。
- 涉及 `Virtual <-> Real`、`Stop -> Real`、`Real -> Stop` 的高风险切换必须弹确认提示。

`Action` 列保留：

```text
工作台 | 参数 | 删除
```

删除必须二次确认。

---

## 3. Legs Snapshot 展开区

展开区用于快速解释策略内部结构。一行代表一条 market leg，不是一条 YES/NO side。

推荐字段：

```text
Leg | Question | Params | Side | YES Qty | YES Avg | YES Mark | NO Qty | NO Avg | NO Mark | Exposure | PnL | Updated
```

字段含义：

| 字段 | 含义 |
|---|---|
| Leg | `leg_index + 1` |
| Question | 当前 leg 对应的 Polymarket 市场问题 |
| Params | `strategy_legs.params_json` 的摘要 |
| Side | 当前实际持仓方向摘要 |
| YES Qty / Avg / Mark | YES side 的数量、均价、估值价格 |
| NO Qty / Avg / Mark | NO side 的数量、均价、估值价格 |
| Exposure | YES 成本 + NO 成本 |
| PnL | YES PnL + NO PnL |
| Updated | leg 持仓或行情最近更新时间 |

`Side` 的取值：

```text
YES  = 只有 YES 持仓
NO   = 只有 NO 持仓
Both = YES 和 NO 都有持仓
None = 当前无持仓
```

视觉约定：

- `YES` badge 使用浅绿色
- `NO` badge 使用浅红色
- `Both` 使用中性色或紫/蓝
- `None` 使用灰色
- YES 三列可以轻微偏绿
- NO 三列可以轻微偏红

---

## 4. Recent Action / Print

展开区的 `Recent Action / Print` 展示最近策略输出，不是完整事件日志。

当前约定：

```text
Time | Type | Content
```

后端返回最近 30 条，前端使用固定高度滚动表格展示。

`Type` 取值：

```text
action | print | error
```

视觉约定：

- `error` 明显强调
- `print` 弱化
- `action` 正常强调

重复 Print 展示约定：

- 相同 `type + mode + content` 的连续重复内容应合并展示。
- 合并计数显示在内容右侧，避免挤压左侧时间和正文。
- 时间显示该组内容的最新更新时间。
- 合并只发生在前端展示层，不代表后端删除历史事件。

---

## 5. Polymarket 外链规则

所有前端出现的期权标题、市场标题、Question 都应尽量可点击打开 Polymarket。

覆盖位置：

```text
策略监控首页 Legs Snapshot -> Question
Polymarket 市场查询 -> Question / 打开
我的仓位 -> Title
自选市场 -> Question
工作台市场选择器 / 当前主市场
```

链接构造优先级：

```text
1. 如果有真实 url，直接使用
2. 如果有 event_slug + market slug，使用 /event/{event_slug}/{market_slug}
3. 如果检测到是 grouped market 但缺少 event_slug，不拼错误 /event/{market_slug}，改用搜索兜底
4. 普通非分组 market 才使用 /event/{slug}
5. 最后使用 question / condition_id / token 搜索兜底
```

为什么不能直接用 `/event/{slug}`：

Polymarket 很多日期、档位、候选项类市场是 grouped market。此时 `market.slug` 是父 event 下面的子市场 slug，不是顶层 event slug。

示例：

```text
错误：
https://polymarket.com/event/microstrategy-sells-any-bitcoin-by-december-31-2026

正确：
https://polymarket.com/event/{父 event slug}/microstrategy-sells-any-bitcoin-by-december-31-2026
```

如果本地缓存暂时没有父 event slug，则应跳转到：

```text
https://polymarket.com/search?q={question}
```

这样至少不会打开 Polymarket 的 404 页面。

---

## 6. 当前相关实现文件

前端：

```text
static/app.js
static/polymarket_links.js
static/watchlist.js
static/strategy_workspace_v2.js
static/workspace_v3_patch.js
static/workspace_v3.css
static/styles.css
```

后端：

```text
services/polymarket_service.py
app.py
services/strategy_data_source.py
```

核心数据表：

```text
strategy_registry
strategy_legs
strategy_virtual_positions
strategy_virtual_events
```
