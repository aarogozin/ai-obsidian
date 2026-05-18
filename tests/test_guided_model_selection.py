from __future__ import annotations

import builtins

from ai_obsidian import installer
from ai_obsidian.model_catalog import ModelChoice


def hide_downloaded_models(monkeypatch):
    monkeypatch.setattr(installer, "discover_downloaded_mlx_models", lambda current_model_dir=None: [])


def test_choose_model_guides_by_family_version_size(monkeypatch):
    hide_downloaded_models(monkeypatch)
    choices = [
        ModelChoice("mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit", "Qwen 3.6 35B", 64, "qwen", "new"),
        ModelChoice("mlx-community/gemma-4-e4b-it-OptiQ-4bit", "Gemma 4 E4B", 16, "gemma", "new"),
    ]
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 64)
    monkeypatch.setattr(installer, "load_model_choices", lambda load_remote_models, searches=None: (choices, "test"))
    answers = iter(
        [
            "2",  # gemma
            "",  # version 4
            "",  # small
            "",  # first matching repo
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "mlx-community/gemma-4-e4b-it-OptiQ-4bit"


def test_choose_model_fetches_after_size_and_family(monkeypatch):
    hide_downloaded_models(monkeypatch)
    calls: list[str] = []
    choices = [
        ModelChoice("mlx-community/Qwen3-1.7B-4bit", "Qwen 3", 16, "qwen", "ok"),
    ]

    def fake_load(load_remote_models, searches=None):
        calls.append("fetch")
        assert searches == installer.MODEL_SEARCHES_BY_FAMILY["qwen"]
        return choices, "test"

    monkeypatch.setattr(installer, "system_memory_gb", lambda: 16)
    monkeypatch.setattr(installer, "load_model_choices", fake_load)

    answers = iter(["", "", "", ""])
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _: calls.append("input") or next(answers),
    )

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "mlx-community/Qwen3-1.7B-4bit"
    assert calls[:2] == ["input", "fetch"]
    assert "fetch" in calls


def test_choose_model_shows_newer_versions_before_size_filter(monkeypatch):
    hide_downloaded_models(monkeypatch)
    choices = [
        ModelChoice("mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit", "Qwen 3.6 35B", 64, "qwen", "new"),
        ModelChoice("mlx-community/Qwen3.5-9B-OptiQ-4bit", "Qwen 3.5 9B", 32, "qwen", "balanced"),
    ]
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 64)
    monkeypatch.setattr(installer, "load_model_choices", lambda load_remote_models, searches=None: (choices, "test"))
    answers = iter(
        [
            "",  # qwen
            "1",  # explicitly choose visible Qwen 3.6
            "",  # large is the only size for Qwen 3.6
            "",  # first matching repo
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"


def test_choose_model_defaults_to_balanced_version_when_large_newer_exists(monkeypatch):
    hide_downloaded_models(monkeypatch)
    choices = [
        ModelChoice("mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit", "Qwen 3.6 35B", 64, "qwen", "new"),
        ModelChoice("mlx-community/Qwen3.5-9B-OptiQ-4bit", "Qwen 3.5 9B", 32, "qwen", "balanced"),
    ]
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 64)
    monkeypatch.setattr(installer, "load_model_choices", lambda load_remote_models, searches=None: (choices, "test"))
    answers = iter(
        [
            "",  # qwen
            "",  # default should be Qwen 3.5 because it has balanced option
            "",  # balanced
            "",  # first matching repo
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "mlx-community/Qwen3.5-9B-OptiQ-4bit"


def test_choose_model_offers_downloaded_models_before_remote_fetch(monkeypatch, tmp_path):
    calls: list[str] = []
    local = installer.DownloadedMlxModel(
        id="unsloth/Qwen3.6-27B-UD-MLX-6bit",
        source="LM Studio",
        model_dir=tmp_path / "models",
        path=tmp_path / "models" / "unsloth" / "Qwen3.6-27B-UD-MLX-6bit",
        size_bytes=28 * 1024 * 1024 * 1024,
        safetensor_count=6,
    )
    monkeypatch.setattr(installer, "discover_downloaded_mlx_models", lambda current_model_dir=None: [local])
    monkeypatch.setattr(installer, "load_model_choices", lambda *args, **kwargs: calls.append("fetch") or ([], "test"))
    monkeypatch.setattr(builtins, "input", lambda _: "")

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "unsloth/Qwen3.6-27B-UD-MLX-6bit"
    assert selected.model_dir == tmp_path / "models"
    assert selected.downloaded is True
    assert calls == []


def test_choose_model_can_skip_downloaded_models(monkeypatch, tmp_path):
    local = installer.DownloadedMlxModel(
        id="unsloth/Qwen3.6-27B-UD-MLX-6bit",
        source="LM Studio",
        model_dir=tmp_path / "models",
        path=tmp_path / "models" / "unsloth" / "Qwen3.6-27B-UD-MLX-6bit",
        size_bytes=28 * 1024 * 1024 * 1024,
        safetensor_count=6,
    )
    choices = [ModelChoice("mlx-community/Qwen3-1.7B-4bit", "Qwen 3", 16, "qwen", "ok")]
    monkeypatch.setattr(installer, "discover_downloaded_mlx_models", lambda current_model_dir=None: [local])
    monkeypatch.setattr(installer, "system_memory_gb", lambda: 16)
    monkeypatch.setattr(installer, "load_model_choices", lambda load_remote_models, searches=None: (choices, "test"))
    answers = iter(["2", "", "", "", ""])
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    selected = installer.choose_model(load_remote_models=True)

    assert selected.repo_id == "mlx-community/Qwen3-1.7B-4bit"
    assert selected.downloaded is False
