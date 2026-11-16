# ADR 0033: Partner claims: agents read and flag, code checks them, they never decide

- **Status:** Accepted (slice 2: the agents)
- **Control:** [HM-37](../controls.md#hm-37)

## Context

Slice 1 ([ADR 0032](0032-partner-claims-rules-decide.md)) assesses a claim that arrives as typed
fields. Real claims arrive as a portal form plus documents: an invoice, tear sheets, screenshots,
attendee lists. Someone has to read the documents, and the parts rules can't judge (a reused
tear sheet, an edited total, a line that says only "Hospitality") need judgment. The documents
come from the partner who wants to be paid, so they are untrusted: one synthetic invoice
(C-108) asks the AI to approve the claim and pay new bank details.

## Decision

- **The portal's fields stay the portal's.** Partner, pre-approval, activity type, amount
  claimed, payee and upload categories come from the submission, never from a document or an
  agent. Neither agent is given the partner, the pre-approval, the payee or the amount claimed,
  though the documents themselves may name the partner (an invoice's bill-to line).
- **The intake agent extracts; code checks it.** `partner_claim_intake` (Haiku, no tools,
  financial data only) reads the redacted documents, fenced and escaped, and returns the invoice
  number, issuer, lines, total and activity dates. Code then checks every number, name and date
  against the documents' own text (`check_extraction`). A value that isn't there adds
  `extraction_unsupported` and sends the claim to review; nothing extracted, or a failed call,
  gives `extraction_failed` with nothing reimbursable. No value is invented to keep going, and
  every rule finding that needs no invoice (`portal_reasons`: partner status and hold,
  pre-approval, activity and evidence, the payee, text aimed at the AI) is kept, so a document
  crafted to make the agent fail can't lower the route. The checks look for each value where it
  belongs: the invoice number as a whole token, the issuer as a whole part of a line of the
  invoice's header (never the Bill-to party), the total on the total line, a line's amount on
  one line with its unit count written as units (not one of the line's money figures) and,
  where a unit price is stated, units times that price making the amount, and the activity's
  start and end as the pair written together (review 38 tightened these three).
- **Text aimed at the AI is found by code in the raw documents,** by the repo's injection
  detector inside the slice-1 rules, not by asking an agent whether it was attacked.
- **The proof reviewer can only add review.** `partner_proof_reviewer` (Sonnet, no tools,
  financial data only) returns concerns from a closed list of five and a confidence, with no
  free-text field. Each concern maps to a fixed reason with a fixed text, tier and least senior
  role. The tier and the role are the maximum of the rules' and the concerns'; no rule finding
  is ever removed. An edited document needs the finance controller. A failed review adds
  `review_failed`; a low-confidence one adds `review_uncertain`. Both go to a person.
- **Both agents run inside the shared controls:** hall pass by digest, approved prompt hash,
  data-class check (`carries={financial}`), kill switch, budgets, typed output and a call record
  for each. Every agent that answered is checked against the registry again before the result
  is returned, so a stop made while the claim was prepared discards their answers
  (`agent_stopped`). Each claim is a case under the `partners` budget in
  `registry/budgets.yaml` (2 steps, 0.05 USD, 120 s; 1.00 USD a day, 10.00 a month, estimates,
  not measurements): refused at a cap it gets `spend_refused` before any call, out of budget
  before an agent's call `budget_exhausted` (review 38).

## Alternatives considered

- **Let the agent fill every field, portal fields included.** Rejected: a forged document could
  then change the payee or the partner. A field the portal already holds is never re-read from
  what the partner uploaded.
- **Trust the extraction and only spot-check it.** Rejected: an injected or mistaken total
  would change what is reimbursable. Checking every value against the text is cheap and exact.
- **Let the reviewer clear a rule finding ("the duplicate looks legitimate").** Rejected: the
  reviewer reads documents a partner wrote; a clean review never lightens a route.
- **Give the reviewer a free-text rationale.** Deferred: it would need its own checks (no
  personal data, no promises) before a person saw it. The closed list is enough for routing.

## Consequences

- The agents are tested only with scripted fake clients: 11 documented claims, with
  hand-written answer keys for the reviewer (`data/synthetic/partners/review_answers.yaml`).
  No live run has measured either agent yet; that is slice 4.
- Three things the intake agent returns are trusted, not checked: each line's category (the
  benchmarks and exclusions follow it) and the competitor-content and public-sector flags. A
  dropped flag puts a claim on the wrong route (C-107 would be green, not compliance review),
  though a person still signs. Slice 4 measures how often.
- The checks assume an invoice laid out in lines with a total line; other layouts go to review.
  A start date that isn't the written pair goes to review as `extraction_unsupported`; the
  rules don't re-derive `activity_before_approval` from the written dates, so C-105 extracted
  with a later start is review, not the hold it gets when read correctly (HM-37 gap).
- A portal submission naming a partner or pre-approval the book doesn't hold goes to a person
  (`partner_not_approved`, `no_preapproval`) instead of stopping with an error.
- Redaction removes contact details, card numbers and IBANs before an agent sees the documents.
  It is pattern-based and doesn't remove people's names; the synthetic attendee lists hold
  counts only.
- No pipeline, audit events or panel page yet (slices 3 and 5).
