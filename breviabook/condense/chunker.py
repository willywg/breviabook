"""Chapter-aware, token-based chunking (ROADMAP §7.2).

Rules that distinguish BreviaBook from the reference repos:
- chunk *within* a chapter; never cross chapter boundaries;
- target ~2000 tokens (not ~450 — summarization needs more context, §2.2);
- blocks are atomic, so a ``CodeBlock``/``TableBlock`` is NEVER split across chunks;
- carry a light ``prev_context`` (trailing text of the previous chunk) for continuity,
  kept separate from ``blocks`` so it is never re-condensed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from breviabook.ir.models import Block, Document
from breviabook.utils.tokens import block_text, block_tokens, count_tokens

DEFAULT_MAX_TOKENS = 2000
_CONTEXT_CHARS = 400


class Chunk(BaseModel):
    """A contiguous group of blocks from a single chapter, sized for one LLM pass."""

    id: str
    chapter_index: int
    chapter_title: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    token_count: int = 0
    prev_context: str | None = None


class Chunker:
    """Splits a :class:`~breviabook.ir.models.Document` into chapter-bounded chunks."""

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens

    def chunk(self, doc: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        for ch_index, chapter in enumerate(doc.chapters):
            chunks.extend(self._chunk_chapter(ch_index, chapter.title, chapter.blocks))
        return chunks

    def _chunk_chapter(self, ch_index: int, title: str | None, blocks: list[Block]) -> list[Chunk]:
        out: list[Chunk] = []
        current: list[Block] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current, current_tokens
            if not current:
                return
            prev_context = self._context_from(out[-1]) if out else None
            out.append(
                Chunk(
                    id=f"ch{ch_index}-{len(out) + 1}",
                    chapter_index=ch_index,
                    chapter_title=title,
                    blocks=current,
                    token_count=current_tokens,
                    prev_context=prev_context,
                )
            )
            current = []
            current_tokens = 0

        for block in blocks:
            bt = block_tokens(block)
            if current and current_tokens + bt > self.max_tokens:
                flush()
            current.append(block)
            current_tokens += bt
        flush()
        return out

    def _context_from(self, prev: Chunk) -> str | None:
        """Trailing text of the previous chunk, truncated — continuity hint for the LLM."""
        for block in reversed(prev.blocks):
            text = block_text(block).strip()
            if text:
                return text[-_CONTEXT_CHARS:]
        return None


def count_document_tokens(doc: Document) -> int:
    """Total estimated input tokens for a document (used by ``--dry-run``, Phase 12)."""
    total = 0
    for chapter in doc.chapters:
        if chapter.title:
            total += count_tokens(chapter.title)
        total += sum(block_tokens(b) for b in chapter.blocks)
    return total
