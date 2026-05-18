from __future__ import annotations

import subprocess

from ai_obsidian import cli
from ai_obsidian import voice


def test_build_transcription_command_includes_model_and_language(tmp_path):
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"audio")

    command = voice.build_transcription_command(audio, language="ru", model="mlx-community/whisper-small-mlx")

    assert command[0] == voice.sys.executable
    assert str(audio) in command
    assert '"language": "ru"' in command[-1]
    assert '"model": "mlx-community/whisper-small-mlx"' in command[-1]
    assert "path_or_hf_repo" in command[2]


def test_transcribe_audio_runs_mlx_whisper_backend(tmp_path, monkeypatch):
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"audio")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "hello world\n"
        stderr = ""

    monkeypatch.setattr(voice, "has_mlx_whisper", lambda: True)
    monkeypatch.setattr(
        voice.subprocess,
        "run",
        lambda command, capture_output, text, check, timeout, env: calls.append(command) or Result(),
    )

    result = voice.transcribe_audio(audio, language="en", model="mlx-community/whisper-small-mlx")

    assert result.text == "hello world"
    assert calls == [result.command]


def test_transcribe_audio_reports_backend_failure(tmp_path, monkeypatch):
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"audio")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "bad audio"

    monkeypatch.setattr(voice, "has_mlx_whisper", lambda: True)
    monkeypatch.setattr(
        voice.subprocess,
        "run",
        lambda command, capture_output, text, check, timeout, env: Result(),
    )

    try:
        voice.transcribe_audio(audio)
    except RuntimeError as exc:
        assert "bad audio" in str(exc)
    else:
        raise AssertionError("expected transcription failure")


def test_transcribe_audio_reports_concise_traceback_error(tmp_path, monkeypatch):
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"audio")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Fetching 4 files: 100%\nTraceback...\nTypeError: unexpected keyword argument 'model'\n"

    monkeypatch.setattr(voice, "has_mlx_whisper", lambda: True)
    monkeypatch.setattr(
        voice.subprocess,
        "run",
        lambda command, capture_output, text, check, timeout, env: Result(),
    )

    try:
        voice.transcribe_audio(audio)
    except RuntimeError as exc:
        assert str(exc) == "Transcription failed: TypeError: unexpected keyword argument 'model'"
    else:
        raise AssertionError("expected transcription failure")


def test_cmd_voice_transcribe_prints_text(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"audio")

    monkeypatch.setattr(
        cli,
        "transcribe_audio",
        lambda path, language, model: voice.TranscriptionResult(text="привет", command=[]),
    )

    status = cli.cmd_voice_transcribe(
        type("Args", (), {"audio_file": str(audio), "language": "ru", "model": voice.DEFAULT_STT_MODEL})()
    )

    assert status == 0
    assert capsys.readouterr().out == "привет\n"


def test_transcription_environment_adds_homebrew_ffmpeg_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)

    env = voice.transcription_environment()

    parts = env["PATH"].split(":")
    assert parts[:4] == ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def test_cmd_voice_transcribe_returns_error_without_modifying_notes(tmp_path, monkeypatch):
    audio = tmp_path / "sample.webm"
    note = tmp_path / "note.md"
    audio.write_bytes(b"audio")
    note.write_text("keep\n", encoding="utf-8")

    def fail(path, language, model):
        raise RuntimeError("no microphone")

    monkeypatch.setattr(cli, "transcribe_audio", fail)

    status = cli.cmd_voice_transcribe(
        type("Args", (), {"audio_file": str(audio), "language": "auto", "model": voice.DEFAULT_STT_MODEL})()
    )

    assert status == 1
    assert note.read_text(encoding="utf-8") == "keep\n"
