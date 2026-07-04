"""Build the evaluation PDF source: docs/Chronos-Report.html.

Concatenates the design document, benchmarks, and protocol diagrams into a
single print-styled HTML page. Mermaid diagrams render client-side (CDN)
— print via headless Edge/Chrome with a virtual time budget so they finish
before the PDF snapshot:

    python tools/build_report.py
    msedge --headless --print-to-pdf=docs/Chronos-Report.pdf \
           --virtual-time-budget=20000 docs/Chronos-Report.html
"""

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

SECTIONS = [
    ("Engineering Design Document", DOCS / "DESIGN_DOC.md"),
    ("Benchmarks", DOCS / "BENCHMARKS.md"),
    ("Protocol Diagrams", DOCS / "DIAGRAMS.md"),
]

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", system-ui, sans-serif;
  color: #1a1a1a; font-size: 10.5pt; line-height: 1.55;
  max-width: 175mm; margin: 0 auto;
}
.cover { text-align: center; padding-top: 70mm; page-break-after: always; }
.cover h1 { font-size: 26pt; border: none; margin-bottom: 4pt; }
.cover p { color: #555; }
h1 { font-size: 16pt; border-bottom: 2px solid #1a1a1a; padding-bottom: 4pt;
     margin-top: 28pt; page-break-after: avoid; }
section.part > h1 { page-break-before: always; }
h2 { font-size: 12.5pt; margin-top: 18pt; page-break-after: avoid; }
h3 { font-size: 11pt; page-break-after: avoid; }
code, pre { font-family: Consolas, monospace; font-size: 8.8pt; }
pre { background: #f5f5f2; border: 1px solid #ddd; border-radius: 4px;
      padding: 8px 10px; overflow-x: hidden; white-space: pre-wrap;
      page-break-inside: avoid; }
code { background: #f5f5f2; padding: 0 3px; border-radius: 3px; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 10pt 0;
        page-break-inside: avoid; font-size: 9.5pt; }
th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #f0efec; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 12px;
             color: #555; }
.mermaid { text-align: center; page-break-inside: avoid; margin: 14pt 0; }
a { color: inherit; text-decoration: none; }
hr { border: none; border-top: 1px solid #ddd; margin: 18pt 0; }
"""

MERMAID = """
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "neutral" });
</script>
"""


def convert(md_text: str) -> str:
    # Lift mermaid fences out before conversion so they render client-side.
    blocks: list[str] = []

    def stash(match: re.Match) -> str:
        blocks.append(match.group(1))
        return f"\n@@MERMAID{len(blocks) - 1}@@\n"

    md_text = re.sub(r"```mermaid\n(.*?)```", stash, md_text, flags=re.S)
    html = markdown.markdown(
        md_text, extensions=["tables", "fenced_code", "sane_lists"]
    )
    for i, block in enumerate(blocks):
        html = html.replace(
            f"@@MERMAID{i}@@", f'<pre class="mermaid">{block}</pre>'
        )
        html = html.replace(
            f"<p>@@MERMAID{i}@@</p>", f'<pre class="mermaid">{block}</pre>'
        )
    return html


def main() -> None:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Chronos — Engineering Report</title>",
        f"<style>{CSS}</style></head><body>",
        "<div class='cover'><h1>Chronos</h1>",
        "<p>A production-inspired distributed job scheduler</p>",
        "<p>Engineering design document · benchmarks · protocol diagrams</p>",
        "<p>PostgreSQL SKIP LOCKED queue · leases &amp; heartbeats · "
        "fenced transitions · retries &amp; DLQ · idempotent enqueue</p></div>",
    ]
    for title, path in SECTIONS:
        parts.append(f"<section class='part'>{convert(path.read_text(encoding='utf-8'))}</section>")
    parts.append(MERMAID)
    parts.append("</body></html>")

    out = DOCS / "Chronos-Report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
