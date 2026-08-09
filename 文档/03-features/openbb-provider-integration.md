# OpenBB 数据 Provider 接入

更新日期：2026-08-05

## 定位

OpenBB 是 DataTube 的可选上游数据网关，不是新的数据平台，也不替换现有 Binance、Polymarket、Finnhub 或 CoinGecko 接口。

```text
OpenBB REST API
    -> OpenBBProviderService
    -> 上游批次（gateway + upstream_provider）
    -> DataTube canonical adapter（后续阶段）
    -> Catalog / Parquet / Manifest
    -> Research Backtest
```

DataTube 始终拥有 instrument identity、canonical schema、Bar 完成状态与 `available_time`、质量检查、内容寻址 Parquet、Catalog、不可变 Manifest 和回测复现语义。OpenBB 返回的数据不能绕过这些步骤直接交给 Research Backtest。

FRED 同样通过 OpenBB Gateway 访问；DataTube 不再建立平行 FRED HTTP Client。当前已验证 FRED Endpoint 与凭证隔离，但 `macro_series.v1`、Vintage、AS_PUBLISHED/INITIAL_RELEASE 和宏观 Point-in-time Manifest 尚未完成，因此 FRED 当前不能作为正式历史回测输入。

## 当前实现范围

当前实现包含受控读取接口和 canonical 日线适配器：

- 连接一个已经运行的 OpenBB REST 服务；
- 复用 DataTube Settings 保存启用状态、Base URL、Provider 白名单和超时；
- 复用 `/api/system/latency` 显示 OpenBB 健康与延迟；
- 暴露只读 capabilities；
- 允许本机请求 OpenBB 股票日线历史数据；
- 保留 `gateway=openbb` 和实际 `upstream_provider`；
- `OpenBBEquityHistoryAdapter` 可以通过受控本地验证流程写入独立的 Research Catalog/Manifest；
- 分钟线仅开放受控的美股盘前只读查询；尚不支持分钟线 Manifest、正式 PIT 基本面、宏观 Manifest 和自动 fallback。

当前受控 DataTube API：

```http
GET  /api/research/data/providers/openbb
POST /api/research/data/providers/openbb/equity/historical
GET  /api/research/data/providers/openbb/fred/series?symbol=DGS10&limit=1
POST /api/research/data/source-policy/resolve
```

HTTP 历史接口允许 `interval=1d`，以及方案 A 的受控 `1m/5m` 盘前查询。分钟查询必须显式传入 `session=PREMARKET_0400_0930_ET`；DataTube 强制向上游请求 extended-hours，再由研究脚本只保留纽约时间 `04:00 <= t < 09:30` 的完整 Bar。`1m` 最长 8 个自然日，`5m` 最长 60 个自然日。

该接口返回的是上游批次，不是正式 Dataset。当前只有日线能够进入 Manifest；正式执行单元仍是 `OPENBB_EQUITY_DAILY_EXPORT` Research Task，必须具有有效 Research Session 所解析出的内部授权、有效 worker lease、幂等预算 reservation 和任务 attempt 记录。旧显式 Grant 仅作为兼容调用保留。

盘前市值排名的可复跑命令：

```powershell
python .agents\skills\datatube\scripts\premarket_market_cap_research.py `
  --interval 5m --lookback-days 7
```

脚本以 `当前市值 / 当前报价` 推导股本代理，再乘以盘前价格。它只能用于探索性快照和前向采样，不能证明历史预测有效性，也不能作为正式 PIT Factor Evaluation 输入。要进入正式因子检验，必须先接入历史时点股本或历史时点市值并冻结为 Manifest。

受控本地验证命令：

```powershell
.venv\Scripts\python.exe scripts\verify_openbb_pipeline.py `
  --symbol AAPL --venue XNAS `
  --start-date 2025-01-01 --end-date 2025-12-31 `
  --provider yfinance --adjustment splits_only
```

该脚本会执行 OpenBB 读取、`bars.v1` 转换、Parquet/Catalog/Manifest 提交和 `FrozenManifestData` 物理验证。它不创建策略、不提交审批，也不执行交易。

## 设置

在 DataTube 的 Settings -> API & 数据源中配置：

```json
{
  "openbb_settings": {
    "enabled": false,
    "base_url": "http://127.0.0.1:6901",
    "default_provider": "yfinance",
    "allowed_providers": ["yfinance"],
    "timeout_sec": 30
  }
}
```

