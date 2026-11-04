# Review log

Every review of hall-monitor, what it found, and what changed. Findings that were not acted on
are listed with the reason.

## Review 1: two independent AI code reviews (milestone 3)

**Reviewers:** two Claude agents, run separately, each given the code and not the author's
conclusions. One looked at agent design and API use; the other at governance, security and eval
rigour. **Fixed in:** `d083a79`, `ecd20c3`.

| Finding | Severity | Outcome |
|---|---|---|
| Routing trusted the agents' labels: a claim paying $0 but labelled "covered" could go through | High | Fixed: routing checks amounts and facts ([ADR 0001](../adr/0001-routing-decided-in-code.md)) |
| A model refusal or API error crashed the run instead of stopping the claim | High | Fixed: every failure stops the claim and goes to a person ([ADR 0007](../adr/0007-failures-stop-the-claim.md)) |
| Claim text could close its own fence and pose as instructions | High | Fixed: claim fields, agent outputs and critic feedback are escaped ([ADR 0010](../adr/0010-injection-defence-in-code.md)) |
| Critic revisions overwrote earlier answers, losing the record | High | Fixed: every revision is kept with the decision fields it changed |
| Tracing captured function inputs and outputs, sending claim text and medical details to Langfuse | High | Fixed: traces carry metadata only; a test enforces it |
| The approval covered the manifest but not the prompt, so a prompt edit ran unapproved | High | Fixed: approvals pin the prompt hash too ([ADR 0003](../adr/0003-approvals-pin-hashes.md)) |
| Scoring rewarded citing extra clauses, and an amount could match as part of a longer number | Medium | Fixed: clause precision reported; amounts match whole numbers only |
| README described planned controls as if built | Medium | Fixed: planned controls marked as planned |

## Review 2: claims audit before external review (milestone 10)

**Reviewer:** an independent Claude agent, read-only, asked to check every factual claim in the
README, controls register, business summary, one-pagers and eval findings against the code,
tests and results, and to mark each true, overstated, false or unverifiable.

It confirmed the headline numbers (recomputed from the per-run scores), zero unsafe routes in
all six published runs, the routing rule, the tool gateway, the audit hash chain and the
adjuster decision rules. It found 17 problems; the most serious were claims that went further
than the code.

| Finding | Verdict | Outcome |
|---|---|---|
| Prompt approval wasn't checked at run time, only in CI | False | Fixed in code: `run_agent` checks the prompt-template hash against the approval and records it on every call |
| "Reviewed approval": anyone who can commit can regenerate approvals | Overstated | `.github/CODEOWNERS` added; docs say review depends on branch protection, which isn't on yet |
| The CI gate "can't silently ship a regression" | False as worded | Reworded; results that don't record agent versions now count as stale |
| "Checks the adjuster's identity" | Overstated | Reworded: the ID is checked against the register, not authenticated |
| "The evidence the human saw is recorded" | Overstated | Reworded: the decision is bound to the evidence pack's hash; the pack is in the checkpoint store |
| "Nothing personal in logs" | Overstated | Reworded: no claim narrative; the log does hold policy numbers, tool arguments and adjusters' reasons |
| Manifest data classes "enforced" | Overstated | Reworded: declared, not enforced; the recommender's limit is enforced by its input schema |
| Redaction gaps (attachment names, tool results) | Overstated | Listed as gaps in the register and ADR 0009 |
| "Every step is logged"; "any failure goes to a person" | Overstated | Fixed in code: any unexpected error now stops the claim. Logging wording narrowed; events are still written when triage finishes |
| README's NIST column disagreed with the register | Inconsistent | Column replaced with register IDs; the register is the single source |
| Nine framework mappings didn't fit | Mismatched | Removed from the controls they didn't fit. GOVERN 1.5, GOVERN 6.1, MAP 4.1, RI-20, RI-26 and MI-11 now appear under "not covered" with reasons; RI-07, MANAGE 4.1, MANAGE 4.3 and RI-19 stay only on the controls they fit (HM-08, HM-05 and HM-06, HM-08, HM-18). Kept, with reasons: HM-16 → MI-09 (cost per claim is spend monitoring; no alerting is a stated gap) and HM-03 → MI-16 (claim scope preserves source-data access) |
| "Verified" framework IDs | Overstated | Reworded: IDs must be in the locally checked list |
| Executive brief said 36 scored claims | False | Fixed: 34 |
| "Fully right" coverage counted extra clauses | Overstated | Reworded, with clause precision noted |
| "No invented facts" covered more than the check does | Overstated | Narrowed to extracted values and cited clauses |
| Published numbers come from agent versions no longer approved | Overstated | The README and one-pagers name the versions and commits the numbers were scored on |
| Minor: HM-02's tests, "authority" in the decision record, SDK span capture, code-corrected policy numbers route automatically, owner and risk tier unused at run time | Mixed | HM-02's tests, the decision record wording and the span caveat corrected; owner and risk tier noted in HM-01's gaps. A policy number corrected from the claim's own text still routes automatically, by design: it is recorded as a code correction and comes from the customer's own document |

