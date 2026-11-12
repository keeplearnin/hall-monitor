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
- **Adaptive rounds.** Each round applies the next mutation only to seeds not yet through.
- **The judge is code and reads the seed, not the defence.** A breach is defined by what the
  attacker planted and what must hold (must-route complaints, no agent text in the log), never
  by the defence's own patterns.
- **Harm, layer and trade-off are reported apart.** A failed detector isn't counted as harm when
  routing still holds; the agent raising urgency is the desk's accepted design, reported so its
  cost shows.
- **Tests hold the harm line on routing and the log.** Must-route misses and agent text in the
  log must stay at zero. Kept drafts with a delivery path are published as found, not gated,
  until they are fixed.

## Alternatives considered

- **A model as judge.** Rejected for every breach code can decide; a model judge can be argued
  with by the same attack.
- **Fix the findings in this slice.** Rejected: the arena's first job is to show what it finds.
  Fixes are separate, test-first changes the arena then re-measures.

## Consequences

The first run shows gaps in the draft check (it reads text undecoded) and in the injection
detector (entities encoded twice, look-alike letters). They are published, not hidden, and
listed as gaps. No live attacker or defender has been run.
