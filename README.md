hall-monitor

Governance patterns for agentic AI in regulated financial services. Worked example: insurance claims triage.

Every agent needs a hall pass. hall-monitor checks it, watches where the agent goes, writes everything down, and calls a grown-up when it matters.

Status: early build, in public. This repo is under active development (started November 2026). Each milestone ships with its eval numbers, including the bad ones. See Roadmap and Results.

Why this exists

Most agent demos show what a model can do. Regulated firms need to know what an agent is allowed to do, who approved it, what it touched, and whether anyone can prove that six months later.

Those answers usually live in policy documents. hall-monitor puts them in code:

Every agent is registered (its hall pass) before it can run, with an owner, a risk tier and an explicit tool allow-list.
Consequential decisions stop for a human, and the evidence the human saw is recorded with their decision.
Every step is logged in an append-only, hash-chained audit trail that shows if anything was altered.
Every change is evaluated in CI, so a new prompt or model can't silently ship a regression.
Every control maps to a framework (NIST AI RMF; FINOS AI Governance Framework planned), so risk, compliance and audit teams can evaluate it without reading the code.

The governance layer is the product. Claims triage is the proof that it works on a real-shaped regulated workflow.

The worked example: insurance claims triage

When a customer files a claim, someone has to read it, check it against the policy, spot anything suspicious, and decide what happens next: pay it, ask for more information, investigate, or deny it. Claims are high-volume and time-sensitive, they carry personal and sometimes medical information, and a wrong denial harms a real customer. That makes triage a good test of whether governed agents can speed up the work without taking unacceptable risk.

The synthetic claims in this repo cover trip cancellation, lost or delayed baggage, and emergency medical expenses abroad.

The agents:

Intake. Extract structured fields from the claim submission: claimant, policy number, incident date and type, amounts claimed, supporting documents.
Coverage check. Compare the claim with the policy wording (retrieved from a policy library) and identify the clauses that apply, including exclusions and limits.
Critic. Check the extraction and the coverage reasoning against the source documents and send them back for correction when they disagree.
Risk and fraud flags. Flag inconsistencies, such as dates that don't line up, duplicate receipts or claims filed soon after a policy started, and assign a risk level.
Recommendation. Propose a next step with its reasons and the evidence behind it.

The agent never denies a claim or approves a large payout on its own. Denials, payouts above a threshold and any fraud flag go to a human adjuster through an approval gate.

Agents get supporting records only through governed MCP tools (policy_lookup, claims_history).

All data in this repository is synthetic. No real customer, policy or claims data from any company is used.

Architecture
disagrees
denial, large payoutor fraud flag
routine, low value
approved
sent back
Claim submission
Gatewayregistry check, routing,PII redaction, cost
Intake agent
Coverage check agent
Critic agent
Risk and fraud agent
Recommendation
Adjusterapproval gate
Next step
Hash-chainedaudit log
Langfusetraces, cost, evals
Governance controls
Control	What it does	NIST AI RMF function	Status
Agent registry	Typed manifest per agent: ID, owner, risk tier, model, allowed tools, version. Unregistered agents are refused.	Govern	Built (milestone 1)
Tool allow-list	Agents can call only the MCP tools their manifest names	Govern, Manage	Planned
Adjuster approval gates	Graph interrupts on denials, payouts above threshold and fraud flags; the adjuster's identity, the evidence shown and the decision are recorded	Manage	Planned
Tamper-evident audit log	Append-only log, each entry hashed with the previous one	Govern, Manage	Planned
PII and health-data redaction	Personal and medical details masked before they reach a model or a log	Manage	Planned
Model routing	Cheaper model by default; stronger model for complex or high-risk claims	Manage	Planned
Cost and value telemetry	Cost per claim, time to triage, human override rate, exception rate	Measure	Planned
Eval harness	Extraction accuracy, coverage-reasoning accuracy and hallucination rate against ground truth	Measure	Planned
Fairness checks	Recommendation rates compared across synthetic customer groups, so systematic skew is caught before release	Measure	Planned
Adversarial test suite	Prompt injection in claim text and attachments, PII leakage, unauthorized tool escalation	Measure, Manage	Planned
CI eval gate	A pull request fails if eval scores drop below threshold	Manage	Planned
Framework mapping	Every control and audit field mapped to NIST AI RMF and FINOS AIGF	Map, Govern	Planned
Results

Numbers are published as each milestone lands, from runs anyone can reproduce with the commands in this repo.

Milestone	Extraction accuracy	Coverage-reasoning accuracy	Hallucination rate	Cost per claim	Human override rate
Baseline	to be published	to be published	to be published	to be published	n/a
Roadmap
 1. Repo, agent registry, synthetic claims and policies with ground truth, tracing
 2. Baseline eval run, results committed
 3. Critic agent and self-correction loop
 4. MCP servers: policy_lookup, claims_history
 5. Adjuster approval gate with persistent checkpoints
 6. Hash-chained audit log
 7. Model routing and cost-per-claim telemetry
 8. PII redaction, fairness checks and adversarial test suite
 9. NIST AI RMF and FINOS AIGF mapping; CI eval gate
 10. Architecture decision records, one-page board brief, external review
Stack

Python 3.12 · LangGraph · Claude (Haiku and Sonnet) · MCP via fastmcp · Postgres + pgvector · Langfuse · DeepEval · FastAPI

Quickstart

Milestone 1 runs the intake agent on a claim and prints its extraction with the hall pass it ran under.

bash
git clone https://github.com/keeplearnin/hall-monitor.git
cd hall-monitor
uv sync
cp .env.example .env   # add ANTHROPIC_API_KEY and Langfuse keys
uv run python -m hall_monitor.run --claim data/synthetic/claims/claim_001.json
uv run pytest          # registry, dataset and intake checks; no API calls
Repository layout
text
hall-monitor/
├── registry/            # Agent manifests (YAML) and the registry schema
├── hall_monitor/
│   ├── agents/          # Intake, coverage check, critic, risk and fraud, recommendation
│   ├── gateway/         # Routing, redaction, cost tracking
│   ├── governance/      # Approval gates, audit log
│   └── tools/           # MCP servers
├── data/synthetic/
│   ├── policies/        # Synthetic policy wordings
│   ├── claims/          # Synthetic claim submissions
│   └── ground_truth/    # Expected extractions and coverage decisions
├── evals/               # Eval suites, fairness checks, adversarial tests
├── docs/
│   ├── controls.md      # Control-to-framework mapping
│   ├── adr/             # Architecture decision records
│   └── board-brief.md   # One-page summary for executives and boards
└── .github/workflows/   # CI eval gate
Design principles
Governance in code, not in documents. If a control can't be tested, it doesn't count.
Humans make the consequential calls. The agent prepares the evidence; an adjuster decides denials, large payouts and fraud.
Measure before you claim. Every result in this README comes from a reproducible run.
Model-agnostic by design. Controls sit in the gateway and the graph, not inside one vendor's model.
Built for audit. Any recommendation can be traced back to its inputs, model, prompt version, policy clauses and approver.
Non-goals
A production claims system. This is a reference implementation.
A polished user interface. The evals, audit trail and control mapping are the point.
Legal, compliance, underwriting or claims-handling advice.
Contributing

Issues and pull requests are welcome, especially from people working on AI governance in insurance, banking and other regulated industries. If you've had to put an agent in front of an auditor or a regulator, I'd like to hear what they asked.

Author

Built by Hari Perugu, engineering and AI platform leader in regulated financial services. LinkedIn

This is a personal project. It is not affiliated with, endorsed by, or built on the systems or data of any current or former employer.

License

Apache License 2.0
