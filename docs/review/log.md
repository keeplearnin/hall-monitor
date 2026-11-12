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

## Review 6: whole repository after HM-10 (amount trace)

**What:** four reviewers (change control, workflow and audit, runtime data controls, docs)
read the whole repository at `redact-tin-bank`. Their findings are fixed in groups, one PR
each; this entry starts with the amount trace.

| Finding | Severity | Outcome |
|---|---|---|
| The amount trace matched bare figures, so a year from a date (2026 in 2026-08-14), taken as the only item, the total and the payout, traced and was paid automatically on all 16 automatic test claims; ADR 0001 said a figure from a date went to a person | High | Routing counts only figures marked as dollars ($, US$, USD), never part of a date, time or reference; a fault test bends every automatic claim this way |
| The currency was the intake's label and nothing checked it against the documents, so a claim written in euros and labelled USD was paid 1:1 | Medium | Fixed by the same rule: euro figures don't trace. A bare "$" meant as another currency still counts (HM-04 gap) |

**Pre-PR review of that fix:** 4 findings, 3 confirmed and 1 plausible, all fixed.

| Finding | Severity | Outcome |
|---|---|---|
| A "$" or "USD" counted even with another currency code beside it: "CAD $450.00", "$450.00 CAD" and "EUR 450 USD" claims were still paid 1:1 on all 16 automatic claims | High | A figure with another three-letter code, €, £ or ¥ beside it, or a minus sign before it, doesn't trace; fault tests write every automatic claim as EUR, "CAD $" and "$ CAD" |
| The marker could sit on another line, so a bare year at a line end traced when the next line started "USD" | Medium | One space or tab at most between the marker and the figure |
| The scoring comment said its amount check followed the routing rule, but scoring accepts any currency | Low | Comment says what each check asks |
| A discount line "-$20.00" traced as $20 | Low, plausible | A minus sign before the marker stops it; "($20.00)" still traces (HM-04 gap), because the claims put ordinary amounts in brackets |

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

## Review 6: fields beside the claim text

The prompt-injection and redaction findings from review 6 that concern the claim's channel,
attachment kinds and encoded markup.

| Finding | Severity | Outcome |
|---|---|---|
| `channel` wasn't scanned for injection or redacted, yet went into every prompt; an override instruction or an email there reached the model and the claim routed automatically | Medium | `channel` must be a code (`^[a-z][a-z_]{0,39}$`) and is scanned |
| `channel` went raw into the audit log's actor, so free text from the claimant reached a log that can't be erased | Low | Fixed by the same rule |
| An attachment's `kind` was scanned but not redacted, so an email or phone number there reached the model | Low | `kind` must be a code too |
| Markup written as HTML entities, full-width brackets or with a zero-width space slipped past the detector, and `&lt;system&gt;` rendered exactly like an escaped real tag | Low-Medium, plausible | The detector decodes entities, compatibility forms and invisible characters first; `escape` escapes `&` |
| The coverage step's cited clauses are free model text the recommender receives unchecked | Low, plausible | Logged in HM-09 gaps; checking clause shape would change the coverage output schema, which needs a new approval |

**Pre-PR review of those fixes:** 6 findings, all confirmed; fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| Decoding before matching turned `&#10;` and `&#46;` into the newline and full stop the patterns stop at, so injections the old detector caught passed | High (regression) | The raw and the decoded text are both searched; a test checks decoding only adds matches |
| Codes were read with underscores as spaces, so internal tool names (`policy_lookup`) in a kind or channel weren't flagged | Medium | Codes are searched as written and with spaces |
| A 40-character code could still spell a name or an instruction (`from_jane_doe`, `approve_full_amount`), so the docs' "no free text" was false | Medium | Channel and kind must be known values |
| Only six invisible characters were dropped; direction marks, invisible operators and tag characters still split words | Low | Every format character, the grapheme joiner and variation selectors are dropped |
| Escaping `&` changes what the model receives for 5 of the 36 claims, with no approval change and published results not marked stale | Low | Logged in HM-01 gaps: approvals don't pin how requests are rendered |
| A claim file with a bad channel or kind failed with a traceback that echoed the submitted value | Low | `run` names the fields and error types only |

## Review 6: kill switch and live evals

The change-control findings from review 6 that concern operating the kill switch and the
live-eval workflow.

| Finding | Severity | Outcome |
|---|---|---|
| Once a stop was committed, CI failed for good: 443 tests failed while the stop was live and 17 after the reviewed restart, because tests ran agents from the shipped registry, asserted it had no stops and reused `stop-0001` | Medium-High | Tests run agents from a copy of the registry with no stops; a test checks no test loads the shipped one |
| Two owners running `kill_switch stop` at once could lose a stop without an error, or corrupt the file | Medium | Stops are added under a file lock and the file is replaced in one step |
| `live-eval.yml` ran any dispatched branch with the real API key | Medium | The job runs only from main in the `live-eval` environment; the key must move to that environment, limited to main, which is a repository setting (HM-17 gap) |
| An agent with the ID `pipeline` skipped every gateway check | Low | The registry refuses that ID |
| The stop command failed if any other registry file couldn't be read | Low | It reads only the manifests' names and the owners |

**Pre-PR review of those fixes:** 5 findings, 2 confirmed and 3 plausible; all fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| Replacing the stop list broke a symlink to a shared store, so the stop stayed local and other hosts kept running the agent; permissions were reset too | Medium | The path is resolved first and the file's mode kept; a test stops an agent through a symlink |
| One test still copied the shipped registry, so stopping `intake` failed it | Medium | Fixed, and the guard now forbids copying or loading the shipped registry in any test but the one that checks it; stopping every agent at once leaves the suite green |
| `fcntl` was imported at module level, so the registry wouldn't load on Windows | Low, plausible | Imported where the lock is taken |
| The replace wasn't synced first, and needs write access to the directory | Low, plausible | Synced before the replace; the directory access is logged in HM-19 gaps |
| Each test run left a registry copy in the temp directory | Low | Removed at exit |

## Review 6: approvals and the record of interrupted agents

| Finding | Severity | Outcome |
|---|---|---|
| The approved prompt hash for `coverage_check` and `critic` didn't cover the untrusted-claim notice, which they filled in at run time, so an edit to it reached the model with no new approval | Medium | The notice is in the hashed template (coverage-check-v4, critic-v3; the text sent is unchanged) |
| The policy wording filled into those prompts isn't covered by any approval | Low | Logged in HM-01 gaps; covering it would be a new kind of approval |
| A stop or an API error between an agent's tool rounds dropped the record of the requests it had already sent, so the audit trail missed them and the cost read low | Medium | The interrupted agent's requests are kept on the error and recorded |

**Pre-PR review of those fixes:** 7 findings, 5 confirmed and 2 plausible; fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| The new helper was inserted between `@tracing.observe(name="triage")` and `triage`, so claims got no triage span | Medium | Moved; a test checks the decorator wraps `triage` |
| A billed answer that fails validation inside the SDK's parse call raises before its usage is read, so it is still missing from the record and the cost | Medium | Logged in HM-01 gaps; capturing it means validating outside the SDK call |
| README's list of current prompt versions was stale | Low | Updated |
| ADR 0003 and the one-pagers said any prompt change needs approval; the policy wording doesn't | Low | Say the template is approved and the wording isn't |
| The test didn't check the interrupted call is logged once and costed | Low | It does now |
| An error object raised twice could carry an earlier claim's partial record | Low, plausible | Always set |
| A brace in the notice would break `.format` | Low, plausible | A test checks it has none |

## Review 6: docs against code

**Reviewer:** a review of the README, one-pagers, ADRs and controls register against the code.
Each finding was checked against the code before the docs changed; no code behaviour changed.

