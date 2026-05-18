from __future__ import annotations

from argparse import Namespace

from ai_obsidian import cli
from ai_obsidian.model_catalog import ModelChoice


def test_models_list_filters_out_large_models_on_16gb(monkeypatch, capsys):
    choices = [
        ModelChoice("mlx-community/Qwen3-1.7B-4bit", "small", 16, "qwen", "ok"),
        ModelChoice("mlx-community/Qwen3.6-35B-A3B-4bit", "large", 64, "qwen", "too big"),
    ]
    monkeypatch.setattr(cli, "system_memory_gb", lambda: 16)
    monkeypatch.setattr(cli, "load_model_choices", lambda load_remote_models: (choices, "test"))

    status = cli.cmd_models(Namespace(action="list", model=None))

    output = capsys.readouterr().out
    assert status == 0
    assert "Qwen3-1.7B" in output
    assert "Qwen3.6-35B" not in output
