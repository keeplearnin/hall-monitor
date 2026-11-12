# CIO and CAIO readiness checklist

What a CIO (operations, risk, cost) and a Chief AI Officer (AI governance, model risk) would check
before trusting the agentic workflows, each with one status and the code, test, result or doc
that proves it. **Pass** means built, tested and evidenced in this repo. **Partial** means built
but with a stated limit. **Gap** means not done. hall-monitor is a reference build on synthetic
and public data running on one machine; "pass" is about the controls, not about a production
deployment. Every claim here was checked against the code; the [review log](review/log.md)
records the skeptical review of this page.

**Count:** 19 pass, 10 partial, 6 gap (35 items).

## Top gaps

1. **No live evidence on the approved agents.** Every agent's model card says "no": the one live
   claims baseline was scored on earlier versions and the fixed order, and the supervisors, the
   complaint desk and HR intake have never run live. Every agentic claim here rests on scripted
   runs and tests. A live baseline costs about $5–8 and runs only when Hari approves the spend.
2. **Branch protection is off.** CI and the eval gate run on every PR, but a failing check doesn't
   block a merge and CODEOWNERS isn't enforced, so separation of duties for changes rests on
   habit. A repository setting only Hari can change.
3. **No live monitoring: drift, override rate, cost per day.** Override rate exists only simulated;
   nothing tracks drift or watches production metrics, and nothing schedules the regular checks.
4. **No dependency vulnerability scanning.** Versions are pinned (`uv.lock`, `--locked` in CI) but
   nothing scans them.
5. **Fairness has no four-fifths or adverse-impact analysis.** The claims name-swap test has no
   published result, the complaint counterfactual is structural, and HR leave (Annex III
   high-risk) has no group-level analysis.
6. **Egress is guarded only on the complaint desk.** Claims and HR call the model through the
   SDK's own client, not the allow-listed transport; a static check stops any other HTTP use.
7. **The EU AI Act's organisational duties aren't covered**: informing workers, a
   fundamental-rights impact assessment, telling the employee an AI read their request.

## CIO: operations, risk and cost

| # | Item | Status | Proof | Limit |
|---|---|---|---|---|
| 1 | Runbook: deploy, stop, restart, roll back, recover | **Pass** | [runbook](runbook.md) | One machine; no hosted deployment to run |
| 2 | Kill-switch drill with measured time-to-stop | **Partial** | [drill results](../evals/results/kill-switch-drill/report.md), gated in [`evals/gate.yaml`](../evals/gate.yaml); `hall_monitor/evals/stop_timing.py`; `tests/test_kill_switch.py::test_the_kill_switch_drill_finds_no_call_or_automatic_route_after_a_stop` | Measured offline with a simulated 50 ms call: time-to-stop is the call in flight plus overhead. Live time-to-stop (about one model call, seconds) not measured |
| 3 | A stop takes effect mid-case, at the next step | **Pass** | Every supervisor turn and chosen step re-checks every agent the case used: `test_stopping_the_supervisor_mid_run_stops_the_claim_at_the_next_step`, `test_a_stop_on_a_specialist_that_already_answered_halts_the_claim_at_the_next_step` (and desk and HR equivalents); found and fixed by the drill | A call already in flight finishes (HM-19) |
| 4 | Fails safe: outages, timeouts, wrong answers, budget exhaustion and partial runs go to a person | **Pass** | `tests/test_agentic.py`, `tests/test_desk_agentic.py`, `tests/test_hr_agentic.py`; [failure drills](failure-drills-design.md) (HM-31); [ADR 0007](adr/0007-failures-stop-the-claim.md) | Scripted faults; a live model fails in other ways too |
| 5 | Duplicate runs: nothing paid, routed, signed or sent twice | **Pass** | Once-only log entries; `test_two_agentic_runs_of_one_claim_at_once_record_one_result`, `test_a_concurrent_second_triage_makes_no_model_call_and_the_batch_continues`, `test_two_agentic_preparations_at_once_log_one_prepared_entry_each`, failure drills' duplicate case; HM-25 for sending | A duplicate claim submission can still pay for two triage runs' model calls |
| 6 | Cost bounded per case, hard caps in code | **Pass** | Budgets per case: claims 16 steps / 0.25 USD / 300 s, desk 6 / 0.02 / 120, leave 8 / 0.02 / 120, checked before every model call; an unpriced cost stops the loop (`agentic.py`, `complaints/pipeline.py`, `hr/pipeline.py`; `test_running_out_of_money_*`) | Overshoot of one agent's call; caps set from published costs, not tuned live |
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
| 18 | Who approved what | **Pass** | Approvals as reviewed diffs to `registry/approvals.yaml` (HM-01); stops by owner ID; signed adjuster and HR decisions bound to evidence hashes (HM-05, HM-23) | — |
| 19 | Reproducible eval tied to a commit | **Pass** | Every result records `git_commit`, `git_dirty` and the agents' digests; the scripted results are regenerated by a test or a single command | Live results need a paid run to reproduce |
| 20 | Honest limits: a known-gaps list; nothing declared that isn't enforced | **Pass** | Register `gaps` and `not_covered` ([controls](controls.md)); this checklist; reviews 1–24 in the [review log](review/log.md) | — |
| 21 | Changes can't merge without passing checks | **Gap** | — | Branch protection off (Hari's setting); CI is advisory |

