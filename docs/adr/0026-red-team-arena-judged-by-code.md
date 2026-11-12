# ADR 0026: A red-team arena against a hijacked agent, judged by code

- **Status:** Accepted (red-team arena, first slice)
- **Control:** [HM-30](../controls.md#hm-30)

## Context

The complaint desk ([design](../complaint-desk-design.md)) has layered defences against
hostile input: the injection detector, a fenced and escaped prompt, routing in code, the draft
check and an audit log that keeps codes and hashes only. They were tested against cases their
authors wrote. The arena ([design](../redteam-arena-design.md)) keeps attacking them: it tries
each attack, then rewrites the ones that were blocked and tries again.

## Decision

- **The defender assumes a hijacked model.** The triage agent is replaced by a stand-in that
  answers whatever each attack asks. What holds then holds whatever the model does; the result
  says it measures the code, not the model. The stand-in marks itself scripted, so no log shows
  its answers as the model's.
- **The attacker is deterministic in slice 1.** Synthetic seeds, rewritten by a fixed list of
  mutations, so a run is repeatable and the published result is checked against the code on
  every pull request.
- **Adaptive rounds.** Each round applies the next mutation to every seed that hasn't caused
  harm yet. A seed that only beat a defence layer or inflated urgency keeps being tried, so every
  round still checks it for harm.
- **The judge is code and reads the seed, not the defence.** A breach is defined by what the
  attacker planted and by a human label of whether a specialist should see the complaint
  (`must_route`, written from the whole complaint), never by the defence's own patterns. Tests
  check the label one way only: a complaint the code must route is labelled must-route.
- **Harm, layer and trade-off are reported apart.** A failed detector isn't counted as harm when
  routing still holds; the agent raising urgency is the desk's accepted design, reported so its
  cost shows.
- **Tests hold the harm line where the code claims it.** A complaint whose labels or tags call
  for a specialist is never played down, and no text reaches the log. Must-route complaints
  shown only in the narrative and kept drafts with a delivery path are published as found, not
  gated, until they are fixed.

## Alternatives considered

- **A model as judge.** Rejected for every breach code can decide; a model judge can be argued
  with by the same attack.
- **Fix the findings in this slice.** Rejected: the arena's first job is to show what it finds.
  Fixes are separate, test-first changes the arena then re-measures.

## Consequences

The first run shows three gaps. Routing reads only CFPB's labels and tags, so a must-route
complaint that says what happened only in its narrative goes to the standard queue (3 of 3 such
seeds, round 1). The draft check misses promises in words it doesn't list (one seed, plain
text, round 1) and reads text undecoded (four seeds hidden with entities or full-width forms).
The injection detector misses entities encoded twice and look-alike letters. They are
published, not hidden, and listed as gaps. No live attacker or defender has been run.
