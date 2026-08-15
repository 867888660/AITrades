# Research Iteration

Use after a ResearchResult exists. Interpret evidence against the predeclared
primary metric and decision rule; do not select a new success metric after
seeing the result.

```text
COMPLETE       → KEEP | REJECT | INCONCLUSIVE + Learning
INVALID        → revise the Candidate within the same Contract
SYSTEM_BLOCKED → no research conclusion; report issue_id and stop
FAILED         → no research conclusion
```

Every Learning states what changed in belief, important warnings, and at most
one next hypothesis. A Universe, product type, primary metric, or material scope
change requires a new Contract version. Do not automatically advance from
Factor to Alpha or from Alpha to Portfolio Evidence.
