"""The control panel's pages: plain HTML, rendered on the server from the views.

Every value is escaped unless it is markup built here (Safe). Links are relative, so the same
pages work served (`serve`) and saved as files (`snapshot`). No scripts, and no fonts or styles
from anywhere else: the page's security policy allows only its own stylesheet.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from urllib.parse import quote

from hall_monitor.hr.routing import REASONS as LEAVE_REASONS
from hall_monitor.panel.views import linkable

NAV = (
    ("index.html", "Overview"),
    ("agents.html", "Agents"),
    ("queue.html", "Review queue"),
    ("cases.html", "Cases"),
    ("audit.html", "Audit integrity"),
    ("evals.html", "Evals and gate"),
    ("controls.html", "Controls register"),
)


class Safe(str):
    """Markup built by this module, inserted without escaping."""


def esc(value: object) -> Safe:
    if isinstance(value, Safe):
        return value
    return Safe(html.escape("" if value is None else str(value)))


def join(parts: Iterable[object]) -> Safe:
    return Safe("".join(esc(p) for p in parts))


def tag(name: str, *children: object, **attrs: object) -> Safe:
    attributes = "".join(
        f' {k.rstrip("_").replace("_", "-")}="{esc(v)}"' for k, v in attrs.items() if v is not None
    )
    return Safe(f"<{name}{attributes}>{join(children)}</{name}>")


def badge(text: str, tone: str) -> Safe:
    return tag("span", text, class_=f"badge {tone}")


def table(headers: Iterable[str], rows: Iterable[Iterable[object]], empty: str) -> Safe:
    rows = list(rows)
    if not rows:
        return tag("p", empty, class_="empty")
    return tag(
        "div",
        tag(
            "table",
            tag("thead", tag("tr", *(tag("th", h) for h in headers))),
            tag("tbody", *(tag("tr", *(tag("td", c) for c in row)) for row in rows)),
        ),
        class_="table-wrap",
    )


def usd(value: object) -> str:
    if value is None:
        return "—"
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value)
    if 0 < abs(value) < 0.01:
        return f"${value:,.4f}"
    return f"${value:,.3f}" if 0 < abs(value) < 1 else f"${value:,.2f}"


# The same policy the server sends as a header, so a saved snapshot carries it too.
CSP = "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'"


def case_link(claim_id: str, prefix: str = "") -> Safe:
    """A link to a case page, or the ID as text when it can't safely be a path."""
    if not linkable(claim_id):
        return tag("span", claim_id, title="no page: this ID can't be used in a link")
    return tag("a", claim_id, href=f"{prefix}cases/{quote(claim_id, safe='')}.html")


def short(digest: str | None) -> Safe:
    return tag("code", digest[:12], title=digest) if digest else esc("—")


STATE_TONE = {
    "may run": "ok",
    "stopped": "stop",
    "refused": "stop",
    "unapproved": "stop",
    "pass": "ok",
    "fail": "stop",
    "stale": "warn",
    "missing": "muted",
    "built": "ok",
    "partial": "warn",
    "decided": "ok",
    "waiting for a person": "warn",
    "waiting for HR": "warn",
    "evidence pack erased or changed": "stop",
    "routed automatically, no decision recorded": "stop",
    "triaged, no decision recorded": "stop",
    "withheld": "muted",
}


def state_badge(state: str) -> Safe:
    tone = STATE_TONE.get(state) or STATE_TONE.get(state.split(",")[0], "muted")
    return badge(state, tone)


def number(value: object) -> object:
    return f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else value


