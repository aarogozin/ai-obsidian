from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .soul import SOUL_FILENAME


IGNORED_DIRS = {
    ".git",
    ".obsidian",
    ".trash",
    "__pycache__",
}


@dataclass
class Note:
    path: Path
    relative_path: str
    text: str


def collect_notes(vault_path: Path, max_files: int = 30, max_bytes_per_file: int = 12_000) -> list[Note]:
    notes: list[Note] = []
    for path in sorted(vault_path.rglob("*.md")):
        if len(notes) >= max_files:
            break
        relative_parts = path.relative_to(vault_path).parts
        if len(relative_parts) == 1 and relative_parts[0] == SOUL_FILENAME:
            continue
        if any(part in IGNORED_DIRS for part in relative_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        notes.append(
            Note(
                path=path,
                relative_path=path.relative_to(vault_path).as_posix(),
                text=text[:max_bytes_per_file],
            )
        )
    return notes


def read_note(vault_path: Path, relative_path: str) -> Note:
    path = safe_note_path(vault_path, relative_path)
    if not path.exists():
        raise VaultError(f"Note does not exist: {relative_path}")
    if not path.is_file() or path.suffix.lower() != ".md":
        raise VaultError(f"Path is not a markdown note: {relative_path}")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    return Note(path=path, relative_path=path.relative_to(vault_path).as_posix(), text=text)


def safe_note_path(vault_path: Path, relative_path: str) -> Path:
    path = (vault_path / relative_path).resolve()
    try:
        path.relative_to(vault_path.resolve())
    except ValueError as exc:
        raise VaultError(f"Path escapes vault: {relative_path}") from exc
    return path


def build_context(notes: list[Note], max_chars: int = 36_000) -> str:
    chunks: list[str] = []
    used = 0
    for note in notes:
        chunk = f"\n--- {note.relative_path} ---\n{note.text.strip()}\n"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining <= 500:
                break
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        used += len(chunk)
    return "".join(chunks).strip()


class VaultError(RuntimeError):
    pass
