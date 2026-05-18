from __future__ import annotations

import builtins
import json

from ai_obsidian import cli
from ai_obsidian import installer


def test_init_preserves_existing_vault_content(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer, "DEFAULT_MODEL_DIR", home / ".omlx" / "models")
    monkeypatch.setattr(installer, "DEFAULT_VAULTS_ROOT", home / "Documents" / "Obsidian")
    monkeypatch.setattr(installer, "is_omlx_installed", lambda: False)
    monkeypatch.setattr(installer, "ensure_prerequisites", lambda **kwargs: 0)
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 16)

    vault_root = tmp_path / "vaults"
    main_vault = vault_root / "Main"
    main_vault.mkdir(parents=True)
    note = main_vault / "Meeting.md"
    note.write_text("# Meeting\n\nDo not delete me.\n", encoding="utf-8")

    answers = iter(
        [
            "",  # model dir default
            "",  # oMLX service default
            "3",  # no API key for deterministic test
            str(vault_root),
            "Main",
            "y",  # register existing default vault
            "y",  # create soul.md for existing vault
            "",  # builtin chat default
            "",  # qwen family
            "",  # qwen 3 version
            "",  # small size
            "",  # default matching model
            "y",  # save
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    status, config = installer.run_init(load_remote_models=False)
    assert status == 0
    assert config is not None
    assert note.read_text(encoding="utf-8") == "# Meeting\n\nDo not delete me.\n"
    assert (main_vault / ".obsidian").is_dir()
    assert (main_vault / "soul.md").is_file()


def test_cmd_init_merges_with_existing_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    existing_vault = tmp_path / "Existing"
    existing_vault.mkdir()
    cli.save_config({"vaults": {"existing": {"name": "existing", "path": str(existing_vault)}}})

    returned = {
        "vaults": {
            "Main": {
                "name": "Main",
                "path": str(tmp_path / "Main"),
            }
        },
        "omlx": {
            "mode": "service",
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "model_dir": str(home / ".omlx" / "models"),
            "selected_model": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
        },
        "chat": {"default_engine": "builtin"},
    }
    monkeypatch.setattr(cli, "run_init", lambda load_remote_models: (0, returned))
    monkeypatch.setattr(cli, "ask_yes_no", lambda prompt, default: False)

    status = cli.cmd_init(type("Args", (), {"offline": True})())

    assert status == 0
    saved = json.loads(cli.config_path().read_text(encoding="utf-8"))
    assert saved["vaults"]["existing"]["path"] == str(existing_vault)
    assert saved["vaults"]["Main"]["name"] == "Main"
    assert saved["omlx"]["mode"] == "service"


def test_cmd_init_launches_stack_after_save_when_requested(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    returned = {
        "vaults": {"Main": {"name": "Main", "path": str(tmp_path / "Main")}},
        "omlx": {
            "mode": "service",
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "model_dir": str(home / ".omlx" / "models"),
            "selected_model": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
        },
        "default_vault": "Main",
        "chat": {"default_engine": "builtin"},
    }
    launched: list[str | None] = []
    monkeypatch.setattr(cli, "run_init", lambda load_remote_models: (0, returned))
    monkeypatch.setattr(cli, "ask_yes_no", lambda prompt, default: True)
    monkeypatch.setattr(cli, "cmd_stack_start", lambda args: launched.append(args.vault) or 0)

    status = cli.cmd_init(type("Args", (), {"offline": True})())

    assert status == 0
    assert launched == ["Main"]


def test_existing_omlx_prompts_for_current_key(monkeypatch):
    answers = iter(["", "current-secret"])
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    assert installer.ask_api_key(omlx_installed=True) == "current-secret"


def test_new_omlx_default_generates_key(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _: "")
    monkeypatch.setattr(installer.secrets, "token_urlsafe", lambda _: "generated-secret")

    assert installer.ask_api_key(omlx_installed=False) == "generated-secret"


def test_recommended_size_defaults_to_balanced_on_32gb_plus(monkeypatch):
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 32)

    assert installer.recommended_size_default_index() == 1


def test_allowed_size_buckets_hide_large_on_32gb():
    assert installer.allowed_size_buckets_for_memory(16) == ["small"]
    assert installer.allowed_size_buckets_for_memory(32) == ["small", "balanced"]
    assert installer.allowed_size_buckets_for_memory(64) == ["small", "balanced", "large"]


def test_model_memory_warning_for_too_large_model(monkeypatch, capsys):
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 32)
    model = installer.ModelChoice("mlx-community/Qwen3.6-35B-A3B-4bit", "Qwen", 64, "qwen", "")

    installer.warn_if_model_exceeds_memory(model)

    assert "Warning" in capsys.readouterr().out


def test_model_dir_candidates_include_popular_provider_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer, "DEFAULT_MODEL_DIR", home / ".omlx" / "models")
    (home / ".ollama" / "models" / ".omlx" / "models").mkdir(parents=True)
    (home / ".lmstudio" / "models").mkdir(parents=True)
    (home / "Library" / "Application Support" / "LM Studio" / "models").mkdir(parents=True)

    candidates = installer.discover_model_dir_candidates()
    paths = {str(candidate.path) for candidate in candidates if candidate.path}

    assert str(home / ".omlx" / "models") in paths
    assert str(home / ".ollama" / "models") in paths
    assert str(home / ".ollama" / "models" / ".omlx" / "models") in paths
    assert str(home / ".lmstudio" / "models") in paths
    assert str(home / "Library" / "Application Support" / "LM Studio" / "models") in paths


def test_model_dir_candidates_prefer_current_lmstudio_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer, "DEFAULT_MODEL_DIR", home / ".omlx" / "models")
    lmstudio_model = home / ".lmstudio" / "models" / "unsloth" / "Qwen3.6-27B-UD-MLX-6bit"
    lmstudio_model.mkdir(parents=True)
    (lmstudio_model / "config.json").write_text("{}", encoding="utf-8")
    (lmstudio_model / "model-00001-of-00001.safetensors").write_bytes(b"abc")

    candidates = installer.discover_model_dir_candidates()
    selected = candidates[installer.recommended_model_dir_index(candidates)]

    assert selected.id == "lmstudio"


def test_recommended_model_dir_prefers_existing_mlx_models(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer, "DEFAULT_MODEL_DIR", home / ".omlx" / "models")
    app_model = home / ".ollama" / "models" / ".omlx" / "models" / "mlx-community" / "Qwen3.6-27B-4bit"
    app_model.mkdir(parents=True)
    (app_model / "config.json").write_text("{}", encoding="utf-8")
    (app_model / "model-00001-of-00001.safetensors").write_bytes(b"abc")

    candidates = installer.discover_model_dir_candidates()
    selected = candidates[installer.recommended_model_dir_index(candidates)]

    assert selected.id == "omlx_app"


def test_ask_model_dir_supports_custom_path(tmp_path, monkeypatch):
    custom = tmp_path / "custom-models"
    monkeypatch.setattr(
        installer,
        "discover_model_dir_candidates",
        lambda: [
            installer.ModelDirCandidate("omlx", "oMLX default", tmp_path / "models", "default"),
            installer.ModelDirCandidate("custom", "Custom path", None, "enter another directory manually"),
        ],
    )
    answers = iter(["2", str(custom)])
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    assert installer.ask_model_dir() == custom.resolve()
