"""Checkpoint / resume for long jobs (ROADMAP §5, §10 Phase 3, §13.4).

Per-chunk results are appended to a JSONL file and flushed immediately, so an interrupted
run loses at most the in-flight chunk. On restart, the manager reloads the file and the
pipeline skips any chunk already recorded. The result payload is intentionally generic
(``dict[str, object]``) — the condenser (Phase 4) defines its shape.

These files are job state, not source — they are gitignored (``checkpoints/``, ``.brevia/``).
"""

from __future__ import annotations

import json
from pathlib import Path

Result = dict[str, object]


class CheckpointManager:
    """Durable, append-only store of per-chunk results keyed by chunk id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._results: dict[str, Result] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue  # tolerate a torn final line from a crash
            if isinstance(record, dict):
                cid = record.get("chunk_id")
                result = record.get("result")
                if isinstance(cid, str) and isinstance(result, dict):
                    self._results[cid] = result  # last write wins

    def is_done(self, chunk_id: str) -> bool:
        return chunk_id in self._results

    def get(self, chunk_id: str) -> Result | None:
        return self._results.get(chunk_id)

    def record(self, chunk_id: str, result: Result) -> None:
        """Persist ``result`` for ``chunk_id`` (append + flush) and update memory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"chunk_id": chunk_id, "result": result}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        self._results[chunk_id] = result

    def results(self) -> dict[str, Result]:
        return dict(self._results)

    def clear(self) -> None:
        self._results.clear()
        self.path.unlink(missing_ok=True)
