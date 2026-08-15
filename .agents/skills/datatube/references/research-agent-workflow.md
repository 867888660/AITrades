# Research Agent Workflow

Read [RESEARCH_PROGRAM.md](RESEARCH_PROGRAM.md) first. This workflow exposes
research meaning only. Internal DataTube IR and engineering operations are not
part of the Research Agent's tool surface.

## Interface

```text
ALIGN(goal)
REVIEW_AND_CONFIRM(AlignedResearchIntent)
START(AlignedResearchIntent)
RESUME(anchor + AlignedResearchIntent)
STATUS(session_id)
EXPERIMENT(session_id, CandidateSpec)
RESULT(experiment_id)
DECIDE(experiment_id, KEEP | REJECT | INCONCLUSIVE, Learning)
```

Use only these commands for a normal research project:

```powershell
python scripts/datatube_client.py researcher-align --data alignment.json
python scripts/datatube_client.py researcher-start --data start.json
python scripts/datatube_client.py researcher-resume RUN run_123 --data alignment.json
python scripts/datatube_client.py researcher-status <session_id>
python scripts/datatube_client.py researcher-pause <session_id>
python scripts/datatube_client.py researcher-continue <session_id>
python scripts/datatube_client.py researcher-answer <session_id> "answer"
python scripts/datatube_client.py researcher-experiment <session_id> --data candidate.json
python scripts/datatube_client.py researcher-result <experiment_id>
python scripts/datatube_client.py researcher-decide <experiment_id> --data decision.json
```

Preflight only with:

```powershell
python scripts/datatube_client.py capabilities --section researcher
```

RESUME accepts only an existing anchor. `RESEARCH_RESUME_ANCHOR_NOT_FOUND` is a
terminal request error and must not create a Session. Ask the user only when the
response contains multiple concrete Project candidates. A zero-candidate
`NEED_HUMAN` state is stale/invalid and must not be answered.

When browsing Library candidates with requested asset-class or frequency,
select only `COMPATIBLE` items. `UNKNOWN` means required metadata is missing;
do not treat it as compatible or select it automatically.

Do not use the legacy Project, Universe, Requirement, Preview, Run, worker, or
inspection commands from this workflow. They are internal/engineering surfaces.

After START, load only the experiment reference selected by the Contract:

```text
stop_at=FACTOR → research-factor-experiment.md
stop_at=ALPHA  → research-alpha-experiment.md
stop_at=UNIVERSE → research-universe-experiment.md
stop_at=PORTFOLIO_EVIDENCE → research-portfolio-evidence.md
Result exists  → research-iterate.md
explicit strategy request → research-strategy-handoff.md, then Strategy workflow
```

## ALIGN, Review, Confirm, Then START

The user's first sentence may be a goal rather than a complete research design.
Before START, resolve material ambiguity as a researcher and submit the semantic
plan to `researcher-align`. Keep inference and recommendations efficient: ask
only about material ambiguity and ask one concise question at a time.

When ALIGN returns `status=READY`, do not START yet. `READY` means that the plan
is semantically complete enough for review; it is not user authorization.
Before START or RESUME:

1. Show the complete normalized Alignment and every additional semantic value
   that will be frozen into the Research Contract.
2. Clearly label user-provided, researcher-recommended, system-fixed, and
   inherited values where known.
3. Give the user a direct opportunity to change any item.
4. Ask for explicit confirmation of that exact plan.
5. Preserve and send the reviewed `alignment_hash` only after confirmation.

The original research request, backend `READY`, silence, or agreement given
before the complete review is not confirmation. If the user changes any value,
rerun ALIGN, show the revised complete plan, and request confirmation again.

For example, if the user says “研究美股中期动量”, recommend a research boundary:

> 建议先研究 2000 年至今的美国普通股，使用历史时点有效的股票资格，
> 排除上市不足 12 个月的股票；第一阶段先评价 12-1 动量因子的预测能力。
> 你希望研究广泛市场，还是只研究大盘股？

