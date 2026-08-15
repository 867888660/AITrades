# Research Alignment

Alignment determines what the study must answer before any research Session or
Experiment is created. It does not create a Factor, Alpha, portfolio, Strategy,
Project, data request, or execution object.

## Alignment Model

Resolve only five meanings:

```text
QUESTION  What exact research question must be answered?
STOP_AT   Where must this study stop?
BASE      Is it new work or a continuation/reuse of prior research?
SCOPE     What economic population, period, frequency, and Universe apply?
EVIDENCE  What predeclared evidence would answer the question?
```

`STOP_AT` is one of:

```text
UNIVERSE | FACTOR | ALPHA | PORTFOLIO_EVIDENCE
```

`Existing Research` is not a level. Represent it as `entry_mode=RESUME` plus an
anchor. Library reuse is `entry_mode=START` with semantic `base_refs`.

The researcher facade executes all four stopping points as distinct products.
Never downgrade Universe Design into Factor Evaluation, Portfolio Evidence into
Alpha Evaluation, or any research product into Strategy creation.

## Conversation Rule

Use inference to reduce configuration burden, not to bypass user control. Infer
values already expressed by the user and recommend sensible routine research
choices. Ask only when two interpretations would materially change the research
claim. Ask one concise question at a time, give one recommended answer and at
most one materially different alternative.

An inferred or researcher-recommended value is a proposal, not user consent.
The initial request to research, the absence of material ambiguity, and backend
`status=READY` do not authorize START or RESUME.

After all material ambiguity is resolved:

1. Submit the semantic plan to `researcher-align`.
2. Treat the returned normalized Alignment as the source of truth.
3. Present one complete pre-start review to the user.
4. Invite the user to modify any value, including inferred routine choices.
5. Require an explicit confirmation made after that review.
6. Only then call START or RESUME with the exact reviewed Alignment and its
   `alignment_hash`.

Do not turn clarification into a configuration form. The researcher should do
the design work, ask only material questions, and then make every conclusion-
affecting choice visible once before execution.

Do not ask about Provider, Manifest, Requirement, data preparation, warmup,
Bundle, Preview, worker, queue, or source implementation.

## Pre-Start Review

The review must show the complete research plan that will be frozen, not only
the fields that required clarification. Include every applicable item below:

```text
QUESTION AND DECISION     question, decision supported
PRODUCT AND ENTRY         STOP_AT, START or RESUME, anchor/base references
ASSET AND UNIVERSE        asset class, instruments/economic population,
                          eligibility, selection, exclusions, reconstitution
SAMPLE                    research period and frequency
EVIDENCE                  profile, primary metric, decision rule, baseline,
                          validation protocol and horizons
BOUNDARIES                constraints, experiment limit, assumptions,
                          explicit out-of-scope work
```

Where known, label values as `USER`, `RESEARCHER_RECOMMENDED`, `SYSTEM_FIXED`,
or `INHERITED`. Do not call a researcher recommendation a default. If a
contract-defining field is empty, show it as `NOT_SPECIFIED` or
`NOT_APPLICABLE`; do not silently omit it.

Use the normalized ALIGN response for all fields it returns. Also show any
additional semantic values that will be sent in START or RESUME and frozen into
the Contract. The user must see the actual values, not a shorter paraphrase that
could differ from the payload.

Prefer a concise research card over raw JSON. A suitable shape is:

```text
Research Alignment

Research question
  Question / decision supported / stopping point

Sample and Universe
  Asset scope / eligibility and selection / period / frequency

Evidence and decision
  Evidence profile / primary metric / decision rule / baseline / validation

Boundaries
  Constraints / experiment limit / assumptions / out of scope

For each value: final value + USER, RESEARCHER_RECOMMENDED, SYSTEM_FIXED,
or INHERITED
```

Do not respond with only `READY`, an `alignment_hash`, or a JSON dump. Keep the
card readable, but never shorten it by hiding a contract-defining value.

End the review with a direct choice, for example:

> You may change any item above. If the plan is correct, reply "confirm and
> start".

Silence, a previous general request to research, or an acknowledgement made
before the complete review is not confirmation. If the user changes any item,
rerun ALIGN, show the revised complete plan, and request confirmation again.

## Evidence Profiles

```text
QUICK     screening evidence; not a promotion decision
STANDARD  normal primary test plus predefined robustness
DEEP      stronger validation protocol for a promotion decision
```

Evidence depth never changes `STOP_AT`. Factor evidence cannot use Sharpe or
drawdown; Alpha evidence cannot claim portfolio performance.

## Interface

Validate an alignment without creating research objects:

```powershell
python scripts/datatube_client.py researcher-align --data alignment.json
```

When Library reuse is material, search only the researcher-facing semantic
cards and place chosen `asset_ref` values in `base_refs`:

```powershell
python scripts/datatube_client.py researcher-library --kind FACTOR --q momentum --asset-class US_EQUITY --frequency 1d
```

Do not use Definition or Project APIs to discover reusable research assets.

Backend `status=READY`, `route_available=true`, and an empty
`unresolved_material_question` mean that the plan is ready for user review.
They do not mean that research is authorized. Preserve the returned
`alignment_hash`, show the exact normalized plan, and proceed to START or RESUME
only after the user explicitly confirms that reviewed version.

Alignment is complete when the question, stopping point, material Universe and
scope, evidence profile, primary metric, and explicit out-of-scope boundary are
clear. Alignment is authorized for execution only after the user has seen and
explicitly confirmed the complete pre-start review. This confirmation is always
required for START and RESUME, even when no material ambiguity remains.
