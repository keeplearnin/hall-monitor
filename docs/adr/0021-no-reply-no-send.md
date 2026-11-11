# ADR 0021: The complaint desk never sends anything, enforced in code

- **Status:** Accepted (complaint desk, first slice)
- **Control:** [HM-25](../controls.md#hm-25)

## Context

The complaint desk ([design](../complaint-desk-design.md)) reads live complaints from the public
CFPB database and prepares each one for a person, including a draft acknowledgement. A complaint
desk is exactly the kind of system where an agent "helpfully" replying to a consumer, emailing a
company or filing something with a regulator would do real harm: a reply to a real person about
a real complaint, from a system that isn't the company and has no standing to speak for it.

Hari asked for the rule to be a hard check in code, not a policy line: **the system never
replies to, contacts or sends anything to anyone.** A sentence in a prompt or a README is not a
control; an agent given a send tool will eventually use it, and a developer adding one won't
read the README first.

## Decision

The rule is enforced in four places, each with tests, built before any other part of the desk:

- **One outbound HTTP path, guarded.** `hall_monitor/egress.py` is the only module allowed an
  HTTP client, and a client can be made only with one of its two shipped policies (an equal
  copy built elsewhere, or a policy swapped onto a client after it was made, is refused at run
  time, and using its httpx or policy types elsewhere, by any import spelling, fails the
  static check). Its transport checks each request before anything is sent: https only, the exact
  host, the default port, no credentials in the URL, a path that doesn't normalise elsewhere, and
  a method and path prefix the policy names. The data policy allows only `GET` on the CFPB
  complaint API. Redirects are not followed, and a redirect a caller asks to follow is checked
  again. Each refusal is logged to the audit log: policy, method, host and a hash of the URL,
  never a body. The desk's model client (the Anthropic SDK, which uses its own `httpx2`) gets
  the same guard, allowing only `POST` to the messages endpoint itself (not batches or token counting), with
  retries off.
- **A read-only tool catalogue.** `hall_monitor/no_send.py` lists every tool the system may
  expose or grant, with what it reads. The registry refuses at run time an agent whose manifest
  lists any tool outside it, or one whose name reads as sending; approvals refuse to approve
  one; the tool gateway takes only a plain in-process MCP server (fastmcp's client would post
  tool calls to a URL, and a proxy forwards them), refuses to start on a server that exposes such a tool, and refuses to call
  one. Send words are matched in any naming style (`sendEmail`, `send-email`, `replyto`).
- **Drafts with no delivery path.** `DraftResponse` has no field that could hold an address, a
  recipient, a channel or a send time, and `never_sent` can only be true. A stored draft goes in
  the evidence store by its hash; the log entry records the hash, `never_sent: true` and
  `delivery: "none"`, never the text, and it is an evidence entry, so verifying the log
  reports a stored draft that was changed (or erased).
- **A static check in CI.** `python -m hall_monitor.no_send check` reads every module's syntax
  tree and `pyproject.toml`, and fails on mail, messaging and raw network libraries
  (`smtplib`, `socket`, `urllib.request`, `http.client`, `requests`, `aiohttp`, `twilio`,
  `sendgrid`, `slack_sdk`, `boto3` and more) and raw connection calls (`open_connection`,
  `create_connection`), any HTTP client outside `egress.py` or its internals imported
  elsewhere, HTTP write calls (`.post`, `.put`, `.patch`, `.delete`, a constant `getattr` of
  one, and `request`, `stream` or `build_request` with any method but GET), mail calls,
  fastmcp's client and proxies anywhere but the gateway, dynamic imports (including an imported `import_module`), `os`
  functions that run another program (however `os` or the function is imported), and sending
  packages in the dependencies. The one exception,
  `subprocess` in `evals/meta.py`, runs fixed local `git` commands and is named in the check.

## Alternatives considered

- **A rule in the prompt.** Rejected: a prompt is not a control, and the model is the component
  the rule most needs to hold against.
- **A network firewall or sandbox.** Better isolation, and what production should add, but it
  lives outside the repo and can't be proven by its tests. The checks here are the part the code
  can own; the register lists process isolation as not covered.
- **Allow-listing by DNS or IP at the socket.** Rejected for now: brittle with CDNs, and it
  would also have to cover the libraries' own sockets. The transport guard sits at the one place
  this code makes requests.

## Consequences

The rule binds this repository's code. Third-party libraries are not scanned: the Anthropic SDK
posts model requests (guarded for the desk, unguarded where the claims and HR examples build
their own client), and Langfuse posts metadata-only traces when its keys are set, without the
egress guard. Neither reaches a consumer, a company or CFPB. A determined developer could still
defeat a static check (a method name held in a variable, code built from strings, a C
extension); the check is a tripwire for the honest mistake, and review is still the control for
the rest. Review 14 found five plain ways past the first version of the check; each is now a
test case. The send-word list can refuse a
harmless tool name; that is the intended direction of error.
