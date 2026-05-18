from __future__ import annotations

from argparse import Namespace

from ai_obsidian import cli


class FakeClient:
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key

    def list_models(self) -> list[str]:
        return []


def write_config(tmp_path, monkeypatch, model_dir):
    home = tmp_path / "home"
    vault = tmp_path / "Main"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    cli.save_config(
        {
            "omlx": {
                "mode": "service",
                "base_url": "http://localhost:8000/v1",
                "api_key": "",
                "model_dir": str(model_dir),
                "selected_model": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
            },
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "chat": {"default_engine": "builtin"},
        }
    )


def test_stack_start_skips_download_when_model_exists_locally(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    model = model_dir / "mlx-community" / "Qwen3.5-2B-OptiQ-4bit"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model-00001-of-00001.safetensors").write_bytes(b"abc")
    write_config(tmp_path, monkeypatch, model_dir)

    downloads: list[str] = []
    monkeypatch.setattr(cli, "OmlxClient", FakeClient)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (None, None))
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: downloads.append(repo) or 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: 0)
    monkeypatch.setattr(cli, "listener_for_base_url", lambda base_url: None)
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: 0)
    monkeypatch.setattr(
        cli,
        "wait_for_omlx_models",
        lambda client, selected_model=None: ["mlx-community/Qwen3.5-2B-OptiQ-4bit"],
    )

    status = cli.cmd_stack_start(Namespace(vault="Main", interactive=False))

    assert status == 0
    assert downloads == []


def test_stack_start_downloads_missing_model(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)

    downloads: list[str] = []
    monkeypatch.setattr(cli, "OmlxClient", FakeClient)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (None, None))
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: downloads.append(repo) or 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: 0)
    monkeypatch.setattr(cli, "listener_for_base_url", lambda base_url: None)
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: 0)
    monkeypatch.setattr(
        cli,
        "wait_for_omlx_models",
        lambda client, selected_model=None: ["mlx-community/Qwen3.5-2B-OptiQ-4bit"],
    )

    status = cli.cmd_stack_start(Namespace(vault="Main", interactive=False))

    assert status == 0
    assert downloads == ["mlx-community/Qwen3.5-2B-OptiQ-4bit"]


def test_models_download_uses_configured_model_dir(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: calls.append((repo, dest)) or 0)

    status = cli.cmd_models(Namespace(action="download", model="mlx-community/Qwen3.5-2B-OptiQ-4bit"))

    assert status == 0
    assert calls == [("mlx-community/Qwen3.5-2B-OptiQ-4bit", model_dir)]


def test_stack_start_uses_already_reachable_omlx_without_starting_service(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)
    services: list[object] = []
    downloads: list[str] = []
    monkeypatch.setattr(cli, "OmlxClient", FakeClient)
    monkeypatch.setattr(
        cli,
        "list_models_if_reachable",
        lambda client: (["mlx-community/Qwen3.5-2B-OptiQ-4bit"], None),
    )
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: downloads.append(repo) or 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: services.append(args) or 0)
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: 0)

    status = cli.cmd_stack_start(Namespace(vault="Main", interactive=False))

    assert status == 0
    assert services == []
    assert downloads == []


def test_stack_start_reports_port_listener_instead_of_bootstrap_noise(tmp_path, monkeypatch, capsys):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)
    monkeypatch.setattr(cli, "OmlxClient", FakeClient)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (None, None))
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: 0)
    monkeypatch.setattr(cli, "listener_for_base_url", lambda base_url: "python3 57264 tonyr TCP 127.0.0.1:8000")

    status = cli.cmd_stack_start(Namespace(vault="Main", interactive=False))

    assert status == 1
    output = capsys.readouterr().out
    assert "already in use" in output
    assert "python3 57264" in output


def test_stack_start_syncs_obsidian_plugin_with_active_model(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)
    synced: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "OmlxClient", FakeClient)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (["Qwen3.5-2B-OptiQ-4bit"], None))
    monkeypatch.setattr(cli, "download_model_repo", lambda repo, dest: 0)
    monkeypatch.setattr(
        cli,
        "sync_obsidian_plugins_after_stack_ready",
        lambda config, vault: synced.append((config["omlx"]["selected_model"], vault)) or 0,
    )

    status = cli.cmd_stack_start(Namespace(vault="Main", interactive=False))

    assert status == 0
    assert synced == [("Qwen3.5-2B-OptiQ-4bit", "Main")]
    assert cli.load_config()["omlx"]["selected_model"] == "Qwen3.5-2B-OptiQ-4bit"


def test_stack_status_prints_entrypoints_when_ready(tmp_path, monkeypatch, capsys):
    model_dir = tmp_path / "models"
    vault = tmp_path / "Main"
    vault.mkdir()
    write_config(tmp_path, monkeypatch, model_dir)
    config = cli.load_config()
    config["vaults"]["Main"]["path"] = str(vault)
    cli.save_config(config)
    monkeypatch.setattr(cli, "cmd_doctor", lambda args: 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: 0)
    monkeypatch.setattr(cli, "cmd_models_status", lambda: 0)
    monkeypatch.setattr(cli, "cmd_repair", lambda args: 0)

    status = cli.cmd_stack_status(Namespace(vault=None))

    assert status == 0
    output = capsys.readouterr().out
    assert "oMLX browser chat: http://localhost:8000/admin/chat" in output
    assert "./ai-obsidian chat --vault Main" in output


def test_stack_status_fails_when_safe_repair_fails(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)
    monkeypatch.setattr(cli, "cmd_doctor", lambda args: 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: 0)
    monkeypatch.setattr(cli, "cmd_models_status", lambda: 0)
    monkeypatch.setattr(cli, "cmd_repair", lambda args: 1)

    status = cli.cmd_stack_status(Namespace(vault=None))

    assert status == 1


def test_service_start_prints_ready_links_when_model_is_served(tmp_path, monkeypatch, capsys):
    model_dir = tmp_path / "models"
    write_config(tmp_path, monkeypatch, model_dir)

    class Result:
        returncode = 0

    monkeypatch.setattr(cli.shutil, "which", lambda name: "brew")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, check=False: Result())
    monkeypatch.setattr(
        cli,
        "list_models_if_reachable",
        lambda client: (["mlx-community/Qwen3.5-2B-OptiQ-4bit"], None),
    )
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: 0)

    status = cli.cmd_service(Namespace(action="start"))

    assert status == 0
    output = capsys.readouterr().out
    assert "You are ready." in output
    assert "oMLX browser chat: http://localhost:8000/admin/chat" in output
