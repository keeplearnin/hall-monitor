# Synthetic partner claims (HM-36)

Fictional marketing development fund (MDF) and co-op reimbursement claims at Tarnwick Devices, a
fictional hardware maker. Every vendor, partner, agency, invoice and amount here is made up.
Program parameters follow public program terms where one exists (noted in `program.yaml`); the
rest, including the approval ladder, are assumptions. See the
[design](../../../docs/partner-claims-design.md).

| File | What it holds |
|---|---|
| `program.yaml` | The program terms the rules read: eligible activities, evidence per activity, windows, rates, caps, the approval ladder |
| `partners.yaml` | Five partners: status, accounts-receivable hold, and the approved payee account |
| `preapprovals.yaml` | Pre-approved activities: partner, activity type, approval date and amount |
| `history.yaml` | Claims already paid, checked for duplicates |
| `claims.yaml` | 47 claims: the demo cases (`C-*`) and boundary cases (`B-*`) |
| `answers.yaml` | Hand-written answer keys: tier, route, the least senior role, reasons, what may be reimbursed and what is held |

`python -m hall_monitor.partners` runs the rules on every claim and compares the result with the
answer keys.

## The boundary cases

| Rule | Just inside | Just outside |
|---|---|---|
| Claim window (45 days) | B-01 (day 45) | B-02 (day 46) |
| Merchandise cap ($75 a unit) | B-03 ($75.00) | B-04 ($76.00) |
| Benchmark rate (10% over) | B-05 (exactly 10%) | B-06 ($1 more) |
| Duplicate (3 matching fields) | B-07 (2 fields) | C-103 (3 fields) |
| Approval ladder ($5,000) | B-08 ($5,000.00) | B-09 ($5,000.01) |
| Approval ladder ($25,000) | B-19 ($25,000.00) | B-20 ($25,000.01) |
| Split claims (how many) | B-10A and B-10B (2) | C-106A to C (3) |
| Split claims (7-day span) | B-21A to C (exactly 7 days) | B-22A to C (8 days) |
| Split claims (95% band) | B-23A ($4,750.00) | B-24A ($4,749.99) |

Each rule that has no number also has a case: B-25 (a pre-approval already partly used), B-26
(lines that don't add up), B-27 (merchandise with no unit count), B-28 (claimed before the
activity ended), and the rest as listed in `claims.yaml`.

## Answer-key changes

Any change to `answers.yaml` is logged here with the date and the reason.

- **2026-11-15, C-104:** `decided_by` from `partner_marketing_manager` to
  `regional_marketing_lead`. The deciding role now follows the amount claimed ($6,000), not what
  survives the rules ($1,800): whoever releases the held $4,200 needs authority for the whole
  claim (review 36). The design doc and ADR 0032 were changed to match.
- **2026-11-15, B-19 to B-28 added** (review 36): boundary cases for the $25,000 step, the 7-day
  span and 95% band of the split rule, and a case for each new rule.
