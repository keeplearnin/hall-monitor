# PDFs to share

| File | What it is |
|---|---|
| [`hall-monitor-architecture-and-functionality.pdf`](hall-monitor-architecture-and-functionality.pdf) | Four landscape pages: the system architecture; how one case moves, with the supervisor loop; the three workflows side by side; and the controls register at a glance |
| [`hall-monitor-executive-briefing.pdf`](hall-monitor-executive-briefing.pdf) | The [executive brief](../executive-brief.html), printed: two pages for business leaders |

Both are snapshots. Rebuild them when the code or the docs they come from change, in the same
pull request.

## Rebuild

The diagrams are drawn by [`make_diagrams.py`](make_diagrams.py) with reportlab, which is used
only here and so isn't a project dependency:

```bash
uv run --with reportlab python docs/pdf/make_diagrams.py
```

Pages 1 to 3 are written by hand from the code and the docs as of the script's `AS_OF` date:
update them, and `AS_OF`, when the architecture, the budgets or a status changes. Page 4 is read
from [`controls/controls.yaml`](../../controls/controls.yaml) each time, so it can't drift from
the register.

The executive briefing is the HTML page printed by a browser; its print layout (two tall pages,
colours kept) is in the page's own `@media print` rules. With Chrome on macOS:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-pdf-header-footer --virtual-time-budget=5000 --print-to-pdf="$PWD/docs/pdf/hall-monitor-executive-briefing.pdf" "file://$PWD/docs/executive-brief.html"
```

Any browser's Print to PDF gives the same layout. The page loads its fonts from Google Fonts;
offline, a fallback font is used.
