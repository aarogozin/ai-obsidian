from __future__ import annotations

import difflib
from pathlib import Path

from .chat_providers import ExternalProviderError, ask_external_provider
from .omlx import OmlxClient, OmlxError
from .soul import soul_instruction_block
from .vault import VaultError, build_context, collect_notes, read_note


SYSTEM_PROMPT = """You are a local AI assistant working with an Obsidian vault.
Help the user summarize, organize, and improve Markdown notes.
Be concise. Mention note filenames when your answer depends on specific notes.
Do not claim that you changed files unless the tool explicitly applies an edit."""


EXTERNAL_SYSTEM_PROMPT = """You are a read-only AI Obsidian chat provider.
The user is working with an Obsidian vault, but you only receive selected note context in this prompt.
Do not modify files, run commands, or claim you changed notes.
If the user asks for note edits, describe the proposed changes and remind them to use `--engine builtin` for safe diff/confirmation edits.
Be concise. Mention note filenames when your answer depends on specific notes."""


EDIT_SYSTEM_PROMPT = """You rewrite one Obsidian Markdown note.
Return only the complete new Markdown file content.
Do not wrap the answer in a code block.
Preserve useful existing content unless the user asks to remove it."""


def run_builtin_chat(
    vault_path: Path,
    client: OmlxClient,
    model: str | None,
    max_files: int,
    once: str | None = None,
) -> int:
    try:
        selected_model = client.choose_model(model)
    except OmlxError as exc:
        print(exc)
        return 1

    notes = collect_notes(vault_path, max_files=max_files)
    context = build_context(notes)
    soul = soul_instruction_block(vault_path)
    history: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt_with_soul(SYSTEM_PROMPT, soul)},
        {
            "role": "user",
            "content": f"Vault path: {vault_path}\nAvailable note context:\n{context or '(No markdown notes found.)'}",
        },
    ]

    print(f"Vault: {vault_path}")
    print(f"Model: {selected_model}")
    print(f"Loaded notes: {len(notes)}")

    if once:
        return ask_once(client, selected_model, history, once)

    print("Commands: /help, /files, /read <note.md>, /edit <note.md> <instruction>, /exit")
    while True:
        try:
            user_input = input("\nai-obsidian> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print_help()
            continue
        if user_input == "/files":
            for note in notes:
                print(note.relative_path)
            continue
        if user_input.startswith("/read "):
            show_note(vault_path, user_input.removeprefix("/read ").strip())
            continue
        if user_input.startswith("/edit "):
            handle_edit(vault_path, client, selected_model, user_input.removeprefix("/edit ").strip())
            notes = collect_notes(vault_path, max_files=max_files)
            context = build_context(notes)
            history[1] = {
                "role": "user",
                "content": f"Vault path: {vault_path}\nAvailable note context:\n{context or '(No markdown notes found.)'}",
            }
            continue

        history.append({"role": "user", "content": user_input})
        try:
            answer = client.chat(selected_model, history)
        except OmlxError as exc:
            print(exc)
            continue
        history.append({"role": "assistant", "content": answer})
        print(f"\n{answer}")


def run_external_chat(
    vault_path: Path,
    engine: str,
    max_files: int,
    once: str | None = None,
) -> int:
    notes = collect_notes(vault_path, max_files=max_files)
    context = build_context(notes)
    soul = soul_instruction_block(vault_path)
    history: list[dict[str, str]] = []

    print(f"Vault: {vault_path}")
    print(f"Engine: {engine}")
    print(f"Loaded notes: {len(notes)}")
    print("External engines are read-only in AI Obsidian. Use --engine builtin for safe note edits.")

    if once:
        return ask_external_once(engine, context, history, once, soul=soul)

    print("Commands: /help, /files, /read <note.md>, /edit <note.md> <instruction>, /exit")
    while True:
        try:
            user_input = input("\nai-obsidian> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print_external_help()
            continue
        if user_input == "/files":
            for note in notes:
                print(note.relative_path)
            continue
        if user_input.startswith("/read "):
            show_note(vault_path, user_input.removeprefix("/read ").strip())
            continue
        if user_input.startswith("/edit "):
            print("Safe note edits are only available with --engine builtin.")
            continue

        status, answer = ask_external(engine, context, history, user_input, soul=soul)
        if status != 0:
            continue
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})
        print(f"\n{answer}")


