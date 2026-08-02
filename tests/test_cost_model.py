"""The cost model, and the measurements it stands on.

Two kinds of test live here. The first kind pins the constants to the artefacts
they were measured from — build a real prompt, count it, and fail if the constant
has drifted away from the thing it claims to describe. Those exist because the
number is shown to a user as money: a prompt edit that silently makes every
estimate wrong should be a red test, not a support ticket.

The second kind checks the shape of the arithmetic — that a bigger book costs
more, that translation costs more than not translating, that the model does not
quietly go back to predicting one pass per stage.
"""

from __future__ import annotations

from breviabook.condense.cost_model import (
    CONDENSE_PROMPT_OVERHEAD,
    ESTIMATE_SPREAD,
    JSON_ENVELOPE,
    SYNTH_PROMPT_OVERHEAD,
    estimate_condense_tokens,
    estimate_translate_only_tokens,
    spread,
)
from breviabook.condense.prompts import build_condense_messages, build_synthesize_messages
from breviabook.utils.tokens import count_tokens

# The book the constants were calibrated on, and what it really billed.
# Site Reliability Engineering, 479 pages, 30% target, gemini-3.6-flash.
SRE = {"input_tokens": 215_771, "chapters": 51, "chunks": 134, "target_ratio": 0.30}
SRE_ACTUAL_PROMPT = 557_808
SRE_ACTUAL_COMPLETION = 250_467


# --------------------------------------------------------------------------- #
# The constants, against the prompts they describe
# --------------------------------------------------------------------------- #


def test_condense_overhead_still_covers_the_real_prompt() -> None:
    """The fixed part of the condense prompt must fit inside the per-call constant.

    An empty body isolates the structure contract from the chunk's own text. The
    rest of the constant is [TEXT n]/[IMG]/marker scaffolding, which scales with
    the chunk, so the fixed part is necessarily smaller — but not by much, and if
    the contract ever grows past the whole constant the estimate is broken.
    """
    fixed = sum(count_tokens(m["content"]) for m in build_condense_messages("", 0.255, ["i1"]))
    assert fixed < CONDENSE_PROMPT_OVERHEAD, (
        f"the condense prompt's fixed part is now {fixed} tokens, at or above the "
        f"{CONDENSE_PROMPT_OVERHEAD}-token per-call constant — re-measure it"
    )
    # And it must not have collapsed either: a constant three times the real
    # prompt would overcharge every estimate.
    assert fixed > CONDENSE_PROMPT_OVERHEAD * 0.4


def test_synth_overhead_matches_the_real_synthesis_prompt() -> None:
    fixed = sum(count_tokens(m["content"]) for m in build_synthesize_messages("", 100))
    assert abs(fixed - SYNTH_PROMPT_OVERHEAD) < SYNTH_PROMPT_OVERHEAD * 0.35, (
        f"the synthesis prompt is now {fixed} tokens against a constant of "
        f"{SYNTH_PROMPT_OVERHEAD} — re-measure it"
    )


def test_the_envelope_is_above_one() -> None:
    """JSON around prose can only add tokens, never remove them."""
    assert JSON_ENVELOPE > 1.0


# --------------------------------------------------------------------------- #
# The model, against the run it was built from
# --------------------------------------------------------------------------- #


def test_the_model_lands_near_a_real_book_run() -> None:
    """The whole point: predict a measured bill, and miss high rather than low.

    This is the regression that matters. The formula this replaced predicted 0.56x
    of the real prompt tokens and 0.52x of the completion tokens on this exact
    book, which showed a user $1.44 for a run that cost $2.72.
    """
    passes = estimate_condense_tokens(**SRE)

    prompt_ratio = passes.prompt / SRE_ACTUAL_PROMPT
    completion_ratio = passes.completion / SRE_ACTUAL_COMPLETION
    assert 0.8 < prompt_ratio < 1.3, f"prompt off by {prompt_ratio:.2f}x"
    assert 0.8 < completion_ratio < 1.4, f"completion off by {completion_ratio:.2f}x"

    # Completion is priced ~5x input on Flash, so a low completion estimate is the
    # expensive kind of wrong. It must not come in under the real run.
    assert passes.completion >= SRE_ACTUAL_COMPLETION


def test_more_calls_than_one_per_stage() -> None:
    """The old model assumed chunks + chapters. Trim passes are real calls."""
    passes = estimate_condense_tokens(**SRE)
    assert passes.calls > SRE["chunks"] + SRE["chapters"]


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_translation_costs_more_than_not_translating() -> None:
    plain = estimate_condense_tokens(**SRE)
    translated = estimate_condense_tokens(**SRE, translate=True)
    assert translated.prompt > plain.prompt
    assert translated.completion > plain.completion
    assert translated.calls > plain.calls


def test_a_smaller_target_costs_less() -> None:
    """Less output to write, and less text for every downstream pass to read."""
    tight = estimate_condense_tokens(**{**SRE, "target_ratio": 0.20})
    loose = estimate_condense_tokens(**{**SRE, "target_ratio": 0.50})
    assert tight.completion < loose.completion
    assert tight.prompt < loose.prompt


def test_reading_the_book_is_the_floor() -> None:
    """Whatever else happens, the condense pass reads every input token once."""
    passes = estimate_condense_tokens(**SRE)
    assert passes.prompt > SRE["input_tokens"]


def test_translate_only_has_no_trim_loop() -> None:
    """Translation is one pass, so its call count is its batch count."""
    passes = estimate_translate_only_tokens(
        input_tokens=100_000, translatable_units=800, units_per_batch=40
    )
    assert passes.calls == 20
    assert passes.completion > 100_000  # expansion + envelope


def test_translate_only_with_nothing_to_translate() -> None:
    passes = estimate_translate_only_tokens(
        input_tokens=0, translatable_units=0, units_per_batch=40
    )
    assert passes.calls == 0
    assert passes.prompt == 0


def test_spread_brackets_the_point_estimate() -> None:
    low, high = spread(10.0)
    assert low < 10.0 < high
    assert (high - low) / 2 == 10.0 * ESTIMATE_SPREAD
