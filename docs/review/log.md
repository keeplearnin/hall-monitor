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

**Not changed, and why:** audit events are still written after triage completes rather than as
each step happens, and adjuster reasons are still stored as written. Both are listed as gaps
(HM-06) and noted in ADR 0008 as production changes.
