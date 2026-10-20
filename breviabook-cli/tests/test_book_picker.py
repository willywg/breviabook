"""Book selection behavior without Docker, paid requests, or a real terminal."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book_picker import select_books  # noqa: E402


class BookPickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.labels = ["Book A [New]", "Book B [Resume]", "Book C [Completed]"]
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_checkbox_starts_unchecked_and_returns_selection_in_book_order(self):
        checkbox = MagicMock()
        checkbox.return_value.unsafe_ask.return_value = [2, 0]
        questionary = SimpleNamespace(
            Choice=lambda label, **kwargs: SimpleNamespace(title=label, **kwargs),
            checkbox=checkbox,
        )
        with (
            patch.dict(sys.modules, {"questionary": questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
        ):
            self.assertEqual(select_books(self.labels), [0, 2])
        choices = checkbox.call_args.kwargs["choices"]
        self.assertEqual([choice.title for choice in choices], self.labels)
        self.assertFalse(any(choice.checked for choice in choices))
        self.assertEqual([choice.value for choice in choices], [0, 1, 2])

    def test_checkbox_allows_none_for_rescan_and_cancel_for_quit(self):
        questionary = MagicMock()
        with (
            patch.dict(sys.modules, {"questionary": questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
        ):
            questionary.checkbox.return_value.unsafe_ask.return_value = []
            self.assertEqual(select_books(self.labels), [])
            questionary.checkbox.return_value.unsafe_ask.return_value = None
            self.assertIsNone(select_books(self.labels))

    def test_checkbox_ctrl_c_aborts_without_submitting(self):
        questionary = MagicMock()
        questionary.checkbox.return_value.unsafe_ask.side_effect = KeyboardInterrupt
        with (
            patch.dict(sys.modules, {"questionary": questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
            self.assertRaises(KeyboardInterrupt),
        ):
            select_books(self.labels)

    def test_missing_checkbox_dependency_reports_the_install_command(self):
        with (
            patch.dict(sys.modules, {"questionary": None}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
            self.assertRaisesRegex(RuntimeError, "requirements.txt"),
        ):
            select_books(self.labels)

    def test_redirected_input_defaults_to_none_and_supports_explicit_selections(self):
        for answer, expected in (
            ("", []),
            ("all", [0, 1, 2]),
            ("3,1-2,1", [0, 1, 2]),
            ("1,3", [0, 2]),
            ("2", [1]),
            ("r", []),
            ("q", None),
        ):
            with (
                self.subTest(answer=answer),
                patch("pickers.sys.stdin.isatty", return_value=False),
                patch("builtins.input", return_value=answer),
            ):
                self.assertEqual(select_books(self.labels), expected)

    def test_invalid_numbers_are_rejected_without_processing_any_books(self):
        with (
            patch("pickers.sys.stdin.isatty", return_value=False),
            patch("builtins.input", side_effect=["0", "9", "3-1", "1-2-3", "text", "q"]) as read,
        ):
            self.assertIsNone(select_books(self.labels))
        self.assertEqual(read.call_count, 6)


if __name__ == "__main__":
    unittest.main()
