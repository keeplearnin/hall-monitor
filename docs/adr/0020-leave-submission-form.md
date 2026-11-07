# ADR 0020: Leave requests are added through a separate form, not the panel

- **Status:** Accepted (HR example, fifth slice)
- **Control:** [HM-24](../controls.md#hm-24)

## Context

Leave requests reached HR's queue only from the command line (`python -m hall_monitor.hr.run`),
which prepares the synthetic messages. A form makes the workflow easier to show and to try. The
obvious place for one is the control panel, but the panel's main property is that it can't
change anything: every route is GET and it opens the audit log read-only
([ADR 0016](0016-read-only-control-panel.md)). A form there would end that.

## Decision

- **A separate app.** `python -m hall_monitor.hr.submit` serves its own small form on another
  port. The panel stays GET-only, and its test that every route only reads still holds.
- **It only feeds the pipeline.** A submission goes through `hr/pipeline.py prepare`, as
  `hr.run` does: redaction, the check for text aimed at the AI, the leave agents, the rules in
  code and the audit log. The form decides nothing; HR signs every decision
  ([ADR 0019](0019-signed-hr-sign-off.md)). The submitter sees the request's ID and state only,
  never its route, reasons or role, which could reveal what it is about.
- **The mode is explicit, and demo is free.** `--demo` writes to the demo log with the demo
  builder's scripted model, which answers from the answer key, so it offers only the synthetic
  messages. `--live` takes free text and calls the Claude API for each one, up to a per-run cap
  (`--max-requests`, 20).
- **This machine only, and only from its own page.** With no sign-in, the app answers only
  requests addressed to localhost and listens only on loopback. A page elsewhere could still make
  a browser on this machine post the form, so a submission needs the form's token (another site
  can't read the page to get it) and the form's own origin. The pages send a same-origin
  referrer policy instead of the panel's no-referrer, because under no-referrer a browser posts
  the form with `Origin: null`, which the check rightly refuses; this showed up in a browser, not
  in the tests, and a test now checks the header.

## Alternatives considered

- **A form inside the panel.** Rejected: it would end the panel's read-only property, which is
  the claim the panel is built around.
- **Free text in demo mode.** Rejected: the scripted model can only answer for messages it
  knows, so it would show the answer for a different message than the one typed.
- **A per-session or per-user token, and sign-in.** Deferred to the panel's access phase; a
  per-run token is enough to stop another site posting through the browser.

## Consequences

Anyone who can use a browser on this machine can submit as any synthetic employee, and in live
mode free text goes to the Claude API, so the "synthetic data only" notice matters: redaction
removes labelled identifiers, not names or conditions. The spend cap is per run, in memory. Live
mode is tested only with a fake client.
