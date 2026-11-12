# Complaint desk eval (scripted)

Snapshot `2026-10-01` (500 public CFPB complaints, manifest `be882cd30552`), run 2026-11-12 02:57 UTC.

**The agent here is a scripted stand-in with fixed rules, not the model.** The routing metrics measure the desk's code (must_route_floor_below_high is the code's own floor, before any agent raised it); the final urgency, the specialist share and the flagged rates below also depend on the stand-in's rules. It says nothing about the model's summaries, categories or urgency.

| Metric | Value |
|---|---|
| complaints | 500 |
| errors | 0 |
| agent_failed | 0 |
| supervisor_failed | 0 |
| audit_chain_verified | True |
| must_route_total | 197 |
| must_route_missed | 0 |
| must_route_below_high | 0 |
| must_route_floor_below_high | 0 |
| must_route_reason_missing | 0 |
| untimely_total | 20 |
| untimely_to_specialist_rate | 1.0 |
| specialist_share | 0.424 |
| drafts_kept | 500 |
| drafts_dropped | 0 |
| drafts_with_delivery_path | 0 |
| counterfactual_flip_rate | 0.0 |
| region_specialist_gap | 0.3333 |
| tag_specialist_rate_older_american | 1.0 |
| tag_specialist_rate_servicemember | 1.0 |
| monetary_relief_total | 31 |
| monetary_relief_flagged_rate | 0.5161 |
| explanation_total | 289 |
| explanation_flagged_rate | 0.3841 |
| cost_usd | 0.0 |

Group rates by ZIP region (first digit of the prefix) and by tag are in results.json. They are reported, not gated: a region's specialist rate reflects what is complained about there (more fraud, more mortgages), not only how the desk treats it. The counterfactual flip rate is gated, but it can't flip by construction: routing doesn't read the state or ZIP prefix and the agent is never sent them, and the check calls no model. It shows location doesn't reach the decision, not that the model is unbiased.

Partial ground truth: CFPB's published company response. 0.5161 of the 31 complaints closed with monetary relief were flagged (specialist review or high urgency), against 0.3841 of the 289 closed with an explanation. A closed complaint can still have been urgent; this is a signal, not an answer key.
