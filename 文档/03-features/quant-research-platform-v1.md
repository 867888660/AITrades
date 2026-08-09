# DataTube Quant Research V1 当前实现

更新日期：2026-08-04

复核日期：2026-08-04（Requirement 自动维护、实时进度、Research / Library UI 与全量回归）

本文只描述已经落地并通过测试的能力。Legacy Strategy Backtest、Research Backtest、VirtualRunner 和 Real Execution 是四条不同链路，不能互相冒充。

## 当前定位

DataTube 已完成本次冻结的 Research Run 基线，并通过真实五标的横截面 Phase 0B、原子故障、端到端 Worker 和 UI 回归验收。除原有 Factor/Alpha Evaluation、Requirement、Coverage、Plan 与受控数据 Worker 外，现已接通全局 Library、项目 Definition Ref、五层 Readiness、确定性 Manifest Resolver、Run Inputs Preview、Frozen Bundle v2、预算预留、QUEUED Run、Outbox、Definitions 输入模式的 Formal Research Worker，以及面向 Agent 的 START/RESUME Research Session、Context Resolver、Research Iteration 和 AgentMonitor。Requirement 自动维护器现会扫描全部 Library Requirement 与活跃 Research，自动创建 Binance、OpenBB Equity Daily 和 Polymarket 历史数据任务，并在 Research / Library 显示实时进度。统一 Research Task DAG Orchestrator、FRED Point-in-time 和精确股票交易日历仍未完成。

```text
Phase 0A：单标的数据与执行语义验证——已完成
Phase 0B：真实五标的横截面研究闭环——已完成
Phase 1A：Factor / Alpha Evaluation——已完成 V1
Phase 1B：Gap Detector / 受控 Binance Backfill Worker——已完成 V1
Phase 1C：RequirementSet / RequirementCompiler / Coverage——已完成 V1
Phase 1D：Resolved Data Plan / Grant Scope / Input Bundle——已完成 V1
Phase 1E：Research 工作卡片 + 不可变 Library 资产——已完成 V2
Phase 1F：Formal Research Worker（Definitions 输入模式）——已完成 V1
Phase 1G：Readiness / Resolver / Preview / Atomic Run Lifecycle——已完成 V1
Phase 1H：Research Agent Session / Context Resolver / Iteration——已完成 V1
Phase 1I：Requirement 自动扫描 / Provider Task / Live Progress——已完成 V1
后续：统一 Task DAG Orchestrator——待完成
```

## 已验证的真实 Phase 0B

固定数据窗口：2026-04-11 00:00 UTC 至 2026-07-09 23:00 UTC。

```text
BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT / XRPUSDT
5 × 2160 根完整 1h Bar
momentum_20
volatility_20（20 期收益率波动率）
0.7 × cs_rank(momentum_20) - 0.3 × cs_rank(volatility_20)
Top 2 / Equal Weight / Long Only
每日调仓
下一根 1h Bar Open 成交
fee = 10 bps / slippage = 5 bps
```

验收结果：

```text
Alpha 横截面数量：2140
实际再平衡：89
出现过的 Top-2 组合：19
成交：278
相同输入确定性重放：通过
Manifest / Universe / Factor / Alpha / Portfolio / Backtest Lineage：通过
```

该样本总收益约为 -18.65%。这只是研究链路验收结果，不能解释为策略推荐或收益承诺。

## 2026-07-12 独立复核修正

第二轮没有直接接受第一次实现结论，而是用反例重新审计。已修正：

- `available_time` 精确等于下一根 Bar Open 时曾额外延迟一根 Bar；
- `AVERAGE` Rank 在同分值下曾退化为按 Instrument ID 排序；
- `read_rows(columns=..., as_of=...)` 未显式读取 `available_time` 时会错误返回空集；
- `ema` 曾在每个窗口重新起算，现改为 SMA Seed 后递推；
- Artifact 并发写使用同一临时文件名，且零成交 Orders 无法落盘；
- Binance Adapter 连接未强制只读，未来未闭合 Bar 未排除；
- 字符串布尔值和 NaN 成本/权重的输入验证不严格；
- Task DAG 缺少环检测，Plan/Grant/Task 幂等键可静默复用不同内容；
- 过期 Grant 可能编译任务，过期 Lease Worker 可能提交完成；
- Turnover 统一改为按实际成交金额统计。

