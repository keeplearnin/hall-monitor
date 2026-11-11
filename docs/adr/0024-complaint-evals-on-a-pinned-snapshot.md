# ADR 0024: Complaint evals run on a pinned snapshot, scored against a hand-written label key

- **Status:** Accepted (complaint desk, fourth slice)
- **Control:** [HM-28](../controls.md#hm-28)

## Context

Live data changes every day, so an eval run against it can't be repeated or gated. CFPB
publishes no answer key for triage: its company response and timeliness say how the company
answered, not how urgent the complaint was. And review 16 showed the danger of scoring routing
against itself: the only test over real labels defined "must route" by the patterns it was
meant to check, and passed while 72 of 500 complaints were missed.

## Decision

- **The pinned snapshot is the eval set.** `data/cfpb/snapshots/2026-10-01` (500 real complaints,
  minimised, its manifest hash in code), so every result can be reproduced offline.
- **A hand-written answer key, kept as data.** `data/cfpb/label_reasons.yaml` maps each CFPB
  label that calls for specialist review to its reason, read by hand. The eval and the tests
  score routing against it and the tags, never against the routing patterns.
- **What is gated.** On the scripted run, in CI: no must-route complaint missed, below high
  urgency or missing its reason; every untimely complaint to specialist review; no errors; no
  stored draft with a delivery path; zero counterfactual flips. The live run gets the same
  thresholds when it exists.
- **Fairness, two ways.** The counterfactual check is gated: the same complaint from other
  states and ZIP prefixes must give the same code reasons and send the agent the same text.
  Group rates by ZIP region and by tag are reported, not gated, because they reflect what is
  complained about where.
- **CFPB's response as a signal, reported.** The share of complaints closed with monetary relief
  that the desk flagged, beside the share of those closed with an explanation.
- **The scripted run is labelled as such.** It measures the desk's code with a stand-in agent;
  every report says so.

## Alternatives considered

- **Scoring against CFPB's company response.** Rejected as ground truth: a well-handled urgent
  complaint and a trivial one can both close with an explanation.
- **An LLM judge.** Rejected, as for the claims ([ADR 0012](0012-deterministic-scoring.md)).
- **Gating group rates.** Rejected for now: without adjusting for case mix, a gate would fire on
  where fraud happens, not on how the desk treats people.

## Consequences

The answer key is one person's reading of one day's labels; a label it doesn't list is routed
only by the patterns, and the eval can't see a miss there. The counterfactual check can't flip
by construction (the agent is never sent location), so it shows location doesn't reach the
decision, not that the model is unbiased. The model itself is unmeasured until a live run,
which costs money and is run only when asked.
