# ADR 0014: Adjuster decisions are signed, and evidence is stored by its hash

- **Status:** Accepted (milestone 11)
- **Controls:** [HM-05](../controls.md#hm-05), [HM-06](../controls.md#hm-06)

## Context

The claims audit found two weak points in the approval gate. Any string that matched a
registered adjuster ID was accepted as that adjuster. And the evidence pack the decision rested
on lived only in the workflow checkpoint, which isn't tamper-evident: someone who could edit it
could show the adjuster different evidence, or change it afterwards.

## Decision

- **Signed decisions.** Each adjuster in [`registry/adjusters.yaml`](../../registry/adjusters.yaml)
  has an Ed25519 public key. A decision counts only if it carries a valid signature over the
  claim ID and every decision field, evidence hash included
  ([`governance/signing.py`](../../hall_monitor/governance/signing.py)). The system holds public
  keys only, so it can check a decision but never make one. The signature is kept in the audit
  log.
- **Evidence stored by hash.** At triage the evidence pack is stored in the audit database under
  its SHA-256, and the log records that hash. Before any decision, the review step checks that
  the checkpoint's pack is the one recorded and still matches the stored copy. `verify` reports a
  stored pack that no longer matches its hash.
- **Erasure without breaking the chain.** A stored pack may be deleted, for example under a
  data-protection request. `verify` reports it as erased, not as tampering, because its hash
  stays in the chain.

## Alternatives considered

- **Shared-secret (HMAC) signatures.** Rejected: whoever verifies could also forge.
- **Trust the identity of whoever runs the review command.** Fine behind single sign-on in
  production, but it proves nothing in a reference implementation. Signatures work either way.
- **Put the whole pack in the log.** Rejected: the pack holds AI findings that can include
  medical detail, and the log can't be erased.

## Consequences

- The two fictional adjusters use demo keys derived from their IDs, public by design and marked
  `demo_key: true`. A real deployment issues keys from the firm's identity system (smart cards, an
  HSM, or signing behind sign-on).
- Losing an adjuster's key means re-issuing it and updating the register, which is a reviewed
  change.
- The decision's inputs are integrity-checked: the evidence pack and, since the Phase B
  review, the triage result the approval rules read, both against the hashes logged at
  triage. Other checkpoint state is not.
