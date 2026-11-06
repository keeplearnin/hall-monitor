# Board brief: governing AI agents in claims handling

*hall-monitor, November 2026. One page for a board or risk committee.*

## What this is

A working reference system that shows how AI agents can help triage insurance claims while the
firm keeps control of every decision. It uses fictional claims and policies. It is not in
production, and nothing here is a recommendation to deploy.

## The risk a board would own

If AI agents take part in claims decisions, the firm is accountable for each outcome: a wrong
payment, an unfair denial, a leak of health data, or a decision nobody can explain to a
regulator. The question for the board is not whether the AI is clever, but whether the firm can
show, claim by claim, what the AI was allowed to do and who decided.

## How the risk is controlled

| Risk | Control | How we know it works |
|---|---|---|
| AI decides something it shouldn't | A fixed software rule sends any denial, payout over $2,500, fraud sign, AI correction, suspicious text or failure to a person | 0 of 204 test runs let a claim through that needed a person |
| Wrong amount paid | A second AI checks the first and sends errors back; corrections that change a decision go to a person | Caught 84 of 84 planted errors, 1 false alarm in 34 (on an earlier version) |
| Unapproved AI in use | Each AI step needs an approved permit; any change to its model, tools or instructions is refused at run time until re-approved (the policy wording it reads is not yet covered) | Automated tests on every change |
| AI misbehaving in use | Each AI step has a named owner who can stop it at once, without an approval; turning it back on needs one | Automated tests; relies on repository controls not yet switched on |
| Personal and health data exposed | Identifiers removed from claim text before the AI sees it; the recommendation step sees structured fields only; no claim narrative in logs | Automated tests; not yet in a scored live run |
| Manipulated claims | Claim text treated as evidence, never instructions; suspicious text sent to a person; AI can see only that claim's records | 10 attack tests built; not yet run live |
| Unfair outcomes | Each claim re-run with names from six groups to see if outcomes change | Test built; not yet run live |
| No record for the regulator | Tamper-evident log of each claim's AI steps, look-ups, corrections, decision and cost | Automated tests |

Every control is mapped to the NIST AI Risk Management Framework and the FINOS AI Governance
Framework, including what isn't covered ([controls register](controls.md)). The reasons behind
each design choice are in the [decision records](adr/README.md).

## What is not yet proven

- **Fictional data, one author.** The same person wrote the claims and the answer keys. The
  numbers are early signals, not rates to rely on.
- **Results are from earlier versions.** Every AI step has been revised since the scored runs.
- **Recent controls untested live.** The privacy, attack and fairness defences pass automated
  tests but haven't had a scored live run (about $20 of AI usage).
- **No independent review yet.** An external review package is ready
  ([review](review/README.md)); nobody outside the project has reviewed it.

## Decisions a board would need to make before any pilot

1. **Where the human line sits.** The $2,500 threshold is illustrative. Lower means more
   adjuster time; higher means more automatic payments. This is a risk-appetite decision.
2. **Adjuster capacity.** In testing about 58% of claims go to a person (the test set is
   weighted to hard cases). A pilot needs the real rate and the staff to meet it.
3. **Provider dependence.** By design, if the AI provider is down, claims wait for people rather
   than switching to an unapproved model. Is that acceptable availability?
4. **Health data with an AI provider.** Steps that need medical details send them to the model
   provider. That needs a data processing agreement and a privacy assessment.
5. **Who approves AI changes.** Software refuses unapproved changes, but today anyone who can
   commit can approve one. Someone accountable (model risk, compliance) must own approvals, with
   repository controls that enforce it.

## What we're asking for

Feedback and independent review, especially from people who have put AI systems in front of an
auditor or regulator.
