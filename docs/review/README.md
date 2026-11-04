# External review package

hall-monitor asks to be judged on whether its governance claims are true and whether they would
stand up in front of an auditor or regulator. This page tells a reviewer what to look at and how
to report. Reviews so far, and what each one changed, are in the [review log](log.md).

**Status:** no review by anyone outside the project has happened yet. The reviews in the log
were done by independent AI agents with no access to the author's reasoning; they are useful,
but they are not an external review.

## Who we'd like to hear from

- Model risk, compliance or internal audit in insurance or banking
- Security engineers who test LLM applications
- Engineers who run agent systems in production

## What to review (about two hours)

| Read | To judge |
|---|---|
| [Board brief](../board-brief.md) | Is the risk framed the way a board would need it? |
| [Controls register](../controls.md) | Do the controls fit the NIST AI RMF and FINOS AIGF entries they're mapped to? Is anything important missing from "not covered"? |
| [Decision records](../adr/README.md) | Are the trade-offs right for a regulated firm? |
| [`governance/routing.py`](../../hall_monitor/governance/routing.py), [`governance/approval.py`](../../hall_monitor/governance/approval.py) | Can a claim reach automatic payment when it shouldn't, or be decided by someone without authority? |
| [`governance/audit.py`](../../hall_monitor/governance/audit.py) | Is the tamper-evidence claim stated honestly? |
| [`gateway/`](../../hall_monitor/gateway/) and [`tools/gateway.py`](../../hall_monitor/tools/gateway.py) | Can claim text steer an agent, or reach another customer's records? |
| [Eval results](../../evals/results/) and [`evals/scoring.py`](../../hall_monitor/evals/scoring.py) | Do the published numbers support what the README says? |

## Questions we most want answered

1. Would you accept this evidence for the controls it claims, and if not, what would you need?
2. What is the most serious risk the design does not address?
3. Which claim in the docs goes further than the code?
4. Is the routing rule the right shape, or does a fixed threshold create its own risk?

## Running it

Everything except the live evals runs offline, with no API key:

```bash
uv sync
```

```bash
uv run pytest -q
```

```bash
uv run python -m hall_monitor.evals.gate
```

## How to report

Open a GitHub issue with a title starting "Review:", one finding per issue: what you checked, what you
found, and how serious you think it is. Findings and what we did about them are added to the
[review log](log.md), with credit if you want it.