修正后旧 Smoke Test、真实 Phase 0B 和正式反例/并发/故障测试全部通过。Factor/Alpha/Backtest Artifact 因 Code Hash 改变生成新版本，没有错误复用旧结果。

验收入口：

```powershell
.venv\Scripts\python.exe scripts\verify_cross_sectional_phase0b.py
```

## 正式研究链

```text
Data Requirement
    → Dataset Catalog
    → content-addressed Parquet
    → Dataset Manifest
    → FrozenManifestData physical validation
    → Universe Definition / Snapshot
    → FactorSpec / Factor Artifact
    → AlphaSpec / Alpha Artifact
    → PortfolioSpec / Portfolio Targets
    → ExecutionSpec / NEXT_BAR_OPEN
    → Orders / Equity / Backtest Result
    → Artifact Lineage / Pin
```

## Requirement 编译模型

研究者编辑 `Factor / Alpha / Universe / Backtest / Evaluation`，系统将依赖编译为冻结的 `RequirementSet`；人工补充需求参与同一次语义合并。相同输入幂等复用，任何来源版本或语义变化都会创建新版本，旧版本进入 `SUPERSEDED` 而不会被覆盖。

```text
Declared Requirement
    → 语义展开与依赖追踪
    → Resolved RequirementSet
    → Catalog / Manifest Coverage
    → Effective Requirement
```

合并键包含 `data_type / frequency / adjustment / time_semantics / point_in_time_policy / quality_policy / source_policy`。只有语义兼容的需求才能合并字段与最大预热观测数；系统依赖不可直接删除，人工依赖通过 `MANUAL` Dependency Link 单独识别。

Requirement 自动维护属于后端基础设施，不是 Research Agent 自主操作：维护器每 30 秒扫描所有 Library Requirement（包括尚未被 Research 引用的资产）以及活跃 Research 的非 `SUPERSEDED` RequirementSet，把可支持的数据缺口转换为受限、幂等、可审计的 Provider Task。该路径使用隐藏系统项目与内部维护授权，只允许 Binance Bars、OpenBB Equity Daily 和 Polymarket Price History 三类任务；它不借用用户 Grant，也不扩大 Research Session Policy。终态失败保持可见，不会在调度循环中无限重建。详见 [Requirement 数据自动维护与实时进度](../04-operations/requirement-data-maintenance.md)。

```text
GET /api/research/data/requirement-sets
GET /api/research/data/requirement-sets/<id>
GET /api/research/data/requirement-sets/<id>/coverage
```

## Resolved Data Plan 与 Research Input Bundle

`Resolved Data Plan` 与 `Research Input Bundle v1` 继续作为兼容读取链路，复用 Artifact/Lineage/Pin，不建立平行产物系统。它们不再承担正式 UI 的运行前冻结语义：Resolved Plan 只在 Requirements 高级详情展示，v1 Bundle 不得冒充 v2 Frozen Bundle。

正式 v2 链路先生成 `Run Inputs Preview`。Preview 包含 Definition、Data Resolution、Execution 和 Authorization closure 的指纹，但它仍是可失效的解析结果；Factor、Alpha、Universe、Requirement、Manifest、Engine、Policy、Grant 或 Resolver 相关版本变化时必须标记 `STALE` 并重新解析。

创建 Run 时，服务端在单一事务中重新校验 Preview、确定性 Manifest 结果、当前授权和预算，然后写入不可变 Frozen Bundle、预算预留、`QUEUED` Run 与 Outbox；任一步失败全部回滚。Bundle 保存 `grant_id / grant_version / scope_snapshot / policy_version / authorization_check_result` 作为历史授权证据，但 Replay 或新 Run 必须重新检查当前复用授权，不能继承旧授权。

