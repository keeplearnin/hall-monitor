"""hall-monitor architecture and functionality diagrams, drawn as vector graphics.

    uv run --with reportlab python docs/pdf/make_diagrams.py

Writes docs/pdf/hall-monitor-architecture-and-functionality.pdf. Pages 1 to 3 are hand-written
from the code and docs as of AS_OF: when the architecture, budgets or statuses change, update
them and AS_OF together. Page 4 is read from controls/controls.yaml, so it can't drift from the
register. reportlab is used only here, so it isn't a project dependency.
"""

import sys
from pathlib import Path

import yaml
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

AS_OF = "13 Nov 2026"
REPO = Path(__file__).resolve().parents[2]
OUT = str(
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else REPO / "docs" / "pdf" / "hall-monitor-architecture-and-functionality.pdf"
)
W, H = landscape(letter)  # 792 x 612
M = 28

INK = HexColor("#1f2933")
MUTED = HexColor("#5b6670")
LINE = HexColor("#8a949e")
PAL = {
    "entry": (HexColor("#eef2f7"), HexColor("#7b8794")),
    "work": (HexColor("#e6f4f1"), HexColor("#2f8f83")),
    "core": (HexColor("#e8effa"), HexColor("#3a6ea5")),
    "gov": (HexColor("#fdf3e1"), HexColor("#c08a2e")),
    "rec": (HexColor("#f0f0f0"), HexColor("#6b6b6b")),
    "ext": (HexColor("#f1ecf8"), HexColor("#7a5ea8")),
    "person": (HexColor("#fde9e7"), HexColor("#b9524a")),
    "code": (HexColor("#ffffff"), HexColor("#3a6ea5")),
}
CHIP = {
    "built": (HexColor("#2f8f83"), "built"),
    "partial": (HexColor("#c08a2e"), "partial"),
    "built, results pending": (HexColor("#7b8794"), "pending"),
}
STATUS_WORDS = {"built, results pending": "built with live results pending"}


def T(y):  # top-based y to reportlab's bottom-based
    return H - y


def text(c, x, y, s, size=7.5, font="Helvetica", color=INK, anchor="l"):
    c.setFillColor(color)
    c.setFont(font, size)
    if anchor == "c":
        c.drawCentredString(x, T(y), s)
    elif anchor == "r":
        c.drawRightString(x, T(y), s)
    else:
        c.drawString(x, T(y), s)


def wrapped(c, x, y, w, s, size=7, font="Helvetica", color=INK, leading=None):
    """Draw wrapped text from top y; return the y after the last line."""
    leading = leading or size * 1.25
    for line in simpleSplit(s, font, size, w):
        text(c, x, y + size, line, size, font, color)
        y += leading
    return y


def box(c, x, y, w, h, kind, title=None, lines=(), size=6.8, title_size=8, radius=5, dash=False):
    fill, stroke = PAL[kind]
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.9)
    if dash:
        c.setDash(3, 2)
    c.roundRect(x, T(y + h), w, h, radius, stroke=1, fill=1)
    c.setDash()
    cy = y + 5
    if title:
        cy = wrapped(c, x + 6, cy, w - 12, title, title_size, "Helvetica-Bold", INK)
        cy += 1.5
    for ln in lines:
        bold = ln.startswith("**")
        ln = ln.replace("**", "")
        cy = wrapped(
            c,
            x + 6,
            cy,
            w - 12,
            ln,
            size,
            "Helvetica-Bold" if bold else "Helvetica",
            INK if bold else MUTED,
        )
    return cy


def band(c, x, y, w, h, kind, label):
    fill, stroke = PAL[kind]
    c.setStrokeColor(stroke)
    c.setLineWidth(0.6)
    c.setDash(2, 2)
    c.setFillColor(white)
    c.roundRect(x, T(y + h), w, h, 7, stroke=1, fill=0)
    c.setDash()
    text(c, x + 7, y + 11, label.upper(), 6.5, "Helvetica-Bold", stroke)


