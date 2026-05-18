from __future__ import annotations

import json
from argparse import Namespace

from ai_obsidian import cli
from ai_obsidian.vault import VaultError, read_note


def test_vault_create_preserves_existing_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault_path = tmp_path / "ExistingVault"
    vault_path.mkdir()
    note = vault_path / "Meeting.md"
    note.write_text("# Meeting\n\nOriginal notes stay here.\n", encoding="utf-8")

    status = cli.cmd_vault_create(Namespace(path=str(vault_path), name="work"))

    assert status == 0
    assert note.read_text(encoding="utf-8") == "# Meeting\n\nOriginal notes stay here.\n"
    assert (vault_path / ".obsidian").is_dir()
    assert cli.load_config()["vaults"]["work"]["path"] == str(vault_path.resolve())


def test_vault_add_only_registers_existing_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    note = vault_path / "Inbox.md"
    note.write_text("Important note\n", encoding="utf-8")

    status = cli.cmd_vault_add(Namespace(path=str(vault_path), name="inbox"))

    assert status == 0
    assert note.read_text(encoding="utf-8") == "Important note\n"
    assert not (vault_path / ".obsidian").exists()


def test_resolve_vault_prefers_registered_name_over_local_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    registered = tmp_path / "registered" / "Main"
    local = tmp_path / "cwd" / "Main"
    registered.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "cwd")
    cli.save_config({"vaults": {"Main": {"name": "Main", "path": str(registered)}}})

    assert cli.resolve_vault("Main") == registered


def test_safe_note_path_blocks_path_escape(tmp_path):
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()

    try:
        read_note(vault_path, "../outside.md")
    except VaultError as exc:
        assert "escapes vault" in str(exc)
    else:
        raise AssertionError("Expected VaultError")


def test_save_config_merges_without_dropping_existing_vaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    existing_vault = tmp_path / "Existing"
    existing_vault.mkdir()
    cli.save_config({"vaults": {"existing": {"name": "existing", "path": str(existing_vault)}}})

    new_config = cli.load_config()
    new_config.update({"chat": {"default_engine": "builtin"}})
    cli.save_config(new_config)

    saved = json.loads(cli.config_path().read_text(encoding="utf-8"))
    assert saved["vaults"]["existing"]["path"] == str(existing_vault)
    assert saved["chat"]["default_engine"] == "builtin"


def test_config_file_permissions_are_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cli.save_config({"omlx": {"api_key": "secret"}})

    mode = cli.config_path().stat().st_mode & 0o777
    assert mode == 0o600


def test_load_config_repairs_existing_loose_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = cli.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"ok": true}\n', encoding="utf-8")
    path.chmod(0o644)

    assert cli.load_config() == {"ok": True}
    assert path.stat().st_mode & 0o777 == 0o600
