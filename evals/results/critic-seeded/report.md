# Critic eval: seeded errors

Run 2026-11-03 22:27 UTC on commit `ecd20c3`. 118 cases, 0 errored. One critic call per case.

| Case | Cases | Result |
|---|---|---|
| Control (nothing wrong): false alarms | 34 | 2.9% |
| Amount ignores cap: caught | 16 | 100.0% |
| Unsupported flag: caught | 34 | 100.0% |
| Missing incident date: caught | 34 | 100.0% |
| Cost per review | | $0.0153 |

## Misses and false alarms

- claim_008 control (false alarm). Issues raised: [coverage_check] The reasoning relies on clause 2.3 (prompt cancellation within 72 hours) when it says the trip was cancelled the next day. But applicable_clauses lists only 2.1

## Reproduce

```bash
uv run python -m hall_monitor.evals.critic_seeded --out evals/results/critic-seeded
```