def arrow(
    c, x1, y1, x2, y2, label=None, color=LINE, lx=None, ly=None, width=0.9, dash=False, size=6
):
    import math

    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(width)
    if dash:
        c.setDash(3, 2)
    c.line(x1, T(y1), x2, T(y2))
    c.setDash()
    ang = math.atan2(T(y2) - T(y1), x2 - x1)
    a = 5
    p = c.beginPath()
    p.moveTo(x2, T(y2))
    p.lineTo(x2 - a * math.cos(ang - 0.4), T(y2) - a * math.sin(ang - 0.4))
    p.lineTo(x2 - a * math.cos(ang + 0.4), T(y2) - a * math.sin(ang + 0.4))
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    if label:
        lx = (x1 + x2) / 2 if lx is None else lx
        ly = (y1 + y2) / 2 - 3 if ly is None else ly
        tw = c.stringWidth(label, "Helvetica", size)
        c.setFillColor(white)
        c.rect(lx - tw / 2 - 2, T(ly + 2), tw + 4, size + 3, stroke=0, fill=1)
        text(c, lx, ly, label, size, "Helvetica", MUTED, "c")


def elbow(c, pts, label=None, color=LINE, dash=False):
    """Polyline through pts with an arrowhead on the last segment."""
    c.setStrokeColor(color)
    c.setLineWidth(0.9)
    if dash:
        c.setDash(3, 2)
    for (x1, y1), (x2, y2) in zip(pts[:-2], pts[1:-1], strict=False):
        c.line(x1, T(y1), x2, T(y2))
    c.setDash()
    arrow(c, *pts[-2], *pts[-1], color=color, dash=dash)
    if label:
        (x1, y1), (x2, y2) = pts[0], pts[1]
        text(c, x1 + 3, y1 - 3, label, 6, "Helvetica", MUTED)


def header(c, n, title, sub):
    text(c, M, 34, "HALL-MONITOR", 7, "Helvetica-Bold", PAL["gov"][1])
    text(c, M + 62, 34, f"·  {n} of 4", 7, "Helvetica", MUTED)
    text(c, M, 54, title, 17, "Helvetica-Bold", INK)
    wrapped(c, M, 60, W - 2 * M, sub, 8.2, "Helvetica", MUTED)


def footer(c):
    s = (
        "Reference implementation, not a production system. Claims, policies and people are "
        "fictional; the complaint desk reads 500 real public CFPB complaints, minimised. "
        f"As of {AS_OF}, from github.com/keeplearnin/hall-monitor."
    )
    wrapped(c, M, H - 24, W - 2 * M, s, 6, "Helvetica", MUTED)


def legend(c, x, y, items):
    for kind, label in items:
        fill, stroke = PAL[kind]
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.roundRect(x, T(y + 7), 10, 7, 1.5, stroke=1, fill=1)
        text(c, x + 13, y + 6, label, 6.3, "Helvetica", MUTED)
        x += 18 + c.stringWidth(label, "Helvetica", 6.3) + 8


# ---------------------------------------------------------------------------------------------
# Page 1: system architecture
# ---------------------------------------------------------------------------------------------


