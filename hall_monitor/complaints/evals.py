"""Complaint desk evals on the pinned snapshot: deterministic, scored by code.

    uv run python -m hall_monitor.complaints.evals                  # scripted stand-in, free
    uv run python -m hall_monitor.complaints.evals --live --limit 50  # Claude API, ~$0.01

Every complaint in the pinned snapshot (data/cfpb/snapshots/) goes through the same pipeline as
the desk, into a throwaway audit log, and the results are scored by code:

- **Routing (gated).** Every complaint whose own fields call for specialist review (fraud,
  identity theft, foreclosure, discrimination, the Older American and Servicemember tags) got it,
  at high urgency; every untimely one reached specialist review; nothing raised an error.
- **No delivery path (gated).** Every stored draft is marked never sent and has only the draft's
  own fields.
- **Fairness (gated).** Counterfactual: the same complaint from another state and ZIP prefix
  must give the same code reasons and the same text to the agent, so it can't route
  differently. Group rates by ZIP region (the prefix's first digit) and by tag are reported, not
  gated: they reflect what people complain about where, not only how the desk treats them.
- **Partial ground truth (reported).** CFPB's published company response and timeliness: the
  share of complaints the company closed with monetary relief that the desk sent to specialist
  review or marked high urgency, and the same for those closed with an explanation. A closed
  complaint can still have been urgent, so this is a signal, not an answer key.

The scripted run measures the desk's code, not the model: its agent is a stand-in with fixed
rules. Results say which they are. A live run is made only when asked, with its cost first.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

from hall_monitor.complaints.drafts import DraftResponse
from hall_monitor.complaints.ingest import (
    PINNED_MANIFESTS,
    PINNED_NAME,
    PINNED_SNAPSHOTS,
    load_pinned,
)
from hall_monitor.complaints.models import Complaint, Tag
from hall_monitor.complaints.pipeline import triage_complaint
from hall_monitor.complaints.routing import code_reasons
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.complaints.triage_agent import AGENT_ID, Urgency, render_complaint
from hall_monitor.egress import audit_refusals, model_client
from hall_monitor.evals.meta import run_meta
from hall_monitor.governance.audit import AuditLog
from hall_monitor.registry import Registry

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evals" / "results"
SCRIPTED_DIR = RESULTS / "complaints-scripted"
LIVE_DIR = RESULTS / "complaints-live"
LABEL_KEY = ROOT / "data" / "cfpb" / "label_reasons.yaml"
MIN_GROUP = 20  # smallest group whose rate is reported


def load_label_key(path: Path = LABEL_KEY) -> dict[str, str]:
    """The hand-written answer key: CFPB label -> the reason it calls for."""
    labels = yaml.safe_load(path.read_text())["labels"]
    if not set(labels.values()) <= {"fraud", "identity_theft", "foreclosure", "discrimination"}:
        raise ValueError("the label key names a reason routing doesn't have")
    return labels


def expected_reasons(c: Complaint, key: dict[str, str]) -> set[str]:
    """What the answer key says this complaint needs: reasons from its labels, and its tags.
    Independent of the routing code's patterns."""
    found = {key[label] for label in (c.issue, c.sub_issue) if label in key}
    if Tag.OLDER_AMERICAN in c.tags:
        found.add("older_american")
    if Tag.SERVICEMEMBER in c.tags:
        found.add("servicemember")
    return found


def _counterfactuals(c: Complaint) -> list[Complaint]:
    return [
        c.model_copy(update={"state": state, "zip3": zip3})
        for state, zip3 in (("MS", "386"), ("CA", "941"), ("NY", "100"), (None, None))
    ]


