# Synthetic HR leave data

Everything here is invented: Larkspur Supply Co., Larkspur Labs LLC, their sites and their
employees. No real employee, payroll or medical data is used, and there is no connection to any
HR system. Workday is named only to say where each step would sit in a real HR process.

The records hold what the federal FMLA eligibility rules need and nothing else: IDs, dates,
hours and counts. There are no names, no diagnoses and no medical certificates, and pending
discipline, a complaint or a termination is a yes or no only.

- `company.json`: the two employers (coverage by weeks with 50 or more employees, and how each
  measures its leave year), their worksites (state and headcount when notice was given), and
  surface miles between sites. **A pair of sites not listed is more than 75 miles apart.**
- `employees.json`: employment periods (an `end` is the day after the last day worked), the
  site each person works at or is assigned to, an hours ledger of hours actually worked per week
  (weeks end on a Saturday), FMLA leave already taken, and the yes-or-no flags.
- `requests.json`: leave requests as an intake step would extract them: dates, a reason
  category, the family relationship where it matters, and whether there are signs of an
  accommodation need. Dates are inclusive.
- `ground_truth.json`: the answer key for each request, worked out by hand: eligibility, the
  checks that fail or can't be decided, weeks left and asked for, the route, the least senior
  role that may decide, the routing reasons, and when the eligibility notice is due.

Larkspur measures its leave year rolling backward from the leave's start; tests cover the other
three methods the regulations allow.

## Requests

| Request | Expected | Why it's here |
|---|---|---|
| lr_001 | Specialist sign-off | Clean baseline: 4 of 12 weeks |
| lr_002 | HR manager: short service | 355 days, under 52 weeks |
| lr_003 | Specialist sign-off | Exactly 52 weeks (364 days) |
| lr_004 | HR manager: low hours | 1,249 hours; 40-hour weeks just outside the 12 months |
| lr_005 | Specialist sign-off | Exactly 1,250 hours; notice on a Thursday |
| lr_006 | HR manager: small worksite | 49 employees within 75 miles |
| lr_007 | Specialist sign-off | 50 within 75 miles, counting a site exactly 75 miles away |
| lr_008 | HR manager | Employer not covered, and 30 within 75 miles (another employer's office nearby doesn't count) |
| lr_009 | HR director: past entitlement | 10 weeks used, 3 asked, 2 left |
| lr_010 | Specialist sign-off | Only 8 days of an older leave fall in the rolling year: 10.86 weeks left |
| lr_011 | Leave specialist: intermittent | Intermittent leave always goes to a person |
| lr_012 | HR director: retaliation risk | Pending discipline, complaint or termination |
| lr_013 | Leave specialist | Possible ADA or PWFA accommodation |
| lr_014 | Leave specialist: state law | California site |
| lr_015 | Leave specialist | Key employee |
| lr_016 | Leave specialist | Both parents work here, so bonding leave is shared |
| lr_017 | HR manager: bonding window | Bonding leave ending more than 12 months after the birth |
| lr_018 | HR manager | A parent-in-law isn't a covered family member |
| lr_019 | Leave specialist | Reason unclear |
| lr_020 | Specialist sign-off | Military caregiver: 26 weeks, 18 asked; notice on a Friday |
| lr_021 | HR manager: short service | Service before a break of almost 11 years isn't counted: 182 days |
| lr_022 | Specialist sign-off | Service before a 4-year break counts: 939 days |
| lr_023 | Leave specialist | No hours ledger: hours can't be checked |
| lr_024 | Specialist sign-off | Works from home; the worksite is the office they're assigned to |
| lr_025 | Leave specialist | Care for an adult child: a person checks the disability condition |

## Assumptions to confirm with HR

The design asked HR nine questions; until they answer, the rules take these positions:

- **No approval without a person.** Even a clean request is a draft for a leave specialist to
  sign (question 1).
- **Sign-off limits** are the design's guess: a leave specialist approves within the entitlement
  or asks for more, an HR manager denies or finds an employee ineligible, an HR director extends
  past the entitlement or decides a case with a retaliation risk (question 2).
- **Intermittent leave always goes to a person** (question 5).
- **The employer doesn't count service before a break of 7 years or more**, unless the employee
  record says it must (military service or a written agreement).

## Changes to answer keys

None yet.
