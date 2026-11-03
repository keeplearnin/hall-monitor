"""The controls register (controls/controls.yaml) and the document generated from it.

    uv run python -m hall_monitor.governance.controls     # regenerate docs/controls.md

The tests in tests/test_controls.py keep the register honest: every code and test reference
must exist, every framework ID must be in controls/frameworks.yaml, every audit event the code
writes must be mapped, and docs/controls.md must match the register.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "controls" / "controls.yaml"
FRAMEWORKS = ROOT / "controls" / "frameworks.yaml"
DOC = ROOT / "docs" / "controls.md"


def load_register(path: Path = REGISTER) -> dict:
    return yaml.safe_load(path.read_text())


def load_frameworks(path: Path = FRAMEWORKS) -> dict:
    return yaml.safe_load(path.read_text())


def _anchor(control: dict) -> str:
    return control["id"].lower()


def _ref(ref: str) -> str:
    if ref.startswith("pending:"):
        return f"*Pending live run:* `{ref.removeprefix('pending:').strip()}`"
    path, _, symbol = ref.partition("::")
    return f"[`{path}`](../{path})" + (f" `{symbol}`" if symbol else "")


def render(register: dict, frameworks: dict) -> str:
    nist = frameworks["nist_ai_rmf"]
    finos = frameworks["finos_aigf"]
    controls = register["controls"]
    not_covered = {n["ref"]: n["why"] for n in register["not_covered"]}

    def by(key: str, sub: str | None = None) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for c in controls:
            values = c[key][sub] if sub else c[key]
            for v in values:
                index.setdefault(v, []).append(c["id"])
        return index

    def links(ids: list[str]) -> str:
        return ", ".join(f"[{i}](#{i.lower()})" for i in ids)

    lines = [
        "# Controls",
        "",
        "<!-- Generated from controls/controls.yaml by "
        "`uv run python -m hall_monitor.governance.controls`. Don't edit by hand. -->",
        "",
        f"Every hall-monitor control, mapped to the {nist['name']} and the "
        f"{finos['name']} ({finos['version']}). Each control names its code, the tests that "
        "prove it and its published evidence; a test fails if any reference goes stale. "
        "Framework identifiers were checked against the sources listed at the end.",
        "",
        "## Summary",
        "",
        "| ID | Control | Status | NIST AI RMF | FINOS risks | FINOS mitigations |",
        "|---|---|---|---|---|---|",
    ]
    for c in controls:
        lines.append(
            f"| [{c['id']}](#{_anchor(c)}) | {c['name']} | {c['status']} | {', '.join(c['nist'])} "
            f"| {', '.join(c['finos']['risks'])} | {', '.join(c['finos']['mitigations'])} |"
        )

    lines += ["", "## Controls", ""]
    for c in controls:
        lines += [f"### {c['id']}", "", f"**{c['name']}** ({c['status']})", "", c["what"], ""]
        lines += ["- **Code:** " + "; ".join(_ref(r) for r in c["code"])]
        lines += ["- **Tests:** " + "; ".join(_ref(r) for r in c["tests"])]
        if c["evidence"]:
            lines += ["- **Evidence:** " + "; ".join(_ref(r) for r in c["evidence"])]
        lines += [
            "- **NIST AI RMF:** "
            + "; ".join(f"{n} ({nist['subcategories'][n].split('.')[0]})" for n in c["nist"])
        ]
        lines += [
            "- **FINOS risks:** "
            + "; ".join(f"{r} {finos['risks'][r]}" for r in c["finos"]["risks"])
        ]
        lines += [
            "- **FINOS mitigations:** "
            + "; ".join(f"{m} {finos['mitigations'][m]}" for m in c["finos"]["mitigations"])
        ]
        if c.get("gaps"):
            lines += [f"- **Gaps:** {c['gaps']}"]
        lines.append("")

    nist_index = by("nist")
    lines += ["## NIST AI RMF coverage", "", "| Subcategory | Controls |", "|---|---|"]
    for sub, text in nist["subcategories"].items():
        cell = links(nist_index[sub]) if sub in nist_index else f"Not covered: {not_covered[sub]}"
        lines.append(f"| **{sub}** {text} | {cell} |")

    for kind, title in (("risks", "risks"), ("mitigations", "mitigations")):
        index = by("finos", kind)
        lines += ["", f"## FINOS AIGF {title}", "", "| ID | Title | Controls |", "|---|---|---|"]
        for fid, ftitle in finos[kind].items():
            if fid in index:
                cell = links(index[fid])
            elif fid in not_covered:
                cell = f"Not covered: {not_covered[fid]}"
            else:
                cell = "Not mapped"
            lines.append(f"| {fid} | {ftitle} | {cell} |")

    lines += ["", "## Audit log events", "", "| Event | Evidence for |", "|---|---|"]
    for event, ids in register["audit_events"].items():
        lines.append(f"| `{event}` | {links(ids)} |")

    lines += [
        "",
        "## Sources",
        "",
        f"- {nist['name']}: <{nist['source']}>. {nist['note']}",
        f"- {finos['name']} {finos['version']}: <{finos['source']}>",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    DOC.write_text(render(load_register(), load_frameworks()))
