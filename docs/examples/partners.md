# Partner rebate and marketing-fund claims

> **Status:** the rules in code and two agents are built. **No pipeline, evals, panel page or
> live run yet:** the agents are tested with scripted stand-ins only.

## Why this domain

A fictional hardware maker reimburses its channel partners for approved marketing activity (marketing development funds and co-op): the partner runs a campaign, an event or a trade show and claims with proof. Checking that proof is still manual at every vendor, and it is where abuse hides: duplicate or inflated invoices, backdated activity, claims split to stay under an approval limit, payments redirected to new bank details. The design is in [`docs/partner-claims-design.md`](../partner-claims-design.md) ([ADR 0032](../adr/0032-partner-claims-rules-decide.md), HM-36).

## What the agents do

**Slice 2, the agents ([ADR 0033](../adr/0033-partner-claim-agents-read-and-flag.md), HM-37):** an intake agent (Haiku) reads the claim's redacted documents and extracts the invoice and activity dates; code checks every extracted number, name and date against the documents' own text (a unit count written as units, the activity dates as the pair written together, the issuer from the invoice header rather than the Bill-to party), and a value that isn't there sends the claim to review. A stop made while a claim is prepared discards the agents' answers, and each claim is held to an approved partners budget and daily and monthly caps (an estimate: the agents haven't run live). A proof reviewer (Sonnet) raises concerns from a closed list (recycled proof, an edited document, a vague invoice line, evidence that doesn't match, low brand content); each only adds review, so a clean review never clears a rule finding.

The partner, pre-approval, payee and amount claimed stay the portal's: neither agent is given them, though the documents may name the partner. If the intake agent fails, nothing is reimbursable and every finding that needs no invoice (the payee, the partner, text aimed at the AI) is kept. The line categories and the competitor and public-sector flags are taken from the agent unchecked, a stated gap.

## What code decides

**Slice 1, the rules in code (no model):** `hall_monitor/partners/rules.py` routes every claim to a person, as compliance review, hold, review or expedited sign-off, with a fixed reason for each finding and what may be reimbursed in exact decimals. It checks the pre-approval, what earlier claims have used of it, and its dates, the 45-day claim window, eligible activities and the evidence each needs, a third-party invoice whose lines add up, excluded costs, a $75 merchandise cap, rates more than 10% over benchmark (the excess is held), competitor content (30% rate), duplicates across partners (ignoring punctuation and legal suffixes), split claims just under an approval limit, the payee against the partner's approved account, partner status and holds, hospitality for public-sector guests, and text in the documents aimed at the AI. Nothing in it can pay or change a payee.

## Who decides

A person, for every claim. The least senior role that may decide follows a dollar ladder on the amount claimed ($5,000 / $25,000, an assumption: no vendor publishes its thresholds) or what a finding demands. No sign-off flow is built yet.

## Data

47 fictional claims at a fictional hardware maker, with boundary cases on both sides of every numeric rule, invoice and evidence text for 11 of them, and hand-written answer keys for the rules and the proof reviewer ([`data/synthetic/partners/`](../../data/synthetic/partners/README.md)).

## Results

`python -m hall_monitor.partners` scores the rules against the hand-written answer keys. The agents are tested on 11 documented claims with scripted agents and hand-written reviewer answer keys; no live run yet, and no published eval result.

## Run it

```bash
uv run python -m hall_monitor.partners                     # rules on 47 synthetic claims vs the answer keys; no API calls
uv run python -m hall_monitor.partners --claim C-108       # one claim's assessment
```

## Where it lives

- **Code:** `hall_monitor/partners/`.
- **Design and decisions:** [`docs/partner-claims-design.md`](../partner-claims-design.md); ADRs [0032](../adr/0032-partner-claims-rules-decide.md) and [0033](../adr/0033-partner-claim-agents-read-and-flag.md). The pipeline, evals and a panel page are the next slices.
