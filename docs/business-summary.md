# hall-monitor: business summary

*November 2026, after milestone 11 and Phase B so far. Plain English, one page.*

## The problem

Insurers and banks want AI to speed up work like claims handling, but a regulator will ask:

- What was the AI allowed to do?
- Who approved it?
- What records did it look at, and what happened to personal data?
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
model it may use and the records it may look up. A changed permit, or changed core instructions, is
refused until a new approval is committed (not yet covered: the policy wording the AI reads, the descriptions of its look-ups and the text that sends a correction back). Who may commit that approval is set by repository
controls, which aren't switched on yet. The AI can see policy records and claims
history only through an approved, logged channel, and only for the claim in front of it. Claims
history is matched by the insured's name, so two customers with the same name would share it.

Each AI step's owner is a named person who can stop it with one command, without waiting for
an approval. The stop applies from its very next step, and any claim that used its work and
hasn't yet reached the routing rule goes to a person. A claim already routed for automatic
handling finishes without being checked again. Turning it
back on needs an approval, like any other change.

Before any AI sees a claim's text, software removes identifiers it doesn't need (dates of birth, card,
passport and phone numbers, labelled tax ID and bank account numbers, emails) and flags any text that tries to instruct the AI.

The AI never decides on its own. A rule written in ordinary software (not AI) sends a claim to a
human adjuster if any of these apply:

- it would be denied
- it pays more than $2,500
- it has any fraud flag
- the double-check changed the answer
- the claim contains text aimed at the AI
- anything went wrong

The claim then waits for an adjuster, who must have the authority for the amount. Their decision is
recorded with the exact evidence they saw. Only small, clean claims are handled automatically.

Each claim's AI steps and decision go into a **tamper-evident record**: which AI ran, under which approved permit and
instructions, which records it looked up, every correction, the decision, who made it, and what it
cost. Changing or deleting anything in that record shows up when it is checked. Someone who can
write to the file could rewrite the whole record consistently; that is caught only by comparing it
with a copy of its latest fingerprint kept somewhere else.

Every control is mapped to the NIST AI Risk Management Framework and the FINOS AI Governance
Framework, with what isn't covered and why ([controls](controls.md)). Every proposed change is
checked automatically, including a quality gate on the published test results, and the check
fails if they fall short. A failed check doesn't yet stop the change being merged: that needs
repository controls that aren't switched on.

## What we've measured

All claims, policies and people are fictional, and one person wrote both the claims and the answer
keys. Treat these as early signals, not proven rates.

- **No claim that needed a person went through automatically** in 204 test runs on 34 claims.
- **98% of coverage decisions were right**: amount, outcome and every deciding policy rule cited
  (about a quarter of the rules cited were extra).
- **The double-check caught 84 of 84 deliberately planted mistakes**, with 1 false alarm in 34
  clean claims. It costs about 1.5 cents a claim.
- **No invented facts** in what the AI extracted and cited: every value and policy rule traced
  back to a source. Its written reasoning isn't checked this way.
- **About 4 cents and 21 seconds of AI time per claim.**
- **An adjuster would have overridden the AI on about 2% of the claims sent to them** (simulated).

These numbers come from runs on earlier versions of every AI step, before milestones 4 to 8. The approved data channel, the approval
gate, the tamper-evident record, the privacy and attack defences, the signed decisions, the kill
switch and the tax ID and bank number removal have passed their tests, but haven't yet been
scored in a full live run. Neither have the new attack and fairness tests. The routing rule is
stricter than when the numbers were scored: it now also checks the claim against the policy
record and the claimed amounts against the documents.

What went wrong along the way is published too. One example: an early version said "capped at
$3,000" and then paid $3,640. The double-check now exists to catch exactly that, and in tests it
always did.

## What's next

| Next | What it adds |
|---|---|
| Live results | A scored run of everything built since milestone 4, the attack tests, and a fairness test that checks whether changing only the customer's name changes the outcome |
| External review | Review by people outside the project. The package is ready ([review](review/README.md)); none has happened yet |

## The ask

hall-monitor is a reference implementation, not a production system. It shows how AI governance
can be built, tested and evidenced. Feedback is welcome, especially from anyone who has had to put
an AI system in front of an auditor or a regulator.

One-page versions: [executive brief](executive-brief.html) for business leaders,
[board brief](board-brief.md) for a board or risk committee, and
[architecture](architecture.html) for engineering managers and architects. Why each design choice
was made: [decision records](adr/README.md).
