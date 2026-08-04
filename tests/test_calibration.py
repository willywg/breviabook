"""Per-model condensation calibration — the table and the invariants it must keep."""

from __future__ import annotations

import pytest

from breviabook.condense.calibration import (
    _CALIBRATIONS,
    DEFAULT_CALIBRATION,
    UNDERSHOOT_WARN_FACTOR,
    calibration_for,
    is_calibrated,
)


def test_unknown_model_gets_the_neutral_default() -> None:
    """The whole point: a bias measured on one model is not evidence about another."""
    assert calibration_for("some/model-nobody-ran").ask_factor == 1.0
    assert calibration_for("some/model-nobody-ran") is DEFAULT_CALIBRATION


@pytest.mark.parametrize("model", [None, ""])
def test_missing_model_gets_the_default(model: str | None) -> None:
    """Callers that do not know the model yet — the estimator before a model is chosen."""
    assert calibration_for(model) is DEFAULT_CALIBRATION


def test_default_applies_no_correction() -> None:
    assert DEFAULT_CALIBRATION.ask_factor == 1.0


def test_no_prefix_matching() -> None:
    """`gemini-3.6-flash`'s number is not evidence about `-lite`, however similar the name."""
    assert calibration_for("gemini-3.6-flash").ask_factor == 0.85
    assert calibration_for("gemini-3.6-flash-lite") is DEFAULT_CALIBRATION
    assert calibration_for("gemini-3.5-flash-lite") is DEFAULT_CALIBRATION


def test_is_calibrated_matches_the_table() -> None:
    assert is_calibrated("gemini-3.6-flash")
    assert not is_calibrated("some/model-nobody-ran")
    assert not is_calibrated(None)


@pytest.mark.parametrize("model", sorted(_CALIBRATIONS))
def test_every_entry_is_plausible_and_sourced(model: str) -> None:
    """An entry is a claim that someone measured it — the note is where that is recorded."""
    cal = _CALIBRATIONS[model]
    # A factor outside this range is a measurement error or a model that should be
    # rejected rather than calibrated: it would mean asking for less than a third,
    # or more than double, of what the user said they wanted.
    assert 0.3 <= cal.ask_factor <= 2.0
    assert 1.0 <= cal.overshoot <= 2.0
    assert len(cal.note) > 40


def test_undershoot_warning_is_loose_enough_not_to_cry_wolf() -> None:
    """It has to survive normal run-to-run variance or nobody will read it.

    Flash's own measured band spans ~6 points around a 30% target; a threshold
    that fires inside that band fires on healthy runs.
    """
    assert 0.5 <= UNDERSHOOT_WARN_FACTOR <= 0.85