**Not changed at the time, and why:** audit events were still written after triage completed,
and adjuster reasons were stored as written. Both were listed as gaps (HM-06) and fixed in
milestone 11, below.

## Follow-up: milestone 11 hardening

Fixes for the review 2 findings that were documented rather than fixed:

| Finding (review 2) | Fix |
|---|---|
| Adjuster identity not verified | Decisions must be signed with the adjuster's Ed25519 key over the claim, the decision and the evidence hash ([ADR 0014](../adr/0014-signed-decisions-and-stored-evidence.md)) |
| Evidence pack only in the checkpoint store | Stored in the audit database under its hash; the review step refuses a checkpoint pack that doesn't match; `verify` reports a changed or erased pack |
| Personal data in the log (adjuster name and reason) | The log keeps the adjuster ID and a hash of the reason |
| Events written after triage, grouped by kind | Written as they happen; a claim whose events can't be recorded doesn't proceed |
| Data classes declared, not enforced | Enforced for every agent input and tool before the model or tool is called |
| Redaction missed file names and tool results | Both are redacted |

Still open: the demo adjusters' keys are public by design; branch protection is not switched on,
so approvals aren't enforced by repository settings; the live evals haven't been rerun.

## Review 3: full code review (after milestone 11)

**Reviewer:** Claude, reviewing all of `hall_monitor/` for correctness, cleanup and design depth.
It ran in one pass with no separate verification step, so some findings may be wrong. 32
findings. **Fixed in:** this branch.

**Fixed**

| Finding | Fix |
|---|---|
| No code checked that the claimant is the insured or that the incident is within cover | Routing checks both against the policy record; a claim with no policy record or no incident date goes to a person |
| Covered amount (USD) compared with the claimed amount in the claim's currency | A claim not in USD goes to a person, and economy routing applies only to USD claims |
| A model missing from the price table crashed triage | Clear error; the claim stops for a person instead of crashing; a test checks every approved model is priced |
| The eval gate's staleness check ignored prompt changes | Results record each agent's prompt-template hash, and the gate compares it with the approval |
| Adversarial and fairness results recorded no agent versions, so they would always be stale | Every eval runner records the same run metadata, agents included, from one helper |
| `audit verify` crashed on a malformed evidence entry | Reported as a problem |
| The "read-only" audit CLI re-created triggers an attacker had removed | The CLI opens the log read-only, and verify reports missing triggers |
| A claim whose triage crashed could never be resubmitted | Resubmitting starts its triage again; earlier events stay in the log |
| The evidence hash was computed in two places that had to agree | One definition |
| The seeded critic eval divided by zero when every case of a seed errored, and returned success on errors | Fixed |
| One exception lost the whole fairness run | Each run's failure is recorded, not raised |

**Fixed in Phase A1 (approval rules):** demo-key signatures are marked in the record; denials
above an adjuster's authority need a senior adjuster; paying a claim the AI never assessed needs a
senior adjuster; the adjuster is shown the stored evidence pack, with a warning if the checkpoint
changed; one override rule shared by validation and the record; an explicit error instead of an
assert in the automatic decision.

**Fixed in Phase A2 (audit log):** reasons and critic issues are stored as keyed hashes (HMAC),
not plain SHA-256; every audit read takes the writer's lock. A test with three writers and three
readers failed before the lock (a read saw a half-written entry) and passes after.

**Fixed in Phase A3 (gateway and tools):** a tool call that fails after it was allowed is
recorded; the gateway runs inside an event loop; agents aren't offered tools their data classes
would refuse; identifiers removed from tool results are counted per call; claims history matches
names ignoring case, accents, spacing and punctuation (nicknames still don't match); the
recommender's input is declared, and its manifest approved (0.4.1), as health data, because its
claim type and clause numbers can reveal a medical claim.

**Not fixed yet** (in the roadmap or noted as gaps): the scoring check misses amounts written
without cents; the fairness runner picks a claim's policy by text match and swaps first names
that are also ordinary words; `waiting()` scans every checkpoint; the catch-all in triage can hide
internal bugs as stopped claims in evals; and smaller cleanups.
