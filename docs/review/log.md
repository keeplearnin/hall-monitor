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
| Adversarial and fairness results recorded no agent versions, so they would always be stale | The adversarial, fairness and seeded-critic runners record run metadata, agents included, from one helper; the baseline runner records the agents from the calls it made |
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

**Fixed in Phase A4 (evals):** the scoring check finds whole amounts written without cents
($1,000) and still never matches part of another amount; API failures and unexpected errors in
our own code count as eval errors in every runner, not as safely stopped claims; the fairness
runner takes each claim's policy from its answer key and no longer swaps a first name or surname
that comes right before a number ("June 14"); `waiting()` reads one row per claim instead of every
checkpoint version.

All 32 findings from review 3 are now fixed, each with a test written to fail first. Nicknames
in claims history (Dan for Daniel) still don't match, as noted in the code.

## Review 4: end of Phase A

**Reviewer:** the `governance-reviewer` agent (`.claude/agents/`), whole repo, read-only, each
finding verified against the code (7 reproduced with an offline script). 10 findings, mostly gaps
the Phase A fixes didn't reach. **Fixed in:** Phase A5, each with a test written to fail first.

| Finding | Severity | Outcome |
|---|---|---|
| Adjuster rules compared USD payouts and limits with amounts in the claim's currency | High | A claim not in USD, or with no amount extracted, needs a senior adjuster to pay or deny. Its payout is capped only by their authority (logged as an HM-05 gap) |
| Denying a claim nothing was extracted from needed no authority | Medium | Needs a senior adjuster |
| Paying a high- or medium-risk claim with no flags didn't need a senior adjuster | Medium | It does now |
| NaN or infinite amounts made every routing comparison false | Medium | Amounts must be finite; a non-finite one fails validation and stops the claim |
| Routing and claims history used different name matching (accents) | Medium | One `name_key` for both |
| Claims history can cross between namesakes | Medium | Logged as a gap (HM-03): the data has no customer ID |
| Resubmitting a claim whose triage hadn't finished could race a run still in progress | Medium | Restarting now needs `restart=True` (`run --fresh`) |
| A refused hall pass and "cost unknown" stops counted as safe stops in evals | Low | Counted as errors, whatever the rest of the stop reason |
| `audit verify --expect-seq 0` skipped the anchor check | Low | Fixed |
| ADR 0001 and the review log were out of date | Low | Updated; a test checks ADR 0001 names every routing check |

Not checked by the reviewer: whether the model can actually emit NaN, SQLite contention across
processes, and the HTML one-pagers.

