from __future__ import annotations

import json

from ai_obsidian import cli
from ai_obsidian import obsidian_plugin


def configured_ai_obsidian(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = tmp_path / "Main"
    (vault / ".obsidian").mkdir(parents=True)
    cli.save_config(
        {
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "default_vault": "Main",
            "omlx": {
                "base_url": "http://localhost:8000/v1",
                "api_key": "secret",
                "selected_model": "Qwen3.6-27B-4bit",
            },
        }
    )
    return vault


def test_plugin_status_detects_installed_enabled_and_configured(tmp_path):
    vault = tmp_path / "Main"
    plugin_dir = vault / ".obsidian" / "plugins" / "local-llm-hub"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text('{"version":"0.12.6"}', encoding="utf-8")
    (plugin_dir / "main.js").write_text("", encoding="utf-8")
    (plugin_dir / "data.json").write_text("{}", encoding="utf-8")
    (vault / ".obsidian" / "community-plugins.json").write_text('["local-llm-hub"]', encoding="utf-8")

    status = obsidian_plugin.plugin_status(vault, "local-llm-hub")

    assert status.installed is True
    assert status.enabled is True
    assert status.configured is True
    assert status.version == "0.12.6"


def test_install_plugin_downloads_release_assets_and_enables_plugin(tmp_path, monkeypatch):
    vault = tmp_path / "Main"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr(
        obsidian_plugin,
        "latest_release_assets",
        lambda repo: {
            "main.js": "https://example.test/main.js",
            "manifest.json": "https://example.test/manifest.json",
            "styles.css": "https://example.test/styles.css",
        },
    )

    def fake_download(url, destination):
        destination.write_text(url, encoding="utf-8")

    monkeypatch.setattr(obsidian_plugin, "download_url", fake_download)

    status = obsidian_plugin.install_plugin(vault, "local-llm-hub")

    assert status == 0
    assert (vault / ".obsidian" / "plugins" / "local-llm-hub" / "main.js").exists()
    enabled = json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
    assert "local-llm-hub" in enabled


def test_install_companion_plugin_uses_bundled_assets_and_preserves_notes(tmp_path):
    vault = tmp_path / "Main"
    note = vault / "meeting.md"
    (vault / ".obsidian").mkdir(parents=True)
    note.write_text("do not touch\n", encoding="utf-8")

    status = obsidian_plugin.install_plugin(vault, "companion")

    plugin_dir = vault / ".obsidian" / "plugins" / "ai-obsidian-companion"
    assert status == 0
    assert (plugin_dir / "manifest.json").exists()
    assert (plugin_dir / "main.js").exists()
    assert (plugin_dir / "styles.css").exists()
    assert note.read_text(encoding="utf-8") == "do not touch\n"
    enabled = json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
    assert "ai-obsidian-companion" in enabled


def test_install_companion_plugin_refreshes_changed_bundled_assets(tmp_path):
    vault = tmp_path / "Main"
    plugin_dir = vault / ".obsidian" / "plugins" / "ai-obsidian-companion"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text('{"id":"ai-obsidian-companion","version":"0.0.0"}', encoding="utf-8")
    (plugin_dir / "main.js").write_text("old", encoding="utf-8")
    (plugin_dir / "styles.css").write_text("old", encoding="utf-8")

    status = obsidian_plugin.install_plugin(vault, "companion")

    assert status == 0
    assert (plugin_dir / "main.js").read_text(encoding="utf-8") != "old"


def test_configure_local_llm_hub_writes_diff_confirmed_settings(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()

    status = obsidian_plugin.configure_plugin(
        vault,
        config,
        "local-llm-hub",
        ask_yes_no=lambda prompt, default: True,
    )

    assert status == 0
    data = json.loads((vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json").read_text(encoding="utf-8"))
    assert data["llmConfig"]["framework"] == "lm-studio"
    assert data["llmConfig"]["baseUrl"] == "http://localhost:8000"
    assert data["llmConfig"]["model"] == "Qwen3.6-27B-4bit"
    assert data["llmConfig"]["apiKey"] == "secret"


def test_verify_local_llm_hub_checks_active_model_and_endpoint(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    plugin_dir = vault / ".obsidian" / "plugins" / "local-llm-hub"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text('{"version":"0.12.6"}', encoding="utf-8")
    (plugin_dir / "main.js").write_text("", encoding="utf-8")
    (plugin_dir / "styles.css").write_text("", encoding="utf-8")
    (vault / ".obsidian" / "community-plugins.json").write_text('["local-llm-hub"]', encoding="utf-8")
    obsidian_plugin.configure_plugin(vault, config, "local-llm-hub", yes=True)

    verification = obsidian_plugin.verify_plugin_with_config(vault, "local-llm-hub", config=config)

    assert verification.ok is True
    labels = {label for label, _, _ in verification.checks}
    assert {"base URL", "model", "API key", "availableModels"} <= labels


def test_verify_local_llm_hub_reports_stale_model(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    data_path = vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps(
            {
                "llmConfig": {
                    "framework": "lm-studio",
                    "baseUrl": "http://localhost:8000",
                    "model": "old-model",
                    "apiKey": "secret",
                },
                "availableModels": ["old-model"],
            }
        ),
        encoding="utf-8",
    )

    verification = obsidian_plugin.verify_plugin_with_config(vault, "local-llm-hub", config=config)

    assert verification.ok is False
    failed = {label for label, ok, _ in verification.checks if not ok}
    assert {"model", "availableModels"} <= failed


def test_configure_companion_plugin_writes_cli_and_voice_settings(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    config["voice"] = {"language": "ru"}
    config["companion"] = {
        "cli_path": "/usr/local/bin/ai-obsidian",
        "target_mode": "chat",
        "insert_mode": "append",
        "confirm_before_insert": False,
    }

    status = obsidian_plugin.configure_plugin(
        vault,
        config,
        "companion",
        ask_yes_no=lambda prompt, default: True,
    )

    assert status == 0
    data = json.loads(
        (vault / ".obsidian" / "plugins" / "ai-obsidian-companion" / "data.json").read_text(encoding="utf-8")
    )
    assert data == {
        "cliPath": "/usr/local/bin/ai-obsidian",
        "language": "ru",
        "targetMode": "chat",
        "insertMode": "append",
        "confirmBeforeInsert": False,
    }


def test_verify_companion_plugin_checks_assets_settings_and_dependencies(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    config["companion"] = {"cli_path": "ai-obsidian"}
    obsidian_plugin.install_plugin(vault, "companion")
    obsidian_plugin.configure_plugin(vault, config, "companion", yes=True)
    monkeypatch.setattr(obsidian_plugin, "resolve_executable", lambda command: "/tmp/bin/ai-obsidian")
    monkeypatch.setattr(obsidian_plugin, "command_help_ok", lambda command: True)
    monkeypatch.setattr(obsidian_plugin.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(obsidian_plugin, "has_mlx_whisper_runtime", lambda: True)

    verification = obsidian_plugin.verify_plugin(vault, "companion")

    assert verification.ok is True
    labels = {label for label, _, _ in verification.checks}
    assert {"installed", "enabled", "configured", "target mode", "AI Obsidian CLI", "voice command", "ffmpeg", "mlx-whisper"} <= labels


def test_verify_companion_plugin_reports_missing_voice_dependencies(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    config["companion"] = {"cli_path": "ai-obsidian"}
    obsidian_plugin.install_plugin(vault, "companion")
    obsidian_plugin.configure_plugin(vault, config, "companion", yes=True)
    monkeypatch.setattr(obsidian_plugin, "resolve_executable", lambda command: None)
    monkeypatch.setattr(obsidian_plugin.shutil, "which", lambda name: None)
    monkeypatch.setattr(obsidian_plugin, "has_mlx_whisper_runtime", lambda: False)

    verification = obsidian_plugin.verify_plugin(vault, "companion")

    assert verification.ok is False
    failed = {label for label, ok, _ in verification.checks if not ok}
    assert {"AI Obsidian CLI", "ffmpeg", "mlx-whisper"} <= failed


def test_verify_companion_plugin_rejects_invalid_target_mode(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    obsidian_plugin.install_plugin(vault, "companion")
    obsidian_plugin.configure_plugin(vault, cli.load_config(), "companion", yes=True)
    data_path = vault / ".obsidian" / "plugins" / "ai-obsidian-companion" / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["targetMode"] = "nowhere"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(obsidian_plugin, "resolve_executable", lambda command: "/tmp/bin/ai-obsidian")
    monkeypatch.setattr(obsidian_plugin, "command_help_ok", lambda command: True)
    monkeypatch.setattr(obsidian_plugin.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(obsidian_plugin, "has_mlx_whisper_runtime", lambda: True)

    verification = obsidian_plugin.verify_plugin(vault, "companion")

    assert verification.ok is False
    assert ("target mode", False, "nowhere") in verification.checks


def test_configure_plugin_preserves_existing_settings_and_requires_confirmation(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    data_path = vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text('{"systemPrompt":"keep me"}\n', encoding="utf-8")

    status = obsidian_plugin.configure_plugin(
        vault,
        cli.load_config(),
        "local-llm-hub",
        ask_yes_no=lambda prompt, default: False,
    )

    assert status == 1
    assert json.loads(data_path.read_text(encoding="utf-8")) == {"systemPrompt": "keep me"}


def test_configure_local_llm_hub_removes_stale_api_key_when_config_has_none(tmp_path, monkeypatch):
    vault = configured_ai_obsidian(tmp_path, monkeypatch)
    config = cli.load_config()
    config["omlx"]["api_key"] = ""
    data_path = vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text('{"llmConfig":{"apiKey":"old-secret"}}\n', encoding="utf-8")

    status = obsidian_plugin.configure_plugin(
        vault,
        config,
        "local-llm-hub",
        ask_yes_no=lambda prompt, default: True,
    )

    assert status == 0
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert "apiKey" not in data["llmConfig"]


def test_cmd_plugin_status_uses_default_vault(tmp_path, monkeypatch, capsys):
    configured_ai_obsidian(tmp_path, monkeypatch)

    status = cli.cmd_plugin(type("Args", (), {"action": "status", "vault": None, "plugin": "local-llm-hub", "yes": False})())

    assert status == 0
    assert "Local LLM Hub" in capsys.readouterr().out


def test_register_obsidian_vault_preserves_existing_registry_and_adds_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(obsidian_plugin.sys, "platform", "darwin")
    registry = tmp_path / "obsidian.json"
    existing = tmp_path / "Existing"
    new_vault = tmp_path / "Main"
    existing.mkdir()
    new_vault.mkdir()
    registry.write_text(
        '{"vaults":{"old":{"path":"' + str(existing) + '","ts":1,"open":true}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(obsidian_plugin, "obsidian_app_config_path", lambda: registry)

    obsidian_plugin.register_obsidian_vault(new_vault)

    payload = json.loads(registry.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in payload["vaults"].values()}
    assert str(existing) in paths
    assert str(new_vault.resolve()) in paths
    assert list(tmp_path.glob("obsidian.json.bak-*"))


def test_open_obsidian_vault_uses_direct_app_open_before_uri_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(obsidian_plugin.sys, "platform", "darwin")
    vault = tmp_path / "Main"
    vault.mkdir()
    commands: list[list[str]] = []
    monkeypatch.setattr(obsidian_plugin, "register_obsidian_vault", lambda path: None)

    class Result:
        returncode = 0

    monkeypatch.setattr(
        obsidian_plugin.subprocess,
        "run",
        lambda command, check=False: commands.append(command) or Result(),
    )

    assert obsidian_plugin.open_obsidian_vault(vault) == 0
    assert commands == [["open", "-a", "Obsidian", str(vault.resolve())]]
