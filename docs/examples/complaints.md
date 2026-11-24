# Complaint desk on CFPB data

> **Status:** the no-send rule, ingestion with snapshots, triage, evals, a panel page and a
> submission form are built. **Scripted runs only:** the triage model hasn't been run live.

## Why this domain

A simulated company complaint desk that triages complaints from the public [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/) (free, keyless, read-only, updated daily), so the same controls face real input nobody wrote for them. It prepares each complaint for a person; it is not a CFPB system, it doesn't represent any company in the data, and nothing in it is a finding about a company. The design is in [`docs/complaint-desk-design.md`](../complaint-desk-design.md); it shipped in slices, and this page says what each one has built.

## What the agents do

A complaint triage agent (Haiku, no tools) reads the labels and redacted free text as untrusted, without the complaint ID, state or ZIP prefix, and writes a summary, category, urgency, concerns and a draft acknowledgement. It can only add: a concern or a higher urgency. Its low confidence, an unclear category, an urgency below the code's floor, an untimely company response, text aimed at the AI and any agent failure also go to specialist review; a failure never invents an answer.

A complaint desk supervisor (Haiku, no tools) chooses the next step: run the triage agent, ask for one independent re-check, finish, or hand off to specialist review. It sees codes only, never sends anything and never chooses a queue; code checks every choice and routes every complaint to a person ([ADR 0028](../adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md)).

## What code decides

### Triage routing

**Triage** (HM-27, [ADR 0023](../adr/0023-complaint-triage-routed-in-code.md)): every complaint goes to a person; nothing is resolved automatically. Code reads CFPB's labels and sends to specialist review, at high urgency, anything naming fraud, a scam or a transaction the consumer didn't authorise or make, identity theft (an account, loan, debt or inquiry that isn't theirs), foreclosure or a mortgage the consumer is struggling to pay, or discrimination, and anything tagged Older American or Servicemember, whatever the agent says.

A draft that fails a strict pattern check, read as written and decoded (a refund, credit, waiver, guarantee, commitment or deadline; a link, domain or spelled-out link, email, handle, phone or vanity number, address, extension or person to ask for; any run of three or more digits) is dropped; the rest are kept, never sent. A routed complaint whose stored record later changes in a way a person must see (CFPB adds a tag, marks the response untimely, or publishes a narrative where there was none) is re-routed to specialist review with an added log entry, once per version, never lowered; a routine status change (In progress to Closed with explanation) keeps its route and is counted as changed, not re-routed. The change is fetched only by a manual pull from an earlier date.

### The hard rule: never reply, contact or send

**The system never replies to, contacts or sends anything to anyone.** It is a check in code, built first (HM-25, [ADR 0021](../adr/0021-no-reply-no-send.md)):

- **One outbound data path.** `hall_monitor/egress.py` is the only module allowed an HTTP client, and a client can be made only with its two shipped policies. Its transport refuses, before anything leaves, any request that isn't a `GET` over https to `www.consumerfinance.gov` under the complaint API path (another host, port, scheme or path, credentials in the URL, a path that normalises elsewhere), never follows a redirect, and logs each refusal to the audit log (method, host, a hash of the URL, never a body). The desk's model client is guarded the same way to `POST` on the Claude API's messages endpoint (that path exactly).
- **No send-capable tool.** Every tool is in one read-only catalogue (`hall_monitor/no_send.py`). A manifest that lists a tool outside it, or one whose name reads as sending in any naming style, is refused at run time by the registry and refused for approval; the tool gateway takes only a plain in-process MCP server (not a URL or a proxy), refuses to start on one exposing such a tool, and refuses to call one.
- **Drafts are kept, never sent.** A draft acknowledgement has no field for an address, recipient or channel, `never_sent` can only be true, and it is stored by its hash with a log entry saying `never_sent` and delivery `none`; verifying the log checks the stored draft wasn't changed.
- **CI fails if anyone adds a sending capability.** `python -m hall_monitor.no_send check` reads every module's syntax tree and the dependencies, and fails on mail, messaging and raw network libraries and calls, HTTP clients outside `egress.py` or its internals imported elsewhere, HTTP write calls (named methods, or a method other than GET given to `request`, `stream` or `build_request`), fastmcp clients or proxies outside the gateway, dynamic imports, shell commands and sending packages. It is a tripwire for the honest mistake: code built to evade it (a method name in a variable, say) can, so review is still the control for that.

What still leaves the machine: model calls to the Claude API, and metadata-only traces to Langfuse when its keys are set (Langfuse's client isn't behind the egress guard). Neither reaches a consumer, a company or CFPB.

## Who decides

A person, for every complaint: the desk only prepares it. The control panel's **Complaint desk** page shows the queue, why each complaint went where it did, cost per complaint, pulls, and the no-send control's status (the static check now, egress refusals logged, drafts not marked never sent).

### Adding a complaint by hand

A small form (HM-33, [ADR 0029](../adr/0029-complaint-submission-form.md), proposed), a separate app so the panel stays read-only (`python -m hall_monitor.complaints.submit --demo` or `--live`), takes CFPB's structured fields (a product from CFPB's list, an issue, sub-issue, the two tags) and an optional narrative, gives the complaint a synthetic `syn-` ID that can't be a CFPB one, and runs it through the desk's own agentic pipeline, so it shows on the panel's Complaint desk page. The submitter sees only its ID and state. Demo mode uses the scripted stand-in and calls no API; live mode calls the Claude API through the desk's guarded client and stops at a per-run cap. The protections are the leave form's. Routing reads the labels, not the narrative; the log never holds the typed-in text, though a kept draft may quote it; live mode is tested only with a fake client.

