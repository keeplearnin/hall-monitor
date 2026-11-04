# ADR 0002: Each agent runs on its pinned model, with no silent fallback

- **Status:** Accepted (milestone 1)
- **Control:** [HM-02](../controls.md#hm-02)

## Context

A common reliability pattern is to fall back to another model when the first is unavailable.
In a regulated decision process that means a claim can be decided by a model nobody evaluated
or approved for that step, and the record may not show it.

## Decision

Each agent's manifest names one model (Haiku 5.5 for intake and fraud signs, Sonnet 5.5 for
coverage, the critic and the recommendation). `run_agent` in
[`agents/base.py`](../../hall_monitor/agents/base.py) calls only that model, and records the
model on every call. If the call fails, the claim stops and goes to a person
([ADR 0007](0007-failures-stop-the-claim.md)). The only other model an agent may use is an
approved `economy_model` named in the same manifest, chosen by a code rule
([ADR 0011](0011-economy-routing-off-until-evaluated.md)).

## Alternatives considered

- **Automatic fallback to another provider or model.** Rejected: the fallback would need its
  own evaluation and approval, and the switch would be silent.
- **Approved fallback models in the manifest.** Possible later, if a provider outage becomes a
  real cost. It would be evaluated and approved like any other model.

## Consequences

- A provider outage stops automatic processing. Claims queue for people; none is decided by an
  unapproved model. Availability is an accepted risk, recorded in the controls register.
- Changing a model means changing the manifest, which changes its digest and needs a new
  approval ([ADR 0003](0003-approvals-pin-hashes.md)).
