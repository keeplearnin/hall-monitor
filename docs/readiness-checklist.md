# CIO and CAIO readiness checklist

What a CIO (operations, risk, cost) and a Chief AI Officer (AI governance, model risk) would check
before trusting the agentic workflows, each with one status and the code, test, result or doc
that proves it. **Pass** means built, tested and evidenced in this repo. **Partial** means built
but with a stated limit. **Gap** means not done. hall-monitor is a reference build on synthetic
and public data running on one machine; "pass" is about the controls, not about a production
deployment. Every claim here was checked against the code; the [review log](review/log.md)
records the skeptical review of this page.

**Count:** 16 pass, 16 partial, 12 gap (44 items).

## Top gaps

1. **No live evidence on the approved agents.** Every agent's model card says "no": the one live
   claims baseline was scored on earlier versions and the fixed order, and the supervisors, the
   complaint desk and HR intake have never run live. Every agentic claim here rests on scripted
   runs and tests. A live baseline costs about $5–8 and runs only when Hari approves the spend.
2. **Change control isn't enforced on GitHub.** Branch protection is off: CI and the eval gate run
   on every PR, but a failing check doesn't block a merge and CODEOWNERS isn't enforced, so one
   person can change and approve. A repository setting only Hari can change.
3. **Identity and key custody.** No sign-in on the panel or CLIs, and the decision-signing keys
   are public demo keys, so a demo signature proves nothing about who decided.
4. **Operations around the controls:** no incident response or regulator notification, no backup
   and restore of the audit log and evidence packs, no retention or erasure periods.
5. **No live monitoring:** drift, override rate and daily cost aren't tracked (override rate is
   simulated), and nothing schedules the regular checks.
6. **No dependency vulnerability scanning.** Versions are pinned (`uv.lock`, `--locked` in CI) but
   nothing scans them.
7. **Fairness has no four-fifths or adverse-impact analysis.** The claims name-swap test has no
   published result, the complaint counterfactual is structural, and HR leave (likely Annex III
   high-risk) has no group-level analysis.
8. **Egress is guarded only on the complaint desk.** Claims and HR call the model through the
   SDK's own client, not the allow-listed transport; a static check stops any other HTTP use.
9. **The EU AI Act's organisational duties aren't covered:** AI literacy and reviewer training,
   informing workers and the employee, a fundamental-rights impact assessment, a QMS.

## CIO: operations, risk and cost

