# Agentic leave path, live (first run)

Run 2026-11-15 23:25 UTC at `99399d15417a`, clean tree, on the agents approved at that commit:
leave-supervisor-v1 (0.1.0), leave-intake-v1 (0.1.0) and leave-explainer-v2 (0.2.0), all on
`claude-haiku-5-5`. Command: `uv run python -m hall_monitor.hr.evals --out evals/results/hr-agentic-live`.

28 synthetic leave messages went through the whole agentic pipeline: the leave supervisor choosing
the intake agent, the FMLA rules in code, the route in code, and the explanation agent. Intake is
scored field by field against the structured request each message describes; the end-to-end
result against the answer key.

| Measure | Result |
|---|---|
| Requests | 28 |
| Intake field accuracy | 100% (28 of 28 exact) |
| Route accuracy (route and the role named to decide) | 100% |
| Explanations kept | 28 |
| Explanation failures | 0 |
| Errors or stops | 0 |
| Cost | 0.0258 USD in total, under 0.001 USD per request |

## Limits

- One run. No repeat runs, so there is no measure of run-to-run variation yet.
- 28 short synthetic messages, written by one person, scored against answer keys the same person
  wrote. Real leave requests are longer, messier and come with attachments.
- No decision is signed here. The sign-off rules (HM-23) are tested offline, not in this run.
- The rules and roles are this project's reading of federal FMLA; no HR professional or lawyer
  has reviewed them, and state leave laws are only flagged, not applied.
- The claims and complaint agents have no live run on their current versions; this is the only
  live result on agents approved now.
