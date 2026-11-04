# hall-monitor: working notes for Claude

Governance controls for AI agents in regulated financial services, with travel-insurance claims
triage as the worked example. Public repo: every claim in the docs must be true of the code.

## Commands

```bash
uv run pytest -q                                   # offline; the Claude client is faked
uv run ruff check . && uv run ruff format --check .
uv run python -m hall_monitor.evals.gate           # published results vs evals/gate.yaml
uv run python -m hall_monitor.governance.controls  # regenerate docs/controls.md
uv run python -m hall_monitor.approvals            # re-approve manifests/prompts (a reviewed change)
```

## Rules

- Never call the Anthropic API from tests or CI. Live evals cost money: run them only when the
  user asks, and say the cost first.
- Work on a branch and open a PR. Merge with `gh pr merge --merge` (merge commits keep cited
  commits on main). End commits and PR bodies with the attribution lines the session gives.
- A change to a prompt or manifest needs a version bump (`PROMPT_VERSION` and the manifest
  version) and a new approval; the approvals test fails otherwise.
- Keep docs current in the same PR: README (controls table, roadmap), `controls/controls.yaml`
  (then regenerate `docs/controls.md`), the ADRs in `docs/adr/`, `docs/review/log.md` for review
  findings, and the one-pagers in `docs/` when what they say changes. Tests check register and
  doc references.
- Never overstate: if a control is declared but not enforced, or results are from older agent
  versions, say so. Gaps go in the register's `gaps` and `not_covered`.
- Governance decisions live in code, not in a model: routing, approval rules, data-class and
  tool checks. Failures stop the claim and send it to a person; never invent a value to keep
  going.
- Nothing personal in the audit log or traces: IDs, hashes, decision fields and costs only.
- Synthetic data only. Answer-key changes are logged with the reason in
  `data/synthetic/README.md`.
