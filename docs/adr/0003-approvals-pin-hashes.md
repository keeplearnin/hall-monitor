# ADR 0003: Approvals pin the manifest digest and the prompt hash

- **Status:** Accepted (milestone 1; prompt hash added after the first code review)
- **Control:** [HM-01](../controls.md#hm-01)

## Context

A registry of agents is only a control if what runs is what was approved. A model swap, a new
tool or a reworded prompt can change behaviour as much as new code.

## Decision

Every agent has a typed manifest in [`registry/`](../../registry/) naming its owner, risk tier,
model, tools and the data classes it may see. [`registry/approvals.yaml`](../../registry/approvals.yaml)
records, per agent, the SHA-256 of its manifest and of its system-prompt template (everything
but the policy wording filled into it at run time). A change to that
file is the approval, so it is reviewed as one; who approved it is the pull request's reviewer.
`Registry.require` in [`registry.py`](../../hall_monitor/registry.py) refuses to run an agent
that is unregistered, inactive, or whose manifest digest doesn't match its approval, and
`run_agent` passes the hash of the system-prompt template it is about to send, which must match
too. That hash is recorded on every call, so an audit entry ties back to an approval. The first
version checked only the manifest; a review pointed out that a prompt edit slipped past, so the
prompt hash was added to the approvals and checked in CI, and a later claims audit found it was
still not checked at run time, which is now fixed.

## Alternatives considered

- **A registry without approvals.** Rejected: it documents but doesn't enforce.
- **An external model-risk inventory.** Right for production, but out of scope for a reference
  implementation; the file format is simple to export to one.

## Consequences

- Any change to an agent's model, tools, data classes or prompt template fails until the
  approval is updated in a reviewed pull request. That is deliberate friction. The policy
  wording the coverage and critic agents are given isn't covered: editing it needs no new
  approval (HM-01 gap).
- Eval results record the digests they were scored on, so the gate can tell when published
  results no longer describe the approved agents ([ADR 0013](0013-published-results-and-gate.md)).
- Approvals are a file in Git, so who may approve is enforced by repository settings, not by
  this code. `.github/CODEOWNERS` names an owner for the registry, but it takes effect only with
  branch protection that requires code-owner review, which isn't switched on yet.
