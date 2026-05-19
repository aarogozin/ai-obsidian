from __future__ import annotations

from ai_obsidian import prerequisites


def test_ensure_prerequisites_installs_missing_items_in_order(monkeypatch):
    commands: list[list[str]] = []
    status = prerequisites.PrerequisiteStatus(
        arch_ok=True,
        macos_ok=True,
        brew_path="brew",
        obsidian_installed=False,
        omlx_installed=False,
        hf_cli_path=None,
        ffmpeg_path=None,
        mlx_whisper_available=False,
    )
    monkeypatch.setattr(prerequisites, "check_prerequisites", lambda: status)
    monkeypatch.setattr(prerequisites, "run_command", lambda command: commands.append(command) or 0)

    result = prerequisites.ensure_prerequisites(interactive=False, start_omlx_service=True)

    assert result == 0
    assert commands == [
        ["brew", "install", "--cask", "obsidian"],
        ["brew", "tap", "jundot/omlx", "https://github.com/jundot/omlx"],
        ["brew", "install", "omlx"],
        [prerequisites.sys.executable, "-m", "pip", "install", "huggingface_hub[cli]"],
        ["brew", "install", "ffmpeg"],
        [prerequisites.sys.executable, "-m", "pip", "install", "mlx-whisper"],
        ["brew", "services", "start", "omlx"],
    ]


def test_ensure_prerequisites_skips_installed_items(monkeypatch):
    commands: list[list[str]] = []
    status = prerequisites.PrerequisiteStatus(
        arch_ok=True,
        macos_ok=True,
        brew_path="brew",
        obsidian_installed=True,
        omlx_installed=True,
        hf_cli_path="hf",
        ffmpeg_path="ffmpeg",
        mlx_whisper_available=True,
    )
    monkeypatch.setattr(prerequisites, "check_prerequisites", lambda: status)
    monkeypatch.setattr(prerequisites, "run_command", lambda command: commands.append(command) or 0)

    result = prerequisites.ensure_prerequisites(interactive=False, start_omlx_service=True)

    assert result == 0
    assert commands == [["brew", "services", "start", "omlx"]]


def test_missing_homebrew_asks_and_installs_in_interactive_mode(monkeypatch):
    commands: list[tuple[list[str], dict[str, str] | None]] = []
    status = prerequisites.PrerequisiteStatus(
        arch_ok=True,
        macos_ok=True,
        brew_path=None,
        obsidian_installed=True,
        omlx_installed=True,
        hf_cli_path="hf",
        ffmpeg_path="ffmpeg",
        mlx_whisper_available=True,
    )
    monkeypatch.setattr(prerequisites, "check_prerequisites", lambda: status)
    monkeypatch.setattr(prerequisites, "find_homebrew", lambda: "brew")
    monkeypatch.setattr(
        prerequisites,
        "run_command",
        lambda command, env=None: commands.append((command, env)) or 0,
    )

    result = prerequisites.ensure_prerequisites(
        interactive=True,
        ask_yes_no=lambda prompt, default: True,
        start_omlx_service=False,
    )

    assert result == 0
    assert commands == [
        (
            [
                "/bin/bash",
                "-c",
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
            ],
            None,
        )
    ]


def test_missing_homebrew_does_not_install_noninteractive_without_allow(monkeypatch):
    commands: list[list[str]] = []
    status = prerequisites.PrerequisiteStatus(
        arch_ok=True,
        macos_ok=True,
        brew_path=None,
        obsidian_installed=True,
        omlx_installed=True,
        hf_cli_path="hf",
        ffmpeg_path="ffmpeg",
        mlx_whisper_available=True,
    )
    monkeypatch.setattr(prerequisites, "check_prerequisites", lambda: status)
    monkeypatch.setattr(prerequisites, "run_command", lambda command, env=None: commands.append(command) or 0)

    result = prerequisites.ensure_prerequisites(interactive=False, start_omlx_service=False)

    assert result == 1
    assert commands == []


def test_missing_homebrew_installs_noninteractive_when_allowed(monkeypatch):
    commands: list[tuple[list[str], dict[str, str] | None]] = []
    status = prerequisites.PrerequisiteStatus(
        arch_ok=True,
        macos_ok=True,
        brew_path=None,
        obsidian_installed=True,
        omlx_installed=True,
        hf_cli_path="hf",
        ffmpeg_path="ffmpeg",
        mlx_whisper_available=True,
    )
    monkeypatch.setattr(prerequisites, "check_prerequisites", lambda: status)
    monkeypatch.setattr(prerequisites, "find_homebrew", lambda: "brew")
    monkeypatch.setattr(
        prerequisites,
        "run_command",
        lambda command, env=None: commands.append((command, env)) or 0,
    )

    result = prerequisites.ensure_prerequisites(
        interactive=False,
        start_omlx_service=False,
        allow_homebrew_install=True,
    )

    assert result == 0
    assert commands == [
        (
            [
                "/bin/bash",
                "-c",
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
            ],
            {"NONINTERACTIVE": "1"},
        )
    ]


def test_find_homebrew_uses_apple_silicon_default_path(tmp_path, monkeypatch):
    brew = tmp_path / "brew"
    brew.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(prerequisites.shutil, "which", lambda name: None)
    monkeypatch.setattr(prerequisites, "Path", lambda value: brew if value == "/opt/homebrew/bin/brew" else tmp_path / value)

    assert prerequisites.find_homebrew() == str(brew)


def test_find_hf_cli_checks_current_python_bin(tmp_path, monkeypatch):
    python_bin = tmp_path / "venv" / "bin"
    python_bin.mkdir(parents=True)
    hf = python_bin / "hf"
    hf.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(prerequisites.shutil, "which", lambda name: None)
    monkeypatch.setattr(prerequisites.sys, "executable", str(python_bin / "python"))

    assert prerequisites.find_hf_cli() == str(hf)


def test_find_executable_checks_common_gui_hidden_paths(tmp_path, monkeypatch):
    hermes = tmp_path / ".local" / "bin" / "hermes"
    hermes.parent.mkdir(parents=True)
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(prerequisites.shutil, "which", lambda name: None)
    monkeypatch.setattr(prerequisites.Path, "home", classmethod(lambda cls: tmp_path))

    assert prerequisites.find_executable("hermes") == str(hermes)


def test_ensure_hermes_cli_installed_uses_official_installer_when_allowed(monkeypatch):
    commands: list[list[str]] = []
    installed = {"done": False}

    def fake_find(name: str):
        return "hermes" if installed["done"] else None

    def fake_run(command, env=None):
        commands.append(command)
        installed["done"] = True
        return 0

    monkeypatch.setattr(prerequisites, "find_executable", fake_find)
    monkeypatch.setattr(prerequisites, "run_command", fake_run)

    status = prerequisites.ensure_hermes_cli_installed(allow_install=True)

    assert status == 0
    assert commands == [
        [
            "/bin/bash",
            "-c",
            "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash",
        ]
    ]
