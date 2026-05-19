from __future__ import annotations

from http.client import IncompleteRead

from ai_obsidian import model_catalog
from ai_obsidian.model_catalog import ModelChoice


def test_online_model_choices_use_remote_when_available(monkeypatch):
    remote = [ModelChoice("mlx-community/QwenFresh-4bit", "QwenFresh 4-bit", 16, "qwen", "remote")]
    monkeypatch.setattr(model_catalog, "fetch_huggingface_models", lambda searches=None: remote)

    choices, source = model_catalog.load_model_choices(load_remote_models=True)

    assert choices == remote
    assert "Hugging Face" in source


def test_model_choices_fall_back_when_remote_empty(monkeypatch):
    monkeypatch.setattr(model_catalog, "fetch_huggingface_models", lambda searches=None: [])

    choices, source = model_catalog.load_model_choices(load_remote_models=True)

    assert choices
    assert source == "offline fallback list"


def test_load_model_choices_forwards_targeted_searches(monkeypatch):
    seen = []
    monkeypatch.setattr(model_catalog, "fetch_huggingface_models", lambda searches=None: seen.append(searches) or [])

    model_catalog.load_model_choices(load_remote_models=True, searches=("gemma-4",))

    assert seen == [("gemma-4",)]


def test_model_choices_fall_back_when_remote_response_is_incomplete(monkeypatch):
    def broken_fetch(_query):
        raise IncompleteRead(b"partial", 10)

    monkeypatch.setattr(model_catalog, "fetch_huggingface_query", broken_fetch)

    choices, source = model_catalog.load_model_choices(load_remote_models=True)

    assert choices
    assert source == "offline fallback list"


def test_model_fetch_filter_rejects_non_chat_model_names():
    assert model_catalog.guess_ram("mlx-community/qwen3-8b-4bit") == 32
    assert model_catalog.guess_ram("mlx-community/qwen3-1.7b-4bit") == 16
    assert model_catalog.guess_ram("mlx-community/qwen3-30b-a3b-4bit") == 64


def test_fetch_combines_recent_popular_and_targeted_queries(monkeypatch):
    queries: list[dict[str, str]] = []

    def fake_fetch(query):
        queries.append(query)
        if query.get("search") == "Qwen3.6":
            return [
                {
                    "id": "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
                    "lastModified": "2026-05-14T07:51:16.000Z",
                    "downloads": 2110,
                }
            ]
        if query.get("search") == "gemma-4":
            return [
                {
                    "id": "mlx-community/gemma-4-e4b-it-OptiQ-4bit",
                    "lastModified": "2026-05-10T00:03:04.000Z",
                    "downloads": 10752,
                }
            ]
        return []

    monkeypatch.setattr(model_catalog, "fetch_huggingface_query", fake_fetch)

    choices = model_catalog.fetch_huggingface_models()

    repo_ids = [choice.repo_id for choice in choices]
    assert "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit" in repo_ids
    assert "mlx-community/gemma-4-e4b-it-OptiQ-4bit" in repo_ids
    assert any(query.get("sort") == "lastModified" for query in queries)
    assert any(query.get("sort") == "downloads" for query in queries)
    assert any(query.get("search") == "Qwen3.6" for query in queries)
    assert any(query.get("search") == "gemma-4" for query in queries)


def test_model_metadata_parses_family_version_and_size():
    qwen = ModelChoice(
        "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
        "Qwen3.6 35B",
        64,
        "qwen",
        "",
    )
    gemma = ModelChoice(
        "mlx-community/gemma-4-e4b-it-OptiQ-4bit",
        "Gemma 4 E4B",
        16,
        "gemma",
        "",
    )

    assert model_catalog.model_version(qwen.repo_id) == "3.6"
    assert model_catalog.size_bucket_for_model(qwen) == "large"
    assert model_catalog.model_version(gemma.repo_id) == "4"
    assert model_catalog.size_bucket_for_model(gemma) == "small"


def test_filter_model_choices_by_family_version_and_size():
    choices = [
        ModelChoice("mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit", "large", 64, "qwen", ""),
        ModelChoice("mlx-community/Qwen3.5-9B-OptiQ-4bit", "balanced", 32, "qwen", ""),
        ModelChoice("mlx-community/gemma-4-e4b-it-OptiQ-4bit", "small", 16, "gemma", ""),
    ]

    filtered = model_catalog.filter_model_choices(choices, "gemma", "4", "small")

    assert [choice.repo_id for choice in filtered] == ["mlx-community/gemma-4-e4b-it-OptiQ-4bit"]


def test_apple_silicon_filter_accepts_mlx_safetensors_models():
    item = {
        "id": "mlx-community/gemma-4-e4b-it-OptiQ-4bit",
        "pipeline_tag": "text-generation",
        "library_name": "mlx",
        "tags": ["mlx", "safetensors", "4bit"],
    }

    assert model_catalog.is_apple_silicon_mlx_model(item)


def test_apple_silicon_filter_rejects_non_mlx_quant_formats():
    for repo_id in [
        "someone/Qwen3.6-35B-GGUF",
        "someone/gemma-4-31B-GPTQ",
        "someone/Qwen3.6-35B-AWQ",
        "someone/Llama-3.2-ONNX",
    ]:
        item = {
            "id": repo_id,
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "tags": ["4bit"],
        }

        assert not model_catalog.is_apple_silicon_mlx_model(item)


def test_model_choice_note_marks_apple_silicon_candidate():
    choice = model_catalog.model_choice_from_hf_item(
        {
            "id": "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
            "pipeline_tag": "text-generation",
            "library_name": "mlx",
            "tags": ["mlx", "safetensors", "4bit"],
            "lastModified": "2026-05-14T07:51:16.000Z",
            "downloads": 2110,
        }
    )

    assert choice is not None
    assert "Apple Silicon MLX candidate" in choice.note
