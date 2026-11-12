# ADR 0029: Typed-in complaints reach the desk through a separate form, not the panel

- **Status:** Proposed (complaint desk; new functionality, scope to be confirmed by the owner)
- **Control:** [HM-33](../controls.md#hm-33)

## Context

Complaints reach the desk only from CFPB, through ingestion (HM-26) and `complaints.run`. To show
the desk working on a complaint someone chooses, end to end, there was no way in short of
editing a snapshot. The leave example solved the same problem with a separate form
([ADR 0020](0020-leave-submission-form.md)); the panel stays read-only
([ADR 0016](0016-read-only-control-panel.md)).

## Decision

- **A separate app, as the leave form.** `python -m hall_monitor.complaints.submit` serves its
  own small form on port 8767. The panel stays GET-only. The protections are the leave form's,
  reused rather than reinvented: loopback and localhost only, a per-run form token another site
  can't read, the form's own origin (and Sec-Fetch-Site when sent), only a form post with each
  field once, a body limit enforced while it is read, and the panel's page policy with a
  same-origin referrer policy.
- **Structured fields, and a synthetic ID.** The form takes what the desk reads: one of CFPB's
  products (the list in the pinned snapshot, nothing else), an issue and optional sub-issue (up
  to 300 characters each, CFPB's issues offered as suggestions), the two CFPB tags, and an
  optional narrative (up to 10,000 characters). CFPB's labels are a fixed list and the pipeline
  redacts only the free text, so a typed issue or sub-issue holding anything redaction would
  remove is refused, as are control and invisible format characters in any field. It builds a `Complaint` with a synthetic ID,
  `syn-` and 12 hex digits; CFPB's IDs are digits only, and the desk's CFPB source model refuses
  anything else, so the two can't collide. State, ZIP prefix, company response and timeliness
  are left empty. It is synthetic data: every page says so.
- **The real pipeline, agentic.** A submission runs through `complaints/pipeline.py
  triage_complaint` as `complaints.run` runs it: redaction, the check for text aimed at the AI,
  the desk supervisor and triage agent within code's bounds (ADR 0028), routing in code, the
  draft kept and never sent, and the audit log (IDs, keyed hashes, codes, costs; never the text).
  The result shows on the panel's Complaint desk page as `cfpb-syn-...`.
- **The submitter sees the ID and state only.** "Routed to a person at the complaint desk", or,
  if triage was cut off, that it was received and not routed. Never the queue, reasons, urgency,
  summary or draft: those are for the desk's people, on the panel.
- **The mode is explicit, and demo is free.** `--demo` writes to the demo log (`var/demo/`) with
  the scripted stand-in (`complaints/scripted.py`), which the log and the panel label as
  scripted. Unlike the leave form's demo, it takes free text: the stand-in answers from the
  labels by fixed rules, so what it answers is about the complaint typed, not another one.
  `--live` calls the Claude API through the desk's egress-guarded client (`egress.model_client`,
  HM-25), which reaches only the messages endpoint and logs any refusal, and stops at a per-run
  cap (`--max-complaints`, 5 by default, at most 50). The start-up line and every live page say
  what it costs: typically well under a cent a complaint at Haiku's price; the desk's own
  budget makes no further model call once a complaint reaches 0.02 USD, though a call already
  started can take it a little past that.

## Alternatives considered

- **A form inside the panel.** Rejected for the reason ADR 0020 gives.
- **Adding the complaint to the desk's store, so `complaints.run --from-store` picks it up.**
  Rejected: the store holds CFPB's records, minimised; mixing typed-in synthetic ones into it
  would put them in live runs and blur what the store's counts mean.
- **Running the fixed order.** Rejected: the desk runs agentic by default, and the form should
  show what the desk does.

## Consequences

No reply and no send (HM-25) hold: the form serves a page and writes to the audit log; the
static check passes on it, and live mode's only outbound path is the guarded model client. The
typed-in text is not kept as such: the log has its redaction counts and the hash of the record,
so a person working the complaint on the panel sees its codes, not what it says; but a kept
draft is stored in full in the evidence store and may quote it, a name included. Routing reads the
labels and tags, not the narrative (as the red-team arena found, HM-30): a narrative about fraud
under a plain label reaches specialist review only if the agent raises it, and in demo mode the
stand-in reads the labels only, so there the narrative affects only redaction and the injection
check. A form complaint whose process is killed outright mid-triage stays "claimed, not routed":
`complaints.run` routes unfinished complaints only from its own input, and the form keeps no copy
to route; the panel's desk page says so for `cfpb-syn-` complaints rather than promising a
re-run. The token and origin check stop another site, not a user or process on this machine.
The spend cap is per run, in memory, and counts submissions, not dollars. Live mode is tested
only with a fake client, and its first live run would be the agents' first on typed-in text.
