from __future__ import annotations

import builtins
import subprocess
import tempfile
from pathlib import Path

from ai_obsidian import chat, cli
from ai_obsidian.chat_providers import (
    ExternalProviderError,
    ask_external_provider,
    build_provider_invocation,
    external_engine_status,
)


def test_hermes_adapter_builds_oneshot_command(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda name: f"/bin/{name}" if name == "hermes" else None)

    invocation = build_provider_invocation("hermes", "summarize")

    assert invocation.command == ["/bin/hermes", "--ignore-rules", "-z", "summarize"]
    assert invocation.stdin is None


def test_claude_adapter_builds_read_only_print_command(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda name: f"/bin/{name}" if name == "claude" else None)

    invocation = build_provider_invocation("claude", "summarize")

    assert invocation.command == [
        "/bin/claude",
        "--print",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
    ]
    assert invocation.stdin == "summarize"


def test_missing_external_executable_returns_actionable_status(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda _name: None)

    status = external_engine_status("hermes")

    assert status.available is False
    assert "not installed or not on PATH" in status.detail


def test_missing_external_executable_raises_actionable_error(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda _name: None)

    try:
        ask_external_provider("claude", "hello")
    except ExternalProviderError as exc:
        assert "Claude Code CLI is not installed" in str(exc)
    else:
        raise AssertionError("expected missing executable error")


def test_external_provider_nonzero_exit_is_concise(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda name: f"/bin/{name}")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["/bin/hermes"],
            returncode=1,
            stdout="",
            stderr="line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        ask_external_provider("hermes", "hello")
    except ExternalProviderError as exc:
        message = str(exc)
        assert "hermes exited with code 1" in message
        assert "line8" in message
        assert "line9" not in message
    else:
        raise AssertionError("expected nonzero exit error")


def test_external_provider_runs_outside_caller_working_directory(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda name: f"/bin/{name}")
    seen_kwargs: dict[str, object] = {}

    def fake_run(*_args, **kwargs):
        seen_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args=["/bin/hermes"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ask_external_provider("hermes", "hello") == "ok"
    assert seen_kwargs["cwd"] == tempfile.gettempdir()


def test_external_engine_once_answers_without_modifying_notes(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "Main"
    note = vault / "note.md"
    vault.mkdir()
    note.write_text("original\n", encoding="utf-8")
    prompts: list[str] = []

    def fake_ask(engine: str, prompt: str):
        prompts.append(f"{engine}:{prompt}")
        return "summary"

    monkeypatch.setattr(chat, "ask_external_provider", fake_ask)

    status = chat.run_external_chat(vault, "hermes", max_files=30, once="summarize")

    assert status == 0
    assert "summary" in capsys.readouterr().out
    assert note.read_text(encoding="utf-8") == "original\n"
    assert "note.md" in prompts[0]
    assert str(vault) not in prompts[0]


def test_external_engine_rejects_edit_without_modifying_notes(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "Main"
    note = vault / "note.md"
    vault.mkdir()
    note.write_text("original\n", encoding="utf-8")
    inputs = iter(["/edit note.md rewrite it", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(inputs))
    monkeypatch.setattr(chat, "ask_external_provider", lambda _engine, _prompt: "should not run")

    status = chat.run_external_chat(vault, "claude", max_files=30)

    assert status == 0
    assert "Safe note edits are only available with --engine builtin." in capsys.readouterr().out
    assert note.read_text(encoding="utf-8") == "original\n"


def test_cli_routes_hermes_chat_to_external_provider(tmp_path, monkeypatch):
    vault = tmp_path / "Main"
    vault.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    cli.save_config(
        {
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "default_vault": "Main",
            "chat": {"default_engine": "builtin"},
        }
    )
    calls: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        cli,
        "run_external_chat",
        lambda vault_path, engine, max_files, once=None: calls.append((vault_path, engine, once or "")) or 0,
    )

    status = cli.cmd_chat(
        type(
            "Args",
            (),
            {
                "vault": None,
                "engine": "hermes",
                "model": None,
                "base_url": None,
                "api_key": None,
                "max_files": 30,
                "once": "hello",
            },
        )()
    )

    assert status == 0
    assert calls == [(vault, "hermes", "hello")]


def test_doctor_json_includes_external_engine_status(monkeypatch):
    monkeypatch.setattr("ai_obsidian.chat_providers.find_executable", lambda name: f"/bin/{name}" if name == "hermes" else None)
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda _client: (None, RuntimeError("offline")))

    health = cli.collect_health()

    assert health["external_engines"]["hermes"]["available"] is True
    assert health["external_engines"]["claude"]["available"] is False
