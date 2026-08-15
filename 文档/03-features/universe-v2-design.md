# Universe v2：三类产品模型与五阶段动态规则

## 1. 目标

Universe v2 将用户和 Agent 面向的模型收敛为三种类型：

```text
STATIC      明确 Instrument 列表
DYNAMIC     Base → Filter → Rank → Select → Rebalance
COMPOSITE   对已存在 Universe 做 Union / Intersection / Difference
```

不增加 `MARKET_CAP_UNIVERSE`、`LIQUIDITY_UNIVERSE`、`TOP_N_UNIVERSE` 等按业务字段命名的类型。复杂度由版本化的 Field Registry 承担，而不是进入 Universe DSL。

当前 `benchmark_set` 和 `multi_leg_set` 作为兼容类型继续可读。新产品模型中，Benchmark 权重属于 `BenchmarkSpec`，Pair/Multi-leg 属于独立的组合定义，不再扩展 Universe 类型。

### 1.1 当前交付状态

截至 2026-08-14，已完成的是安全基础层：

| 能力 | 状态 | 实现 |
| --- | --- | --- |
| 三类型和五模块 Schema | 已完成 | `UniverseV2Compiler` |
| Field Registry 与别名 | 已完成 | `UniverseFieldRegistry` |
| Operator、Select、Buffer、Schedule 校验 | 已完成 | `UniverseV2Compiler` |
| Requirement/Warmup 编译 | 已完成 | `UniverseV2Compiler.compile` |
| Frozen rows 的 PIT Membership Timeline | 已完成 | `UniverseMembershipEngine` |
| 正式市值/基本面 PIT Filter 绑定 | 已完成 | `UniverseService` + Frozen Manifest |
| Shared Universe Dynamic Preview | 已完成 | 返回 `REQUIRES_FROZEN_DATA` |
| Agent Capability 暴露 | 已完成 | `universe_capabilities` |
| Shared Dynamic 持久化和 Binding | 未接通 | 等待 Formal Universe Evaluation |
| 正式 `UNIVERSE_EVALUATION` Run | 未接通 | 后续阶段 |
| Composite Timeline 执行 | 未接通 | 后续阶段 |

当前能力声明必须按以下方式理解：

- `field_registry` 表示编译器认识该字段，不等于 Formal Pipeline 已经能计算它；
- `field_execution_status=FORMAL_PIPELINE` 才表示已经接入正式执行；
- `REGISTERED_NOT_YET_BOUND` 只能用于定义检查和 Requirement 规划；
- 当前 Formal Researcher 的 `selection_methods` 仍是 `ALL_ELIGIBLE`；
- `TOP_N / BOTTOM_N / PERCENTILE / Buffer` 当前属于 `authoring_selection_methods`；
- 当前已接入 Formal Pipeline 的动态字段是 `market_cap_usd`、`roe_ttm`、
  `pe_ttm`、`pb_mrq`、`fcf_yield_ttm`。

因此，“定义合法”“编译成功”“需要冻结数据”“冻结评估完成”“已绑定到 Research”是五种不同状态，不得合并描述。

## 2. 外部定义

```json
{
  "schema_version": "universe-definition.v2",
  "type": "DYNAMIC",
  "base": {"ref": "equity:CRSP:ALL"},
  "filters": [
    ["security_type", "=", "COMMON_STOCK"],
    ["listing_age_days", ">=", 365],
    ["price_usd", ">=", 5],
    ["market_cap_usd", ">=", 300000000],
    ["adv20_usd", ">=", 5000000]
  ],
  "rank": {"field": "market_cap_usd", "order": "DESC"},
  "select": {
    "method": "TOP_N",
    "value": 1500,
    "buffer": {"entry": 1400, "exit": 1600}
  },
  "rebalance": "MONTHLY"
}
```

Filter 数组是作者层简写。编译器会把它规范化为显式对象；所有 Filter 使用固定的 AND 语义。需要 OR 时，通过多个 Universe 的 `UNION` 表达，不增加嵌套布尔表达式。

## 3. Field Registry

每个可用字段必须注册以下合同：

- 稳定 `field_id` 和 `contract_version`；
- value type、unit、source unit 和单位转换；
- Filter/Rank 能力及 Operator 白名单；
- Asset Class、Data Type、Frequency；
- 源字段、lookback、warmup 和派生计算版本；
- `event_time` / `available_time` 与 PIT Policy；
- Missing Policy；
- 实际覆盖率由 Catalog/Manifest 状态动态补充。

Universe 定义只能引用注册字段，不接受 SQL、任意公式或 Provider 查询表达式。新增字段需要代码审核、Requirement 合同和 PIT 测试。

### 3.1 已绑定的美股市值与基本面字段

“基本面很好”不是一个系统字段。研究者必须把它拆成可复核的阈值，系统再按固定
Field Contract 执行。当前正式口径为：

```text
market_cap_usd = CRSP market_cap × 1000（源单位 USD_THOUSANDS）
roe_ttm        = net_income_ttm / 最新已披露股东权益；equity > 0
pe_ttm         = market_cap_usd / net_income_ttm；net_income_ttm > 0
pb_mrq         = market_cap_usd / 最新已披露股东权益；equity > 0
fcf_yield_ttm  = (operating_cash_flow_ttm - capex_ttm) / market_cap_usd
```

