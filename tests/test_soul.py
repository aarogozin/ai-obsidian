from __future__ import annotations

import builtins
import json
from argparse import Namespace

from ai_obsidian import chat, cli, obsidian_plugin
from ai_obsidian.soul import SOUL_FILENAME, create_soul, managed_soul_block, read_soul
from ai_obsidian.vault import collect_notes


class FakeClient:
    def __init__(self):
        self.messages: list[list[dict[str, str]]] = []

    def choose_model(self, model):
        return model or "model-a"

    def chat(self, model, messages):
        self.messages.append(messages)
        return "answer"


def test_collect_notes_excludes_root_soul_file(tmp_path):
    vault = tmp_path / "Main"
    vault.mkdir()
    (vault / SOUL_FILENAME).write_text("# Vault Soul\n", encoding="utf-8")
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")

    notes = collect_notes(vault)

    assert [note.relative_path for note in notes] == ["note.md"]


def test_builtin_chat_includes_soul_as_system_instruction(tmp_path, capsys):
    vault = tmp_path / "Main"
    vault.mkdir()
    (vault / SOUL_FILENAME).write_text("Write in Russian.\n", encoding="utf-8")
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    client = FakeClient()

    status = chat.run_builtin_chat(vault, client, model=None, max_files=30, once="summarize")

    assert status == 0
    assert "Write in Russian." in client.messages[0][0]["content"]
    assert "soul.md" in client.messages[0][0]["content"]
    assert "answer" in capsys.readouterr().out


def test_external_chat_prompt_includes_soul(tmp_path, monkeypatch):
    vault = tmp_path / "Main"
    vault.mkdir()
    (vault / SOUL_FILENAME).write_text("Prefer primary sources.\n", encoding="utf-8")
    prompts: list[str] = []
    monkeypatch.setattr(chat, "ask_external_provider", lambda engine, prompt: prompts.append(prompt) or "ok")

    status = chat.run_external_chat(vault, "hermes", max_files=30, once="research")

    assert status == 0
    assert "Prefer primary sources." in prompts[0]
    assert "Available note context" in prompts[0]


def test_edit_prompt_includes_soul(tmp_path, monkeypatch):
    vault = tmp_path / "Main"
    vault.mkdir()
    (vault / SOUL_FILENAME).write_text("Preserve my voice.\n", encoding="utf-8")
    (vault / "note.md").write_text("hello\n", encoding="utf-8")
    client = FakeClient()
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")

    chat.handle_edit(vault, client, "model-a", "note.md rewrite")

    assert "Preserve my voice." in client.messages[0][0]["content"]


def test_local_llm_hub_syncs_soul_managed_block_and_preserves_user_prompt(tmp_path):
    vault = tmp_path / "Main"
    (vault / ".obsidian" / "plugins" / "local-llm-hub").mkdir(parents=True)
    create_soul(vault)
    soul_text = read_soul(vault)
    data_path = vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json"
    data_path.write_text('{"systemPrompt":"Keep my existing persona."}\n', encoding="utf-8")
    config = {
        "omlx": {
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "selected_model": "model-a",
        }
    }

    status = obsidian_plugin.configure_plugin(vault, config, "local-llm-hub", yes=True)

    assert status == 0
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["systemPrompt"].startswith("Keep my existing persona.")
    assert managed_soul_block(soul_text) in data["systemPrompt"]


def test_local_llm_hub_replaces_only_existing_soul_managed_block(tmp_path):
    vault = tmp_path / "Main"
    (vault / ".obsidian" / "plugins" / "local-llm-hub").mkdir(parents=True)
    (vault / SOUL_FILENAME).write_text("New soul.\n", encoding="utf-8")
    data_path = vault / ".obsidian" / "plugins" / "local-llm-hub" / "data.json"
    data_path.write_text(
        json.dumps({"systemPrompt": f"User text.\n\n{managed_soul_block('Old soul.')}"}),
        encoding="utf-8",
    )
    config = {
        "omlx": {
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "selected_model": "model-a",
        }
    }

    obsidian_plugin.configure_plugin(vault, config, "local-llm-hub", yes=True)

    prompt = json.loads(data_path.read_text(encoding="utf-8"))["systemPrompt"]
    assert "User text." in prompt
    assert "New soul." in prompt
    assert "Old soul." not in prompt


def test_doctor_json_includes_soul_status(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault = tmp_path / "Main"
    vault.mkdir()
    create_soul(vault)
    monkeypatch.setenv("HOME", str(home))
    cli.save_config(
        {
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "omlx": {"base_url": "http://localhost:8000/v1", "selected_model": "model-a"},
        }
    )
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda _client: (["model-a"], None))

    health = cli.collect_health()

    assert health["soul"]["exists"] is True
    assert health["soul"]["readable"] is True


def test_repair_creates_soul_without_modifying_notes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault = tmp_path / "Main"
    note = vault / "note.md"
    (vault / ".obsidian").mkdir(parents=True)
    note.write_text("keep\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    cli.save_config(
        {
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
            "omlx": {"base_url": "http://localhost:8000/v1", "selected_model": "model-a"},
        }
    )
    monkeypatch.setattr(cli, "repair_served_model_id", lambda config: config)
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault_name: 0)

    status = cli.cmd_repair(Namespace(vault=None, yes=True, interactive=False))

    assert status == 0
    assert (vault / SOUL_FILENAME).is_file()
    assert note.read_text(encoding="utf-8") == "keep\n"


def test_soul_cli_init_status_show(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    vault = tmp_path / "Main"
    vault.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cli.save_config({"default_vault": "Main", "vaults": {"Main": {"name": "Main", "path": str(vault)}}})

    assert cli.cmd_soul(Namespace(action="init", vault=None)) == 0
    assert cli.cmd_soul(Namespace(action="status", vault=None)) == 0
    assert cli.cmd_soul(Namespace(action="show", vault=None)) == 0
    assert "Vault Soul" in capsys.readouterr().out
