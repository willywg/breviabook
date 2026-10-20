"""Book discovery and durable launcher state; no engine dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

PROVIDER_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": None,
}
FORMATS = ("epub", "pdf", "md")


@dataclass(frozen=True)
class Options:
    provider: str
    model: str
    target_ratio: float = 0.30
    formats: tuple[str, ...] = FORMATS
    concurrency: int = 4

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_KEYS or not self.model.strip():
            raise ValueError("Choose a supported provider and a nonempty model ID.")
        if not 0 < self.target_ratio <= 1:
            raise ValueError("Target ratio must be greater than 0 and at most 1.")
        if not self.formats or any(fmt not in FORMATS for fmt in self.formats):
            raise ValueError("Formats must be a comma-separated list of epub,pdf,md.")
        if self.concurrency < 1:
            raise ValueError("Concurrency must be at least 1.")

    @classmethod
    def from_json(cls, value: object) -> Options:
        if not isinstance(value, dict):
            raise ValueError("Invalid saved options.")
        provider, model = value.get("provider"), value.get("model")
        ratio, formats = value.get("target_ratio"), value.get("formats")
        concurrency = value.get("concurrency")
        if (
            not isinstance(provider, str)
            or not isinstance(model, str)
            or not isinstance(ratio, (float, int))
            or isinstance(ratio, bool)
            or not isinstance(formats, list)
            or not all(isinstance(fmt, str) for fmt in formats)
            or not isinstance(concurrency, int)
            or isinstance(concurrency, bool)
        ):
            raise ValueError("Invalid saved options.")
        return cls(provider, model, float(ratio), tuple(formats), concurrency)


@dataclass(frozen=True)
class Book:
    path: Path
    relative_path: Path
    identity: str

    @property
    def directory_name(self) -> str:
        title = re.sub(r"[^\w-]+", "-", self.path.stem).strip("-_")[:40] or "book"
        return f"book-{title}-{self.identity[:16]}"


def discover_books(input_dir: Path) -> list[Book]:
    books = []
    for path in sorted(input_dir.rglob("*"), key=lambda p: p.as_posix().casefold()):
        if not path.is_file() or path.suffix.lower() not in (".epub", ".pdf"):
            continue
        # The Docker input mount cannot access files linked outside this folder.
        if not path.resolve().is_relative_to(input_dir.resolve()):
            continue
        relative = path.relative_to(input_dir)
        with path.open("rb") as stream:
            content_hash = hashlib.file_digest(stream, "sha256").digest()
        identity = hashlib.sha256(relative.as_posix().encode() + b"\0" + content_hash)
        books.append(Book(path, relative, identity.hexdigest()))
    return books


@dataclass
class Job:
    book: Book
    output_root: Path
    attempt: int = 0
    options: Options | None = None
    completed: bool = False
    legacy: bool = False

    @property
    def directory(self) -> Path:
        return self.output_root / self.book.directory_name

    @property
    def metadata_path(self) -> Path:
        return self.directory / "job.json"

    @property
    def output_dir(self) -> Path:
        if self.legacy:
            return self.output_root
        return self.directory / f"run-{self.attempt:03d}"

    @property
    def checkpoint_path(self) -> Path:
        return self.output_dir / ".breviabook" / f"{self.book.path.stem}-condensed.jsonl"

    def missing_outputs(self) -> list[str]:
        if self.options is None:
            return []
        names = [f"{self.book.path.stem}-condensed.{fmt}" for fmt in self.options.formats]
        return [
            name
            for name in names
            if not (self.output_dir / name).is_file()
            or (self.output_dir / name).stat().st_size == 0
        ]

    @property
    def status(self) -> str:
        if self.completed and not self.missing_outputs():
            return "Completed"
        if self.options is not None:
            return "Resume"
        if self.legacy:
            return "Existing files (completion unknown)"
        return "New"

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "book_id": self.book.identity,
            "source": self.book.relative_path.as_posix(),
            "attempt": self.attempt,
            "options": asdict(self.options) if self.options else None,
            "completed": self.completed,
            "legacy": self.legacy,
        }
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(self.metadata_path)

    def start(self, options: Options, *, resume: bool) -> None:
        if not resume:
            self.attempt += 1
            self.legacy = False
        self.options = options
        self.completed = False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.save()


def load_job(book: Book, output_root: Path, *, allow_legacy: bool = True) -> Job:
    job = Job(book, output_root)
    if job.metadata_path.exists():
        try:
            data = json.loads(job.metadata_path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or data.get("book_id") != book.identity
                or type(data.get("attempt")) is not int
                or data["attempt"] < 0
                or type(data.get("completed")) is not bool
                or type(data.get("legacy")) is not bool
                or (data["attempt"] == 0 and not data["legacy"])
            ):
                raise ValueError("Invalid job record.")
            job.options = Options.from_json(data.get("options"))
            job.attempt = data["attempt"]
            job.completed = data["completed"]
            job.legacy = data["legacy"]
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError(
                f"Cannot read {job.metadata_path}. Move that record aside and retry; "
                "keep the checkpoint files."
            ) from exc
    elif allow_legacy:
        stem = book.path.stem
        existing = [output_root / f"{stem}-condensed.{fmt}" for fmt in FORMATS]
        checkpoint = output_root / ".breviabook" / f"{stem}-condensed.jsonl"
        job.legacy = checkpoint.is_file() or any(path.is_file() for path in existing)
    return job
