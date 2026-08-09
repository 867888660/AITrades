# Factor Run、Alpha Run 与 Research Backtest MVP

状态：已实现并完成回归验收（2026-08-04）

本文定义 Research Runs 的产品边界、结果合同、页面栏目和历史兼容规则。正式执行仍遵循 [Research Run 语义与实现基线](research-run-semantics.md) 中的 Preview、Frozen Bundle、Manifest、授权与 Lineage 规则。

## 最终产品边界

最初的 MVP 曾把 Alpha 信号评估、组合构建和回测会计放在同一个 Alpha Run 中。最终实现将其拆成三个可独立解释的 Run：

```text
Factor Evaluation
  因子值 → Coverage / Distribution → IC / Rank IC → Quantile Return
        ↓
Alpha Evaluation
  因子组合 → Signal → IC / Rank IC → Decay / Turnover / Regime
        ↓
Research Backtest
  冻结 Alpha Lineage → Portfolio Targets → Positions / Trades / Costs
  → Equity / Performance / Drawdown
```

边界固定为：

| 产品 Run | 持久化 `run_type` | 开始 | 结束 | 不负责 |
|---|---|---|---|---|
| Factor Run | `FACTOR_EVALUATION` | 已验证 Factor Definition 的确定性计算 | 因子预测能力与分组表现 | Signal、Portfolio、交易与收益 |
| Alpha Run | `ALPHA_EVALUATION` | Signal 构建 | Signal 预测能力、衰减、稳定性与换手诊断 | Portfolio、执行、持仓、交易与收益 |
| Research Backtest | `RESEARCH_BACKTEST` | 冻结的研究输入和 Alpha Lineage | 组合、交易、成本、净值、业绩与回撤 | Strategy 创建、Paper、Live |

因此，当前最准确的产品表述是：

> Factor Run 结束于因子预测能力与分组表现；Alpha Run 负责从因子组合到可评估 Signal；Research Backtest 从组合构建开始，结束于交易、成本与收益。

三个 Run 都是 Research 对象，不会自动创建 Strategy。Strategy 运行时引用已发布的 Library Alpha，是后续独立边界。

## 共同运行合同

三个 Run 都必须冻结并记录：

- Project Revision、Universe Snapshot 与实际 instrument 列表；
- Factor/Alpha Definition ID、版本、Spec Hash、Engine Version 与 Code Hash；
- Effective RequirementSet、Exact Manifest IDs、Provider、频率、字段与时间范围；
- Evaluation、Portfolio、Execution、Benchmark 等当前 Run 需要的 Spec Hash；
- Preview Fingerprint、Frozen Bundle、Research Session 授权快照与预算；
- Produced Artifact IDs、Artifact Dependencies、Run 时间、尝试次数、错误与 Lineage。

研究区间与 warmup 分离：Requirement Compiler 可以为窗口计算向前扩展数据开始时间，但 Factor、Alpha 和回测输出必须切回用户指定的 research period。形成信号的当前 Close 不得进入 Future Return；执行采用下一根 Bar 的可成交时点。

Artifact 允许按不可变内容复用。结果读取不能只依赖 `created_by_run_id`，还必须读取 Run `output.produced_*_artifact_ids` 中引用的 Artifact；因此命中缓存或内容去重后，结果页仍能展示完整栏目和 Lineage。

## Factor Run MVP

### Overview

展示 Run ID、Project、状态、Bundle、开始/结束时间、Factor 数量、结果 Artifact 和诊断汇总。产品结果合同为 `factor-run-result.v1`，`product_run_type=FACTOR_RUN`。

### Factor Definition

展示不可变的 Definition ID、名称、版本、Spec Hash、Engine Version、计算维度、频率、输出单位和方向。页面只能解释本 Run 已冻结的版本，不能用当前草稿覆盖历史定义。

### Universe

展示 Universe Snapshot ID、版本、实际 instrument 列表和成员数。Factor 统计只在该冻结横截面内计算。

### Data Inputs