## Data

**Ingestion** (HM-26, [ADR 0022](../adr/0022-cfpb-ingestion-and-snapshots.md)): `python -m hall_monitor.complaints.ingest pull` reads complaints about the simulated company's products (a consumer lender and bank, so not credit reporting) through one read-only MCP tool behind the tool gateway, which only the pipeline may call. Pulls go oldest first and resume after a stored cursor, stop at the desk's capacity and say whether they caught up; `--every 6h` repeats them. Requests are spaced and retried with backoff; records are minimised at the door (no company name, ZIP prefix only), validated strictly, kept by complaint ID (a repeat skipped, a change a new version) or quarantined with field names and error types only. Every pull is snapshotted to disk with a manifest of hashes and replays offline; the pinned snapshot ([`data/cfpb/`](../../data/cfpb/README.md), 500 real complaints received on 1 October 2026) is what evals and demos run on. Tested against a fake CFPB, plus the one live pull that made the snapshot. It is the only folder of real data in the repo.

**A finding that changed the design:** as checked on 2026-11-11, CFPB's API and bulk download no longer publish consumer narratives, so the desk triages on the structured fields (product, issue, sub-issue, tags, state, ZIP prefix, how it was submitted), keeps the narrative optional in case it returns, and tests the injection and redaction paths with a labelled synthetic set.

## Results

**Evals** (HM-28, [ADR 0024](../adr/0024-complaint-evals-on-a-pinned-snapshot.md)): `python -m hall_monitor.complaints.evals` scores the desk by code on the pinned snapshot, against a hand-written answer key of CFPB labels ([`data/cfpb/label_reasons.yaml`](../../data/cfpb/label_reasons.yaml)), never against the routing patterns. On the scripted run (a stand-in agent with fixed rules, so it measures the desk's code, not the model): all 197 complaints the key and tags say need specialist review got it, at high urgency, with their reasons; all 20 untimely ones too; 0 errors; 0 drafts with a delivery path. A counterfactual check (gated) confirms the state and ZIP prefix change neither the code's reasons nor the text the agent is sent; it can't flip by construction and calls no model, so it shows location doesn't reach the decision, not that the model is unbiased. Specialist and high-urgency rates by ZIP region and tag are reported, not gated. As partial ground truth, 16 of the 31 complaints the company closed with monetary relief (52%) were flagged, against 111 of the 289 closed with an explanation (38%); with the stand-in agent, those rates depend on its rules too. The CI gate checks these on every pull request. The live model hasn't been evaluated.

## Run it

```bash
uv run python -m hall_monitor.complaints.run --demo        # triage the pinned snapshot offline with a scripted stand-in; no API calls
uv run python -m hall_monitor.complaints.evals             # score the desk on the pinned snapshot, scripted; no API calls
uv run python -m hall_monitor.complaints.submit --demo     # form at http://127.0.0.1:8767: add a synthetic complaint to the desk; no API calls
uv run python -m hall_monitor.complaints.submit --live     # calls the Claude API: well under a cent a complaint (desk budget $0.02), 5 per run
```

`python -m hall_monitor.complaints.run --live --limit N` and `python -m hall_monitor.complaints.evals --live --limit N` call the API (well under a cent a complaint); `--live` needs `--limit`.

## Where it lives

- **Code:** `hall_monitor/complaints/`, plus `hall_monitor/egress.py` and `hall_monitor/no_send.py` for the no-send rule. The desk action table is `desk_action` in `pipeline.py`. The loop it runs in is the shared `hall_monitor/agents/loop.py`.
- **Design and decisions:** [`docs/complaint-desk-design.md`](../complaint-desk-design.md); ADRs [0021](../adr/0021-no-reply-no-send.md) to [0024](../adr/0024-complaint-evals-on-a-pinned-snapshot.md) and [0029](../adr/0029-complaint-submission-form.md).

### Built on this desk

Two pieces of work use the complaint desk as their test bed. They aren't examples of their own:

- **Complaint investigation crew** ([design](../complaint-crew-design.md)): a lead agent hands parts of a case to helper agents under narrower passes. Delegation and handoffs are built (`hall_monitor/delegation.py`, `hall_monitor/handoff.py`, ADRs [0025](../adr/0025-delegated-passes-only-narrow.md) and [0031](../adr/0031-agents-act-within-their-grant.md)); the crew agents aren't.
- **Red-team arena** ([design](../redteam-arena-design.md), [ADR 0026](../adr/0026-red-team-arena-judged-by-code.md)): an offline attacker, a hijacked defender and a judge in code (`hall_monitor/redteam/`); scripted run only, and its findings aren't fixed yet: 3 of 18 attacks still caused harm, all complaints told only in the narrative ([report](../../evals/results/redteam-scripted/report.md)).
