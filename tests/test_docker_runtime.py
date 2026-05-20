from __future__ import annotations

import json
import subprocess

from ai_obsidian import cli
from ai_obsidian import docker_runtime


def test_docker_status_without_cli_is_actionable(monkeypatch):
    monkeypatch.setattr(docker_runtime.shutil, "which", lambda name: None)

    status = docker_runtime.docker_status()

    assert status.ok is False
    assert status.docker_cli is None
    assert "Docker Desktop" in status.error


def test_parse_docker_model_list_handles_json_lines():
    output = "\n".join(
        [
            json.dumps({"model": "ai/smollm2"}),
            json.dumps({"name": "ai/qwen2.5-coder"}),
        ]
    )

    assert docker_runtime.parse_docker_model_list(output) == ["ai/smollm2", "ai/qwen2.5-coder"]


def test_setup_apply_accepts_docker_model_runner_profile(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = tmp_path / "Main"
    profile = tmp_path / "docker-profile.json"
    profile.write_text(
        json.dumps(
            {
                "runtime": {"mode": "docker-model-runner"},
                "omlx": {"selected_model": "ai/smollm2"},
                "vault": {"mode": "create", "name": "Main", "path": str(vault)},
                "plugins": {"install_hub": False, "install_companion": False},
                "launch": {"start_stack": False, "open_obsidian": False},
            }
        ),
        encoding="utf-8",
    )

    status = cli.cmd_setup_apply(type("Args", (), {"profile": str(profile), "yes": True, "dry_run": True})())

    assert status == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["runtime"]["mode"] == "docker-model-runner"
    assert plan["omlx"]["mode"] == "docker-model-runner"
    assert plan["omlx"]["base_url"] == docker_runtime.DEFAULT_DMR_BASE_URL
    assert plan["omlx"]["api_key"] == ""


def test_setup_apply_docker_mode_does_not_install_native_prerequisites(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = tmp_path / "Main"
    profile = tmp_path / "docker-profile.json"
    profile.write_text(
        json.dumps(
            {
                "runtime": {"mode": "docker-model-runner"},
                "omlx": {"selected_model": "ai/smollm2"},
                "vault": {"mode": "create", "name": "Main", "path": str(vault)},
                "plugins": {"install_hub": False, "install_companion": False},
                "launch": {"start_stack": False, "open_obsidian": False},
            }
        ),
        encoding="utf-8",
    )
    native_calls: list[object] = []
    monkeypatch.setattr(cli, "ensure_prerequisites", lambda **kwargs: native_calls.append(kwargs) or 0)
    monkeypatch.setattr(cli, "ensure_docker_setup_prerequisites", lambda base_url: 0)

    status = cli.cmd_setup_apply(type("Args", (), {"profile": str(profile), "yes": True, "dry_run": False})())

    assert status == 0
    assert native_calls == []
    config = cli.load_config()
    assert config["runtime"]["mode"] == "docker-model-runner"
    assert config["omlx"]["selected_model"] == "ai/smollm2"


def test_stack_start_docker_pulls_missing_model_and_syncs_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = tmp_path / "Main"
    vault.mkdir()
    cli.save_config(
        {
            "runtime": {"mode": "docker-model-runner"},
            "omlx": {
                "mode": "docker-model-runner",
                "base_url": docker_runtime.DEFAULT_DMR_BASE_URL,
                "selected_model": "ai/smollm2",
            },
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
        }
    )
    pulls: list[str] = []
    syncs: list[str] = []
    monkeypatch.setattr(cli, "ensure_docker_model_runner", lambda base_url: 0)
    monkeypatch.setattr(cli, "docker_model_list", lambda: [])
    monkeypatch.setattr(cli, "docker_model_pull", lambda model: pulls.append(model) or 0)
    monkeypatch.setattr(cli, "docker_model_run_detached", lambda model: 0)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (None, RuntimeError("offline")))
    monkeypatch.setattr(cli, "wait_for_omlx_models", lambda client, selected_model=None, service_label="oMLX": ["ai/smollm2"])
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: syncs.append(vault) or 0)

    status = cli.cmd_stack_start(type("Args", (), {"vault": "Main", "interactive": False})())

    assert status == 0
    assert pulls == ["ai/smollm2"]
    assert syncs == ["Main"]


def test_docker_runtime_uses_container_model_runner_url_for_internal_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AI_OBSIDIAN_IN_CONTAINER", "1")
    monkeypatch.setenv("AI_OBSIDIAN_DMR_CONTAINER_BASE_URL", "http://host.docker.internal:12434/engines/v1")
    vault = tmp_path / "Main"
    vault.mkdir()
    cli.save_config(
        {
            "runtime": {"mode": "docker-model-runner"},
            "omlx": {
                "mode": "docker-model-runner",
                "base_url": docker_runtime.DEFAULT_DMR_BASE_URL,
                "selected_model": "ai/smollm2",
            },
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
        }
    )
    client_urls: list[str] = []

    class Client:
        def __init__(self, base_url, api_key=None):
            client_urls.append(base_url)

    monkeypatch.setattr(cli, "OmlxClient", Client)
    monkeypatch.setattr(cli, "ensure_docker_model_runner", lambda base_url: 0)
    monkeypatch.setattr(cli, "docker_model_list", lambda: ["ai/smollm2"])
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (["ai/smollm2"], None))
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault: 0)

    assert cli.cmd_stack_start(type("Args", (), {"vault": "Main", "interactive": False})()) == 0
    assert client_urls == ["http://host.docker.internal:12434/engines/v1"]


