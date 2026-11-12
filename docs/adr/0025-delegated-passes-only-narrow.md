# ADR 0025: When an agent hands work to another, the pass only narrows

- **Status:** Accepted (complaint crew, first slice)
- **Control:** [HM-29](../controls.md#hm-29)

## Context

The complaint crew ([design](../complaint-crew-design.md)) has a lead agent that hands parts of
a case to helper agents. Until now every agent was called by code, so its manifest alone set
what it could reach ([ADR 0004](0004-tool-gateway-claim-scope.md)). With handoffs, a helper
could end up reaching more than the agent that asked it for help (a confused deputy), chains
could loop, and stopping one agent might not stop the work it had handed out.

## Decision

- **A grant per case.** Code issues the lead's grant for one case scope. Each handoff makes a
  child grant whose tools and data classes are inside both the parent grant and the helper's
  own manifest.
- **Handoffs are declared.** `may_delegate_to` in the manifest lists who an agent may hand work
  to. It is part of the approved hall pass; an empty list is left out of the digest so existing
  approvals stand.
- **Over-asking is refused, not trimmed.** A request for a tool or data class the parent doesn't
  hold fails and is logged, so an attempt to widen a pass shows up.
- **No loops, depth two.** An agent already in the chain can't be handed work again, and chains
  stop two handoffs below the pipeline.
- **Allotments only shrink.** Children's allotments can't add up to more than the parent's.
- **The whole chain is checked on every use.** Each tool call under a grant checks every agent
  in the chain against the registry, so a stop on the lead ends its helpers' work at once.
- **The book is the authority.** A grant counts only if the case's grant book issued it, and
  closing the book ends every grant. The tool gateway refuses a crew agent's tool call without
  a grant from the book.
- **Logged as it happens.** `delegation_granted` and `delegation_refused` hold IDs, the chain,
  tool names, data classes and allotments.

## Alternatives considered

- **Let a helper keep its own manifest's reach.** Rejected: the lead could then use a helper to
  reach a tool or data class it isn't allowed itself.
- **Trim an over-wide request quietly.** Rejected: it hides the attempt; refusing and logging it
  makes it reviewable.
- **Signed, self-contained tokens (capability tokens).** Worth it across processes or services;
  in one process the book gives the same guarantee with less to get wrong. Revisit with
  milestone 15's shared store.

## Consequences

The crew can be shown and tested without any model call. Grants are allow-lists in one process,
not isolation (FINOS MI-22 stays not covered). The book lives in memory for one run; a crash
sends the case to a person without a case file. Spending against an allotment is enforced only
once agents run (slice 3).
