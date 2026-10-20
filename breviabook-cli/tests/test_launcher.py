"""Behavior tests using a simulated Docker engine; no paid requests."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import BatchSession, credentials, main, process, select_runner  # noqa: E402
from docker_runner import DockerRunner  # noqa: E402
from jobs import Options, discover_books, load_job  # noqa: E402
from local_runner import LocalRunner, PdfUnavailableError  # noqa: E402


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.input = self.project / "input"
        self.output = self.project / "output"
        self.input.mkdir()
        self.output.mkdir()
        (self.project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.options = Options("openrouter", "example/model")
        self.args = argparse.Namespace(
            model=None, target_ratio=0.3, formats="epub,pdf,md", concurrency=4
        )
        self.console = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.console)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def book(self, name: str = "A book [1] 雪.EPUB", content: bytes = b"book"):
        path = self.input / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return next(book for book in discover_books(self.input) if book.path == path)

    def render(self, job, **_kwargs):
        for fmt in job.options.formats:
            (job.output_dir / f"{job.book.path.stem}-condensed.{fmt}").write_text("summary")
        return 0

    def test_first_launch_creates_folders_without_needing_docker(self):
        fresh = self.project / "fresh"
        fresh.mkdir()
        (fresh / "compose.yaml").touch()
        with (
            patch("builtins.input", return_value="q"),
            patch.object(DockerRunner, "check") as check,
        ):
            self.assertEqual(main(["--project-dir", str(fresh)]), 0)
        self.assertTrue((fresh / "input").is_dir())
        self.assertTrue((fresh / "output").is_dir())
        check.assert_not_called()

    def test_discovers_supported_formats_with_arbitrary_names_and_subfolders(self):
        self.book()
        self.book("Nested/a different book.PDF")
        (self.input / "ignore.txt").touch()
        self.assertEqual(len(discover_books(self.input)), 2)

    def test_changed_contents_and_same_stem_formats_get_separate_jobs(self):
        first = self.book("book.epub")
        second = self.book("book.pdf")
        changed = self.book("book.epub", b"a different edition")
        self.assertEqual(len({first.identity, second.identity, changed.identity}), 3)

    def test_new_interrupted_completed_and_deleted_output_states(self):
        book = self.book()
        job = load_job(book, self.output)
        self.assertEqual(job.status, "New")
        job.start(self.options, resume=False)
        restored = load_job(book, self.output)
        self.assertEqual(restored.status, "Resume")
        self.assertEqual(restored.options, self.options)
        self.render(restored)
        restored.completed = True
        restored.save()
        self.assertEqual(load_job(book, self.output).status, "Completed")
        (restored.output_dir / f"{book.path.stem}-condensed.md").unlink()
        self.assertEqual(load_job(book, self.output).status, "Resume")

    def test_zero_length_output_is_incomplete(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        self.render(job)
        (job.output_dir / f"{job.book.path.stem}-condensed.pdf").write_bytes(b"")
        self.assertTrue(job.missing_outputs())

    def test_redo_keeps_previous_outputs_and_checkpoint(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        self.render(job)
        old_output = job.output_dir
        job.checkpoint_path.parent.mkdir()
        job.checkpoint_path.write_text("checkpoint")
        old_checkpoint = job.checkpoint_path
        job.start(self.options, resume=False)
        self.assertNotEqual(job.output_dir, old_output)
        self.assertEqual(job.attempt, 2)
        self.assertTrue(old_checkpoint.is_file())
        self.assertTrue(list(old_output.glob("*.md")))

    def test_legacy_output_is_not_assumed_complete_and_can_resume(self):
        book = self.book("old book.epub")
        checkpoint = self.output / ".breviabook" / "old book-condensed.jsonl"
        checkpoint.parent.mkdir()
        checkpoint.write_text("cached")
        job = load_job(book, self.output)
        self.assertTrue(job.status.startswith("Existing"))
        job.start(self.options, resume=True)
        restored = load_job(book, self.output)
        self.assertEqual(restored.status, "Resume")
        self.assertEqual(restored.checkpoint_path, checkpoint)

    def test_redo_legacy_run_uses_separate_directory(self):
        book = self.book("old.epub")
        legacy = self.output / "old-condensed.md"
        legacy.write_text("original")
        job = load_job(book, self.output)
        job.start(self.options, resume=False)
        self.assertFalse(job.legacy)
        self.assertNotEqual(job.output_dir, self.output)
        self.assertEqual(legacy.read_text(), "original")

    def test_ambiguous_legacy_output_can_be_excluded(self):
        book = self.book("book.epub")
        self.book("book.pdf")
        (self.output / "book-condensed.md").write_text("unknown source")
        self.assertEqual(load_job(book, self.output, allow_legacy=False).status, "New")

    def test_corrupt_state_is_reported_without_destroying_it(self):
        job = load_job(self.book(), self.output)
        job.directory.mkdir()
        job.metadata_path.write_text("not json")
        with self.assertRaisesRegex(ValueError, "keep the checkpoint files"):
            load_job(job.book, self.output)
        self.assertEqual(job.metadata_path.read_text(), "not json")

    def test_credentials_are_hidden_and_not_written_to_state_or_argv(self):
        with patch("app.getpass.getpass", return_value="secret-key") as hidden:
            key = credentials("openrouter")
        hidden.assert_called_once()
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        self.assertNotIn(key, job.metadata_path.read_text())
        self.assertNotIn("API_KEY", job.metadata_path.read_text())
        command = DockerRunner(self.project).command(job, resume=True, container_name="test-only")
        self.assertNotIn(key, command)
        self.assertIn("OPENROUTER_API_KEY", command)
        self.assertIn("--resume", command)
        self.assertIn("/input/A book [1] 雪.EPUB", command)
        self.assertNotIn(
            "--resume",
            DockerRunner(self.project).command(job, resume=False, container_name="test-only"),
        )

    def test_credentials_can_reuse_environment_and_ollama_needs_no_key(self):
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "from-environment"}),
            patch("app.getpass.getpass", return_value=""),
        ):
            self.assertEqual(credentials("openrouter"), "from-environment")
        with patch("app.getpass.getpass") as hidden:
            self.assertIsNone(credentials("ollama"))
        hidden.assert_not_called()

    def test_visible_getpass_fallback_is_refused(self):
        import getpass

        with (
            patch("app.getpass.getpass", side_effect=getpass.GetPassWarning),
            self.assertRaisesRegex(RuntimeError, "interactive terminal"),
        ):
            credentials("openrouter")

    def test_new_run_is_marked_complete_only_after_successful_rendering(self):
        job = load_job(self.book(), self.output)
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["1", "example/model"]),
            patch("app.credentials", return_value="hidden-secret"),
        ):
            process(job, self.args, runner)
        self.assertEqual(load_job(job.book, self.output).status, "Completed")
        self.assertFalse(runner.run.call_args.kwargs["resume"])
        self.assertNotIn("hidden-secret", job.metadata_path.read_text())

    def test_resume_uses_saved_settings_and_checkpoint_directory(self):
        job = load_job(self.book(), self.output)
        saved = Options("openrouter", "previous/model", 0.5, ("md",), 2)
        job.start(saved, resume=False)
        original_dir = job.output_dir
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["", ""]),
            patch("app.credentials", return_value="key"),
        ):
            process(load_job(job.book, self.output), self.args, runner)
        restored = load_job(job.book, self.output)
        self.assertEqual(restored.options, saved)
        self.assertEqual(restored.output_dir, original_dir)
        self.assertTrue(runner.run.call_args.kwargs["resume"])

    def test_completed_book_defaults_to_cancel_without_key_or_docker(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        self.render(job)
        job.completed = True
        job.save()
        runner = MagicMock()
        with patch("builtins.input", return_value=""), patch("app.credentials") as key:
            process(job, self.args, runner)
        runner.check.assert_not_called()
        runner.run.assert_not_called()
        key.assert_not_called()
        self.assertEqual(job.status, "Completed")

    def test_completed_book_arrow_menu_cancel_never_starts_processing(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        self.render(job)
        job.completed = True
        job.save()
        runner = MagicMock()
        questionary = MagicMock()
        questionary.select.return_value.unsafe_ask.return_value = 1
        with (
            patch.dict(sys.modules, {"questionary": questionary}),
            patch("pickers.sys.stdin.isatty", return_value=True),
            patch("pickers.sys.stdout.isatty", return_value=True),
            patch("app.credentials") as key,
        ):
            process(job, self.args, runner)
        self.assertEqual(questionary.select.call_args.args[0], "What next?")
        runner.check.assert_not_called()
        runner.run.assert_not_called()
        key.assert_not_called()

    def test_provider_change_starts_new_attempt_instead_of_reusing_cache(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["2", "another/model"]),
            patch("app.credentials", return_value="key"),
        ):
            process(job, self.args, runner)
        self.assertEqual(job.attempt, 2)
        self.assertFalse(runner.run.call_args.kwargs["resume"])

    def test_failed_or_missing_output_run_stays_resumable(self):
        for exit_code in (1, 0):
            with self.subTest(exit_code=exit_code):
                job = load_job(self.book(f"failure-{exit_code}.epub"), self.output)
                runner = MagicMock()
                runner.run.return_value = exit_code
                with (
                    patch("builtins.input", side_effect=["1", "example/model"]),
                    patch("app.credentials", return_value="key"),
                ):
                    process(job, self.args, runner)
                self.assertEqual(load_job(job.book, self.output).status, "Resume")

    def test_interruption_keeps_saved_settings_for_next_launch(self):
        job = load_job(self.book(), self.output)
        runner = MagicMock()
        runner.run.side_effect = KeyboardInterrupt
        with (
            patch("builtins.input", side_effect=["1", "example/model"]),
            patch("app.credentials", return_value="key"),
            self.assertRaises(KeyboardInterrupt),
        ):
            process(job, self.args, runner)
        self.assertEqual(load_job(job.book, self.output).status, "Resume")

    def test_docker_gets_key_through_environment_only(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with (
            patch.object(DockerRunner, "build_image"),
            patch("docker_runner.subprocess.Popen") as popen,
        ):
            popen.return_value.__enter__.return_value.wait.return_value = 0
            self.assertEqual(
                DockerRunner(self.project).run(job, resume=False, api_key="do-not-print"), 0
            )
        self.assertNotIn("do-not-print", popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs["env"]["OPENROUTER_API_KEY"], "do-not-print")
        self.assertNotIn("do-not-print", self.console.getvalue())

    def test_ctrl_c_stops_only_this_launchers_named_container(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with (
            patch.object(DockerRunner, "build_image"),
            patch("docker_runner.subprocess.Popen") as popen,
        ):
            process_mock = popen.return_value.__enter__.return_value
            process_mock.wait.side_effect = [KeyboardInterrupt, 0]
            with (
                patch("docker_runner.subprocess.run") as stop,
                self.assertRaises(KeyboardInterrupt),
            ):
                DockerRunner(self.project).run(job, resume=False, api_key="key")
        command = popen.call_args.args[0]
        container = command[command.index("--name") + 1]
        self.assertTrue(container.startswith("breviabook-cli-"))
        self.assertEqual(stop.call_args.args[0], ["docker", "stop", "--time", "5", container])

    def test_docker_preserves_terminal_for_the_original_live_ui(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with (
            patch("docker_runner.sys.stdin.isatty", return_value=True),
            patch("docker_runner.sys.stdout.isatty", return_value=True),
        ):
            command = DockerRunner(self.project).command(job, resume=True, container_name="ui-test")
        self.assertNotIn("-T", command)

    def test_docker_disables_terminal_only_for_redirected_streams(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        for stdin_terminal, stdout_terminal in ((True, False), (False, True), (False, False)):
            with (
                self.subTest(stdin=stdin_terminal, stdout=stdout_terminal),
                patch("docker_runner.sys.stdin.isatty", return_value=stdin_terminal),
                patch("docker_runner.sys.stdout.isatty", return_value=stdout_terminal),
            ):
                command = DockerRunner(self.project).command(
                    job, resume=True, container_name="log-test"
                )
            self.assertIn("-T", command)

    def test_broken_buildx_endpoint_uses_process_local_legacy_builder(self):
        environment = {"DOCKER_BUILDKIT": "1", "COMPOSE_DOCKER_CLI_BUILD": "1"}
        responses = [
            subprocess.CompletedProcess([], 1, "", 'docker endpoint for "default" not found'),
            subprocess.CompletedProcess([], 0),
        ]
        with (
            patch.dict(os.environ, {"DOCKER_BUILDKIT": "1"}),
            patch("docker_runner.subprocess.run", side_effect=responses) as run,
        ):
            DockerRunner(self.project).build_image(environment)
            self.assertEqual(os.environ["DOCKER_BUILDKIT"], "1")
        build = run.call_args_list[1]
        self.assertEqual(build.args[0][-2:], ["build", "breviabook"])
        self.assertEqual(build.kwargs["env"]["DOCKER_BUILDKIT"], "0")
        self.assertEqual(build.kwargs["env"]["COMPOSE_DOCKER_CLI_BUILD"], "0")

    def test_healthy_builder_keeps_existing_build_settings(self):
        environment = {"DOCKER_BUILDKIT": "1"}
        with patch(
            "docker_runner.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "working builder", ""),
                subprocess.CompletedProcess([], 0),
            ],
        ):
            DockerRunner(self.project).build_image(environment)
        self.assertEqual(environment, {"DOCKER_BUILDKIT": "1"})

    def test_missing_buildx_does_not_disable_buildkit_for_unrelated_errors(self):
        environment = {}
        with patch(
            "docker_runner.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 1, "", "buildx is not a docker command"),
                subprocess.CompletedProcess([], 0),
            ],
        ):
            DockerRunner(self.project).build_image(environment)
        self.assertNotIn("DOCKER_BUILDKIT", environment)

    def test_build_failure_never_starts_processing(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with (
            patch(
                "docker_runner.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "working", ""),
                    subprocess.CalledProcessError(1, ["docker", "compose", "build"]),
                ],
            ),
            patch("docker_runner.subprocess.Popen") as popen,
            self.assertRaisesRegex(RuntimeError, "No book-processing container was started"),
        ):
            DockerRunner(self.project).run(job, resume=False, api_key="key")
        popen.assert_not_called()
        self.assertEqual(load_job(job.book, self.output).status, "Resume")

    def test_each_book_gets_a_new_container_without_building_inside_run(self):
        job = load_job(self.book("Example book.epub"), self.output)
        job.start(self.options, resume=False)
        with (
            patch.object(DockerRunner, "build_image") as build,
            patch("docker_runner.subprocess.Popen") as popen,
        ):
            child = popen.return_value.__enter__.return_value
            child.wait.return_value = 0
            runner = DockerRunner(self.project)
            runner.run(job, resume=False, api_key="secret-key")
            runner.run(job, resume=True, api_key="secret-key")
        commands = [call.args[0] for call in popen.call_args_list]
        names = [command[command.index("--name") + 1] for command in commands]
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(name.startswith("breviabook-cli-example-book-") for name in names))
        self.assertTrue(all("--rm" in command and "--build" not in command for command in commands))
        self.assertEqual(build.call_count, 2)

    def test_docker_failure_is_actionable(self):
        error = subprocess.CalledProcessError(1, ["docker"], stderr="permission denied")
        with (
            patch("docker_runner.subprocess.run", side_effect=error),
            self.assertRaisesRegex(RuntimeError, "config permissions"),
        ):
            DockerRunner(self.project).check()

    def test_full_menu_new_complete_then_cancel(self):
        self.book()
        answers = ["1", "1", "1", "example/model", "1", "", "q"]
        with (
            patch("builtins.input", side_effect=answers),
            patch("app.credentials", return_value="key"),
            patch.object(DockerRunner, "check"),
            patch.object(DockerRunner, "run", side_effect=self.render) as run,
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        self.assertEqual(run.call_count, 1)
        self.assertIn("Already processed", self.console.getvalue())

    def test_options_validate_untrusted_saved_records(self):
        for invalid in (None, {}, {"provider": "unsupported"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Options.from_json(invalid)
        record = json.loads(
            json.dumps(
                {
                    "provider": "openrouter",
                    "model": "example/model",
                    "target_ratio": 0.3,
                    "formats": ["md"],
                    "concurrency": True,
                }
            )
        )
        with self.assertRaises(ValueError):
            Options.from_json(record)

    def test_available_docker_is_recommended_and_default(self):
        with patch.object(DockerRunner, "check"), patch("builtins.input", return_value=""):
            runner = select_runner(self.project)
        self.assertIsInstance(runner, DockerRunner)
        self.assertIn("Docker (recommended)", self.console.getvalue())

    def test_user_can_choose_local_even_with_docker_available(self):
        with patch.object(DockerRunner, "check"), patch("builtins.input", return_value="2"):
            self.assertIsInstance(select_runner(self.project), LocalRunner)

    def test_missing_docker_and_stopped_engine_fall_back_to_local(self):
        for error in (
            FileNotFoundError("docker"),
            PermissionError("Docker executable is inaccessible"),
            subprocess.CalledProcessError(
                1, ["docker"], stderr="Cannot connect to the Docker daemon"
            ),
        ):
            with (
                self.subTest(error=error),
                patch("docker_runner.subprocess.run", side_effect=error),
            ):
                self.assertIsInstance(select_runner(self.project), LocalRunner)
        self.assertIn("Using local Python", self.console.getvalue())

    def test_local_mode_does_not_need_compose_or_probe_docker(self):
        (self.project / "compose.yaml").unlink()
        with patch.object(DockerRunner, "check") as check:
            self.assertIsInstance(select_runner(self.project, "local"), LocalRunner)
        check.assert_not_called()
        self.assertIsInstance(select_runner(self.project), LocalRunner)

    def test_explicit_docker_reports_failure_instead_of_switching(self):
        with (
            patch.object(DockerRunner, "check", side_effect=RuntimeError("daemon stopped")),
            self.assertRaisesRegex(RuntimeError, "daemon stopped"),
        ):
            select_runner(self.project, "docker")

    def test_cancelling_runtime_selection_does_not_configure_or_start_job(self):
        job = load_job(self.book(), self.output)
        self.args.runtime = "auto"
        with (
            patch.object(DockerRunner, "check"),
            patch("builtins.input", return_value="q"),
            patch("app.configure") as configure,
        ):
            process(job, self.args)
        configure.assert_not_called()
        self.assertFalse(job.metadata_path.exists())

    def test_local_command_uses_host_paths_and_preserves_resume_options(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        command = LocalRunner(self.project).command(job, resume=True)
        self.assertEqual(command[1:4], ["-m", "breviabook.cli", "condense"])
        self.assertIn(str(job.book.path), command)
        self.assertIn(str(job.output_dir), command)
        self.assertIn("--resume", command)
        self.assertIn("example/model", command)
        self.assertNotIn("docker", command)

    def test_local_prefers_project_venv(self):
        relative = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        python = self.project / ".venv" / relative
        python.parent.mkdir(parents=True)
        python.touch()
        self.assertEqual(LocalRunner(self.project).python, str(python))

    def test_local_missing_dependencies_give_install_instructions(self):
        with (
            patch(
                "local_runner.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1, ["python"], stderr="No module named typer"
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "pip install -e"),
        ):
            LocalRunner(self.project).check(self.options)

    def test_local_pdf_libraries_checked_before_processing(self):
        with (
            patch(
                "local_runner.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CalledProcessError(1, ["python"], stderr="missing Pango"),
                ],
            ),
            self.assertRaises(PdfUnavailableError),
        ):
            LocalRunner(self.project).check(self.options)

    def test_unavailable_local_pdf_can_be_skipped_while_resuming(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        previous_dir = job.output_dir
        runner = MagicMock()
        runner.check.side_effect = PdfUnavailableError("PDF unavailable")
        runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["", "", "1"]),
            patch("app.credentials", return_value="key"),
        ):
            process(job, self.args, runner)
        restored = load_job(job.book, self.output)
        self.assertEqual(restored.status, "Completed")
        self.assertEqual(restored.options.formats, ("epub", "md"))
        self.assertEqual(restored.output_dir, previous_dir)
        self.assertTrue(runner.run.call_args.kwargs["resume"])

    def test_unavailable_pdf_cancel_does_not_save_or_start(self):
        job = load_job(self.book(), self.output)
        runner = MagicMock()
        runner.check.side_effect = PdfUnavailableError("PDF unavailable")
        with (
            patch("builtins.input", side_effect=["1", "example/model", "2"]),
            patch("app.credentials") as key,
        ):
            process(job, self.args, runner)
        runner.run.assert_not_called()
        key.assert_not_called()
        self.assertFalse(job.metadata_path.exists())

    def test_local_key_is_passed_in_environment_only(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with patch("local_runner.subprocess.Popen") as popen:
            popen.return_value.__enter__.return_value.wait.return_value = 0
            self.assertEqual(
                LocalRunner(self.project).run(job, resume=False, api_key="local-secret"), 0
            )
        self.assertNotIn("local-secret", popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs["env"]["OPENROUTER_API_KEY"], "local-secret")
        self.assertNotIn("local-secret", job.metadata_path.read_text())

    def test_local_ctrl_c_stops_child_process_and_keeps_resume_state(self):
        job = load_job(self.book(), self.output)
        job.start(self.options, resume=False)
        with (
            patch("local_runner.subprocess.Popen") as popen,
            patch("local_runner.os.killpg", create=True) as killpg,
        ):
            child = popen.return_value.__enter__.return_value
            child.wait.side_effect = [KeyboardInterrupt, 0]
            with self.assertRaises(KeyboardInterrupt):
                LocalRunner(self.project).run(job, resume=False, api_key="key")
        if sys.platform == "win32":
            child.send_signal.assert_called_once()
        else:
            killpg.assert_called_once()
        self.assertEqual(load_job(job.book, self.output).status, "Resume")

    def test_full_menu_without_docker_runs_local_without_compose_file(self):
        self.book()
        (self.project / "compose.yaml").unlink()
        with (
            patch("builtins.input", side_effect=["1", "1", "example/model", "q"]),
            patch("app.credentials", return_value="key"),
            patch.object(LocalRunner, "check"),
            patch.object(LocalRunner, "run", side_effect=self.render) as run,
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(load_job(discover_books(self.input)[0], self.output).status, "Completed")

    def test_batch_requires_explicit_all_and_reuses_provider_key_and_runtime(self):
        self.book("first.epub")
        self.book("second.pdf")
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["", "all", "1", "example/model", "q"]),
            patch("app.select_runner", return_value=runner) as runtime,
            patch("app.credentials", return_value="batch-secret") as key,
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        runtime.assert_called_once()
        key.assert_called_once_with("openrouter")
        self.assertEqual(runner.run.call_count, 2)
        for book in discover_books(self.input):
            job = load_job(book, self.output)
            self.assertEqual(job.status, "Completed")
            self.assertNotIn("batch-secret", job.metadata_path.read_text())
        self.assertIn("Book 2/2", self.console.getvalue())

    def test_batch_subset_processes_only_checked_books(self):
        self.book("a.epub")
        self.book("b.epub")
        self.book("c.epub")
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("app.select_books", side_effect=[[0, 2], None]),
            patch("builtins.input", side_effect=["1", "example/model"]),
            patch("app.select_runner", return_value=runner),
            patch("app.credentials", return_value="key"),
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        self.assertEqual(runner.run.call_count, 2)
        statuses = [load_job(book, self.output).status for book in discover_books(self.input)]
        self.assertEqual(statuses, ["Completed", "New", "Completed"])

    def test_batch_keeps_saved_options_and_credentials_separate_by_provider(self):
        first = load_job(self.book("a.epub"), self.output)
        second = load_job(self.book("b.epub"), self.output)
        saved = Options("openai", "saved/model", 0.6, ("md",), 2)
        second.start(saved, resume=False)
        previous_output = second.output_dir
        session = BatchSession(runner=MagicMock())
        session.runner.run.side_effect = self.render
        with (
            patch("builtins.input", side_effect=["1", "new/model"]),
            patch("app.credentials", side_effect=["router-key", "openai-key"]) as key,
        ):
            process(first, self.args, session=session)
            process(second, self.args, session=session)
        self.assertEqual([call.args[0] for call in key.call_args_list], ["openrouter", "openai"])
        self.assertEqual(second.options, saved)
        self.assertEqual(second.output_dir, previous_output)
        self.assertTrue(session.runner.run.call_args.kwargs["resume"])

    def test_batch_failure_continues_with_next_book(self):
        self.book("a.epub")
        self.book("b.epub")
        runner = MagicMock()
        runner.run.side_effect = [OSError("test failure"), 1]
        with (
            patch("app.select_books", side_effect=[[0, 1], None]),
            patch("builtins.input", side_effect=["1", "example/model"]),
            patch("app.select_runner", return_value=runner),
            patch("app.credentials", return_value="key"),
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        self.assertEqual(runner.run.call_count, 2)
        self.assertTrue(
            all(
                load_job(book, self.output).status == "Resume"
                for book in discover_books(self.input)
            )
        )

    def test_batch_ctrl_c_cancels_remaining_books(self):
        self.book("a.epub")
        self.book("b.epub")
        runner = MagicMock()
        runner.run.side_effect = KeyboardInterrupt
        with (
            patch("app.select_books", return_value=[0, 1]),
            patch("builtins.input", side_effect=["1", "example/model"]),
            patch("app.select_runner", return_value=runner),
            patch("app.credentials", return_value="key"),
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 130)
        self.assertEqual(runner.run.call_count, 1)
        statuses = [load_job(book, self.output).status for book in discover_books(self.input)]
        self.assertEqual(statuses, ["Resume", "New"])

    def test_completed_book_still_requires_redo_in_a_batch(self):
        first = load_job(self.book("a.epub"), self.output)
        first.start(self.options, resume=False)
        self.render(first)
        first.completed = True
        first.save()
        self.book("b.epub")
        runner = MagicMock()
        runner.run.side_effect = self.render
        with (
            patch("app.select_books", side_effect=[[0, 1], None]),
            patch("builtins.input", side_effect=["", "1", "example/model"]),
            patch("app.select_runner", return_value=runner),
            patch("app.credentials", return_value="key"),
        ):
            self.assertEqual(main(["--project-dir", str(self.project)]), 0)
        runner.run.assert_called_once()
        self.assertEqual(load_job(first.book, self.output).attempt, 1)


if __name__ == "__main__":
    unittest.main()