| Finding | Severity | Outcome |
|---|---|---|
| README, business summary, ADR 0013 and ADR 0015 said a change "can't merge" or "must pass" without approval and checks; main has no branch protection or rulesets, so a failing CI run doesn't block a merge | Medium | Reworded: CI checks and fails; blocking a merge needs branch protection, which isn't on. HM-17 gaps say so too |
| HM-13 said every extracted value and cited clause must trace to a source; that check runs only when published eval runs are scored | Low | HM-13 says so; at run time only amounts, claimant name, incident date and policy number are checked |
| README said the Anthropic SDK's spans stay metadata-only; that is a default nothing enforces | Low | Reworded to match HM-11 gaps |
| README, HM-10 and ADR 0009 said each agent input declares its data classes; only the recommender's does, the rest default to every class | Low | Reworded: in practice the input check constrains only the recommender |
| "19 controls, each with its code, tests and evidence": 10 have no evidence | Low | README and register header say evidence is listed where it exists |
| Business summary implied any change to the record shows up; a consistent rewrite of the whole chain doesn't, without an outside copy of the chain head | Low | Added |
| ADR 0014 and `signing.py` said the system holds public keys only and can never make a decision; the demo keys are derivable and the review CLI signs with them | Low | Narrowed to real adjusters; demo keys derivable by design |
| README said only a senior adjuster decides a claim whose amount can't be compared with USD limits; a junior may still investigate or ask for information | Low | Narrowed to paying or denying |
| Business summary said any undecided claim that used a stopped agent's work goes to a person; the automatic finish after routing isn't rechecked | Low | Narrowed to claims not yet routed |
| Board brief and business summary said the AI sees only that claim's records; claims history is matched by name, so namesakes share it | Low | Caveat added (HM-03 gap) |
| Board brief and README said "AI correction" goes to a person; only critic changes to decision fields (or unresolved disagreement) do | Low | Narrowed |
| README said 14 ADRs; there are 15 | Low | Fixed |
| README and business summary labelled the current system "after milestone 9" and "after milestone 10" | Low | Now "after milestone 11 and Phase B so far" |
| README, business summary and ADR 0013 listed only milestones 4-8 as unscored | Low | Milestone 11, Phase B and the tax-ID and bank redaction added; published routing figures noted as from an older, less strict rule |
| README roadmap had no entry for Phase B | Low | Entry added for fault injection, the kill switch and the tax-ID and bank redaction |
| RI-27 said checkpoints aren't integrity-checked; the evidence pack and triage result in them are | Low | Reworded: other checkpoint state isn't |
| ADR 0009 said claims history carries health; `tools/server.py` declares pii, health and financial | Low | Fixed |
| HM-10 gaps said "account" with no number/no./# is missed, but "account ending 1234" is caught, and didn't mention the 4-17 digit limit | Low | Reworded |
| README Stack listed pgvector, DeepEval and FastAPI; none is a dependency or used | Low | Removed; the stack lists what is used |

The same stale "predate milestones 4-8" claim in `docs/executive-brief.html` and
`docs/architecture.html` is corrected too.

## Review 6: end-of-phase review of the fixes together

**Reviewer:** a review of the six fix pull requests together, at the end of the phase. The
runtime findings were fixed in code test-first; the change-control and docs findings changed
docs, comments and `.github/CODEOWNERS` only.

**Runtime**

| Finding | Severity | Outcome |
|---|---|---|
| A traced function's exception text, which can repeat the model's output, reached Langfuse spans | Medium | Fixed: the span is marked as an error by type only (HM-11) |
| The claim ID was free text reaching every audit entry, the recommender's input and traces | Medium | Fixed: it must be a code (HM-06, HM-09) |
| More ways of writing another currency beside "$" (HK $, (CAD) $, Cdn $, $450 cad, Unicode minus) traced as USD | Medium | Fixed; a bare "$", a code not in the list or a currency named only in a header still trace as USD (HM-04 gaps) |
| An adjuster's request_information or investigate is the claim's final decision, so the workflow can't triage it again with new documents | Medium | Logged in HM-05 gaps; a follow-up path is a design decision |
| Injection detection ran before redaction, which can join words into a phrase only the agents read | Low | Fixed: both versions are scanned (HM-09) |
| `audit show` crashed on an unparseable entry | Low | Fixed: reported as tampered with, exit 3 (HM-06) |
| `review decide` crashed with a traceback on an impossible payout | Low | Fixed: refused with a message naming the fields (HM-05) |
| A claim whose evidence pack was logged but never checkpointed said in_progress | Low | Fixed: it says needs_a_person (HM-05) |
| `run` and `review` can't select an audit log other than `var/audit.sqlite` | Low | Logged in HM-05 gaps |
| With model routing on, the recommender's economy choice uses the extraction before critic corrections | Low | Logged in HM-16 gaps; a correction that would change the choice sends the claim to a person anyway |

**Change control and docs**

| Finding | Severity | Outcome |
|---|---|---|
| `evals/results/` and the answer keys had no code owner, yet the gate trusts the agent digests each result file records and the answer keys drive `unsafe_routes` | Medium | `/evals/` and `/data/synthetic/` given the same owner; HM-17 gaps say the gate doesn't check a result's digests against its commit |
| Approvals don't pin the tool definitions sent to the model or the reviewer-feedback text | Low | Logged in HM-01 gaps; the one-pagers' caveat covers them |
| A sentence about billed answers that fail validation sat in HM-01's gaps, breaking its flow | Low | Moved to HM-06 gaps |
| The executive brief said "after milestone 10", stopped its roadmap at 10, sent "any AI correction" to an adjuster and had no caveat on changed instructions; the architecture page lacked HM-19 and said every agent input is checked against data classes | Low | Fixed to match README and the register |
| `approvals.py` and CLAUDE.md said a test fails unless a prompt change has a new PROMPT_VERSION; the test only checks approvals.yaml is regenerated | Low | Say the version bump is a review rule |
| ADR 0015 said a claim is never paid on a stopped agent's answer; ADR 0001 said a threshold change is gated | Low | Narrowed: the automatic finish isn't rechecked; CI checks, nothing blocks a merge yet |
| ADR 0013 said every run is committed with its raw results; `raw_results.jsonl` is git-ignored and live runs are uploaded for a person to choose from | Low | Fixed |
| README said 10 controls have no evidence (13 have none published), its quickstart baseline overwrote the published 6-claim result, and its layout omitted the owners and stop lists and `kill_switch.py` | Low | Fixed; the quickstart writes to `evals/results/my-baseline` |
| `check-append-only` crashed on a malformed stop list | Low | Fixed: fails with a message (HM-19) |
| The CI workflows pin third-party actions by version tag, not commit SHA, and live-eval passes the API key in the same job | Low | Logged in HM-17 gaps |
| HM-05 said a second run "can't triage" a claim; its gaps say both runs call the agents | Low | Says the second run's result isn't recorded |

**Review of those fixes:** 5 findings, 4 confirmed and 1 plausible; fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| A listed code still traced as USD with a non-breaking space, several spaces, a full stop, comma or equals sign between, or written "$450 in CAD" or "$450 Canadian": five automatic claims written that way were paid 1:1 | High | Any space but a line break, and common punctuation, count as a separator; "in CAD" and currency names are recognised; fault tests write every automatic claim with a non-breaking space, "Cdn." and "in CAD" |
| The case-insensitive list caught ordinary US text ("Denver, CO $450", "$450 co-pay", "Taxi – $45"), sending those claims to a person | Medium | Two-letter forms that are also words or state codes are left out, and only a minus touching the mark counts; tests pin both directions; what the list misses and refuses is in HM-04's gaps |
| Eval variants' claim IDs ("claim_029+adv_01", "claim_001~anglo") broke the new claim-ID rule because the evals copy claims without validation | Low | They use "-"; the gate still reads the old separators in published results |
| `status` can report `needs_a_person` for a moment between logging the pack and saving the checkpoint, seen from another process | Low, plausible | Logged; it is transient |
| `check-append-only` crashed on a malformed base stop list | Low | Refused with a message, like a malformed head |

## Review 7: the control panel (HM-20)

**Reviewer:** the governance-reviewer agent, on the control-panel branch before its pull request.
13 findings: 11 confirmed, 2 plausible. All fixed, test-first, and the fixes reviewed again
(below); the limits that remain are in HM-20's gaps.

| Finding | Severity | Outcome |
|---|---|---|
| The case trace showed every payload as logged, so tool-call arguments and tool errors as the model wrote them (a name sent as a policy number reached the page) and policyholders' policy numbers; HM-20, the README and the footer said nothing personal was shown | High | Each event shows an allow-list of fields; tool arguments, tool errors, corrected policy numbers, unknown fields and unknown events are withheld; reasons are shown only if they match the code's own templates |
| `snapshot` wrote `cases/{claim_id}.html` as given, so a claim ID of `../../../index` wrote outside the folder, and IDs with `?`, `#` or `/` aborted it | Medium | Only IDs matching a safe pattern get a page or a link; the write path is checked to stay inside the folder |
| A payload that no longer parsed, or a file that isn't a database, made the overview and case pages fail with a server error, hiding the "doesn't verify" warning on the very log that needed it | Medium | Every page reports a log that isn't a database or doesn't parse as unreadable and points to the audit page, which reports the failure (entries with fields of the wrong type: see the re-review) |
| The queue listed any claim with an evidence pack and no decision, including one routed automatically whose decision was never recorded, which the review CLI refuses | Medium | The queue holds claims routed to a person; the others show as needing a person, on the overview too |
| The demo copied approvals that clear a stop but reset the stop list, so once the real registry had a cleared stop every demo agent was refused and the build crashed; a refused claim could also be decided twice | Medium | The copy drops the clearances with the stops; the junior adjuster's decision counts when it stands |
| The read-only test couldn't see a writable open: a writable `AuditLog` leaves the files unchanged, and the AST check matched only the bare name | Medium | A test records every database opened while reading every page and requires read-only; the ADR says what the byte check covers |
| `testserver` was an allowed host in production, so a spoofed single-label name could reach the panel | Low, plausible | Only `localhost` and `127.0.0.1`; the snapshot's client uses `localhost` |
| `serve --host ::1` started but refused every request | Low | IPv6 is refused at start, with the reason |
| A non-ASCII digit in the anchor (`²`) gave a server error instead of a refusal | Low | Only ASCII digits are accepted |
| A broken approvals file made the eval page fail with a server error | Low | The page says the gate can't run on these files |
| Reasons with figures other than "payout above" were counted one row per amount | Low | Every template with figures is grouped without them |
| A saved snapshot had no security policy, since that was only an HTTP header | Low, plausible | Every page also carries it as a meta tag |
| The README said no page shows stop reasons, but the case page shows the pipeline's stop reason | Low | It says kill-switch stop reasons |

**Re-review of those fixes:** 8 findings, 7 confirmed and 1 plausible; all fixed.

| Finding | Severity | Outcome |
|---|---|---|
| An entry that still parsed but had fields of the wrong type (`routing_reasons: 5`) still gave a server error on the overview, case and queue pages | Medium | Every shown value is checked against what the code writes; a test changes each field of each event to every wrong type and requires every page to render |
| HM-20, the README and the footer said nothing the model wrote is shown, but typed values it chose (amounts, dates, enums, a currency code) are | Medium | They say no free text the model wrote; HM-20's gaps list the typed values shown |
| Allow-listed fields passed any value through, so text written into one by hand reached the page | Low | A shown value must look code-written or it is withheld; HM-20's gaps say this is a backstop for a log that doesn't verify, not a personal-data filter |
| `serve --host 127.0.0.2` started but refused every request | Low | Only the two addresses the host check answers to |
| Two claim IDs differing only in case overwrote each other's pages on a case-insensitive disk | Low | The snapshot refuses them |
| A case's route came from the last triage result, not the one its evidence pack records, so a run that lost the race could hide a claim from the queue | Low, plausible | The route is from the result the pack records, as the workflow reads it |
| The reason-template test was a hand-written list with injection codes that don't exist | Low | A test builds reasons from the real routing rule, refusals and cost error |
| The review log and a docstring still described the first fixes as complete | Low | Corrected |

## Review 8: the HR leave rules (HM-21)

**Reviewer:** the governance-reviewer agent, on the hr-leave-rules branch before its pull
request. It checked the rules against its reading of 29 CFR Part 825 (not the text itself), and
recomputed 18 answer keys by hand. 18 findings: 11 confirmed in the code, 7 where the code's
behaviour was shown but the legal premise is the reviewer's reading. All fixed, test-first, or
logged in HM-21's gaps.

| Finding | Severity | Outcome |
|---|---|---|
| Leave was counted as calendar days over 7, so a Monday-to-Friday week was 0.71 weeks and a single day a seventh: a request over the entitlement went to specialist sign-off with no flag | High | Leave taken and asked for is counted in workdays of the employee's normal workweek (825.205(b)) |
| Forward-from-first-leave used only leave start dates, so a leave running past a period's end counted nowhere, and a request over the entitlement went to sign-off | High | Periods follow each day of leave: the next period starts on the first leave day after the last one ends |
| Any military caregiver request more than 12 months after earlier caregiver leave got a fresh 26 weeks, though the 26 are per servicemember and injury, which the records don't say | High | That entitlement can't be decided and goes to a person (`caregiver_history`) |
| "The records hold no names and nothing medical" was false: the reason category, an adult child's disability and the accommodation flag are health information, and the test only denied seven field names | High | The docs say what health information is held and how coarsely; a test fixes the exact list of fields |
| An employee with no employer record, a request for an unknown employee, a request with no answer key, and a fixed year starting 31 April crashed the run | Medium | Each goes to a person or is refused when the records load |
| Overlapping employment periods counted twice | Medium | Refused when the records load |
| Missing facts were filled in: a missing coverage year was 0 weeks, a missing distance was far, a week missing from the ledger was 0 hours, so an HR manager was shown a reason for ineligibility the records don't support | Medium | Each is "can't decide"; every pair of sites must have a distance; a payroll week the ledger doesn't cover is unknown (825.110(c)(3)) |
| The spouse flag missed military caregiver leave (825.127(f)) | Medium | Flagged; the code is now `spouses_same_employer`, worded for spouses |
| The state-law flag ignored where the employee lives | Medium | Worksite or home state |
| The bonding window was all or nothing, though leave inside the 12 months qualifies up to their end | Medium | The reason fails only if the leave starts after them; leave running past them goes to an HR manager |
| Service was counted in days, though any week on the payroll counts (825.110(b)(3)) | Medium | Counted in payroll weeks; the boundary requests moved to a Saturday and a Sunday hire |
| The ADR and register said every rule was tested on both sides of its boundary, and every flag on a clean request | Medium | Boundary tests added for the entitlement, the caregiver period and the coverage year, and on one side for the forward period's end and the fixed year's first day (the other sides came with the re-review); the docs say what is tested |
| The role table was presented as the design's, but the design isn't in the repo, six roles are the code's choice, a role is a minimum not an exclusive, and nothing enforces it yet | Medium | The docs say all three; HM-21's gaps say more leave than is left goes to a director though a manager could deny the excess |
| Data minimisation claims were false: `home_state` was unused, `family_member_age` was exact, and the check facts carried a child's birth date | Low | `home_state` is used for the state flag; age is under 18 or not; the facts say only whether leave starts inside the bonding year |
| An anniversary year from each employee's hire date can't be represented | Low | Logged in HM-21's gaps |
| Coverage is judged on the notice year, which wasn't stated | Low, plausible | Stated as an assumption |
| Leave that becomes FMLA leave once the employee reaches 12 months during it (825.110(d)) isn't split out | Low, plausible | Logged in HM-21's gaps |
| Routing relied on each check setting a code; the spouse wording was for parents only; the key-employee comment omitted "salaried"; "other" relationships (someone in place of a parent) failed instead of going to a person | Low | Routing is on each check's answer; wording fixed; "other" can't be decided |