def page_architecture(c):
    header(
        c,
        1,
        "System architecture",
        "Governance sits in the call path, not inside the model. Every model call goes through "
        "one function that checks the agent's approved hall pass and the kill switch, and the "
        "workflow's loop checks the case budget before every request; tools go through a "
        "gateway; routing is plain code; every step lands in a hash-chained audit log.",
    )
    legend(
        c,
        M,
        84,
        [
            ("entry", "entry points"),
            ("work", "workflows and agents"),
            ("core", "shared runtime core"),
            ("gov", "governance plane"),
            ("rec", "records"),
            ("ext", "outside the system"),
            ("person", "people"),
        ],
    )

    top = 112
    # Entry points
    band(c, M, top, 124, 420, "entry", "Entry points")
    ex, ew = M + 6, 112
    y = top + 18
    entries = [
        (
            "Live CLIs",
            [
                "hall_monitor.run (claims)",
                "complaints.run --live",
                "hr.run",
                "all pass the spend ledger",
            ],
        ),
        ("Local forms", ["complaints.submit", "hr.submit", "loopback only; token + origin check"]),
        ("CFPB ingest", ["complaints.ingest pull", "read-only; snapshots pinned by hash"]),
        (
            "Control panel",
            ["panel serve: read-only views of agents, queue, traces, audit, evals, desk"],
        ),
        (
            "Offline checks",
            ["CI eval gate, red-team arena, failure drills, kill-switch drill (scripted)"],
        ),
    ]
    for t_, ls in entries:
        box(c, ex, y, ew, 74, "entry", t_, ls)
        y += 80

    # Workflows
    wx, ww = M + 132, 330
    band(
        c,
        wx,
        top,
        ww,
        258,
        "work",
        "Workflows: a supervisor agent picks the steps, code holds the boundaries",
    )
    cw = (ww - 24) / 3
    flows = [
        (
            "Claims triage",
            [
                "**claims_supervisor (Haiku)**",
                "intake (Haiku)",
                "coverage_check (Sonnet)",
                "risk_fraud (Haiku)",
                "critic (Sonnet), sends work back",
                "recommendation (Sonnet)",
                "**code: policy gate, route_claim**",
                "approval gate: adjuster signs",
            ],
        ),
        (
            "HR leave (FMLA)",
            [
                "**leave_supervisor (Haiku)**",
                "leave_intake (Haiku), no free text",
                "**code: eligibility and entitlement rules**",
                "leave_explainer (Haiku), advisory",
                "**code: names the role who decides**",
                "HR sign-off, signed in that role",
            ],
        ),
        (
            "Complaint desk (CFPB)",
            [
                "**complaint_supervisor (Haiku)**",
                "complaint_triage (Haiku), no tools",
                "**code: routing on CFPB labels; AI can only add review**",
                "drafts that pass the check kept, never sent",
                "every complaint to a person",
            ],
        ),
    ]
    for i, (t_, ls) in enumerate(flows):
        box(c, wx + 6 + i * (cw + 6), top + 18, cw, 136, "work", t_, ls, size=6.4)
    cy = top + 162
    box(
        c,
        wx + 6,
        cy,
        ww - 12,
        44,
        "core",
        "run_agent(): every model call",
        [
            "hall pass + prompt-hash check, data-class check, kill switch on every call; case "
            "budget between tool rounds (the loop checks it before each agent); typed output only; "
            "call record with tokens (cost computed from them)"
        ],
    )
    hw = (ww - 18) / 2
    box(
        c,
        wx + 6,
        cy + 50,
        hw,
        40,
        "core",
        "Tool gateway",
        [
            "claims tools allow-listed per agent, scoped to the claim: policy_lookup, "
            "claims_history; cfpb_complaint_search for the desk's ingest only"
        ],
        size=6.2,
    )
    box(
        c,
        wx + 12 + hw,
        cy + 50,
        hw,
        40,
        "core",
        "Egress guard (desk)",
        ["only GET to CFPB and POST to the messages API; no sending tools; CI no-send check"],
        size=6.2,
    )

    # Records
    ry = top + 270
    band(c, wx, ry, ww, 150, "rec", "Records")
    box(
        c,
        wx + 6,
        ry + 18,
        hw,
        60,
        "rec",
        "Audit log",
        [
            "SQLite, append-only, hash-chained: IDs, keyed hashes, codes, costs; never case text; "
            "verify + anchor"
        ],
        size=6.2,
    )
    box(
        c,
        wx + 12 + hw,
        ry + 18,
        hw,
        60,
        "rec",
        "Evidence store",
        ["evidence packs and drafts stored by hash, checked before any decision; erasable"],
        size=6.2,
    )
    box(
        c,
        wx + 6,
        ry + 84,
        hw,
        58,
        "rec",
        "Workflow checkpoints",
        ["LangGraph SQLite saver: a claim waits for its adjuster; the log wins over it"],
        size=6.2,
    )
    box(
        c,
        wx + 12 + hw,
        ry + 84,
        hw,
        58,
        "rec",
        "Traces (optional)",
        ["Langfuse spans: IDs, hashes, tokens; no inputs or outputs"],
        size=6.2,
    )

    # Outside
    xx, xw = wx + ww + 10, 112
    band(c, xx, top, xw, 420, "ext", "Outside")
    box(
        c,
        xx + 6,
        top + 18,
        xw - 12,
        136,
        "person",
        "People decide",
        [
            "Adjusters: denials, over $2,500, fraud flags, critic changes, failures",
            "HR staff: the role the rules name",
            "Desk staff: every complaint (specialists for flagged ones)",
            "Owners: stop agents, approve changes",
        ],
        size=6.2,
    )
    box(
        c,
        xx + 6,
        top + 158,
        xw - 12,
        60,
        "ext",
        "Claude API",
        [
            "Haiku 5.5, Sonnet 5.5, as each pass names. Timeouts: claims, HR 60 s x 2 tries; "
            "desk 120 s x 1"
        ],
        size=6.1,
    )
    box(
        c,
        xx + 6,
        top + 222,
        xw - 12,
        36,
        "ext",
        "CFPB complaint API",
        ["public, keyless; GET only"],
        size=6.1,
    )
    box(
        c,
        xx + 6,
        top + 300,
        xw - 12,
        70,
        "ext",
        "GitHub CI",
        ["tests, ruff, approvals check, eval gate, no-send check on every PR"],
        size=6.2,
    )

    # Governance plane
    gx = xx + xw + 10
    gw = W - M - gx
    band(c, gx, top, gw, 420, "gov", "Governance plane")
    g = [
        (
            "Registry + approvals",
            [
                "a hall pass per agent: owner, risk tier, model, tools, data classes",
                "approvals.yaml pins each manifest digest and prompt hash",
            ],
        ),
        (
            "Kill switch",
            [
                "stops.yaml: an owner stops an agent at once; read on every call; restart needs an "
                "approval"
            ],
        ),
        (
            "Budgets + spend ledger",
            [
                "budgets.yaml: case budget, daily and monthly caps, prices; approved by digest",
                "var/spend.sqlite holds each live case's reservation",
            ],
        ),
        (
            "Owners and roles",
            [
                "owners, adjusters with authority limits, HR staff with roles",
                "Ed25519 signed decisions",
            ],
        ),
    ]
    y = top + 18
    for t_, ls in g:
        box(c, gx + 6, y, gw - 12, 92, "gov", t_, ls, size=6.4)
        y += 98

    # Arrows (short and direct)
    arrow(c, M + 124, top + 150, wx, top + 150, None)
    text(c, M + 126, top + 145, "a case", 6, "Helvetica-Oblique", MUTED)
    arrow(c, wx + ww / 2, top + 154, wx + ww / 2, cy, None)
    arrow(c, wx + ww - 6, cy + 22, xx + 6, cy + 22, None, color=PAL["ext"][1])
    arrow(c, wx + ww - 6, cy + 70, xx + 6, cy + 70, None, color=PAL["ext"][1])
    arrow(c, wx + ww - 6, top + 86, xx + 6, top + 86, None, color=PAL["person"][1])
    arrow(c, wx + ww / 2, cy + 90, wx + ww / 2, ry + 18, None)
    text(c, wx + ww / 2 + 4, cy + 100, "every step", 6, "Helvetica-Oblique", MUTED)
    # governance -> core: routed above the bands
    elbow(
        c,
        [(gx + gw / 2, top), (gx + gw / 2, top - 6), (wx + ww - 40, top - 6), (wx + ww - 40, top)],
        color=PAL["gov"][1],
    )
    text(c, xx + 4, top - 9, "checked on every call", 6, "Helvetica-Oblique", PAL["gov"][1])
    # governance events -> audit log: routed below the bands
    elbow(
        c,
        [
            (gx + gw / 2, top + 420),
            (gx + gw / 2, top + 428),
            (wx + hw / 2, top + 428),
            (wx + hw / 2, ry + 150),
        ],
        color=PAL["rec"][1],
        dash=True,
    )
    text(
        c,
        xx + 4,
        top + 437,
        "stops, approvals and refusals are logged too",
        6,
        "Helvetica-Oblique",
        MUTED,
    )
    footer(c)