| # | Item | Status | Proof | Limit |
|---|---|---|---|---|
| 1 | Runbook: deploy, stop, restart, roll back, recover | **Partial** | [runbook](runbook.md) | No backup and restore of the audit log, packs and anchor; no incident escalation; no procedure for a compromised signing key; one machine |
| 2 | Kill-switch drill with measured time-to-stop | **Partial** | [drill results](../evals/results/kill-switch-drill/report.md), gated in [`evals/gate.yaml`](../evals/gate.yaml) so a broken switch fails it (`test_the_drill_fails_when_the_kill_switch_is_broken`); `hall_monitor/evals/stop_timing.py` | Measured offline with a simulated 50 ms call. Live, each call is bounded at 60 s per attempt and two attempts, so worst-case time-to-stop is about two minutes and the typical one a single call; not measured live |
| 3 | A stop takes effect mid-case, at the next step | **Pass** | Every supervisor turn and chosen step re-checks every agent the case used: `test_a_stop_on_a_specialist_that_already_answered_halts_the_claim_at_the_next_step`, `test_a_stop_during_a_specialist_call_means_no_further_supervisor_turn` (and desk and HR equivalents); found and fixed by the drill | A call already in flight finishes, up to about two minutes live (HM-19); a stop on an agent not used yet takes effect when it is next needed |
| 4 | Fails safe: outages, timeouts, wrong answers, budget exhaustion and partial runs go to a person | **Pass** | `tests/test_agentic.py`, `tests/test_desk_agentic.py`, `tests/test_hr_agentic.py`; [failure drills](failure-drills-design.md) (HM-31); [ADR 0007](adr/0007-failures-stop-the-claim.md) | Scripted faults; a live model fails in other ways too |
| 5 | Duplicate runs: nothing paid, routed, signed or sent twice | **Pass** | Once-only log entries; `test_two_agentic_runs_of_one_claim_at_once_record_one_result`, `test_a_concurrent_second_triage_makes_no_model_call_and_the_batch_continues`, `test_two_agentic_preparations_at_once_log_one_prepared_entry_each`, failure drills' duplicate case; HM-25 for sending | A duplicate claim submission can still pay for two triage runs' model calls |
| 6 | Cost bounded per case, hard caps in code | **Pass** | Budgets per case: claims 16 steps / 0.25 USD / 300 s, desk 6 / 0.02 / 120, leave 8 / 0.02 / 120, checked before every model request, each round of a claims specialist's tool loop included; an unpriced cost stops the loop; every call bounded at 60 s per attempt, two attempts (`default_client`, `test_the_default_model_client_bounds_each_call`, `test_the_time_budget_holds_inside_a_specialist_s_tool_loop`) | Cost and time caps are hard to within one model request in flight: about two minutes and that request's cost (until review 27 a tool-using agent could run up to about 14 minutes past); time-to-stop stays about two minutes, since the hall pass is checked every round; caps set from published costs, not tuned live |
| 7 | Cost bounded and visible per run | **Partial** | Cost per case in every result and the audit log (HM-16); a batch cap with reservations in the failure drills (HM-31); the panel shows cost | The claims, desk and HR CLIs have no batch or daily cap (the live complaint run caps the count) |
| 8 | Agentic vs fixed cost offline, live clearly marked | **Pass** | [agentic-scripted report](../evals/results/agentic-scripted/report.md): supervisor calls and cost per case per policy at fixed fake token counts, marked as not live | No live cost measured |
| 9 | Egress limits | **Partial** | `hall_monitor/egress.py` allow-list on the complaint desk; `python -m hall_monitor.no_send check` in CI fails on any other HTTP use (HM-25) | Claims and HR use the Anthropic SDK's own client, not the guarded transport |
| 10 | Secrets handling | **Partial** | Keys from the environment only; `.env` ignored, `.env.example` lists names; traces never capture inputs (HM-11) | No secret manager or rotation; the live-eval workflow expects its key in a `live-eval` environment limited to main, but whether the secret is set there is a repository setting the code can't show |
| 11 | Dependency pinning | **Pass** | `uv.lock`; CI installs with `uv sync --locked` | — |
| 12 | Dependency scanning | **Gap** | — | No `pip-audit`, Dependabot or SBOM |
| 13 | Least-privilege tools | **Pass** | Gateway allow-lists and data-class checks on every call (HM-03); supervisors have no tools; delegation narrows (HM-29) | Allow-lists in one process, not isolation |
| 14 | Injection defences | **Partial** | Fencing and escaping, a detector that routes to a person (HM-09); supervisors never see case text (`test_claim_text_tool_results_and_agent_reasoning_never_reach_the_supervisor`); [red-team arena](../evals/results/redteam-scripted/report.md) (HM-30) | The arena found encodings the detector misses; no live red team |
| 15 | Threat model for the agentic loop | **Pass** | [threat model (STRIDE)](threat-model-agentic.md) | Single-process assumptions stated |
| 16 | Audit chain verifies | **Pass** | `python -m hall_monitor.audit verify`; HM-06; `tests/test_audit.py` | SQLite on one machine |
| 17 | Audit anchor | **Partial** | `audit head` and `verify --expect-seq --expect-hash` | Anchoring is manual; nothing publishes the head elsewhere automatically |
| 18 | Who approved what | **Partial** | Approvals as reviewed diffs to `registry/approvals.yaml` (HM-01); stops by owner ID; adjuster and HR decisions signed and bound to evidence hashes (HM-05, HM-23) | The signing keys are public demo keys (HM-05 and HM-23 gaps), so a demo signature proves nothing about who decided; stop owner IDs aren't authenticated |
| 19 | Reproducible eval tied to a commit | **Pass** | Every result records `git_commit`, `git_dirty` and the agents' digests; the scripted results are regenerated by a test or a single command | Live results need a paid run to reproduce |
| 20 | Honest limits: a known-gaps list; nothing declared that isn't enforced | **Pass** | Register `gaps` and `not_covered` ([controls](controls.md)); this checklist; every review in the [review log](review/log.md) | — |
| 21 | Changes can't merge without passing checks | **Gap** | — | Branch protection off (Hari's setting); CI is advisory |

## CAIO: AI governance and model risk

