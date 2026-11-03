# hall-monitor: business summary

*November 2026, after milestone 4. Plain English, one page.*

## The problem

Insurers and banks want AI to speed up work like claims handling, but a regulator will ask:

- What was the AI allowed to do?
- Who approved it?
- What records did it look at?
- Who made the final decision?
- Can you prove all of that six months from now?

Today those answers usually sit in policy documents. hall-monitor puts them into software, so the
rules are enforced rather than just written down.

## What it does

A customer's travel-insurance claim goes through five AI steps:

1. **Read** the claim and its documents.
2. **Check** it against the policy.
3. **Look for fraud signs**, including the customer's earlier claims.
4. **Double-check** the work. A second AI compares the others' work with the documents and sends
   mistakes back.
5. **Recommend** a next step: pay, ask for information, investigate or deny.

Every AI step needs a **hall pass**: an approved permit that names its owner, its risk level, the AI
model it may use and the records it may look up. A changed permit, or changed instructions, can't
run until someone approves the change in a reviewed update. The AI can see policy records and claims
history only through an approved, logged channel, and anything it isn't allowed is refused.

The AI never decides on its own. A rule written in ordinary software (not AI) sends a claim to a
human adjuster if any of these apply:

- it would be denied
- it pays more than $2,500
- it has any fraud flag
- the double-check changed the answer
- anything went wrong

Only small, clean claims are handled automatically. Every claim keeps a full record: which AI ran,
under which approved permit and instructions, which records it looked up, every correction, and
what it cost.

## What we've measured

All claims, policies and people are fictional, and one person wrote both the claims and the answer
keys. Treat these as early signals, not proven rates.

- **No claim that needed a person went through automatically** in 204 test runs on 34 claims.
- **98% of coverage decisions were fully right**: amount, outcome and the policy rule.
- **The double-check caught 84 of 84 deliberately planted mistakes**, with 1 false alarm in 34
  clean claims. It costs about 1.5 cents a claim.
- **No invented facts.** Every name, amount and policy rule the AI used traced back to a source.
- **About 4 cents and 21 seconds of AI time per claim.**

What went wrong along the way is published too. One example: an early version said "capped at
$3,000" and then paid $3,640. The double-check now exists to catch exactly that, and in tests it
always did.

## What's next

| Next | What it adds |
|---|---|
| Milestone 5 | An approval screen for adjusters that saves the evidence they saw with their decision |
| Milestone 6 | A tamper-evident record of every step |
| Milestones 7-8 | Cheaper models for simple claims; privacy masking; fairness and attack testing |
| Milestones 9-10 | Mapping to NIST AI RMF and FINOS; an automatic quality gate on every change; an external review |

Milestone 4's own test results (the AI fetching its records through the approved channel) are
still to come.

## The ask

hall-monitor is a reference implementation, not a production system. It shows how AI governance
can be built, tested and evidenced. Feedback is welcome, especially from anyone who has had to put
an AI system in front of an auditor or a regulator.

One-page versions: [executive brief](executive-brief.html) for business leaders and
[architecture](architecture.html) for engineering managers and architects.
