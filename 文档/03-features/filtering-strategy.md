# Filtering Strategy

`main.py` 的 WebSocket 处理链路不会把每一条盘口变化都写入数据库，而是先做一次基于订单簿厚度的过滤，只保留会改变市场有效流动性的更新。

## 目标

- 降低 `market_deltas` / `markets_state` 的无效写入。
- 避免远离市价的小单、撤单和机器人抖动污染策略图表与事件流。
- 让策略监控只消费“真正影响盘口承接力”的变化。

## 三步处理

1. 内存订单簿重建
   WebSocket 推送通常是增量碎片，系统先把消息合并进 `self.local_orderbooks`，恢复单个 token 的完整买卖盘。

2. 计算 1c 深度指标
   使用 `compute_1c_depth()` 从重建后的订单簿提取 `best_bid`、`best_ask`、`spread_c`、`depth_bid_usd`、`depth_ask_usd` 等指标。

3. 差分判定
   若最新深度指标相对上次状态没有实质变化，则跳过写库；只有结构性变化才会写入实时市场 SQLite 和事件流。

## 结果

- `markets_state` 保存当前快照。
- `market_deltas` 保存有效增量事件。
- 策略图表、策略事件、策略详情都基于过滤后的数据，而不是原始噪声流。

## 适用范围

- `HighProb`
- `LowProb`
- `StrategyMonitoring`
- `MyHolding`

这份文档只保留算法职责说明。数据库分层与架构 review 请见 [architecture-review.md](../05-decisions/architecture-review.md)。
