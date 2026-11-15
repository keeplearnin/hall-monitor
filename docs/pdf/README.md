# Files to share

| File | What it is |
|---|---|
| [`hall-monitor-architecture-and-functionality.pdf`](hall-monitor-architecture-and-functionality.pdf) | Four landscape pages: the system architecture; how one case moves, with the supervisor loop; the three workflows side by side; and the controls register at a glance |
| [`hall-monitor-executive-briefing.pdf`](hall-monitor-executive-briefing.pdf) | The [executive brief](../executive-brief.html), printed: two pages for business leaders |
| [`hall-monitor-executive-briefing.docx`](hall-monitor-executive-briefing.docx) | The same brief as an editable Word document (two US Letter pages in LibreOffice; Word may paginate slightly differently) |

All three are snapshots. Rebuild them when the code or the docs they come from change, in the same
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

The Word version is built by [`make_exec_docx.py`](make_exec_docx.py) with python-docx (used only
here). It reads every word from `docs/executive-brief.html` each time and stops if any item on the page
(a figure, question, roadmap item or note) wasn't read, so the text can't drift from the page. The
figure is a snapshot, refreshed by hand (below):

```bash
uv run --with python-docx python docs/pdf/make_exec_docx.py
```

Its figure is [`claim-flow.png`](claim-flow.png), the page's claim-flow diagram captured in the
light theme. Refresh it when that diagram changes (Chrome on macOS; the page is forced to its light
theme and everything but the diagram hidden, then the capture is cropped to the drawing):

```bash
sed 's/<html lang="en">/<html lang="en" data-theme="light">/; s#</head>#<style>header,.grid,footer,figcaption,figure>h2{display:none!important} body{background:\#fff;margin:0} .sheet{padding:0!important;max-width:none!important} figure{margin:0!important;border:0!important;padding:8px!important} .scroll{overflow:visible!important}</style></head>#' docs/executive-brief.html > /tmp/claim-flow.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1080,600 --virtual-time-budget=5000 --screenshot=/tmp/claim-flow.png file:///tmp/claim-flow.html
uv run --with pillow python -c "from PIL import Image, ImageChops; im = Image.open('/tmp/claim-flow.png').convert('RGB'); b = ImageChops.difference(im, Image.new('RGB', im.size, 'white')).getbbox(); im.crop((b[0] - 12, b[1] - 12, b[2] + 12, b[3] + 12)).save('docs/pdf/claim-flow.png')"
```