**Pre-PR review of Phase A5** (the same agent, on the branch's diff): 3 findings, fixed before
the PR. The "cost unknown" fix only worked alongside an API failure; the docs still promised a
payout cap that no longer applies to non-USD claims; and no test showed a NaN from the model
stopping the claim. The last one now has a pipeline test, which passed first time: the guard
already worked, and the test now proves it.

## Phase B1: agents that get it wrong

**What:** `tests/test_agent_faults.py` bends each agent's answer in 22 ways a model could get it
wrong (wrong claimant, another insured's policy, dates outside cover, another currency, invented
or double-counted amounts, payouts above the claim or the threshold, a fraud flag labelled low,
a wrong recommendation, a critic that is never satisfied), on each of the 16 claims the answer key
routes automatically, and checks every one reaches a person. It also makes each agent in turn
return nothing, the wrong type, an unfinished turn, an API failure or an unexpected error.

**What the tests broke, fixed test-first:**

| Finding | Severity | Outcome |
|---|---|---|
| An extraction with invented or inflated amounts, paid in full by coverage, routed automatically | High | The routing rule traces each claimed item to its own occurrence in the claim documents, requires the total claimed to appear there, and checks the items add up to the total |
| `run_agent` accepted any stop reason except refusal, tool use and max tokens, so `pause_turn` or a later stop reason passed a partial answer | Medium | Only `end_turn` counts |
| An answer of the wrong type (a recommendation where an extraction was asked for) crashed the stop path instead of stopping the claim | Medium | `run_agent` refuses output that isn't the type asked for |

**Known limits, pinned by tests:** a risk step that misses a fraud signal, and a coverage step
that ignores a limit but pays no more than was claimed (and no more than $2,500), still route
automatically. Code can't see either; they're logged as HM-04 gaps.

**Pre-PR review** (the `governance-reviewer` agent on the branch's diff): 5 findings.

| Finding | Severity | Outcome |
|---|---|---|
| The first amount trace only asked whether a figure appeared anywhere: an item counted twice, a total line counted as an item, or a date or year taken as an amount still routed automatically | High | Each item now uses its own occurrence and the total must appear in the documents; three new faults cover these. A figure written in both the narrative and a receipt still counts twice, so the total check is what catches a double-counted item there (logged as an HM-04 gap) |
| ADR 0001, HM-04 and the README claimed more than that first trace did | High | Reworded to what the code checks, with the remaining limit stated |
| Routing tests traced amounts in the original text, the pipeline in the redacted text | Medium | The tests use the redacted text |
| Eval scoring traced amounts differently from the routing rule | Low | Scoring uses the same rule on the same redacted text |
| The incomplete-answer test only ever failed at intake; one fault also tripped a second reason | Low | The fake takes a stop reason per agent and the test runs at every agent. The payout-above-claim check is shown on its own by "coverage: pays more than claimed" |

## Kill switch (HM-19)

**What:** each agent's owner is a named person; an owner stops an agent by appending to
`registry/stops.yaml`, with no approval, and the registry reads that file on every check.
Restarting needs the stop cleared in `registry/approvals.yaml`. [ADR 0015](../adr/0015-kill-switch.md).

**Found while building it, fixed test-first:** a refused tool call goes back to the model as an
error, so a stop made during an agent's tool rounds didn't halt its next model request (the test
saw 7 more). `run_agent` now checks again before each later round.

**Pre-PR review** (the `governance-reviewer` instructions on the branch's diff, run as a
general-purpose agent because the session started outside the repo): 6 findings, all confirmed.

| Finding | Severity | Outcome |
|---|---|---|
| A claim in progress could be paid automatically on the answer of an agent stopped after it gave it; the docs said the claim goes to a person | High | The pipeline checks every agent the result rests on just before routing. The workflow's automatic finish right after isn't checked again (HM-19 gaps) |
| The CI workflow and the kill-switch code had no code owner, so one pull request could delete a stop and disable the check, even with branch protection on | High | CODEOWNERS covers `/hall_monitor/` and `/.github/`; a test checks those paths have an owner and the stop list has none |
| An approval could clear a stop before it was made, so the stop then did nothing | Medium | Clearing a stop the list doesn't hold, for that agent, refuses every agent |
| A stop list that isn't UTF-8 raised an error the gateway didn't catch, so the refused tool call wasn't recorded | Medium | Caught as an unreadable stop list; a test checks the refused call is recorded |
| A hand-edited stop's agent name was repeated in the refusal, which reaches the audit log | Low | The agent must be an agent ID, and the message names the stop only |
| YAML keeps the last of two equal keys, so `stops: []` added at the end hid every stop on the host; "records who stopped it" overstated a self-declared ID | Low | Duplicate keys refuse every agent; the docs say the owner ID is the one given, not authenticated |

## Review 5: whole repository after the kill switch (Phase B)

**Reviewer:** the `governance-reviewer` instructions, run as a general-purpose agent on the whole
repository at the kill-switch branch. 6 findings; 4 confirmed with offline scripts and fixed
test-first, 2 plausible and logged.

| Finding | Severity | Outcome |
|---|---|---|
| `run --fresh` deleted the claim's saved state before resubmitting, so a decided claim could be paid twice and a waiting one re-triaged until it routed automatically | High | `--fresh` now passes `restart=True`, which restarts only a triage that never finished |
| The approval rules read the saved triage result, which nothing checked: an edited checkpoint could clear a fraud flag and let a junior adjuster pay; ADR 0014 said the decision's inputs were checked | Medium | The result is checked against the `result_sha256` logged at triage; ADR 0014 corrected |
| Rejections in the log named the adjuster, and a malformed submission's pydantic message repeated submitted text | Medium | Rejections name adjusters by ID; a malformed submission is summarised by error type; an unregistered ID isn't repeated |
| The model's currency and policy number were free text and reached routing reasons and corrections in the log | Medium | Both must be code-shaped (`^[A-Z]{3}$`; letters, digits and dashes); an extraction that isn't stops the claim. Tool-call arguments are still logged as written (HM-06 gap) |
| Langfuse records an exception's message on a span, so a failed model output's text could reach traces when tracing is on | Low-Medium, plausible | Logged; not yet fixed |
| A signed decision has no nonce, so it could verify again after a re-triage with an identical pack | Low, plausible | Logged in HM-05 gaps; with the fixes below a finished claim can't be re-triaged |

**Pre-PR review of those fixes:** 4 findings, all confirmed.

| Finding | Severity | Outcome |
|---|---|---|
| `submit` judged "finished" from the checkpoint alone, so a new or deleted checkpoints file let a decided claim be triaged and paid again; the docs said a finished claim couldn't be | High | `submit` refuses any claim whose evidence pack is in the audit log. A crash after that entry but before the checkpoint is saved now needs a person (HM-05 gap) |
| An unforeseen error's text went into `stop_reason`; a tool error repeats the model's tool arguments, so they reached the routing reasons and the evidence pack | Medium | The stop reason carries the error type only |
| A saved result edited until it no longer parsed crashed the review step instead of being a recorded rejection | Medium | Parsed inside the check; logged as a rejection |
| Approvals don't pin output schemas, and the API sends pydantic patterns to the model as description text, so the new currency and policy-number patterns changed what the model sees without a new approval; answers like `usd` now stop the claim | Low | Logged in HM-01 gaps; pinning output schemas in approvals is left for its own change |

## Tax ID and bank detail redaction (HM-10)

**What:** tax IDs, bank account and routing numbers (each after a label) and IBANs (checked
with their checksum) are redacted with the other identifiers.

**Pre-PR review** (the `governance-reviewer` instructions on the branch's diff): 6 findings, all
confirmed.

| Finding | Severity | Outcome |
|---|---|---|
| The labelled tax ID pattern had three adjacent `\s*`, so a label followed by a long blank run (common in OCR text) took cubic time: 24 s at 2,000 spaces, enough to hang triage | High | Every run between a label and its number is bounded; a test times 5,000 spaces after each label |
| The unlabelled EIN pattern removed invoice numbers, claim IDs and some phone numbers | Medium | Removed: tax IDs need a label (HM-10 gap) |
| "account" followed by any digits removed amounts, dates and years, and "account ending" ate the amount after it | Medium | An account number needs "number", "no." or "#" and stops before a decimal; "ending" takes four digits only |
| IBANs followed by a word, in lower case, or split by dashes were missed | Medium | A failed checksum retries shorter prefixes; dashes allowed; any case after "IBAN" |
| Common labels were missed ("EIN 12 3456789", "routing # ...", "A/C no.", "Account No. is ...") | Medium | Caught; what is still missed is listed in HM-10's gaps |
| Counts by type follow pattern order; HM-10, the controls doc and the business summary dropped "labelled" | Low | Docstring says so; the docs say "labelled" |

## Review 6: approval gate and audit log

The approval-gate and audit-log findings from review 6 (the four-reviewer review of the whole
repository after HM-10).

| Finding | Severity | Outcome |
|---|---|---|
| Two runs of the same claim at once (a redelivered message, a double click) both paid it, and two adjusters deciding at once both had decisions recorded | High | `evidence_pack` and `final_decision` are once-only per claim, checked inside the append's `BEGIN IMMEDIATE`; the second run or decision fails with a message, and a second adjuster's attempt is recorded as a rejection |
| Restoring the checkpoints file from a backup let a decided claim be decided and paid again | High | `decide` refuses a claim the log shows decided, and `status` reports it decided from the log |
| Appending a `triage_result` and an `evidence_pack` to the log made the review trust them, so a junior adjuster could pay a fraud-flagged claim and `verify` stayed OK | Medium | The pack's entry names the result it was built from, and the review checks those; a second pack appended by hand makes the claim undecidable, and the adjuster is shown the pack stored at triage |
| A rejection logged an unkeyed hash of the whole submission, reason included, so a guessed reason could be confirmed | Medium | Logged as a keyed hash |
| The final decision's signature covers the reason, so with the public key it confirms a guessed reason | Medium | Logged in the HM-06 gaps and ADR 0014; not yet fixed (it needs a change to what is signed) |
| `waiting()` crashed on a new checkpoints file | Low | Creates LangGraph's tables first |
| The audit CLI crashed on a file with no `audit_log` table, exiting 1 ("no log") rather than 3 ("tampered") | Low | Reports it as not a readable log, exit 3 |

**Pre-PR review of those fixes:** 4 findings and 1 smaller one, all confirmed and fixed.

| Finding | Severity | Outcome |
|---|---|---|
| `status` trusted a `final` in the checkpoint before the log, so an edited checkpoint took a waiting claim out of the queue and showed a payout nobody approved | Medium | The log decides; a checkpoint decision it doesn't hold, or that differs from it, is reported as a problem |
| Two runs of a claim going to an adjuster both recorded a pack, leaving it undecidable with a message that read as tampering | Medium | The pack is once-only too, and its entry names the result it was built from |
| With two packs recorded, the adjuster was shown the checkpoint's copy | Low | Shown the pack stored at triage |
| The audit CLI called a log another process held locked "tampered" (exit 3) | Low | Busy is exit 4 |
| A lost race in `review decide` or `run` ended in a traceback | Low | One-line message, exit 1 |
