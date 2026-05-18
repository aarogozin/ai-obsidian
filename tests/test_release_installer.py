from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"


def installer_env(tmp_path: Path, extra_path: str | None = None) -> dict[str, str]:
    path = extra_path if extra_path is not None else os.environ.get("PATH", "")
    return {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": path,
        "AI_OBSIDIAN_TEST_UNAME_S": "Darwin",
        "AI_OBSIDIAN_TEST_UNAME_M": "arm64",
        "AI_OBSIDIAN_TEST_MACOS_VERSION": "15.0",
    }


def test_install_script_has_valid_bash_syntax():
    result = subprocess.run(["bash", "-n", str(INSTALLER)], check=False)

    assert result.returncode == 0


def test_install_script_dry_run_handles_clean_mac_without_homebrew(tmp_path):
    env = installer_env(tmp_path, extra_path="/usr/bin:/bin")
    env["AI_OBSIDIAN_TEST_NO_DEFAULT_BREW"] = "1"
    result = subprocess.run(
        [
            str(INSTALLER),
            "--dry-run",
            "--yes",
            "--no-init",
            "--source-dir",
            str(ROOT),
            "--install-dir",
            str(tmp_path / "install"),
            "--bin-dir",
            str(tmp_path / "bin"),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Homebrew is missing" in result.stdout
    assert "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh" in result.stdout
    assert "not in PATH" in result.stdout
    assert not (tmp_path / "install").exists()


def test_install_script_local_source_install_writes_path_shim(tmp_path):
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    brew = stub_dir / "brew"
    brew.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    brew.chmod(0o755)
    env = installer_env(tmp_path, extra_path=f"{stub_dir}:{os.environ.get('PATH', '')}")
    install_dir = tmp_path / "install"
    bin_dir = tmp_path / "bin"

    result = subprocess.run(
        [
            str(INSTALLER),
            "--yes",
            "--no-init",
            "--source-dir",
            str(ROOT),
            "--install-dir",
            str(install_dir),
            "--bin-dir",
            str(bin_dir),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    shim = bin_dir / "ai-obsidian"
    assert shim.exists()
    assert os.access(shim, os.X_OK)
    assert f'exec "{install_dir}/.venv/bin/ai-obsidian"' in shim.read_text(encoding="utf-8")
    help_result = subprocess.run([str(shim), "--help"], text=True, capture_output=True, check=False)
    assert help_result.returncode == 0
    assert "Install and operate a local Obsidian" in help_result.stdout