**Re-review of those fixes:** 10 findings, 7 confirmed and 3 where the legal premise is the
reviewer's reading; all fixed. Reverting each review 8 fix in turn, 21 of 23 were caught by a
test; the 2 that weren't now are.

| Finding | Severity | Outcome |
|---|---|---|
| While a military caregiver period was open, other leave was checked only against the employer's leave year, so 22 weeks of caregiver leave still left a full 12 for other reasons and a request over the combined 26 went to sign-off | High | Other leave is held to the 26 less all leave in the open period as well (825.200(f)) |
| Caregiver days counted against the 12 weeks for other reasons, sending a request that fits to an HR director | Medium | The 12 count leave for other reasons only |
| Counting requested leave in calendar days could be reverted unnoticed: every test request ran Monday to Sunday | Medium | A Monday-to-Friday request against 0.8 weeks left |
| Birth bonding starting before the birth passed as bonding | Medium | It can't be decided: before a birth it is prenatal care or a health condition; leave before a placement still qualifies |
| A missing home state was taken as "not a state with its own law", and a missing worksite skipped the home state | Low | Every record has a home state; a missing one can't be decided, and both are checked |
| Overlapping leave already taken counted twice | Low | Refused when the records load |
| A period ending, or a leave starting, on a Sunday wasn't tested | Low | Tested |
| The field-list test missed the records nested in an employee's, and leave already taken wasn't listed as health information | Low | Nested records' fields are fixed too; the docs list it |
| The docs said a child's age is only under 18 or not, though bonding requests carry the birth date; "18 problems, all fixed" though two were logged; boundary tests on both sides that were on one; an "11-year break" that is almost 11 | Low | Corrected |
| An unknown leave-year method was treated as forward-from-first-leave | Low | It can't be decided |
| Bonding from 29 February ended a day early (plausible) | Low | It runs to the end of 28 February |

## Review 9: the HR leave agents (HM-22)

**Reviewer:** the governance-reviewer agent, on the hr-agents branch before its pull request,
with offline probes against the fake model client. 9 findings: 8 confirmed, 1 plausible. All
fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| `leave_assessed` logged plain SHA-256 hashes of the extraction and the result; with so few possible values, a guess found the full extraction (reason category, family member, dates) in 2.3 seconds | High | Keyed hashes (HMAC) for the message, extraction, result and note; a test checks the logged value isn't the plain hash |
| No catch-all: an unexpected error, or an extreme date from the model (year 1, year 9999), crashed the pipeline with no route and no `leave_prepared` entry, and aborted the eval | High | Every step after intake and both agent calls catch any error and send the request to a person; dates outside 1900 to 2199 are refused when the answer is parsed |
| The explanation's `dict[str, str]` became an object with no properties in the schema the API is sent, so a live model could only answer `{}` and every note would be dropped | Medium | A list of notes with a fixed set of check ids; a test runs the SDK's schema transform on it |
| The docs said neither agent can change the result, but the rules run on what intake extracts | Medium | They say intake's facts shape the result and the eval measures it; the explanation can't change it |
| A model with no price set `stopped_reason` without stopping the request | Low | A cost that can't be shown stops it |
| The injection detector didn't know the HR tags or HR phrasings | Low | It knows the HR tags and a few HR phrasings; the gap says it is still a phrase list |
| "Each agent's hall pass allows exactly the data classes it is sent" and "never the employee" overstated: the test compared each manifest with a hand-written constant, and the explanation's input holds employment facts no class covers | Low, plausible | Reworded to what is checked; the employment facts are named |
| The eval didn't load `.env` or flush traces, unlike every other eval | Low | It does both |
| Explanation failures weren't counted anywhere, and a stopped explanation agent was described as stopping the request | Low | The eval counts them; the docs say a failed explanation only leaves the note out |

