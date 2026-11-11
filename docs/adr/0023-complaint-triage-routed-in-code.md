# ADR 0023: Every complaint goes to a person; code chooses the queue, the agent only advises

- **Status:** Accepted (complaint desk, third slice)
- **Control:** [HM-27](../controls.md#hm-27)

## Context

The complaint desk ([design](../complaint-desk-design.md)) prepares public CFPB complaints for a
simulated company's complaint team. Some complaints must reach a specialist quickly whatever
anyone thinks of them: fraud, identity theft, a home at risk, discrimination, and consumers CFPB
tags as older Americans or servicemembers. CFPB no longer publishes narratives, so what the desk
has is CFPB's structured fields: the product, issue and sub-issue the consumer chose, the tags,
and whether the company answered on time.

The existing examples settled the pattern: routing is code ([ADR 0001](0001-routing-decided-in-code.md)),
failures stop and go to a person ([ADR 0007](0007-failures-stop-the-claim.md)), and agents explain
while code decides ([ADR 0018](0018-leave-agents-explain-code-decides.md)).

## Decision

- **No automatic path.** Every complaint ends with a person. The code chooses between
  specialist review and the standard queue, and an urgency floor.
- **The must-route list is code on CFPB's labels.** Fraud, a scam or an unauthorised transaction;
  identity theft or an account or debt the consumer says isn't theirs; foreclosure, a mortgage
  the consumer is struggling to pay, a short sale or deed-in-lieu; discrimination; the Older
  American and Servicemember tags. Each goes to specialist review at high urgency, with the
  agent's answer or without it. Matching is deliberately broad: a wrong referral costs a
  person's time, a missed one can cost a home.
- **The agent can only add.** It may raise a concern the labels don't show, or a higher urgency;
  it can't remove a reason or lower the urgency below the code's floor. Low confidence, an
  unclear category, an urgency below the floor and a concern the code didn't find each send the
  complaint to specialist review, so the agent's doubt or disagreement is a person's to settle.
- **Failures never invent an answer.** A refusal, a stopped or unapproved agent, an API error or
  an answer that doesn't validate gives `agent_failed` and specialist review, with no category,
  summary or draft.
- **The agent sees what it needs.** Product, issue, sub-issue, tags, how it was submitted, and
  the redacted public response and narrative, fenced and escaped as untrusted; not the
  complaint ID, state or ZIP prefix, so they can't sway it (and routing provably doesn't read
  them).
- **Drafts are checked, then kept, never sent.** A draft that promises an outcome or carries a
  link, email address or phone number is not kept, and the log says why ([ADR 0021](0021-no-reply-no-send.md)).
- **The log keeps codes.** Scoped `cfpb-<complaint id>`, so it can't collide with claims or leave
  requests; the summary only as a keyed hash.

## Alternatives considered

- **Let the agent route plain complaints automatically.** Rejected: there is nothing to automate
  safely here (no payment, no decision), and an "automatic" queue would read as handled.
- **A classifier on CFPB's labels with no agent.** The code rule is that classifier for the
  must-route list. The agent's value is the summary and the cases the labels miss; the eval
  (next slice) measures whether it adds anything.

## Consequences

The labels are the consumer's own choice on CFPB's form, so a fraud complaint filed under
another issue is caught only if the agent raises it. The agent is tested only with a fake client
and a scripted stand-in; its accuracy is unmeasured until a live run. With no narratives in live
data, the injection and redaction paths are exercised mainly by tests.
