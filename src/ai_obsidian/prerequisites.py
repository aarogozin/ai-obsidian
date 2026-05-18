from __future__ import annotations

import platform
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class PrerequisiteStatus:
    arch_ok: bool
    macos_ok: bool
    brew_path: str | None
    obsidian_installed: bool
    omlx_installed: bool
    hf_cli_path: str | None
    ffmpeg_path: str | None = None
    mlx_whisper_available: bool = False


AskYesNo = Callable[[str, bool], bool]


def check_prerequisites() -> PrerequisiteStatus:
    brew_path = find_homebrew()
    return PrerequisiteStatus(
        arch_ok=platform.machine() == "arm64",
        macos_ok=is_supported_macos(),
        brew_path=brew_path,
        obsidian_installed=is_brew_cask_installed("obsidian"),
        omlx_installed=is_brew_formula_installed("omlx"),
        hf_cli_path=find_hf_cli(),
        ffmpeg_path=shutil.which("ffmpeg"),
        mlx_whisper_available=has_mlx_whisper(),
    )


def find_hf_cli() -> str | None:
    for name in ("hf", "huggingface-cli"):
        path = shutil.which(name)
        if path:
            return path
        sibling = Path(sys.executable).parent / name
        if sibling.exists() and sibling.is_file():
            return str(sibling)
    return None


