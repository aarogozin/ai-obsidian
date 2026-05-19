from __future__ import annotations

import json
from pathlib import Path

from ai_obsidian import cli
from ai_obsidian.prerequisites import PrerequisiteStatus


def test_setup_status_json_reports_without_mutating_notes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = tmp_path / "Vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("keep me\n", encoding="utf-8")
    cli.save_config(
        {
            "vaults": {"Vault": {"name": "Vault", "path": str(vault)}},
            "default_vault": "Vault",
            "omlx": {"api_key": "secret", "selected_model": "local-model"},
        }
    )
    monkeypatch.setattr(
        cli,
        "check_prerequisites",
        lambda: PrerequisiteStatus(True, True, "/opt/homebrew/bin/brew", True, True, "hf", "ffmpeg", True),
    )
    monkeypatch.setattr(cli, "discover_downloaded_models", lambda: [])
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: ([], None))

    status = cli.cmd_setup_status(type("Args", (), {"json": True})())

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["config"]["omlx"]["api_key"] == ""
    assert payload["config"]["omlx"]["api_key_configured"] is True
    assert payload["vaults"][0]["exists"] is True
    assert note.read_text(encoding="utf-8") == "keep me\n"


def test_setup_models_json_puts_downloaded_models_before_remote(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    downloaded = cli.DownloadedModel(
        id="mlx-community/Qwen3-local-4bit",
        source="LM Studio",
        format="MLX",
        path=tmp_path / "models" / "Qwen3-local-4bit",
        size_bytes=123,
    )
    remote = [
        cli.ModelChoice("mlx-community/Qwen3-1.7B-4bit", "Qwen3 1.7B", 16, "qwen", "remote"),
        cli.ModelChoice("mlx-community/Qwen3-35B-4bit", "Qwen3 35B", 64, "qwen", "too large"),
    ]
    monkeypatch.setattr(cli, "discover_downloaded_models", lambda: [downloaded])
    monkeypatch.setattr(cli, "load_model_choices", lambda load_remote_models, searches=None: (remote, "test"))
    monkeypatch.setattr(cli, "system_memory_gb", lambda: 16)

    status = cli.cmd_setup_models(
        type(
            "Args",
            (),
            {"json": True, "offline": False, "family": "qwen", "version": None, "size": None, "model_dir": None},
        )()
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["downloaded"][0]["id"] == "mlx-community/Qwen3-local-4bit"
    assert [model["repo_id"] for model in payload["remote"]] == ["mlx-community/Qwen3-1.7B-4bit"]


def test_setup_models_json_includes_explicit_model_dir_without_name_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    configured_dir = tmp_path / "configured"
    local = cli.DownloadedMlxModel(
        id="mlx-community/Qwen3-configured-4bit",
        source="configured",
        model_dir=configured_dir,
        path=configured_dir / "mlx-community" / "Qwen3-configured-4bit",
        size_bytes=456,
        safetensor_count=2,
    )
    monkeypatch.setattr(cli, "discover_downloaded_models", lambda: [])
    monkeypatch.setattr(cli, "discover_downloaded_mlx_models", lambda model_dir=None: [local] if model_dir == configured_dir else [])
    monkeypatch.setattr(cli, "load_model_choices", lambda load_remote_models, searches=None: ([], "offline fallback list"))
    monkeypatch.setattr(cli, "system_memory_gb", lambda: 64)

    status = cli.cmd_setup_models(
        type(
            "Args",
            (),
            {
                "json": True,
                "offline": False,
                "family": None,
                "version": None,
                "size": None,
                "model_dir": str(configured_dir),
            },
        )()
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["downloaded"][0]["id"] == "mlx-community/Qwen3-configured-4bit"


def test_setup_apply_dry_run_validates_profile_without_creating_vault(tmp_path, capsys):
    profile = tmp_path / "profile.json"
    vault = tmp_path / "Main"
    profile.write_text(
        json.dumps(
            {
                "omlx": {
                    "mode": "service",
                    "model_dir": str(tmp_path / "models"),
                    "selected_model": "mlx-community/Qwen3-1.7B-4bit",
                },
                "vault": {"mode": "create", "name": "Main", "path": str(vault)},
                "chat": {"default_engine": "builtin"},
                "plugins": {"install_hub": False, "install_companion": False},
                "launch": {"start_stack": False, "open_obsidian": False},
            }
        ),
        encoding="utf-8",
    )

    status = cli.cmd_setup_apply(type("Args", (), {"profile": str(profile), "yes": True, "dry_run": True})())

    assert status == 0
    assert not vault.exists()
    assert json.loads(capsys.readouterr().out)["plan"]["vault"]["path"] == str(vault.resolve())


def test_setup_apply_creates_vault_soul_and_config_idempotently(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    profile = tmp_path / "profile.json"
    vault = tmp_path / "Main"
    profile.write_text(
        json.dumps(
            {
                "omlx": {
                    "mode": "service",
                    "api_key": "test-key",
                    "model_dir": str(tmp_path / "models"),
                    "selected_model": "mlx-community/Qwen3-1.7B-4bit",
                },
                "vault": {"mode": "create", "name": "Main", "path": str(vault)},
                "chat": {"default_engine": "builtin"},
                "plugins": {"install_hub": False, "install_companion": False},
                "launch": {"start_stack": False, "open_obsidian": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: 0)

    args = type("Args", (), {"profile": str(profile), "yes": True, "dry_run": False})()
    assert cli.cmd_setup_apply(args) == 0
    soul = vault / "soul.md"
    original = soul.read_text(encoding="utf-8")
    soul.write_text("custom soul\n", encoding="utf-8")

    assert cli.cmd_setup_apply(args) == 0

    config = cli.load_config()
    assert config["default_vault"] == "Main"
    assert config["vaults"]["Main"]["path"] == str(vault.resolve())
    assert config["omlx"]["selected_model"] == "mlx-community/Qwen3-1.7B-4bit"
    assert soul.read_text(encoding="utf-8") == "custom soul\n"
    assert original.startswith("# Vault Soul")


def test_setup_apply_blank_api_key_preserves_existing_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cli.save_config({"omlx": {"api_key": "keep-me"}})
    profile = tmp_path / "profile.json"
    vault = tmp_path / "Main"
    profile.write_text(
        json.dumps(
            {
                "omlx": {
                    "mode": "service",
                    "api_key": "",
                    "model_dir": str(tmp_path / "models"),
                    "selected_model": "mlx-community/Qwen3-1.7B-4bit",
                },
                "vault": {"mode": "create", "name": "Main", "path": str(vault)},
                "plugins": {"install_hub": False, "install_companion": False},
                "launch": {"start_stack": False, "open_obsidian": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: 0)

    status = cli.cmd_setup_apply(type("Args", (), {"profile": str(profile), "yes": True, "dry_run": False})())

    assert status == 0
    assert cli.load_config()["omlx"]["api_key"] == "keep-me"
