"""Per-model condensation calibration.

Condensation does not land on the ratio it is asked for, and *how far off* it
lands is a property of the model, not of the pipeline. Gemini Flash returns more
than asked; MiMo returned a third less; `gpt-5.6-luna` returns about half again
as much. Correcting all three with one constant means correcting two of them in
the wrong direction.

So the correction lives here, keyed by model, and an **unmeasured model gets no
correction at all** — it is asked for exactly the target. That default is the
point: a bias measured on one model is not evidence about another, and pretending
otherwise is what this module exists to stop. Adding a model here is a claim that
someone ran it and read the number.

## The two numbers, and why they are separate

``ask_factor`` is what the *prompt* asks for, as a fraction of the user's target.
It is measured end-to-end: run the book, divide the final ratio by the ratio that
was actually asked, and invert. Under-asking is deliberate for a model that
overshoots.

``overshoot`` is what *synthesis receives*, as a multiple of the target, and it
feeds :mod:`breviabook.condense.cost_model` rather than any prompt. It is a
different measurement point, taken mid-pipeline, and it does not follow from
``ask_factor`` — a perfectly calibrated model can still hand synthesis a chapter
well above target and get trimmed back down. Keep them independent until someone
measures both for the same model on the same run.

## Nobody lands on the target

Both calibrated models overshoot on a full book: Flash asks 25.5% and returns
34%, luna asks 20.1% and returns 39%. Calibration narrows the gap, it does not
close it, and the remaining gap is in the safe direction. Do not read an entry
here as a promise that a run will hit ``target_ratio`` — the estimate quotes a
range for this reason.

## Which miss is worse

Overshooting leaves the reader more of their book than they asked for.
Undershooting deletes it, and the deletion is invisible: every structural metric
stays clean while whole arguments go missing. They are not symmetric mistakes, so
where a measurement is uncertain, round the ask factor **up**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_CALIBRATION",
    "ModelCalibration",
    "UNDERSHOOT_WARN_FACTOR",
    "calibration_for",
    "is_calibrated",
]


@dataclass(frozen=True)
class ModelCalibration:
    """How one model behaves against a condensation target.

    ``note`` is not decoration — it records the sample a number came from, which
    is the only way a later reader can tell a full-book calibration from an
    eight-chunk guess.
    """

    ask_factor: float
    overshoot: float
    note: str


# What a model we have never measured gets: ask for exactly the target, and price
# it with the one overshoot figure we have. Both are honest admissions of
# ignorance rather than estimates — the ask factor applies no correction, and the
# overshoot errs high so the quoted price does not come in under the bill.
DEFAULT_CALIBRATION: Final = ModelCalibration(
    ask_factor=1.0,
    overshoot=1.30,
    note="uncalibrated — asked for the target exactly, priced with the Gemini overshoot",
)

# Exact model ids only. A prefix rule would quietly extend one model's measurement
# to its whole family, which is the mistake this module was written to undo:
# 0.85 was derived on `gemini-3.6-flash` and is not evidence about `-lite` or
# `-pro`, however similar the names look.
_CALIBRATIONS: Final[dict[str, ModelCalibration]] = {
    "gemini-3.6-flash": ModelCalibration(
        ask_factor=0.85,
        overshoot=1.30,
        note=(
            "Site Reliability Engineering, 479 pages, full run: chapters reached "
            "synthesis at 1.11x-1.41x of target (median 1.22x) from a prompt asking "
            "for the target exactly. 0.85 rather than the 0.82 that would centre the "
            "correction, because overshooting is the safer miss. The only entry here "
            "derived from a whole book."
        ),
    ),
    "openai/gpt-5.6-luna": ModelCalibration(
        ask_factor=0.67,
        overshoot=1.30,
        note=(
            "Site Reliability Engineering, 479 pages, two full runs at different asks: "
            "0.67 (asking 20.1%) returned 39.0%, and 0.40 (asking 12.0%) returned "
            "34.8% — Flash's own 34.0%. Held at 0.67 anyway, because the tighter ask "
            "buys length by dropping figures: 60 images survive at 0.67 and 44 at "
            "0.40, against Flash's 58, with prose and section structure identical in "
            "both. Squeezing this model shows up in the diagrams before it shows up "
            "in the sentences, and diagrams are what this product promises to keep. "
            "The overshoot figure is still Gemini's — nobody has measured luna's "
            "synthesis input."
        ),
    ),
}

# How far below target a finished run has to land before it is worth saying so.
# The engine already warns when output is *longer* than input; the opposite miss
# is the one that costs the reader content, and it was silent until now. 0.75 is
# deliberately loose — a run at 22% against a 30% target is within the noise of
# several models here, and a warning that fires on normal runs is one nobody
# reads.
UNDERSHOOT_WARN_FACTOR: Final = 0.75


def calibration_for(model: str | None) -> ModelCalibration:
    """The calibration for ``model``, or the neutral default when it is unmeasured."""
    if not model:
        return DEFAULT_CALIBRATION
    return _CALIBRATIONS.get(model, DEFAULT_CALIBRATION)


def is_calibrated(model: str | None) -> bool:
    """Whether ``model`` has a measured entry, as opposed to falling back to the default."""
    return bool(model) and model in _CALIBRATIONS