Definitions 输入模式固定 Factor/Alpha Definition 的 ID、Version、Spec Hash，以及精确 Manifest、Universe Snapshot、Engine/Code Hash；运行完成后由 Run 关联 produced Artifact。`PRECOMPUTED_ARTIFACTS` 模式可在 Bundle 合同中固定输入 Artifact，但 Formal Research Worker V1 尚未执行该模式。Bundle 不复制 Parquet，也不因新版本出现而失去历史意义；物理损坏或当前策略禁止复用时必须拒绝再次执行。

`/research` 已升级为英文简化研究工作台。项目区提供 Overview / Universe / Factor / Alpha / Data / Strategy / Runs；Library 通过各页面的选择入口复用。RequirementSet、Resolved Plan、Preview、Frozen Bundle 与技术 ID 不作为普通用户的必选操作，但 Data 页保留 Effective Contract、Coverage、自动维护状态与 `Live Download`。普通缺口显示 `Queued / Preparing / Checking`，终态错误显示 `Failed / Unavailable`；Data 状态页不再提供 `Complete Missing Data`、独立下载或下载重试触发按钮。

UI 提交 `BTCUSDT` 等上游符号时，受控 API 必须先通过 Instrument Registry alias 解析为 `crypto_spot:BINANCE:BTCUSDT` 等规范化 Instrument ID，再进入 Requirement Compiler。这样 Requirement Coverage、Catalog 和 Manifest Resolver 使用同一身份，不会把已有数据误报为 `DATASET_MISSING`。本机 Human UI 与持有有效 Research Session 的 Agent 均可通过正式 API 创建研究对象；Agent 写入会逐次校验 Capability、Session、Scope 与预算，不会创建或批准策略，也不会触发实盘交易。Session 在后端映射到内部授权兼容记录，Agent 和用户均不需要管理 `grant_id`。数据缺口仍通过独立受控任务补齐，但任务由后端维护器自动创建，而不是由页面点击或 Run 临时联网触发。

## Universe

已支持：

- `STATIC_LIST`
- `TOP_N_BY_TURNOVER`

Universe Definition 不可原地修改；同名同版本但规则不同会被拒绝。Snapshot 固定：

```text
universe_snapshot_id
universe_definition_id
as_of_time
actual_instrument_ids
selection_inputs
selection_rule_version
dataset_manifest_ids
fingerprint
```

Alpha、Factor Artifact、Portfolio Target 和 Backtest Result 都可以绑定 `universe_snapshot_id`。

## 数据冻结规则

`BinanceHistoryAdapter` 与 `OpenBBEquityHistoryAdapter` 共用 `CanonicalBarsCommitter`，避免不同 Provider 各自实现 Parquet、fingerprint 和 Manifest 提交语义。OpenBB 当前只支持 XNAS/XNYS 股票日线，采用保守 D+1 UTC 可用时间；不同上游 Provider 和复权口径形成不同 Source Dataset，不能静默覆盖或拼接。详见 [OpenBB 数据 Provider 接入](openbb-provider-integration.md)。

新导出的 Parquet 使用内容寻址文件名：

```text
.../year=YYYY/month=MM/objects/sha256-<content_hash>.parquet
```

提交顺序：临时写入、关闭、质量检查、Checksum、原子 Rename、Manifest Commit、Catalog READY。旧对象不被覆盖。

`FrozenManifestData` 打开和读取时都会验证：

- Manifest 必须为 `READY`；
- 文件存在，大小和 SHA-256 一致；
- Parquet 内嵌 Schema Version 一致；
- Row Count 和时间范围一致；
- `bars.v1` 必需字段完整；
- Bar 必须为 `COMPLETE`；
- `available_time >= bar_end_time`。

同一 Dataset 内容变化后生成 Manifest V2；V1 继续指向自己的物理对象并可复现。故障注入测试会主动篡改 V1 文件并确认读取被拒绝。

## Factor / Alpha 语义

FactorSpec 当前显式记录：输入、算子、窗口、频率、计算维度、最小观测数、缺失规则、时间对齐、可用时点、未完成 Bar 规则、输出单位与方向、Engine Version 和 Code Hash。

当前 Factor 算子：

