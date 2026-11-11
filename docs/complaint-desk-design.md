# Design: a simulated complaint desk on live CFPB data

- **Status:** Accepted for build (Hari, 2026-11-11: "go build it"). Written before the code, on
  the branch, so the reasoning is recorded. Where the build differs, the ADRs and the controls
  register win and this page is updated in the same pull request.
- **Controls:** HM-25 (no reply, no send), HM-26 (ingestion), HM-27 (complaint triage),
  HM-28 (complaint evals and fairness). Numbers are assigned as each slice lands.

## Why this example

The claims and HR examples run on synthetic data. A second worked example on public, real-time
data shows the same controls holding up against input nobody wrote for them. The Consumer
Financial Protection Bureau (CFPB) publishes its Consumer Complaint Database through a free,
keyless, read-only API, updated daily, under CC0. It is financial services, the records are
already published, and every existing control has something to do.

**The framing is a simulated company complaint desk.** The desk pretends complaints have arrived
at a company's own complaint team and prepares each one for a person: what it is about, how
urgent it is, who must look at it and a draft acknowledgement. Nothing it does reaches CFPB, any
company or any consumer. It is not a CFPB system, it does not represent any company named in the
data, and nothing in it is a finding about a company: a complaint is one consumer's account,
which CFPB publishes without verifying.

## What the API gives us (checked 2026-11-11)

`GET https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/` with
`size`, `frm`, `sort`, `no_aggs`, `date_received_min` and `date_received_max`; one record by ID
at `.../api/v1/<complaint_id>`. Each hit's `_source` has `complaint_id`, `date_received`,
`date_sent_to_company`, `product`, `sub_product`, `issue`, `sub_issue`, `company`, `state`,
`zip_code`, `tags` (`Older American`, `Servicemember`, both, or null), `submitted_via`,
`company_response`, `company_public_response` and `timely`.

**The live API no longer returns consumer narratives.** The brief assumed the narratives were
public. As checked on 2026-11-11, neither the search API nor the bulk download
(`files.consumerfinance.gov/ccdb/complaints.csv.zip`, header row checked) carries
`complaint_what_happened`, and `has_narrative=true` no longer filters anything (the same count
with and without it). `format=json` returns 404 and `field=complaint_what_happened` is refused.
ZIP codes come as five digits.

What that changes:

- **Triage works on the structured fields.** Product, issue, sub-issue, tags, state and how it
  was submitted say a great deal: "Identity theft protection", "Foreclosure", "Fraud or scam",
  "Discrimination" and the tags drive most of the routing, and they are code checks.
- **The narrative stays optional in the schema.** If CFPB restores narratives, ingestion keeps
  them (redacted, scanned for injection) without a schema change; until then the field is null.
- **The injection and redaction paths are still exercised.** Every free-text field from CFPB
  (`company_public_response`, issue labels, anything a future narrative adds) is treated as
  untrusted and scanned. A synthetic adversarial set, labelled synthetic, supplies narratives and
  injected fields to test those paths, because live data no longer will.
- **The agent's job is smaller than first planned, and the docs say so.** It writes a short
  internal summary, a category and urgency for the person, and a never-sent draft
  acknowledgement. Code routes. The eval measures whether the agent adds anything over code.

## The hard rule: nothing is ever sent (HM-25)

Hari asked for this to be a check in code, not a policy line. **The system never replies to,
contacts or sends anything to anyone.** It is built first, test-first, and enforced in four
places:

1. **One outbound path, GET only, one host.** `hall_monitor/egress.py` is the only module that
   may open an HTTP connection for data. Its transport refuses any request that isn't a `GET` to
   `www.consumerfinance.gov` under the complaint API path, on every hop (redirects are off and
   each request is checked again), and records each refusal in the audit log. The complaint
   desk's model client goes through the same kind of guard, allowing only `POST` to the model
   API's messages endpoint (see "What still leaves the machine").
2. **Through the governed tool gateway.** The CFPB fetch is an MCP tool, `cfpb_complaint_search`,
   behind `ToolGateway`. Only the pipeline may call it; no agent is offered it.
3. **No send-capable tool can exist or be granted.** Every tool is declared in one catalogue of
   read-only tools (`hall_monitor/no_send.py`). A manifest that lists a tool outside it, or one
   whose name reads as sending (send, email, reply, post, notify, webhook, submit, publish, sms,
   message, dispatch, deliver, forward, upload, tweet, call), is refused at run time by the
   registry, refused for approval, and the gateway refuses to start on a server that exposes such
   a tool.
4. **A CI check fails if anyone adds a sending capability.** `python -m hall_monitor.no_send
   check` reads every module's syntax tree and fails on imports of mail, messaging and raw
   network libraries (`smtplib`, `socket`, `urllib.request`, `http.client`, `requests`,
   `aiohttp`, `ftplib`, `xmlrpc`, `twilio`, `sendgrid`, `slack_sdk` and others), on any `httpx`
   use outside `egress.py`, on any HTTP method other than GET in `egress.py`'s data path, and on
   send-capable packages in `pyproject.toml`. It runs in CI and in the tests.

**Drafts are kept, never sent.** A draft acknowledgement is a `DraftResponse` with
`never_sent: Literal[True]` and no field that could hold an address, a recipient or a channel.
It is stored in the evidence store by its hash; the audit log records the hash and `never_sent`.
There is no code that reads a draft for delivery, and the static check stops one being added
quietly.

**What still leaves the machine, stated plainly.** Model calls go to the Claude API (a `POST`
carrying the redacted structured fields), and traces go to Langfuse when its keys are set
(metadata only, HM-11). Neither reaches a consumer, a company or CFPB. The desk's model client is
guarded to its one endpoint; Langfuse's client is not guarded by the egress policy, which is a
gap in the register.

