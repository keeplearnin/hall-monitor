# ADR 0022: Complaints come in through the gateway, minimised, with a snapshot of every pull

- **Status:** Accepted (complaint desk, second slice)
- **Control:** [HM-26](../controls.md#hm-26)

## Context

The complaint desk ([design](../complaint-desk-design.md)) reads live data from the CFPB
complaint API. Live data brings the problems synthetic data never had: the source can be slow,
down or rate-limited; records can change after they are first seen (a company response
arrives); a record can be malformed; and an eval run against live data can't be repeated
tomorrow. The no-send rule ([ADR 0021](0021-no-reply-no-send.md)) also has to hold for the one
path that does reach out.

Two facts about the API, found while building: it ignores `frm` and pages with `search_after`
(a cursor from each hit's sort value), and it no longer publishes consumer narratives.

## Decision

- **Through the governed tool gateway.** The fetch is an MCP tool, `cfpb_complaint_search`,
  behind `ToolGateway`, callable by the pipeline only; every call, allowed or refused, is in the
  audit log. Its arguments are checked (a date, a cursor of digits, a page size up to 100), so
  nothing else can be put in the query. Under it, `CfpbClient` (in `egress.py`, the one module
  allowed an HTTP client) makes GET requests through the egress guard.
- **Minimised at the door.** The tool keeps an allow-list of fields, so the company name never
  enters the system and a field CFPB adds later is dropped and reported by name. ZIP codes are
  cut to three digits. The desk is one simulated company; real company names beside triage
  results would read as findings.
- **Incremental by cursor.** Pulls sort oldest first and resume after the stored cursor, which
  moves only after a page is processed. Each pull takes up to the desk's capacity and says
  whether it caught up, so a backlog is visible rather than silently skipped.
- **Reliable.** Requests are spaced (one a second by default); timeouts, network errors, 429 and
  5xx are retried with exponential backoff and jitter, honouring `Retry-After` up to a cap;
  another 4xx, a body that isn't JSON or one of the wrong shape stops the pull, which records
  `failed` with the error's type and keeps what it already stored.
- **Idempotent and validated.** Records are keyed by complaint ID in SQLite: a repeat is
  skipped, a changed record becomes a new version. A record that fails strict validation is
  quarantined with field names and error types (never values) and a hash, once.
- **Snapshots.** Each page is written to disk as the tool returned it before it is processed,
  with a manifest of hashes. `replay` rebuilds a store from a snapshot offline, and a pinned
  snapshot committed under `data/cfpb/snapshots/` is what every eval and demo runs on.
- **Desk products only.** The simulated company is a consumer lender and bank; credit
  reporting complaints go to consumer reporting agencies and aren't pulled.

## Alternatives considered

- **The bulk CSV download.** Complete and simple, but 360 MB a day for a desk that takes a few
  hundred complaints, on a second host the egress policy would have to allow.
- **A date watermark** (the design's first plan). Rejected once paging turned out to be by
  cursor: a date watermark re-reads or skips within a day, and the API won't page past 10,000.
- **Keeping company names.** Rejected: nothing the desk does needs them, and keeping them invites
  reading triage results as findings about a company.

## Consequences

A complaint CFPB indexes late, with a receipt time before the cursor, is missed; a periodic
re-pull from a date (`--since`) catches it, and dedupe keeps that cheap. The schedule is a
foreground loop (`--every`), not a service: production would run it under cron or a scheduler.
The store and snapshots under `var/` are local, unencrypted SQLite and JSON. The pinned snapshot
is real, published complaint data, not synthetic; it is minimised as above.
