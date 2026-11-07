# ADR 0018: The leave agents read and explain; code decides and a person signs

- **Status:** Accepted (HR example, second slice)
- **Control:** [HM-22](../controls.md#hm-22)

## Context

The leave rules came first ([ADR 0017](0017-leave-rules-in-code.md)), so the agents could be
added against rules that already decide eligibility, entitlement and who must decide. Two jobs
need a model: reading an employee's message, which is free text with a name and a health
condition, and explaining the result to the leave specialist in plain words. The design asked
that no agent ever sees a diagnosis it doesn't need and that none can approve or deny leave.

## Decision

- **Same machinery as claims.** Both agents run through `run_agent`: a manifest with a named
  owner, an approval pinning the manifest and prompt hashes, the kill switch, the data-class
  check and the audit log. No HR-specific path around any of them.
- **Intake extracts, with nowhere to put a diagnosis.** The message is redacted for identifiers
  and fenced as untrusted; the agent's answer is dates, a reason category and yes-or-no facts,
  with no free-text field, so a condition can't travel downstream even if the model writes one.
- **Code decides; the explanation can't change it.** The rules run on the extraction and set
  the route and the role, so intake's facts do shape the result: a wrong date or family member
  changes it, which is why the eval exists. The explanation agent reads only the code's result
  (no message, no employee ID, no request ID); its note, one per check, is kept only if it
  covers exactly the checks the code made, and is shaped as a list because the API's structured
  output can't express a map with free keys. If it fails or is stopped, the request goes to the
  same person without a note, and the log says why.
- **Text aimed at the AI goes to a person,** whatever the agents return, by the same detector
  the claims use, on the message as sent and as redacted.
- **Failures go to a person,** as in claims ([ADR 0007](0007-failures-stop-the-claim.md)): a
  refusal, a stopped or unapproved intake agent, an API error, any unexpected error, an
  extraction without dates, with an end before its start or with a date outside 1900 to 2199,
  no employee record, or a cost that can't be shown. A cost that can't be shown after the code
  routed keeps its reasons, so the role never drops.
- **Keyed hashes only.** The log ties the message, extraction, result, note and each agent's
  input to the request by keyed hash (HMAC), and the plain input hash stays out of traces: an
  extraction, or the result the explanation agent reads, has so few possible values that a
  plain hash could be confirmed by guessing, which would reveal the reason category. Only the
  published prompt templates are hashed plainly.
- **An eval for what the rules can't see.** A wrong date or the wrong family member changes the
  result without any rule noticing, so `hall_monitor.hr.evals` scores intake field by field and
  the end-to-end result against the answer keys. It costs money to run and hasn't been run.

## Alternatives considered

- **One agent that reads the message and recommends a decision.** Rejected: the recommendation
  would be an argument with the rules, and its reasons couldn't be tested.
- **Let intake return a free-text summary for the specialist.** Rejected: it would carry the
  condition into every later step; the specialist reads the message itself.
- **Skip the explanation agent.** Possible, since the checks carry their rules and facts; kept
  because the specialist reads a sentence faster than a JSON check, and it costs a cheap call.
  It is advisory, so it can be dropped without changing any control.

## Consequences

- The intake agent's accuracy on real messages is unmeasured until a live run.
- The design's critic and risk agents aren't built; the risk flags come from records in code.
- The signed sign-off came next ([ADR 0019](0019-signed-hr-sign-off.md)), then the panel's HR
  cases.

## Evidence

`tests/test_hr_agents.py`: with a scripted model, every message reaches the answer key's
result; an intake error the rules can't see shows up in the eval; text aimed at the AI goes to
a person; identifiers are redacted before intake; the explanation agent sees no message or
identity; every failure of intake or the rules stops the request; the audit log holds nothing
the employee or a model wrote, and no plain hash but the prompt templates'. Review 9 in
docs/review/log.md.
