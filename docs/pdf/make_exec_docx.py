"""The executive brief as a Word document, read from docs/executive-brief.html.

    uv run --with python-docx python docs/pdf/make_exec_docx.py

Writes docs/pdf/hall-monitor-executive-briefing.docx. Every word comes from the HTML page each
time it runs (the eyebrow, title, lede, figure caption and description, the figures, the
regulator's questions with their status, the roadmap and the notes), and it stops if any item on
the page wasn't read, so the text can't drift from the brief. The claim-flow figure is
docs/pdf/claim-flow.png, a light-theme snapshot of the page's diagram, refreshed by hand when the
diagram changes (docs/pdf/README.md says how). python-docx is used only here, so it isn't a
project dependency.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "executive-brief.html"
FIGURE = ROOT / "docs" / "pdf" / "claim-flow.png"
OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else ROOT / "docs" / "pdf" / ("hall-monitor-executive-briefing.docx")
)

FONT = "Arial"
INK, MUTED = RGBColor(0x1F, 0x29, 0x33), RGBColor(0x5B, 0x66, 0x70)
AMBER, GREEN, LINK = "B7791F", "2F8F83", RGBColor(0x3A, 0x6E, 0xA5)
RULE = "D5DBE1"
BODY_IN = 7.0  # Letter with 0.75 in margins


# --- Reading the page ----------------------------------------------------------------------


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _runs(fragment: str) -> list[tuple[str, bool]]:
    """Text and whether it is bold, from inline <b>/<strong> (with or without attributes)."""
    parts = re.split(r"(<(?:b|strong)\b[^>]*>.*?</(?:b|strong)>)", fragment, flags=re.S)
    runs = []
    for part in parts:
        if not part:
            continue
        bold = bool(re.match(r"<(?:b|strong)\b", part))
        text = html.unescape(re.sub(r"<[^>]+>", "", part))
        text = re.sub(r"\s+", " ", text)
        if text:
            runs.append((text, bold))
    return runs


def _one(pattern: str, page: str) -> str:
    match = re.search(pattern, page, re.S)
    if match is None:
        raise SystemExit(f"executive-brief.html: nothing matches {pattern!r}")
    return match.group(1)


def _container(tag: str, cls: str, page: str) -> tuple[str, str]:
    """A list's inner markup and the <h2> just before it (its section title)."""
    match = re.search(rf'<{tag}\b[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</{tag}>', page, re.S)
    if match is None:
        raise SystemExit(f"executive-brief.html: no <{tag} class={cls!r}>")
    headings = re.findall(r"<h2\b[^>]*>(.*?)</h2>", page[: match.start()], re.S)
    if not headings:
        raise SystemExit(f"executive-brief.html: no heading before <{tag} class={cls!r}>")
    return match.group(1), _text(headings[-1])


