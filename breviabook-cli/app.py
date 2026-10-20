"""Interactive front end to the Docker or local Python engine."""

from __future__ import annotations

import argparse
import getpass
import os
import warnings
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from book_picker import select_books
from docker_runner import DockerRunner
from execution import Runner
from jobs import PROVIDER_KEYS, Job, Options, discover_books, load_job
from local_runner import LocalRunner, PdfUnavailableError
from pickers import choose


def credentials(provider: str) -> str | None:
    key_name = PROVIDER_KEYS[provider]
    if key_name is None:
        return None
    existing = os.environ.get(key_name, "").strip()
    prompt = f"{provider} API key (hidden)"
    if existing:
        prompt += f" [Enter to use {key_name} from environment]"
    while True:
        # Refuse getpass's visible-input fallback if a terminal cannot hide keystrokes.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass(prompt + ": ").strip() or existing
            except getpass.GetPassWarning as exc:
                raise RuntimeError(
                    "Run in an interactive terminal to enter a key securely."
                ) from exc
        if key:
            return key
        print("An API key is required; press Ctrl+C to cancel.")


def configure(previous: Options | None, args: argparse.Namespace) -> Options | None:
    providers = list(PROVIDER_KEYS)
    default_provider = previous.provider if previous else "openrouter"
    selection = choose("Provider", providers, default=providers.index(default_provider))
    if selection is None:
        return None
    provider = providers[selection]
    model = previous.model if previous and previous.provider == provider else args.model
    if not model and provider == "ollama":
        model = os.environ.get("DEFAULT_MODEL", "gemma4:e4b")
    while True:
        suffix = f" [{model}]" if model else ""
        selected_model = input(f"Model ID{suffix}: ").strip() or model
        if selected_model:
            break
        print("Enter your provider's model ID (for OpenRouter: publisher/model-name).")
    return Options(
        provider=provider,
        model=selected_model,
        target_ratio=previous.target_ratio if previous else args.target_ratio,
        formats=previous.formats if previous else tuple(args.formats.split(",")),
        concurrency=previous.concurrency if previous else args.concurrency,
    )


def select_runner(project: Path, runtime: str = "auto") -> Runner | None:
    if runtime == "local":
        print("\nUsing local Python.")
        return LocalRunner(project)
    docker = DockerRunner(project)
    print("\nChecking Docker Compose and the active engine...")
    try:
        docker.check()
    except RuntimeError as exc:
        if runtime == "docker":
            raise
        print(f"Docker unavailable: {exc}")
        print("Using local Python. Docker is recommended when available.")
        return LocalRunner(project)
    if runtime == "docker":
        return docker
    selected = choose("Run with", ["Docker (recommended)", "Local Python"], default=0)
    if selected is None:
        return None
    return docker if selected == 0 else LocalRunner(project)


@dataclass
class BatchSession:
    """Reuse interactive choices and secrets in memory for one selected queue."""

    runner: Runner | None = None
    options: Options | None = None
    keys: dict[str, str | None] = field(default_factory=dict, repr=False)
    skip_pdf: bool = False