Do not ask for Provider, Manifest, Requirement, warmup, or worker choices.

Example START payload, sent only after the user confirms the complete reviewed
plan:

```json
{
  "objective": "验证美国股票中期动量是否具有稳定预测能力",
  "aligned_research_intent": {
    "question": "验证美国股票中期动量是否具有稳定预测能力",
    "decision_supported": "判断该因子是否值得进入 Alpha 研究",
    "stop_at": "FACTOR",
    "evidence_profile": "STANDARD",
    "out_of_scope": ["Alpha construction", "portfolio backtest", "strategy creation"]
  },
  "instrument_scope": ["AAPL", "MSFT", "NVDA"],
  "frequency": "1d",
  "research_period": {"start": "2000-01-01", "end": "2025-12-31"},
  "universe_policy": {
    "eligibility": {
      "mode": "STATIC_LIST",
      "instrument_scope": ["AAPL", "MSFT", "NVDA"]
    },
    "selection": {"method": "ALL_ELIGIBLE"},
    "exclusions": []
  },
  "evidence_profile": "STANDARD",
  "research_contract": {
    "evaluation": {"primary_metric": "rank_ic", "decision_rule": {"minimum_rank_ic": 0.02}}
  },
  "idempotency_key": "us-medium-term-momentum-v1"
}
```

If ALIGN returns `NEEDS_INPUT`, discuss only its one material question and then
rerun ALIGN. If it returns `UNSUPPORTED`, stop; never substitute another
research product. If it returns `READY`, perform the review-and-confirm gate;
never interpret `READY` itself as permission to run.

## Research Contract

The active Contract freezes:

- objective and asset scope
- research period and frequency
- Universe eligibility/selection meaning
- constraints
- product type, primary metric, baseline, and decision rule
- experiment budget and validation protocol

Do not silently change it after a disappointing result. A material change starts
a new Contract version and comparison lane.

## CandidateSpec

Each Experiment must contain one falsifiable hypothesis, an intervention set,
and controlled variables. A Factor Evaluation declares one Factor. An Alpha or
Portfolio Evidence Experiment may declare multiple Factors plus an explicit
component mapping; DataTube binds the internal definition identities.

```json
{
  "idempotency_key": "momentum-12-1-v1",
  "candidate": {
    "hypothesis": {
      "statement": "过去 12 个月收益剔除最近 1 个月后，对下个月收益具有正向预测能力",
      "expected_direction": "POSITIVE"
    },
    "intervention_set": [
      {"component": "factor", "change": "introduce 12-1 momentum"}
    ],
    "controlled_variables": ["universe", "research_period", "frequency"],
    "factor": {
      "name": "momentum_12_1",
      "operator": "pct_change",
      "input_field": "close",
      "window": 252,
      "frequency": "1d",
      "output_direction": "HIGHER_IS_BETTER"
    },
    "evaluation": {
      "run_type": "FACTOR_EVALUATION",
      "primary_metric": "rank_ic",
      "horizons": [1, 5, 20]
    }
  }
}
```

CandidateSpec must never contain Provider, Manifest, RequirementSet, Preview,
Bundle, worker, PIT implementation, or data-source fields. DataTube rejects such
fields before execution.

Alpha example:

```json
{
  "hypothesis": "Ranking medium-term momentum predicts next-period returns",
  "intervention_set": [{"component": "alpha", "change": "rank momentum"}],
  "controlled_variables": ["universe", "research_period", "frequency"],
  "factor": {
    "name": "momentum_12_1",
    "operator": "pct_change",
    "input_field": "close",
    "window": 252
  },
  "alpha": {
    "name": "momentum_rank_alpha",
    "weight": 1.0,
    "transform": "CS_RANK"
  },
  "evaluation": {
    "run_type": "ALPHA_EVALUATION",
    "primary_metric": "rank_ic"
  }
}
```

