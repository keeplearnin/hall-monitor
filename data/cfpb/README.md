# CFPB complaint snapshots

Public complaints from the [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/),
published by the Consumer Financial Protection Bureau under CC0. Each complaint is one
consumer's account, which CFPB publishes without verifying; nothing here is a finding about any
company, and the company name is not kept.

These are **not synthetic**, unlike everything under `data/synthetic/`. They are kept for the
simulated complaint desk ([design](../../docs/complaint-desk-design.md)), so its evals and demos
are reproducible without the network.

## What is kept

Each snapshot is a pull exactly as the desk's CFPB tool returned it
(`hall_monitor/complaints/cfpb.py`), after minimisation at the door:

- only an allow-list of fields (complaint ID, dates, product, sub-product, issue, sub-issue,
  state, ZIP, tags, how it was submitted, the company's response, its public response,
  timeliness, and the narrative, which CFPB no longer publishes);
- the company name and any other field dropped, listed by name only in `dropped_fields`;
- the ZIP code cut to its first three digits (`752XX`).

`manifest.json` records the query, the counts and each page's SHA-256; loading a snapshot
(`ingest.load_snapshot`) refuses one whose pages don't match.

## Snapshots

| Name | Pulled | Query | Records |
|---|---|---|---|
| `2026-10-01` | 2026-11-11 | the desk's ten products, received from 2026-10-01, oldest first, first 500 (00:00 to 07:46 UTC on 1 October) | 500, none quarantined |

A pinned snapshot is never overwritten (`ingest pin` refuses); a new one gets a new name, and an
eval that moves to it says so.
