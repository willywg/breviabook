"""CLI pipeline wiring: end-to-end condensation with the mock provider."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from breviabook.llm.base import Message
from breviabook.llm.usage import Usage
from breviabook.parsers.epub_parser import EpubParser
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.pipeline import (
    condense_book,
    estimate_condense,
    validate_formats,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def _structured_array_for_run(run_body: str, run_id: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    pattern = re.compile(
        r"\[BLOCK (\d+) type=(paragraph|list|quote)(?: ordered=(true|false))?\]\n"
        r"(.*?)(?=\n\[BLOCK |\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(run_body + "\n"):
        btype = match.group(2)
        block_body = match.group(4).strip()
        suffix = f"{run_id}-{match.group(1)}"
        if btype == "paragraph":
            out.append({"type": "paragraph", "text": f"c{suffix}"})
        elif btype == "quote":
            out.append({"type": "quote", "text": f"q{suffix}"})
        else:
            items: list[str] = []
            for line in block_body.splitlines():
                line = line.strip()
                if line.startswith("- ") or re.match(r"\d+\.\s", line):
                    items.append(f"c{len(items) + 1}")
            if not items:
                items = ["c1"]
            out.append({"type": "list", "items": items})
    return out


def _reply_for_run(run_body: str, run_id: str) -> str | list[dict[str, object]]:
    if re.search(r"\[BLOCK \d+ type=(list|quote)\b", run_body):
        return _structured_array_for_run(run_body, run_id)
    return f"c{run_id}"


def _adaptive_texts_reply(content: str) -> dict[str, object]:
    """Build condense/synth JSON texts keyed by [TEXT n], preserving list/quote structure."""
    body_match = re.search(
        r"--- (?:EXCERPT|SECTION) START ---\n(.*)\n--- (?:EXCERPT|SECTION) END ---",
        content,
        re.DOTALL,
    )
    if not body_match:
        return {}
    body = body_match.group(1)
    texts: dict[str, object] = {}
    markers = list(re.finditer(r"\[TEXT (\d+)\]\n", body))
    for index, match in enumerate(markers):
        run_id = match.group(1)
        start = match.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        texts[run_id] = _reply_for_run(body[start:end].strip(), run_id)
    return texts


def _adaptive_condense_json(content: str) -> str:
    return json.dumps(
        {"texts": _adaptive_texts_reply(content), "essential_images": ["fig1"]},
        ensure_ascii=False,
    )


class ScriptedProvider:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return self.reply


def test_validate_formats_normalizes() -> None:
    assert validate_formats(["MD", "epub", "md"]) == ["md", "epub"]
    with pytest.raises(ValueError):
        validate_formats(["docx"])
    with pytest.raises(ValueError):
        validate_formats([])


async def test_end_to_end_writes_md_and_epub(tmp_path: Path) -> None:
    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md", "epub"],
        provider=AdaptiveProvider(),
        model="m",
    )
    names = {p.name for p in result.output_files}
    assert names == {"sample-condensed.md", "sample-condensed.epub"}
    for p in result.output_files:
        assert p.exists() and p.stat().st_size > 0
    # The produced EPUB is itself parseable.
    reparsed = EpubParser().parse(tmp_path / "sample-condensed.epub")
    assert reparsed.chapters
    assert result.input_tokens > 0


async def test_unsupported_input_raises(tmp_path: Path) -> None:
    fake = tmp_path / "book.txt"
    fake.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported input format"):
        await condense_book(
            input_path=fake,
            out_dir=tmp_path,
            formats=["md"],
            provider=AdaptiveProvider(),
            model="m",
        )


class AdaptiveProvider:
    """Returns block-aligned condense/synth JSON derived from the prompt body."""

    name = "adaptive"

    def __init__(self) -> None:
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return _adaptive_condense_json(messages[-1]["content"])


class PhaseAwareProvider:
    """Counts condense-phase calls separately (synthesis still runs on resume)."""

    name = "phase-aware"

    def __init__(self) -> None:
        self.condense_calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        if messages[-1]["content"].startswith("Condense the following book excerpt"):
            self.condense_calls += 1
        return _adaptive_condense_json(messages[-1]["content"])


async def test_resume_skips_provider_for_done_chunks(tmp_path: Path) -> None:
    # Real end-to-end resume: two full condense_book calls, the second with resume=True.
    provider1 = PhaseAwareProvider()
    result1 = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider1,
        model="m",
        resume=False,
    )
    assert result1.chunks_total > 0
    assert provider1.condense_calls == result1.chunks_total
    assert result1.chunks_reused == 0

    provider2 = PhaseAwareProvider()
    result2 = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider2,
        model="m",
        resume=True,
    )
    assert provider2.condense_calls == 0  # every chunk came from the checkpoint
    assert result2.chunks_reused == result2.chunks_total


async def test_resume_with_changed_target_ratio_recomputes(tmp_path: Path) -> None:
    provider1 = PhaseAwareProvider()
    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider1,
        model="m",
        target_ratio=0.30,
        resume=False,
    )

    # Resume with a different ratio: the fingerprint invalidates every record.
    provider2 = PhaseAwareProvider()
    result2 = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider2,
        model="m",
        target_ratio=0.50,
        resume=True,
    )
    assert result2.chunks_reused == 0
    assert provider2.condense_calls == result2.chunks_total


async def test_fresh_run_clears_stale_checkpoint(tmp_path: Path) -> None:
    cp_path = tmp_path / ".breviabook" / "sample-condensed.jsonl"
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    cp_path.write_text('{"chunk_id": "stale", "result": {"id": "stale"}}\n', encoding="utf-8")

    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=AdaptiveProvider(),
        model="m",
        resume=False,  # should clear the stale entry
    )
    assert not CheckpointManager(cp_path).is_done("stale")


def test_estimate_no_llm(tmp_path: Path) -> None:
    est = estimate_condense(FIXTURE, chunk_tokens=2000, target_ratio=0.3)
    assert est.input_tokens > 0
    assert est.chapters == 2
    assert est.chunks >= 2
    assert est.estimated_output_tokens == round(est.input_tokens * 0.3)


PDF_FIXTURE = Path(__file__).parent / "fixtures" / "sample.pdf"


async def test_pdf_input_end_to_end(tmp_path: Path) -> None:
    # The PDF has an outline, so no TOC inference is needed; condense to Markdown.
    await condense_book(
        input_path=PDF_FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=AdaptiveProvider(),
        model="m",
    )
    out = tmp_path / "sample-condensed.md"
    assert out.exists()
    assert "Chapter One" in out.read_text(encoding="utf-8")


def test_estimate_on_pdf_no_llm() -> None:
    est = estimate_condense(PDF_FIXTURE, chunk_tokens=2000, target_ratio=0.3)
    assert est.chapters == 2
    assert est.input_tokens > 0


def test_estimate_includes_token_breakdown() -> None:
    base = estimate_condense(FIXTURE, target_ratio=0.3)
    assert base.estimated_prompt_tokens > 0
    assert base.estimated_completion_tokens > 0
    assert base.estimated_cost_usd is None  # no provider/model given -> no cost
    # Translation adds an extra pass -> more estimated completion tokens.
    translated = estimate_condense(FIXTURE, target_ratio=0.3, translate_to="Spanish")
    assert translated.estimated_completion_tokens > base.estimated_completion_tokens


class RoutingProvider:
    """Returns a translate reply for translate prompts, else a condense/synth reply."""

    name = "routing"
    usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        content = messages[-1]["content"]
        if '"translations"' in content:
            return json.dumps({"translations": {str(i): f"ES{i}" for i in range(1, 30)}})
        return _adaptive_condense_json(content)


async def test_translation_end_to_end(tmp_path: Path) -> None:
    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=RoutingProvider(),
        model="m",
        translate_to="Spanish",
    )
    text = (tmp_path / "sample-condensed.md").read_text(encoding="utf-8")
    assert "ES" in text  # translated content present
    assert "```" in text  # code fence preserved (untranslated)


class VisionRoutingProvider(RoutingProvider):
    """RoutingProvider that also drops images via vision ranking (score 0)."""

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        return json.dumps({"score": 0.0, "essential": False})


async def test_rank_images_drops_via_vision(tmp_path: Path) -> None:
    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=VisionRoutingProvider(),
        model="m",
        rank_images=True,
    )
    # The fixture's only image scores 0 -> dropped -> no images/ dir written.
    text = (tmp_path / "sample-condensed.md").read_text(encoding="utf-8")
    assert "![" not in text
    assert not (tmp_path / "images").exists()


async def test_rank_images_requires_vision_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="vision-capable"):
        await condense_book(
            input_path=FIXTURE,
            out_dir=tmp_path,
            formats=["md"],
            provider=ScriptedProvider(
                json.dumps({"texts": {"1": "c"}, "essential_images": ["fig1"]})
            ),
            model="m",
            rank_images=True,
        )


async def test_pdf_render_failure_is_skipped_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If PDF rendering fails (e.g. weasyprint's system libs are missing), skip just that format
    # and keep the others, with a warning — never discard the whole already-paid-for run.
    from breviabook import pipeline as pl

    real = pl._renderer_for

    class BoomPdf:
        name = "pdf"

        def render(self, doc: object, out_dir: Path, *, stem: str = "x") -> Path:
            raise RuntimeError("weasyprint system libraries missing")

    monkeypatch.setattr(pl, "_renderer_for", lambda fmt: BoomPdf() if fmt == "pdf" else real(fmt))
    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md", "pdf"],
        provider=AdaptiveProvider(),
        model="m",
    )
    assert {p.name for p in result.output_files} == {"sample-condensed.md"}  # pdf skipped
    assert any("pdf: skipped" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Translate-only pipeline tests (feat/translate-command)
# --------------------------------------------------------------------------- #

_TRANSLATE_REPLY = json.dumps({"translations": {str(i): f"ES{i}" for i in range(1, 60)}})


class TranslateOnlyProvider:
    name = "translate-only"

    def __init__(self) -> None:
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return _TRANSLATE_REPLY


async def test_translate_only_no_condense_calls(tmp_path: Path) -> None:
    provider = TranslateOnlyProvider()
    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider,
        model="m",
        translate_only=True,
        translate_to="Spanish",
    )
    assert result.translate_only
    # The fixture has 2 short chapters; depending on exact token count, verify
    # there are zero condense/synthesize phases (translate-only path was taken).
    assert result.chunks_total == 0
    assert result.chunks_reused == 0
    assert result.output_files


async def test_translate_only_requires_translate_to(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="translate_only requires translate_to"):
        await condense_book(
            input_path=FIXTURE,
            out_dir=tmp_path,
            formats=["md"],
            provider=AdaptiveProvider(),
            model="m",
            translate_only=True,
        )


async def test_translate_only_preserves_code_and_images(tmp_path: Path) -> None:
    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=TranslateOnlyProvider(),
        model="m",
        translate_only=True,
        translate_to="Spanish",
    )
    text = (tmp_path / "sample-spanish.md").read_text(encoding="utf-8")
    # Code blocks must be preserved (the fixture has a code block).
    assert "```" in text


async def test_translate_only_resume_skips_done_batches(tmp_path: Path) -> None:
    cp_path = tmp_path / ".breviabook" / "sample-spanish.jsonl"
    provider1 = TranslateOnlyProvider()
    await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider1,
        model="m",
        translate_only=True,
        translate_to="Spanish",
        resume=False,
    )
    first_calls = provider1.calls
    assert first_calls > 0
    assert cp_path.exists()

    # Second run with --resume: no new LLM calls.
    provider2 = TranslateOnlyProvider()
    result2 = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=provider2,
        model="m",
        translate_only=True,
        translate_to="Spanish",
        resume=True,
    )
    assert provider2.calls == 0
    assert result2.batches_reused == first_calls


async def test_translate_only_image_selector_still_runs(tmp_path: Path) -> None:
    # The fixture has an image. Even though nothing was condensed, ImageSelector
    # must run to strip dangling ImageBlocks.
    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=TranslateOnlyProvider(),
        model="m",
        translate_only=True,
        translate_to="Spanish",
    )
    assert result.output_files


async def test_translate_only_untranslated_warning(tmp_path: Path) -> None:
    # A provider that fails to parse on the first batch triggers untranslated warning.
    class FailingProvider:
        name = "failing"
        usage = Usage()

        async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
            return "not json"

    result = await condense_book(
        input_path=FIXTURE,
        out_dir=tmp_path,
        formats=["md"],
        provider=FailingProvider(),
        model="m",
        translate_only=True,
        translate_to="Spanish",
    )
    assert any("untranslated" in w.lower() for w in result.warnings)


def test_estimate_translate_only() -> None:
    est = estimate_condense(FIXTURE, translate_only=True, translate_to="Spanish")
    assert est.chunks == 0
    assert est.translatable_units > 0
    assert est.batches > 0
    assert est.estimated_output_tokens > est.input_tokens  # expansion
    assert "compression" not in str(est).lower()  # no compression term here
    # Verify the expansion factor is applied.
    expected_output = round(est.input_tokens * 1.15)
    # Allow small rounding difference.
    assert abs(est.estimated_output_tokens - expected_output) <= 50


# --- Four-phase resume: no paid work repeats (feat--checkpoint-remaining-phases) ------- #


class AllPhaseProvider:
    """Counts LLM calls per phase (condense / synthesis / translate / vision) separately."""

    name = "all-phase"

    def __init__(self) -> None:
        self.condense_calls = 0
        self.synth_calls = 0
        self.translate_calls = 0
        self.vision_calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        content = messages[-1]["content"]
        if content.startswith("Condense the following book excerpt"):
            self.condense_calls += 1
        elif content.startswith(("Smooth", "Condense further")):
            self.synth_calls += 1
        if '"translations"' in content:
            self.translate_calls += 1
            return json.dumps({"translations": {str(i): f"ES{i}" for i in range(1, 80)}})
        return _adaptive_condense_json(content)

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        self.vision_calls += 1
        return json.dumps({"score": 0.9, "essential": True})


# chunk_tokens=20 splits the fixture's chapters into 3 + 2 chunks, so synthesis actually
# runs (a single-chunk chapter would skip it and make the reuse assertion vacuous).
_FOUR_PHASE = dict(
    input_path=FIXTURE,
    formats=["md"],
    model="m",
    chunk_tokens=20,
    translate_to="Spanish",
    rank_images=True,
)


async def test_full_run_resume_makes_zero_calls_in_all_four_phases(tmp_path: Path) -> None:
    first = AllPhaseProvider()
    r1 = await condense_book(out_dir=tmp_path, provider=first, resume=False, **_FOUR_PHASE)
    # First run exercises every phase — otherwise the resume assertion proves nothing.
    assert first.condense_calls > 0
    assert first.synth_calls > 0
    assert first.translate_calls > 0
    assert first.vision_calls > 0

    second = AllPhaseProvider()
    r2 = await condense_book(out_dir=tmp_path, provider=second, resume=True, **_FOUR_PHASE)
    assert (
        second.condense_calls,
        second.synth_calls,
        second.translate_calls,
        second.vision_calls,
    ) == (0, 0, 0, 0)
    # Reuse counters reflect that every unit came from the checkpoint.
    assert r2.chunks_reused == r1.chunks_total
    assert r2.chapters_reused > 0
    assert r2.images_reused > 0
    assert r2.batches_reused > 0


async def test_changed_model_recomputes_all_four_phases(tmp_path: Path) -> None:
    await condense_book(out_dir=tmp_path, provider=AllPhaseProvider(), resume=False, **_FOUR_PHASE)
    # Resume with a different model: every phase fingerprint includes model, so all recompute.
    args = {**_FOUR_PHASE, "model": "other-model"}
    p = AllPhaseProvider()
    await condense_book(out_dir=tmp_path, provider=p, resume=True, **args)
    assert p.condense_calls > 0
    assert p.synth_calls > 0
    assert p.translate_calls > 0
    assert p.vision_calls > 0


async def test_checkpoint_keys_are_namespaced_by_phase(tmp_path: Path) -> None:
    await condense_book(out_dir=tmp_path, provider=AllPhaseProvider(), resume=False, **_FOUR_PHASE)
    cp_path = tmp_path / ".breviabook" / "sample-condensed.jsonl"
    keys = set(CheckpointManager(cp_path).results())
    assert keys, "checkpoint should hold records from all phases"
    for key in keys:
        # Collision-free by construction: bare condense ids never contain ':'; every other
        # phase carries a prefix ending in one.
        assert re.fullmatch(r"ch\d+-\d+", key) or key.startswith(("syn:", "tr:", "img:")), key
    # All four phases actually wrote something.
    assert any(re.fullmatch(r"ch\d+-\d+", k) for k in keys)
    assert any(k.startswith("syn:") for k in keys)
    assert any(k.startswith("tr:") for k in keys)
    assert any(k.startswith("img:") for k in keys)
