"""Usage accounting: accumulation, extraction, and provider/pipeline surfacing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from breviabook.llm.base import Message
from breviabook.llm.key_pool import KeyPool
from breviabook.llm.providers.openai import OpenAIProvider
from breviabook.llm.usage import Usage, extract_usage
from breviabook.pipeline import condense_book

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def _response(text: str, prompt: int, completion: int, cached: int = 0) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def test_usage_add_and_totals() -> None:
    u = Usage()
    u.add(10, 5, 2, 0.01)
    u.add(20, 8, 0, 0.02)
    assert u.prompt_tokens == 30
    assert u.completion_tokens == 13
    assert u.cached_tokens == 2
    assert u.total_tokens == 43
    assert u.calls == 2
    assert round(u.cost_usd, 2) == 0.03


def test_extract_usage_from_dict() -> None:
    assert extract_usage(_response("x", 12, 4, 3)) == (12, 4, 3)


def test_extract_usage_missing_is_zero() -> None:
    assert extract_usage({"choices": [{"message": {"content": "x"}}]}) == (0, 0, 0)


async def test_provider_accumulates_usage() -> None:
    async def completer(**kwargs: Any) -> dict[str, Any]:
        return _response("ok", prompt=100, completion=20, cached=10)

    provider = OpenAIProvider(KeyPool(["k"]), completer=completer)
    await provider.generate([{"role": "user", "content": "a"}], "gpt-4o")
    await provider.generate([{"role": "user", "content": "b"}], "gpt-4o")
    assert provider.usage.calls == 2
    assert provider.usage.prompt_tokens == 200
    assert provider.usage.completion_tokens == 40
    assert provider.usage.cached_tokens == 20
    assert provider.usage.cost_usd == 0.0  # injected completer skips cost


async def test_pipeline_surfaces_usage(tmp_path: Path) -> None:
    import json

    reply = json.dumps({"texts": {"1": "c"}, "essential_images": []})

    async def completer(**kwargs: Any) -> dict[str, Any]:
        return _response(reply, prompt=50, completion=10)

    provider = OpenAIProvider(KeyPool(["k"]), completer=completer)
    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider,
        model="gpt-4o",
    )
    assert result.usage is not None
    assert result.usage.calls > 0
    assert result.usage.prompt_tokens > 0


async def test_pipeline_surfaces_empty_usage_when_fake_does_not_accumulate(
    tmp_path: Path,
) -> None:
    import json

    class PlainProvider:
        name = "plain"

        def __init__(self) -> None:
            self.usage = Usage()

        async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
            return json.dumps({"texts": {"1": "c"}, "essential_images": []})

    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=PlainProvider(),
        model="m",
    )
    assert result.usage is not None
    assert result.usage.calls == 0  # fake never calls usage.add
