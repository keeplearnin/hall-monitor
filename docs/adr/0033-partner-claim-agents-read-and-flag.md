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
  agent. Neither agent sees the partner, the pre-approval, the payee or the amount claimed.
- **The intake agent extracts; code checks it.** `partner_claim_intake` (Haiku, no tools,
  financial data only) reads the redacted documents, fenced and escaped, and returns the invoice
  number, issuer, lines, total and activity dates. Code then checks every number, name and date
  against the documents' own text (`check_extraction`). A value that isn't there adds
  `extraction_unsupported` and sends the claim to review; nothing extracted, or a failed call,
  gives `extraction_failed` with nothing reimbursable. No value is invented to keep going.
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
  for each.

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
- `check_extraction` checks that each value appears in the text, not that it is in the right
  place: an agent that swaps the total and a line amount that both appear on the invoice would
  pass the check. The rules' "lines don't add up" check catches some of these, not all.
- The intake agent decides the line categories, and the benchmarks and exclusions follow the
  category. A miscategorised line (alcohol called "hospitality") is a real risk the prompt
  addresses but code can't fully check; it is measured in slice 4.
- Redaction removes contact details and card numbers before an agent sees the documents; it is
  pattern-based and can miss formats it doesn't know.
- No pipeline, audit events or panel page yet (slices 3 and 5).
