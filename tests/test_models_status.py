from __future__ import annotations

from ai_obsidian import cli


def test_discover_local_models_finds_two_level_hf_layout(tmp_path):
    model_dir = tmp_path / "models"
    model = model_dir / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model-00001-of-00002.safetensors").write_bytes(b"abc")
    (model / "model-00002-of-00002.safetensors").write_bytes(b"defg")

    models = cli.discover_local_models(model_dir)

    assert len(models) == 1
    assert models[0].id == "mlx-community/Qwen3.6-35B-A3B-4bit"
    assert models[0].size_bytes == 7 + 2
    assert models[0].safetensor_count == 2


def test_model_matches_short_and_org_ids():
    assert cli.model_matches("Qwen3.6-35B-A3B-4bit", "mlx-community/Qwen3.6-35B-A3B-4bit")
    assert cli.model_matches("mlx-community/Qwen3.6-35B-A3B-4bit", "Qwen3.6-35B-A3B-4bit")
    assert not cli.model_matches("gemma-4-e4b-it-OptiQ-4bit", "Qwen3.6-35B-A3B-4bit")


def test_format_bytes():
    assert cli.format_bytes(512) == "512 B"
    assert cli.format_bytes(1024 * 1024) == "1.0 MB"


def test_has_model_files_requires_config_and_weights(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model-00001-of-00001.safetensors").write_bytes(b"abc")
    assert not cli.has_model_files(model)

    (model / "config.json").write_text("{}", encoding="utf-8")
    assert cli.has_model_files(model)


def test_models_status_warns_when_local_model_is_not_served(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    model_dir = tmp_path / "models"
    model = model_dir / "mlx-community" / "Qwen3.6-27B-4bit"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model-00001-of-00001.safetensors").write_bytes(b"abc")
    cli.save_config(
        {
            "omlx": {
                "base_url": "http://localhost:8000/v1",
                "model_dir": str(model_dir),
                "selected_model": "mlx-community/Qwen3.6-27B-4bit",
            }
        }
    )

    class FakeClient:
        def __init__(self, base_url: str, api_key: str | None = None):
            pass

        def list_models(self) -> list[str]:
            return ["Qwen3.6-35B-A3B-4bit"]

    monkeypatch.setattr(cli, "OmlxClient", FakeClient)

    assert cli.cmd_models_status() == 1
    assert "active oMLX server does not expose it" in capsys.readouterr().out


def test_models_dirs_lists_provider_candidates(monkeypatch, capsys):
    class Candidate:
        pass

    candidates = [Candidate()]
    monkeypatch.setattr(cli, "discover_model_dir_candidates", lambda: candidates)
    monkeypatch.setattr(cli, "model_dir_label", lambda candidate: "oMLX default: /tmp/models")

    assert cli.cmd_models_dirs() == 0
    assert "oMLX default: /tmp/models" in capsys.readouterr().out


def test_discover_downloaded_models_lists_mlx_gguf_and_ollama(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "config_path", lambda: home / ".ai-obsidian" / "config.json")

    model_root = tmp_path / "models"
    mlx = model_root / "mlx-community" / "Qwen3.6-27B-4bit"
    mlx.mkdir(parents=True)
    (mlx / "config.json").write_text("{}", encoding="utf-8")
    (mlx / "model-00001-of-00001.safetensors").write_bytes(b"abc")
    gguf = model_root / "llama.gguf"
    gguf.write_bytes(b"12345")
    manifest = model_root / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "8b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"layers":[{"mediaType":"application/vnd.ollama.image.model","size":7}]}',
        encoding="utf-8",
    )
    cli.save_config({"omlx": {"model_dir": str(model_root)}})
    monkeypatch.setattr(cli, "discover_model_dir_candidates", lambda: [])

    models = cli.discover_downloaded_models()

    assert {model.id for model in models} == {
        "mlx-community/Qwen3.6-27B-4bit",
        "llama",
        "qwen3:8b",
    }
    assert {model.format for model in models} == {"MLX", "GGUF", "Ollama manifest"}


def test_models_local_prints_downloaded_models(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "discover_downloaded_models",
        lambda: [
            cli.DownloadedModel(
                id="Qwen3.6-27B-4bit",
                source="oMLX default",
                format="MLX",
                path=cli.Path("/tmp/model"),
                size_bytes=1024,
                note="1 safetensors",
            )
        ],
    )

    assert cli.cmd_models_local() == 0
    output = capsys.readouterr().out
    assert "Qwen3.6-27B-4bit" in output
    assert "1.0 KB" in output
