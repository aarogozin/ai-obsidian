from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


VALID_LANGUAGES = {"auto", "ru", "en"}
DEFAULT_STT_MODEL = "mlx-community/whisper-small-mlx"


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    command: list[str]


def find_mlx_whisper() -> str | None:
    path = shutil.which("mlx_whisper")
    if path:
        return path
    sibling = Path(sys.executable).parent / "mlx_whisper"
    if sibling.exists() and sibling.is_file():
        return str(sibling)
    return None


def has_mlx_whisper() -> bool:
    if find_mlx_whisper():
        return True
    try:
        __import__("mlx_whisper")
    except ImportError:
        return False
    return True


def build_transcription_command(
    audio_file: Path,
    *,
    language: str = "auto",
    model: str = DEFAULT_STT_MODEL,
) -> list[str]:
    if language not in VALID_LANGUAGES:
        allowed = ", ".join(sorted(VALID_LANGUAGES))
        raise ValueError(f"Unsupported language `{language}`. Use one of: {allowed}")

    options = {"model": model}
    if language != "auto":
        options["language"] = language

    script = (
        "import json, sys\n"
        "import inspect\n"
        "import mlx_whisper\n"
        "audio = sys.argv[1]\n"
        "options = json.loads(sys.argv[2])\n"
        "model = options.pop('model')\n"
        "parameters = inspect.signature(mlx_whisper.transcribe).parameters\n"
        "if 'path_or_hf_repo' in parameters:\n"
        "    options['path_or_hf_repo'] = model\n"
        "else:\n"
        "    options['model'] = model\n"
        "result = mlx_whisper.transcribe(audio, **options)\n"
        "text = result.get('text', '') if isinstance(result, dict) else str(result)\n"
        "print(text.strip())\n"
    )
    return [sys.executable, "-c", script, str(audio_file), json.dumps(options, sort_keys=True)]


def transcribe_audio(
    audio_file: Path,
    *,
    language: str = "auto",
    model: str = DEFAULT_STT_MODEL,
    timeout_seconds: int = 600,
) -> TranscriptionResult:
    audio_file = audio_file.expanduser().resolve()
    if not audio_file.exists() or not audio_file.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {audio_file}")
    if not has_mlx_whisper():
        raise RuntimeError(
            "mlx-whisper is not installed. Run `ai-obsidian install --execute` "
            "or install it into this environment with `python -m pip install mlx-whisper`."
        )

    command = build_transcription_command(audio_file, language=language, model=model)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
        env=transcription_environment(),
    )
    if result.returncode != 0:
        message = concise_error(result.stderr) or concise_error(result.stdout) or f"exit code {result.returncode}"
        raise RuntimeError(f"Transcription failed: {message}")
    text = result.stdout.strip()
    if not text:
        raise RuntimeError("Transcription finished but returned no text.")
    return TranscriptionResult(text=text, command=command)


def concise_error(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if line.startswith(("TypeError:", "ValueError:", "RuntimeError:", "ImportError:", "FileNotFoundError:")):
            return line
    return lines[-1]


def transcription_environment() -> dict[str, str]:
    env = os.environ.copy()
    path_parts = []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        path_parts.append(str(Path(ffmpeg).parent))
    path_parts.extend(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])

    existing = env.get("PATH", "")
    for part in existing.split(os.pathsep):
        if part:
            path_parts.append(part)
    env["PATH"] = os.pathsep.join(dedupe(path_parts))
    return env


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