设置沿用现有 `config_loader`，不创建独立配置文件。未来需要 API Key 的 OpenBB Provider 必须接入现有 encrypted secrets，不允许写入普通 `web_settings.json`、Manifest、日志或审计 payload。

FRED API Key 使用现有 Settings 保存。Windows 上由当前用户 DPAPI 加密，启动隔离 OpenBB API 时由 `scripts/openbb_service.py` 解密并只注入子进程的 `FRED_API_KEY` 环境变量；不会生成包含凭据的 OpenBB `user_settings.json`。OpenBB 官方服务需要重启才能读取新的环境变量。

最小隔离环境包含固定版本的 `openbb-core`、`openbb-platform-api`、`openbb-fred`、`openbb-economy` 和轻量 `openbb` interface，目录为 `.openbb-venv/` 且被 Git 忽略。

## 监控语义

OpenBB 作为 `finance` 分组的一项加入现有 `/api/system/latency`：

```text
disabled     配置未启用，不视为管线故障
healthy      OpenBB 服务可访问
degraded     服务有响应，但探针返回非成功状态
unavailable  已启用但所有健康探针均失败
```

健康探针依次尝试 `/system/health`、`/health`、`/openapi.json` 和 `/`。这是服务连通性监控，不代表某个付费 Provider 的凭据、额度和具体 endpoint 一定可用。

## 来源身份

不得把来源只记录成 `OPENBB`。正式落盘时至少记录：

```text
gateway             OPENBB
upstream_provider   YFINANCE / FMP / TIINGO / ...
endpoint            equity.price.historical
provider_version    OpenBB provider extension 版本
request_hash        脱敏后的完整请求摘要
```

同一标的的不同上游 Provider 必须形成不同 Source Dataset。重叠数据不互相覆盖。

## Source Policy

当前支持两种只读策略：

```json
{"mode": "FIXED", "manifest_ids": ["manifest_..."]}
```

`FIXED` 固定一个 READY Manifest，不启用运行时 fallback。

```json
{
  "mode": "COMPARE",
  "manifest_ids": ["manifest_left", "manifest_right"],
  "price_tolerance_bps": 1.0
}
```

`COMPARE` 要求两个 Manifest 的 instrument、data type 和 frequency 相同，报告左右独有时间点、共享时间点、close 差异和冲突。结果始终为 `KEEP_BOTH`，不修改 Source Dataset，也不创建 composite。

受控导出任务支持整请求级 `PRIMARY_FALLBACK`：

```json
{
  "mode": "PRIMARY_FALLBACK",
  "providers": ["fmp", "yfinance"]
}
```

只有网络/API 失败、返回空结果或无完整日线等明确可回退错误才尝试下一个 Provider。OHLC 非法、时间重复、scope 越权和参数错误不会触发换源。成功 Manifest 只包含第一个成功 Provider 的完整请求结果，任务 output 记录每次 source attempt；不会逐行拼接。

## Research Control Plane 任务

任务类型为 `OPENBB_EQUITY_DAILY_EXPORT`。Task input 至少包含：

```json
{
  "grant_id": "grant_...",
  "symbol": "AAPL",
  "venue": "XNAS",
  "provider": "yfinance",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "adjustment": "splits_only",
  "budget": {"download_bytes": 10000000, "runtime_seconds": 300}
}
```

Grant scope 可以限制 `asset_classes`、`venues`、`symbols`、`providers` 和 `endpoints`。worker 在调用 OpenBB 前再次检查这些范围；超出任何白名单都会让任务失败。成功任务消费 budget reservation，失败任务释放尚未消费的 reservation。Agent 不能批准自己的 Plan，也不能扩大人工 Grant。

单次 worker 入口：

```powershell
.venv\Scripts\python.exe scripts\openbb_research_worker.py --worker-id openbb-worker-1
```

worker 每次只领取一个 READY 的 `OPENBB_EQUITY_DAILY_EXPORT` 任务。没有任务时返回 `IDLE`。任务领取、并发冲突、lease、attempt 和失败状态全部复用 Research Control Plane。

常驻轮询模式：

```powershell
.venv\Scripts\python.exe scripts\openbb_research_worker.py `
  --worker-id openbb-worker-1 --continuous --poll-seconds 5 --lease-seconds 300
