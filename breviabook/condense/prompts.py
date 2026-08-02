"""Prompt templates for condensation (ROADMAP §7.1, §7.3).

The contract is JSON-in/JSON-out: we present the chunk as labeled segments and ask the model
to return condensed text per ``[TEXT n]`` run plus the ids of essential images. Code is shown
for context but explicitly preserved by us, not the model.
"""

from __future__ import annotations

from breviabook.llm.base import Message

# Every entry names the source block it came from. Position used to carry that
# mapping, which forced an exact one-entry-per-block count — and merging two
# paragraphs into one is precisely what condensing asks for, so the contract
# contradicted the instruction and the parser rejected the model for obeying it.
# An explicit address lets prose merge and split freely while a list stays
# identifiable as the list.
_BLOCK_ADDRESS_RULES = """\
- Each [TEXT n] segment lists its sub-blocks as [BLOCK k type=paragraph|list|quote]. Return a \
JSON **array** for that [TEXT n] key in which every entry names the source block it came from \
with "block": k. Example:
  {"1": [{"block": 1, "type": "paragraph", "text": "..."}, {"block": 2, "type": "list", \
"items": ["a", "b"], "ordered": false}, {"block": 3, "type": "quote", "text": "..."}]}
- Because each entry names its source, you may merge several paragraph blocks into one entry \
(name the first of them) or split one paragraph block across several entries (repeat the same \
"block"). Keep entries in ascending block order.
- Every type=list and type=quote block must appear exactly once and keep its own type. Never \
fold a list or a quote into a paragraph, and never drop one.
- For lists: return condensed plain strings in "items"; match the ordered= label.
- For quotes: return condensed plain string in "text"."""

_PARAGRAPH_ONLY_SHORTCUT = """\
- When a [TEXT n] segment has only [BLOCK … type=paragraph] sub-blocks, you may instead return \
a plain string with paragraphs separated by blank lines."""

_CONDENSE_STRUCTURE_RULES = f"""\
{_BLOCK_ADDRESS_RULES}
- Condense the content of each block; do not reorder them.
{_PARAGRAPH_ONLY_SHORTCUT}"""

_SYNTH_STRUCTURE_RULES = f"""\
{_BLOCK_ADDRESS_RULES}
- Smooth transitions and remove cross-segment repetition; do not reorder blocks.
{_PARAGRAPH_ONLY_SHORTCUT}"""

CONDENSE_SYSTEM_PROMPT = (
    "You are an expert technical editor. You condense technical book content so it reads "
    "fast and without filler, while preserving technical accuracy, definitions, concrete "
    "examples, numbers, and the meaning of code and figures. You never invent content, and "
    "you never alter or summarize code."
)


def build_condense_messages(body: str, target_ratio: float, image_ids: list[str]) -> list[Message]:
    """Build the chat messages for condensing one chunk."""
    pct = max(1, round(target_ratio * 100))
    available = ", ".join(image_ids) if image_ids else "(none)"
    user = f"""Condense the following book excerpt to roughly {pct}% of its original length.

Rules:
- Condense each segment labeled [TEXT n]. Remove redundancy, filler, and repetition; keep key
  facts, definitions, concrete examples, numbers, and technical terms.
{_CONDENSE_STRUCTURE_RULES}
- Preserve technical identifiers, API names, file paths, and URLs exactly.
- Do NOT reproduce code blocks; they are preserved automatically. Condense prose only.
- Images appear as [IMG:id — "caption"]. Decide which are ESSENTIAL to understand the retained
  content and list only those ids in "essential_images". Omit decorative or redundant images.
- Do not add commentary, headings, or content that was not present.

Return ONLY a JSON object (no markdown fences, no prose) of exactly this form:
{{"texts": {{"1": "<condensed [TEXT 1] or array of block objects>", "2": "..."}}, \
"essential_images": ["id"]}}

Available image ids: {available}

--- EXCERPT START ---
{body}
--- EXCERPT END ---"""
    return [
        {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


SYNTH_SYSTEM_PROMPT = (
    "You are an expert technical editor performing a chapter-level pass. You weave separately "
    "condensed sections into one coherent, fast-reading chapter, removing repetition across "
    "section boundaries and trimming to a target length, without losing technical accuracy, "
    "examples, or the meaning of code and figures. You never invent content or alter code."
)


def build_synthesize_messages(
    body: str, target_tokens: int, *, smooth: bool = True
) -> list[Message]:
    """Build the chat messages for the per-chapter synthesis / trim pass."""
    words = max(1, round(target_tokens / 1.3))
    action = (
        "Smooth the transitions between segments, remove cross-segment repetition, and condense"
        if smooth
        else "Condense further and tighten"
    )
    user = f"""{action} the prose below so the whole chapter reads as one coherent, fast section \
of about {words} words total.

Rules:
- Condense each segment labeled [TEXT n].
{_SYNTH_STRUCTURE_RULES}
- Remove repetition that appears across segments (these are chunk boundaries).
- Preserve technical identifiers, API names, file paths, and URLs exactly.
- Do NOT reproduce code blocks, tables, or images; they are preserved automatically.
- Do not invent content or add headings that were not present.

Return ONLY a JSON object (no markdown fences, no prose) of exactly this form:
{{"texts": {{"1": "<condensed [TEXT 1] or array of block objects>", "2": "..."}}}}

--- SECTION START ---
{body}
--- SECTION END ---"""
    return [
        {"role": "system", "content": SYNTH_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
