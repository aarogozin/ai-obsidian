from __future__ import annotations

from argparse import Namespace

from ai_obsidian import cli


def test_repair_served_model_id_saves_served_model(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    config = {
        "omlx": {
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "selected_model": "org/model-a",
        }
    }
    cli.save_config(config)
    monkeypatch.setattr(cli, "list_models_if_reachable", lambda client: (["model-a"], None))

    repaired = cli.repair_served_model_id(cli.load_config())

    assert repaired["omlx"]["selected_model"] == "model-a"
    assert cli.load_config()["omlx"]["selected_model"] == "model-a"


def test_repair_does_not_modify_vault_notes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault = tmp_path / "Main"
    note = vault / "note.md"
    (vault / ".obsidian").mkdir(parents=True)
    note.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    cli.save_config(
        {
            "omlx": {"base_url": "http://localhost:8000/v1", "selected_model": "model-a"},
            "default_vault": "Main",
            "vaults": {"Main": {"name": "Main", "path": str(vault)}},
        }
    )
    monkeypatch.setattr(cli, "repair_served_model_id", lambda config: config)
    monkeypatch.setattr(cli, "sync_obsidian_plugins_after_stack_ready", lambda config, vault_name: 0)

    status = cli.cmd_repair(Namespace(vault=None))

    assert status == 0
    assert note.read_text(encoding="utf-8") == "keep me\n"


def test_doctor_json_reports_machine_readable_health(monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_health", lambda: {"ok": True, "plugins": {}})

    status = cli.cmd_doctor(Namespace(json=True))

    assert status == 0
    assert '"ok": true' in capsys.readouterr().out
