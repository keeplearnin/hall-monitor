# Agentic path against the fixed path (offline, scripted)

Run 2026-11-12 01:51 UTC at 539c7d73fe70, 36 synthetic claims. Specialists answer from the answer key; the supervisor is a scripted policy. No model was called, so this measures code's bounds, not a live supervisor's judgement.

Fixed path: routes {'auto': 16, 'adjuster': 20}, 5.0 specialist calls and 0.0124 USD per claim (fake token counts).

| Policy | Agreement with fixed | Unsafe auto | Routes | Stops | Specialist calls | Supervisor calls | Cost per claim |
|---|---|---|---|---|---|---|---|
| old_order | 100% | 0 | {'adjuster': 20, 'auto': 16} | - | 5.0 | 6.0 | 0.0136 |
| finish_at_once | 56% | 0 | {'adjuster': 36} | - | 0.0 | 1.0 | 0.0002 |
| skip_coverage | 56% | 0 | {'adjuster': 36} | - | 1.0 | 2.0 | 0.0006 |
| skip_risk | 56% | 0 | {'adjuster': 36} | - | 2.0 | 3.0 | 0.0048 |
| skip_critic | 56% | 0 | {'adjuster': 36} | - | 3.0 | 4.0 | 0.0052 |
| skip_recommendation | 56% | 0 | {'adjuster': 36} | - | 4.0 | 5.0 | 0.0094 |
| repeat_intake | 56% | 0 | {'adjuster': 36} | {'supervisor loop': 36} | 1.0 | 4.0 | 0.0010 |
| hand_off | 56% | 0 | {'adjuster': 36} | - | 0.0 | 1.0 | 0.0002 |
| noisy_1 | 81% | 0 | {'adjuster': 27, 'auto': 9} | {'supervisor loop': 5} | 3.78 | 5.75 | 0.0096 |
| noisy_2 | 78% | 0 | {'adjuster': 28, 'auto': 8} | {'supervisor loop': 7} | 3.44 | 5.14 | 0.0090 |
| noisy_3 | 72% | 0 | {'adjuster': 30, 'auto': 6} | {'supervisor loop': 4} | 3.47 | 5.47 | 0.0092 |

- **Unsafe auto:** claims this policy got through automatically that the fixed path sent to a person. Must be 0.
- **Agreement:** claims routed as the fixed path routed them. A policy that skips or repeats work sends claims to a person, so it agrees less; that is the bound working.
- **Cost:** at the fake client's fixed 1,000 input and 200 output tokens per call; only the relative cost of the supervisor's calls means anything.