def _items(inner: str, tag: str) -> list[tuple[str, str]]:
    """Each item's attributes and inner markup."""
    return re.findall(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", inner, re.S)


def _span(item: str, cls: str) -> str:
    """The inner markup of the span with this class, allowing nested spans."""
    match = re.search(rf'<span\b[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>', item)
    if match is None:
        raise ValueError(cls)
    depth, i = 1, match.end()
    for tag in re.finditer(r"<(/?)span\b[^>]*>", item[i:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return item[i : i + tag.start()]
    raise ValueError(cls)


def _expect(found: int, parsed: int, what: str) -> None:
    """Fail rather than write a gap: every item on the page must have been read."""
    if found == 0 or found != parsed:
        raise SystemExit(f"executive-brief.html: {found} {what} on the page, {parsed} read")


def read_brief(page: str) -> dict:
    eyebrow = _one(r'<div class="eyebrow">(.*?)</div>', page)
    figure = _one(r"<figure>(.*?)</figure>", page)
    footer = _one(r"<footer>(.*?)</footer>", page)

    stats_html, stats_title = _container("dl", "stats", page)
    stats = []
    raw = re.findall(r'<div\b[^>]*class="[^"]*\bstat\b', stats_html)
    for n, d in re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", stats_html, re.S):
        stats.append((_text(n), _runs(d)))
    _expect(len(raw), len(stats), "stats")

    qa_html, qa_title = _container("ul", "qa", page)
    items = _items(qa_html, "li")
    qa = []
    for _, item in items:
        try:
            question = _span(item, "q")
            status = _span(item, "chip")
            answer = _span(item, "a")
        except ValueError:
            continue
        question = question.replace(question[question.index("<span") :], "")
        qa.append((_text(question), _text(status), _runs(answer)))
    _expect(len(items), len(qa), "regulator questions")

    road_html, road_title = _container("ol", "road", page)
    items = _items(road_html, "li")
    road = []
    for attrs, item in items:
        spans = re.findall(r"<span\b([^>]*)>(.*?)</span>", item, re.S)
        if len(spans) != 2 or 'class="m"' not in spans[0][0]:
            continue
        done = bool(re.search(r'class="[^"]*\bdone\b', attrs))
        road.append((done, _text(spans[0][1]), _text(spans[1][1])))
    _expect(len(items), len(road), "roadmap items")

    children = re.findall(r"<(span|p|div)\b[^>]*>(.*?)</\1>", footer, re.S)
    notes = [_runs(inner) for _, inner in children]
    links = re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', footer, re.S)
    _expect(len(re.findall(r"<(?:span|p|div|a)\b", footer)), len(notes) + len(links), "notes")

    return {
        "ticket": _text(_one(r'<span class="ticket">(.*?)</span>', eyebrow)),
        "eyebrow": _text(re.sub(r'<span class="ticket">.*?</span>', "", eyebrow, flags=re.S)),
        "title": _text(_one(r"<h1\b[^>]*>(.*?)</h1>", page)),
        "lede": _runs(_one(r'<p class="lede">(.*?)</p>', page)),
        "figure_title": _text(_one(r"<h2\b[^>]*>(.*?)</h2>", figure)),
        "figure_alt": html.unescape(_one(r'<svg\b[^>]*aria-label="([^"]*)"', figure)),
        "caption": _runs(_one(r"<figcaption\b[^>]*>(.*?)</figcaption>", figure)),
        "stats_title": stats_title,
        "stats": stats,
        "qa_title": qa_title,
        "qa": qa,
        "road_title": road_title,
        "road": road,
        "notes": notes,
        "links": [(url, _text(t)) for url, t in links],
    }


# --- Writing the document ------------------------------------------------------------------


def _run(paragraph, text, *, size=10, bold=False, color=INK, caps=False):
    run = paragraph.add_run(text)
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.all_caps = caps
    return run


def _para(doc_or_cell, *, before=0, after=4, align=None, keep_next=False):
    paragraph = doc_or_cell.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before, fmt.space_after = Pt(before), Pt(after)
    fmt.keep_with_next = keep_next
    if align is not None:
        paragraph.alignment = align
    return paragraph


def _heading(doc, text):
    paragraph = doc.add_heading(level=2)
    paragraph.paragraph_format.space_before = Pt(11)
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.paragraph_format.keep_with_next = True
    _run(paragraph, text, size=9, bold=True, color=MUTED, caps=True)


def _shade(cell, fill):
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def _borders(cell, *, bottom=RULE, others=None):
    """A thin rule under each row, nothing else; others set a colour for the other sides."""
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        edge = OxmlElement(f"w:{side}")
        color = bottom if side == "bottom" else others
        if color is None:
            edge.set(qn("w:val"), "nil")
        else:
            edge.set(qn("w:val"), "single")
            edge.set(qn("w:sz"), "4" if side == "bottom" else "12")
            edge.set(qn("w:color"), color)
        borders.append(edge)
    cell._tc.get_or_add_tcPr().append(borders)


def _no_split(row):
    props = row._tr.get_or_add_trPr()
    props.append(OxmlElement("w:cantSplit"))


def _header_row(row):
    props = row._tr.get_or_add_trPr()
    props.append(OxmlElement("w:tblHeader"))


def _table(doc, widths, rows):
    table = doc.add_table(rows=0, cols=len(widths))
    table.autofit = False
    margins = OxmlElement("w:tblCellMar")
    for side, twips in (("top", 25), ("bottom", 25), ("left", 80), ("right", 80)):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:w"), str(twips))
        edge.set(qn("w:type"), "dxa")
        margins.append(edge)
    table._tbl.tblPr.append(margins)
    for row_cells in rows:
        row = table.add_row()
        _no_split(row)
        for cell, width in zip(row.cells, widths, strict=True):
            cell.width = Inches(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
        row_cells(row.cells)
    for column, width in zip(table.columns, widths, strict=True):
        column.width = Inches(width)
    return table


def _cell_para(cell, runs, *, size=9, color=MUTED, align=None):
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    if align is not None:
        paragraph.alignment = align
    for text, bold in runs:
        _run(paragraph, text, size=size, bold=bold, color=INK if bold else color)
    return paragraph


def _hyperlink(paragraph, url, text):
    part = paragraph.part
    rel = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    for tag, val in (("w:color", "3A6EA5"), ("w:u", "single"), ("w:sz", "16")):
        el = OxmlElement(tag)
        el.set(qn("w:val"), val)
        props.append(el)
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), FONT)
    fonts.set(qn("w:hAnsi"), FONT)
    props.append(fonts)
    run.append(props)
    text_el = OxmlElement("w:t")
    text_el.text = text
    run.append(text_el)
    link.append(run)
    paragraph._p.append(link)


def _page_number(paragraph):
    run = paragraph.add_run()
    run.font.name, run.font.size, run.font.color.rgb = FONT, Pt(7), MUTED
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def build(brief: dict, out: Path) -> None:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.left_margin = section.right_margin = Inches(0.75)
    section.top_margin = section.bottom_margin = Inches(0.55)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = FONT, Pt(10)
    doc.core_properties.title = "hall-monitor: executive briefing"
    doc.core_properties.author = "hall-monitor"
    doc.core_properties.subject = brief["eyebrow"]

    date = re.search(r"updated ([^·]+)", brief["eyebrow"])
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    label = f"hall-monitor · executive briefing · {date.group(1).strip() if date else ''} · page "
    _run(footer, label, size=7, color=MUTED)
    _page_number(footer)

    eyebrow = _para(doc, after=8)
    _run(eyebrow, brief["ticket"], size=7.5, bold=True, color=RGBColor.from_string(AMBER))
    _run(eyebrow, "   " + brief["eyebrow"], size=7.5, color=MUTED)
    title = _para(doc, after=8, keep_next=True)
    _run(title, brief["title"], size=22, bold=True)
    lede = _para(doc, after=10)
    for text, bold in brief["lede"]:
        _run(lede, text, size=11, bold=bold, color=INK if bold else MUTED)

    _heading(doc, brief["figure_title"])
    picture = _para(doc, after=6, keep_next=True)
    shape = picture.add_run().add_picture(str(FIGURE), width=Inches(BODY_IN - 0.3))
    shape._inline.docPr.set("descr", brief["figure_alt"])  # the page's own description
    shape._inline.docPr.set("title", brief["figure_title"])
    caption = _para(doc, after=8)
    for text, bold in brief["caption"]:
        _run(caption, text, size=9, bold=bold, color=INK if bold else MUTED)

    _heading(doc, brief["stats_title"])

    def stat_row(number, runs):
        def fill(cells):
            _cell_para(cells[0], [(number, True)], size=16)
            _cell_para(cells[1], runs, size=10)
            cells[1].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for c in cells:
                _borders(c)

        return fill

    _table(doc, [1.05, BODY_IN - 1.05], [stat_row(n, d) for n, d in brief["stats"]])

    _heading(doc, brief["qa_title"])
    widths = [1.9, 0.62, BODY_IN - 2.52]

    def head(names):
        def fill(cells):
            for c, name in zip(cells, names, strict=True):
                _cell_para(c, [(name, False)], size=8)
                _borders(c)

        return fill

    def qa_row(question, status, answer):
        def fill(cells):
            _cell_para(cells[0], [(question, True)], size=9.5)
            badge = _cell_para(cells[1], [], align=WD_ALIGN_PARAGRAPH.CENTER)
            _run(badge, status, size=7.5, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
            cells[1].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _shade(cells[1], GREEN if status.lower() == "built" else AMBER)
            _cell_para(cells[2], answer, size=9)
            for c in (cells[0], cells[2]):
                _borders(c)
            _borders(cells[1], bottom="FFFFFF", others="FFFFFF")

        return fill

    qa = _table(
        doc, widths, [head(["Question", "Status", "Answer"])] + [qa_row(*q) for q in brief["qa"]]
    )
    _header_row(qa.rows[0])

    _heading(doc, brief["road_title"])
    widths = [0.85, 0.6, BODY_IN - 1.45]
    green = RGBColor.from_string(GREEN)

    def road_row(done, milestone, text):
        def fill(cells):
            p = _cell_para(cells[0], [])
            _run(p, milestone, size=9, bold=True, color=green if done else MUTED)
            p = _cell_para(cells[1], [])
            _run(p, "Done" if done else "Open", size=8, color=green if done else MUTED)
            _cell_para(cells[2], [(text, False)], size=9, color=INK)
            for c in cells:
                _borders(c)

        return fill

    road = _table(
        doc,
        widths,
        [head(["Milestone", "Status", "What it adds"])] + [road_row(*r) for r in brief["road"]],
    )
    _header_row(road.rows[0])

    rule = _para(doc, before=2, after=2)
    bottom = OxmlElement("w:pBdr")
    edge = OxmlElement("w:bottom")
    for key, val in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", RULE)):
        edge.set(qn(key), val)
    bottom.append(edge)
    rule._p.get_or_add_pPr().append(bottom)
    for note in brief["notes"]:
        paragraph = _para(doc, after=2)
        for text, bold in note:
            _run(paragraph, text, size=8, bold=bold, color=INK if bold else MUTED)
    for url, text in brief["links"]:
        _hyperlink(_para(doc), url, text)

    doc.save(out)


if __name__ == "__main__":
    build(read_brief(SOURCE.read_text()), OUT)
    print(OUT)
