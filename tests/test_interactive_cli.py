from __future__ import annotations

import builtins

from ai_obsidian import cli


def write_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = tmp_path / "Main"
    vault.mkdir()
    cli.save_config(
        {
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "default_vault": "Main",
            "omlx": {
                "base_url": "http://localhost:8000/v1",
                "model_dir": str(tmp_path / "models"),
                "selected_model": "old-model",
            },
            "chat": {"default_engine": "builtin"},
        }
    )
    return vault


def test_root_without_args_opens_interactive_menu(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "ask_choice", lambda prompt, options, default=0: "doctor")
    monkeypatch.setattr(cli, "cmd_doctor", lambda args: calls.append("doctor") or 0)

    assert cli.main([]) == 0
    assert calls == ["doctor"]


def test_models_use_without_id_picks_downloaded_model_first(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch)
    model = cli.DownloadedModel(
        id="mlx-community/Qwen3.6-27B-4bit",
        source="LM Studio",
        format="MLX",
        path=tmp_path / "models" / "mlx-community" / "Qwen3.6-27B-4bit",
        size_bytes=1024,
    )
    monkeypatch.setattr(cli, "discover_downloaded_models", lambda: [model])
    monkeypatch.setattr(builtins, "input", lambda _: "")

    status = cli.cmd_models(type("Args", (), {"action": "use", "model": None})())

    assert status == 0
    assert cli.load_config()["omlx"]["selected_model"] == "mlx-community/Qwen3.6-27B-4bit"


def test_chat_without_vault_uses_default_vault(tmp_path, monkeypatch):
    vault = write_config(tmp_path, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(cli, "OmlxClient", lambda base_url, api_key=None: object())
    monkeypatch.setattr(
        cli,
        "run_builtin_chat",
        lambda vault_path, client, model, max_files, once=None: calls.append(vault_path) or 0,
    )

    status = cli.cmd_chat(
        type(
            "Args",
            (),
            {
                "vault": None,
                "engine": None,
                "model": None,
                "base_url": None,
                "api_key": None,
                "max_files": 30,
                "once": "hello",
            },
        )()
    )

    assert status == 0
    assert calls == [vault]


def test_vault_create_without_path_prompts_for_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    new_vault = tmp_path / "NewVault"
    monkeypatch.setattr(builtins, "input", lambda _: str(new_vault))

    status = cli.cmd_vault_create(type("Args", (), {"path": None, "name": None})())

    assert status == 0
    assert (new_vault / ".obsidian").is_dir()
    assert cli.load_config()["vaults"]["NewVault"]["path"] == str(new_vault.resolve())
