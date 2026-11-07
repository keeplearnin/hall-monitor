# ADR 0019: A person signs each leave decision, within the role the rules name

- **Status:** Accepted (HR example, third slice)
- **Control:** [HM-23](../controls.md#hm-23)

## Context

The leave rules name the least senior role that may decide each request
([ADR 0017](0017-leave-rules-in-code.md)), and the agents prepare it
([ADR 0018](0018-leave-agents-explain-code-decides.md)), but until a decision step checks that
role, the role is a label. The claims example already has a signed approval gate
([ADR 0014](0014-signed-decisions-and-stored-evidence.md)); the HR sign-off follows it, with
two differences the claims gate taught.

## Decision

- **The audit log is the state.** Each prepared request stores its evidence pack once, under its
  hash, and logs the hash; preparing it again is refused. A decision reads the pack the log
  recorded, and the request's one final decision is written with a once-only append, so two
  people deciding at once record one decision. No checkpoint file is involved: the claims gate
  learned that a restored checkpoint must never decide anything.
- **Signed over the evidence.** A decision counts only if the staff member is registered, it is
  signed with their Ed25519 key over the request, every decision field and the evidence hash,
  and the hash is the one the log recorded.
- **Role and outcome rules in code.** The staff member's role, read from
  `registry/hr_staff.yaml`, must be at least the one the routing rule named. Approving FMLA leave
  needs an eligible employee, a qualifying reason, and weeks within what is left and was asked;
  denying needs an HR manager or above; extending past the entitlement needs a director; denying,
  extending, or approving a request the rule sent to HR review needs a written reason. Asking for
  more information is open to anyone registered.
- **No signature in the chain.** The claims log keeps each decision's signature, and with the
  public key a signature can confirm a guessed reason (HM-06 gap). The HR log keeps the staff
  ID, role, outcome, weeks, the code's route and keyed hashes of the reason and of the signed
  decision; the signed decision, with its reason and signature, is stored beside the evidence
  pack, which can be erased.
- **A nonce in the pack.** The pack's hash is logged plainly, because the signer quotes it; a
  random nonce in the pack means a guess at its few structured values can't confirm it.

## Alternatives considered

- **A LangGraph interrupt, as claims use.** Rejected here: the log already decides claim state,
  and a second store for HR would be a second thing to keep honest.
- **Let the panel sign.** Rejected for now: the panel has no sign-in, so signing there would let
  anyone at the machine act as staff ([ADR 0016](0016-read-only-control-panel.md)).

## Consequences

- The three fictional staff sign with public demo keys, so a demo decision proves nothing about
  who made it, and each logged decision says so.
- "Approve" covers FMLA leave only; anything outside it is decided outside this step.
- Asking for more information ends the request; there is no follow-up path yet.

## Evidence

`tests/test_hr_signoff.py`: each role and outcome rule refuses what it should, each was removed
in turn to check a test fails, a wrong key, changed decision, other evidence or changed pack is
refused, two decisions at once record one, and the log keeps no reason or signature while the
store can still prove them.
