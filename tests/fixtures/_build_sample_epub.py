"""Build the deterministic test fixture ``sample.epub``.

Run with ``uv run python tests/fixtures/_build_sample_epub.py``. The generated EPUB is
committed alongside this script so its provenance is reviewable. It exercises every IR block
type (heading, paragraph, code, image, table, quote, list) plus image extraction.
"""

from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "sample.epub"


def make_png() -> bytes:
    """Return a valid 1x1 grayscale PNG (built with correct CRCs — no external deps)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)  # 1x1, 8-bit grayscale
    idat = zlib.compress(b"\x00\xff")  # one scanline: filter byte 0 + one white pixel
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:brevia-sample-0001</dc:identifier>
    <dc:title>Brevia Sample Book</dc:title>
    <dc:creator>William Wong Garay</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="fig1" href="images/fig1.png" media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""

CH1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter One</title></head>
<body>
  <h1>Chapter One</h1>
  <p>This is the first paragraph of the sample book.</p>
  <pre><code class="language-python">def hello() -> str:
    return "world"
</code></pre>
  <blockquote>A quote worth keeping.</blockquote>
  <ul><li>First item</li><li>Second item</li></ul>
</body></html>
"""

CH2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter Two</title></head>
<body>
  <h1>Chapter Two</h1>
  <p>Second chapter intro paragraph.</p>
  <figure>
    <img src="images/fig1.png" alt="architecture diagram"/>
    <figcaption>Figure 2.1 - the architecture</figcaption>
  </figure>
  <table>
    <tr><th>Name</th><th>Value</th></tr>
    <tr><td>alpha</td><td>1</td></tr>
  </table>
</body></html>
"""


def build() -> None:
    import zipfile

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype MUST be first and stored uncompressed per the EPUB spec.
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/ch1.xhtml", CH1)
        zf.writestr("OEBPS/ch2.xhtml", CH2)
        zf.writestr("OEBPS/images/fig1.png", make_png())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
