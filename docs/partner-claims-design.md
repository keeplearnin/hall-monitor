# Design: partner rebate and marketing-fund claims

- **Status:** Proposed. Slice 1 (the rules in code) and slice 2 (the agents, scripted only) are
  built; the pipeline, evals and the panel come later. Written before the code, on the branch, so the reasoning is recorded. Where the build
  differs, the ADRs and the controls register win and this page is updated in the same PR.
- **Controls:** HM-36 (claim rules in code, slice 1), HM-37 (the agents read and flag, slice 2).
  Later slices take the next numbers.
- **Decision record:** [ADR 0032](adr/0032-partner-claims-rules-decide.md).

## Why this example

A hardware maker that sells through partners pays them marketing development funds (MDF)
and co-op funds: the partner runs an approved activity (an ad campaign, an event, a trade show),
then claims reimbursement with proof. Every large vendor runs the same lifecycle (Cisco's,
Microsoft's and Dell's program terms are public): allocate funds, pre-approve the activity,
claim within a window with proof of performance and a third-party invoice, audit, pay or claw
back. Checking the evidence is still done by hand everywhere, and that is where abuse hides:
inflated or duplicate invoices, campaigns that never ran, backdating, claims split to stay under
an approval limit, payments redirected to new bank details. The same file also decides the
accounting (under ASC 606 a payment to a partner reduces revenue unless it buys a distinct,
fairly valued service) and is what regulators examined when discounts and "marketing
reimbursements" were used as bribe slush funds.

So it is a strong test of the repo's controls on a commercial process: real money, untrusted
documents, authority limits, and a hard line that the AI never pays anyone.

Everything here is fictional: the vendor (Tarnwick Devices, a hardware maker), its partners,
agencies and amounts. Public program terms are cited where a parameter comes from one; the rest
is labelled as an assumption.

## Who does what

| Step | Who | What |
|---|---|---|
| Read the claim's documents | Agent (slice 2) | Extracts invoice lines, dates, evidence types; documents are untrusted input |
| Hard checks | Code (slice 1) | Pre-approval, dates, eligibility, evidence, third-party invoice, caps, benchmark rates, duplicates, split claims, payee, partner status, text aimed at the AI |
| Judgment | Agent (slice 2) | Is the proof genuine? Does a vague invoice line hide ineligible cost? Every call cites evidence and the program clause |
| Route | Code | Green, yellow, red or compliance review, to the least senior role whose authority covers it |
| Decide | A person | Every claim; green means expedited sign-off plus post-payment sampling, never automatic payment |
| Pay | ERP, outside this system | Only after a person's approval, and only to the payee on the partner's approved record |

**The agent never pays, approves or changes a payee.** In slice 1 there is no agent at all: the
claim arrives as typed fields. In slice 2 ([ADR 0033](adr/0033-partner-claim-agents-read-and-flag.md))
an intake agent extracts the invoice and dates from the documents and code checks every value
against their text; the portal's fields (partner, payee, amount claimed) never come from an
agent. A proof reviewer raises concerns from a closed list, each of which only adds review. The
judgment row above is narrower than first planned: the reviewer returns concern codes and a
confidence, not cited evidence or program clauses.

## The rules (slice 1)

Program terms are data (`data/synthetic/partners/program.yaml`), so a change to them is a
reviewed change to one file. Each rule gives a fixed reason code; the code, not a model, writes
every reason a person sees.

