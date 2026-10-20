"""Shared single-choice picker behavior, without book processing."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pickers import choose  # noqa: E402


class SinglePickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.select = MagicMock()
        self.questionary = SimpleNamespace(
            Choice=lambda label, **kwargs: SimpleNamespace(title=label, **kwargs),
            select=self.select,
        )

    def test_arrow_menu_selects_exactly_one_and_preserves_cancel_default(self):
        self.select.return_value.unsafe_ask.return_value = 1
        with (
            patch.dict(sys.modules, {"questionary": self.questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
        ):
            self.assertEqual(choose("What next?", ["Redo summary", "Cancel"], default=1), 1)
        choices = self.select.call_args.kwargs["choices"]
        self.assertEqual([choice.title for choice in choices], ["Redo summary", "Cancel"])
        self.assertEqual(self.select.call_args.kwargs["default"].value, 1)
        self.assertFalse(self.select.call_args.kwargs["use_shortcuts"])

    def test_menu_without_explicit_default_highlights_first_option(self):
        self.select.return_value.unsafe_ask.return_value = 0
        with (
            patch.dict(sys.modules, {"questionary": self.questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
        ):
            self.assertEqual(choose("Provider", ["OpenRouter", "OpenAI"]), 0)
        self.assertEqual(self.select.call_args.kwargs["default"].value, 0)

    def test_single_choice_rejects_multiple_or_invalid_selections(self):
        with (
            patch.dict(sys.modules, {"questionary": self.questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
        ):
            for answer in ([0, 1], True, -1, 9):
                self.select.return_value.unsafe_ask.return_value = answer
                with self.subTest(answer=answer), self.assertRaises(ValueError):
                    choose("What next?", ["Redo summary", "Cancel"])

    def test_ctrl_c_does_not_choose_default_or_redo_a_book(self):
        self.select.return_value.unsafe_ask.side_effect = KeyboardInterrupt
        with (
            patch.dict(sys.modules, {"questionary": self.questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
            self.assertRaises(KeyboardInterrupt),
        ):
            choose("What next?", ["Redo summary", "Cancel"], default=1)

    def test_redirected_selection_keeps_default_and_cancel_behavior(self):
        for answer, expected in (("", 1), ("1", 0), ("2", 1), ("q", None)):
            with (
                self.subTest(answer=answer),
                patch("pickers.sys.stdin.isatty", return_value=False),
                patch("builtins.input", return_value=answer),
            ):
                self.assertEqual(choose("What next?", ["Redo", "Cancel"], default=1), expected)

    def test_redirected_multiple_choices_are_rejected(self):
        with (
            patch("pickers.sys.stdin.isatty", return_value=False),
            patch("builtins.input", side_effect=["1,2", "", "0", "2"]) as read,
        ):
            self.assertEqual(choose("Provider", ["OpenRouter", "OpenAI"]), 1)
        self.assertEqual(read.call_count, 4)


if __name__ == "__main__":
    unittest.main()