```text
pct_change / difference / ratio
rolling_mean / rolling_std / rolling_return_std / ema
ma_crossover（fast_window < slow window；金叉 +1，死叉 -1，其余 0）
```

`ma_crossover` 于 2026-07-16 接入 `factor-engine.v3`。测试合同使用 `window` 表示慢线窗口、`parameters.fast_window` 表示快线窗口，并需要 `slow_window + 1` 个观测值才能比较前后两个时点。Factor 使用 `factor_as_of_time` 与 `available_time`，完整 Bar 的 Close 只能在 Bar Close 后参与计算。

Alpha 明确使用 `CS_RANK`，并将 `raw_score`、`rank` 和 `percentile` 分离。AlphaSpec 记录 Universe Snapshot、最低横截面数量、Coverage、Missing Policy、权重、变换、Engine Version 和 Code Hash。

Alpha 本地编辑器与 Factor 同构的 Draft、真实值 Preview、指纹失效和 Validate 设计见 [Research Alpha 编辑与验证系统设计](research-alpha-editor-design.md)。

## Portfolio 与回测会计

当前 PortfolioSpec 支持：

```text
TOP_N / EQUAL_WEIGHT / LONG_ONLY
DAILY 或 EVERY_SIGNAL
max_position_weight / minimum_score / cash_buffer
```

Portfolio 只在 `RESEARCH_BACKTEST` 中执行。每个 Signal timestamp 都必须生成 Target：存在合格标的时为 `INVESTED / TOP_N_SELECTED`；没有 score 达到 `minimum_score` 时为显式 `FLAT / NO_ELIGIBLE_INSTRUMENT`，并使用空 weights 触发已有持仓在下一根可执行 Bar 退出。不得把“无合格标的”解释为“沿用上一期持仓”。

ExecutionSpec 固定支持：

```text
signal = BAR_CLOSE
execution = NEXT_BAR_OPEN
price = NEXT_OPEN_PLUS_SLIPPAGE
fee = FIXED_BPS
slippage = FIXED_BPS
missing_price = FAIL_RUN
fractional_quantity = true
exchange_rounding = false
target_equity = EXECUTION_OPEN_PRE_TRADE
sell_before_buy = true
```

买入滑点向上、卖出滑点向下，买卖双方按成交额收费。先卖后买；现金不足时所有买单统一按比例缩减，不允许无意负现金。多标的时间线必须完全对齐，缺 Bar 会失败，不会静默取交集或 Forward Fill。

Backtest Result 记录 Manifest、Universe、Factor、Alpha、Portfolio/Execution Hash、Engine/Code Version、Random Seed、订单、再平衡事件、持仓和净值。指标包含收益、年化收益、波动率、Sharpe、回撤、换手、费用、滑点成本、成交次数、调仓次数、暴露和现金比例。

## Factor / Alpha Evaluation V1

统一 Future Return 定义：信号在 `available_time=T` 可用，从第一根 `bar_start >= T` 的未来 Bar Open 建仓，到第 `h` 根未来 Bar Close 结束。当前支持 1/6/24 等任意正整数 Bar Horizon，禁止把形成信号的当前 Close 混入未来收益。

Factor Evaluation 已输出：

```text
Coverage / Missing Rate
Mean / Std / Quantiles / 5-Sigma Outlier Ratio
Cross-section Mean Stability / Lag-1 Correlation
Average Rank Turnover
Rank IC Mean / Std / Positive Rate
Quantile Returns / High-Low Spread
```

Alpha Evaluation 已输出：

```text
Score Distribution
Pearson IC / Spearman Rank IC（按 Horizon）
Consecutive Rank Stability
Top / Bottom Return
Long-Short Spread
Top-N Membership Turnover
1/6/24 Bar Holding-period Decay
Estimated Return After Fee + Slippage
Bull / Bear / Sideways Regime Performance
```

当前正式边界已经拆分：`FACTOR_EVALUATION` 与 `ALPHA_EVALUATION` 只负责预测性评估；Portfolio、Execution、Position、Trade、Cost、Equity、Performance 与 Drawdown 只由 `RESEARCH_BACKTEST` 生成。历史 Alpha Evaluation 若包含回测 Artifact，作为 `LEGACY_HYBRID_RUN` 只读兼容，不会被改写。详见 [Factor Run、Alpha Run 与 Research Backtest MVP](factor-alpha-run-mvp.md)。

