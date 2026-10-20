"""Run the Python engine directly, using the project's virtual environment if present."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from execution import condense_arguments
from jobs import PROVIDER_KEYS, Job, Options


class PdfUnavailableError(RuntimeError):
    """Local PDF rendering needs optional Python and system libraries."""


class LocalRunner:
    def __init__(self, project: Path) -> None:
        self.project = project

    @property
    def python(self) -> str:
        relative = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        candidate = self.project / ".venv" / relative
        return str(candidate) if candidate.is_file() else sys.executable

    def check(self, options: Options | None = None) -> None:
        try:
            subprocess.run(
                [self.python, "-m", "breviabook.cli", "version"],
                cwd=self.project,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            shell_prefix = "& " if sys.platform == "win32" else ""
            raise RuntimeError(
                "Local engine dependencies are unavailable. Install them, then retry:\n"
                f'  {shell_prefix}"{self.python}" -m pip install -e "{self.project}"\n'
                "Local mode requires Python 3.11+."
            ) from exc
        if options and "pdf" in options.formats:
            try:
                subprocess.run(
                    [
                        self.python,
                        "-c",
                        "from breviabook.render.pdf_renderer import weasyprint_available; "
                        "raise SystemExit(0 if weasyprint_available() else 1)",
                    ],
                    cwd=self.project,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise PdfUnavailableError(
                    "Local PDF output is unavailable. Use Docker for PDF, or install the "
                    "[pdf] extra and WeasyPrint's system libraries (see README.md)."
                ) from exc

    def command(self, job: Job, *, resume: bool) -> list[str]:
        return [self.python, "-m", "breviabook.cli"] + condense_arguments(
            job, input_path=str(job.book.path), output_path=str(job.output_dir), resume=resume
        )

    def run(self, job: Job, *, resume: bool, api_key: str | None) -> int:
        environment = dict(os.environ)
        if job.options is not None:
            key_name = PROVIDER_KEYS[job.options.provider]
            if key_name and api_key:
                environment[key_name] = api_key
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags = 0
        with subprocess.Popen(
            self.command(job, resume=resume),
            cwd=self.project,
            env=environment,
            start_new_session=sys.platform != "win32",
            creationflags=flags,
        ) as process:
            try:
                return process.wait()
            except KeyboardInterrupt:
                print("\nStopping the local engine; saved checkpoints will be kept...")
                try:
                    if sys.platform == "win32":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                raise