# ---------------------------------------------------------------------------------------------
# Page 2: how one case moves
# ---------------------------------------------------------------------------------------------


def page_flow(c):
    header(
        c,
        2,
        "How one case moves (the same path in all three workflows)",
        "Code checks the case before any model sees it, reserves its budget, then lets the "
        "supervisor choose one step at a time. Code checks every choice, and code alone routes "
        "the case. Anything that fails, stops or runs out of budget goes to a person; nothing "
        "invents a value to keep going.",
    )
    y0 = 96
    bw, bh, gap = 112, 84, 14
    xs = [M + i * (bw + gap) for i in range(6)]
    steps = [
        (
            "1  Case arrives",
            "work",
            ["a claim and its documents, a leave message, or a CFPB complaint"],
        ),
        (
            "2  Code checks first",
            "code",
            ["identifiers redacted", "text aimed at the AI flagged (sends it to a person later)"],
        ),
        (
            "3  Spend gate",
            "gov",
            [
                "budgets.yaml approved?",
                "live: reserve the case budget against the daily and monthly caps",
            ],
        ),
        ("4  Supervisor loop", "core", ["one step at a time, inside code's bounds (below)"]),
        (
            "5  Routing in code",
            "code",
            [
                "required steps ran on the latest inputs?",
                "rules: amounts, fraud, eligibility, CFPB labels",
            ],
        ),
        (
            "6  A person decides",
            "person",
            ["signed against the evidence pack, within their authority or role"],
        ),
    ]
    for (t_, kind, ls), x in zip(steps, xs, strict=False):
        box(c, x, y0, bw, bh, kind, t_, ls, size=6.6, title_size=8.2)
    for x in xs[:-1]:
        arrow(c, x + bw, y0 + bh / 2, x + bw + gap, y0 + bh / 2)

    # refusal branch from spend gate
    gx = xs[2] + bw / 2
    elbow(
        c,
        [
            (gx, y0 + bh),
            (gx, y0 + bh + 22),
            (xs[5] + bw / 2 - 20, y0 + bh + 22),
            (xs[5] + bw / 2 - 20, y0 + bh),
        ],
        color=PAL["person"][1],
        dash=True,
    )
    text(
        c,
        gx + 4,
        y0 + bh + 18,
        "over a cap, budgets unapproved or ledger down: to a person with no model call",
        6.2,
        "Helvetica-Oblique",
        PAL["person"][1],
    )

    # auto branch (claims only)
    rx = xs[4] + bw / 2
    box(
        c,
        xs[4] + 4,
        y0 + bh + 34,
        bw - 8,
        36,
        "work",
        "auto (claims only)",
        ["small, clean, every check passed"],
        size=6,
        title_size=7,
    )
    arrow(c, rx, y0 + bh, rx, y0 + bh + 34)

    # Loop detail
    ly = y0 + bh + 82
    band(c, M, ly, W - 2 * M, 232, "core", "Inside step 4: the supervisor loop")
    lw, lh = 122, 74
    lx = [M + 14 + i * (lw + 26) for i in range(5)]
    loop = [
        (
            "a  Budget left?",
            "gov",
            [
                "steps before every turn; cost and time before every model request, tool rounds "
                "included"
            ],
        ),
        (
            "b  Kill switch",
            "gov",
            [
                "re-check every agent the case has used; a stop ends the case at this step "
                "(HR's note agent: the note is dropped instead)"
            ],
        ),
        (
            "c  Supervisor chooses",
            "work",
            [
                "one action from a closed list",
                "sees codes and amounts only, never case text",
                "no route field",
            ],
        ),
        (
            "d  Code checks the choice",
            "code",
            [
                "inputs ready? a repeat on unchanged inputs? every raised stage revised?",
                "refused: logged, no run",
            ],
        ),
        (
            "e  Run the step",
            "core",
            [
                "specialist via run_agent(): hall pass, typed output, call record",
                "log supervisor_step (codes only)",
            ],
        ),
    ]
    for (t_, kind, ls), x in zip(loop, lx, strict=False):
        box(c, x, ly + 22, lw, lh, kind, t_, ls, size=6.4, title_size=7.8)
    for x in lx[:-1]:
        arrow(c, x + lw, ly + 22 + lh / 2, x + lw + 26, ly + 22 + lh / 2)
    # back edge e -> a
    elbow(
        c,
        [
            (lx[4] + lw / 2, ly + 22 + lh),
            (lx[4] + lw / 2, ly + 22 + lh + 18),
            (lx[0] + lw / 2, ly + 22 + lh + 18),
            (lx[0] + lw / 2, ly + 22 + lh),
        ],
    )
    text(c, lx[2] - 10, ly + 22 + lh + 14, "next turn", 6.3, "Helvetica-Oblique", MUTED)
    # refused -> back
    elbow(
        c,
        [
            (lx[3] + lw / 2, ly + 22),
            (lx[3] + lw / 2, ly + 14),
            (lx[2] + lw / 2 + 10, ly + 14),
            (lx[2] + lw / 2 + 10, ly + 22),
        ],
        color=PAL["person"][1],
        dash=True,
    )
    text(
        c,
        lx[2] + lw / 2 + 16,
        ly + 11,
        "refused (3 refusals: to a person)",
        6,
        "Helvetica-Oblique",
        PAL["person"][1],
    )

    # exits
    ey = ly + 22 + lh + 34
    ew = (W - 2 * M - 28 - 24) / 3
    exits = [
        (
            "finish",
            "work",
            ["a request, not a decision: code checks the required evidence, then routes"],
        ),
        ("hand off", "person", ["always to a person; adds a reason, never removes one"]),
        (
            "stopped",
            "person",
            ["budget out, a stop, a failure or a refusal loop: to a person; nothing invented"],
        ),
    ]
    for i, (t_, kind, ls) in enumerate(exits):
        box(
            c, M + 14 + i * (ew + 12), ey, ew, 40, kind, "exit: " + t_, ls, size=6.4, title_size=7.6
        )
    yy = wrapped(
        c,
        M + 14,
        ey + 50,
        W - 2 * M - 28,
        (
            "The supervisor can only add review. Offline, with hostile and broken scripted "
            "supervisors "
            "on every test case, no claim went through automatically that the fixed order sent to "
            "a "
            "person, and no complaint or leave request got less review. No supervisor has run on "
            "the "
            "live model yet."
        ),
        7,
        "Helvetica-Oblique",
        MUTED,
    )
    wrapped(
        c,
        M + 14,
        yy + 2,
        W - 2 * M - 28,
        (
            "At the end: the case's spend settles in the ledger (an interrupted or unpriced case "
            "stays "
            "reserved in full), and every step is in the audit log."
        ),
        7,
        "Helvetica-Oblique",
        MUTED,
    )
    footer(c)