真实五标的样本的 1h 诊断结果：`momentum_20` 平均 Rank IC 约 `-0.0095`，组合 Alpha Top-Bottom Spread 约 `0.0037%`，Rank Stability 约 `0.83`，Top-2 Membership Turnover 约 `16.0%`。这些数值说明当前信号边际较弱且成本敏感，不构成策略推荐。

## Binance Gap Detector / Backfill Worker V1

受控任务类型：

```text
BINANCE_BARS_BACKFILL
```

执行链：

```text
Approved Research Task
    → 验证 Grant / Project / Plan / Symbol / Interval / Endpoint Scope
    → 原子 Budget Reservation
    → Backfill Job Lease
    → 精确 Gap Detection
    → Binance API 分页下载
    → Page Cursor / Heartbeat / 指数退避
    → 失败后保留已完成分页并断点续传
    → 完整性与 OHLC Quality Check
    → Canonical Parquet / Manifest Commit
    → Provenance / Task Output / Budget Consume
```

Backfill Job 固定记录：

```text
idempotency_key / source_request_hash
cursor_time / pages_completed / rows_fetched / rows_stored
attempt_count / max_attempts / next_retry_at
lease_owner / lease_expires_at / heartbeat_at
manifest_commit_status / manifest_id / dataset_id
last_error / completed_at
```

Gap Detector 不使用三倍周期容忍值，按期望 Open Time 精确识别连续缺口、重复时间、错位时间和范围外数据。请求结束时间会裁剪到最后一根已完成 Bar，未完成 K 线不会进入 Manifest。

Worker 入口：

```powershell
.venv\Scripts\python.exe scripts\binance_backfill_worker.py --once
```

Worker 只会领取已经存在且具有人工 Grant 的 READY Task；它不会自行创建项目、批准计划或扩大预算。

只读状态 API：

```http
GET /api/research/data/backfill/binance/jobs
GET /api/research/data/backfill/binance/jobs/<job_id>
GET /api/research/data/backfill/binance/worker-status
```

## Artifact 与 Lineage

正式类型集合：

```text
DATASET_MANIFEST / UNIVERSE_SNAPSHOT
FACTOR_VALUES / FACTOR_EVALUATION
ALPHA_VALUES / ALPHA_EVALUATION
PORTFOLIO_TARGETS
BACKTEST_ORDERS / BACKTEST_RESULT
RESEARCH_REPORT
RESOLVED_DATA_PLAN
RESEARCH_INPUT_BUNDLE
```

新 Artifact 身份包含完整 Spec、输入 Manifest/Artifact、Universe Snapshot、Schema、Engine Version 和 Code Hash。名称和参数相同但代码变化时不会复用旧 Artifact。已发布 Backtest Result 自动 Pin。

## API

当前 Research API 同时提供兼容读取接口和受本机访问、身份、Research Session、预算、幂等与审计约束保护的 v2 写接口：

