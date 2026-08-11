# Data Source 集中管理

更新日期：2026-08-10

## 定位

`Settings -> Data Source` 是 DataTube 的数据连接控制面。它集中展示 Finnhub、CoinGecko、Binance、Polymarket 与 OpenBB 上游来源，但不会替代 Catalog、Canonical Schema、质量校验和不可变 Manifest。

正式 Research 的边界保持不变：

```text
Requirement
-> Data Source routing policy
-> bounded preparation task
-> whole-request Provider fallback
-> canonical validation
-> immutable Manifest + provenance
-> frozen Research Bundle
```

Factor、Alpha 和 Research Backtest 运行期间不联网切换来源。

## 当前来源

- DataTube 原生：Binance、Polymarket、Finnhub、CoinGecko；
- OpenBB：YFinance、Polygon、Tiingo、FMP、Intrinio、FRED；
- 正式股票日线适配器：YFinance、Polygon、Tiingo、FMP、Intrinio；
- Finnhub 当前只提供 Quote/Profile discovery，不会显示为正式历史数据；
- FRED 当前仍没有正式 PIT Macro Manifest。

## 排序语义

排序不是全局列表，而是按能力范围保存：

```text
EQUITY:1D:BARS
EQUITY:SNAPSHOT:QUOTE
CRYPTO_SPOT:*:BARS
CRYPTO:SNAPSHOT:SNAPSHOT
POLYMARKET_BINARY:*:PRICE_HISTORY
MACRO:1D:SERIES
```

后端是唯一真相来源。更新请求携带 `expected_version`，版本冲突返回 HTTP 409，避免两个页面相互覆盖。`FIXED` Requirement 始终尊重自身来源；只有 `AUTO` 或 `PRIMARY_FALLBACK` Requirement 使用 Data Source 基础顺序。

当前 OpenBB fallback 保持整请求一致性：一次 Provider 尝试必须完整返回并通过 canonical 校验，失败后才尝试下一 Provider，不会逐行或逐股票静默拼接。

## 受控 API

```http
GET /api/data-sources
PUT /api/data-sources/routing
POST /api/data-sources/openbb/activate
POST /api/data-sources/openbb/reload
```

这些 API 都仅接受本机请求。GET 返回来源状态、能力和凭据是否已配置，不返回密钥。PUT 只更新路由模式与基础顺序。OpenBB activate/reload 只操作 DataTube 管理的隔离 OpenBB 子进程，并在返回前完成健康检查。

## 凭据

以下凭据通过 Settings 写入 `web_settings.secrets.json`，使用现有 DPAPI/Fernet 加密：

```text
Finnhub API keys
CoinGecko API key
FRED API key
Polygon API key
Tiingo token
FMP API key
Intrinio API key
```

公开 Settings 和 Data Source API 只返回布尔状态。OpenBB Key 由 `scripts/openbb_service.py` 解密后注入隔离子进程环境，不写入普通 JSON、OpenBB user settings、日志、Manifest 或审计 payload。输入新 OpenBB Key 并保存时，设置页会自动启用对应 Provider、显示分步加载进度、重启 OpenBB companion 并等待健康检查；已保存但未启用的凭据会显示“等待启用”和“一键启用并加载”。

## 安装

`requirements-openbb.txt` 固定安装 DataTube 当前支持的 Provider 扩展。Bootstrap 会检查所有扩展 marker；缺失时在 `.openbb-venv` 中补装，不污染主 `.venv`。

## 后续阶段

- Provider/endpoint 级 quota、token bucket 与并发上限；
- Circuit Breaker 与独立健康事件；
- Provider attempt/failover 审计查询；
- 静态字段质量合同与跨源 Compare；
- Agent 只读分析及可人工 Apply 的排序建议。