def ensure_prerequisites(
    *,
    interactive: bool,
    ask_yes_no: AskYesNo | None = None,
    start_omlx_service: bool = True,
    allow_homebrew_install: bool = False,
    status: PrerequisiteStatus | None = None,
    print_status: bool = True,
) -> int:
    status = status or check_prerequisites()
    if print_status:
        print_prerequisite_status(status)

    if not status.arch_ok or not status.macos_ok:
        print("This project requires Apple Silicon and macOS 15 or newer.")
        return 1
    if not status.brew_path:
        if should_install_homebrew(interactive, ask_yes_no, allow_homebrew_install):
            if install_homebrew(noninteractive=not interactive) != 0:
                return 1
            status.brew_path = find_homebrew()
            if not status.brew_path:
                print("Homebrew install finished, but brew was not found at /opt/homebrew/bin/brew.")
                return 1
        else:
            print("Homebrew is required. Install Homebrew first, then re-run this command.")
            print("Official installer:")
            print('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
            return 1

    brew = status.brew_path
    if not status.obsidian_installed:
        if should_install(interactive, ask_yes_no, "Install Obsidian with Homebrew Cask?"):
            if run_command([brew, "install", "--cask", "obsidian"]) != 0:
                return 1
        else:
            print("Obsidian is required. Re-run setup and allow: brew install --cask obsidian")
            return 1
    elif status.obsidian_installed:
        print("Obsidian is already installed.")

    if not status.omlx_installed:
        if should_install(interactive, ask_yes_no, "Install oMLX with Homebrew?"):
            if run_command([brew, "tap", "jundot/omlx", "https://github.com/jundot/omlx"]) != 0:
                return 1
            if run_command([brew, "install", "omlx"]) != 0:
                return 1
        else:
            print("oMLX is required. Re-run setup and allow: brew install omlx")
            return 1
    elif status.omlx_installed:
        print("oMLX is already installed.")

    if not status.hf_cli_path:
        if should_install(interactive, ask_yes_no, "Install Hugging Face CLI into this Python environment?"):
            if install_huggingface_cli() != 0:
                return 1
        else:
            print("Hugging Face CLI is required for model downloads.")
            print("Install it later with: python3 -m pip install 'huggingface_hub[cli]'")
            return 1
    elif status.hf_cli_path:
        print(f"Hugging Face CLI is available: {status.hf_cli_path}")

    if not status.ffmpeg_path:
        if should_install(interactive, ask_yes_no, "Install ffmpeg for Obsidian push-to-talk audio handling?"):
            if run_command([brew, "install", "ffmpeg"]) != 0:
                return 1
        else:
            print("ffmpeg is required for reliable voice transcription from Obsidian recordings.")
            return 1
    elif status.ffmpeg_path:
        print(f"ffmpeg is available: {status.ffmpeg_path}")

    if not status.mlx_whisper_available:
        if should_install(interactive, ask_yes_no, "Install mlx-whisper into this Python environment for local speech-to-text?"):
            if install_mlx_whisper() != 0:
                return 1
        else:
            print("mlx-whisper is required for local push-to-talk transcription.")
            print("Install it later with: python3 -m pip install mlx-whisper")
            return 1
    else:
        print("mlx-whisper is available.")

    if start_omlx_service:
        if run_command([brew, "services", "start", "omlx"]) != 0:
            return 1

    return 0


def print_prerequisite_status(status: PrerequisiteStatus) -> None:
    print("Prerequisite check:")
    print(f"- Apple Silicon: {'ok' if status.arch_ok else 'needs attention'}")
    print(f"- macOS 15+: {'ok' if status.macos_ok else 'needs attention'}")
    print(f"- Homebrew: {status.brew_path or 'missing'}")
    print(f"- Obsidian: {'installed' if status.obsidian_installed else 'missing'}")
    print(f"- oMLX: {'installed' if status.omlx_installed else 'missing'}")
    print(f"- Hugging Face CLI: {status.hf_cli_path or 'missing'}")
    print(f"- ffmpeg: {status.ffmpeg_path or 'missing'}")
    print(f"- mlx-whisper: {'available' if status.mlx_whisper_available else 'missing'}")


def should_install(interactive: bool, ask_yes_no: AskYesNo | None, prompt: str) -> bool:
    if not interactive:
        return True
    if ask_yes_no is None:
        return False
    return ask_yes_no(prompt, default=True)


def should_install_homebrew(interactive: bool, ask_yes_no: AskYesNo | None, allow_homebrew_install: bool) -> bool:
    if interactive:
        if ask_yes_no is None:
            return False
        return ask_yes_no("Homebrew is missing. Install Homebrew now using the official installer?", default=True)
    return allow_homebrew_install or os.environ.get("AI_OBSIDIAN_ALLOW_HOMEBREW_INSTALL") == "1"


def install_homebrew(*, noninteractive: bool) -> int:
    command = [
        "/bin/bash",
        "-c",
        '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
    ]
    env = {"NONINTERACTIVE": "1"} if noninteractive else None
    return run_command(command, env=env)


def find_homebrew() -> str | None:
    path = shutil.which("brew")
    if path:
        return path
    default_path = Path("/opt/homebrew/bin/brew")
    if default_path.exists() and default_path.is_file():
        return str(default_path)
    return None


def install_huggingface_cli() -> int:
    command = [sys.executable, "-m", "pip", "install", "huggingface_hub[cli]"]
    return run_command(command)


def install_mlx_whisper() -> int:
    command = [sys.executable, "-m", "pip", "install", "mlx-whisper"]
    return run_command(command)


def has_mlx_whisper() -> bool:
    if shutil.which("mlx_whisper"):
        return True
    sibling = Path(sys.executable).parent / "mlx_whisper"
    if sibling.exists() and sibling.is_file():
        return True
    try:
        __import__("mlx_whisper")
    except ImportError:
        return False
    return True


def run_command(command: list[str], env: dict[str, str] | None = None) -> int:
    print(f"Running: {' '.join(command)}")
    sys.stdout.flush()
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
    result = subprocess.run(command, check=False, env=merged_env)
    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}: {' '.join(command)}")
    return result.returncode


def is_supported_macos() -> bool:
    version = platform.mac_ver()[0]
    if not version:
        return False
    major = int(version.split(".", maxsplit=1)[0])
    return major >= 15


def is_brew_cask_installed(name: str) -> bool:
    brew = find_homebrew()
    if not brew:
        return False
    result = subprocess.run([brew, "list", "--cask", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def is_brew_formula_installed(name: str) -> bool:
    brew = find_homebrew()
    if not brew:
        return False
    result = subprocess.run([brew, "list", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0
