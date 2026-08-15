# Universe Experiment

Use only when the active Contract has `stop_at=UNIVERSE` and
`run_type=UNIVERSE_DESIGN`. For explicit Universe backend design or repair,
leave the Researcher route and use an engineering workflow.

## Contents

- Product contract and five Dynamic modules
- Capability gate and current executable subset
- Universe Design Candidate semantics
- Frozen dynamic membership
- Completion language

## Product Contract

Present only three Universe v2 product types:

```text
STATIC      explicit Instrument members
DYNAMIC     Base → Filter → Rank → Select → Rebalance
COMPOSITE   Union / Intersection / Difference of Universes
```

Do not invent `MARKET_CAP_UNIVERSE`, `LIQUIDITY_UNIVERSE`,
`TOP_N_UNIVERSE`, or arbitrary expression types. Treat legacy
`benchmark_set` and `multi_leg_set` as compatibility objects, not new
Universe v2 authoring choices. Benchmark weighting belongs to BenchmarkSpec;
Pair/Multi-leg semantics belong to a separate group or strategy object.

A Dynamic definition has exactly five user-facing modules:

```yaml
type: DYNAMIC
base:
  ref: equity:CRSP:ALL
filters:
  - [security_type, "=", COMMON_STOCK]
  - [listing_age_days, ">=", 365]
  - [market_cap_usd, ">=", 300000000]
rank:
  field: market_cap_usd
  order: DESC
select:
  method: TOP_N
  value: 1500
rebalance: MONTHLY
```

Filters are AND-only. Use a Composite Union for OR semantics. Do not embed SQL,
Provider queries, Factor formulas, or nested Boolean expressions in a Universe.

## Capability Gate

Read `universe_capabilities` from the researcher capability response before
interpreting a request. Keep these claims separate:

- `field_registry`: fields the v2 compiler can validate and compile.
- `field_execution_status`: whether each field is wired into the current
  formal pipeline.
- `dynamic_point_in_time_filters`: PIT rules the current formal pipeline can
  actually materialize.
- `selection_methods`: methods currently accepted by the formal Researcher
  pipeline.
- `authoring_selection_methods`: methods accepted by the v2 compiler/preview,
  not proof of formal execution support.

Currently, formal Researcher selection remains `ALL_ELIGIBLE`.
`TOP_N`, `BOTTOM_N`, `PERCENTILE`, and Buffer are valid v2 authoring
contracts but must not be reported as formally evaluated until a Frozen
Universe Evaluation path supports them. A field marked
`REGISTERED_NOT_YET_BOUND` is not executable research evidence.

## Universe Design Candidate

A standalone Universe Experiment tests one falsifiable claim about the economic
population available to later research. Candidate changes belong in
`universe_selection`: explicit instrument scope, identity eligibility,
`ALL_ELIGIBLE` selection, or explicit exclusions. Keep period and frequency
fixed.

Identity eligibility includes security type, exchange, share type, listing age,
and listing/delisting validity. These rules define the PIT candidate pool.
Field eligibility changes membership inside that pool.

The Result may report immutable identity membership, eligible count, coverage,
selection evidence, and PIT/survivorship diagnostics. It creates no formal
Factor/Alpha/Backtest Run and must not claim predictive or portfolio
performance.

## Frozen Dynamic Membership

When a Dynamic Universe feeds a Factor, Alpha, or Portfolio Evidence study, the
backend must:

```text
Definition
→ Compile Field Contracts and Requirements
→ Freeze Manifest IDs and exact decision/effective schedule
→ resolve only values with available_time <= decision_time
→ Filter → Rank → Select
→ materialize Membership Timeline and compressed segments
→ freeze Snapshot / Bundle fingerprint
```

Missing field values are excluded. Never zero-fill them or substitute current
values, a full-period mean, or a full-period median. Universe Rebalance means
membership reconstitution; it is not Portfolio rebalance. A close-time decision
must not imply same-close execution.

The formal equity PIT fields are `market_cap_usd`, `roe_ttm`, `pe_ttm`,
`pb_mrq`, and `fcf_yield_ttm`, all with `LATEST_AVAILABLE` semantics and
`EXCLUDE` for any missing required input. Their code-owned formulas are:

```text
roe_ttm       = net_income_ttm / latest reported equity; equity > 0
pe_ttm        = market_cap_usd / net_income_ttm; net_income_ttm > 0
pb_mrq        = market_cap_usd / latest reported equity; equity > 0
fcf_yield_ttm = (operating_cash_flow_ttm - capex_ttm) / market_cap_usd
```

Ratio thresholds are decimals (`0.15` means 15%). Market cap filters freeze
`equity_valuation_daily`; ratio contracts also freeze the exact required
`fundamentals_pit` fields, and PE/PB/FCF Yield additionally freeze valuation
when their formula needs market cap. Use explicit rules rather than inventing
a `fundamentals_good` field. TTM requirements freeze an 18-month pre-start SEC
warmup window so four discrete quarters can exist at the first decision; this
does not expand the evaluation period:

```yaml
eligibility:
  mode: HISTORICAL_EQUITY_PIT
  security_types: [COMMON_STOCK]
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

A standalone `UNIVERSE_DESIGN` product currently cannot pin the required
Manifests. Reject a PIT-field Candidate with
`DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION`. Shared Universe authoring may
return `REQUIRES_FROZEN_DATA`; that means the rule compiled, not that
membership resolved. Never fall back to a static or all-eligible result.

## Completion Language

Use the highest truthful state:

```text
AUTHORED
COMPILED
REQUIRES_FROZEN_DATA
FROZEN_EVALUATED
BOUND
```

Only `FROZEN_EVALUATED` or `BOUND` can support a claim about dynamic
membership. Report the definition schema, compiled fingerprint when present,
frozen evidence state, Membership Timeline/Snapshot IDs when present, coverage,
missing exclusions, and warnings.

Raw archive start does not imply the study is runnable. Distinguish raw archive,
READY Catalog, warmup, PIT-field, and final evaluation coverage.