| Rule | Reason code | Tier | Public anchor |
|---|---|---|---|
| Partner not on the approved list | `partner_not_approved` | compliance | Cisco MVF (company-only payees) |
| Hospitality with public-sector attendees | `public_sector_hospitality` | compliance | FCPA cases on marketing funds |
| No pre-approval, or one for another partner or activity | `no_preapproval` | red | Cisco, Ruckus |
| Activity started before the approval | `activity_before_approval` | red | Cisco ("Approved" before start) |
| Claimed more than 45 days after the activity ended | `claim_late` | red | Microsoft FY24 (45 days) |
| Activity type not in the program | `activity_ineligible` | red | All programs |
| Invoice issued by the partner itself | `invoice_from_partner` | red | Cisco, DigiCert (third-party invoice) |
| Matches an earlier paid claim or another claim, whoever the partner, on 3 or more of invoice number, issuer, total, start and end (case, punctuation, leading zeros and legal suffixes ignored) | `duplicate_claim` | red | Level6 (vendor guidance) |
| Payee account not the partner's approved one | `payee_not_on_master` | red | Payment-redirection fraud |
| Text in the documents aimed at the AI | `text_addresses_ai` | red | The repo's injection detector |
| Partner on accounts-receivable hold | `partner_on_ar_hold` | red | Dell FY24 |
| Required evidence for the activity type missing | `missing_evidence` | yellow | Cisco, Microsoft |
| Alcohol, travel or gift cards on the invoice | `excluded_cost` | yellow | Cisco |
| Merchandise over $75 a unit | `merchandise_over_cap` | yellow | Cisco |
| A line more than 10% above its benchmark rate | `rate_over_benchmark` | yellow | Level6 (hold, not reject) |
| Competitor products in the creative | `competitor_content` | yellow | Microsoft (30% cap) |
| More than the pre-approved amount | `over_preapproval` | yellow | All programs |
| Claimed more than the invoice total | `claimed_over_invoice` | yellow | All programs |
| Earlier claims already used some of the pre-approval | `preapproval_used_up` | yellow | All programs (partial claims allowed, never past the approval) |
| The invoice lines don't add up to its total | `lines_dont_match_invoice` | yellow | Proof of cost |
| Merchandise with no unit count, so the cap can't be checked | `merchandise_units_missing` | yellow | Cisco (per-unit cap) |
| Impossible dates: ends before it starts, or claimed before it ended | `dates_inconsistent` | yellow | Data sanity |
| Three or more claims from one partner and agency, submitted within one 7-day span, each within 5% under the same approval threshold | `split_under_threshold` | yellow | Split-claim pattern (Level6, fraud literature) |

**What may be reimbursed:** the eligible invoice lines (excluded costs dropped; merchandise
capped at $75 a unit, worked out from the line's amount and unit count, not its stated unit
price; a line more than 10% over its benchmark counted at the benchmark, the excess held for a
person), times the reimbursement rate (100%, or 30% with competitor content), capped at what was
claimed, the invoice total, and the pre-approved amount less what earlier claims on it have
claimed. The rules report this figure and the amount held, to the cent; they never move money.
A line just over the variance is counted at the benchmark, not at benchmark plus 10%, so one
dollar more can mean $200 less (B-05 and B-06): a deliberate prompt to justify the rate.

**Routing.** Compliance if any compliance reason; else red (hold) if any red; else yellow
(review) if any yellow; else green (expedited sign-off). The least senior role that may decide
is the higher of the approval ladder for the amount claimed (the amount at stake: whoever
releases a held part needs authority for the whole) and any role a reason demands:

| Role | Up to | Note |
|---|---|---|
| Partner marketing manager | $5,000 | **Assumption.** No vendor publishes its thresholds; modelled on Oracle Siebel PRM's approver limits |
| Regional marketing lead | $25,000 | Also required for a split-claim pattern |
| Finance controller | above | Also required for an activity before approval, a payee mismatch or text aimed at the AI |
| Compliance officer | any | Every compliance reason |

## Data

`data/synthetic/partners/`: the program terms, five fictional partners, pre-approvals, one
earlier paid claim, 47 claims and hand-written answer keys. The claims are the eight demo cases
from the research (one is a set of three split claims) plus boundary cases on both sides of
every numeric rule: the 45th and 46th day, a rate exactly 10% and just over 10% above
benchmark, merchandise at and just over $75, $5,000 and $5,000.01 and $25,000 and $25,000.01 on
the ladder, two and three matching duplicate fields, two and three claims under a threshold,
three claims within exactly 7 days and over 8, and a lowest claim at exactly 95% of the limit
and one cent under.

## Slices

1. **The rules in code** (this PR): program terms, synthetic data, `hall_monitor/partners/rules.py`,
   tests on both sides of every boundary, and `python -m hall_monitor.partners`, which scores
   the rules against the answer keys. No model.
2. **Agents:** an extraction agent and a judgment agent through `run_agent()`, with hall passes,
   approvals and model cards; documents fenced as untrusted.
3. **The pipeline:** redaction and injection checks first, the spend check, a supervisor within
   code's bounds, every failure to a person, audit events.
4. **Evals and the gate**, including the agentic comparison.
5. **The panel page.**
6. **A live run,** only when the spend is approved.

## What this doesn't do

- No payment, no change to a payee, no message to a partner: drafts only, as on the complaint
  desk.
- No accounting classification: an agent may later suggest expense or contra-revenue, but the
  policy, fair-value benchmarks and accrual judgments stay with accounting staff.
- Rebates computed from distributor sales data and deal-registration discounts are out of scope;
  this covers reimbursement claims, where the manual effort sits.
- The proposal stage (lead time before an activity) is the partner portal's job; these rules
  start at the claim.