展示 Exact Manifest、Provider、Dataset、频率、字段、research period、warmup coverage、质量状态和校验身份。Run 不在计算过程中联网补数或重新选择 Manifest。

### Factor Output

`FACTOR_VALUES` 保存逐 instrument、逐 `factor_as_of_time` 的因子值和可用时间。输出身份包含 Factor Spec、输入 Manifest、Universe Snapshot、Engine Version 和 Code Hash。

### Coverage

至少输出：

- overall coverage、missing rate、valid rows、total rows；
- instrument 级 coverage；
- cross-section count 与 eligible cross-section count。

Coverage 只描述结果可用比例，不等同于 Data Readiness。

### Distribution

至少输出 mean、std、quantiles、5-Sigma outlier ratio、时间稳定性和 average rank turnover，用于识别尺度异常、极端值和横截面排序不稳定。

### IC / Rank IC

按 EvaluationSpec 的每个 horizon 分别输出 Pearson IC 与 Spearman Rank IC：

```text
count / mean / std / ICIR / t_stat / positive_rate
```

每个时间点只使用具有有效 Factor 值和 Future Return 的横截面；样本不足时不伪造为 0，而是减少 count 并产生稳定诊断。

### Quantile Return

按 horizon 输出各 quantile 的 mean return、High-Minus-Low Spread 与 monotonicity。Factor Run 在这里结束，不生成 Target、Position、Trade、成本、Equity 或 Drawdown。

### Diagnostics

诊断使用稳定 `code + severity + details`，覆盖样本不足、Coverage、IC 不可用、分组不可用、异常值和数据一致性问题。自然语言只用于展示，不作为控制条件。

### Logs

展示 Run 状态、`created_at / queued_at / started_at / finished_at`、`attempt_count` 和结构化 `error`。Logs 描述执行生命周期；Diagnostics 描述研究结果质量，两者不能混用。

## Alpha Run MVP

### Overview

展示 Run、Project、Bundle、Alpha 数量、Signals 数量、Evaluation Artifact、诊断汇总和时间。新合同为 `alpha-evaluation-result.v2`，`product_run_type=ALPHA_RUN`。

### Alpha Definition

展示 Definition ID、名称、版本、Spec Hash、Engine Version、output scale、minimum coverage、minimum cross-section size 和 missing policy。

### Factor Inputs

每个 Component 固定记录 Factor Definition ID、版本、Spec Hash、权重、transform、ascending 方向和实际 `FACTOR_VALUES` Artifact。相同名称不能替代不同版本或不同 Spec Hash。

### Universe

Alpha 必须使用冻结的 Universe Snapshot。横截面数量不足时按合同排除该时间点并记录诊断，不允许静默缩小到一个不同 Universe。

### Signal Rules

Alpha Engine 明确分离：

```text
raw_score / rank / percentile
```

Signal Rules 来自不可变 AlphaSpec，包括组件权重与变换、排序方向、Coverage、Missing Policy、Rank Method 和 Output Scale。

### Signals

`ALPHA_VALUES` 保存逐时间、逐 instrument 的 raw score、rank、percentile、Coverage 和组件贡献。Signals 是预测性研究输出，不是订单。

### IC & Accuracy

Alpha 与 Factor 一样，按 horizon 输出 Pearson IC 和 Spearman Rank IC 的 count、mean、std、ICIR、t-stat 与 positive rate。`IC_UNAVAILABLE` 表示横截面或 Future Return 不足，不能解释为 IC 等于 0。

### Decay

Holding-period decay 输出 1/6/24 或 EvaluationSpec 指定 horizon 的 Top、Bottom、Long-Short Spread，以及信号效果随持有期变化的结构。

### Turnover

展示 average membership turnover、average rank stability 和有效 score count，回答信号是否稳定以及后续组合化可能面临的换手压力。

### Regime Analysis

按已定义的 Bull、Bear、Sideways 等 regime 展示信号表现。没有足够样本时必须显示不可用或样本数，不得外推结果。

### Diagnostics