def process(
    job: Job,
    args: argparse.Namespace,
    runner: Runner | None = None,
    *,
    session: BatchSession | None = None,
) -> None:
    resume = job.status == "Resume"
    if job.status == "Completed":
        print(f"\nAlready processed: {job.book.relative_path}")
        print(f"Previous summary: {job.output_dir}")
        if (
            choose("What next?", ["Redo summary (keep the previous files)", "Cancel"], default=1)
            != 0
        ):
            return
    elif job.status.startswith("Existing"):
        print("\nExisting output/checkpoints found; original source and settings are unverified.")
        action = choose("What next?", ["Resume existing run", "Redo summary", "Cancel"], default=2)
        if action is None or action == 2:
            return
        resume = action == 0
    elif resume:
        print("\nInterrupted or incomplete run detected; resuming with saved settings.")

    if runner is None and session is not None:
        runner = session.runner
    if runner is None:
        runner = select_runner(job.output_root.parent, args.runtime)
        if runner is None:
            return
    if session is not None:
        session.runner = runner
    options: Options | None
    if session is not None and session.options is not None:
        if resume and job.options is not None:
            options = job.options
            print(f"Using saved settings: {options.provider} / {options.model}")
        else:
            options = Options(
                session.options.provider,
                session.options.model,
                args.target_ratio,
                tuple(args.formats.split(",")),
                args.concurrency,
            )
    else:
        options = configure(job.options if resume else None, args)
    if options is None:
        return
    if session is not None and session.options is None:
        session.options = options
    if resume and job.options is not None and options != job.options:
        print("Provider/model changed: starting a new attempt and keeping the old checkpoints.")
        resume = False
    if session is not None and session.skip_pdf and "pdf" in options.formats:
        remaining_formats = tuple(fmt for fmt in options.formats if fmt != "pdf")
        if not remaining_formats:
            raise RuntimeError(
                "This book requests only PDF; use Docker or enable local PDF output."
            )
        options = replace(options, formats=remaining_formats)
    try:
        runner.check(options)
    except PdfUnavailableError as exc:
        print(str(exc))
        remaining_formats = tuple(fmt for fmt in options.formats if fmt != "pdf")
        if not remaining_formats:
            raise RuntimeError("Choose --formats epub,md or use Docker for PDF output.") from exc
        selection = choose(
            "Local PDF is unavailable",
            [f"Continue with {','.join(remaining_formats)} (skip PDF)", "Cancel"],
            default=0,
        )
        if selection != 0:
            return
        options = replace(options, formats=remaining_formats)
        if session is not None:
            session.skip_pdf = True
    print(
        f"\nKeep {options.target_ratio:.0%}; outputs {','.join(options.formats)}; "
        f"concurrency {options.concurrency}."
    )
    if session is not None and options.provider in session.keys:
        key = session.keys[options.provider]
    else:
        key = credentials(options.provider)
        if session is not None:
            session.keys[options.provider] = key
    job.start(options, resume=resume)
    print(f"{'Resuming' if resume else 'Processing'} {job.book.relative_path}")
    print(f"Output: {job.output_dir}\n")
    code = runner.run(job, resume=resume, api_key=key)
    if code:
        print(f"\nProcessing exited with code {code}. Select this book again to resume.")
        return
    missing = job.missing_outputs()
    if missing:
        print("\nRun incomplete; missing output: " + ", ".join(missing))
        print("Select this book again to resume. Check the engine warnings above.")
        return
    job.completed = True
    job.save()
    print(f"\nCompleted. Summary files: {job.output_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Condense a book with Docker or local Python.")
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--model", help="Default model ID offered at the prompt")
    parser.add_argument("--target-ratio", type=float, default=0.30, help="New jobs: share to keep")
    parser.add_argument("--formats", default="epub,pdf,md", help="New jobs: epub,pdf,md")
    parser.add_argument("--concurrency", type=int, default=4, help="New jobs: concurrent LLM calls")
    parser.add_argument(
        "--runtime",
        choices=("auto", "docker", "local"),
        default="auto",
        help="Choose interactively with local fallback (auto), or require docker/local",
    )
    args = parser.parse_args(argv)
    args.formats = ",".join(dict.fromkeys(fmt.strip().lower() for fmt in args.formats.split(",")))
    try:
        Options(
            "openrouter",
            args.model or "validate",
            args.target_ratio,
            tuple(args.formats.split(",")),
            args.concurrency,
        )
    except ValueError as exc:
        parser.error(str(exc))
    project = args.project_dir.resolve()
    if not project.is_dir():
        parser.error(f"Project directory not found: {project}")
    input_dir, output_dir = project / "input", project / "output"
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Input:  {input_dir}\nOutput: {output_dir}")
        while True:
            books = discover_books(input_dir)
            if not books:
                print("\nDrop EPUB/PDF books into input; filenames can contain spaces or Unicode.")
                if choose("No books found", ["Rescan input", "Quit"], default=0) != 0:
                    return 0
                continue
            stems = Counter(book.path.stem.casefold() for book in books)
            jobs = [
                load_job(book, output_dir, allow_legacy=stems[book.path.stem.casefold()] == 1)
                for book in books
            ]
            labels = [f"{job.book.relative_path} [{job.status}]" for job in jobs]
            selected = select_books(labels)
            if selected is None:
                return 0
            if not selected:
                continue
            session = BatchSession()
            print(f"\nQueued {len(selected)} book(s); processing one at a time.")
            try:
                for position, index in enumerate(selected, 1):
                    job = jobs[index]
                    print(f"\nBook {position}/{len(selected)}: {job.book.relative_path}")
                    try:
                        process(job, args, session=session)
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f"\nError processing {job.book.relative_path}: {exc}")
            finally:
                session.keys.clear()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. Any saved checkpoints will be reused next time.")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
