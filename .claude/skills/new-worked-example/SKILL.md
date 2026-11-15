---
name: new-worked-example
description: How to add a new worked example to hall-monitor end to end — a governed, agentic workflow such as partner rebate and marketing-fund (MDF) claims, warranty and returns, or access requests — in the order the existing examples (claims, HR leave, the complaint desk) were built. Use when asked to design, build, add or plan a new use case, workflow, worked example or demo domain in this repo, or to slice one into pull requests.
---

# Adding a worked example

A worked example is a domain where AI agents prepare work and the repo's controls hold: hall
passes, routing in code, people deciding, the audit log, budgets, the kill switch. The existing
ones are the templates; copy their shape, not their code:

| Example | Package | Design | Notes |
|---|---|---|---|
| Insurance claims | `hall_monitor/` (pipeline.py, agentic.py, agents/) | README sections | The first; most mature |
| HR leave (FMLA) | `hall_monitor/hr/` | ADRs 0017–0020 | Rules first, then agents, sign-off, form |
| Complaint desk | `hall_monitor/complaints/` | `docs/complaint-desk-design.md` | Public data, never sends anything |
| Complaint crew | `hall_monitor/delegation.py`, `handoff.py` | `docs/complaint-crew-design.md` | Built in slices; agents not yet built |

`CLAUDE.md` is binding throughout. This skill only orders the work.

## Non-negotiables

- **Synthetic or public data only.** Fictional companies, partners and people. Never a real
  company's name, data or internal process in the repo (it is public). A real company may appear
  only as a cited public source (for example a published program guide), never as the subject.
- **Decisions in code, not the model.** Routing, eligibility, thresholds, approval rules,
  data-class and tool checks are code. The model extracts, judges what rules can't, and
  recommends. It never holds authority to approve, pay, send or change a payee.
- **Failures go to a person.** A refusal, outage, bad output, budget stop or kill-switch stop
  routes the case to a person with a fixed reason. Nothing invents a value to keep going.
- **Nothing personal in the audit log or traces:** IDs, keyed hashes, codes, decision fields and
  costs only. Free text the model wrote never reaches a log or a panel page.
- **Never overstate.** Scripted runs are labelled scripted; a control that is declared but not
  enforced, or results from older agent versions, are said so. Gaps go in the register.
- **Test-first.** Every rule, refusal and fix gets a test that fails before the change.
- **Live API calls only when the user approves the spend,** with the cost stated first.

## Before any code

1. **Pick the free numbers.** Next control ID, ADR, review number and roadmap milestone:

   ```bash
   grep "id: HM-" controls/controls.yaml | tail -1
   ls docs/adr | tail -3
   grep "^## Review " docs/review/log.md | tail -1
   grep -n "^[0-9]*\. " README.md | tail -2
   ```

   Numbers can collide with work on another branch: check open PRs (`gh pr list`) and renumber on
   merge if needed (PR #61 had to move from ADR 0028 / HM-32 / Review 22 to 0031 / HM-35 /
   Review 31).
2. **Write the design doc** `docs/<name>-design.md` with status **Proposed**, before code, on
   the branch. Cover: why this domain; what the AI does, what code decides, who decides and at
   what authority; the hard rules; what data the agents may see (minimum per agent); the
   untrusted inputs and how injection is handled; what is never allowed (for example payment or
   sending); the slices; what it doesn't do. Mark every assumption as one (for example approval
   thresholds no source publishes).
3. **Write the ADR** `docs/adr/NNNN-<slug>.md` (Context, Decision, Alternatives considered,
   Consequences) and add its row to `docs/adr/README.md` in number order. `tests/test_docs.py`
   requires a `- **Status:** Accepted|Proposed|Superseded by …` line, ADR numbers with no gaps,
   and a cited `HM-NN` that exists in `controls/controls.yaml`; `tests/test_controls.py` requires
   each control's `code` and `tests` (`file::symbol`) to exist. So the design doc, the ADR and the
   control land together in the slice 1 PR, with that slice's code and tests.