The Candidate run type and primary metric must match the active Contract. A
Candidate cannot silently turn Factor research into Alpha research or select a
new primary metric after results are observed.

## Native Factor Packs

A Factor Pack is one named research object, not 157 hand-authored Candidate
fields. The current native pack is:

```text
pack_id: qlib.alpha158_without_vwap
display_name: Qlib Alpha158-compatible (VWAP excluded)
factor_count: 157
is_standard_alpha158: false
```

Mentioning Alpha158 in the START objective freezes this exact identity into the
Contract. Submit only its semantic identity in the Candidate:

```json
{
  "hypothesis": "The no-VWAP Alpha158 pack contains stable predictive factors",
  "intervention_set": [{"component": "factor_pack", "change": "evaluate the native pack"}],
  "controlled_variables": ["universe", "research_period", "frequency"],
  "factor_pack": {"pack_id": "qlib.alpha158_without_vwap"},
  "evaluation": {
    "run_type": "FACTOR_EVALUATION",
    "primary_metric": "rank_ic",
    "horizons": [1, 5, 20]
  }
}
```

Do not ask for Qlib runtime, cache, Manifest, OHLCV fields, or warmup. DataTube
derives and freezes those details. The Result reports pack-level metric
distribution and leading/lagging members; it does not return 157 infrastructure
objects. A single Factor cannot replace a Contract-frozen Factor Pack.

## Experiment Lifecycle

Public states are:

```text
ACCEPTED | COMPILING | PREPARING_DATA | QUEUED | RUNNING | EVALUATING
| COMPLETE | INVALID | SYSTEM_BLOCKED | FAILED | CANCELLED
```

`PREPARING_DATA` is normal backend progress. Do not inspect queues or start a
worker. Poll STATUS/RESULT while DataTube compiles, prepares, validates, freezes,
and runs the Experiment automatically.

For an explicit engineering diagnosis, every persisted `PREPARING_DATA` state
must correspond to active scoped preparation or a short post-commit readiness
check. Provider workers must drain independently of global Requirement scans,
and Experiment advancement must be isolated so a large universe cannot block
unrelated Experiments.

`INVALID` means the Candidate did not satisfy the research contract or supported
research language. Revise the research hypothesis/specification.

`SYSTEM_BLOCKED` means no valid research result exists. Report the public issue
and stop; do not diagnose or repair infrastructure. It does not prove a global
outage. If the user separately requests engineering diagnosis, inspect the
reported issue and verify that maintenance is scoped to the affected Research
or Session before making any system-wide claim.

## ResearchResult

The stable envelope contains:

```text
status
product_type
goal_conformance
decision_metrics
research_diagnostics
gates
comparison
warnings
provenance.reproducible
```

Product evidence remains typed. Factor Evaluation contains Factor evidence;
Alpha Evaluation must never claim portfolio returns; Research Backtest owns
positions, costs, equity, Sharpe, and drawdown.

The researcher facade supports `UNIVERSE_DESIGN`, `FACTOR_EVALUATION`,
`ALPHA_EVALUATION`, and `RESEARCH_BACKTEST`. Portfolio Evidence requires
explicit portfolio, execution-cost, and benchmark assumptions and stops before
Strategy creation.

## Decision and Learning

Only a `COMPLETE` Experiment can receive a research decision:

```json
{
  "decision": "KEEP",
  "learning": {
    "summary": "12-1 momentum passes the primary Rank IC rule but weakens in the latest regime.",
    "next_hypothesis": "Test whether excluding the highest-volatility names improves regime stability."
  }
}
```

`SYSTEM_BLOCKED`, `FAILED`, and `INVALID` are statuses, not research decisions.

## Closeout

Report the research goal, Contract version, completed Experiments, decisions,
primary evidence, research warnings, remaining experiment capacity, and next
hypothesis. For research-only work always state:

```text
No strategy was created or submitted. No virtual or live trade was executed.
```
