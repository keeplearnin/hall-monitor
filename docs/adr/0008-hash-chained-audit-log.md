# ADR 0008: A hash-chained SQLite audit log, anchored outside the database

- **Status:** Accepted (milestone 6; events written as they happen since milestone 11)
- **Control:** [HM-06](../controls.md#hm-06)

## Context

An auditor needs to know that the record of what happened hasn't been changed since. Ordinary
application logs can be edited by anyone with access.

## Decision

[`governance/audit.py`](../../hall_monitor/governance/audit.py) keeps an append-only log in
SQLite. Each entry's hash covers its own fields and the previous entry's hash; triggers refuse
updates and deletes; `verify` reports any changed, missing or reordered entry. Events are written
as they happen: the pipeline reports each agent call, tool call, review, revision and
correction to a recorder that appends it at once, so a run that dies part-way still leaves its
trail, and a claim whose events can't be recorded doesn't proceed.

Entries hold IDs, hashes, decisions and costs, and no claim narrative or documents, to keep
personal data out of a log that can't be edited. The adjuster's written reason, critic issues,
each agent call's hash of what the agent was sent and each tool call's hash of its result are kept
as keyed hashes (HMAC-SHA256, key from `HALL_MONITOR_AUDIT_KEY` or a file beside the
log), so the hash can be matched but not confirmed by guessing; the adjuster's ID is kept rather
than their name; the full decision stays in the workflow's record. The decision's signature, also
logged, covers the reason, so with the public key it can still confirm a guess (HM-06 gap).
The input and tool-result hashes were plain until Review 12: the recommendation agent is sent a
structured summary with few possible values, and a policy with no earlier claims returns only the
insured's name, so a guess could confirm either. Traces leave the input hash out.
Entries do hold policy numbers, decision fields and tool-call arguments.

Someone with write access to the file could rebuild the whole chain, so the chain head
(`audit head`) is meant to be copied somewhere they can't change, and `verify` checks against
that copy.

## Alternatives considered

- **A ledger database or blockchain.** Rejected: more infrastructure for the same property,
  given an external anchor.
- **Write-once cloud storage (for example S3 Object Lock).** The right production anchor; the
  chain head is small enough to store there.

## Consequences

- Tampering is detectable, not prevented. That is stated plainly in the code and the docs.
- The anchor is only as good as where it's kept, which is a deployment decision.
- A log outage stops claims rather than letting them through unrecorded.
- Evidence packs are stored beside the log, by hash, where they can be erased without breaking
  the chain ([ADR 0014](0014-signed-decisions-and-stored-evidence.md)).
- Copying the chain head out is still manual.
- SQLite suits one writer; production would move to Postgres with the same hashing.
