# ADR 0009: Remove identifiers no agent needs; keep medical details where coverage needs them

- **Status:** Accepted (milestone 8)
- **Controls:** [HM-10](../controls.md#hm-10), [HM-11](../controls.md#hm-11)

## Context

Claims carry personal data, and many carry health data. Data protection law expects each
processing step to use only what it needs. But removing medical details would break the
coverage decision, which depends on them.

## Decision

- **Redact before any model sees the claim.** Dates of birth, emails, phone numbers, card,
  passport and Social Security numbers are removed from the narrative and attachment text by
  [`gateway/redaction.py`](../../hall_monitor/gateway/redaction.py); only counts by type are
  recorded. Patterns are deliberately narrow so an ordinary date is never removed as a phone
  number.
- **Keep what decisions depend on.** Names, policy numbers, dates, amounts and medical details
  stay. Each manifest declares the data classes the agent may see; that is a declaration for
  review, not yet checked at run time.
- **Minimise the recommender.** It receives structured fields only (`RecommendationInput`): no
  names, no claim text, no free-text reasoning that could carry medical details.
- **No claim narrative in telemetry or the audit log.** Trace decorators capture metadata
  only, and the audit log holds IDs, hashes and decision fields.

## Alternatives considered

- **Redact everything sensitive, including medical details.** Rejected: coverage accuracy would
  collapse, pushing more claims to people for no privacy gain at the human step.
- **A third-party PII service.** Reasonable in production; the narrow, tested patterns here are
  enough to show the control and are easy to replace.

## Consequences

- Pattern-based redaction will miss unusual formats; it is a layer, not a guarantee.
- Attachment file names and tool results (claims history has short health summaries) are not
  redacted.
- The recommender's limit is real because its input schema enforces it. The other agents'
  data classes are not enforced; doing that would mean filtering each agent's input by class.
- Health data still goes to the model provider for the agents that need it, which a production
  deployment must cover in its data processing agreement.
