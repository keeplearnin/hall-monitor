# Architecture decision records

Each record explains one design choice: the problem, what was decided, what else was considered,
and what it costs. They are written for an engineer or reviewer who wants to know why the
system is built the way it is, and each links to the code and the control it supports
([controls register](../controls.md)).

| ADR | Decision | Control |
|---|---|---|
| [0001](0001-routing-decided-in-code.md) | A code rule, not a model, decides who decides | HM-04 |
| [0002](0002-pinned-models-no-fallback.md) | Each agent runs on its pinned model, with no silent fallback | HM-02 |
| [0003](0003-approvals-pin-hashes.md) | Approvals pin the manifest digest and the prompt hash | HM-01 |
| [0004](0004-tool-gateway-claim-scope.md) | Agents reach data only through a gateway, scoped to the claim | HM-03 |
| [0005](0005-critic-as-measured-insurance.md) | A critic agent checks the work, and its value is measured | HM-07 |
| [0006](0006-plain-python-then-langgraph.md) | Plain Python for the pipeline; LangGraph only for the wait for a person | HM-05 |
| [0007](0007-failures-stop-the-claim.md) | Any failure stops the claim and sends it to a person | HM-08 |
| [0008](0008-hash-chained-audit-log.md) | A hash-chained SQLite audit log, anchored outside the database | HM-06 |
| [0009](0009-redaction-and-minimisation.md) | Remove identifiers no agent needs; keep medical details where coverage needs them | HM-10, HM-11 |
| [0010](0010-injection-defence-in-code.md) | Prompt-injection defence in layers, with the last layer in code | HM-09 |
| [0011](0011-economy-routing-off-until-evaluated.md) | Cheaper-model routing is built but off until it is evaluated | HM-16 |
| [0012](0012-deterministic-scoring.md) | Results are scored by code against answer keys, not by an LLM judge | HM-12, HM-13 |
| [0013](0013-published-results-and-gate.md) | Publish every result, including failures, and check every pull request against them | HM-17, HM-18 |
| [0014](0014-signed-decisions-and-stored-evidence.md) | Adjuster decisions are signed, and evidence is stored by its hash | HM-05, HM-06 |
| [0015](0015-kill-switch.md) | Stopping an agent needs no approval; restarting it does | HM-19 |
| [0016](0016-read-only-control-panel.md) | The control panel reads the controls and changes nothing | HM-20 |
| [0017](0017-leave-rules-in-code.md) | Leave eligibility and who decides are code, built before any agent | HM-21 |
| [0018](0018-leave-agents-explain-code-decides.md) | The leave agents read and explain; code decides and a person signs | HM-22 |
| [0019](0019-signed-hr-sign-off.md) | A person signs each leave decision, within the role the rules name | HM-23 |
| [0020](0020-leave-submission-form.md) | Leave requests are added through a separate form, not the panel | HM-24 |
| [0021](0021-no-reply-no-send.md) | The complaint desk never sends anything, enforced in code | HM-25 |

New records take the next number. A decision that changes gets a new record that supersedes the
old one; the old one stays, marked "Superseded by".