def page(title: str, current: str, body: Safe, *, root: str = "", demo: bool = False) -> str:
    nav = tag(
        "nav",
        *(
            tag("a", label, href=root + href, aria_current="page" if href == current else None)
            for href, label in NAV
        ),
        aria_label="Panel sections",
    )
    banners = [
        tag(
            "p",
            tag("strong", "Read-only. "),
            "This panel changes nothing. An owner stops an agent with ",
            tag("code", "python -m hall_monitor.kill_switch stop"),
            "; an adjuster decides with ",
            tag("code", "python -m hall_monitor.review decide"),
            "; approvals and restarts are reviewed changes to the registry.",
            class_="banner",
        )
    ]
    if demo:
        banners.append(
            tag(
                "p",
                tag("strong", "Demo data. "),
                "Synthetic claims and leave requests answered by a scripted model from the "
                "answer key, not live model runs; costs are from the scripted token counts. "
                "Adjusters and HR staff sign with public demo keys.",
                class_="banner demo",
            )
        )
    doc = tag(
        "html",
        tag(
            "head",
            Safe('<meta charset="utf-8">'),
            Safe('<meta name="viewport" content="width=device-width, initial-scale=1">'),
            Safe(f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'),
            tag("title", f"{title} · hall-monitor control panel"),
            Safe(f'<link rel="stylesheet" href="{esc(root)}panel.css">'),
        ),
        tag(
            "body",
            tag(
                "header",
                tag(
                    "div",
                    tag("a", "hall-monitor", href=root + "index.html", class_="brand"),
                    tag("span", "control panel", class_="brand-sub"),
                    class_="brand-row",
                ),
                nav,
            ),
            tag("main", *banners, tag("h1", title), body),
            tag(
                "footer",
                "Reads the registry, stop list, audit log, controls register and eval gate. Shows "
                "IDs, hashes, decision fields, reasons in the code's own words and costs; free "
                "text the model wrote, and kill-switch and adjusters' reasons, are withheld.",
            ),
        ),
        lang="en",
    )
    return "<!doctype html>\n" + doc


# --- Pages -----------------------------------------------------------------------------------


def _attention(data: dict) -> Safe:
    """What an operator must see first: agents that can't run, the review queue, the audit
    chain. Each line is a fact from the core, with a link to where it comes from."""
    items = []
    if data["registry_problem"]:
        items.append((state_badge("fail"), data["registry_problem"], None))
    for a in data["agents_not_running"]:
        items.append(
            (
                state_badge(a["state"]),
                f"Agent {a['id']} can't run ({a['state']}); its owner is {a['owner']}.",
                f"agents/{a['id']}.html",
            )
        )
    if data["audit_problem"]:
        items.append((state_badge("fail"), data["audit_problem"] + ".", "audit.html"))
    unrecorded = sum(w.get("metrics", {}).get("no_decision_recorded", 0) for w in data["workflows"])
    if unrecorded:
        items.append(
            (
                state_badge("fail"),
                f"{unrecorded} cases finished triage with no decision recorded; a person must "
                "look at them.",
                "cases.html",
            )
        )
    waiting = sum(w.get("metrics", {}).get("waiting_for_a_person", 0) for w in data["workflows"])
    if waiting:
        items.append(
            (badge("queue", "warn"), f"{waiting} claims are waiting for a person.", "queue.html")
        )
    leave = next((w["metrics"] for w in data["workflows"] if w["id"] == "hr_leave"), {})
    if leave.get("waiting_for_hr"):
        line = f"{leave['waiting_for_hr']} leave requests are waiting for HR."
        items.append((badge("queue", "warn"), line, "queue.html"))
    if leave.get("evidence_problems"):
        line = (
            f"{leave['evidence_problems']} leave requests have an evidence pack that was erased "
            "or changed, so they can't be decided."
        )
        items.append((state_badge("fail"), line, "audit.html"))
    if leave.get("stopped_before_the_rules"):
        line = (
            f"{leave['stopped_before_the_rules']} leave requests stopped before the rules ran; "
            "prepare them again once the cause is fixed."
        )
        items.append((state_badge("fail"), line, "cases.html"))
    audit = data["audit"]
    if not audit["exists"]:
        items.append((badge("audit", "muted"), "No audit log yet.", "audit.html"))
    elif audit["ok"]:
        line = f"The audit chain verifies: {audit['entries']} entries."
        items.append((state_badge("pass"), line, "audit.html"))
    else:
        line = f"The audit chain does NOT verify: {len(audit['problems'])} problems."
        items.append((state_badge("fail"), line, "audit.html"))
    return tag(
        "section",
        tag("h2", "Needs attention"),
        tag(
            "ul",
            *(
                tag("li", mark, " ", text, " ", tag("a", "Open", href=href) if href else "")
                for mark, text, href in items
            ),
            class_="attention",
        ),
        class_="card attention-card",
    )


def _tiles(stats: list[tuple[str, object, bool]]) -> Safe:
    """(label, value, needs a person when above zero)"""
    return tag(
        "dl",
        *(
            tag(
                "div",
                tag("dt", label),
                tag("dd", value),
                class_="stat flag" if flag and value else "stat",
            )
            for label, value, flag in stats
        ),
        class_="stats",
    )


def _claims_card(w: dict) -> Safe:
    m = w["metrics"]
    share = f"{m['sent_to_a_person'] / m['triaged']:.0%}" if m["triaged"] else "—"
    stats = [
        ("Cases", m["cases"], False),
        ("Sent to a person", f"{m['sent_to_a_person']} ({share})", False),
        ("Waiting for a person", m["waiting_for_a_person"], True),
        ("Decided automatically", m["decided_automatically"], False),
        ("Decided by a person", m["decided_by_a_person"], False),
        ("People overrode the AI", m["overrides"], False),
        ("Stopped cases", m["stopped_cases"], True),
        ("Decisions refused by the rules", m["rejected_decisions"], False),
        ("Model cost", usd(m["cost_usd"]), False),
        ("Cost per case", usd(m["cost_per_case_usd"]), False),
    ]
    return tag(
        "section",
        tag("h2", w["name"]),
        badge(w["status"], "ok"),
        _tiles(stats),
        tag("h3", "Why cases went to a person (a case can have several reasons)"),
        table(
            ("Reason the routing rule gave", "Cases"),
            m["routing_reasons"],
            "No case has been routed yet.",
        ),
        class_="card wide",
    )


def _leave_card(w: dict) -> Safe:
    m = w["metrics"]
    review = f"{m['hr_review'] / m['prepared']:.0%}" if m["prepared"] else "—"
    stats = [
        ("Requests", m["requests"], False),
        ("To HR review", f"{m['hr_review']} ({review})", False),
        ("To specialist sign-off", m["specialist_sign_off"], False),
        ("Waiting for HR", m["waiting_for_hr"], True),
        ("Approved", m["approved"], False),
        ("Denied", m["denied"], False),
        ("Extended", m["extended"], False),
        ("Information asked for", m["information_requested"], False),
        ("Decisions refused by the rules", m["refused_decisions"], False),
        ("Stopped before the rules", m["stopped_before_the_rules"], True),
        ("Text aimed at the AI", m["text_aimed_at_the_ai"], True),
        ("Model cost", usd(m["cost_usd"]), False),
    ]
    return tag(
        "section",
        tag("h2", w["name"]),
        badge(w["status"], "ok"),
        tag(
            "p",
            "Every request is decided by a person: specialist sign-off is a draft a leave "
            "specialist signs. The rules name the least senior role that may decide.",
            class_="lead",
        ),
        _tiles(stats),
        tag("h3", "Why requests went to HR review (a request can have several reasons)"),
        table(
            ("Reason the rules gave", "Requests"),
            m["reasons"],
            "No request has been sent to HR review yet.",
        ),
        class_="card wide",
    )


def overview_page(data: dict) -> str:
    cards = [
        _claims_card(w) if w["id"] == "travel_claims" else _leave_card(w) for w in data["workflows"]
    ]
    body = join([_attention(data), tag("div", *cards, class_="grid")])
    return page("Overview", "index.html", body, demo=data["demo"])


def agents_page(data: dict, *, demo: bool) -> str:
    problem = tag("p", data["problem"], class_="banner alert") if data["problem"] else Safe("")
    rows = (
        (
            tag("a", a["name"], href=f"agents/{a['id']}.html"),
            tag("span", a["owner"]["name"] or a["owner"]["id"], title=a["owner"]["id"]),
            a["risk_tier"],
            tag("code", a["model"]),
            a["version"],
            state_badge(
                "pass" if a["manifest_approved"] and a["prompt_approved"] is not False else "fail"
            ),
            state_badge(a["state"]),
        )
        for a in data["agents"]
    )
    body = join(
        [
            problem,
            tag(
                "p",
                "Whether an agent may run is the registry's own answer: the same check that "
                "refuses it at run time, with its current prompt.",
                class_="lead",
            ),
            table(
                ("Agent", "Owner", "Risk tier", "Model", "Version", "Approved", "Now"),
                rows,
                "No agents are registered.",
            ),
        ]
    )
    return page("Agents", "agents.html", body, demo=demo)


def agent_page(a: dict, *, demo: bool) -> str:
    facts = [
        ("Purpose", a["purpose"]),
        ("Owner", f"{a['owner']['name']} ({a['owner']['id']}), {a['owner']['role']}"),
        ("Risk tier", a["risk_tier"]),
        ("Status in manifest", a["status"]),
        ("Model", tag("code", a["model"])),
        (
            "Economy model",
            "none"
            if a["economy_model"] is None
            else join(
                [
                    tag("code", a["economy_model"]),
                    f" for claims up to {usd(a['economy_max_claim_usd'])}",
                ]
            ),
        ),
        ("Tools it may call", ", ".join(a["allowed_tools"]) or "none"),
        ("Data classes it may see", ", ".join(a["data_classes"]) or "none"),
        ("Manifest version", a["version"]),
        (
            "Manifest digest",
            join(
                [
                    short(a["manifest_digest"]),
                    " ",
                    state_badge("pass" if a["manifest_approved"] else "fail"),
                ]
            ),
        ),
        (
            "Prompt",
            "no prompt"
            if a["prompt_sha256"] is None
            else join(
                [
                    f"{a['prompt_version']} ",
                    short(a["prompt_sha256"]),
                    " ",
                    state_badge("pass" if a["prompt_approved"] else "fail"),
                ]
            ),
        ),
        ("May it run now?", join([state_badge(a["state"]), " ", a["refused_because"] or ""])),
    ]
    stop_rows = (
        (
            s["id"],
            s["stopped_by"],
            s["stopped_at"],
            "cleared in a reviewed approval" if s["cleared"] else "in force",
        )
        for s in a["stops"]
    )
    eval_rows = (
        (
            e["check"],
            e["version"],
            state_badge(e["state"]),
            "yes" if e["current"] else "no: an older manifest",
        )
        for e in a["evals"]
    )
    body = join(
        [
            tag("dl", *(tag("div", tag("dt", k), tag("dd", v)) for k, v in facts), class_="facts"),
            tag("h2", "Stops"),
            tag(
                "p",
                "The owner stops it with ",
                tag(
                    "code",
                    f"python -m hall_monitor.kill_switch stop {a['id']} "
                    f"--by {a['owner']['id']} --reason …",
                ),
                ". It is refused from its next call; restarting it needs a reviewed approval. "
                "Stop reasons stay in the stop list.",
                class_="lead",
            ),
            table(("Stop", "By", "At", "State"), stop_rows, "Never stopped."),
            tag("h2", "Scored in"),
            table(
                ("Eval check", "Version scored", "Gate", "Current manifest?"),
                eval_rows,
                "No published eval scored this agent.",
            ),
        ]
    )
    return page(a["name"], "agents.html", body, root="../", demo=demo)


def _case_rows(cases: list[dict]) -> Iterable[tuple]:
    for c in cases:
        yield (
            case_link(c["claim_id"]),
            state_badge(c["state"]),
            c["route"] or "—",
            c["recommended_next_step"] or "—",
            usd(c["covered_amount"]),
            c["decided_by"] or "—",
            "yes" if c["overrode_ai"] else "",
            usd(c["cost_usd"]),
        )


def _reasons_text(codes: list[str]) -> str:
    """Leave routing codes as the rules explain them; anything else as it was shown."""
    return "; ".join(LEAVE_REASONS[c][0] if c in LEAVE_REASONS else c for c in codes) or "—"


def queue_page(queue: list[dict], leave: list[dict], *, demo: bool) -> str:
    rows = (
        (
            case_link(c["claim_id"]),
            "; ".join(c["routing_reasons"]) or "—",
            c["recommended_next_step"] or "—",
            usd(c["covered_amount"]),
            c["rejected_decisions"] or "",
        )
        for c in queue
    )
    leave_rows = (
        (
            case_link(r["request_id"]),
            r["decided_by"] or "—",
            _reasons_text(r["reasons"]) if r["route"] == "hr_review" else "specialist sign-off",
            r["information_requested"] or "",
            r["refused_decisions"] or "",
        )
        for r in leave
    )
    body = join(
        [
            tag("h2", "Claims"),
            tag(
                "p",
                "Claims the routing rule sent to a person and nobody has decided. An adjuster "
                "opens one with ",
                tag("code", "python -m hall_monitor.review show CLAIM_ID"),
                " to see the evidence, and signs a decision within their authority.",
                class_="lead",
            ),
            table(
                (
                    "Claim",
                    "Why it came to a person",
                    "AI recommends",
                    "AI payout",
                    "Refused decisions",
                ),
                rows,
                "No claim is waiting for a person.",
            ),
            tag("h2", "Leave requests"),
            tag(
                "p",
                "Every leave request waits for a person. HR opens one with ",
                tag("code", "python -m hall_monitor.hr.review show REQUEST_ID"),
                " to read the message and the evidence, and signs a decision; the rules name the "
                "least senior role that may make it.",
                class_="lead",
            ),
            table(
                ("Request", "Needs at least", "Why", "Information asked for", "Refused decisions"),
                leave_rows,
                "No leave request is waiting for HR.",
            ),
        ]
    )
    return page("Review queue", "queue.html", body, demo=demo)


def cases_page(cases: list[dict], leave: list[dict], *, demo: bool) -> str:
    leave_rows = (
        (
            case_link(r["request_id"]),
            state_badge(r["state"]),
            r["route"] or "—",
            r["decided_by"] or "—",
            "—" if not r["decision"] else f"{r['decision']['outcome']} ({r['decision']['role']})",
            usd(r["cost_usd"]),
        )
        for r in leave
    )
    body = join(
        [
            tag("h2", "Claims"),
            table(
                (
                    "Claim",
                    "State",
                    "Route",
                    "AI recommends",
                    "AI payout",
                    "Decided by",
                    "Overrode AI",
                    "Cost",
                ),
                _case_rows(cases),
                "No claims in the audit log yet.",
            ),
            tag("h2", "Leave requests"),
            table(
                ("Request", "State", "Route", "Needs at least", "Decision", "Cost"),
                leave_rows,
                "No leave requests in the audit log yet.",
            ),
        ]
    )
    return page("Cases", "cases.html", body, demo=demo)


def case_page(trace: dict, *, demo: bool) -> str:
    if trace["case"] is None:
        body = tag("p", state_badge("fail"), " ", trace["problem"], ".", class_="banner alert")
        return page(f"Case {trace['claim_id']}", "cases.html", body, root="../", demo=demo)
    c = trace["case"]
    if trace.get("kind") == "leave":
        decision = c["decision"] or {}
        facts = [
            ("State", state_badge(c["state"])),
            ("Route", c["route"] or "—"),
            ("Needs at least", c["decided_by"] or "—"),
            ("Why", _reasons_text(c["reasons"]) if c["reasons"] else "none"),
            ("Stopped", c["stopped_reason"] or "no"),
            ("Text aimed at the AI", "yes" if c["injection_flagged"] else "no"),
            (
                "Decision",
                "not yet"
                if not decision
                else f"{decision.get('outcome')}, {decision.get('weeks')} weeks, by "
                f"{decision.get('decided_by')} ({decision.get('role')})",
            ),
            ("Information asked for", c["information_requested"]),
            ("Refused decisions", c["refused_decisions"]),
            ("Model cost", usd(c["cost_usd"])),
        ]
        return _trace_page(trace, facts, "request", "Leave request", demo)
    facts = [
        ("State", state_badge(c["state"])),
        ("Route", c["route"] or "—"),
        ("Routing reasons", "; ".join(c["routing_reasons"]) or "none"),
        ("Stopped", c["stopped_reason"] or "no"),
        ("AI recommends", f"{c['recommended_next_step'] or '—'}, {usd(c['covered_amount'])}"),
        ("Decided by", c["decided_by"] or "not yet"),
        (
            "Decision",
            f"{c['next_step'] or '—'}, {usd(c['payout_usd'])}" if c["decided_by"] else "—",
        ),
        ("Overrode the AI", "yes" if c["overrode_ai"] else "no"),
        ("Model cost", usd(c["cost_usd"])),
    ]
    return _trace_page(trace, facts, "claim", "Case", demo)


def _trace_page(trace: dict, facts: list, noun: str, title: str, demo: bool) -> str:
    rows = (
        (
            e["seq"],
            e["recorded_at"],
            tag("strong", e["event"]),
            tag("code", e["actor"]),
            tag("pre", json.dumps(e["payload"], indent=1, sort_keys=True)),
            short(e["entry_hash"]),
        )
        for e in trace["entries"]
    )
    body = join(
        [
            tag("dl", *(tag("div", tag("dt", k), tag("dd", v)) for k, v in facts), class_="facts"),
            tag(
                "p",
                f"Every entry the audit log holds for this {noun}, in order. The log keeps IDs, "
                "hashes, decision fields and costs; written reasons only as keyed hashes, and "
                "anything the code doesn't recognise is withheld. ",
                tag(
                    "a",
                    "This record as JSON",
                    href=f"../api/cases/{quote(trace['claim_id'], safe='')}.json",
                ),
                ".",
                class_="lead",
            ),
            table(("#", "Recorded", "Event", "Actor", "What was recorded", "Entry hash"), rows, ""),
        ]
    )
    return page(f"{title} {trace['claim_id']}", "cases.html", body, root="../", demo=demo)


def audit_page(result: dict, *, demo: bool) -> str:
    if not result["exists"]:
        verdict = tag("p", "No audit log yet.", class_="empty")
    elif result["ok"]:
        verdict = tag(
            "p", state_badge("pass"), f" All {result['entries']} entries verify.", class_="verdict"
        )
    else:
        verdict = join(
            [
                tag("p", state_badge("fail"), " The audit log does not verify.", class_="verdict"),
                tag("ul", *(tag("li", p) for p in result["problems"])),
            ]
        )
    head = result["head"]
    form = tag(
        "form",
        tag("label", "Anchored entry ", Safe('<input name="seq" inputmode="numeric" required>')),
        tag("label", "Entry hash ", Safe('<input name="hash" size="64" required>')),
        Safe('<button type="submit">Verify against this head</button>'),
        method="get",
        action="audit.html",
        class_="anchor",
    )
    body = join(
        [
            verdict,
            tag(
                "dl",
                tag(
                    "div",
                    tag("dt", "Head now"),
                    tag(
                        "dd",
                        "—"
                        if head is None
                        else join([f"entry {head['seq']} ", short(head["entry_hash"])]),
                    ),
                ),
                tag(
                    "div",
                    tag("dt", "Checked against"),
                    tag(
                        "dd",
                        "no anchor"
                        if result["anchor"] is None
                        else join(
                            [
                                f"entry {result['anchor']['seq']} ",
                                short(result["anchor"]["entry_hash"]),
                            ]
                        ),
                    ),
                ),
                tag(
                    "div",
                    tag("dt", "Evidence packs erased"),
                    tag("dd", len(result["evidence_erased"])),
                ),
                class_="facts",
            ),
            tag(
                "p",
                "Recomputing the chain shows any entry changed, removed or reordered. Someone "
                "able to rewrite the whole file could make a new chain that verifies, so check "
                "it against a head you copied out earlier (",
                tag("code", "python -m hall_monitor.audit head"),
                "). The panel can't anchor heads for you yet.",
                class_="lead",
            ),
            form,
        ]
    )
    return page("Audit integrity", "audit.html", body, demo=demo)


def evals_page(data: dict, *, demo: bool) -> str:
    if data["problem"]:
        body = tag("p", state_badge("fail"), " ", data["problem"], ".", class_="banner alert")
        return page("Evals and gate", "evals.html", body, demo=demo)
    sections = []
    for check in data["checks"]:
        rows = (
            (
                t["metric"],
                f"{t['rule']} {t['limit']}",
                "—" if t["value"] is None else number(t["value"]),
            )
            for t in check["thresholds"]
        )
        sections.append(
            tag(
                "section",
                tag("h2", check["name"]),
                tag(
                    "p",
                    state_badge(check["state"]),
                    " required" if check["required"] else " not required yet",
                    f" · run {check['run_at']}" if check["run_at"] else "",
                    " · ",
                    tag("code", check["results"]),
                ),
                tag("ul", *(tag("li", line) for line in check["failures"] + check["notes"]))
                if check["failures"] or check["notes"]
                else Safe(""),
                table(("Metric", "Threshold", "Published value"), rows, ""),
                class_="card wide",
            )
        )
    body = join(
        [
            tag(
                "p",
                state_badge("pass" if data["passed"] else "fail"),
                " The CI eval gate ",
                "passes" if data["passed"] else "fails",
                f". Results scored on agent versions no longer approved: {data['stale_results']}.",
                class_="verdict",
            ),
            tag("div", *sections, class_="grid"),
        ]
    )
    return page("Evals and gate", "evals.html", body, demo=demo)


def controls_page(data: dict, *, demo: bool) -> str:
    sections = []
    for c in data["controls"]:
        sections.append(
            tag(
                "details",
                tag(
                    "summary", tag("strong", c["id"]), " ", c["name"], " ", state_badge(c["status"])
                ),
                tag("p", c["what"]),
                tag("p", tag("strong", "Gaps: "), c["gaps"]) if c["gaps"] else Safe(""),
                tag(
                    "dl",
                    tag("div", tag("dt", "Code"), tag("dd", *(tag("code", r) for r in c["code"]))),
                    tag("div", tag("dt", "Tests"), tag("dd", f"{len(c['tests'])} tests")),
                    tag(
                        "div",
                        tag("dt", "Evidence"),
                        tag("dd", *([tag("code", r) for r in c["evidence"]] or ["none published"])),
                    ),
                    tag("div", tag("dt", "NIST AI RMF"), tag("dd", ", ".join(c["nist"]))),
                    tag(
                        "div",
                        tag("dt", "FINOS AIGF"),
                        tag("dd", ", ".join(c["finos"]["risks"] + c["finos"]["mitigations"])),
                    ),
                    class_="facts",
                ),
                class_="control",
            )
        )
    not_covered = table(
        ("Reference", "Why it isn't covered"),
        ((n["ref"], n.get("why") or n.get("reason") or "") for n in data["not_covered"]),
        "",
    )
    body = join(
        [
            tag(
                "p",
                "The published register: each control names its code, the tests that prove it, "
                "its evidence and its gaps. CI fails if any reference stops existing.",
                class_="lead",
            ),
            *sections,
            tag("h2", "Not covered"),
            not_covered,
        ]
    )
    return page("Controls register", "controls.html", body, demo=demo)


def not_found_page(what: str, *, root: str, demo: bool) -> str:
    return page("Not found", "", tag("p", f"No {what} by that ID."), root=root, demo=demo)