4. **Get the user's go-ahead on scope** if the work adds new functionality beyond what they
   asked for (ADR 0029 is still Proposed, pending the owner's confirmation).

## The data

- `data/synthetic/<name>/` with a `README.md` saying what each file is and logging every
  answer-key change with its reason (as `data/synthetic/README.md` does).
- **Hand-written answer keys**, written from the case, never from the code's own patterns (the
  desk's `data/cfpb/label_reasons.yaml` exists so a weaker pattern shows up in the eval).
- **Boundary cases on both sides of every rule** (a deadline day before and after, an amount at
  and just over a threshold), plus the known abuse patterns for the domain and at least one case
  with text aimed at the AI.
- Public parameters (deadlines, caps, rates) are fine when cited; anything invented is labelled.

## Slices, one pull request each

Each slice ends green: `pytest`, ruff, the eval gate, `no_send check`, controls regenerated,
docs current, governance review logged. The order below is the one that worked.

### 1. Rules in code, no model

- `hall_monitor/<pkg>/routing.py` (or `rules.py`): the deterministic decisions, with a fixed
  reason template for every outcome. Every reason a person sees is a template, never model text.
- `hall_monitor/<pkg>/models.py`: typed records (Pydantic, `extra="forbid"`).
- Tests on both sides of every boundary; a test that every routing reason appears in an answer
  key or is a pipeline reason (see `tests/test_hr_leave.py`).
- A CLI that scores the rules against the answer keys (`python -m hall_monitor.hr` is the model).

### 2. Agents inside the shared controls

- One module per agent: `AGENT_ID`, `PROMPT_VERSION`, `SYSTEM_PROMPT`, a typed output model, and a
  `run_<agent>()` that calls `run_agent()` in `hall_monitor/agents/base.py` — the one call path
  (hall pass, prompt hash, data classes, kill switch, budget guards, call record). Never call the
  SDK directly. Pass `carries=` with the data classes the input really holds: left out, the
  input is taken to carry every class and refused unless the manifest grants them all.
- Untrusted input is fenced and escaped (`agents/base.py` `escape`); outputs carry no free-text
  field where personal data could hide unless the design needs one. An agent that reads
  untrusted text puts an `UNTRUSTED_<X>_NOTICE` constant in its approved `SYSTEM_PROMPT` (like
  `UNTRUSTED_COMPLAINT_NOTICE`), and every agent goes in `notices` or
  `readers_of_nothing_untrusted` in `tests/test_registry.py`
  (`test_the_approved_prompt_covers_the_untrusted_claim_notice` fails otherwise).
- A manifest per agent, `registry/<agent_id>.yaml`: an owner from `registry/owners.yaml` (a
  person), risk tier, model (it must have a price in `registry/budgets.yaml`), `allowed_tools`
  only from `READ_ONLY_TOOLS` in `hall_monitor/no_send.py`, and the minimum `data_classes`. The
  file name must match the agent ID, and the ID goes in `EXPECTED_AGENTS` in
  `tests/test_registry.py` (a test fails otherwise).
- Add a `<PKG>_MODULES` tuple in `hall_monitor/approvals.py` and include it in `AGENT_MODULES`
  (it is built from `CLAIMS_MODULES + HR_MODULES + COMPLAINT_MODULES`), then approve:
  `uv run python -m hall_monitor.approvals`. Review the `registry/approvals.yaml` diff as an
  approval. Any later prompt or manifest change bumps `PROMPT_VERSION` and the manifest version
  (no test checks the bump: review it).
- Add each agent to `WORKFLOWS` and `LIMITS` in `hall_monitor/governance/model_cards.py`, then
  regenerate `docs/model-cards.md` with `uv run python -m hall_monitor.governance.model_cards`
  (a test fails otherwise).
- Tests with scripted fake clients (`tests/fakes.py`); no network.

### 3. The pipeline

- `hall_monitor/<pkg>/pipeline.py`, modelled on `complaints/pipeline.py` or `hr/pipeline.py`:
  1. redact identifiers and detect text aimed at the AI before any model sees the case;
  2. `start_case("<workflow>", case_id, spend)` from `hall_monitor/spend.py` before the first
     model call (approved budget, and with a ledger the daily and monthly caps);
  3. a supervisor that picks steps within code's bounds (ADR 0028): closed action list, codes
     and amounts only in its view, refusals, step/cost/time budgets, re-check of every used
     agent's hall pass each turn, and code alone routing;
  4. every failure, refusal, stop and spend refusal routed to a person with a fixed reason;
  5. audit events written as they happen (IDs, keyed hashes, codes, costs), and `finish_case`
     settling the spend.
- **New workflow key:** add it to the `Workflow` Literal and the `WORKFLOWS` tuple in
  `hall_monitor/budgets.py`, to `registry/budgets.yaml` (an owner from `owners.yaml`, case
  budget, daily and monthly caps) and to `test_the_shipped_budgets_are_approved` in
  `tests/test_spend.py`, then re-approve. Caps are a demo setting; say so.
- Register every new audit event under `audit_events:` in `controls/controls.yaml`
  (`test_audit_events_are_all_mapped` checks both directions).
- In `hall_monitor/panel/views.py`, add the new stop and reason templates and each new event's
  fields to the `_SHOWN_FIELDS` allow-list, or the panel shows them as withheld.
- Live entry points (`run.py`, a form) pass `SpendLedger(DEFAULT_SPEND_DB)`; demo and scripted
  runs don't. Test that each live entry point passes it.
- A scripted stand-in client for demos, marked scripted in the log
  (`complaints/scripted.py`).
- Red-team tests: injection through every untrusted field, hostile and broken supervisors, every
  budget and stop path, duplicate concurrent runs (see `tests/test_desk_agentic.py`).

### 4. Evals and the gate

- `hall_monitor/<pkg>/evals.py`: score against the answer keys; publish
  `evals/results/<name>-scripted/` (results.json + report.md, labelled scripted and recording the
  commit and agent versions).
- A check in `evals/gate.yaml` for the things that must never happen (for example no case that
  needed a person went through automatically; zero agent failures unrouted).
- Add the workflow's policies to `hall_monitor/evals/agentic_compare.py` (agentic vs fixed under
  hostile scripted supervisors; gated).