比例字段使用小数（`0.15` 表示 15%）。`roe_ttm` 冻结 SEC
`fundamentals_pit`；PE、PB、FCF Yield 会同时冻结其所需的 SEC 基本面和
`equity_valuation_daily`。每次成员资格变化都由 `available_time` 驱动；决策时点之后
才披露的报表不得回灌。任一必要输入缺失、非有限，或 PE/PB 分母不为正时，该证券
按 `EXCLUDE` 处理，不做零填充、横截面填充或当前值回填。
TTM Requirement 会在研究起点前冻结 18 个月的 SEC 披露预热窗口，以便在第一个
决策时点构造四个离散季度；预热数据不扩大正式评价区间。

例如“广泛市场 + 显式基本面条件”可以写成：

```yaml
point_in_time_filters:
  - field: market_cap_usd
    minimum: 300000000
  - field: roe_ttm
    minimum: 0.15
  - field: pe_ttm
    maximum: 25
  - field: fcf_yield_ttm
    minimum: 0.03
```

这些阈值只是定义示例，不代表系统替用户确认了“基本面很好”的研究含义。

## 4. 编译合同

`UniverseV2Compiler` 负责：

1. 将别名和简写规范化；
2. 验证字段、Operator、Rank、Select、Buffer 与 Rebalance；
3. 编译 Data Requirements 与最大 Warmup；
4. 将 Rebalance 编译为内部 Membership Schedule Profile；
5. 固定 Missing、As-of、Tie-break 等系统策略；
6. 生成确定性 Fingerprint。

默认系统策略：

```text
Filter join       AND
Missing           EXCLUDE
As-of             LATEST_AVAILABLE
Tie break         instrument_id ASC
Hard invalid      immediate removal
```

Universe 的 Rebalance 表示成员重构，不等于 Portfolio 的交易再平衡。内部使用 `membership_schedule` 命名。对于股票，默认 Profile 是 Session Close 决策、Next Session Open 生效；对于 24×7 资产使用 UTC 日界。正式执行必须将 Profile 展开成明确的 `decision_time/effective_time` 时间点。

## 5. 冻结执行与 Membership Timeline

正式链路：

```text
Definition
→ Compile
→ RequirementSet
→ Frozen Manifest Binding
→ PIT Field Resolution
→ Filter → Rank → Select
→ Membership Timeline
→ Immutable Snapshot / Bundle
```

`UniverseMembershipEngine` 是纯 PIT 执行器：调用方必须传入冻结后的字段行、Manifest IDs、Base Membership 与明确 Schedule。引擎不会发现 Live 数据，也不会用当前值、全期均值或中位数替代缺失历史值。

每个决策时点只允许使用：

```text
available_time <= decision_time
```

结果冻结：

- 每期 Base、Eligible、Selected 数量；
- Filter 漏斗和 Missing 计数；
- 各期成员列表；
- 压缩后的 Membership Segments；
- Field/Compiler/Engine 版本；
- Frozen Manifest IDs；
- 完整结果 Fingerprint。

Buffer 是 Select 的高级参数。对于 `TOP_N`，先保留仍在 Exit 阈值内的旧成员，再允许进入 Entry 阈值的新成员，最后按确定性 Rank 补齐或裁剪到目标数量。首次重构不使用历史成员状态。

## 6. Shared Universe 兼容边界

Shared Universe 现在可以解析和 Preview `DYNAMIC` 定义，并返回编译合同与 `REQUIRES_FROZEN_DATA`。在尚未提供冻结 Manifest 证据时，它不能创建兼容性的 `STATIC_LIST` Snapshot，也不能持久化为已解析 Universe。错误码为：

```text
DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION
```

这是安全边界，不是功能降级。旧 Shared Universe 的静态兼容桥不具备表达动态 Membership Timeline 的能力；在 Formal Universe Evaluation 接通前，拒绝写入比保存错误静态名单更安全。

## 7. 后续接入顺序

1. 增加正式 `UNIVERSE_EVALUATION` 产品，冻结 Bundle 并生成 Membership Timeline Artifact。
2. Shared Universe Revision 保存规则；Project Binding 固定 Revision，而不是只跟随稳定 ID。
3. Formal v2 Factor/Alpha/Research Backtest 统一消费动态 Snapshot/Timeline。
4. Composite 在相同有效时间轴上组合子 Timeline，并固定子 Revision。
5. 旧 `STATIC_LIST`、`TOP_N_BY_TURNOVER`、`HISTORICAL_EQUITY_PIT` 保持只读重放；新建统一写 v2。

不得重写历史 Definition、Snapshot、Bundle、Run、Artifact、Audit 或 Fingerprint。

## 8. 实现与验证索引

- `services/data_platform/universe_v2.py`：Schema、Field Registry、Compiler、Membership Engine 与 Capability。
- `services/data_platform/shared_universe_service.py`：Dynamic authoring/preview 和冻结门禁。
- `services/agent_interface_service.py`：Researcher Capability 接入。
- `tests/unit/test_universe_v2.py`：Requirement 编译、未来数据隔离、Missing 排除、确定性 Tie-break、Buffer、Fingerprint 与 Shared Preview 测试。
- `tests/unit/test_universe.py`：正式市值/基本面 Manifest 绑定、TTM 披露门禁、未来数据隔离与缺失排除测试。
- `.agents/skills/datatube/references/research-universe-experiment.md`：Agent Universe 工作流与完成语义。
- `skills/datatube/references/research-universe-experiment.md`：可发布 Skill 副本。
