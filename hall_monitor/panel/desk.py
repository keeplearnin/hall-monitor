"""The complaint desk's page in the control panel: read from the audit log alone.

Shows the queue (what went to specialist review and why, in the code's own words), urgency,
cost per complaint, the pulls from CFPB (counts, retries, status), and the no-send control's
status: the static check run now against this checkout, egress refusals logged, and whether
every stored draft is marked never sent. Never the summary, the draft's text, or anything from
CFPB beyond IDs, codes and counts.
"""

from __future__ import annotations

from collections import Counter

from hall_monitor import no_send
from hall_monitor.complaints.routing import REASONS
from hall_monitor.panel import views
from hall_monitor.panel.pages import badge, page, table, tag, usd
from hall_monitor.panel.views import DESK_EVENTS

QUEUES = ("specialist_review", "standard_queue")
# Pull summary fields the page shows: counts and codes only.
PULL_FIELDS = (
    "pull_id",
    "status",
    "error",
    "started_at",
    "seconds",
    "fetched",
    "new",
    "updated",
    "unchanged",
    "quarantined",
    "requests",
    "retries",
)


def desk_ids(entries: list[dict]) -> set[str]:
    """Scopes the desk wrote to: complaints (cfpb-...), pulls and egress refusals."""
    return {e["claim_id"] for e in entries if e["event"] in DESK_EVENTS}


def _int(value: object) -> int | None:
    return value if type(value) is int else None


def complaint_desk(src: views.Sources) -> dict:
    entries, problem = views._entries(src)
    ids = desk_ids(entries)
    mine = [e for e in entries if e["claim_id"] in ids]
    routed = [e for e in mine if e["event"] == "complaint_routed"]
    complaints = []
    reasons: Counter[str] = Counter()
    for e in routed:
        p = e["payload"] if isinstance(e["payload"], dict) else {}
        codes = [r for r in p.get("reasons", []) if r in REASONS]
        reasons.update(codes)
        complaints.append(
            {
                "complaint_id": e["claim_id"],
                "queue": p.get("queue") if p.get("queue") in QUEUES else None,
                "urgency": p.get("urgency")
                if p.get("urgency") in ("low", "normal", "high")
                else None,
                "reasons": codes,
                "explained": [REASONS[r] for r in codes],
                "cost_usd": views._number(p.get("cost_usd")),
                "draft_kept": isinstance(p.get("draft_sha256"), str),
                "agent_failed": "agent_failed" in codes,
            }
        )
    complaints.sort(key=lambda c: (c["queue"] != "specialist_review", c["complaint_id"]))
    pulls = []
    for e in mine:
        if e["event"] == "complaint_pull" and isinstance(e["payload"], dict):
            p = e["payload"]
            pulls.append(
                {
                    k: (
                        p.get(k)
                        if k in ("pull_id", "status", "error", "started_at")
                        else views._number(p.get(k))
                    )
                    for k in PULL_FIELDS
                }
            )
    pulls = sorted(pulls, key=lambda p: str(p["started_at"]), reverse=True)[:20]
    drafts = [e for e in mine if e["event"] == "draft_stored"]
    findings = no_send.scan()
    costs = [c["cost_usd"] for c in complaints if c["cost_usd"] is not None]
    return {
        "problem": problem,
        "complaints": complaints,
        "queues": dict(Counter(c["queue"] for c in complaints)),
        "urgency": dict(Counter(c["urgency"] for c in complaints)),
        "reasons": [
            {"code": code, "explained": REASONS[code], "count": n}
            for code, n in reasons.most_common()
        ],
        "cost": {
            "total_usd": round(sum(costs), 6),
            "per_complaint_usd": round(sum(costs) / len(costs), 6) if costs else None,
        },
        "pulls": pulls,
        "quarantined": sum(e["event"] == "complaint_quarantined" for e in mine),
        "no_send": {
            "static_check_passes": not findings,
            "static_check_findings": len(findings),
            "egress_refusals": sum(e["event"] == "egress_refused" for e in mine),
            "drafts_stored": len(drafts),
            "drafts_not_marked_never_sent": sum(
                not (
                    isinstance(e["payload"], dict)
                    and e["payload"].get("never_sent") is True
                    and e["payload"].get("delivery") == "none"
                )
                for e in drafts
            ),
        },
    }


