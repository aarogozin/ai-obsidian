from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_DMR_BASE_URL = "http://localhost:12434/engines/v1"
RUNTIME_NATIVE_OMLX = "native-omlx"
RUNTIME_DOCKER_MODEL_RUNNER = "docker-model-runner"


@dataclass
class DockerRuntimeStatus:
    docker_cli: str | None
    desktop_running: bool
    daemon_running: bool
    model_runner_running: bool
    api_reachable: bool
    base_url: str
    models: list[str]
    backends: dict[str, dict[str, str]] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.docker_cli and self.daemon_running and self.model_runner_running and self.api_reachable)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def runtime_mode(config: dict[str, Any]) -> str:
    configured = config.get("runtime", {})
    mode = configured.get("mode") if isinstance(configured, dict) else None
    if mode in {RUNTIME_NATIVE_OMLX, RUNTIME_DOCKER_MODEL_RUNNER}:
        return mode
    legacy_mode = config.get("omlx", {}).get("mode")
    if legacy_mode == RUNTIME_DOCKER_MODEL_RUNNER:
        return RUNTIME_DOCKER_MODEL_RUNNER
    return RUNTIME_NATIVE_OMLX


def is_docker_runtime(config: dict[str, Any]) -> bool:
    return runtime_mode(config) == RUNTIME_DOCKER_MODEL_RUNNER


def docker_status(base_url: str = DEFAULT_DMR_BASE_URL) -> DockerRuntimeStatus:
    docker = shutil.which("docker")
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        reachable = api_reachable(base_url)
        return DockerRuntimeStatus(
            docker_cli=docker,
            desktop_running=False,
            daemon_running=True,
            model_runner_running=reachable,
            api_reachable=reachable,
            base_url=base_url,
            models=openai_model_list(base_url) if reachable else [],
            backends={},
            error=None if reachable else api_error(base_url),
        )

    if not docker:
        return DockerRuntimeStatus(
            docker_cli=None,
            desktop_running=False,
            daemon_running=False,
            model_runner_running=False,
            api_reachable=False,
            base_url=base_url,
            models=[],
            error="Docker CLI is not installed. Install Docker Desktop for Mac.",
        )

    desktop_running = docker_desktop_running(docker)
    daemon_running, daemon_error = docker_daemon_running(docker)
    if not daemon_running:
        return DockerRuntimeStatus(
            docker_cli=docker,
            desktop_running=desktop_running,
            daemon_running=False,
            model_runner_running=False,
            api_reachable=False,
            base_url=base_url,
            models=[],
            error=daemon_error or "Docker daemon is not running. Start Docker Desktop.",
        )

    runner_running, runner_error = docker_model_runner_running(docker)
    models = docker_model_list(docker) if runner_running else []
    backends = docker_model_backends(docker) if runner_running else {}
    reachable = api_reachable(base_url) if runner_running else False
    error = runner_error
    if runner_running and not reachable:
        error = api_error(base_url)
    return DockerRuntimeStatus(
        docker_cli=docker,
        desktop_running=desktop_running,
        daemon_running=True,
        model_runner_running=runner_running,
        api_reachable=reachable,
        base_url=base_url,
        models=models,
        backends=backends,
        error=error,
    )


def docker_desktop_running(docker: str) -> bool:
    result = run_capture([docker, "desktop", "status"], timeout=8)
    return result.returncode == 0 and "running" in result.stdout.lower()


def docker_daemon_running(docker: str) -> tuple[bool, str | None]:
    result = run_capture([docker, "info"], timeout=8)
    if result.returncode == 0:
        return True, None
    error = (result.stderr or result.stdout).strip()
    return False, error or "Docker daemon is not reachable."


def docker_model_runner_running(docker: str) -> tuple[bool, str | None]:
    result = run_capture([docker, "model", "status"], timeout=12)
    if result.returncode == 0:
        return True, None
    error = (result.stderr or result.stdout).strip()
    return False, error or "Docker Model Runner is not running or not enabled."


def docker_model_backends(docker: str | None = None) -> dict[str, dict[str, str]]:
    docker = docker or shutil.which("docker")
    if not docker:
        return {}
    result = run_capture([docker, "model", "status"], timeout=12)
    if result.returncode != 0:
        return {}
    return parse_docker_model_status(result.stdout)


def parse_docker_model_status(output: str) -> dict[str, dict[str, str]]:
    backends: dict[str, dict[str, str]] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("docker model runner") or line.startswith("BACKEND"):
            continue
        parts = re.split(r"\s{2,}", line, maxsplit=2)
        if len(parts) < 2:
            continue
        backend = parts[0].strip()
        status = parts[1].strip()
        details = parts[2].strip() if len(parts) > 2 else ""
        if backend:
            backends[backend] = {"status": status, "details": details}
    return backends


def docker_model_list(docker: str | None = None) -> list[str]:
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        return openai_model_list(os.environ.get("AI_OBSIDIAN_DMR_CONTAINER_BASE_URL", DEFAULT_DMR_BASE_URL))
    docker = docker or shutil.which("docker")
    if not docker:
        return []
    result = run_capture([docker, "model", "list", "--json"], timeout=20)
    if result.returncode != 0:
        return []
    return parse_docker_model_list(result.stdout)


