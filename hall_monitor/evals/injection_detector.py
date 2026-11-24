"""How the injection detector does on text written blind to it (plan step 2, offline half).

    uv run python -m hall_monitor.evals.injection_detector             # print the report
    uv run python -m hall_monitor.evals.injection_detector --publish   # also writes RESULTS_DIR

The detector (hall_monitor/gateway/injection.py) is a list of phrasings, so its own tests pass on
the phrases it was written from and say little about anything else (Review 46, M15). This eval
runs it on data/synthetic/injection/detector_set.yaml: attacks and benign look-alikes across
claims, complaints, HR leave and tool results, written by an author who hadn't read the
detector or the earlier attack sets. It reports, for each split, how many attacks the detector
flagged (recall) and how many benign texts it flagged (false alarms), with 95% Wilson intervals.

The held-out split is for measuring, never for tuning: a change to the detector is made against
the dev split, and the held-out numbers are the ones to quote. HELDOUT_SHA256 pins the held-out
cases, so changing them fails a test until the digest is updated and the data README's change
table names the new one.

Plain code, no model: free and deterministic, so it runs in CI. Whether a missed attack would
actually steer a model is the live half, which costs money and runs only when asked.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from hall_monitor.claims.data import DATA_DIR
from hall_monitor.evals.meta import run_meta
from hall_monitor.gateway import injection
from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.registry import Registry

ROOT = Path(__file__).resolve().parents[2]
SET_PATH = DATA_DIR / "injection" / "detector_set.yaml"
RESULTS_DIR = ROOT / "evals" / "results" / "injection-detector"
MODE = "offline-detector"

LABELS = ("attack", "benign")
SPLITS = ("dev", "heldout")
SURFACES = ("claim_narrative", "claim_attachment", "complaint", "leave_request", "tool_result")
FIELDS = {"id", "label", "family", "surface", "split", "text"}

# The held-out cases, pinned (see the module docstring); set when the set was first committed.
HELDOUT_SHA256 = "06f77aa28a00de1ea24e7b6d0dd544e8fea539fca474a76cb0155a510034096a"


class SetError(ValueError):
    pass


def load_cases(path: Path = SET_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise SetError(f"{path}: no cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != FIELDS:
            raise SetError(f"{path}: a case must have exactly {sorted(FIELDS)}: {case!r:.80}")
        if case["id"] in seen:
            raise SetError(f"{path}: duplicate id {case['id']}")
        seen.add(case["id"])
        for field, allowed in (("label", LABELS), ("split", SPLITS), ("surface", SURFACES)):
            if case[field] not in allowed:
                raise SetError(f"{case['id']}: {field} {case[field]!r} not in {allowed}")
        if not isinstance(case["text"], str) or not case["text"].strip():
            raise SetError(f"{case['id']}: empty text")
        if not isinstance(case["family"], str) or not case["family"]:
            raise SetError(f"{case['id']}: no family")
        prefix = {"attack": "atk-", "benign": "ben-"}[case["label"]]
        if not case["id"].startswith(prefix):
            raise SetError(f"{case['id']}: a {case['label']} case's id needs the prefix {prefix}")
    labels: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        labels[case["family"]].add(case["label"])
    mixed = sorted(f for f, seen in labels.items() if len(seen) > 1)
    if mixed:
        raise SetError(f"{path}: families with both labels: {', '.join(mixed)}")
    return cases


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def heldout_sha256(cases: list[dict]) -> str:
    return _digest(sorted((c for c in cases if c["split"] == "heldout"), key=lambda c: c["id"]))


def detector_sha256() -> str:
    """The detector's source: a result is current only for the detector it measured."""
    return hashlib.sha256(inspect.getsource(injection).encode()).hexdigest()


def wilson(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    """The 95% Wilson score interval for hits out of n (None when n is 0)."""
    if n == 0:
        return None
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 3) if n else None


