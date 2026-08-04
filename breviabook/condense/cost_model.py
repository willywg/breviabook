"""What a condense run will cost, before it runs.

This is the arithmetic behind ``--dry-run`` and behind the price the SaaS shows a
user right before they spend their own provider key. It is deliberately a
separate, pure function: the CLI reaches it after parsing a file, the API reaches
it from a profile cached on a database row, and both must produce the same
number or the dry run is lying about the bill.

## Why the obvious formula was wrong

The first version predicted ``input + out`` prompt tokens and ``out * 2``
completion tokens — one clean pass per stage, priced by the model. Measured
against two full runs of a 479-page book it came in at **0.44x-0.53x of the real
bill**, and always low, which is the wrong direction to be wrong in when the
number is shown as money.

Four things were missing, none of them a fudge factor:

1. **Passes are not one per stage.** Synthesis re-runs a whole chapter when the
   first pass overshoots, so a book pays for more chapter generations than it has
   chapters.
2. **The prompt carries the structure contract.** The old per-call overhead of
   250 tokens came from the translate command, whose prompt has no such contract.
   Building the real condense messages for every chunk of two books gave 804 and
   965 tokens per call.
3. **The model does not emit prose, it emits JSON containing prose.** Rebuilding
   the reply the contract demands for all 134 chunks of one book and weighing it
   against :func:`block_tokens` gives a 1.53x envelope — on the completion side,
   which Gemini prices at 5x input.
4. **Condensation overshoots its target**, so every downstream pass reads and
   writes more than the target ratio implies.

With those folded in the same formula lands at **0.92x and 1.11x** of the two
real runs, slightly high rather than low.

## What these constants are, honestly

Every number below was measured rather than chosen, but measured on a small
sample — two runs of one book, plus an offline check of the prompt overhead on a
second. They are a first calibration, not a law. :data:`ESTIMATE_SPREAD` exists
because of that: the caller should show a range, and the range should be wide
enough to contain a book we have not tried.

Re-derive them when the pipeline changes shape. The prompt overheads are pinned
by ``tests/test_cost_model.py``, which builds a real prompt and fails if the
constant drifts away from it — so a prompt edit shows up as a red test rather
than as a quietly wrong price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from breviabook.condense.calibration import calibration_for

__all__ = [
    "ESTIMATE_SPREAD",
    "PassTokens",
    "estimate_condense_tokens",
    "estimate_translate_only_tokens",
    "spread",
]

# Per-call prompt cost of the condense contract itself, on top of the chunk's own
# text: the structure rules, the JSON shape, the [TEXT n]/[IMG]/marker scaffolding.
# Measured by building every real message for two books — 804 tokens/call on an
# EPUB (Site Reliability Engineering, 130 calls) and 965 on a PDF (The Go
# Programming Language, 133 calls). The spread between them is block density:
# a chunk full of code carries more structural markers.
CONDENSE_PROMPT_OVERHEAD: Final = 900

# The same for the per-chapter synthesis prompt, measured at 502 tokens against an
# empty body. Synthesis has no per-block markers to speak of, so unlike the
# condense figure this one barely moves between books.
SYNTH_PROMPT_OVERHEAD: Final = 500

# The model answers with JSON, and the JSON is what gets billed: object keys, the
# quoting and escaping of every string, and a "block": k address on every entry.
# Measured at 1.53x by reconstructing the contract-shaped reply for all 134 chunks
# of one book and weighing it against block_tokens() of the same prose.
JSON_ENVELOPE: Final = 1.55

# Condensation lands above the ratio it is asked for, which is why the condense
# prompt already asks for a fraction of the target. What is left after that
# correction is this: chapters reached synthesis at a mean of 1.29x their target.
# Everything downstream reads and writes that larger text, so the overshoot has to
# be in the estimate even though the pipeline is trying to remove it.
#
# Measured on Gemini Flash, and therefore per-model like the ask factor it pairs
# with — :mod:`breviabook.condense.calibration` holds both. This name stays as the
# figure an unmeasured model is priced with.
CONDENSE_OVERSHOOT: Final = 1.30

# A synthesis pass's output tracks the text it is given rather than the word count
# it is told: measured at 0.93x-1.00x of its input across eight chapters, and at
# 0.88x in aggregate across a full book once short chapters are included.
SYNTH_SHAVE: Final = 0.90

# Share of chapters whose first synthesis pass overshoots far enough to buy a trim
# pass. Measured at 25/51 chapters entering above the trim threshold on the run
# that had the Gemini ask factor applied.
TRIM_PASS_SHARE: Final = 0.50

# What one trim pass actually removes. It regenerates a whole chapter to shave
# this much, which is the reason max_trim_passes is 1.
TRIM_SHAVE: Final = 0.94

# Target-language expansion, EN→ES (also exported from pipeline for the CLI).
TRANSLATION_EXPANSION: Final = 1.15

# How far either side of the point estimate the caller should quote. The model's
# own error on the two runs it was built from is -8%/+11%; this is deliberately
# wider, because a book nobody has run is the normal case and the sample behind
# these constants is one book. Widen it, do not narrow it, until more books have
# been measured.
ESTIMATE_SPREAD: Final = 0.30


@dataclass(frozen=True)
class PassTokens:
    """Predicted provider traffic for one set of run options."""

    prompt: int
    completion: int
    calls: int


def estimate_condense_tokens(
    *,
    input_tokens: int,
    chapters: int,
    chunks: int,
    target_ratio: float,
    translate: bool = False,
    model: str | None = None,
) -> PassTokens:
    """Predict prompt/completion tokens for a condense run, pass by pass.

    ``translate`` adds the same-pass translation of the condensed result.

    ``model`` selects the per-model overshoot from
    :mod:`breviabook.condense.calibration`; omitting it prices the run with the
    uncalibrated default, which is what a caller that does not yet know the model
    should get.

    The shape follows the pipeline: one condense call per chunk, one synthesis
    call per chapter, and a trim pass on the share of chapters that overshoot.
    """
    target_tokens = input_tokens * target_ratio
    # What condensation really hands to synthesis, not what it was asked for.
    condensed = target_tokens * calibration_for(model).overshoot
    synthesized = condensed * SYNTH_SHAVE

    # Phase 4 — condense: reads every chunk once, writes the condensed form.
    condense_prompt = input_tokens + CONDENSE_PROMPT_OVERHEAD * chunks
    condense_completion = condensed * JSON_ENVELOPE

    # Phase 5 — synthesis: reads the condensed chapter, writes a tighter one.
    synth_prompt = condensed + SYNTH_PROMPT_OVERHEAD * chapters
    synth_completion = synthesized * JSON_ENVELOPE

    # Length control — a whole extra chapter generation, for the chapters that
    # need it. The pipeline runs at most one such pass per chapter.
    trims = chapters * TRIM_PASS_SHARE
    trim_prompt = synthesized * TRIM_PASS_SHARE + SYNTH_PROMPT_OVERHEAD * trims
    trim_completion = synthesized * TRIM_PASS_SHARE * TRIM_SHAVE * JSON_ENVELOPE

    prompt = condense_prompt + synth_prompt + trim_prompt
    completion = condense_completion + synth_completion + trim_completion
    calls = chunks + chapters + trims

    if translate:
        # Translation runs over the finished condensed text. It is batched, and the
        # batch count after condensing is not known without re-chunking, so the
        # per-batch overhead is charged per chapter — the coarsest term in here,
        # and a small one next to the condense pass.
        final = synthesized * TRIM_SHAVE
        prompt += final + SYNTH_PROMPT_OVERHEAD * chapters
        completion += final * TRANSLATION_EXPANSION * JSON_ENVELOPE
        calls += chapters

    return PassTokens(round(prompt), round(completion), round(calls))


def estimate_translate_only_tokens(
    *, input_tokens: int, translatable_units: int, units_per_batch: int
) -> PassTokens:
    """Predict prompt/completion tokens for a translate-only run.

    Translation is a single pass with no length-control loop, so this stays close
    to the original arithmetic — the JSON envelope is the one correction, and it
    applies here for the same reason it applies everywhere: the reply is a JSON
    object of translated segments, not bare prose.
    """
    batches = math.ceil(translatable_units / units_per_batch) if translatable_units else 0
    prompt = input_tokens + SYNTH_PROMPT_OVERHEAD * batches
    completion = input_tokens * TRANSLATION_EXPANSION * JSON_ENVELOPE
    return PassTokens(round(prompt), round(completion), batches)


def spread(value: float) -> tuple[float, float]:
    """The honest range around a point estimate: ``(low, high)``."""
    return value * (1 - ESTIMATE_SPREAD), value * (1 + ESTIMATE_SPREAD)
