# Gamma Payload Reference

这份文档原先保存了完整的 Iran 事件原始响应，体积过大且不利于维护。现在只保留字段结构参考，方便在调试 `Gamma API -> 市场标准化 -> 策略监控` 链路时快速对照。

抓取样本: `us-forces-enter-iran-by`

## Event 层关键字段

```json
{
  "id": "158299",
  "slug": "us-forces-enter-iran-by",
  "title": "US forces enter Iran by..?",
  "active": true,
  "closed": false,
  "liquidity": 279532.02707,
  "volume": 3259736.477357,
  "markets": ["..."]
}
```

## Market 层关键字段

```json
{
  "question": "US forces enter Iran by March 31?",
  "conditionId": "0x306d10d4a4d51b41910dbc779ca00908bd917c131541c5c42bbbc736258d2d56",
  "slug": "us-forces-enter-iran-by-march-31-...",
  "outcomes": "[\"Yes\", \"No\"]",
  "outcomePrices": "[\"0.24\", \"0.76\"]",
  "clobTokenIds": "[\"yes_token\", \"no_token\"]",
  "bestBid": 0.23,
  "bestAsk": 0.25,
  "lastTradePrice": 0.25,
  "spread": 0.02,
  "volume": "1049395.063884",
  "liquidity": "64421.4085",
  "active": true,
  "closed": false
}
```

## 这份样本主要用来验证什么

- `conditionId` 能否正确映射到策略监控表。
- `outcomes` / `clobTokenIds` 能否正确拆出 `yes_token` 和 `no_token`。
- 实时市场库中的 `markets_state` 是否能回填 `bestBid` / `bestAsk` / `lastTradePrice`。
- 策略详情页是否能用 `conditionId` 或 token 双向匹配到实时快照。

## 维护原则

- 文档只保留字段骨架，不再保存超长原始 JSON。
- 需要完整 payload 时，建议重新通过 Gamma API 按 `slug` 导出并临时分析。
- 架构分层和数据库职责请查看 [architecture-review.md](../05-decisions/architecture-review.md)。
