"""Tolerant JSON-object extraction from LLM responses.

LLMs often wrap JSON in prose or ```json fences. ``extract_json_object`` pulls the first
top-level object and parses it, raising ``ValueError`` on failure. Callers wrap the error in
their own domain exception as needed.
"""

from __future__ import annotations

import json


def extract_json_object(text: str) -> dict[str, object]:
    """Return the first top-level JSON object in ``text``.

    Raises:
        ValueError: if no parseable JSON object is present.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in model response")
    try:
        # strict=False tolerates literal control chars (newlines/tabs) inside strings, which
        # models often emit unescaped.
        obj = json.loads(text[start : end + 1], strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in model response: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Model response JSON is not an object")
    return obj