## CAIO: AI governance and model risk

| # | Item | Status | Proof | Limit |
|---|---|---|---|---|
| 22 | AI use inventory and a model card per agent | **Pass** | [Inventory and model cards](model-cards.md), generated from the registry and results; `test_the_model_cards_are_current_and_every_agent_has_written_limits` | Cards show the evidence gap (item 27) rather than hide it |
| 23 | NIST AI RMF and FINOS mapping | **Pass** | [controls](controls.md), identifiers checked by tests | — |
| 24 | ISO/IEC 42001 mapping | **Partial** | [regulatory mapping](regulatory-mapping.md), clause level | Informal; not checked against the standard's text; no AI policy, risk register or management review |
| 25 | EU AI Act (HR as Annex III high-risk): logging (Art. 12) and human oversight (Art. 14) | **Pass** | Art. 12: the hash-chained log of every step, supervisor choice, rules result, pack and signed decision; Art. 14: a person signs every leave decision within the role the rules name, no agent can lower it, owners can stop any agent ([mapping](regulatory-mapping.md)) | Offline evidence; no live operator study |
| 26 | EU AI Act: transparency, deployer duties, impact assessment | **Gap** | [regulatory mapping](regulatory-mapping.md) lists them | Worker information, Art. 27 assessment and telling the employee an AI read the request are not done |
| 27 | Evals: accuracy on the approved agents | **Gap** | [model cards](model-cards.md) show no live result on any approved version | One live claims baseline, on earlier versions and the fixed order |
| 28 | Human oversight is real: what the agent decides alone vs what code forces to a person | **Pass** | Model cards ("may decide alone" / "code forces to a person"); routing in code (HM-04, HM-21, HM-27); [ADR 0028](adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md) | — |
| 29 | Evidence it can't be bypassed (red-team results) | **Pass** | 40 scripted supervisor policies against the fixed paths: no claim through that the fixed path sent to a person, no complaint or leave request given less review ([results](../evals/results/agentic-scripted/report.md), gated); the agentic red-team tests; reviews 22–24 found three bypasses, all fixed and now covered by the eval | Scripted adversaries; a live model can't exceed them, but hasn't been run |
| 30 | Fairness (four-fifths, adverse impact) | **Gap** | Counterfactual tests exist: claims name swap (HM-14, no published result), complaints by state and ZIP (HM-28, structural) | No group-level selection-rate analysis anywhere, including HR |
| 31 | Monitoring: drift and override rate | **Gap** | Override rate simulated from evals (`hall_monitor/evals/override.py`) | No live monitoring or drift tracking |
| 32 | Eval gate in CI blocks regressions | **Partial** | `python -m hall_monitor.evals.gate` in CI with thresholds ([`gate.yaml`](../evals/gate.yaml)) | `stale_results: warn` until a fresh live baseline; branch protection off, so a failure doesn't block |
| 33 | Change control: approvals, versioning, re-approval on change | **Pass** | Hash-pinned approvals refused at run time until re-approved (HM-01, ADR 0003); prompt and manifest versions; tests fail on an unapproved change | The version bump is a review rule, not a test |
| 34 | Separation of duties | **Partial** | Decisions need a person with the authority the rules name (HM-05, HM-23); restarting a stopped agent is a reviewed approval | No sign-in on the panel; CODEOWNERS not enforced, so the same person can change and approve |
| 35 | Explainability: each case's trace shows what the agent did and why, in structured fields | **Partial** | `supervisor_step` entries (action, reason code, outcome, refusal, agents, cost) in the panel's case trace, the desk page and HR's evidence pack; every routing reason is a code template | A supervisor's reason code is its own claim, logged, not verified; specialists' free-text reasoning is kept out of the log by design, so the "why" in the log is codes |

## How to read the counts

The passes are controls that hold whatever the model does: routing, oversight, refusals, budgets,
the kill switch, the audit trail. The partials and gaps are mostly about what is outside the code
(organisational duties, repository settings, scheduled monitoring) and about measurement: no live
run on the current agents. That is the honest shape of the case: the boundaries are proven
offline and enforced in code; how well the agents perform inside them is not yet measured.