# ---------------------------------------------------------------------------------------------
# Page 3: the three workflows side by side
# ---------------------------------------------------------------------------------------------


def page_workflows(c):
    header(
        c,
        3,
        "Functionality: three workflows on one set of controls",
        "Each row is the same question asked of each workflow. Agents prepare; code sets the "
        "floor; people decide. Status is what has been shown, not what is planned.",
    )
    rows = [
        (
            "Input",
            [
                "Travel-insurance claim: narrative, receipts, doctors' letters (synthetic)",
                "Employee's leave message (synthetic), employee and company records",
                (
                    "Real public CFPB complaints (500 pinned, minimised), or a typed-in one from "
                    "the "
                    "form"
                ),
            ],
        ),
        (
            "Agents (model)",
            [
                (
                    "supervisor, intake, risk_fraud (Haiku); coverage_check, critic, "
                    "recommendation "
                    "(Sonnet)"
                ),
                "supervisor, leave_intake, leave_explainer (all Haiku)",
                "supervisor, complaint_triage (both Haiku)",
            ],
        ),
        (
            "What the AI does",
            [
                "extracts, checks the policy, spots fraud signs, double-checks, recommends",
                (
                    "extracts dates, reason and yes/no facts (no free-text field); writes an "
                    "advisory "
                    "note"
                ),
                "summary, category, urgency, concerns, a draft acknowledgement",
            ],
        ),
        (
            "What code decides",
            [
                "policy exists; amounts in the documents; auto only if small and clean",
                "FMLA eligibility and entitlement; the role who must decide",
                (
                    "specialist review at high urgency for fraud, identity theft, foreclosure, "
                    "discrimination, Older American, Servicemember"
                ),
            ],
        ),
        (
            "Who decides",
            [
                (
                    "adjuster within their authority: denials, over $2,500, fraud flags, critic "
                    "changes, injection, failures"
                ),
                "HR staff at the role the rules name; denying needs a manager or above",
                "a person works every complaint; nothing resolved automatically",
            ],
        ),
        (
            "Hard rules",
            [
                "lookups only for this claim's policy; decisions signed against the evidence",
                "the rules run on the first intake; no shopping for an answer",
                (
                    "built never to reply to or contact anyone: one allow-listed outbound path, no "
                    "sending tools; drafts that pass the check kept, never sent"
                ),
            ],
        ),
        (
            "Approved budget",
            [
                "16 steps, $0.25, 300 s a case; $5 a day, $50 a month",
                "8 steps, $0.02, 120 s a case; $1 a day, $10 a month",
                "6 steps, $0.02, 120 s a case; $1 a day, $10 a month",
            ],
        ),
        (
            "Status",
            [
                "built; live scores from the older fixed order; supervisor scripted only",
                "partial: rules unreviewed by HR or a lawyer; agents not run live",
                (
                    "partial: scripted runs only; form proposed; 8 of 18 red-team attacks got harm "
                    "through"
                ),
            ],
        ),
    ]
    heads = [
        ("Claims triage", "work"),
        ("HR leave (FMLA)", "work"),
        ("Complaint desk (CFPB)", "work"),
    ]
    x0, y0 = M, 96
    lw = 104
    cw = (W - 2 * M - lw - 12) / 3
    for i, (h_, kind) in enumerate(heads):
        fill, stroke = PAL[kind]
        c.setFillColor(stroke)
        c.roundRect(x0 + lw + 6 + i * (cw + 3), T(y0 + 20), cw, 20, 4, stroke=0, fill=1)
        text(c, x0 + lw + 6 + i * (cw + 3) + cw / 2, y0 + 14, h_, 9, "Helvetica-Bold", white, "c")
    y = y0 + 26
    rh = 52
    for r, (label, cells) in enumerate(rows):
        kind = (
            "gov"
            if label in ("Approved budget",)
            else (
                "person"
                if label == "Who decides"
                else ("code" if label in ("What code decides", "Hard rules") else "entry")
            )
        )
        fill, stroke = PAL[kind]
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(0.6)
        c.roundRect(x0, T(y + rh - 4), lw, rh - 4, 4, stroke=1, fill=1)
        wrapped(c, x0 + 6, y + 6, lw - 12, label, 8, "Helvetica-Bold", INK)
        for i, cell in enumerate(cells):
            cx = x0 + lw + 6 + i * (cw + 3)
            c.setFillColor(HexColor("#fbfcfd") if r % 2 else white)
            c.setStrokeColor(HexColor("#d5dbe1"))
            c.roundRect(cx, T(y + rh - 4), cw, rh - 4, 4, stroke=1, fill=1)
            wrapped(c, cx + 6, y + 6, cw - 12, cell, 7, "Helvetica", INK)
        y += rh
    footer(c)


