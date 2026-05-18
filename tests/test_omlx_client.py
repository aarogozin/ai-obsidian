from __future__ import annotations

from ai_obsidian.omlx import resolve_model_id


def test_resolve_model_id_matches_repo_tail_to_served_id():
    available = ["Qwen3.6-35B-A3B-4bit"]

    assert resolve_model_id("mlx-community/Qwen3.6-35B-A3B-4bit", available) == "Qwen3.6-35B-A3B-4bit"


def test_resolve_model_id_returns_none_for_mismatch():
    assert resolve_model_id("mlx-community/gemma-4-e4b-it-OptiQ-4bit", ["Qwen3.6-35B-A3B-4bit"]) is None