def evaluate(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        signals = detect_in_texts([case["text"]])
        rows.append(
            {
                **{k: case[k] for k in ("id", "label", "family", "surface", "split")},
                "flagged": bool(signals),
                "signals": signals,
            }
        )

    summary: dict = {}
    for split in (*SPLITS, "all"):
        chosen = [r for r in rows if split == "all" or r["split"] == split]
        attacks = [r for r in chosen if r["label"] == "attack"]
        benign = [r for r in chosen if r["label"] == "benign"]
        caught = sum(r["flagged"] for r in attacks)
        alarms = sum(r["flagged"] for r in benign)
        prefix = "" if split == "all" else f"{split}_"
        summary[f"{prefix}attacks"] = len(attacks)
        summary[f"{prefix}attacks_flagged"] = caught
        summary[f"{prefix}recall"] = _rate(caught, len(attacks))
        summary[f"{prefix}recall_95ci"] = wilson(caught, len(attacks))
        summary[f"{prefix}benign"] = len(benign)
        summary[f"{prefix}benign_flagged"] = alarms
        summary[f"{prefix}false_positive_rate"] = _rate(alarms, len(benign))
        summary[f"{prefix}false_positive_rate_95ci"] = wilson(alarms, len(benign))

    families: dict[str, dict] = defaultdict(lambda: {"label": None, "n": 0, "flagged": 0})
    for r in rows:
        f = families[r["family"]]
        f["label"], f["n"], f["flagged"] = r["label"], f["n"] + 1, f["flagged"] + r["flagged"]
    return {
        "summary": summary,
        "by_family": {k: families[k] for k in sorted(families)},
        "by_surface": {
            s: {
                "attacks": sum(r["surface"] == s and r["label"] == "attack" for r in rows),
                "attacks_flagged": sum(
                    r["surface"] == s and r["label"] == "attack" and r["flagged"] for r in rows
                ),
            }
            for s in SURFACES
        },
        # IDs only: the texts are in the set file.
        "missed_attacks": sorted(
            r["id"] for r in rows if r["label"] == "attack" and not r["flagged"]
        ),
        "false_alarms": sorted(r["id"] for r in rows if r["label"] == "benign" and r["flagged"]),
        # Attacks caught only because they spell out an internal tool name (policy_lookup): a
        # writer who knows the names, not one the detector reads as an attack in general.
        "caught_only_by_tool_names": sorted(
            r["id"]
            for r in rows
            if r["label"] == "attack" and r["signals"] == ["names_internal_tools"]
        ),
        "rows": rows,
    }


def run(path: Path = SET_PATH, registry: Registry | None = None) -> dict:
    cases = load_cases(path)
    result = evaluate(cases)
    result["meta"] = run_meta(
        registry or Registry.from_dir(),
        [],  # no agent runs: the detector is code
        mode=MODE,
        set_path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        set_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        heldout_sha256=heldout_sha256(cases),
        detector_sha256=detector_sha256(),
    )
    return result


def _pct(rate: float | None, ci: list[float] | None) -> str:
    if rate is None:
        return "n/a"
    return f"{rate:.0%} (95% CI {ci[0]:.0%}–{ci[1]:.0%})" if ci else f"{rate:.0%}"


def report(result: dict) -> str:
    s, meta = result["summary"], result["meta"]
    lines = [
        "# Injection detector, offline (text written blind to the detector)",
        "",
        f"Set: `{meta['set_path']}` (sha256 {meta['set_sha256'][:12]}). Detector source sha256 "
        f"{meta['detector_sha256'][:12]}. Commit {meta['git_commit'][:12]}"
        f"{' (dirty)' if meta['git_dirty'] else ''}, {meta['run_at']}. No model calls.",
        "",
        "Quote the held-out numbers: the dev split is the one a detector change may be tuned on.",
        "",
        "| Split | Attacks flagged (recall) | Benign flagged (false alarms) |",
        "|---|---|---|",
    ]
    for split, prefix in (("Held-out", "heldout_"), ("Dev", "dev_"), ("All", "")):
        lines.append(
            f"| {split} | {s[prefix + 'attacks_flagged']}/{s[prefix + 'attacks']}: "
            f"{_pct(s[prefix + 'recall'], s[prefix + 'recall_95ci'])} | "
            f"{s[prefix + 'benign_flagged']}/{s[prefix + 'benign']}: "
            f"{_pct(s[prefix + 'false_positive_rate'], s[prefix + 'false_positive_rate_95ci'])} |"
        )
    lines += ["", "## By family (both splits)", "", "| Family | Label | Flagged |", "|---|---|---|"]
    for name, f in result["by_family"].items():
        lines.append(f"| {name} | {f['label']} | {f['flagged']}/{f['n']} |")
    lines += ["", "## Attacks by surface", "", "| Surface | Flagged |", "|---|---|"]
    for name, f in result["by_surface"].items():
        if f["attacks"]:
            lines.append(f"| {name} | {f['attacks_flagged']}/{f['attacks']} |")
    lines += [
        "",
        "## What this does and doesn't show",
        "",
        "- It measures the detector alone, on one author's paraphrases: a different author, or",
        "  an attacker iterating against it, would find other misses. The intervals are wide.",
        "- A missed attack still meets the other defences: the agents are told claim text is",
        "  evidence, code routes and caps every decision, and a person decides what the rules",
        "  send them. Whether a missed attack would steer a model is the live half, not run here.",
        "- A false alarm sends a case to a person; it costs review time, not safety.",
        "",
        "Caught only because the text spells out an internal tool name (`names_internal_tools`): "
        f"{', '.join(result['caught_only_by_tool_names']) or 'none'}. The set's writer knew the"
        " repo's tool names, though not the detector.",
        "",
        f"Missed attacks: {', '.join(result['missed_attacks']) or 'none'}.",
        "",
        f"False alarms: {', '.join(result['false_alarms']) or 'none'}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.evals.injection_detector")
    parser.add_argument("--publish", action="store_true", help=f"write {RESULTS_DIR}")
    args = parser.parse_args(argv)
    result = run()
    text = report(result)
    print(text)
    if args.publish:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        (RESULTS_DIR / "report.md").write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
