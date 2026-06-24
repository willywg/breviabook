"""Prompt templates for condensation (ROADMAP §7.1, §7.3).

The contract is JSON-in/JSON-out: we present the chunk as labeled segments and ask the model
to return condensed text per ``[TEXT n]`` run plus the ids of essential images. Code is shown
for context but explicitly preserved by us, not the model.
"""

from __future__ import annotations

from brevia.llm.base import Message

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
- Preserve technical identifiers, API names, file paths, and URLs exactly.
- Do NOT reproduce code blocks; they are preserved automatically. Condense prose only.
- Images appear as [IMG:id — "caption"]. Decide which are ESSENTIAL to understand the retained
  content and list only those ids in "essential_images". Omit decorative or redundant images.
- Do not add commentary, headings, or content that was not present.

Return ONLY a JSON object (no markdown fences, no prose) of exactly this form:
{{"texts": {{"1": "<condensed text for [TEXT 1]>", "2": "..."}}, "essential_images": ["id"]}}

Available image ids: {available}

--- EXCERPT START ---
{body}
--- EXCERPT END ---"""
    return [
        {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