```http
GET  /api/research/data/catalog
GET  /api/research/data/requirements
GET  /api/research/data/requirement-sets
GET  /api/research/data/requirement-sets/<id>
GET  /api/research/data/requirement-sets/<id>/coverage
GET  /api/research/data/manifests/<manifest_id>?verify=1
GET  /api/research/universes
GET  /api/research/universes/<definition_id>/snapshots
GET  /api/research/universe-snapshots/<snapshot_id>
GET  /api/research/artifacts
GET  /api/research/resolved-plans
GET  /api/research/input-bundles
GET  /api/research/input-bundles/<artifact_id>/verify
GET  /api/research/backtest/capabilities
POST /api/research/backtest/validate
GET  /api/research/projects
GET  /api/research/projects/<project_id>
GET/POST /api/research/definitions
POST /api/research/definitions/<definition_id>/validate
GET  /api/research/definitions/<definition_id>/impact
GET  /api/research/projects/<project_id>/definition-refs
PUT  /api/research/projects/<project_id>/definition-refs/<slot_key>
POST /api/research/manifest-resolver/resolve
GET  /api/research/run-input-previews
POST /api/research/projects/<project_id>/run-input-previews
GET/POST /api/research/runs
GET  /api/research/frozen-bundles/<bundle_id>
POST /api/research/frozen-bundles/<bundle_id>/verify
POST /api/research/run-worker/run-once

GET/POST /api/agent/research/sessions
GET  /api/agent/research/sessions/<session_id>
GET  /api/agent/research/context
POST /api/agent/research/sessions/<session_id>/status
POST /api/agent/research/sessions/<session_id>/continue
POST /api/agent/research/sessions/<session_id>/need-human
POST /api/agent/research/sessions/<session_id>/answer
POST /api/agent/research/sessions/<session_id>/iterations
POST /api/agent/research/iterations/<iteration_id>/complete
```

`POST /api/research/backtest/validate` 仍只做能力验证，不运行回测。Human UI 写接口受本机限制；Agent 使用独立 `/api/agent/research/*` 路径，并同时受全局 `research.*` capability 与 Research Session 约束。Run 创建会重新校验 Preview、Session 解析出的内部授权、预算与幂等键，并在单一事务中写入 Bundle、Run 和 Outbox。旧显式 Grant 接口仅用于兼容既有调用。

## 自动测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe scripts\verify_data_platform.py
.venv\Scripts\python.exe scripts\verify_backtest_contract.py
.venv\Scripts\python.exe scripts\verify_research_backtest.py
.venv\Scripts\python.exe scripts\verify_history_export.py
.venv\Scripts\python.exe scripts\verify_research_pipeline.py
.venv\Scripts\python.exe scripts\verify_research_control_plane.py
.venv\Scripts\python.exe scripts\verify_research_api.py
.venv\Scripts\python.exe scripts\verify_cross_sectional_phase0b.py
```

2026-07-16 回归结果为 58 个单元测试、14 个集成测试和 1 个故障注入测试全部通过；前端 JavaScript 语法检查与 Python 编译通过。覆盖范围新增 Project Research Grant、暂停/恢复、Provider/Universe 越界阻断，以及 Agent 创建对象在正式 Research 前端可见。

2026-08-01 新增 8 个 Research Session/Iteration 单元与 API 测试；原有 Research
Agent 授权兼容测试通过；另执行 29 个 Research Control Plane、Run Contract 与
Workspace 回归测试。两份 DataTube Skill 均通过 `quick_validate.py`，AgentMonitor
JavaScript 与相关 Python 模块语法检查通过。

2026-08-04 Requirement 自动维护与实时进度回归通过：完整 DataTube Research Workspace 测试入口返回成功，覆盖 218 个单元测试、21 个集成测试、1 个故障注入测试以及只读在线检查；同时验证 Library 未引用 Requirement 的扫描、无用户 Grant 的受限维护任务、幂等去重、Research / Library 进度展示和手动补数按钮移除。

## 明确未完成

- 通用的 `CHECK_DATA → BACKFILL → COMMIT_MANIFEST → PREVIEW → RUN` Research Task DAG Orchestrator；当前 Requirement 维护器直接创建三类受支持的 Provider 数据任务，Formal Research Worker 与 Provider Worker 仍各自执行。
- `PRECOMPUTED_ARTIFACTS` 可由 Bundle 合同表达，但 Formal Research Worker V1 尚不执行该模式，必须稳定返回阻断原因，不能伪装成功。
- Research Session 已有独立事件流水且 START 写入 Agent 审计；其余状态和 Iteration 与现有 `agent_runs / agent_run_steps / agent_audit_events` 的完整统一仍未完成。
- 更正式的行业/风格中性化、外部市场阶段标签、复杂 Data Center 和交互式 Lineage 图。
- FRED Point-in-time、精确股票交易日历、Exchange rounding、tick/step/min-notional，以及股票和 Polymarket 专属研究会计。

上述能力不得在 UI、API 或文档中提前标记为“已支持”。
