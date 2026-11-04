# ADR 0008: A hash-chained SQLite audit log, anchored outside the database

- **Status:** Accepted (milestone 6)
- **Control:** [HM-06](../controls.md#hm-06)

## Context

An auditor needs to know that the record of what happened hasn't been changed since. Ordinary
application logs can be edited by anyone with access.

## Decision

[`governance/audit.py`](../../hall_monitor/governance/audit.py) keeps an append-only log in
SQLite. Each entry's hash covers its own fields and the previous entry's hash; triggers refuse
updates and deletes; `verify` reports any changed, missing or reordered entry. Entries hold IDs,
hashes, decisions and costs, and no claim narrative or documents, to keep personal data out of a
log that can't be edited. They do hold policy numbers, decision fields, tool-call arguments and
the adjuster's name and written reason. Someone with write access to the file could rebuild the whole chain, so the
chain head (`audit head`) is meant to be copied somewhere they can't change, and `verify` checks
against that copy.

## Alternatives considered

- **A ledger database or blockchain.** Rejected: more infrastructure for the same property,
  given an external anchor.
- **Write-once cloud storage (for example S3 Object Lock).** The right production anchor; the
  chain head is small enough to store there.

## Consequences

- Tampering is detectable, not prevented. That is stated plainly in the code and the docs.
- The anchor is only as good as where it's kept, which is a deployment decision.
- Triage events are written together when triage finishes, not as each step happens, so a crash
  mid-triage leaves no partial trail (the stopped claim is still logged).
- An adjuster's written reason can contain personal or medical detail and can't be erased. A
  production version would store the reason elsewhere and log its hash.
- SQLite suits one writer; production would move to Postgres with the same hashing.
