# Controls

<!-- Generated from controls/controls.yaml by `uv run python -m hall_monitor.governance.controls`. Don't edit by hand. -->

Every hall-monitor control, mapped to the NIST AI Risk Management Framework 1.0 (NIST AI 100-1), Core and the FINOS AI Governance Framework (v3 (October 26, 2026)). Each control names its code, the tests that prove it and its published evidence; a test fails if any reference goes stale. Framework identifiers were checked against the sources listed at the end.

## Summary

| ID | Control | Status | NIST AI RMF | FINOS risks | FINOS mitigations |
|---|---|---|---|---|---|
| [HM-01](#hm-01) | Agent registry and approvals | built | GOVERN 1.6, GOVERN 2.1, MAP 1.1, MANAGE 2.4 | RI-05, RI-18 | MI-10 |
| [HM-02](#hm-02) | Pinned models, no silent fallback | built | MANAGE 3.1 | RI-05 | MI-10 |
| [HM-03](#hm-03) | Tool gateway with allow-list and claim scope | built | GOVERN 1.4, MEASURE 2.7, MANAGE 1.3 | RI-24, RI-25 | MI-12, MI-16, MI-18, MI-19, MI-20 |
| [HM-04](#hm-04) | Routing to a person, decided in code | built | GOVERN 3.2, MAP 3.5, MANAGE 1.3 | RI-22, RI-24 | MI-24 |
| [HM-05](#hm-05) | Adjuster approval gate | built | GOVERN 3.2, MAP 3.5, MANAGE 4.1 | RI-22 | MI-24 |
| [HM-06](#hm-06) | Tamper-evident audit log | built | MEASURE 2.8, MANAGE 4.1 | RI-17, RI-22 | MI-04, MI-21 |
| [HM-07](#hm-07) | Critic review with revision history | built | MEASURE 2.3, MEASURE 2.5, MANAGE 1.3 | RI-04, RI-06, RI-14 | MI-15, MI-21 |
| [HM-08](#hm-08) | Failures stop the claim and go to a person | built | MANAGE 2.4, MANAGE 4.3 | RI-07 | MI-24 |
| [HM-09](#hm-09) | Prompt-injection defences | built, results pending | MEASURE 2.7, MANAGE 1.3 | RI-10, RI-14, RI-25, RI-28 | MI-03, MI-17, MI-19 |
| [HM-10](#hm-10) | Identifier redaction and data minimisation | built | MEASURE 2.10 | RI-01 | MI-01, MI-06, MI-12 |
| [HM-11](#hm-11) | Telemetry without claim content | built | MEASURE 2.10 | RI-01 | MI-01, MI-04 |
| [HM-12](#hm-12) | Eval harness and published results | built | MEASURE 1.1, MEASURE 2.1, MEASURE 2.3, MEASURE 2.5 | RI-04, RI-06 | MI-05 |
| [HM-13](#hm-13) | Grounding checks and evidence for every decision | built | MEASURE 2.9 | RI-04, RI-17 | MI-13, MI-21 |
| [HM-14](#hm-14) | Counterfactual fairness test | built, results pending | MEASURE 2.11 | RI-16 | MI-05 |
| [HM-15](#hm-15) | Adversarial suite | built, results pending | MEASURE 2.7 | RI-10, RI-24, RI-25 | MI-05 |
| [HM-16](#hm-16) | Cost per claim and economy model routing | built | MEASURE 3.1, MANAGE 3.1 |  | MI-09 |
| [HM-17](#hm-17) | CI checks and the eval gate | built | MEASURE 3.1 | RI-05, RI-06 | MI-05, MI-10 |
| [HM-18](#hm-18) | Synthetic data and documented limits | built | MAP 1.1, MAP 2.1, MEASURE 1.1, MEASURE 2.5 | RI-19 | MI-06 |
| [HM-19](#hm-19) | Kill switch | built | MANAGE 2.4, GOVERN 2.1 | RI-14, RI-18 |  |

## Controls

### HM-01

**Agent registry and approvals** (built)

Every agent has a typed manifest naming its owner (a person listed in registry/owners.yaml, never a team), risk tier, status, model, tools and data classes. Unregistered, inactive or unapproved agents are refused at run time: the manifest digest and the system-prompt template hash must match registry/approvals.yaml, and the template hash is recorded on every call. CI fails on an unapproved change.

- **Code:** [`hall_monitor/registry.py`](../hall_monitor/registry.py) `Registry`; [`hall_monitor/approvals.py`](../hall_monitor/approvals.py) `current_approvals`; [`registry/approvals.yaml`](../registry/approvals.yaml)
- **Tests:** [`tests/test_registry.py`](../tests/test_registry.py) `test_unregistered_agent_is_refused`; [`tests/test_registry.py`](../tests/test_registry.py) `test_unapproved_manifest_is_refused`; [`tests/test_registry.py`](../tests/test_registry.py) `test_approvals_match_the_current_manifests_and_prompts`; [`tests/test_registry.py`](../tests/test_registry.py) `test_unapproved_prompt_is_refused_at_run_time`
- **NIST AI RMF:** GOVERN 1.6 (Mechanisms are in place to inventory AI systems and are resourced according to organizational risk priorities); GOVERN 2.1 (Roles and responsibilities and lines of communication related to mapping, measuring, and managing AI risks are documented and are clear to individuals and teams throughout the organization); MAP 1.1 (Intended purposes, potentially beneficial uses, context-specific laws, norms and expectations, and prospective settings in which the AI system will be deployed are understood and documented); MANAGE 2.4 (Mechanisms are in place and applied, and responsibilities are assigned and understood, to supersede, disengage, or deactivate AI systems that demonstrate performance or outcomes inconsistent with intended use)
- **FINOS risks:** RI-05 Foundation Model Versioning; RI-18 Model Overreach / Expanded Use
- **FINOS mitigations:** MI-10 AI Model Version Pinning
- **Gaps:** Anyone who can commit can regenerate approvals.yaml. Review of that change depends on repository settings: .github/CODEOWNERS names an owner, but main has no branch protection yet, so it isn't enforced. Approvals pin each system-prompt template, not the output schema the model is given, so a change to an output model (a new field constraint, say) needs no new approval; the API passes pydantic patterns to the model as description text, not as constraints, and they are checked only once the answer is back. The owner is checked against the list of people when the registry loads, and the kill switch (HM-19) records the owner ID given for each stop; risk tier is an inventory field that nothing reads at run time.

### HM-02

**Pinned models, no silent fallback** (built)

Each call uses the model its approved manifest names. A refusal or outage never switches to another model; the claim stops and goes to a person.

- **Code:** [`hall_monitor/agents/base.py`](../hall_monitor/agents/base.py) `run_agent`
- **Tests:** [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_every_call_records_what_ran`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_api_error_stops_the_claim`; [`tests/test_cost_routing.py`](../tests/test_cost_routing.py) `test_routing_is_off_by_default`
- **NIST AI RMF:** MANAGE 3.1 (AI risks and benefits from third-party resources are regularly monitored, and risk controls are applied and documented)
- **FINOS risks:** RI-05 Foundation Model Versioning
- **FINOS mitigations:** MI-10 AI Model Version Pinning
- **Gaps:** No fallback model, so a provider outage stops claims; availability is an accepted risk.

### HM-03

**Tool gateway with allow-list and claim scope** (built)

Agents read policy records and claims history only through MCP tools behind a gateway. Each agent is offered only the tools its manifest allows, every call is checked again, lookups are limited to the claim's own policy, and every call, allowed or refused, is recorded.

- **Code:** [`hall_monitor/tools/gateway.py`](../hall_monitor/tools/gateway.py) `ToolGateway`; [`hall_monitor/tools/server.py`](../hall_monitor/tools/server.py) `build_server`
- **Tests:** [`tests/test_tools.py`](../tests/test_tools.py) `test_agents_are_offered_only_their_allowed_tools`; [`tests/test_tools.py`](../tests/test_tools.py) `test_call_outside_the_allow_list_is_refused_and_recorded`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_a_lookup_outside_the_claims_policy_is_refused`; [`tests/test_tools.py`](../tests/test_tools.py) `test_a_tool_call_that_fails_after_it_was_allowed_is_still_recorded`; [`tests/test_tools.py`](../tests/test_tools.py) `test_agents_are_offered_only_tools_their_data_classes_allow`; [`tests/test_tools.py`](../tests/test_tools.py) `test_the_gateway_works_inside_a_running_event_loop`
- **NIST AI RMF:** GOVERN 1.4 (The risk management process and its outcomes are established through transparent policies, procedures, and other controls based on organizational risk priorities); MEASURE 2.7 (AI system security and resilience – as identified in the map function – are evaluated and documented); MANAGE 1.3 (Responses to the AI risks deemed high priority, as identified by the map function, are developed, planned, and documented)
- **FINOS risks:** RI-24 Agent Action Authorization Bypass; RI-25 Tool Chain Manipulation and Injection
- **FINOS mitigations:** MI-12 Role-Based Access Control for AI Data; MI-16 Preserving Source Data Access Controls in AI Systems; MI-18 Agent Authority Least Privilege Framework; MI-19 Tool Chain Validation and Sanitization; MI-20 MCP Server Security Governance
- **Gaps:** Tool arguments are logged as the model wrote them, so a manipulated call's text is kept. Claims history links policies by the insured's name (there's no customer ID in the data), so two customers with the same name would see each other's history; production would use a customer identifier.

### HM-04

**Routing to a person, decided in code** (built)

A code rule, not a model, decides whether a person must decide. It checks the facts behind the agents' labels: denials, payouts above $2,500 or above the amount claimed, any fraud flag or non-low risk, critic changes, unresolved critic disagreement and injection signals. Against the policy record it checks in code that the claimant is the insured (names compared ignoring case, accents and punctuation), the incident falls inside the coverage period and the claim is in USD; a claim with no policy record, no incident date or another currency goes to a person. Against the claim documents it checks that each claimed item matches its own occurrence of that figure, that the total claimed appears in them, and that the items add up to the total.

- **Code:** [`hall_monitor/governance/routing.py`](../hall_monitor/governance/routing.py) `route_claim`
- **Tests:** [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_anything_but_a_clean_small_payment_goes_to_an_adjuster`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_facts_checked_against_the_policy_send_the_claim_to_a_person`; [`tests/test_data.py`](../tests/test_data.py) `test_route_follows_routing_rule`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_a_wrong_answer_sends_the_claim_to_a_person`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_known_limit_a_missed_fraud_signal_routes_automatically`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_known_limit_an_overpayment_within_the_claim_routes_automatically`
- **Evidence:** [`evals/results/hardened-critic/findings.md`](../evals/results/hardened-critic/findings.md)
- **NIST AI RMF:** GOVERN 3.2 (Policies and procedures are in place to define and differentiate roles and responsibilities for human-AI configurations and oversight of AI systems); MAP 3.5 (Processes for human oversight are defined, assessed, and documented in accordance with organizational policies from the govern function); MANAGE 1.3 (Responses to the AI risks deemed high priority, as identified by the map function, are developed, planned, and documented)
- **FINOS risks:** RI-22 Regulatory Compliance and Oversight; RI-24 Agent Action Authorization Bypass
- **FINOS mitigations:** MI-24 Human-in-the-Loop Action Approval Gate
- **Gaps:** A wrong answer that agrees with every fact the rule checks still routes automatically: a risk step that misses a fraud signal, or a coverage step that ignores a limit but pays no more than was claimed and no more than $2,500. Tests pin both. The amount trace checks the agents' figures against what the claimant sent, not the claimant's own figures, and a figure written in both the narrative and a receipt counts twice, so wrong items whose total happens to appear in the documents would pass it.

### HM-05

**Adjuster approval gate** (built)

Claims routed to a person pause in a LangGraph workflow with state saved in SQLite. A decision must be signed with the adjuster's Ed25519 key over the claim, its fields and the evidence hash; come from a registered adjuster within their authority; quote the hash of the evidence pack the claim was paused with, which must match the pack stored under that hash in the audit database; on a USD claim pay no more than was claimed, and give a reason for any override or denial. Only senior adjusters may pay a claim with a fraud flag or above-low risk, or one the AI never assessed for fraud (a stopped claim); denying more than an adjuster's authority, or paying or denying a claim whose amount can't be compared with USD limits (not in USD, or nothing extracted), also needs a senior adjuster. The adjuster is shown the stored pack, with a warning if the checkpoint no longer matches it. A decision signed with a public demo key is marked as such in the record. A claim gets one final decision: the audit log refuses a second inside the same write transaction, so two runs or two adjusters at once, or a checkpoints file restored from a backup, can't decide or pay it again, and the claim shows as decided whatever the checkpoint holds.

- **Code:** [`hall_monitor/workflow.py`](../hall_monitor/workflow.py) `build_graph`; [`hall_monitor/governance/approval.py`](../hall_monitor/governance/approval.py) `validate_decision`; [`hall_monitor/governance/signing.py`](../hall_monitor/governance/signing.py) `verify`; [`hall_monitor/workflow.py`](../hall_monitor/workflow.py) `_check_evidence`; [`registry/adjusters.yaml`](../registry/adjusters.yaml)
- **Tests:** [`tests/test_workflow.py`](../tests/test_workflow.py) `test_claim_waits_for_an_adjuster_across_a_restart`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_payout_above_authority_needs_a_senior_adjuster`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_decisions_that_break_the_rules_are_rejected`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_a_decision_counts_only_with_the_adjusters_signature`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_an_edited_checkpoint_cant_change_the_evidence`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_the_adjuster_is_shown_the_stored_evidence_not_an_edited_checkpoint`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_an_edited_checkpoint_result_cant_relax_the_approval_rules`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_run_fresh_refuses_a_claim_that_finished_triage`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_a_claim_the_log_shows_triaged_cant_be_triaged_again_with_new_checkpoints`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_two_runs_of_the_same_claim_at_once_pay_it_once`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_two_adjusters_deciding_at_once_decide_once`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_a_restored_checkpoints_file_cant_decide_a_claim_again`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_entries_appended_to_the_log_cant_relax_the_approval_rules`; [`tests/test_approval_rules.py`](../tests/test_approval_rules.py) `test_paying_a_claim_the_ai_never_assessed_needs_a_senior_adjuster`; [`tests/test_approval_rules.py`](../tests/test_approval_rules.py) `test_denying_more_than_your_authority_needs_a_senior_adjuster`; [`tests/test_approval_rules.py`](../tests/test_approval_rules.py) `test_a_demo_key_signature_is_marked_in_the_record`; [`tests/test_approval_rules.py`](../tests/test_approval_rules.py) `test_a_claim_not_in_usd_needs_a_senior_adjuster`; [`tests/test_approval_rules.py`](../tests/test_approval_rules.py) `test_paying_a_risky_claim_with_no_flags_needs_a_senior_adjuster`
- **Evidence:** [`evals/results/hardened-critic/override.json`](../evals/results/hardened-critic/override.json)
- **NIST AI RMF:** GOVERN 3.2 (Policies and procedures are in place to define and differentiate roles and responsibilities for human-AI configurations and oversight of AI systems); MAP 3.5 (Processes for human oversight are defined, assessed, and documented in accordance with organizational policies from the govern function); MANAGE 4.1 (Post-deployment AI system monitoring plans are implemented, including mechanisms for capturing and evaluating input from users and other relevant AI actors, appeal and override, decommissioning, incident response, recovery, and change management)
- **FINOS risks:** RI-22 Regulatory Compliance and Oversight
- **FINOS mitigations:** MI-24 Human-in-the-Loop Action Approval Gate
- **Gaps:** A payout on a claim not in USD, or with no amount extracted, is capped only by the senior adjuster's authority: nothing converts currency or bounds it by the claim. The fictional adjusters' keys are public demo keys (decisions signed with them are marked); a deployment would issue keys from the firm's identity system. The evidence pack lists document names rather than the documents. The evidence pack and the triage result the approval rules read are checked against the hashes the audit log recorded at triage (the first pack, and the result logged before it: a second pack appended later makes the claim undecidable rather than replacing it); other checkpoint state isn't. Two runs of one claim at once both triage it, and both record their triage events; only the payment is once-only, and the claim then needs a person. A signed decision carries no nonce, so if a claim were ever triaged again with an identical pack, an earlier signature for it would verify again; the workflow refuses a second triage of any claim the audit log shows finished, whatever the checkpoint file holds. A crash after the evidence pack is logged but before the checkpoint is saved leaves that claim for a person to handle; it can't be restarted. The override rate is simulated with an answer-key adjuster; no real adjusters yet.

### HM-06

**Tamper-evident audit log** (built)

Each claim's triage (agent calls, tool calls, critic reviews, revisions, corrections, result) and every adjuster decision are written, as they happen, to an append-only SQLite log in which each entry's hash covers the previous one. Triggers refuse edits and deletes, and verify reports any changed, missing or reordered entry, any stored evidence pack that no longer matches its hash, and (opened read-only, as the audit CLI does) any trigger someone removed. Entries hold no claim narrative or documents; the adjuster's written reason, critic issues and a rejected submission are kept as keyed hashes (HMAC-SHA256), so the hashes alone can't confirm a guess (but see the gaps on signatures). Every read and write takes the same lock.

- **Code:** [`hall_monitor/governance/audit.py`](../hall_monitor/governance/audit.py) `AuditLog`; [`hall_monitor/audit.py`](../hall_monitor/audit.py) `main`
- **Tests:** [`tests/test_audit.py`](../tests/test_audit.py) `test_a_changed_entry_is_detected`; [`tests/test_audit.py`](../tests/test_audit.py) `test_a_consistent_rewrite_is_caught_only_by_an_anchor`; [`tests/test_audit.py`](../tests/test_audit.py) `test_the_log_holds_no_claim_text`; [`tests/test_audit.py`](../tests/test_audit.py) `test_the_log_keeps_the_adjusters_reason_as_a_hash`; [`tests/test_audit.py`](../tests/test_audit.py) `test_a_rejected_decision_logs_no_names_or_submitted_text`; [`tests/test_intake.py`](../tests/test_intake.py) `test_codes_the_log_repeats_must_be_codes`; [`tests/test_audit.py`](../tests/test_audit.py) `test_evidence_is_stored_by_hash_and_checked_by_verify`; [`tests/test_audit.py`](../tests/test_audit.py) `test_read_only_verify_reports_removed_triggers_and_malformed_entries`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_events_are_reported_as_they_happen`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_a_recording_failure_stops_the_claim`; [`tests/test_audit.py`](../tests/test_audit.py) `test_keyed_hashes_depend_on_the_key`; [`tests/test_audit.py`](../tests/test_audit.py) `test_reads_and_writes_from_many_threads_stay_consistent`; [`tests/test_audit.py`](../tests/test_audit.py) `test_the_cli_reports_a_dropped_table_as_tampering`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_a_rejection_logs_the_submission_as_a_keyed_hash`
- **NIST AI RMF:** MEASURE 2.8 (Risks associated with transparency and accountability – as identified in the map function – are examined and documented); MANAGE 4.1 (Post-deployment AI system monitoring plans are implemented, including mechanisms for capturing and evaluating input from users and other relevant AI actors, appeal and override, decommissioning, incident response, recovery, and change management)
- **FINOS risks:** RI-17 Lack of Explainability; RI-22 Regulatory Compliance and Oversight
- **FINOS mitigations:** MI-04 AI System Observability; MI-21 Agent Decision Audit and Explainability
- **Gaps:** Someone with write access to the file can rewrite the whole chain consistently; only a chain head copied elsewhere catches that, and copying it out is manual. Without HALL_MONITOR_AUDIT_KEY the hash key is a file beside the log, so someone with file access has it too; production keeps it in a secrets manager. Entries do hold policy numbers, decision fields and tool-call arguments. The currency and policy number the model extracts are repeated in routing reasons and corrections, so both must be code-shaped; tool-call arguments are still logged as the model wrote them. An unforeseen error stops the claim with its type only, since its text can repeat those arguments. The final decision entry keeps the adjuster's Ed25519 signature, which covers the reason, so anyone with the log and the public key can confirm a guessed reason: the keyed hash doesn't protect a signed reason yet. The audit CLI reports a file with no readable log as tampered with (exit 3), not missing (exit 1).

### HM-07

**Critic review with revision history** (built)

A second agent checks intake, coverage and risk against the documents and sends errors back, up to twice. Every revision is kept, and one that changes a decision field sends the claim to a person.

- **Code:** [`hall_monitor/agents/critic.py`](../hall_monitor/agents/critic.py) `run_critic`; [`hall_monitor/pipeline.py`](../hall_monitor/pipeline.py) `_triage`
- **Tests:** [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_critic_sends_coverage_back_and_approves_the_revision`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_critic_change_to_the_amount_is_recorded_and_goes_to_a_person`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_unresolved_disagreement_goes_to_an_adjuster`
- **Evidence:** [`evals/results/critic-seeded/findings.md`](../evals/results/critic-seeded/findings.md)
- **NIST AI RMF:** MEASURE 2.3 (AI system performance or assurance criteria are measured qualitatively or quantitatively and demonstrated for conditions similar to deployment setting(s)); MEASURE 2.5 (The AI system to be deployed is demonstrated to be valid and reliable); MANAGE 1.3 (Responses to the AI risks deemed high priority, as identified by the map function, are developed, planned, and documented)
- **FINOS risks:** RI-04 Hallucination and Inaccurate Outputs; RI-06 Non-Deterministic Behaviour; RI-14 Inadequate System Alignment
- **FINOS mitigations:** MI-15 Using Large Language Models for Automated Evaluation (LLM-as-a-Judge); MI-21 Agent Decision Audit and Explainability

### HM-08

**Failures stop the claim and go to a person** (built)

A refusal, API error, invalid output (including output of the wrong type, or a turn that ended any way but end_turn), refused hall pass, missing policy or any unexpected error stops the claim, records why, keeps the cost of calls already made, and routes it to an adjuster.

- **Code:** [`hall_monitor/pipeline.py`](../hall_monitor/pipeline.py) `triage`
- **Tests:** [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_refusal_stops_the_claim_and_keeps_its_cost`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_api_error_stops_the_claim`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_unexpected_error_stops_the_claim`; [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_pipeline_stops_when_the_policy_cant_be_found`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_a_missing_or_failed_answer_stops_the_claim_for_a_person`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_an_answer_of_the_wrong_type_stops_the_claim_for_a_person`; [`tests/test_agent_faults.py`](../tests/test_agent_faults.py) `test_an_incomplete_answer_stops_the_claim_for_a_person`
- **NIST AI RMF:** MANAGE 2.4 (Mechanisms are in place and applied, and responsibilities are assigned and understood, to supersede, disengage, or deactivate AI systems that demonstrate performance or outcomes inconsistent with intended use); MANAGE 4.3 (Incidents and errors are communicated to relevant AI actors, including affected communities)
- **FINOS risks:** RI-07 Availability of Foundational Model
- **FINOS mitigations:** MI-24 Human-in-the-Loop Action Approval Gate

### HM-09

**Prompt-injection defences** (built, results pending)

Claim text and model output are escaped and fenced as untrusted. A code detector flags text aimed at the AI (override instructions, fake markup, claimed approval, requests to skip review, internal tool names) and sends the claim to a person.

- **Code:** [`hall_monitor/agents/base.py`](../hall_monitor/agents/base.py) `escape`; [`hall_monitor/gateway/injection.py`](../hall_monitor/gateway/injection.py) `detect_instructions`
- **Tests:** [`tests/test_pipeline.py`](../tests/test_pipeline.py) `test_claim_text_cannot_close_its_fence_or_forge_feedback`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_no_real_claim_is_flagged`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_flagged_claim_goes_to_a_person_even_if_otherwise_automatic`
- **Evidence:** *Pending live run:* `uv run python -m hall_monitor.evals.adversarial`
- **NIST AI RMF:** MEASURE 2.7 (AI system security and resilience – as identified in the map function – are evaluated and documented); MANAGE 1.3 (Responses to the AI risks deemed high priority, as identified by the map function, are developed, planned, and documented)
- **FINOS risks:** RI-10 Prompt Injection; RI-14 Inadequate System Alignment; RI-25 Tool Chain Manipulation and Injection; RI-28 Multi-Agent Trust Boundary Violations
- **FINOS mitigations:** MI-03 User/App/Model Firewalling/Filtering; MI-17 AI Firewall Implementation and Management; MI-19 Tool Chain Validation and Sanitization
- **Gaps:** Pattern detection misses wording it wasn't written for; the adversarial suite measures that.

### HM-10

**Identifier redaction and data minimisation** (built)

Dates of birth, emails, phone, card, passport and SSN numbers, IBANs, and tax IDs, bank account and routing numbers labelled as such are removed from the claim text, attachment file names and tool results before any model sees them. Each agent input and each tool declares the data classes it carries, and anything beyond the agent's manifest is refused before the model or tool is called, and agents aren't offered tools they'd be refused. The recommender gets a structured summary with no names, claim text or reasoning; its claim type and clause numbers can reveal a medical claim, so it is approved for health data. Identifiers removed from tool results are counted on each tool call.

- **Code:** [`hall_monitor/gateway/redaction.py`](../hall_monitor/gateway/redaction.py) `redact_claim`; [`hall_monitor/agents/recommendation.py`](../hall_monitor/agents/recommendation.py) `recommendation_input`; [`hall_monitor/registry.py`](../hall_monitor/registry.py) `Registry`; [`hall_monitor/tools/server.py`](../hall_monitor/tools/server.py)
- **Tests:** [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_no_identifier_reaches_any_model`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_redaction_never_removes_evidence_from_a_real_claim`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_recommender_sees_structured_fields_only`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_an_input_beyond_the_manifests_data_classes_is_refused_before_the_model`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_a_tool_beyond_the_agents_data_classes_is_refused`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_identifiers_are_redacted_from_tool_results_and_file_names`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_tax_ids_and_bank_details_are_redacted`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_bank_and_tax_patterns_leave_ordinary_evidence_alone`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_redaction_stays_fast_on_long_runs_of_space`
- **NIST AI RMF:** MEASURE 2.10 (Privacy risk of the AI system – as identified in the map function – is examined and documented)
- **FINOS risks:** RI-01 Information Leaked To Hosted Model
- **FINOS mitigations:** MI-01 AI Data Leakage Prevention and Detection; MI-06 Data Quality & Classification/Sensitivity; MI-12 Role-Based Access Control for AI Data
- **Gaps:** Names and medical details still reach the agents that need them to decide. Tax IDs, bank account and routing numbers are caught only after a label ("TIN", "tax ID", "account number", "acct #", "A/C no.", "routing"): an unlabelled number, or "account" with no "number", "no." or "#", is missed, because an unlabelled run of digits could be an amount, a date, an invoice or a booking number. IBANs must pass their checksum and use spaces or dashes, or none; lower case is caught only after "IBAN". Data classes are declared per input by code, not detected from content, so a wrong declaration isn't caught. Pattern redaction misses unusual formats.

### HM-11

**Telemetry without claim content** (built)

Langfuse traces carry IDs, versions, hashes and token counts only: trace decorators don't capture function inputs or outputs.

- **Code:** [`hall_monitor/tracing.py`](../hall_monitor/tracing.py) `observe`
- **Tests:** [`tests/test_controls.py`](../tests/test_controls.py) `test_traces_never_capture_inputs_or_outputs`
- **NIST AI RMF:** MEASURE 2.10 (Privacy risk of the AI system – as identified in the map function – is examined and documented)
- **FINOS risks:** RI-01 Information Leaked To Hosted Model
- **FINOS mitigations:** MI-01 AI Data Leakage Prevention and Detection; MI-04 AI System Observability
- **Gaps:** Only the decorators are tested. Spans from any SDK instrumentation stay metadata-only only if it isn't configured to capture content; nothing in the code prevents that.

### HM-12

**Eval harness and published results** (built)

Deterministic scoring against answer keys: extraction, coverage reasoning with clause precision, risk flags, next step, routing, hallucinated values, cost and simulated override rate. Every run is published with its findings, including the misses and answer-key changes.

- **Code:** [`hall_monitor/evals/scoring.py`](../hall_monitor/evals/scoring.py) `score_run`; [`hall_monitor/evals/baseline.py`](../hall_monitor/evals/baseline.py) `main`
- **Tests:** [`tests/test_scoring.py`](../tests/test_scoring.py) `test_ground_truth_scores_perfectly`; [`tests/test_scoring.py`](../tests/test_scoring.py) `test_failed_runs_count_against_every_metric`
- **Evidence:** [`evals/results/baseline-26/findings.md`](../evals/results/baseline-26/findings.md); [`evals/results/hardened-critic/findings.md`](../evals/results/hardened-critic/findings.md)
- **NIST AI RMF:** MEASURE 1.1 (Approaches and metrics for measurement of AI risks enumerated during the map function are selected for implementation starting with the most significant AI risks); MEASURE 2.1 (Test sets, metrics, and details about the tools used during TEVV are documented); MEASURE 2.3 (AI system performance or assurance criteria are measured qualitatively or quantitatively and demonstrated for conditions similar to deployment setting(s)); MEASURE 2.5 (The AI system to be deployed is demonstrated to be valid and reliable)
- **FINOS risks:** RI-04 Hallucination and Inaccurate Outputs; RI-06 Non-Deterministic Behaviour
- **FINOS mitigations:** MI-05 System Acceptance Testing
- **Gaps:** Published results were scored on earlier agent versions (named in each findings note). Coverage counts as correct when every deciding clause is cited, even with extra clauses; clause precision is reported separately.

### HM-13

**Grounding checks and evidence for every decision** (built)

Every extracted name, policy number, place and amount, and every cited clause, must trace to the claim or the policy wording. Each decision carries an evidence pack: the AI's findings, reasons, clauses, lookups and corrections.

- **Code:** [`hall_monitor/evals/scoring.py`](../hall_monitor/evals/scoring.py) `unsupported_values`; [`hall_monitor/governance/approval.py`](../hall_monitor/governance/approval.py) `build_evidence_pack`
- **Tests:** [`tests/test_scoring.py`](../tests/test_scoring.py) `test_invented_amount_and_clause_count_as_hallucinations`; [`tests/test_workflow.py`](../tests/test_workflow.py) `test_evidence_hash_is_stable_and_tracks_content`
- **Evidence:** [`evals/results/hardened-critic/report.md`](../evals/results/hardened-critic/report.md)
- **NIST AI RMF:** MEASURE 2.9 (The AI model is explained, validated, and documented, and AI system output is interpreted within its context – as identified in the map function – to inform responsible use and governance)
- **FINOS risks:** RI-04 Hallucination and Inaccurate Outputs; RI-17 Lack of Explainability
- **FINOS mitigations:** MI-13 Providing Citations and Source Traceability for AI-Generated Information; MI-21 Agent Decision Audit and Explainability
- **Gaps:** Only extracted values and cited clauses are checked; reasoning text isn't.

### HM-14

**Counterfactual fairness test** (built, results pending)

Each claim is re-run under names associated with six groups, swapped everywhere the name appears, counting changes in next step, route or payout, and per-group payment rates.

- **Code:** [`hall_monitor/evals/fairness.py`](../hall_monitor/evals/fairness.py) `name_variant`
- **Tests:** [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_name_swap_leaves_no_trace_of_the_original`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_fairness_runner_finds_no_flips_when_the_outcome_cannot_change`
- **Evidence:** *Pending live run:* `uv run python -m hall_monitor.evals.fairness`
- **NIST AI RMF:** MEASURE 2.11 (Fairness and bias – as identified in the map function – are evaluated and results are documented)
- **FINOS risks:** RI-16 Bias and Discrimination
- **FINOS mitigations:** MI-05 System Acceptance Testing
- **Gaps:** Names only; pronouns, ages and addresses aren't varied.

### HM-15

**Adversarial suite** (built, results pending)

Ten attacks planted in real claims, scored on whether any raised a payout, turned a denial into a payment, avoided a person, read another policy's records or leaked a redacted value.

- **Code:** [`hall_monitor/evals/adversarial.py`](../hall_monitor/evals/adversarial.py) `score`; [`data/synthetic/adversarial/cases.json`](../data/synthetic/adversarial/cases.json)
- **Tests:** [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_adversarial_runner_scores_a_correct_pipeline_as_holding`; [`tests/test_privacy_security.py`](../tests/test_privacy_security.py) `test_adversarial_runner_catches_an_inflated_payout`
- **Evidence:** *Pending live run:* `uv run python -m hall_monitor.evals.adversarial`
- **NIST AI RMF:** MEASURE 2.7 (AI system security and resilience – as identified in the map function – are evaluated and documented)
- **FINOS risks:** RI-10 Prompt Injection; RI-24 Agent Action Authorization Bypass; RI-25 Tool Chain Manipulation and Injection
- **FINOS mitigations:** MI-05 System Acceptance Testing

### HM-16

**Cost per claim and economy model routing** (built)

Cost per claim, cache-aware and by agent, on every result and in the audit log; the policy wording is prompt-cached. A manifest may approve a cheaper model for small claims, off by default until it is evaluated; critic-driven revisions always use the main model.

- **Code:** [`hall_monitor/gateway/cost.py`](../hall_monitor/gateway/cost.py) `call_cost`; [`hall_monitor/pipeline.py`](../hall_monitor/pipeline.py) `_economy`
- **Tests:** [`tests/test_cost_routing.py`](../tests/test_cost_routing.py) `test_cost_prices_cached_input_separately`; [`tests/test_cost_routing.py`](../tests/test_cost_routing.py) `test_critic_revision_of_economy_work_uses_the_main_model`
- **NIST AI RMF:** MEASURE 3.1 (Approaches, personnel, and documentation are in place to regularly identify and track existing, unanticipated, and emergent AI risks based on factors such as intended and actual performance in deployed contexts); MANAGE 3.1 (AI risks and benefits from third-party resources are regularly monitored, and risk controls are applied and documented)
- **FINOS risks:** 
- **FINOS mitigations:** MI-09 AI System Alerting and Denial of Wallet (DoW) / Spend Monitoring
- **Gaps:** No spend alerting; economy routing is unmeasured and off.

### HM-17

**CI checks and the eval gate** (built)

Every pull request runs lint, the offline tests, the approvals check, this register's checks, and the eval gate, which fails when a published score is below its threshold and flags results scored on an older agent version or prompt than the one approved, or that don't record their versions.

- **Code:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml); [`hall_monitor/evals/gate.py`](../hall_monitor/evals/gate.py) `run_gate`; [`evals/gate.yaml`](../evals/gate.yaml)
- **Tests:** [`tests/test_gate.py`](../tests/test_gate.py) `test_a_score_below_threshold_fails`; [`tests/test_gate.py`](../tests/test_gate.py) `test_stale_results_are_flagged`; [`tests/test_gate.py`](../tests/test_gate.py) `test_results_without_agent_versions_count_as_stale`; [`tests/test_gate.py`](../tests/test_gate.py) `test_a_changed_prompt_makes_a_result_stale`
- **NIST AI RMF:** MEASURE 3.1 (Approaches, personnel, and documentation are in place to regularly identify and track existing, unanticipated, and emergent AI risks based on factors such as intended and actual performance in deployed contexts)
- **FINOS risks:** RI-05 Foundation Model Versioning; RI-06 Non-Deterministic Behaviour
- **FINOS mitigations:** MI-05 System Acceptance Testing; MI-10 AI Model Version Pinning
- **Gaps:** The gate checks published results; it doesn't run live evals itself (that costs money and needs an API key), so a stale result is only a warning until a fresh baseline exists. Until then the gate doesn't block a regression. Metrics other than unsafe routes are read from the summary each run wrote.

### HM-18

**Synthetic data and documented limits** (built)

All data is synthetic, with answer keys whose changes are dated and justified. Each published run states what it can't show: sample size, one author, run-to-run variation.

- **Code:** [`data/synthetic/README.md`](../data/synthetic/README.md)
- **Tests:** [`tests/test_data.py`](../tests/test_data.py) `test_ground_truth_matches_claim`; [`tests/test_data.py`](../tests/test_data.py) `test_cited_clauses_exist_in_wording`
- **Evidence:** [`evals/results/hardened-critic/findings.md`](../evals/results/hardened-critic/findings.md)
- **NIST AI RMF:** MAP 1.1 (Intended purposes, potentially beneficial uses, context-specific laws, norms and expectations, and prospective settings in which the AI system will be deployed are understood and documented); MAP 2.1 (The specific tasks and methods used to implement the tasks that the AI system will support are defined (e); MEASURE 1.1 (Approaches and metrics for measurement of AI risks enumerated during the map function are selected for implementation starting with the most significant AI risks); MEASURE 2.5 (The AI system to be deployed is demonstrated to be valid and reliable)
- **FINOS risks:** RI-19 Data Quality and Drift
- **FINOS mitigations:** MI-06 Data Quality & Classification/Sensitivity
- **Gaps:** Data quality is documented, not monitored; there is no drift detection.

### HM-19

**Kill switch** (built)

An agent's owner can stop it by adding an entry to registry/stops.yaml (python -m hall_monitor.kill_switch stop), which changes no manifest or approval and has no code owner. The registry reads the stop list on every check, so a running process refuses the agent on its next model call, including the next tool round of a call already under way, and on its next tool call; a stopped agent stops the claim and sends it to a person. Every agent a claim's result rests on is checked again just before routing, so a claim that used the answer of an agent stopped after it gave it also goes to a person. The stop list fails closed: if it is missing, unreadable or has a key twice, names an unknown agent or a person not in registry/owners.yaml, or approvals clear a stop it doesn't hold, every agent is refused. Restarting needs the stop cleared in registry/approvals.yaml (python -m hall_monitor.approvals --resume), a reviewed approval. CI fails a pull request that removes or edits a stop. Refusals carry the stop and owner IDs, never the reason.

- **Code:** [`hall_monitor/kill_switch.py`](../hall_monitor/kill_switch.py) `add_stop`; [`hall_monitor/kill_switch.py`](../hall_monitor/kill_switch.py) `check_append_only`; [`hall_monitor/registry.py`](../hall_monitor/registry.py) `Registry`; [`hall_monitor/agents/base.py`](../hall_monitor/agents/base.py) `run_agent`; [`registry/stops.yaml`](../registry/stops.yaml); [`registry/owners.yaml`](../registry/owners.yaml); [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
- **Tests:** [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_stop_takes_effect_on_the_next_call`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_stop_needs_no_approval`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_stop_during_an_agents_tool_rounds_halts_its_next_model_request`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_stopped_agent_stops_the_claim_and_sends_it_to_a_person`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_stop_after_an_agent_answers_still_sends_the_claim_to_a_person`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_a_cleared_stop_that_does_not_exist_refuses_every_agent`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_code_the_kill_switch_relies_on_has_a_code_owner`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_clearing_the_stop_in_a_reviewed_approval_restarts_it`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_an_unreadable_stop_list_refuses_every_agent`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_removing_or_editing_a_stop_fails_the_check`; [`tests/test_kill_switch.py`](../tests/test_kill_switch.py) `test_every_agent_is_owned_by_a_named_person`
- **NIST AI RMF:** MANAGE 2.4 (Mechanisms are in place and applied, and responsibilities are assigned and understood, to supersede, disengage, or deactivate AI systems that demonstrate performance or outcomes inconsistent with intended use); GOVERN 2.1 (Roles and responsibilities and lines of communication related to mapping, measuring, and managing AI risks are documented and are clear to individuals and teams throughout the organization)
- **FINOS risks:** RI-14 Inadequate System Alignment; RI-18 Model Overreach / Expanded Use
- **FINOS mitigations:** 
- **Gaps:** The stop list is a file beside the code: a stop reaches a running process only if that process reads the same file, so a deployment needs it in a store the owner can write to at once. Any listed owner can stop any agent, not only its own owner; that is deliberate, because a wrong stop fails safe. The owner ID on a stop is the one the person gave; nothing authenticates it. A model or tool call already in flight finishes, and the workflow's automatic finish, just after routing, isn't checked again. Clearing a stop takes effect when the registry is next loaded. Append-only is checked only on pull requests. CODEOWNERS covers the code, the CI workflow and the approvals, so a pull request that deleted a stop and disabled the check would need the owner's review, but only once branch protection is on, and it isn't: until then a direct push to main, and always an edit on the host, could delete a stop and restart the agent unreviewed. Stops are not written to the audit log as events; a stopped agent shows there only as the stopped claim's reason.

## NIST AI RMF coverage

| Subcategory | Controls |
|---|---|
| **GOVERN 1.1** Legal and regulatory requirements involving AI are understood, managed, and documented. | Not covered: Legal and regulatory requirements for claims handling aren't analysed; this is a reference implementation, not legal advice. |
| **GOVERN 1.4** The risk management process and its outcomes are established through transparent policies, procedures, and other controls based on organizational risk priorities. | [HM-03](#hm-03) |
| **GOVERN 1.5** Ongoing monitoring and periodic review of the risk management process and its outcomes are planned and organizational roles and responsibilities clearly defined, including determining the frequency of periodic review. | Not covered: CI checks every change, but nothing schedules periodic review of the risk process or its outcomes. |
| **GOVERN 1.6** Mechanisms are in place to inventory AI systems and are resourced according to organizational risk priorities. | [HM-01](#hm-01) |
| **GOVERN 2.1** Roles and responsibilities and lines of communication related to mapping, measuring, and managing AI risks are documented and are clear to individuals and teams throughout the organization. | [HM-01](#hm-01), [HM-19](#hm-19) |
| **GOVERN 3.2** Policies and procedures are in place to define and differentiate roles and responsibilities for human-AI configurations and oversight of AI systems. | [HM-04](#hm-04), [HM-05](#hm-05) |
| **GOVERN 4.1** Organizational policies and practices are in place to foster a critical thinking and safety-first mindset in the design, development, deployment, and uses of AI systems to minimize potential negative impacts. | Not covered: Organisational culture and practices are outside a codebase. |
| **GOVERN 6.1** Policies and procedures are in place that address AI risks associated with third-party entities, including risks of infringement of a third-party's intellectual property or other rights. | Not covered: Third-party policies, including intellectual-property risk from the model provider, aren't addressed; the model is pinned, not assessed. |
| **MAP 1.1** Intended purposes, potentially beneficial uses, context-specific laws, norms and expectations, and prospective settings in which the AI system will be deployed are understood and documented. | [HM-01](#hm-01), [HM-18](#hm-18) |
| **MAP 2.1** The specific tasks and methods used to implement the tasks that the AI system will support are defined (e.g., classifiers, generative models, recommenders). | [HM-18](#hm-18) |
| **MAP 3.5** Processes for human oversight are defined, assessed, and documented in accordance with organizational policies from the govern function. | [HM-04](#hm-04), [HM-05](#hm-05) |
| **MAP 4.1** Approaches for mapping AI technology and legal risks of its components – including the use of third-party data or software – are in place, followed, and documented, as are risks of infringement of a third party's intellectual property or other rights. | Not covered: Legal and intellectual-property risks of third-party components aren't mapped. |
| **MEASURE 1.1** Approaches and metrics for measurement of AI risks enumerated during the map function are selected for implementation starting with the most significant AI risks. The risks or trustworthiness characteristics that will not – or cannot – be measured are properly documented. | [HM-12](#hm-12), [HM-18](#hm-18) |
| **MEASURE 2.1** Test sets, metrics, and details about the tools used during TEVV are documented. | [HM-12](#hm-12) |
| **MEASURE 2.3** AI system performance or assurance criteria are measured qualitatively or quantitatively and demonstrated for conditions similar to deployment setting(s). Measures are documented. | [HM-07](#hm-07), [HM-12](#hm-12) |
| **MEASURE 2.5** The AI system to be deployed is demonstrated to be valid and reliable. Limitations of the generalizability beyond the conditions under which the technology was developed are documented. | [HM-07](#hm-07), [HM-12](#hm-12), [HM-18](#hm-18) |
| **MEASURE 2.7** AI system security and resilience – as identified in the map function – are evaluated and documented. | [HM-03](#hm-03), [HM-09](#hm-09), [HM-15](#hm-15) |
| **MEASURE 2.8** Risks associated with transparency and accountability – as identified in the map function – are examined and documented. | [HM-06](#hm-06) |
| **MEASURE 2.9** The AI model is explained, validated, and documented, and AI system output is interpreted within its context – as identified in the map function – to inform responsible use and governance. | [HM-13](#hm-13) |
| **MEASURE 2.10** Privacy risk of the AI system – as identified in the map function – is examined and documented. | [HM-10](#hm-10), [HM-11](#hm-11) |
| **MEASURE 2.11** Fairness and bias – as identified in the map function – are evaluated and results are documented. | [HM-14](#hm-14) |
| **MEASURE 3.1** Approaches, personnel, and documentation are in place to regularly identify and track existing, unanticipated, and emergent AI risks based on factors such as intended and actual performance in deployed contexts. | [HM-16](#hm-16), [HM-17](#hm-17) |
| **MANAGE 1.3** Responses to the AI risks deemed high priority, as identified by the map function, are developed, planned, and documented. Risk response options can include mitigating, transferring, avoiding, or accepting. | [HM-03](#hm-03), [HM-04](#hm-04), [HM-07](#hm-07), [HM-09](#hm-09) |
| **MANAGE 2.4** Mechanisms are in place and applied, and responsibilities are assigned and understood, to supersede, disengage, or deactivate AI systems that demonstrate performance or outcomes inconsistent with intended use. | [HM-01](#hm-01), [HM-08](#hm-08), [HM-19](#hm-19) |
| **MANAGE 3.1** AI risks and benefits from third-party resources are regularly monitored, and risk controls are applied and documented. | [HM-02](#hm-02), [HM-16](#hm-16) |
| **MANAGE 4.1** Post-deployment AI system monitoring plans are implemented, including mechanisms for capturing and evaluating input from users and other relevant AI actors, appeal and override, decommissioning, incident response, recovery, and change management. | [HM-05](#hm-05), [HM-06](#hm-06) |
| **MANAGE 4.3** Incidents and errors are communicated to relevant AI actors, including affected communities. Processes for tracking, responding to, and recovering from incidents and errors are followed and documented. | [HM-08](#hm-08) |

## FINOS AIGF risks

| ID | Title | Controls |
|---|---|---|
| RI-01 | Information Leaked To Hosted Model | [HM-10](#hm-10), [HM-11](#hm-11) |
| RI-02 | Information Leaked to Vector Store | Not covered: There is no vector store or retrieval index. |
| RI-04 | Hallucination and Inaccurate Outputs | [HM-07](#hm-07), [HM-12](#hm-12), [HM-13](#hm-13) |
| RI-05 | Foundation Model Versioning | [HM-01](#hm-01), [HM-02](#hm-02), [HM-17](#hm-17) |
| RI-06 | Non-Deterministic Behaviour | [HM-07](#hm-07), [HM-12](#hm-12), [HM-17](#hm-17) |
| RI-07 | Availability of Foundational Model | [HM-08](#hm-08) |
| RI-08 | Tampering With the Foundational Model | Not covered: Tampering with the foundation model is the provider's control; the repo only pins the model. |
| RI-09 | Data Poisoning | Not covered: There is no training or fine-tuning, so no training data to poison. |
| RI-10 | Prompt Injection | [HM-09](#hm-09), [HM-15](#hm-15) |
| RI-14 | Inadequate System Alignment | [HM-07](#hm-07), [HM-09](#hm-09), [HM-19](#hm-19) |
| RI-16 | Bias and Discrimination | [HM-14](#hm-14) |
| RI-17 | Lack of Explainability | [HM-06](#hm-06), [HM-13](#hm-13) |
| RI-18 | Model Overreach / Expanded Use | [HM-01](#hm-01), [HM-19](#hm-19) |
| RI-19 | Data Quality and Drift | [HM-18](#hm-18) |
| RI-20 | Reputational Risk | Not covered: Reputational risk is a business judgement; publishing results, failures included, is the only part this repo touches. |
| RI-22 | Regulatory Compliance and Oversight | [HM-04](#hm-04), [HM-05](#hm-05), [HM-06](#hm-06) |
| RI-23 | Intellectual Property (IP) and Copyright | Not covered: Intellectual property in model output isn't assessed. |
| RI-24 | Agent Action Authorization Bypass | [HM-03](#hm-03), [HM-04](#hm-04), [HM-15](#hm-15) |
| RI-25 | Tool Chain Manipulation and Injection | [HM-03](#hm-03), [HM-09](#hm-09), [HM-15](#hm-15) |
| RI-26 | MCP Server Supply Chain Compromise | Not covered: The MCP server is first-party and runs in-process; nothing checks a third-party server's integrity because there isn't one. |
| RI-27 | Agent State Persistence Poisoning | Not covered: Workflow checkpoints are trusted local state; they aren't integrity-checked like the audit log. |
| RI-28 | Multi-Agent Trust Boundary Violations | [HM-09](#hm-09) |
| RI-29 | Agent-Mediated Credential Discovery and Harvesting | Not covered: Agents hold no credentials; the API key stays in the host process. |
| RI-30 | Skill/Plugin Supply Chain Compromise | Not covered: The system loads no skills or plugins. |

## FINOS AIGF mitigations

| ID | Title | Controls |
|---|---|---|
| MI-01 | AI Data Leakage Prevention and Detection | [HM-10](#hm-10), [HM-11](#hm-11) |
| MI-02 | Data Filtering From External Knowledge Bases | Not covered: There is no external knowledge base or retrieval; the policy wording is fixed reference text. |
| MI-03 | User/App/Model Firewalling/Filtering | [HM-09](#hm-09) |
| MI-04 | AI System Observability | [HM-06](#hm-06), [HM-11](#hm-11) |
| MI-05 | System Acceptance Testing | [HM-12](#hm-12), [HM-14](#hm-14), [HM-15](#hm-15), [HM-17](#hm-17) |
| MI-06 | Data Quality & Classification/Sensitivity | [HM-10](#hm-10), [HM-18](#hm-18) |
| MI-07 | Legal and Contractual Frameworks for AI Systems | Not covered: Legal and contractual terms with the model provider are out of scope. |
| MI-08 | Quality of Service (QoS) and DDoS Prevention for AI Systems | Not covered: There is no rate limiting or denial-of-service protection; nothing is exposed as a service. |
| MI-09 | AI System Alerting and Denial of Wallet (DoW) / Spend Monitoring | [HM-16](#hm-16) |
| MI-10 | AI Model Version Pinning | [HM-01](#hm-01), [HM-02](#hm-02), [HM-17](#hm-17) |
| MI-11 | Human Feedback Loop for AI Systems | Not covered: Adjuster overrides are recorded but not fed back into prompts, answer keys or evals. |
| MI-12 | Role-Based Access Control for AI Data | [HM-03](#hm-03), [HM-10](#hm-10) |
| MI-13 | Providing Citations and Source Traceability for AI-Generated Information | [HM-13](#hm-13) |
| MI-14 | Encryption of AI Data at Rest | Not covered: The audit log and checkpoints are unencrypted SQLite files. |
| MI-15 | Using Large Language Models for Automated Evaluation (LLM-as-a-Judge) | [HM-07](#hm-07) |
| MI-16 | Preserving Source Data Access Controls in AI Systems | [HM-03](#hm-03) |
| MI-17 | AI Firewall Implementation and Management | [HM-09](#hm-09) |
| MI-18 | Agent Authority Least Privilege Framework | [HM-03](#hm-03) |
| MI-19 | Tool Chain Validation and Sanitization | [HM-03](#hm-03), [HM-09](#hm-09) |
| MI-20 | MCP Server Security Governance | [HM-03](#hm-03) |
| MI-21 | Agent Decision Audit and Explainability | [HM-06](#hm-06), [HM-07](#hm-07), [HM-13](#hm-13) |
| MI-22 | Multi-Agent Isolation and Segmentation | Not covered: Agents run in one process; they're separated by allow-lists and claim scope, not isolation. |
| MI-23 | Agentic System Credential Protection Framework | Not covered: Agents hold no credentials (see RI-29), so there is nothing for them to protect. |
| MI-24 | Human-in-the-Loop Action Approval Gate | [HM-04](#hm-04), [HM-05](#hm-05), [HM-08](#hm-08) |
| MI-25 | Skill/Plugin Integrity and Governance | Not covered: The system loads no skills or plugins (see RI-30). |

## Audit log events

| Event | Evidence for |
|---|---|
| `claim_submitted` | [HM-09](#hm-09), [HM-10](#hm-10) |
| `agent_call` | [HM-01](#hm-01), [HM-02](#hm-02), [HM-16](#hm-16) |
| `tool_call` | [HM-03](#hm-03) |
| `critic_review` | [HM-07](#hm-07) |
| `revision` | [HM-07](#hm-07) |
| `code_correction` | [HM-08](#hm-08) |
| `triage_result` | [HM-04](#hm-04), [HM-16](#hm-16) |
| `evidence_pack` | [HM-05](#hm-05), [HM-13](#hm-13) |
| `decision_rejected` | [HM-05](#hm-05) |
| `final_decision` | [HM-04](#hm-04), [HM-05](#hm-05) |

## Sources

- NIST AI Risk Management Framework 1.0 (NIST AI 100-1), Core: <https://airc.nist.gov/airmf-resources/airmf/5-sec-core/>. The source page says AI RMF 1.0 is being updated and a revised version is in progress.
- FINOS AI Governance Framework v3 (October 26, 2026): <https://air-governance-framework.finos.org/single-page.html>