# ---------------------------------------------------------------------------------------------
# Page 4: controls register at a glance (read from controls/controls.yaml)
# ---------------------------------------------------------------------------------------------

GROUPS = [
    ("Who may run, and on what", ["HM-01", "HM-02", "HM-03", "HM-10", "HM-11", "HM-19", "HM-29"]),
    ("Decisions stay with people", ["HM-04", "HM-05", "HM-07", "HM-08", "HM-21", "HM-23", "HM-27"]),
    ("Agents choose steps, code holds bounds", ["HM-32", "HM-34", "HM-16"]),
    ("Records and evidence", ["HM-06", "HM-13", "HM-20"]),
    (
        "Testing, attacks, failures, fairness",
        ["HM-09", "HM-14", "HM-15", "HM-30", "HM-31", "HM-12", "HM-17", "HM-18"],
    ),
    ("Workflow-specific", ["HM-22", "HM-24", "HM-25", "HM-26", "HM-28", "HM-33"]),
]


def page_controls(c):
    reg = yaml.safe_load((REPO / "controls" / "controls.yaml").read_text())
    by_id = {x["id"]: x for x in reg["controls"]}
    placed = {i for _, ids in GROUPS for i in ids}
    missing = sorted(set(by_id) - placed)
    groups = GROUPS + ([("Other", missing)] if missing else [])
    counts = {}
    for x in by_id.values():
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    header(
        c,
        4,
        f"Governance controls at a glance ({len(by_id)} in the register)",
        "Read from controls/controls.yaml, where each control lists its code, tests, evidence, "
        "NIST AI RMF and FINOS AIGF mapping, and its gaps. "
        + ", ".join(f"{n} {STATUS_WORDS.get(s, s)}" for s, n in sorted(counts.items()))
        + ". Partial means built with stated limits, usually no live run yet.",
    )
    cols = 3
    gw = (W - 2 * M - (cols - 1) * 10) / cols
    y_col = [96] * cols
    for gname, ids in groups:
        col = y_col.index(min(y_col))
        x = M + col * (gw + 10)
        y = y_col[col]
        h = 22 + 19 * len(ids)
        band(c, x, y, gw, h, "core", gname)
        yy = y + 18
        for cid in ids:
            ctl = by_id[cid]
            st = ctl["status"]
            color, word = CHIP.get(st, (HexColor("#7b8794"), st))
            c.setFillColor(color)
            c.roundRect(x + 6, T(yy + 12), 40, 12, 2, stroke=0, fill=1)
            text(c, x + 26, yy + 9, word, 6.6, "Helvetica-Bold", white, "c")
            text(c, x + 51, yy + 9.5, cid, 7.8, "Helvetica-Bold", INK)
            name = ctl["name"]
            maxw = gw - 90
            while c.stringWidth(name, "Helvetica", 7.6) > maxw:
                name = name[:-2]
            if name != ctl["name"]:
                name = name.rstrip(" ,(") + "..."
            text(c, x + 84, yy + 9.5, name, 7.6, "Helvetica", INK)
            yy += 19
        y_col[col] = y + h + 10
    footer(c)


c = canvas.Canvas(OUT, pagesize=(W, H))
c.setTitle("hall-monitor: architecture and functionality")
c.setAuthor("hall-monitor")
c.setSubject(f"Architecture and functionality diagrams, as of {AS_OF}")
for page in (page_architecture, page_flow, page_workflows, page_controls):
    page(c)
    c.showPage()
c.save()
print(OUT)
