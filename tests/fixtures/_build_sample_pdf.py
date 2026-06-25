"""Build the test fixture ``sample.pdf`` with weasyprint.

Run with the weasyprint system libs available:
    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run python tests/fixtures/_build_sample_pdf.py

The generated PDF is committed so the PDF-parser tests read it directly (pdfplumber/pypdf need
no system libs). Two ``<h1>`` headings make weasyprint emit a 2-entry PDF outline (TOC), and
the page includes paragraphs, a monospace code block, a table, and an embedded PNG.
"""

from __future__ import annotations

import base64
from pathlib import Path

from tests.fixtures._build_sample_epub import make_png

HERE = Path(__file__).parent
OUT = HERE / "sample.pdf"


def build() -> None:
    from weasyprint import HTML

    png_b64 = base64.b64encode(make_png()).decode("ascii")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>BreviaBook Sample PDF</title>
<style>
  @page {{ size: A5; margin: 1.5cm; }}
  body {{ font-family: serif; }}
  h1 {{ font-size: 22pt; page-break-before: always; }}
  h1:first-of-type {{ page-break-before: avoid; }}
  pre {{ font-family: monospace; font-size: 11pt; background: #eee; padding: 6px; }}
  table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #333; padding: 3px; }}
</style></head>
<body>
  <h1>Chapter One</h1>
  <p>This is the first paragraph of the sample PDF, with enough words to form a block.</p>
  <p>A second paragraph in chapter one for good measure.</p>
  <pre><code>def hello() -&gt; str:
    return "world"</code></pre>
  <h1>Chapter Two</h1>
  <p>Second chapter introductory paragraph in the sample PDF document.</p>
  <figure><img src="data:image/png;base64,{png_b64}" alt="diagram"/>
    <figcaption>Figure 2.1</figcaption></figure>
  <table>
    <tr><th>Name</th><th>Value</th></tr>
    <tr><td>alpha</td><td>1</td></tr>
  </table>
</body></html>"""
    HTML(string=html).write_pdf(str(OUT))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