## Ingestion (HM-26)

- **Incremental, on a schedule.** `python -m hall_monitor.complaints.ingest pull` asks for
  complaints received since the last watermark (the newest `date_received` already stored, less
  one day of overlap, because CFPB indexes late). `--every 6h` repeats it; a cron entry or a
  scheduled task runs it in production. No scheduled job runs in CI: CI never touches the
  network.
- **Retries, backoff and rate limiting** in the egress client: at most one request a second by
  default, up to four retries on timeouts, 429 and 5xx, with exponential backoff and jitter, and
  `Retry-After` honoured up to a cap. A 4xx other than 429 is not retried.
- **Idempotent dedupe by complaint ID.** Records go into a local SQLite store keyed by
  `complaint_id`. A record seen again is skipped if its content hash is unchanged and stored as a
  new version if CFPB updated it (a company response arriving, say).
- **Schema validation, with quarantine.** Each record is validated against a strict pydantic
  model. One that fails is quarantined with the reason (field names and error types only) and
  its hash, and the pull goes on.
- **Snapshots.** Every pull writes the raw page responses and a manifest (query, counts, hashes)
  to `var/complaints/snapshots/<pull_id>/`. A pinned snapshot is committed under
  `data/cfpb/snapshots/` and every eval and demo runs from it, so results are reproducible
  without the network.
- **Minimisation at the door.** The company name is dropped on ingestion and never stored: the
  desk is one simulated company, and keeping real company names next to triage results invites
  reading them as findings. ZIP codes are cut to their first three digits (enough for the
  fairness check). The pinned snapshot in git is stored after this step.

## Triage (HM-27)

```text
complaint -> redact, scan for text aimed at the AI           (code)
          -> complaint triage agent: summary, category,      (Claude Haiku, no tools)
             urgency, a never-sent draft acknowledgement
          -> routing                                         (code)
          -> audit log: IDs, hashes, codes, cost              (code)
```

**Routing to a person, decided in code.** These always go to a person, whatever the agent says:

| Reason code | When |
|---|---|
| `fraud` | product, issue or sub-issue names fraud or a scam |
| `identity_theft` | identity theft, information belonging to someone else, or an account opened without consent |
| `foreclosure` | foreclosure, or trouble during payment process on a mortgage that names it |
| `discrimination` | discrimination, or unfair treatment on a protected basis |
| `older_american` | tagged Older American |
| `servicemember` | tagged Servicemember |
| `text_addresses_ai` | any injection signal in any free-text field |
| `agent_failed` | the agent refused, failed, was stopped or isn't approved, or its answer didn't validate |
| `agent_uncertain` | the agent's confidence is not high, or it chose `unclear` |
| `agent_disagrees_with_code` | the agent's urgency is lower than the code's floor for that issue |
| `untimely` | CFPB shows the company response as untimely |

Everything else goes to a standard queue, still for a person to work: the desk prepares, people
act. There is no automatic resolution path at all.

The agent's output is advisory. It can raise urgency, never lower it below the code's floor, and
it can never take a complaint off the person's queue.

## Generalising the core, not forking it

The claims example must keep passing unchanged. The changes to shared code are:

- `ToolGateway` takes the tool server, its data-class map and the pipeline's tools as arguments,
  defaulting to the claims server, so the complaint desk passes its own.
- The registry learns the no-send rule (every manifest's tools must be in the read-only
  catalogue), which every example inherits.
- A new data class, `public_record`, for data already published, so a complaint agent needs no
  approval for PII or financial data it doesn't receive.
- The panel gains a complaint desk page, read from the audit log alone, as the HR page is.

Everything else (hall pass, approvals, the hash-chained log, the kill switch, redaction, the
injection scan, cost accounting, the eval gate) is used as it is.

## Evals and fairness (HM-28)

- **Deterministic, on the pinned snapshot.** No model calls in CI. Routing is scored against
  rules in code (every must-route case went to a person). The agent's category and urgency are
  scored against CFPB's own fields as partial ground truth: `company_response` (monetary relief
  suggests a well-founded complaint) and `timely`. That ground truth is partial and the report
  says so: a closed-with-explanation complaint can still have been urgent.
- **Fairness.** Counterfactual: the same complaint with only the ZIP prefix or the state changed
  must route the same (code routing can't depend on them; the test proves it). Group rates: the
  share sent to a person and the urgency the agent gave, by ZIP prefix region and by tag, with
  the gap reported and gated.
- **Gate.** `evals/gate.yaml` gains the complaint results: zero must-route complaints missed,
  zero drafts with a delivery path, fairness gaps under the threshold.

## Change control and observability

- One new agent, `complaint_triage`, with a manifest, an owner from `registry/owners.yaml` (a new
  fictional owner for the complaint desk), a prompt version and an approval.
- ADRs: 0021 no reply and no send, 0022 ingestion and snapshots, 0023 complaint triage routing,
  0024 complaint evals.
- Structured JSON logs for pulls (counts, retries, quarantined, duration), cost per complaint in
  the audit log, and the panel shows the queue, what went to a person and why, pull metrics and
  the no-send control's status (the static check's result and the egress refusals logged).

## Slices (one pull request each)

1. This design, ADR 0021 and HM-25: the no-send rule, test-first.
2. Ingestion, snapshots and the pinned snapshot (HM-26, ADR 0022).
3. The complaint triage agent, routing in code, drafts kept never-sent (HM-27, ADR 0023).
4. Evals, fairness, gate, panel page and docs (HM-28, ADR 0024).

## Not doing

- Replying, escalating to CFPB, notifying a company or anyone else: never, by design.
- Naming companies: dropped at ingestion.
- Live model runs in CI or tests. Live runs only when Hari asks, with the cost stated first.