覆盖 Coverage、横截面数量、IC、Decay 和 Regime 样本不足等问题。Alpha Run 不产生 Position、Trade、Equity、成本或 Drawdown；这些栏目只属于 Research Backtest。

### Logs

语义与 Factor Run 相同：记录生命周期和结构化错误，不替代 Evaluation Diagnostics。

## Research Backtest MVP

Research Backtest 使用 `research-backtest-result.v1`，`product_run_type=RESEARCH_BACKTEST`。V1 要求恰好一个 Alpha Definition，并冻结其 Factor/Alpha Lineage。

### Portfolio Rules

当前支持 `TOP_N / EQUAL_WEIGHT / LONG_ONLY`、`DAILY / EVERY_SIGNAL`、`top_n`、`max_position_weight`、`minimum_score` 和 `cash_buffer`。

Portfolio Engine 对每个 Signal timestamp 都产生一个 Target：

```text
INVESTED + TOP_N_SELECTED
FLAT     + NO_ELIGIBLE_INSTRUMENT
```

当没有 score 达到 `minimum_score` 时，必须生成显式 `weights={}` 的 `FLAT` Target，而不是跳过该时间点。这样已有持仓会在下一根可执行 Bar 产生 SELL 并回到零仓位。

### Execution Assumptions

V1 固定使用：

```text
signal = BAR_CLOSE
execution = NEXT_BAR_OPEN
price = NEXT_OPEN_PLUS_SLIPPAGE
fee = FIXED_BPS
slippage = FIXED_BPS
sell_before_buy = true
target_equity = EXECUTION_OPEN_PRE_TRADE
fractional_quantity = true
missing_price = FAIL_RUN
```

买入滑点向上、卖出滑点向下；买卖双方按成交额收费；现金不足时买单按同一比例缩减。

### Benchmark

BenchmarkSpec 可以被冻结，但只有实际生成 Benchmark Series 后才允许展示 excess return 或 information ratio。当前 `benchmark_status.materialized=false` 时，UI 必须明确提示不可推断超额指标。

### Portfolio Targets、Positions 与 Trades

`PORTFOLIO_TARGETS` 记录 weights、eligibility、`target_state` 和 `selection_reason`；`POSITION_SERIES` 记录每根 Bar 的数量、市值、现金和暴露；`BACKTEST_ORDERS` 只记录实际成交。

最后一个 Signal 如果没有下一根 Bar，只能记录 skipped rebalance，不能伪造成 Trade。`rebalance_count` 因此可能小于 Target timestamp 数。

### Equity Curve、Performance Metrics 与 Drawdown

至少输出 initial/final equity、total/annualized return、volatility、Sharpe、turnover、fees、slippage cost、trade count、rebalance count、invested/flat rebalance count、exposure、cash ratio、max drawdown、回撤峰值和最长 underwater bars。

费用与滑点必须能逐 Trade 汇总回 Performance Metrics。Drawdown 从同一 Equity Series 派生，不允许使用另一套净值口径。

### Diagnostics 与 Logs

Diagnostics 记录研究回测数据、执行与会计异常；Logs 记录 Run 生命周期、尝试次数和错误。两者都只属于本地历史研究，不代表策略审批或真实执行状态。

## 旧 Alpha Hybrid 兼容

历史 `ALPHA_EVALUATION` Run 可能已经同时包含 `PORTFOLIO_TARGETS` 和 Backtest Artifacts。系统不改写这些不可变历史结果，而是读取为：

```text
product_run_type = LEGACY_HYBRID_RUN
schema_version   = alpha-run-result.v1
legacy_hybrid    = true
```

旧结果继续显示 Portfolio、Positions、Trades、Equity、Performance 和 Drawdown，并显示 migration notice。新 Run 不再用 Alpha Evaluation 生成这些 Artifact；需要组合和收益时创建 `RESEARCH_BACKTEST`。

## API 与页面栏目

```http
GET /api/research/runs/<run_id>/result
GET /api/research/runs/<run_id>/sections/<section_key>?offset=0&limit=200
```