def ask_once(client: OmlxClient, model: str, history: list[dict[str, str]], prompt: str) -> int:
    history.append({"role": "user", "content": prompt})
    try:
        print(client.chat(model, history))
    except OmlxError as exc:
        print(exc)
        return 1
    return 0


def ask_external_once(engine: str, context: str, history: list[dict[str, str]], prompt: str, soul: str = "") -> int:
    status, answer = ask_external(engine, context, history, prompt, soul=soul)
    if status != 0:
        return status
    print(answer)
    return 0


def ask_external(engine: str, context: str, history: list[dict[str, str]], prompt: str, soul: str = "") -> tuple[int, str]:
    provider_prompt = build_external_provider_prompt(context, history, prompt, soul=soul)
    try:
        answer = ask_external_provider(engine, provider_prompt)
    except ExternalProviderError as exc:
        print(exc)
        return 1, ""
    return 0, answer


def build_external_provider_prompt(context: str, history: list[dict[str, str]], prompt: str, soul: str = "") -> str:
    recent_history = history[-8:]
    history_text = "\n".join(
        f"{message['role'].title()}: {message['content']}"
        for message in recent_history
    )
    if not history_text:
        history_text = "(No previous chat turns.)"

    note_context = context or "(No markdown notes found.)"
    soul_section = f"\n\n{soul}" if soul else ""
    return (
        f"{EXTERNAL_SYSTEM_PROMPT}{soul_section}\n\n"
        f"Available note context:\n{note_context}\n\n"
        f"Recent conversation:\n{history_text}\n\n"
        f"User: {prompt}\n"
        "Assistant:"
    )


def print_help() -> None:
    print("/files")
    print("  List markdown files loaded from the vault.")
    print("/read <note.md>")
    print("  Print a note.")
    print("/edit <note.md> <instruction>")
    print("  Ask the model to rewrite one note, show a diff, then ask before writing.")
    print("/exit")
    print("  Leave the chat.")


def print_external_help() -> None:
    print("/files")
    print("  List markdown files loaded from the vault.")
    print("/read <note.md>")
    print("  Print a note.")
    print("/edit <note.md> <instruction>")
    print("  Not available for external engines. Use --engine builtin for safe note edits.")
    print("/exit")
    print("  Leave the chat.")


def show_note(vault_path: Path, relative_path: str) -> None:
    try:
        note = read_note(vault_path, relative_path)
    except VaultError as exc:
        print(exc)
        return
    print(f"\n--- {note.relative_path} ---")
    print(note.text)


def handle_edit(vault_path: Path, client: OmlxClient, model: str, command: str) -> None:
    if " " not in command:
        print("Usage: /edit <note.md> <instruction>")
        return

    relative_path, instruction = command.split(" ", maxsplit=1)
    try:
        note = read_note(vault_path, relative_path)
    except VaultError as exc:
        print(exc)
        return

    messages = [
        {
            "role": "system",
            "content": system_prompt_with_soul(EDIT_SYSTEM_PROMPT, soul_instruction_block(vault_path)),
        },
        {
            "role": "user",
            "content": (
                f"File: {note.relative_path}\n\n"
                f"Instruction: {instruction}\n\n"
                f"Current content:\n{note.text}"
            ),
        },
    ]
    try:
        new_text = client.chat(model, messages)
    except OmlxError as exc:
        print(exc)
        return

    old_lines = note.text.splitlines(keepends=True)
    new_lines = ensure_trailing_newline(new_text).splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{note.relative_path}",
        tofile=f"b/{note.relative_path}",
    )
    print("".join(diff) or "No changes proposed.")
    if old_lines == new_lines:
        return

    confirmation = input("Apply this edit? [y/N] ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("Edit skipped.")
        return

    note.path.write_text("".join(new_lines), encoding="utf-8")
    print(f"Updated {note.relative_path}")


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def system_prompt_with_soul(base_prompt: str, soul: str) -> str:
    if not soul:
        return base_prompt
    return f"{base_prompt}\n\n{soul}"
