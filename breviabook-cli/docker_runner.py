"""Run the existing Compose service without exposing keys in command arguments."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from execution import condense_arguments
from jobs import PROVIDER_KEYS, Job, Options


class DockerRunner:
    def __init__(self, project: Path) -> None:
        self.project = project

    @property
    def compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-directory",
            str(self.project),
            "-f",
            str(self.project / "compose.yaml"),
        ]

    def check(self, options: Options | None = None) -> None:
        if not (self.project / "compose.yaml").is_file():
            raise RuntimeError(f"Docker Compose configuration not found in {self.project}.")
        for command in (
            ["docker", "compose", "version"],
            ["docker", "info", "--format", "{{.ServerVersion}}"],
        ):
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
            except FileNotFoundError as exc:
                raise RuntimeError("Docker was not found. Install/start Docker Desktop.") from exc
            except OSError as exc:
                raise RuntimeError(f"Docker could not start: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "Docker did not respond. Start Docker Desktop and retry."
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    "Docker/Compose is unavailable. Check Docker Desktop and config permissions.\n"
                    + exc.stderr.strip()
                ) from exc

    def command(self, job: Job, *, resume: bool, container_name: str) -> list[str]:
        options = job.options
        if options is None:
            raise ValueError("Save job options before starting Docker.")
        relative_output = job.output_dir.relative_to(self.project / "output")
        command = self.compose + [
            "run",
            "--rm",
            "--name",
            container_name,
        ]
        # Let Compose allocate a terminal for the engine's original Rich UI.
        # Pipes/redirects keep plain logs instead of terminal escape sequences.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            command.append("-T")
        key_name = PROVIDER_KEYS[options.provider]
        if key_name:
            command += ["--env", key_name]
        command += ["breviabook"] + condense_arguments(
            job,
            input_path="/input/" + job.book.relative_path.as_posix(),
            output_path="/output/" + relative_output.as_posix(),
            resume=resume,
        )
        return command

    def build_image(self, environment: dict[str, str]) -> None:
        print(
            "\nPreparing the shared BreviaBook image (reusing cached build layers)...", flush=True
        )
        # A reachable daemon can coexist with broken Buildx context metadata. Probe
        # the builder separately; never retry a book-processing command after failure.
        try:
            builder = subprocess.run(
                ["docker", "buildx", "inspect"],
                capture_output=True,
                text=True,
                env=environment,
                cwd=self.project,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            builder = None  # Compose can use its normal builder if Buildx is absent.
        if builder is not None and builder.returncode:
            diagnostic = (builder.stdout + builder.stderr).lower()
            if "docker endpoint for" in diagnostic and "not found" in diagnostic:
                print(
                    "Buildx has a missing Docker endpoint. Using the legacy builder for "
                    "this run; global Docker settings are unchanged.",
                    flush=True,
                )
                environment["DOCKER_BUILDKIT"] = "0"
                environment["COMPOSE_DOCKER_CLI_BUILD"] = "0"
        try:
            subprocess.run(
                self.compose + ["build", "breviabook"],
                cwd=self.project,
                env=environment,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "Could not build the BreviaBook image. Check the Docker build output above. "
                "No book-processing container was started; saved checkpoints are unchanged."
            ) from exc

    def run(self, job: Job, *, resume: bool, api_key: str | None) -> int:
        title = re.sub(r"[^a-z0-9]+", "-", job.book.path.stem.lower()).strip("-")[:32] or "book"
        container_name = f"breviabook-cli-{title}-{job.book.identity[:8]}-{uuid.uuid4().hex[:8]}"
        command = self.command(job, resume=resume, container_name=container_name)
        environment = dict(os.environ)
        if sys.platform != "win32":
            environment.setdefault("DOCKER_UID", str(os.getuid()))
            environment.setdefault("DOCKER_GID", str(os.getgid()))
        self.build_image(environment)
        if job.options is not None:
            key_name = PROVIDER_KEYS[job.options.provider]
            if key_name and api_key:
                environment[key_name] = api_key
        print(
            f"\nStarting container {container_name} for {job.book.relative_path}...\n", flush=True
        )
        # Isolate the Docker client from Ctrl+C: stop the named container explicitly,
        # so an interrupted launcher does not leave a paid job running in the background.
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags = 0
        with subprocess.Popen(
            command,
            cwd=self.project,
            env=environment,
            start_new_session=sys.platform != "win32",
            creationflags=flags,
        ) as process:
            try:
                return process.wait()
            except KeyboardInterrupt:
                print("\nStopping this book's container; saved checkpoints will be kept...")
                try:
                    subprocess.run(
                        ["docker", "stop", "--time", "5", container_name],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                    )
                except (OSError, subprocess.SubprocessError):
                    print(f"Could not confirm container stopped. Run: docker stop {container_name}")
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise
