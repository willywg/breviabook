"""Shared engine arguments and contract for Docker and local execution."""

from __future__ import annotations

from typing import Protocol

from jobs import Job, Options


class Runner(Protocol):
    def check(self, options: Options | None = None) -> None: ...

    def run(self, job: Job, *, resume: bool, api_key: str | None) -> int: ...


def condense_arguments(job: Job, *, input_path: str, output_path: str, resume: bool) -> list[str]:
    options = job.options
    if options is None:
        raise ValueError("Save job options before starting the engine.")
    command = [
        "condense",
        input_path,
        "--out",
        output_path,
        "--provider",
        options.provider,
        "--model",
        options.model,
        "--target-ratio",
        str(options.target_ratio),
        "--formats",
        ",".join(options.formats),
        "--concurrency",
        str(options.concurrency),
    ]
    if resume:
        command.append("--resume")
    return command
