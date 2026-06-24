"""LLM-based table-of-contents inference (ROADMAP §2.3, §10 Phase 8).

When a PDF has no outline and no manual TOC, send the first pages' text to the LLM and ask it
to infer the chapter structure. Async, so it runs in the pipeline layer (the ``PdfParser``
itself stays sync).
"""

from __future__ import annotations

from brevia.llm.base import LLMProvider, Message
from brevia.parsers.pdf_parser import TocEntry
from brevia.utils.jsonx import extract_json_object

TOC_SYSTEM_PROMPT = (
    "You analyze the opening pages of a book and infer its chapter structure. You return only "
    "the chapters you are confident about, using the page indices provided."
)


def build_toc_messages(sample: str, n_pages: int) -> list[Message]:
    user = f"""Below is the text of the first {n_pages} pages of a book, each preceded by a
[PAGE k] marker where k is the 0-based page index.

Infer the chapter structure. Return ONLY a JSON object (no prose, no fences) of the form:
{{"chapters": [{{"title": "Chapter title", "start_page": 0}}]}}
where "start_page" is the 0-based page index where that chapter begins. Use the page markers to
choose start_page. If you cannot find clear chapters, return {{"chapters": []}}.

--- TEXT ---
{sample}"""
    return [
        {"role": "system", "content": TOC_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def infer_toc(
    provider: LLMProvider,
    model: str,
    page_texts: list[str],
    *,
    max_pages: int = 20,
) -> list[TocEntry]:
    """Infer a TOC from the first ``max_pages`` pages. Returns ``[]`` if none can be inferred."""
    n = min(len(page_texts), max_pages)
    if n == 0:
        return []
    sample = "\n\n".join(f"[PAGE {i}]\n{page_texts[i]}" for i in range(n))
    raw = await provider.generate(build_toc_messages(sample, n), model)
    obj = extract_json_object(raw)  # raises ValueError on unparseable output
    chapters_raw = obj.get("chapters")
    entries: list[TocEntry] = []
    if isinstance(chapters_raw, list):
        for item in chapters_raw:
            if isinstance(item, dict) and "title" in item and "start_page" in item:
                try:
                    entries.append(
                        TocEntry(title=str(item["title"]), start_page=int(item["start_page"]))
                    )
                except (ValueError, TypeError):
                    continue
    return entries