- Fairness or counterfactual checks where people or groups are involved.

### 5. Panel and, if wanted, a form

- A read-only panel page (`hall_monitor/panel/desk.py` is the model), with a `NAV` entry in
  `hall_monitor/panel/pages.py`, a route in `hall_monitor/panel/app.py`, and demo data in
  `hall_monitor/panel/demo.py`. The panel stays read-only.
- A form that adds a case is a separate local app (`hr/submit.py`, `complaints/submit.py`):
  loopback only, per-run token, same-origin check, body limit, demo and live modes, live passing
  the spend ledger.

### 6. A live run

Only with the user's approval of the stated cost. Publish the result even if it is bad.

## Docs in the same pull request

- `controls/controls.yaml`: a new control (what, status, code, tests, evidence, nist, finos,
  gaps), IDs checked against `controls/frameworks.yaml`; then
  `uv run python -m hall_monitor.governance.controls` to regenerate `docs/controls.md`.
- `README.md`: the example's section, a controls table row, the framework-mapping count (a test
  checks the total; the "N have none yet" breakdown is kept by hand), the roadmap item.
- `docs/review/log.md`: the review and every finding's outcome.
- When they change: `docs/readiness-checklist.md`, `docs/threat-model-agentic.md`,
  `docs/runbook.md`, `docs/architecture.html`, the plain-English briefs, and the PDFs in
  `docs/pdf/` (`make_diagrams.py`, with its `AS_OF` date; `make_exec_docx.py`).

## Checks and review

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run python -m hall_monitor.evals.gate
uv run python -m hall_monitor.no_send check
uv run python -m hall_monitor.governance.controls
uv run python -m hall_monitor.governance.model_cards
```

Then run the `governance-reviewer` agent (`.claude/agents/governance-reviewer.md`) on the
branch's changes; fix every confirmed finding test-first and log it. Work in a git worktree on a
branch, open a PR, merge with `gh pr merge --merge` only when the user says so.

## Done means

- [ ] Design doc and ADR, assumptions labelled
- [ ] Synthetic data with hand-written answer keys and boundary cases
- [ ] Rules in code with fixed reason templates, tested both sides of each boundary
- [ ] Agents through `run_agent()`, manifests approved, `EXPECTED_AGENTS` and the untrusted-notice
      sets updated, model cards current
- [ ] Pipeline: redaction and injection first, spend check, supervisor within bounds, every
      failure to a person, audit events registered, panel templates added
- [ ] Workflow budget in `budgets.yaml`, approved
- [ ] Evals published (scripted, labelled), gate check, agentic comparison
- [ ] Panel page with its nav entry and route; form only if wanted
- [ ] Register, README, review log and affected docs current
- [ ] All checks green; governance review logged; PR open