结构化 section 单次最多返回 500 行。Overview 和定义类栏目来自冻结合同；大表栏目从不可变 Artifact 分页读取。Run 列表和结果页分别标识 `Factor Evaluation`、`Alpha Evaluation`、`Research Backtest` 和 `Legacy Alpha + Backtest`。

## 2026-08-04 多资产技术验收

- 受控 Research Session：`research_session_5d4e0194cee84d3f8c5089b4a02908c6`
- Project：`project_a55e753390f34f888580880039eed053`
- 最终接受的历史 Hybrid Run：`run_93cf2909364941ba86b866a4e525ff5b`
- 拒绝的技术基线：`run_915b7515a0704cf5bb5648b2138282a4`（缺少 Alpha IC 记录）

冻结对象：

- Universe Snapshot：`universe_snapshot_78075388defa6f1aa29fab57`；
- Factor：`factor_f653dd1ff2694ae1baaa26d8cc3e6ddf`，`crypto_return_24h`，`factor-engine.v3`，Close `pct_change(24h)`；
- Alpha：`alpha_72ce3d4cca98429cb959bf0770afc083`，`crypto_positive_momentum_rotation_alpha`；
- Portfolio：Top-1、equal weight、long-only、every signal、`minimum_score=0`；
- Execution：initial cash `$10,000`、fee `2 bps`、slippage `10 bps`、next-bar open。

样本使用 Binance Spot 的 BTCUSDT、ETHUSDT、BNBUSDT、SOLUSDT，频率 1h，research period 为 `2026-04-12 00:00` 至 `2026-07-09 23:00 UTC`；系统自动向前扩展 24h warmup，并把输出切回研究区间。

验证结果：

- 8,544 Signals、8,544 Targets、2,136 Target timestamps；
- 2,135 个可执行 rebalances，其中 1,357 个 INVESTED、778 个 FLAT；最后一个 Signal 因没有下一根 Bar 被跳过；
- 485 笔 Trades：243 BUY、242 SELL，四个 instrument 均实际成交；
- 77 个 SELL 直接由 FLAT Target 触发，验证负信号退出语义；
- fees `768.0040576309844`、slippage cost `3840.014896173658`，与逐笔成交汇总一致；
- final equity `6474.054067705456`、total return `-35.259459%`、Sharpe `-3.999238`、max drawdown `-40.584006%`；
- 24h Alpha IC mean `0.025185`、Rank IC mean `0.023769`、Rank IC t-stat `1.7830`；1h 与 6h 结果接近 0；
- diagnostics 为空；UI 已显示 IC/Rank IC、Trades、Rebalances、Flat Targets、Fees、Slippage、Equity 和 Drawdown。

该 Run 证明了多资产数据、Signal、显式退出、下一根 Open、交易会计、成本和结果展示链路；它不证明该 Alpha 具有可部署价值。由于预测边际弱、换手和成本高、成本后收益为负，研究结论为“不进入 Strategy”。

Alpha 预测性统计：

| Horizon | IC Mean | Rank IC Mean | Rank IC t-stat | Top-Bottom Spread |
|---:|---:|---:|---:|---:|
| 1h | -0.004217 | -0.003653 | -0.2814 | -0.0000075 |
| 6h | 0.001017 | -0.004695 | -0.3526 | 0.0003953 |
| 24h | 0.025185 | 0.023769 | 1.7830 | 0.0016887 |

回归验收：

```text
Unit tests            215 PASS
Integration tests      19 PASS
Failure injection       1 PASS
JavaScript syntax       PASS
Research UI result      PASS
```

## 安全边界

- Factor/Alpha/Backtest Run 都只消费冻结数据，不在执行中联网补数；
- Research Artifact 命中缓存或被复用时，身份与依赖仍必须完整可追溯；
- Research Backtest 是本地历史回放，不批准策略，也不执行 Virtual 或 Live 交易；
- 从 Research 跨入 Strategy，必须由用户明确提出；从 Strategy 跨入 Paper/Live，继续遵循人工确认和执行权限边界。
