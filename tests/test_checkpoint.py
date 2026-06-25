"""CheckpointManager: durable, resumable per-chunk results."""

from __future__ import annotations

from pathlib import Path

from breviabook.persistence.checkpoint import CheckpointManager


def test_record_and_query(tmp_path: Path) -> None:
    cp = CheckpointManager(tmp_path / "job.jsonl")
    assert not cp.is_done("ch0-1")
    cp.record("ch0-1", {"text": "condensed"})
    assert cp.is_done("ch0-1")
    assert cp.get("ch0-1") == {"text": "condensed"}


def test_persists_across_instances_resume(tmp_path: Path) -> None:
    path = tmp_path / "job.jsonl"
    cp = CheckpointManager(path)
    cp.record("ch0-1", {"text": "one"})
    cp.record("ch0-2", {"text": "two"})

    resumed = CheckpointManager(path)  # simulate restart
    assert resumed.is_done("ch0-1")
    assert resumed.is_done("ch0-2")
    assert resumed.get("ch0-2") == {"text": "two"}
    assert set(resumed.results()) == {"ch0-1", "ch0-2"}


def test_last_write_wins(tmp_path: Path) -> None:
    path = tmp_path / "job.jsonl"
    cp = CheckpointManager(path)
    cp.record("ch0-1", {"text": "first"})
    cp.record("ch0-1", {"text": "second"})
    assert CheckpointManager(path).get("ch0-1") == {"text": "second"}


def test_clear_resets(tmp_path: Path) -> None:
    path = tmp_path / "job.jsonl"
    cp = CheckpointManager(path)
    cp.record("ch0-1", {"text": "x"})
    cp.clear()
    assert not cp.is_done("ch0-1")
    assert not path.exists()
    cp.clear()  # idempotent on missing file


def test_tolerates_blank_and_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "job.jsonl"
    path.write_text(
        '{"chunk_id": "ch0-1", "result": {"text": "ok"}}\n'
        "\n"
        "{not valid json\n"
        '{"chunk_id": "ch0-2", "result": {"text": "ok2"}}\n',
        encoding="utf-8",
    )
    cp = CheckpointManager(path)
    assert set(cp.results()) == {"ch0-1", "ch0-2"}


def test_skip_resumed_chunks_simulation(tmp_path: Path) -> None:
    path = tmp_path / "job.jsonl"
    cp = CheckpointManager(path)
    cp.record("ch0-1", {"text": "done"})

    processed: list[str] = []
    for chunk_id in ["ch0-1", "ch0-2", "ch0-3"]:
        if cp.is_done(chunk_id):
            continue
        processed.append(chunk_id)
        cp.record(chunk_id, {"text": chunk_id})
    assert processed == ["ch0-2", "ch0-3"]  # ch0-1 skipped on resume
