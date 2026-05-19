from __future__ import annotations

from argparse import Namespace

from ai_obsidian import cli


def test_install_dry_run_does_not_run_commands(monkeypatch, capsys):
    called: list[str] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: called.append("install") or 0)

    status = cli.cmd_install(Namespace(dry_run=True, execute=False, yes=False))

    assert status == 0
    assert called == []
    assert "Install plan:" in capsys.readouterr().out


def test_install_execute_uses_only_non_destructive_brew_commands(monkeypatch):
    called: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: called.append(kwargs) or 0)

    status = cli.cmd_install(Namespace(dry_run=False, execute=True, yes=False))

    assert status == 0
    assert called == [{"interactive": False, "start_omlx_service": True, "allow_homebrew_install": False}]


def test_install_execute_skips_existing_packages(monkeypatch):
    called: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: called.append(kwargs) or 0)

    status = cli.cmd_install(Namespace(dry_run=False, execute=True, yes=True))

    assert status == 0
    assert called == [{"interactive": False, "start_omlx_service": True, "allow_homebrew_install": True}]


def test_install_only_hermes_skips_core_prerequisites(monkeypatch):
    core_calls: list[dict[str, object]] = []
    hermes_calls: list[bool] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: core_calls.append(kwargs) or 0)
    monkeypatch.setattr(
        cli,
        "ensure_hermes_cli_installed",
        lambda *, allow_install: hermes_calls.append(allow_install) or 0,
    )

    status = cli.cmd_install(Namespace(dry_run=False, execute=True, yes=True, only_hermes=True))

    assert status == 0
    assert core_calls == []
    assert hermes_calls == [True]


def test_install_with_hermes_runs_after_core_prerequisites(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: calls.append("core") or 0)
    monkeypatch.setattr(cli, "ensure_hermes_cli_installed", lambda *, allow_install: calls.append("hermes") or 0)

    status = cli.cmd_install(Namespace(dry_run=False, execute=True, yes=True, with_hermes=True))

    assert status == 0
    assert calls == ["core", "hermes"]
