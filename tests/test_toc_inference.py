"""Phase 8: LLM TOC inference (mock provider)."""

from __future__ import annotations

import json

import pytest

from breviabook.llm.base import Message
from breviabook.parsers.pdf_parser import TocEntry
from breviabook.parsers.toc_inference import build_toc_messages, infer_toc


class ScriptedProvider:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return self.reply


def test_build_toc_messages_has_page_markers() -> None:
    messages = build_toc_messages("[PAGE 0]\nhello", 1)
    assert messages[0]["role"] == "system"
    assert "start_page" in messages[1]["content"]
    assert "[PAGE 0]" in messages[1]["content"]


async def test_infer_toc_returns_entries() -> None:
    reply = json.dumps(
        {"chapters": [{"title": "Intro", "start_page": 0}, {"title": "Two", "start_page": 2}]}
    )
    entries = await infer_toc(ScriptedProvider(reply), "m", ["p0", "p1", "p2"])
    assert entries == [TocEntry("Intro", 0), TocEntry("Two", 2)]


async def test_infer_toc_skips_malformed_entries() -> None:
    reply = json.dumps(
        {"chapters": [{"title": "Ok", "start_page": 1}, {"title": "NoPage"}, {"start_page": 3}]}
    )
    entries = await infer_toc(ScriptedProvider(reply), "m", ["p0", "p1"])
    assert entries == [TocEntry("Ok", 1)]


async def test_infer_toc_empty_pages_no_call() -> None:
    provider = ScriptedProvider("{}")
    entries = await infer_toc(provider, "m", [])
    assert entries == []
    assert provider.calls == 0


async def test_infer_toc_invalid_json_raises() -> None:
    with pytest.raises(ValueError):
        await infer_toc(ScriptedProvider("not json"), "m", ["p0"])