**Re-review of those fixes:** 8 findings, all confirmed; all fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| Every `agent_call` entry, and every trace, still held `input_sha256`, a plain hash of what the agent was sent; for the explanation agent a guess recovered the reason category and family member in about 3 seconds | High | The HR log keeps it as a keyed hash and HR traces leave it out (`run_agent(trace_input_hash=False)`); the claims agents still log and trace it, which matters for the recommendation agent's structured input: logged in HM-06's gaps |
| The docs said no plain hash was logged, and the test skipped `agent_call` entries | High | The test checks every entry for the request; the docs say only the prompt templates are hashed plainly |
| "hr" in the avoid-review pattern flagged an ordinary privacy request ("don't forward my note to HR") as text aimed at the AI | Low | Removed |
| The new shared patterns could flag legitimate claim text (a pasted `<message>` tag, an HR manager's approval of business travel) | Low | `message` removed from the tags; the rest logged in HM-22's gaps as failing safe |
| The date validator was untested: the test built its bad answer with `model_copy`, which skips validation | Low | A test parses each out-of-range date |
| "Implausible date" overstated what is refused | Low | "A date outside 1900 to 2199" |
| The branch in `stop` that kept the code's route was unreachable | Low | Removed; an unknown cost after routing keeps the reasons, as the docs now say |
| An unknown cost dropped a good note without saying why | Low | The note's problem says the request stopped |

## Review 10: the signed HR sign-off (HM-23)

**Reviewer:** the governance-reviewer agent, on the hr-signoff branch before its pull request,
with offline probes and a mutation run. 14 findings, all confirmed; all fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| A leave specialist could approve 0.01 of the weeks asked with no reason: a final partial denial by someone not allowed to deny | High | Approving fewer weeks than asked is a partial denial: HR manager or above, with a reason |
| The chain check ignored HR evidence packs, so a changed or erased leave pack verified clean, though HM-06 says it reports any changed pack | High | `verify` checks `leave_evidence` entries too |
| Asking for more information skipped the role check but was the request's one final decision, so a specialist could close a director's case | Medium | It isn't a decision: logged as `leave_information_requested`, and the request keeps waiting |
| An extension had no limits: any weeks, on a request within the entitlement, for an ineligible employee | Medium | Only for an eligible employee past the entitlement, up to the weeks asked |
| Approval counted bonding weeks after the 12 months, which the code knows don't qualify | Medium | The assessment carries the weeks that qualify, and approval can't exceed them |
| Nothing put a request into the sign-off: the only caller of `prepare` passed no log | Medium | `python -m hall_monitor.hr.run` prepares requests into the log (it calls the API) |
| A run that lost the race to prepare a request logged a different route, which `status` showed to HR | Medium | `status` reads the route from the evidence pack |
| A decision that lost a race left its reason in the store and logged no refusal | Low | Only the winning decision is stored; the loss is logged |
| "A refused decision is logged" overstated: already decided, erased or changed evidence and a lost race weren't | Medium | Each is logged now, for any request in the log |
| Seven checks could be removed with every test still passing, though the ADR said each was tested | Medium | A test for each; the ADR says the removal check was by hand |
| HM-22 said the specialist checks the message, but the pack had no message | Medium | The pack holds the redacted message |
| The HM-23 gap understated what the pack holds | Low | It lists every kind of personal and health information in it |
| `status` crashed when a request had evidence but no `leave_prepared` entry | Low | It reads the pack, not that entry |
| The review CLI crashed on bad input, echoing it, and a duplicate staff ID silently changed a role | Low | Clear refusals without the values; a duplicate ID or unknown role refuses the file, naming the entry |

**Re-review of those fixes:** 13 findings, all confirmed; all fixed.

| Finding | Severity | Outcome |
|---|---|---|
| The partial-denial rule only applied when weeks were asked, so a leave specialist could approve 0.01 weeks of intermittent leave as a final decision | High | For intermittent leave, approving fewer weeks than are left is the partial denial; intermittent bonding leave past its year isn't approved in this step |
| The partial-denial test passed with the manager check deleted | Medium | A test where a specialist gives a reason and is still refused |
| The signed decision was stored after its log entry, so a failure between them left a logged decision with no record | Medium | Stored first; a decision that lost a race has its record removed, unless it is the winner's identical record |
| An outage during `hr.run` left every request with a pack the rules never ran on, permanently, since preparation is once-only | Medium | A failure of the intake call leaves no pack; the request is listed as stopped and can be prepared again |
| Refusals named a staff member without checking their signature | Low | Named only if they signed it |
| An information request could be replayed, and nothing kept what was asked or who signed | Low | Stored, signed, and recorded once |
| `show` failed and `list` kept an erased request waiting forever | Low | Both report the evidence problem; `list` separates such requests and stopped ones |
| `hr.run` stopped at the first request already prepared, without saying what it had done | Low | It skips it and goes on, reporting each |
| A bad staff file echoed its values or crashed | Low | Refused naming the problem, without values |
| An extension could grant fewer weeks than were left | Low | An extension's weeks are the total granted and must exceed what is left |
| Five changes went unnoticed by every test (the empty-entitlement extension, the no-pack refusal, the erased-pack status, the bonding year's last day, the CLI's staff-file refusal) | Low | A test for each |
| README rows for HM-21 and HM-22 still said there was no sign-off | Low | Corrected |
| "Every refused decision is logged" covered the CLI's input refusals too | Low | It says those aren't decisions and aren't logged |

## Review 11: leave requests in the control panel (HM-20)

**Reviewer:** the governance-reviewer agent, on the panel-hr branch before its pull request,
with offline probes and a demo build. 9 findings, all confirmed; all fixed.

| Finding | Severity | Outcome |
|---|---|---|
| A leave request's trace showed the employee ID beside reason codes that reveal pregnancy, disability, a child's condition or pending discipline, though HM-20 said the panel didn't show the category | High | No employee ID or weeks on a leave trace; a request's sensitive reasons show as "sensitive (HR only)"; the overview still counts them across requests, and HM-20 says so |
| A leave refusal logged under a claim's ID (a mistyped HR command) turned the claim into a "leave request" and took it out of the claims queue | High | A leave request is one with a logged message; the sign-off refuses any other ID without logging |
| The refusal test exercised 17 of the 25 refusal templates | Medium | A test drives every refusal the code writes and checks every template is matched by one |
| No test checked that HR stop and note-problem messages were shown, and their templates were a positional slice of the claims list | Medium | A test drives every pipeline stop; the templates are named |
| A request stopped before the rules counted as sent to HR review | Low | Routes and reasons count only requests that reached HR |
| A request with an erased pack showed as waiting, though the sign-off refuses it | Low | Shown as "evidence pack erased or changed" and kept out of the queue, with an attention line |
| ADR 0016 said the panel never opens an evidence pack, but the integrity check hashes them | Low | Reworded |
| A non-finite number in a payload broke the JSON routes | Low | Shown as withheld (claims too) |
| A mixed-case staff role made its refusal unmatchable; two templates accepted any text for an ID; the demo banner didn't mention leave requests or demo keys; the privacy test missed redacted messages | Low | Roles must be exact; IDs match the safe ID pattern; the banner and the test are fixed |

**Re-review of those fixes:** 10 findings, 9 confirmed and 1 plausible; all fixed or logged.

| Finding | Severity | Outcome |
|---|---|---|
| Masking wasn't enough: a request's role needed, failed and unknown checks and the count of masked reasons still let a reader work out the reason, exactly for retaliation risk | High | A single request shows no reasons, role needed, checks, weeks or rule-naming refusals at all; only the overview counts reasons |
| Every leave view ran the integrity check, which crashed on a stored pack that isn't JSON, taking down the queue and every leave page | Medium | `verify` reports an unreadable pack as a problem; the panel caches the result until the log changes |
| The stop test didn't drive hall-pass, stop-list, model-call or invalid-extraction stops | Medium | A test drives each |
| Four fixes had no test (exact roles, the ID pattern, a changed pack, the attention line) | Low | A test for each |
| HM-20 said a trace shows no weeks, but the decision's weeks did | Low | Weeks are left off a single request |
| The docs said no leave view reads a pack, but the integrity check reads them all | Low | Reworded |
| A leave request missing its submitted entry showed as a claim | Low | Any event the leave pipeline writes marks a leave request |
| The cost per request left stopped requests out of the count but not the cost | Low | It divides by every request prepared |
| The integrity check ran on every page | Low | Cached until the log changes; the ID pattern is taken from `SAFE_ID` |
| A request's ID and times still let someone who knows when a colleague asked find it | Low, plausible | Logged in HM-20's gaps; the request shows no reasons |

## Review 12: the claims agents' input and tool-result hashes (HM-06)

**Reviewer:** the governance-reviewer agent, on the fix-claims-input-hash branch before its pull
request, with offline probes. The branch closes the gap Review 9's re-review logged: the claims
agents logged and traced `input_sha256`, a plain hash of what each agent was sent, and the
recommendation agent's input is a structured summary a guess could confirm. The log now keeps it
keyed (`input_hmac`), no trace carries it, and the panel doesn't show the plain field from older
logs. 3 findings, 2 confirmed and 1 plausible; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| Tool results go to the agents too, and their hash was logged plain and shown on the panel; for a policy with no earlier claims `claims_history` returns only the insured's name, and an offline probe recovered the name for 32 of 36 synthetic policies in 0.34 seconds from the hash and a list of names. This predates the branch, and the branch's docs ("each agent call's hash of what the agent was sent is keyed") overstated | High | The log keeps it keyed (`result_hmac`); the panel shows only that and leaves out `result_sha256` from older logs; HM-06, ADR 0008 and the README say so, and HM-06's gaps say older logs still hold both hashes plain |
| The register and ADR 0008 cited Review 12 before it existed | Low | This section |
| The input-hash test checked only `agent_call` entries, by key name | Low, plausible | It also checks that no plain input hash appears in any entry |

The HR pipeline keys `keyed_hash(_canonical(h))` and claims key `keyed_hash(h)`; `_canonical`
returns a string unchanged, so both give the same value. The LangGraph checkpoint still holds the
plain input and tool-result hashes, beside the claim and the policy record in clear, and the
published eval results hold the input hashes for synthetic claims (in HM-06's gaps).

**Re-review of those fixes:** no findings. The name probe found no plain or name-only hash in the
log or on any panel page, across three full claims and every policy's tool calls; each new test
fails with its fix undone. Not checked: a live run in which an agent asks for tools (the fake
client never does), so the tool-call path was exercised through direct gateway calls.

## Review 13: the leave submission form (HM-24)

**Reviewer:** the governance-reviewer agent, on the leave-submit branch before its pull request,
with live probes of the demo app and checks undone in memory. Before the review, a check in a
real browser found that the pages' no-referrer policy made the browser post the form with
`Origin: null`, so every real submission was refused; the pages now send a same-origin referrer
policy, and a test checks the header. The review found no way past the token, origin, host or
body checks. 5 findings, 4 confirmed and 1 plausible; all fixed.

| Finding | Severity | Outcome |
|---|---|---|
| The large-body test exercised only the declared length; with the streaming limit (the only one for a chunked body) removed, every test passed | Medium | A test posts a chunked body over the limit with no declared length |
| The README said the form takes a submission "only from its own page", and the gap said "anyone who can use a browser": any process on this machine can read the token and send any origin | Medium | The README, ADR 0020 and HM-24's gap say the check stops other sites posting through a browser, not users or processes on this machine |
| The 32 KB body limit refused valid messages: URL-encoding makes a character up to 9 bytes (6,000 "é" got "too large"), and a browser's CRLF line breaks pushed a message the textarea allowed over 10,000 characters | Low | The limit is the room the longest message needs; line breaks are counted as the textarea counts them; a test sends both |
| The content-type check and the repeated-field refusal had no test, and nothing showed a refused submission doesn't use up the spend cap | Low | A test for each |
| HM-24 was mapped to GOVERN 1.6 (inventory), and a stopped request told the submitter "nothing was kept", though the log keeps its keyed hash | Low, plausible | Mapped to MANAGE 4.1 only; it says the message itself wasn't kept |

**Re-review of those fixes:** no findings. Each check undone in memory (the streaming limit, the
content-type and repeated-field checks, the spend cap's place and its increment, the line-break
handling, the old limit) fails a test; probes found no way past the 10,000-character limit, with
the worst-case 9-byte characters and with line breaks. Not checked: live mode against the API.

## Review 14: the no-reply, no-send rule (HM-25)

**Reviewer:** the governance-reviewer agent, on the complaint desk's first slice before its pull
request, with probes run against the static check and the egress guard (no network, no API).
The URL guard held: case, trailing dots, encoded and plain dot segments, ports, userinfo, http
and redirect hops were all refused, and a model client re-pointed elsewhere was refused before
anything was sent. 7 findings, 5 confirmed (2 split confirmed and plausible); all fixed,
test-first.

| Finding | Severity | Outcome |
|---|---|---|
| egress.py's policy and rule types, and the httpx it imports, could be used from any module: a policy built elsewhere sent a GET with data to another host through the guard, and "two policies exist, and no others" was false | High | A client can be made only with the two shipped policies (an equal copy is refused); importing egress's internals elsewhere fails the static check |
| HTTP writes got past the check: `request(method="POST")`, `stream("POST")`, `build_request("POST")` with `send`, `.delete`, `getattr(c, "post")` | High | Each is flagged; reading a request body (`request.stream()`) and GET calls are not |
| Plain ways past the raw-network, dynamic-import and shell rules: `asyncio.open_connection`, an aliased `import_module`, `from os import system`, `os.posix_spawn` and `os.execvpe` | High | Raw connection calls are flagged; importing `import_module` is flagged; any `os` function that runs a program is flagged however it is imported |
| fastmcp's `Client` can reach a remote MCP server, and the gateway took a URL as its server (a test run tried a DNS lookup) | Medium | The gateway takes only an in-process `FastMCP` server; an MCP client pointed at a URL, or fastmcp's client transports, fail the check |
| `verify` never checked a stored draft, so a draft swapped for one saying a refund was sent passed | Medium | A draft is an evidence entry (`evidence_sha256`): verify reports a changed draft and an erased one |
| The model policy matched by prefix, allowing `/v1/messages/batches` and `/v1/messagesXYZ` | Low | It allows the messages path exactly |
| Send words weren't found in camelCase, hyphenated or run-together names | Low | Names are split into words first, and the strongest words match anywhere in a name |

The docs now call the static check a tripwire for the honest mistake, not a proof: a method name
held in a variable or `getattr` with a computed name still gets past it (in HM-25's gaps).

**Re-review of those fixes:** each new test fails with its fix undone. 4 findings, 3 confirmed
and 1 plausible; the confirmed ones fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| The egress-internals rule saw only the literal name `egress` and absolute imports: `import hall_monitor.egress as e`, `from hall_monitor import egress as eg`, the full dotted name and `from .egress import httpx` scanned clean | High (docs claim false) | The check tracks every name bound to egress.py, dotted names and relative imports |
| A fastmcp proxy is a `FastMCP` subclass that forwards tool calls to a remote server, and passed the gateway's check; `fastmcp.Client(url)` and `Client(URL)` with a variable scanned clean | Medium | The gateway takes only a plain `FastMCP`; fastmcp's client, proxies and client transports fail the check anywhere but the gateway, however they are imported |
| The shipped-policy check ran only when a client was made, so a policy swapped onto it afterwards was used | Low | It is checked again on every request |
| Matching send words anywhere refuses harmless names (`consent_lookup`, `delivery_status_lookup`) | Low, plausible | Kept: the intended direction of error, stated in HM-25's gaps |


## Review 15: complaint ingestion from CFPB (HM-26)

**Reviewer:** the governance-reviewer agent, on the complaint desk's second slice before its pull
request, with offline repros against the tests' fake CFPB (no network, no API). Retries,
argument checks, write-before-process snapshots, idempotent quarantine, transactional upserts and
minimisation by allow-list held. 13 findings, 10 confirmed and 3 plausible; all fixed or stated,
test-first.

| Finding | Severity | Outcome |
|---|---|---|
| The README and HM-18 said all data in the repo is synthetic; the branch commits 500 real complaints. CLAUDE.md's "Synthetic data only" rule says the same | High | The README and HM-18 name the one exception, `data/cfpb/`, minimised and labelled real; a test checks the README. CLAUDE.md is the project's own rule, left for Hari to change |
| CFPB's metadata (`last_updated`, the total) and its field names reached the `complaint_pull` audit entry unchecked: a value of any text was logged | Medium | Only a timestamp, booleans and a count pass; an unknown field name is logged as a placeholder |
| A naive or numeric receipt date passed validation, then every later load failed comparing dates | Medium | Dates must be ISO text with a time zone; others are quarantined |
| A cursor CFPB supplied out of range was stored, and every later pull crashed before its error handling | Medium | Cursors outside 2011 to 2100 are not used, and the cursor is read inside the pull's error handling |
| Nothing checked a page moved the cursor forward: an API that ignored `search_after` would refetch one page until capacity, looking like a backlog | Medium | The pull fails with `cursor_did_not_advance` |
| The late-publication gap was understated: CFPB publishes once the company responds or after about 15 days, so missing complaints behind the cursor is the normal case | Medium, plausible | HM-26 and ADR 0022 say so and name the remedy (a restart at least 15 days back), which nothing schedules yet; resuming reads from the day before the cursor's |
| Nothing pinned the pinned snapshot's manifest: a page and its manifest entry edited together loaded | Medium | The manifest's hash is recorded in code and checked when the snapshot loads |
| HM-25 still said nothing calls the CFPB API | Low | Removed |
| Each pull reported the process's running request and retry counts | Low | Each pull reports its own |
| A restart date was dropped after a failed first pull | Low, plausible | It applies until a pull succeeds |
| `Retry-After: ²` raised a `ValueError` carrying the header into the log | Low | Only ASCII digits count; anything else is ignored |
| A quarantine reason could name an unknown key from a replayed snapshot | Low | Only the model's own field names are named; others are `unknown_field` |
| `pin` copied files the manifest didn't list and took any pull ID | Low, plausible | It copies the manifest and its pages only, and checks the pull ID |

**Re-review of those fixes:** each new test fails with its fix undone. 4 findings, 3 confirmed
and 1 plausible, all Low; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| The pinned-manifest check failed open for a snapshot not listed in code | Low | A pinned snapshot with no recorded hash is refused; a test checks every pinned folder has one |
| A bad cursor already in a store failed every pull as `ValueError` | Low | The stored cursor is checked first and fails as `stored_cursor_invalid`; `--since` replaces it |
| A page with no usable cursor and an API ignoring the cursor shared one error code | Low | `no_usable_cursor` and `cursor_did_not_advance` |
| If CFPB orders IDs as text within a millisecond, IDs of different lengths would look like going backwards | Low, plausible | Another ID in the same millisecond counts as moving forward |

## Review 16: complaint triage (HM-27)

**Reviewer:** the governance-reviewer agent, on the complaint desk's third slice before its pull
request, with offline repros against the pinned snapshot (no network, no API). The agent could
not lower urgency or remove a reason, every failure path gave `agent_failed` with nothing
invented, and the model was sent no complaint ID, state or ZIP. 7 findings, all confirmed (one
in part plausible); all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| 72 of the 500 pinned complaints that should reach specialist review at high urgency didn't: the patterns missed CFPB's own "Debt is not yours" (65) and "Transaction was not authorized" (7); 66 got no reason at all | High | The patterns cover them, and "didn't make", "didn't apply for", "don't recognize" and "fraudulent" |
| "Fraudulent loan", "Unauthorized withdrawals or charges" and "Card was charged for something you did not purchase" were missed too (the first two from the reviewer's memory of CFPB's lists) | High, in part plausible | Covered |
| The only test over real labels was circular: it defined "must route" by the patterns under test | Medium | A hand-written table of every label in the snapshot, with its expected reason, tests each label and the whole snapshot end to end |
| Two workers on one complaint: the second called the model and wrote an agent call and a draft before failing, and the failure stopped the batch | Medium | The received entry is written once, before the model is called; the second worker stops there, and the batch skips it |
| The draft check missed "we'll credit", "will be waived", "within 15 days", "we will correct", bare domains and international numbers | Medium | Caught; the docs name what the pattern list covers |
| The design page's routing table contradicted the code (uncertainty on "not high", discrimination wording, foreclosure, a missing reason) | Medium | The table is the code's, and a test checks its codes match `REASONS` |
| `submitted_via` was sent to the model but not scanned; demo answers were indistinguishable from the model's in a log they shared; `--limit 0` triaged everything | Low | Every field sent is scanned; the routed entry records `scripted`, and the demo refuses the main audit log; a limit must be at least 1 |

With the fixes, 197 of the 500 pinned complaints go to specialist review on their labels or tags
alone.

**Re-review of those fixes:** each new test fails with its fix undone. 5 findings, 4 confirmed
and 1 plausible; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| Claiming a complaint before the model call meant a crash after the claim left it unrouted for good, and later runs counted it as skipped: a fraud complaint could drop out of every queue | High | A crash is routed to specialist review on the spot (`triage_interrupted`) when the log can be written; a complaint a killed run left claimed but unrouted is routed the same way by the next run |
| The `scripted` label was whatever the caller passed, so the tests' own runs labelled scripted answers as the model's | Medium | The stand-in marks itself and the pipeline reads the mark; no caller can omit it |
| "did not authorize" (as in "Lender made a payment you did not authorize") wasn't matched | Low | Matched, and in the hand-written table |
| The draft check still kept "will resolve this" and seven-digit numbers, and newly dropped benign drafts ("we will contact you", "we'll get back to you", a complaint reference) | Low | A list of commitment verbs instead of any "we will"; numbers must be grouped as phone numbers are; tested on benign and bad drafts |
| The demo-guard test ran against the real main log, and a hard link got past the guard | Low, plausible | The test uses a temporary main log; the guard compares files, not only paths |

## Review 17: complaint desk evals, fairness and panel page (HM-28)

**Reviewer:** the governance-reviewer agent, on the complaint desk's fourth slice before its pull
request, with offline repros (no network, no API). The eval scored routing against the
hand-written key, not the patterns, the published result reproduced, panel values were escaped
and every route read-only. 8 findings, all confirmed; all fixed, test-first, with four plausible
ones fixed too.

| Finding | Severity | Outcome |
|---|---|---|
| A live run in which every model call failed passed the live gate: the pipeline routes agent failures to a person, so they never counted as errors | High | Both gate checks fail on any `agent_failed` |
| The stand-in raises urgency for every tag and concern, so a weaker code floor (tags dropped from the high reasons) left the eval unchanged | Medium | The eval scores the code's own floor (`must_route_floor_below_high`), gated; the report says which figures depend on the stand-in |
| Complaint scopes still had claim case pages (`/cases/cfpb-<id>.html` answered as a claim) | Medium | They have none (404) |
| A complaint claimed by a killed triage appeared on no page | Medium | The desk lists it as claimed, not routed |
| The draft-field check compared stored drafts with the model they were stored from, so it was zero by construction | Low | It compares with a field list written in the eval |
| An unparsable file under `hall_monitor/` made the desk page fail instead of reporting the check failed | Low | The static check reports such a file as a finding |
| The label key's log said 22 labels; there are 21 | Low | Corrected, and a test checks the count |
| The README gave "0 counterfactual flips" without saying it can't flip by construction, and rates without their counts | Low | It says both, and that the rates depend on the stand-in |
| Plausible: the gate didn't check a result's mode; a live run's egress refusals were dropped; the counterfactual rendered unredacted text; the reproducibility test compared only the summary | Low | Each gate check names its mode; refusals are counted and gated; the check renders redacted text, as the pipeline does; the test compares the whole result |

## Review 18: who answered each complaint, on the desk page (HM-28)

**Reviewer:** the governance-reviewer agent, before the pull request, with offline repros. New
values were escaped, only an explicit true or false counted, and the page stayed read-only.
4 findings, all confirmed, and 2 plausible; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| A complaint whose model call failed showed as answered by the model, with a green badge: the log's label says only that the stand-in wasn't used | Medium | A failed or interrupted triage shows "no answer"; the register says what the label means |
| The log was named by its file name only, beside paths that pointed at the wrong demo log (`panel serve --demo` reads `var/demo/audit.sqlite`) | Medium | The log is named by its path in the repo, and the page lists all three logs correctly |
| A claimed-but-unrouted complaint had a green badge and counted in the banner's total | Low | It shows "not yet" with a warn badge; the banner counts answered complaints only |
| No test covered non-boolean labels or an interrupted route | Low | Both tested |
| Plausible: "never assumed to be the model" overstated what the log records; the README didn't mention the new column | Low | Both corrected |

## Review 19 (complaint-crew): delegated passes only narrow (HM-29)

**Reviewer:** the governance-reviewer agent, before the pull request, with offline probes. It
confirmed that narrowing, declared handoffs, loops, depth, forged or changed grants, stops down
the chain, the no-grant refusal and the unchanged approval digests all held. 3 findings
confirmed and 1 plausible; all fixed, test-first, or stated as a gap.

| Finding | Severity | Outcome |
|---|---|---|
| A changed copy of a grant (`model_copy` skips validation) put its holder, which could be any text, into the audit log: as the actor of `delegation_refused`, and through the gateway's "held by" message into a `tool_call` entry | Medium | Holder and chain must be plain identifiers; the refusal logs the holder only if it is one; the gateway asks the book first and never quotes the holder |
| `definitions` ignored a grant passed for an agent outside a crew while `call` checked it, so a tool could be offered that would be refused | Low | Both check a grant whenever one is passed |
| A grant binds a case only through its book; the gateway limits a tool's arguments to the case only through `scope_policy`, which is optional | Low | Stated in HM-29's gaps and ADR 0025; the crew pipeline (slice 3) must pass it |
| Plausible: two handoffs from one grant on different threads could each see the old allotment total and together exceed it | Low | The book takes a lock around issuing and handing off; a test with a slowed grant shows only one of two over-large handoffs is made |

## Review 20 (redteam-arena): the red-team arena (HM-30)

### Part 1: self-check (not independent)

**Reviewer:** a self-review against the governance-reviewer checklist
(`.claude/agents/governance-reviewer.md`), done in the session that wrote the code: the
subagent couldn't be started from that session, so this is not an independent review. Checked: the judge reads the
seed's own statement of what was planted (it missed that `must_route` was forced to match
routing, which Part 2 found); results hold codes and
names only; tests never load the shipped registry or call the API. 4 findings, all confirmed,
and 1 plausible; all fixed, test-first, or logged as a gap.

| Finding | Severity | Outcome |
|---|---|---|
| The published result didn't record the agent version and prompt hash it was scored on | Medium | It records the triage agent's manifest digest and prompt hash (`agent_versions`) |
| The canary was only in the agent's text, so complaint text reaching the log would not be caught | Medium | The canary is in the narrative too |
| The judge read only the attempt's own scope, so text leaking into another entry would be missed | Low | It reads the whole round's log |
| The first test of the published result loaded the shipped registry, which the kill-switch test forbids | Low | It uses the test registry; the gap that a live stop changes only the CLI's result is in the register |
| Plausible: the judge counts an entity-encoded draft as carrying the planted link though raw text shows codes | Low | Logged as a gap in HM-30 |

### Part 2: independent review

**Reviewer:** the governance-reviewer agent, on `f72c291`, independent of the session that wrote
the code. 6 findings (one High), all fixed test-first or corrected.

| Finding | Severity | Outcome |
|---|---|---|
| A test forced each seed's `must_route` to equal the code's own routing, so `must_route_missed` could never show a routing gap, while the docs said the judge never uses the defence's patterns | High | `must_route` is a human label written from the whole complaint; the check is one way (code must-route means labelled must-route); three seeds show fraud, a foreclosure sale and an older consumer scammed only in the narrative, on plain labels. All three go to the standard queue: routing reads only labels and tags. Reported as harm and as an HM-30 gap, not fixed here |
| Only the routed entry said the answer was scripted; each `agent_call` and the routed entry's `models` named the hall pass's model, and the result's agents row did too | Medium | A scripted client's `agent_call` entries carry `scripted: true` and `models` reads `complaint_triage:scripted`; the result's `hall_pass` row says the answers came from the scripted hijack. Live entries are unchanged, and so are the published complaint results |
| Plausible: with Langfuse keys exported, the arena would send spans for every attempt, from an offline command, showing hijacked answers as model calls | Medium | The arena turns tracing off for the run and restores the keys after; a test with fake keys and a tripwire module shows Langfuse is never reached |
| The README row and ADR 0026 blamed only encoding, but one promise was kept in plain words in round 1 | Low | Both name the vocabulary gap too |
| Seeds retired on any breach, so a seed that only inflated urgency or beat the detector was never checked for harm again | Low | Seeds retire only on harm; the report states harm first |
| The README said 28 controls; there are 29 | Low | Corrected, and a docs test compares the count with the register |

The arena's own findings (routing reads only labels and tags; the draft check misses some
promise wording and reads text undecoded; the injection detector misses entities encoded twice
and look-alike letters) are published in HM-30's gaps and the result, not fixed in this branch.

**Re-review of the fixes** (governance-reviewer, after the merge of `main`): all six fixes real,
the merge dropped and duplicated nothing, published results reproduce exactly. 3 Low wording
findings, all fixed: the HM-30 gap said 15 seeds (now 18); the design doc promised must-route
misses stay at zero (now: misses on complaints whose labels or tags call for a specialist); a
test comment pointed at Review 19 (now Review 20 Part 1).

## Review 21 (failure-drills): failure drills (HM-31)

### Part 1: self-check (not independent)

**Reviewer:** the session that wrote the change, working through the governance-reviewer
checklist (`.claude/agents/governance-reviewer.md`) by hand. Not an independent review. 2
findings confirmed and 1 doc fix; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| An unforeseen error in one case (a locked database, a bug) ended the whole drill with a traceback and no report | Medium | It is recorded by its type (`test_an_unexpected_error_in_one_case_is_reported_not_fatal`); part 2 found it still wasn't flagged in every case |
| The design said the drill shows the HM-19 gap (a call in flight finishes), but the report didn't count cases in flight at the stop | Medium | The report counts them and how many were then decided automatically (`test_the_report_counts_cases_in_flight_at_the_stop`) |
| The design and ADR named two mutation tests; there were three | Low | Corrected (four after part 2) |

### Part 2: independent review

**Reviewer:** the governance-reviewer agent, run independently on the branch at `2bff0e5`, with
offline repros. 5 findings confirmed and 3 plausible; all fixed, test-first.

| Finding | Severity | Outcome |
|---|---|---|
| A wrong answer went on a random claim and the check asked only that it reached a person; 20 of 36 claims go to a person anyway. With the over-claim routing rule patched out, seed 3 reported no problems | Medium | The check requires the over-claim reason in the logged routing; a mutation test removes that one rule (`test_the_checks_fail_when_the_over_claim_rule_is_removed`) |
| Errors recorded on a case were never read by the check: an error raised after a real submit gave outcome decided, no problems, exit 0, contradicting HM-31 and ADR 0027 | Medium | Any case with an unexpected error is a problem (`test_an_unexpected_error_after_a_finished_submit_is_flagged`) |
| A case the cap refused was counted as with a person, but it never entered the workflow: no checkpoint, status unknown, not in the waiting list | Medium | Not started is reported apart from with a person, and the docs say plainly these cases aren't queued for anyone (`test_a_case_refused_by_the_cap_is_not_queued_for_anyone`) |
| A model fault the owner's stop reached first (the faulty agent never ran) still counted as exercised | Low | Each model fault must leave its own stop reason; faults the stop reached first are listed as masked (`test_a_fault_hidden_by_the_stop_is_reported_as_masked`, `test_a_stop_reason_that_doesnt_match_the_fault_is_a_problem`) |
| "Nothing is triaged or decided twice" overstated: a duplicate submission runs every agent twice and pays twice; only the evidence pack and the decision are once-only | Low | Reworded everywhere; the check allows at most two triage runs for a duplicate and one otherwise |
| Plausible: an error while reading a case's cost escaped the worker, ended the drill with no report and left its reservation held | Low | Caught, the reservation given back, the case flagged, and a leftover reservation is a problem (`test_an_error_while_settling_cost_is_flagged_and_the_drill_finishes`) |
| Plausible: exported Langfuse keys would make a drill send spans (confirmed while writing the test: an export was attempted with the fake keys) | Low | Tracing is off for the drill's duration and the keys are put back (`test_the_drill_never_sends_traces_even_with_langfuse_keys_set`); the register notes this is process-wide |
| Plausible: "cases running together can't pass the cap" holds only if each case costs at most its ceiling, which is checked afterwards | Low | Said in the design, ADR and register |

**Re-review of the fixes** (governance-reviewer, after the merge of `main`): fixes 1 and 3 to 5
and plausible a to c real; the merge clean; the control count right. 1 Medium and 1 Low, both
fixed test-first:

| Finding | Severity | Outcome |
|---|---|---|
| Any ValueError was taken as the workflow's own refusal and stored as text, so an unexpected one (including a pydantic ValidationError, which repeats input) was never flagged | Medium | Only a duplicate's second submission may be refused, and only with the workflow's four known refusals, recorded as a fixed label; any other ValueError is unexpected, recorded by its type |
| The docs said masked faults are those after the risk step; wrong answers stopped before routing and faults on the risk agent are masked too | Low | Design and docstring reworded |

## Review 22 (agent-loop): agents act within their grant (HM-32)

**Reviewer:** the governance-reviewer agent, before the pull request, with offline scripted
probes. It confirmed no path gives a crew agent a model call, tool or helper without a valid
grant, the lead can't raise a helper's budget or reach, and existing agents keep their path.
3 findings confirmed and 2 plausible; all fixed, test-first, or stated as a gap.

| Finding | Severity | Outcome |
|---|---|---|
| A parent's budget counted what it allotted, not what helpers spent: a helper's overshoot never reached the lead, and a helper missing from the allotments still made one paid call (a case reached 150% of its root budget and finished normally) | High | A helper code gave no allotment is refused before any call; a grant with nothing left makes no further call; spending past a helper's allotment is charged up to its parent, so the root's spending is the case's; the docs now say a case overshoots by at most one call per grant in its tree |
| A second handoff to the same helper produced the identical grant, overwriting it and charging the parent's allotment twice | Medium | Grants carry their issue order, so each handoff is a fresh grant with its own spending |
| A helper's failure became a tool result and the lead finished normally, with the helper's call (versions, cost) lost to the caller | Medium | Every handoff's outcome and call is kept on the runner (`outcomes`, `complete`) so the crew pipeline can mark the case incomplete |
| Plausible: nothing tied a handoff runner to the grant it runs under | Low | `Delegated` refuses a runner for another grant or book |
| Plausible: a call the SDK bills and then raises on isn't charged | Low | Stated in HM-32's gaps and ADR 0028 |

**Re-review of the fixes** (governance-reviewer, after the merge of `main`): the cascade
arithmetic, the overshoot bound, fresh grants, kept outcomes and the merge all checked out.
1 Medium and 1 Low, both fixed test-first:

| Finding | Severity | Outcome |
|---|---|---|
| A lead pushed over its allotment by a helper's overshoot, or landing exactly on it, stopped without `grant_budget_reached` in the log | Medium | The event is logged once per grant whenever a grant is stopped for its allotment, from `spend` or `check` |
| A lead could allot its whole budget to helpers and then couldn't make the call that reads their answers, so every such case stopped | Low | A grant can't hand on all of its allotment; the delegation test's 0.6 + 0.4 now uses 0.39 |