def parse_docker_model_list(output: str) -> list[str]:
    output = output.strip()
    if not output:
        return []

    models: list[str] = []
    try:
        payload = json.loads(output)
        items = payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        items = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for item in items:
        if not isinstance(item, dict):
            continue
        item_models = docker_model_ids_from_item(item)
        for model in item_models:
            if model not in models:
                models.append(model)
    return models


def docker_model_ids_from_item(item: dict[str, Any]) -> list[str]:
    tags = item.get("tags")
    if isinstance(tags, list):
        parsed_tags = [str(tag) for tag in tags if isinstance(tag, str) and tag]
        if parsed_tags:
            return parsed_tags
    for key in ("model", "Model", "MODEL", "name", "Name", "id", "ID"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return [value]
    return []


def is_native_mlx_repo_id(model: str | None) -> bool:
    if not model:
        return False
    lowered = model.lower()
    if lowered.startswith(("hf.co/", "ai/", "docker.io/", "ghcr.io/")):
        return False
    owner = model.split("/", maxsplit=1)[0].lower()
    if owner in {"mlx-community", "unsloth"}:
        return True
    return "/mlx" in lowered or "-mlx-" in lowered or lowered.endswith("-mlx")


def is_docker_model_id(model: str | None) -> bool:
    return bool(model and not is_native_mlx_repo_id(model))


def docker_model_runner_suggestion_for_model(model: str | None, backends: dict[str, dict[str, str]] | None = None) -> str | None:
    if not model:
        return None
    lowered = model.lower()
    if is_native_mlx_repo_id(model):
        return (
            "This looks like a native oMLX/MLX Hugging Face repo id. "
            "Docker Model Runner needs a Docker model id such as `ai/smollm2` or a supported `hf.co/...` id."
        )
    if "mlx" in lowered:
        vllm = (backends or {}).get("vllm", {})
        if "not installed" in vllm.get("status", "").lower():
            return "For MLX/Metal models, install the vLLM runner first: docker model install-runner --backend vllm"
    return None


def ensure_docker_desktop_started(docker: str) -> int:
    if docker_desktop_running(docker):
        return 0
    print("Starting Docker Desktop.")
    result = subprocess.run([docker, "desktop", "start"], check=False)
    if result.returncode != 0:
        print("Could not start Docker Desktop. Open Docker Desktop, then retry.")
        return result.returncode
    return wait_for_docker_daemon(docker)


def wait_for_docker_daemon(docker: str, timeout_seconds: int = 90) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        running, _ = docker_daemon_running(docker)
        if running:
            return 0
        time.sleep(2)
    print("Docker daemon did not become ready in time.")
    return 1


def ensure_docker_model_runner(base_url: str = DEFAULT_DMR_BASE_URL) -> int:
    status = docker_status(base_url=base_url)
    if not status.docker_cli:
        print(status.error)
        return 1
    if not status.daemon_running:
        start_status = ensure_docker_desktop_started(status.docker_cli)
        if start_status != 0:
            return start_status
        status = docker_status(base_url=base_url)
    if status.model_runner_running:
        if status.api_reachable:
            return 0
        if enable_docker_model_runner_tcp(status.docker_cli) == 0:
            refreshed = docker_status(base_url=base_url)
            if refreshed.api_reachable:
                return 0
        print(f"Docker Model Runner is running, but the OpenAI endpoint is not reachable at {base_url}.")
        print("Try: docker desktop enable model-runner --tcp=12434")
        return 1

    print("Docker Model Runner is not ready.")
    if status.error:
        print(status.error)
    print("Enable Docker Model Runner in Docker Desktop, then retry.")
    print("Expected OpenAI-compatible endpoint: http://localhost:12434/engines/v1")
    return 1


def enable_docker_model_runner_tcp(docker: str) -> int:
    command = [docker, "desktop", "enable", "model-runner", "--tcp=12434"]
    print(f"Running: {' '.join(command)}")
    return subprocess.run(command, check=False).returncode


def api_reachable(base_url: str) -> bool:
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"{base_url.rstrip('/')}/models", timeout=5):
            return True
    except (OSError, URLError, TimeoutError):
        return False


def api_error(base_url: str) -> str:
    return f"OpenAI endpoint is not reachable at {base_url}. Run: docker desktop enable model-runner --tcp=12434"


def openai_model_list(base_url: str) -> list[str]:
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"{base_url.rstrip('/')}/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    models = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


def docker_model_pull(model: str) -> int:
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        print("Docker model pull must run on the host because Docker Model Runner is a Docker Desktop host feature.")
        print(f"Run on macOS: docker model pull {model}")
        return 1
    docker = shutil.which("docker")
    if not docker:
        print("Docker CLI is not installed. Install Docker Desktop for Mac.")
        return 1
    command = [docker, "model", "pull", model]
    print(f"Running: {' '.join(command)}")
    return subprocess.run(command, check=False).returncode


def docker_model_run_detached(model: str) -> int:
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        print("Docker model run must run on the host because Docker Model Runner is a Docker Desktop host feature.")
        print(f"Run on macOS: docker model run -d {model}")
        return 1
    docker = shutil.which("docker")
    if not docker:
        print("Docker CLI is not installed. Install Docker Desktop for Mac.")
        return 1
    command = [docker, "model", "run", "-d", model]
    print(f"Running: {' '.join(command)}")
    return subprocess.run(command, check=False).returncode


def run_capture(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr=str(exc))
