"""Common single-choice and checkbox pickers, with redirected-input fallbacks."""

from __future__ import annotations

import sys
from types import ModuleType


def _questionary() -> ModuleType:
    try:
        import questionary
    except ImportError as exc:
        raise RuntimeError(
            "The terminal pickers need Questionary. Install the launcher dependencies:\n"
            "  python -m pip install -r breviabook-cli/requirements.txt"
        ) from exc
    return questionary


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose(prompt: str, labels: list[str], *, default: int | None = None) -> int | None:
    """Select exactly one option; None means cancel."""
    if not labels or (default is not None and not 0 <= default < len(labels)):
        raise ValueError("Provide choices and a valid default selection.")
    if _interactive():
        questionary = _questionary()
        choices = [questionary.Choice(label, value=index) for index, label in enumerate(labels)]
        answer = questionary.select(
            prompt,
            choices=choices,
            default=choices[default if default is not None else 0],
            use_shortcuts=False,
            instruction="(arrows move, Enter selects, Ctrl+C quits)",
        ).unsafe_ask()
        if answer is None:
            return None
        if type(answer) is not int or not 0 <= answer < len(labels):
            raise ValueError("Invalid single selection.")
        return answer

    print(f"\n{prompt}")
    for index, label in enumerate(labels, 1):
        print(f"  {index}. {label}")
    print("  q. Cancel / quit")
    while True:
        suffix = f" [{default + 1}]" if default is not None else ""
        answer = input(f"Select{suffix}: ").strip().lower()
        if answer == "q":
            return None
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(labels):
            return int(answer) - 1
        print("Enter a listed number, or q.")


def select_many(prompt: str, labels: list[str]) -> list[int] | None:
    """Return checked indices; none start checked, empty means rescan, None means quit."""
    if _interactive():
        questionary = _questionary()
        print("\nSelect books with Space, then Enter. Submit an empty selection to rescan input.")
        answer = questionary.checkbox(
            prompt,
            choices=[
                questionary.Choice(label, value=index, checked=False)
                for index, label in enumerate(labels)
            ],
            instruction="(arrows move, space toggle, a all, i invert, enter queue, Ctrl+C quit)",
        ).unsafe_ask()
        if answer is None:
            return None
        # Questionary's public return type is Any; keep that at this UI boundary.
        if not isinstance(answer, list) or any(type(index) is not int for index in answer):
            raise ValueError("Invalid book selection.")
        return [index for index in range(len(labels)) if index in answer]

    print(f"\n{prompt} (none selected by default)")
    for index, label in enumerate(labels, 1):
        print(f"  [ ] {index}. {label}")
    while True:
        answer = (
            input("Numbers/ranges (1,3-5); all = select all; Enter/r = rescan; q = quit: ")
            .strip()
            .lower()
        )
        if answer == "q":
            return None
        if not answer or answer == "r":
            return []
        if answer == "all":
            return list(range(len(labels)))
        try:
            chosen: set[int] = set()
            for part in answer.split(","):
                bounds = part.strip().split("-")
                if len(bounds) == 1:
                    start = end = int(bounds[0])
                elif len(bounds) == 2:
                    start, end = (int(bound.strip()) for bound in bounds)
                else:
                    raise ValueError("Invalid range.")
                if not 1 <= start <= end <= len(labels):
                    raise ValueError("Selection out of range.")
                chosen.update(range(start - 1, end))
            return sorted(chosen)
        except ValueError:
            print("Enter valid book numbers/ranges, all, Enter to rescan, or q.")