| # | Item | Status | Proof | Limit |
|---|---|---|---|---|
| 22 | AI use inventory and a model card per agent | **Pass** | [Inventory and model cards](model-cards.md), generated from the registry and results; `test_the_model_cards_are_current_and_every_agent_has_written_limits` | Cards show the evidence gap (item 27) rather than hide it |
| 23 | NIST AI RMF and FINOS mapping | **Pass** | [controls](controls.md), identifiers checked by tests | — |
| 24 | ISO/IEC 42001 mapping | **Partial** | [regulatory mapping](regulatory-mapping.md), clause level | Informal; not checked against the standard's text; no AI policy, risk register or management review |
| 25 | EU AI Act (HR as likely Annex III high-risk): logging (Art. 12) and human oversight (Art. 14) | **Partial** | Art. 12: the hash-chained log of every step, supervisor choice, rules result, pack and signed decision; Art. 14: a person signs every leave decision within the role the rules name, no agent can lower it, owners can stop any agent ([mapping](regulatory-mapping.md)) | Demo signing keys; no log retention period (Art. 19, 26(6)); no panel sign-in; operator understanding and automation bias (Art. 14(4)) not addressed |
| 26 | EU AI Act: transparency, deployer duties, impact assessment | **Gap** | [regulatory mapping](regulatory-mapping.md) lists them | Worker information, Art. 27 assessment and telling the employee an AI read the request are not done |
| 27 | Evals: accuracy on the approved agents | **Gap** | [model cards](model-cards.md) show no live result on any approved version | One live claims baseline, on earlier versions and the fixed order |
| 28 | Human oversight is real: what the agent decides alone vs what code forces to a person | **Pass** | Model cards ("may decide alone" / "code forces to a person"); routing in code (HM-04, HM-21, HM-27); [ADR 0028](adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md) | — |
| 29 | Evidence it can't be bypassed (red-team results) | **Pass** | 40 scripted supervisor policies against the fixed paths: no claim through that the fixed path sent to a person, no complaint or leave request given less review ([results](../evals/results/agentic-scripted/report.md), gated); the agentic red-team tests; reviews 22, 23 and 25 found bypasses, all fixed and now covered by the eval | The boundaries are in code, so a live model meets the same checks; the 40 policies are a sample of hostile behaviour, not an exhaustive search, and no live model has been run |
| 30 | Fairness (four-fifths, adverse impact) | **Gap** | Counterfactual tests exist: claims name swap (HM-14, no published result), complaints by state and ZIP (HM-28, structural) | No group-level selection-rate analysis anywhere, including HR |
| 31 | Monitoring: drift and override rate | **Gap** | Override rate simulated from evals (`hall_monitor/evals/override.py`) | No live monitoring or drift tracking |
| 32 | Eval gate in CI blocks regressions | **Partial** | `python -m hall_monitor.evals.gate` in CI with thresholds ([`gate.yaml`](../evals/gate.yaml)) | `stale_results: warn` until a fresh live baseline; branch protection off, so a failure doesn't block |
| 33 | Change control: approvals, versioning, re-approval on change | **Pass** | Hash-pinned approvals refused at run time until re-approved (HM-01, ADR 0003); prompt and manifest versions; tests fail on an unapproved change | The version bump is a review rule, not a test |
| 34 | Separation of duties | **Partial** | Decisions need a person with the authority the rules name (HM-05, HM-23); restarting a stopped agent is a reviewed approval | No sign-in on the panel; CODEOWNERS not enforced, so the same person can change and approve |
| 35 | Explainability: each case's trace shows what the agent did and why, in structured fields | **Partial** | `supervisor_step` entries (action, reason code, outcome, refusal, agents, cost) in the panel's case trace, the desk page and HR's evidence pack; every routing reason is a code template | A supervisor's reason code is its own claim, logged, not verified; specialists' free-text reasoning is kept out of the log by design, so the "why" in the log is codes |

## Added by the CIO/CAIO review

| # | Item | Status | Proof | Limit |
|---|---|---|---|---|
| 36 | Incident response and regulator notification | **Gap** | — | No incident process, on-call, post-incident review or serious-incident reporting (EU AI Act Art. 73) |
| 37 | Data retention and erasure | **Gap** | Evidence packs are stored by hash and erasable; the log holds no free text (HM-06, HM-10) | No retention periods; the append-only log can't erase an entry; no GDPR erasure procedure for packs |
| 38 | Backup and disaster recovery | **Gap** | — | No backup of the SQLite log, packs and checkpoints, or of the anchor kept elsewhere |
| 39 | Latency, SLA and capacity | **Gap** | Per-call bound of 60 s per attempt, two attempts (`default_client`) | No latency target, capacity plan or rate-limit handling beyond the SDK's retry; the 60 s per-attempt bound is untested live and may stop long Sonnet answers (they would go to a person) |
| 40 | Vendor and model risk | **Partial** | Pinned model IDs with no silent fallback (HM-02); a model change is a reviewed manifest change | Single provider; no deprecation plan; whether the model names are dated snapshots or aliases isn't checked; provider data-retention terms out of scope |
| 41 | Identity, access control and key custody | **Gap** | — | No sign-in on the panel or CLIs; demo signing keys in the repo; access rests on machine access |
| 42 | Data sent to the model provider | **Partial** | Identifiers redacted before any model call (HM-10); data classes checked per agent at run time; the recommendation and supervisors get codes and amounts only | Redacted personal and health text still leaves the machine for the provider; redaction is pattern-based |
| 43 | Reviewer training, AI literacy and automation bias | **Gap** | — | Nothing addresses how adjusters and HR staff are trained to question an AI's preparation (EU AI Act Art. 4, Art. 14(4)) |
| 44 | Continuity when a supervisor is stopped | **Partial** | A stopped supervisor sends every new case to a person (runbook); the fixed order exists for comparison | No staffing plan for the extra cases; falling back to the fixed order is a reviewed code change, not a switch |

## How to read the counts

The passes are controls that hold whatever the model does: routing, oversight, refusals, budgets,
the kill switch, the audit trail. The partials and gaps are mostly about what is outside the code
(organisational duties, repository settings, scheduled monitoring) and about measurement: no live
run on the current agents. That is the honest shape of the case: the boundaries are proven
offline and enforced in code; how well the agents perform inside them is not yet measured.
