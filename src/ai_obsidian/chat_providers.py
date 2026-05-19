from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass

from .prerequisites import find_executable


SUPPORTED_EXTERNAL_ENGINES = {"hermes", "claude"}
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 180
MAX_EXTERNAL_PROMPT_CHARS = 48_000


@dataclass(frozen=True)
class ExternalEngineStatus:
    engine: str
    available: bool
    executable: str | None
    detail: str


@dataclass(frozen=True)
class ProviderInvocation:
    command: list[str]
    stdin: str | None = None


class ExternalProviderError(RuntimeError):
    pass


def external_engine_status(engine: str) -> ExternalEngineStatus:
    if engine not in SUPPORTED_EXTERNAL_ENGINES:
        return ExternalEngineStatus(
            engine=engine,
            available=False,
            executable=None,
            detail="adapter is not implemented",
        )

    executable = find_executable(engine)
    if executable:
        return ExternalEngineStatus(
            engine=engine,
            available=True,
            executable=executable,
            detail="available",
        )
    return ExternalEngineStatus(
        engine=engine,
        available=False,
        executable=None,
        detail=f"{engine} is not installed or not on PATH",
    )


def external_engine_statuses() -> dict[str, dict[str, str | bool | None]]:
    return {
        engine: {
            "available": status.available,
            "executable": status.executable,
            "detail": status.detail,
        }
        for engine in ("hermes", "claude")
        for status in [external_engine_status(engine)]
    }


def ask_external_provider(engine: str, prompt: str, timeout_seconds: int = DEFAULT_EXTERNAL_TIMEOUT_SECONDS) -> str:
    prompt = compact_prompt(prompt)
    invocation = build_provider_invocation(engine, prompt)
    try:
        result = subprocess.run(
            invocation.command,
            input=invocation.stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError as exc:
        raise ExternalProviderError(missing_executable_message(engine)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalProviderError(f"{engine} did not finish within {timeout_seconds} seconds.") from exc
    except OSError as exc:
        raise ExternalProviderError(f"{engine} could not be started: {exc}") from exc

    if result.returncode != 0:
        raise ExternalProviderError(concise_process_error(engine, result))

    output = (result.stdout or "").strip()
    if not output:
        raise ExternalProviderError(f"{engine} returned an empty response.")
    return output


def build_provider_invocation(engine: str, prompt: str) -> ProviderInvocation:
    executable = find_executable(engine)
    if not executable:
        raise ExternalProviderError(missing_executable_message(engine))

    if engine == "hermes":
        return ProviderInvocation([executable, "--ignore-rules", "-z", prompt])
    if engine == "claude":
        return ProviderInvocation(
            [
                executable,
                "--print",
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--no-session-persistence",
            ],
            stdin=prompt,
        )
    raise ExternalProviderError(f"Unsupported external chat engine: {engine}")


def compact_prompt(prompt: str, max_chars: int = MAX_EXTERNAL_PROMPT_CHARS) -> str:
    if len(prompt) <= max_chars:
        return prompt
    keep_head = max_chars // 3
    keep_tail = max_chars - keep_head
    return (
        prompt[:keep_head]
        + "\n\n[...context truncated by AI Obsidian to keep the external CLI prompt bounded...]\n\n"
        + prompt[-keep_tail:]
    )


def missing_executable_message(engine: str) -> str:
    if engine == "hermes":
        return "Hermes CLI is not installed or not on PATH. Install Hermes or use `--engine builtin`."
    if engine == "claude":
        return "Claude Code CLI is not installed or not on PATH. Install Claude Code or use `--engine builtin`."
    return f"{engine} is not installed or not on PATH."


def concise_process_error(engine: str, result: subprocess.CompletedProcess[str]) -> str:
    raw = (result.stderr or result.stdout or "").strip()
    if not raw:
        return f"{engine} exited with code {result.returncode}."

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    summary = "\n".join(lines[:8])
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "..."
    hint = auth_hint(engine, raw)
    suffix = f"\n{hint}" if hint else ""
    return f"{engine} exited with code {result.returncode}:\n{summary}{suffix}"


def auth_hint(engine: str, output: str) -> str | None:
    lowered = output.lower()
    if any(token in lowered for token in ("auth", "login", "api key", "not authenticated")):
        return f"Run `{engine}` once in your terminal to finish authentication, then retry AI Obsidian."
    return None
