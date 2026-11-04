"""Decision records, the board brief and the review package must point at things that exist."""

import re
from pathlib import Path

import pytest

from hall_monitor.governance.controls import ROOT, load_register

DOCS = ROOT / "docs"
ADRS = sorted((DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
PAGES = [
    *ADRS,
    DOCS / "adr" / "README.md",
    DOCS / "board-brief.md",
    *(DOCS / "review").glob("*.md"),
]
CONTROL_IDS = {c["id"] for c in load_register()["controls"]}

LINK = re.compile(r"\]\(([^)\s]+)\)")


def _local_links(page: Path) -> list[str]:
    return [t for t in LINK.findall(page.read_text()) if not re.match(r"[a-z]+:", t)]


def _headings(page: Path) -> set[str]:
    return {
        re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-")
        for h in re.findall(r"^#+ (.+)$", page.read_text(), re.M)
    }


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(DOCS)))
def test_relative_links_resolve(page):
    for target in _local_links(page):
        path, _, anchor = target.partition("#")
        resolved = (page.parent / path).resolve() if path else page
        assert resolved.exists(), f"{target} doesn't exist"
        if anchor and resolved.suffix == ".md":
            assert anchor in _headings(resolved), f"{target}: no anchor {anchor}"


def test_adrs_are_numbered_in_order_and_indexed():
    numbers = [int(p.name[:4]) for p in ADRS]
    assert numbers == list(range(1, len(ADRS) + 1))
    index = (DOCS / "adr" / "README.md").read_text()
    assert all(f"]({p.name})" in index for p in ADRS)


@pytest.mark.parametrize("adr", ADRS, ids=lambda p: p.name[:4])
def test_adr_has_status_and_known_controls(adr):
    text = adr.read_text()
    assert re.search(r"^- \*\*Status:\*\* (Accepted|Proposed|Superseded by)", text, re.M)
    cited = set(re.findall(r"\bHM-\d{2}\b", text))
    assert cited, "an ADR names the control it supports"
    assert cited <= CONTROL_IDS


def test_adr_0001_lists_every_routing_check():
    """The ADR that explains the routing rule must name each fact the code checks."""
    adr = (DOCS / "adr" / "0001-routing-decided-in-code.md").read_text()
    for fact in (
        "insured",
        "coverage period",
        "USD",
        "no policy record",
        "incident date",
        "claim documents",
        "add up",
    ):
        assert fact in adr, fact