```

常驻模式只有在没有 READY 任务时才等待；每轮最多执行一个任务。按 `Ctrl+C` 会完成当前同步步骤后停止轮询并输出 `STOPPED`。OpenBB 请求执行期间，worker 每隔不超过 lease 三分之一的时间调用现有 `heartbeat_task` 续租。如果续租失败，任务不会被报告为成功，未消费预算会尝试释放。

只读监控接口：

```http
GET /api/research/data/providers/openbb/worker-status
```

返回 OpenBB 服务健康状态，以及 Control Plane 中该任务类型的 READY、RUNNING、PENDING、SUCCEEDED、FAILED 数量、活动任务和最近失败。Settings 的 OpenBB 区域复用该接口展示摘要，不建立新的 worker 状态数据库。

## Manifest Provenance

每个 OpenBB Manifest 都在 `dataset_manifest_provenance` 中记录：

```text
gateway / upstream_provider / original_publisher
endpoint / gateway_version / provider_version
request_hash / sanitized request / source policy
```

`grant_id` 和 budget 不属于数据身份，不进入 request hash；API key、token、authorization、secret 和 password 字段会在持久化前替换为 `[REDACTED]`。同一个 Manifest 的 provenance 不允许原地修改。`GET /api/research/data/manifests/<manifest_id>` 会随 Manifest 返回 provenance。

## 与现有 Provider 的优先关系

- Binance Kline、市场结构和实时行情继续以 Binance 原生接口为准；
- Polymarket 市场、盘口和历史继续以 Polymarket 原生接口为准；
- OpenBB 首先用于补充股票、ETF、宏观和基本面覆盖；
- OpenBB 的聚合加密价格不能静默替代 Binance 交易场所数据；
- 正式回测固定读取 Manifest，运行期间不调用 OpenBB 或自动切源。

## Requirement、Plan 与 Bundle

OpenBB 数据准备已纳入统一研究对象：`RequirementSet` 描述需要什么数据；`Resolved Data Plan` 冻结 OpenBB Gateway、Endpoint、Provider、复权语义和成本估算；执行前验证 `Plan ⊆ Grant Scope`，范围内无需逐计划人工审批。READY Manifest 可与 Universe Snapshot、RequirementSet、Plan 和 Policy Version 一起冻结为 `RESEARCH_INPUT_BUNDLE` Artifact。回测通过 `input_bundle_id` 引用该集合，不读取 Catalog 最新状态。

`/research` 页面复用现有 DataTube 导航与 UI 组件。研究构建器提供本机受控的 Project 创建、Requirement 编译/Coverage、Resolved Plan 生成和 READY Manifest Bundle 冻结；对象浏览器提供 Project、Requirement、Resolved Plan、Dataset、Bundle、Artifact 和 Worker 的只读检查视图。普通 Research / Agent Run 仍受 Session、Scope 与预算控制；Requirement 历史数据准备则由独立的系统维护器自动创建受限任务。该维护路径目前只允许 OpenBB 股票日线、Binance Bars 与 Polymarket Price History，不借用用户 Grant，也不能用于研究计算或交易执行。Research / Library 只读取其状态与实时进度。

## Canonical 日线语义

股票日线当前支持 `XNAS` 和 `XNYS`，并要求显式提供 venue。Dataset ID 包含 gateway、实际 provider、instrument、频率和复权口径，例如：

```text
openbb:yfinance:equity:XNAS:AAPL:bars:1d:splits_only
```

不同 provider 或不同复权口径不会写入同一个 Source Dataset。当前采用保守的 `CONSERVATIVE_D_PLUS_1_UTC` 可用性策略：交易日期对应的数据到下一自然日 00:00 UTC 才视为可用。这比假设固定美股收盘时间更保守，能够避免当前交易日未完成 Bar，但会牺牲一部分信号及时性。以后接入正式交易所日历后可以发布新的 source/schema version，不能原地修改旧 Manifest 语义。

Binance 和 OpenBB 现在共用 `CanonicalBarsCommitter`，统一完成：

- 月度分区；
- 内容寻址 Parquet；
- schema/source version metadata；
- Dataset fingerprint；
- Catalog upsert；
- 不可变 Manifest commit。

## 下一阶段

1. 引入正式 XNAS/XNYS 交易所日历，将保守 D+1 策略升级为精确 close/early-close；
2. 为常驻 worker 增加进程级启动/停止控制和更细粒度运行审计；
3. 从 OpenBB capabilities 中稳定提取 core/provider extension version；
4. 完成五股票真实数据端到端回归；
5. 增加 fallback/compare 的 UI 与运行审计视图；
6. 设计分区级 composite lineage；仍禁止逐行静默拼接。
