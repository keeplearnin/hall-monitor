# ADR 0009: Remove identifiers no agent needs; keep medical details where coverage needs them

- **Status:** Accepted (milestone 8; data classes enforced since milestone 11)
- **Controls:** [HM-10](../controls.md#hm-10), [HM-11](../controls.md#hm-11)

## Context

Claims carry personal data, and many carry health data. Data protection law expects each
processing step to use only what it needs. But removing medical details would break the
coverage decision, which depends on them.

## Decision

- **Redact before any model sees the claim.** Dates of birth, emails, phone numbers, card,
  passport and Social Security numbers, IBANs (checked with their checksum), and labelled tax
  IDs, bank account and routing numbers are removed from the claim text, attachment file names
  and tool results by
  [`gateway/redaction.py`](../../hall_monitor/gateway/redaction.py); only counts by type are
  recorded. Patterns are deliberately narrow so an ordinary date is never removed as a phone
  number.
- **Keep what decisions depend on.** Names, policy numbers, dates, amounts and medical details
  stay, but only for agents approved to see them.
- **Enforce data classes at run time.** Each manifest lists the classes the agent may see (pii,
  health, financial). Each agent input and each tool declares the classes it carries: claims
  history carries pii, health and financial, policy records pii and financial, and an input that
  says nothing is taken to carry everything. Only the recommender's input declares its classes,
  so the input check constrains only the recommender; the other agents are approved for every
  class. Anything beyond the manifest is refused before the model or tool
  is called.
- **Minimise the recommender.** It receives structured fields only (`RecommendationInput`): no
  names, no claim text, no free-text reasoning that could carry medical details. Its claim type
  (`emergency_medical`) and clause numbers (5.1, pre-existing conditions) can still reveal a
  medical claim, so the input is declared, and the agent approved, as health data.
- **No claim narrative in telemetry or the audit log.** Trace decorators capture metadata
  only, and the audit log holds IDs, hashes and decision fields.

## Alternatives considered

- **Redact everything sensitive, including medical details.** Rejected: coverage accuracy would
  collapse, pushing more claims to people for no privacy gain at the human step.
- **A third-party PII service.** Reasonable in production; the narrow, tested patterns here are
  enough to show the control and are easy to replace.

## Consequences

- Pattern-based redaction will miss unusual formats; it is a layer, not a guarantee.
- Classes are declared by code, not detected from content, so a wrong declaration isn't caught.
  The default (everything) keeps an undeclared input away from a restricted agent.
- Health data still goes to the model provider for the agents that need it, which a production
  deployment must cover in its data processing agreement.