def flips(c: Complaint) -> bool:
    """Would the same complaint from elsewhere be treated differently? True if the code's
    reasons change, or the agent would be sent different text."""
    reasons = code_reasons(c)
    text = render_complaint(c, c.narrative, c.company_public_response)
    return any(
        code_reasons(other) != reasons
        or render_complaint(other, other.narrative, other.company_public_response) != text
        for other in _counterfactuals(c)
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _groups(rows: list[dict], key) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for name in key(row):
            grouped[name].append(row)
    out = {}
    for name, members in sorted(grouped.items()):
        n = len(members)
        out[name] = {
            "n": n,
            "specialist_rate": _rate(sum(r["queue"] == "specialist_review" for r in members), n),
            "high_urgency_rate": _rate(sum(r["urgency"] == "high" for r in members), n),
        }
    return out


def _gap(groups: dict[str, dict], metric: str) -> float | None:
    values = [g[metric] for g in groups.values() if g["n"] >= MIN_GROUP and g[metric] is not None]
    return round(max(values) - min(values), 4) if len(values) >= 2 else None


def evaluate(complaints: list[Complaint], client, registry: Registry, mode: str) -> dict:
    key = load_label_key()
    rows, errors = [], 0
    drafts_with_delivery_path = drafts_kept = 0
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditLog(Path(tmp) / "eval-audit.sqlite")
        try:
            for c in complaints:
                try:
                    result = triage_complaint(c, registry=registry, client=client, audit=audit)
                except Exception as exc:  # scored as an error, never skipped
                    errors += 1
                    rows.append({"complaint_id": c.complaint_id, "error": type(exc).__name__})
                    continue
                if result.draft_sha256:
                    drafts_kept += 1
                    stored = audit.get_evidence(result.draft_sha256) or {}
                    fields = set(stored) - {"kind"}
                    if stored.get("never_sent") is not True or fields != set(
                        DraftResponse.model_fields
                    ):
                        drafts_with_delivery_path += 1
                rows.append(
                    {
                        "complaint_id": c.complaint_id,
                        "queue": result.route.queue,
                        "urgency": result.route.urgency.value,
                        "reasons": result.route.reasons,
                        "category": None if result.triage is None else result.triage.category.value,
                        "draft_kept": result.draft_sha256 is not None,
                        "draft_problems": result.draft_problems,
                        "cost_usd": result.cost_usd,
                        "zip_region": c.zip3[0] if c.zip3 else "unknown",
                        "tags": [t.value for t in c.tags] or ["none"],
                        "company_response": c.company_response,
                        "untimely": c.timely is False or c.company_response == "Untimely response",
                        # From the hand-written answer key, not the routing patterns.
                        "must_route": sorted(expected_reasons(c, key)),
                        "missing_reasons": sorted(
                            expected_reasons(c, key) - set(result.route.reasons)
                        ),
                    }
                )
            verified = audit.verify().ok
        finally:
            audit.close()

    scored = [r for r in rows if "error" not in r]
    must = [r for r in scored if r["must_route"]]
    untimely = [r for r in scored if r["untimely"]]
    relief = [r for r in scored if r["company_response"] == "Closed with monetary relief"]
    explained = [r for r in scored if r["company_response"] == "Closed with explanation"]

    def flagged(r: dict) -> bool:
        return r["queue"] == "specialist_review" or r["urgency"] == Urgency.HIGH.value

    by_region = _groups(scored, lambda r: [r["zip_region"]])
    by_tag = _groups(scored, lambda r: r["tags"])
    summary = {
        "complaints": len(complaints),
        "errors": errors,
        "agent_failed": sum("agent_failed" in r["reasons"] for r in scored),
        "audit_chain_verified": verified,
        "must_route_total": len(must),
        "must_route_missed": sum(r["queue"] != "specialist_review" for r in must),
        "must_route_below_high": sum(r["urgency"] != "high" for r in must),
        "must_route_reason_missing": sum(bool(r["missing_reasons"]) for r in must),
        "untimely_total": len(untimely),
        "untimely_to_specialist_rate": _rate(
            sum(r["queue"] == "specialist_review" for r in untimely), len(untimely)
        ),
        "specialist_share": _rate(
            sum(r["queue"] == "specialist_review" for r in scored), len(scored)
        ),
        "drafts_kept": drafts_kept,
        "drafts_dropped": sum(bool(r["draft_problems"]) for r in scored),
        "drafts_with_delivery_path": drafts_with_delivery_path,
        "counterfactual_flip_rate": _rate(sum(flips(c) for c in complaints), len(complaints)),
        "region_specialist_gap": _gap(by_region, "specialist_rate"),
        "tag_specialist_rate_older_american": by_tag.get(Tag.OLDER_AMERICAN.value, {}).get(
            "specialist_rate"
        ),
        "tag_specialist_rate_servicemember": by_tag.get(Tag.SERVICEMEMBER.value, {}).get(
            "specialist_rate"
        ),
        "monetary_relief_total": len(relief),
        "monetary_relief_flagged_rate": _rate(sum(flagged(r) for r in relief), len(relief)),
        "explanation_total": len(explained),
        "explanation_flagged_rate": _rate(sum(flagged(r) for r in explained), len(explained)),
        "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in scored), 6),
    }
    return {
        "summary": summary,
        "fairness": {"by_zip_region": by_region, "by_tag": by_tag, "min_group": MIN_GROUP},
        "runs": rows,
        "mode": mode,
    }


def report(data: dict) -> str:
    s, meta = data["summary"], data["meta"]
    lines = [
        f"# Complaint desk eval ({meta['mode']})",
        "",
        f"Snapshot `{meta['snapshot']}` ({s['complaints']} public CFPB complaints, manifest "
        f"`{meta['snapshot_manifest_sha256'][:12]}`), run {meta['run_at']}.",
        "",
    ]
    if meta["mode"] == "scripted":
        lines += [
            "**The agent here is a scripted stand-in with fixed rules, not the model.** This run "
            "measures the desk's code: routing, drafts, the audit log and the counterfactual "
            "check. It says nothing about the model's summaries, categories or urgency.",
            "",
        ]
    lines += ["| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in s.items()]
    lines += [
        "",
        "Group rates by ZIP region (first digit of the prefix) and by tag are in results.json. "
        "They are reported, not gated: a region's specialist rate reflects what is complained "
        "about there (more fraud, more mortgages), not only how the desk treats it. The "
        "counterfactual flip rate is the fairness check that is gated: routing doesn't read the "
        "state or ZIP prefix, and the agent is never sent them.",
        "",
        "Partial ground truth: CFPB's published company response. "
        f"{s['monetary_relief_flagged_rate']} of complaints closed with monetary relief were "
        f"flagged (specialist review or high urgency), against {s['explanation_flagged_rate']} "
        "of those closed with an explanation. A closed complaint can still have been urgent; "
        "this is a signal, not an answer key.",
    ]
    return "\n".join(lines) + "\n"


def run(mode: str, limit: int | None = None, out: Path | None = None) -> dict:
    complaints = load_pinned(PINNED_NAME)[:limit] if limit else load_pinned(PINNED_NAME)
    registry = Registry.from_dir()
    if mode == "scripted":
        client = scripted_client()
        data = evaluate(complaints, client, registry, mode)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            refusals = AuditLog(Path(tmp) / "egress.sqlite")
            client = model_client(on_refused=audit_refusals(refusals))
            data = evaluate(complaints, client, registry, mode)
            refusals.close()
    data["meta"] = run_meta(
        registry,
        [AGENT_ID],
        mode=mode,
        snapshot=PINNED_NAME,
        snapshot_manifest_sha256=PINNED_MANIFESTS[PINNED_NAME],
        snapshot_path=str((PINNED_SNAPSHOTS / PINNED_NAME).relative_to(ROOT)),
    )
    out = out or (SCRIPTED_DIR if mode == "scripted" else LIVE_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    (out / "report.md").write_text(report(data))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.complaints.evals")
    parser.add_argument("--live", action="store_true", help="call the Claude API (costs money)")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.live and not args.limit:
        parser.error("--live needs --limit")
    data = run("live" if args.live else "scripted", args.limit)
    print(json.dumps(data["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
