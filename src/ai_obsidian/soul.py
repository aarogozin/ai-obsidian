from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SOUL_FILENAME = "soul.md"
SOUL_BLOCK_START = "<!-- ai-obsidian:soul:start -->"
SOUL_BLOCK_END = "<!-- ai-obsidian:soul:end -->"

DEFAULT_SOUL_TEXT = """# Vault Soul

## Language
Write notes in Russian by default.
Use English for code, exact source names, technical terms, and quoted material.

## Agent Behavior
Be concise, practical, and specific.
Ask clarifying questions before large restructures.
Preserve the user's original meaning and voice.

## Note Style
Use Markdown headings and short sections.
Prefer useful links over generic summaries.
Avoid excessive nesting, tags, or folder creation.

## Research
Prefer primary sources, official docs, papers, source repositories, and direct product pages.
When using web research, include links and mark uncertainty.

## Safety
Never delete notes.
Never silently rewrite notes.
For note edits, show a diff and wait for explicit confirmation.
"""


@dataclass(frozen=True)
class SoulStatus:
    path: Path
    exists: bool
    readable: bool
    detail: str


def soul_path(vault_path: Path) -> Path:
    return vault_path / SOUL_FILENAME


def soul_status(vault_path: Path) -> SoulStatus:
    path = soul_path(vault_path)
    if not path.exists():
        return SoulStatus(path=path, exists=False, readable=False, detail="missing")
    if not path.is_file():
        return SoulStatus(path=path, exists=True, readable=False, detail="not a file")
    try:
        path.read_text(encoding="utf-8")
    except OSError as exc:
        return SoulStatus(path=path, exists=True, readable=False, detail=str(exc))
    except UnicodeDecodeError:
        return SoulStatus(path=path, exists=True, readable=False, detail="not valid UTF-8")
    return SoulStatus(path=path, exists=True, readable=True, detail="ok")


def read_soul(vault_path: Path) -> str:
    status = soul_status(vault_path)
    if not status.exists or not status.readable:
        return ""
    return status.path.read_text(encoding="utf-8").strip()


def create_soul(vault_path: Path) -> bool:
    path = soul_path(vault_path)
    if path.exists():
        return False
    path.write_text(DEFAULT_SOUL_TEXT, encoding="utf-8")
    return True


def ensure_soul(
    vault_path: Path,
    *,
    ask_yes_no=None,
    default: bool = True,
    force_prompt: bool = False,
) -> bool:
    path = soul_path(vault_path)
    if path.exists():
        return False
    if force_prompt and ask_yes_no is not None:
        if not ask_yes_no(f"Create vault instructions at {path}?", default=default):
            return False
    return create_soul(vault_path)


def soul_instruction_block(vault_path: Path) -> str:
    text = read_soul(vault_path)
    if not text:
        return ""
    return f"Vault instructions from {SOUL_FILENAME}:\n{text}"


def managed_soul_block(soul_text: str) -> str:
    return (
        f"{SOUL_BLOCK_START}\n"
        f"AI Obsidian vault instructions from {SOUL_FILENAME}:\n\n"
        f"{soul_text.strip()}\n"
        f"{SOUL_BLOCK_END}"
    )


def sync_soul_managed_block(existing_prompt: str, soul_text: str) -> str:
    if not soul_text.strip():
        return existing_prompt
    block = managed_soul_block(soul_text)
    start = existing_prompt.find(SOUL_BLOCK_START)
    end = existing_prompt.find(SOUL_BLOCK_END)
    if start != -1 and end != -1 and end > start:
        end += len(SOUL_BLOCK_END)
        return f"{existing_prompt[:start].rstrip()}\n\n{block}\n\n{existing_prompt[end:].lstrip()}".strip()
    if not existing_prompt.strip():
        return block
    return f"{existing_prompt.rstrip()}\n\n{block}"


def prompt_has_current_soul(prompt: str, soul_text: str) -> bool:
    if not soul_text.strip():
        return False
    return managed_soul_block(soul_text) in prompt