def complaints_page(data: dict, *, demo: bool) -> str:
    ns = data["no_send"]
    ok = ns["static_check_passes"] and ns["drafts_not_marked_never_sent"] == 0
    status = tag(
        "section",
        tag("h2", "No reply, no send (HM-25)"),
        tag(
            "p",
            badge("holding" if ok else "check failed", "ok" if ok else "stop"),
            " The system never replies to, contacts or sends anything to anyone.",
        ),
        table(
            ["Check", "Now"],
            [
                (
                    "Static check on this checkout",
                    "passes"
                    if ns["static_check_passes"]
                    else f"{ns['static_check_findings']} findings",
                ),
                ("Outbound requests refused and logged", ns["egress_refusals"]),
                ("Drafts kept (never sent)", ns["drafts_stored"]),
                ("Drafts not marked never sent", ns["drafts_not_marked_never_sent"]),
            ],
            "",
        ),
    )
    queues = data["queues"]
    summary = tag(
        "section",
        tag("h2", "Queue"),
        tag(
            "p",
            "Every complaint goes to a person. ",
            f"{queues.get('specialist_review', 0)} in specialist review, "
            f"{queues.get('standard_queue', 0)} in the standard queue. Cost: ",
            usd(data["cost"]["total_usd"]),
            " in all, ",
            usd(data["cost"]["per_complaint_usd"]),
            " a complaint.",
        ),
        table(
            ["Why it went to specialist review", "Code", "Complaints"],
            [(r["explained"], tag("code", r["code"]), r["count"]) for r in data["reasons"]],
            "No complaint has been triaged.",
        ),
    )
    rows = [
        (
            tag("code", c["complaint_id"]),
            badge(c["queue"] or "unknown", "warn" if c["queue"] == "specialist_review" else "ok"),
            c["urgency"] or "unknown",
            "; ".join(c["explained"]) or "none",
            usd(c["cost_usd"]),
        )
        for c in data["complaints"]
    ]
    listing = tag(
        "section",
        tag("h2", "Complaints"),
        table(["Complaint", "Queue", "Urgency", "Reasons", "Cost"], rows, "None yet."),
    )
    pull_rows = [
        (
            tag("code", p["pull_id"]),
            badge(str(p["status"]), "stop" if p["status"] == "failed" else "ok"),
            p["error"] or "",
            p["fetched"],
            p["new"],
            p["updated"],
            p["quarantined"],
            p["retries"],
            p["seconds"],
        )
        for p in data["pulls"]
    ]
    pulls = tag(
        "section",
        tag("h2", "Pulls from CFPB"),
        tag(
            "p",
            f"{data['quarantined']} records quarantined in all (field names and error types only).",
        ),
        table(
            [
                "Pull",
                "Status",
                "Error",
                "Fetched",
                "New",
                "Updated",
                "Quarantined",
                "Retries",
                "Seconds",
            ],
            pull_rows,
            "No pulls in this log. Evals and the demo run from the pinned snapshot.",
        ),
    )
    note = tag(
        "p",
        "Public CFPB complaints, prepared for a simulated company's complaint desk. A complaint is "
        "one consumer's account, published unverified; nothing here is a finding about a company, "
        "and no company is named. Summaries and drafts are withheld.",
        class_="banner",
    )
    problem = tag("p", data["problem"], class_="banner") if data["problem"] else ""
    return page(
        "Complaint desk",
        "complaints.html",
        tag("div", problem, note, status, summary, listing, pulls),
        demo=demo,
    )
