# Regulatory mapping: NIST AI RMF, FINOS, ISO/IEC 42001 and the EU AI Act

- **NIST AI RMF 1.0 and FINOS AIGF v3** are mapped control by control in the register, with
  identifiers checked against a local list and tests that fail on a stale reference:
  [`docs/controls.md`](controls.md). That mapping is the maintained one.
- **ISO/IEC 42001 and the EU AI Act** are mapped below, informally, by an engineer, at the level
  of clauses and articles. The identifiers haven't been checked against the standard's or the
  regulation's text the way the NIST and FINOS ones are, nothing here is legal advice, and
  hall-monitor is a reference build on synthetic and public data, not a deployed system. A firm
  would have its own counsel and auditors confirm scope and wording.

## ISO/IEC 42001 (AI management system), clause level

| Clause | What it asks for, briefly | Where hall-monitor has evidence | Status |
|---|---|---|---|
| 4 Context | Scope of the AI system and interested parties | [README](../README.md), the design docs, the [inventory](model-cards.md) | Partial: scope is documented; no stakeholder analysis |
| 5 Leadership | Policy, roles, accountability | A named owner per agent ([`registry/owners.yaml`](../registry/owners.yaml)); owners can stop agents; approvals are reviewed changes | Partial: no AI policy document, no governance body |
| 6 Planning | Risk assessment and treatment, objectives | The [controls register](controls.md) with gaps and not-covered items; the [threat model](threat-model-agentic.md); eval thresholds ([`evals/gate.yaml`](../evals/gate.yaml)) | Partial: risks assessed per control, not in a formal risk register with owners and dates |
| 7 Support | Resources, competence, awareness, documented information | ADRs, runbook, model cards, review log, all in version control | Partial: no training or competence records |
| 8 Operation | Operational control, impact assessment, change control | Hall passes and approvals (HM-01/02), routing in code (HM-04), kill switch (HM-19), the agentic bounds (HM-32), change control in CI | Built for the controls listed; no AI impact assessment document |
| 9 Performance evaluation | Monitoring, measurement, internal audit, management review | Published evals and a CI gate (HM-12, HM-17), override rate (simulated), audit chain verify (HM-06), independent reviews logged ([review log](review/log.md)) | Partial: no live monitoring or drift tracking; reviews are by agents and the author, no management review |
| 10 Improvement | Nonconformity, corrective action | Every review finding fixed test-first or listed ([review log](review/log.md)) | Built (in-repo); no formal CAPA process |

## EU AI Act

**Which workflows are in scope.** Leave decisions are about employment ("decisions affecting
terms of work-related relationships"), which the Act lists as high-risk in Annex III, point 4.
Claims triage for travel insurance and a complaint desk are not on the Annex III list as written
here (Annex III names life and health insurance pricing and risk assessment, which this isn't);
a firm would confirm. So the HR leave example is treated as high-risk, and the others are held
to the same controls without claiming the Act requires it.

Obligations for a high-risk system, as the provider and deployer would see them:

| Article | Obligation, briefly | HR leave evidence | Status |
|---|---|---|---|
| Art. 9 Risk management | A continuous risk-management process | Register gaps, threat model, eval gate, reviews | Partial: not a documented lifecycle process |
| Art. 10 Data governance | Training, validation and test data quality | Synthetic answer keys with logged changes ([`data/synthetic/README.md`](../data/synthetic/README.md)); no model training here | Partial: test data only; no live-data validation |
| Art. 11 Technical documentation | Documentation of the system | ADRs, model cards, controls register, design docs | Partial: not in the Annex IV structure |
| Art. 12 Record-keeping | Automatic logging over the lifetime | Hash-chained audit log of every request, agent call, supervisor step, rules result, evidence pack and signed decision, with no free text (HM-06, HM-23, HM-32) | Built |
| Art. 13 Transparency to deployers | Instructions for use, capabilities and limits | Model cards (what each agent may decide alone, what code forces to a person, evidence), runbook | Partial: written for engineers, not as instructions for use |
| Art. 14 Human oversight | People can understand, monitor, override and stop the system | Every leave decision is signed by a person with the authority the rules name (HM-23); the code's route and role can't be lowered by any agent; owners can stop any agent mid-case (HM-19); red-team tests and the offline eval show the supervisor can't bypass it | Built (offline evidence); no live operator study |
| Art. 15 Accuracy, robustness, cybersecurity | Appropriate accuracy, resilience to errors and attacks | Fault injection, failure drills, red-team arena, injection defences, the agentic red-team suite | Partial: HR intake accuracy not measured live; no live red team |
| Art. 26 Deployer duties (incl. informing workers) | Use per instructions, oversight by competent people, monitoring, inform workers' representatives | Oversight roles enforced in code; logs kept | Gap: worker information and representative consultation are organisational, outside the code |
| Art. 27 Fundamental-rights impact assessment | Where required for certain deployers | — | Gap: not done |

**Transparency to the employee (Art. 50 and general fairness).** The employee isn't told an AI
read their request; the form (HM-24) says the request goes to HR, not how it is prepared. A gap
for a real deployment.

## Fairness for HR

The claims and complaint examples have counterfactual checks (name swap for claims, state and ZIP
for complaints, HM-14, HM-28). HR leave has no adverse-impact or four-fifths analysis: the 28
synthetic requests carry no protected attributes to group by, and eligibility is decided by rules
in code, so the agents' influence is the intake extraction. A real deployment would measure
selection rates by group on live outcomes; listed as a gap in the
[readiness checklist](readiness-checklist.md).