def test_cmd_docker_status_handles_daemon_off(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "docker_status",
        lambda base_url: docker_runtime.DockerRuntimeStatus(
            docker_cli="/usr/local/bin/docker",
            desktop_running=False,
            daemon_running=False,
            model_runner_running=False,
            api_reachable=False,
            base_url=base_url,
            models=[],
            error="Docker daemon is not running.",
        ),
    )

    status = cli.cmd_docker(type("Args", (), {"action": "status"})())

    assert status == 1
    assert "Docker daemon: not reachable" in capsys.readouterr().out


def test_docker_model_pull_streams_native_command(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(docker_runtime.shutil, "which", lambda name: "/usr/local/bin/docker")
    monkeypatch.setattr(
        docker_runtime.subprocess,
        "run",
        lambda command, check=False: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )

    assert docker_runtime.docker_model_pull("ai/smollm2") == 0
    assert calls == [["/usr/local/bin/docker", "model", "pull", "ai/smollm2"]]


def test_parse_docker_model_list_prefers_tags_over_digest():
    output = json.dumps(
        [
            {
                "id": "sha256:abc",
                "tags": ["docker.io/ai/smollm2:latest"],
            }
        ]
    )

    assert docker_runtime.parse_docker_model_list(output) == ["docker.io/ai/smollm2:latest"]


def test_parse_docker_model_status_backends():
    output = """Docker Model Runner is running

BACKEND    STATUS         DETAILS
llama.cpp  Running        llama.cpp latest-metal
mlx        Not Installed  package not installed
vllm       Not Installed
"""

    backends = docker_runtime.parse_docker_model_status(output)

    assert backends["llama.cpp"]["status"] == "Running"
    assert backends["mlx"]["status"] == "Not Installed"
    assert backends["vllm"]["status"] == "Not Installed"


def test_setup_models_docker_runtime_does_not_fetch_huggingface(monkeypatch):
    monkeypatch.setattr(cli, "docker_model_list", lambda: ["docker.io/ai/smollm2:latest"])

    payload = cli.collect_setup_models(
        load_remote_models=True,
        family=None,
        version=None,
        size=None,
        model_dir=None,
        runtime=docker_runtime.RUNTIME_DOCKER_MODEL_RUNNER,
    )

    assert payload["runtime"] == docker_runtime.RUNTIME_DOCKER_MODEL_RUNNER
    assert payload["downloaded"] == []
    assert payload["docker_models"][0]["id"] == "docker.io/ai/smollm2:latest"
    assert payload["remote"][0]["repo_id"] == "ai/smollm2"


def test_docker_start_refuses_native_mlx_model_without_pull(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cli.save_config(
        {
            "runtime": {"mode": "docker-model-runner"},
            "omlx": {
                "mode": "docker-model-runner",
                "base_url": docker_runtime.DEFAULT_DMR_BASE_URL,
                "selected_model": "mlx-community/Qwen3.6-27B-4bit",
            },
        }
    )
    pulls: list[str] = []
    monkeypatch.setattr(cli, "ensure_docker_model_runner", lambda base_url: 0)
    monkeypatch.setattr(cli, "docker_model_pull", lambda model: pulls.append(model) or 0)

    status = cli.cmd_docker(type("Args", (), {"action": "start"})())

    assert status == 1
    assert pulls == []
    assert "not a Docker Model Runner model id" in capsys.readouterr().out


def test_model_matches_docker_latest_tag():
    assert cli.model_matches("ai/smollm2", "docker.io/ai/smollm2:latest")


def test_docker_setup_prerequisites_can_skip_host_obsidian_check(monkeypatch):
    monkeypatch.setenv("AI_OBSIDIAN_SKIP_OBSIDIAN_APP_CHECK", "1")
    monkeypatch.setattr(
        cli,
        "docker_status",
        lambda base_url: docker_runtime.DockerRuntimeStatus(
            docker_cli="/usr/local/bin/docker",
            desktop_running=True,
            daemon_running=True,
            model_runner_running=True,
            api_reachable=True,
            base_url=base_url,
            models=[],
        ),
    )
    monkeypatch.setattr(cli, "obsidian_app_available", lambda: False)

    assert cli.ensure_docker_setup_prerequisites(docker_runtime.DEFAULT_DMR_BASE_URL) == 0
