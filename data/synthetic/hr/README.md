# Synthetic HR leave data

Everything here is invented: Larkspur Supply Co., Larkspur Labs LLC, their sites and their
employees. No real employee, payroll or medical data is used, and there is no connection to any
HR system. Workday is named only to say where each step would sit in a real HR process.

The records hold what the federal FMLA eligibility and routing rules read: IDs, dates, hours and
counts. There are no names, no diagnoses and no medical certificates; pending discipline, a
complaint or a termination is a yes or no, and the age of a child being cared for is only under
18 or not. Some fields are still health information, kept as coarse as the rules allow: the
reason category of a request and of leave already taken, whether an adult child can't care for
themselves because of a disability, and signs of an accommodation need. Bonding requests carry
the date of the birth or placement, which the 12-month window needs.

- `company.json`: the two employers (coverage by weeks with 50 or more employees, and how each
  measures its leave year), their worksites (state and headcount when notice was given), and
  surface miles between **every pair** of one employer's sites. A pair with no distance can't be
  decided, so the rules never treat it as far away.
- `employees.json`: employment periods (an `end` is the day after the last day worked), the
  site each person works at or is assigned to, the state they live in (on every record: a
  missing one can't be decided), an
  hours ledger of hours actually worked per week (weeks end on a Saturday; a week on the payroll
  the ledger doesn't cover is unknown, not zero), the normal workweek (Monday to Friday unless
  stated), FMLA leave already taken, and the yes-or-no flags.
- `requests.json`: leave requests as an intake step would extract them: dates, a reason
  category, the family relationship where it matters, and whether there are signs of an
  accommodation need. Dates are inclusive.
- `messages.json`: each request as the employee wrote it, with the time it was sent. This is
  what the leave intake agent reads (after identifier redaction); `requests.json` is the answer
  key for what it should extract. Treat the text as untrusted input.
- `adversarial.json`: two messages with text aimed at the AI, and the injection signals each
  should raise.
- `ground_truth.json`: the answer key for each request, worked out by hand: eligibility, the
  checks that fail or can't be decided, weeks left and asked for, the route, the least senior
  role that may decide, the routing reasons, and when the eligibility notice is due.

Larkspur measures its leave year rolling backward from the leave's start; tests cover the other
three methods the regulations allow.

## Requests

| Request | Expected | Why it's here |
|---|---|---|
| lr_001 | Local manager sign-off | Clean baseline: 4 of 12 weeks |
| lr_002 | HR business partner: short service | Hired on a Sunday: 51 weeks on the payroll |
| lr_003 | Local manager sign-off | Hired the Saturday before: that day's week counts, making 52 |
| lr_004 | HR business partner: low hours | 1,249 hours; 40-hour weeks just outside the 12 months |
| lr_005 | Local manager sign-off | Exactly 1,250 hours; notice on a Thursday |
| lr_006 | HR business partner: small worksite | 49 employees within 75 miles |
| lr_007 | Local manager sign-off | 50 within 75 miles, counting a site exactly 75 miles away |
| lr_008 | HR business partner | Employer not covered, and 30 within 75 miles (another employer's office nearby doesn't count) |
| lr_009 | HR director: past entitlement | 10 workweeks used, 3 asked, 2 left |
| lr_010 | Local manager sign-off | Only 5 workdays of an older leave fall in the rolling year: 11 weeks left |
| lr_011 | HR business partner: intermittent | Intermittent leave always goes to a person |
| lr_012 | HR director: retaliation risk | Pending discipline, complaint or termination |
| lr_013 | HR business partner | Possible ADA or PWFA accommodation |
| lr_014 | HR business partner: state law | California site |
| lr_015 | HR business partner | Key employee |
| lr_016 | HR business partner | Spouse works here too, so bonding leave may be shared |
| lr_017 | HR business partner: bonding window | Bonding leave starting inside the 12 months after the birth and ending after them |
| lr_018 | HR business partner | A parent-in-law isn't a covered family member |
| lr_019 | HR business partner | Reason unclear |
| lr_020 | HR business partner | Military caregiver: 26 weeks, 18 asked; notice on a Friday; a person checks the servicemember's covered status |
| lr_021 | HR business partner: short service | Service before a break of almost 11 years isn't counted: 27 weeks |
| lr_022 | Local manager sign-off | Service before a 4-year break counts: 136 weeks |
| lr_023 | HR business partner | No hours ledger: hours can't be checked |
| lr_024 | Local manager sign-off | Works from home; the worksite is the office they're assigned to |
| lr_025 | HR business partner | Care for an adult child: a person checks the disability condition |
| lr_026 | HR business partner | Earlier caregiver leave in a period that has ended: a person checks whether it was for the same servicemember and injury, and the covered status |
| lr_027 | HR business partner: state law | Works at an Ohio office but lives in New York |
| lr_028 | HR business partner | Birth bonding leave starting before the expected birth |

## Assumptions to confirm with HR

The design asked HR nine questions; until they answer, the rules take these positions:

- **No approval without a person.** Even a clean request is a draft for the employee's local
  (branch) manager to sign (question 1).
- **Sign-off limits** follow how a branch company already works: the local manager prepares
  every request and approves only the plain light-path case (no reason raised); an HR business
  partner denies, decides partial approvals and decides every request sent to HR review
  (including military leave, after attesting that the covered status was checked); an HR
  director extends past the entitlement or decides a case with a retaliation risk (question 2).
- **Intermittent leave always goes to a person** (question 5).
- **The employer doesn't count service before a break of 7 years or more**, unless the employee
  record says it must (military service or a written agreement).
- **Employer coverage is judged on the calendar year of the notice and the year before.**
- **A state list only routes.** A request goes to a person if the worksite or the employee's
  home is in a state with its own family or medical leave law; the list isn't a legal one.

## Changes to answer keys

| Date | Request | Change | Why |
|---|---|---|---|
| 2026-11-15 | lr_020, lr_026 | `reason` becomes unknown and `military_status_unverified` is added; lr_020 moves from specialist sign-off to HR review (leave specialist) | Review 25: military caregiver leave needs a covered servicemember (29 CFR 825.127) and qualifying exigency a family member on covered active duty (825.126). The records carry neither status, so the rule can't pass the reason; a person checks it. The old keys passed on the relationship alone. |
| 2026-11-15 | all 28 | Roles renamed to match a branch company: `route` `specialist_sign_off` → `manager_sign_off` and its `decided_by` `leave_specialist` → `local_manager` (7 light-path requests); `decided_by` `hr_manager` → `hr_business_partner` (7); `decided_by` `leave_specialist` → `hr_business_partner` for every request sent to HR review (12: lr_011, 013-016, 019, 020, 023, 025-028). No eligibility, check, weeks, reason code or route changes. | A local (branch) manager decides only the plain light path; the senior HR role (HR business partner) decides every flagged request, so every reason that named a leave specialist now names an HR business partner. HR directors are unchanged. |
