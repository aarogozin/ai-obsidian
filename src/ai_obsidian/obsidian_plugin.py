from __future__ import annotations

import difflib
import hashlib
import importlib.resources
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .soul import prompt_has_current_soul, read_soul, sync_soul_managed_block


DEFAULT_PLUGIN_ID = "local-llm-hub"
FALLBACK_PLUGIN_ID = "local-llm-helper"
COMPANION_PLUGIN_ID = "ai-obsidian-companion"


@dataclass(frozen=True)
class PluginDefinition:
    id: str
    name: str
    repo: str
    command_hint: str
    local_resource: str | None = None


PLUGIN_DEFINITIONS = {
    DEFAULT_PLUGIN_ID: PluginDefinition(
        id=DEFAULT_PLUGIN_ID,
        name="Local LLM Hub",
        repo="takeshy/obsidian-local-llm-hub",
        command_hint="Open Local LLM Hub chat from the Obsidian ribbon or command palette.",
    ),
    FALLBACK_PLUGIN_ID: PluginDefinition(
        id=FALLBACK_PLUGIN_ID,
        name="Local LLM Helper",
        repo="manimohans/obsidian-local-llm-helper",
        command_hint="Command Palette -> Chat: Notes (RAG) or open the Local LLM Helper sidebar.",
    ),
    COMPANION_PLUGIN_ID: PluginDefinition(
        id=COMPANION_PLUGIN_ID,
        name="AI Obsidian Companion",
        repo="local",
        command_hint="Use the microphone ribbon icon or Command Palette -> AI Obsidian: Push to Talk.",
        local_resource="obsidian_companion",
    ),
}


@dataclass
class PluginStatus:
    definition: PluginDefinition
    vault_path: Path
    plugin_dir: Path
    installed: bool
    enabled: bool
    configured: bool
    version: str | None = None


@dataclass
class PluginVerification:
    ok: bool
    checks: list[tuple[str, bool, str]]


def normalize_plugin_id(plugin_id: str | None) -> str:
    if not plugin_id:
        return DEFAULT_PLUGIN_ID
    if plugin_id in {"hub", "local-llm-hub"}:
        return DEFAULT_PLUGIN_ID
    if plugin_id in {"helper", "local-llm-helper"}:
        return FALLBACK_PLUGIN_ID
    if plugin_id in {"companion", COMPANION_PLUGIN_ID}:
        return COMPANION_PLUGIN_ID
    return plugin_id


def plugin_definition(plugin_id: str | None) -> PluginDefinition:
    normalized = normalize_plugin_id(plugin_id)
    try:
        return PLUGIN_DEFINITIONS[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(PLUGIN_DEFINITIONS))
        raise ValueError(f"Unknown plugin `{normalized}`. Available plugins: {available}") from exc


def plugin_dir(vault_path: Path, plugin_id: str) -> Path:
    return vault_path / ".obsidian" / "plugins" / plugin_id


def plugin_data_path(vault_path: Path, plugin_id: str) -> Path:
    return plugin_dir(vault_path, plugin_id) / "data.json"


def enabled_plugins_path(vault_path: Path) -> Path:
    return vault_path / ".obsidian" / "community-plugins.json"


def plugin_status(vault_path: Path, plugin_id: str | None = None) -> PluginStatus:
    definition = plugin_definition(plugin_id)
    directory = plugin_dir(vault_path, definition.id)
    manifest = directory / "manifest.json"
    version = None
    if manifest.exists():
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        except (OSError, json.JSONDecodeError):
            version = None
    return PluginStatus(
        definition=definition,
        vault_path=vault_path,
        plugin_dir=directory,
        installed=manifest.exists() and (directory / "main.js").exists(),
        enabled=is_plugin_enabled(vault_path, definition.id),
        configured=plugin_data_path(vault_path, definition.id).exists(),
        version=version,
    )


def print_plugin_status(status: PluginStatus) -> None:
    print(f"{status.definition.name} ({status.definition.id})")
    print(f"Vault: {status.vault_path}")
    print(f"Plugin directory: {status.plugin_dir}")
    print(f"Installed: {'yes' if status.installed else 'no'}")
    print(f"Enabled in Obsidian: {'yes' if status.enabled else 'no'}")
    print(f"Configured: {'yes' if status.configured else 'no'}")
    if status.version:
        print(f"Version: {status.version}")


def print_plugin_verification(verification: PluginVerification) -> None:
    print("Plugin verification:")
    for label, ok, detail in verification.checks:
        marker = "ok" if ok else "needs attention"
        suffix = f": {detail}" if detail else ""
        print(f"- {label}: {marker}{suffix}")


def install_plugin(
    vault_path: Path,
    plugin_id: str | None = None,
    *,
    enable: bool = True,
    force: bool = False,
) -> int:
    definition = plugin_definition(plugin_id)
    target_dir = plugin_dir(vault_path, definition.id)
    manifest = target_dir / "manifest.json"
    if manifest.exists() and not force:
        if definition.local_resource and local_plugin_needs_update(target_dir, definition):
            print(f"Updating bundled {definition.name}: {target_dir}")
            return install_local_plugin(vault_path, definition, enable=enable, force=False)
        print(f"{definition.name} is already installed: {target_dir}")
        if enable:
            enable_plugin(vault_path, definition.id)
        return 0

    if definition.local_resource:
        return install_local_plugin(vault_path, definition, enable=enable, force=force)

    try:
        assets = latest_release_assets(definition.repo)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Could not fetch latest {definition.name} release: {exc}")
        print(f"Manual fallback: install from https://github.com/{definition.repo}/releases/latest")
        return 1
    required = ["main.js", "manifest.json", "styles.css"]
    missing = [name for name in required if name not in assets]
    if missing:
        print(f"Latest {definition.name} release is missing required assets: {', '.join(missing)}")
        print(f"Manual fallback: install from https://github.com/{definition.repo}/releases/latest")
        return 1

    if target_dir.exists() and force:
        backup_existing_path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for name in required:
        destination = target_dir / name
        print(f"Downloading {definition.name} {name}")
        print(f"  {assets[name]}")
        try:
            download_url(assets[name], destination)
        except RuntimeError as exc:
            print(exc)
            return 1

    if enable:
        enable_plugin(vault_path, definition.id)
    print(f"Installed {definition.name}: {target_dir}")
    return 0


def local_plugin_needs_update(target_dir: Path, definition: PluginDefinition) -> bool:
    if not definition.local_resource:
        return False
    try:
        resource_dir = importlib.resources.files("ai_obsidian.resources").joinpath(definition.local_resource)
        for name in ("main.js", "manifest.json", "styles.css"):
            source = resource_dir.joinpath(name)
            destination = target_dir / name
            if not destination.exists() or destination.read_bytes() != source.read_bytes():
                return True
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return False
    return False


def install_local_plugin(
    vault_path: Path,
    definition: PluginDefinition,
    *,
    enable: bool,
    force: bool,
) -> int:
    target_dir = plugin_dir(vault_path, definition.id)
    if target_dir.exists() and force:
        backup_existing_path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        resource_dir = importlib.resources.files("ai_obsidian.resources").joinpath(definition.local_resource)
        for name in ("main.js", "manifest.json", "styles.css"):
            source = resource_dir.joinpath(name)
            destination = target_dir / name
            destination.write_bytes(source.read_bytes())
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        print(f"Could not install bundled {definition.name}: {exc}")
        return 1

    if enable:
        enable_plugin(vault_path, definition.id)
    print(f"Installed {definition.name}: {target_dir}")
    return 0


def latest_release_assets(repo: str) -> dict[str, str]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assets: dict[str, str] = {}
    for asset in payload.get("assets", []):
        name = asset.get("name")
        download = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(download, str):
            assets[name] = download
    return assets


def download_url(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with destination.open("wb") as file:
                shutil.copyfileobj(response, file)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download {url}: {exc}") from exc


def is_plugin_enabled(vault_path: Path, plugin_id: str) -> bool:
    path = enabled_plugins_path(vault_path)
    if not path.exists():
        return False
    try:
        enabled = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(enabled, list) and plugin_id in enabled


def enable_plugin(vault_path: Path, plugin_id: str) -> None:
    path = enabled_plugins_path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    enabled: list[str] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                enabled = [item for item in payload if isinstance(item, str)]
        except (OSError, json.JSONDecodeError):
            backup_existing_path(path)
    if plugin_id not in enabled:
        enabled.append(plugin_id)
    path.write_text(json.dumps(enabled, indent=2) + "\n", encoding="utf-8")


def configure_plugin(
    vault_path: Path,
    config: dict[str, Any],
    plugin_id: str | None = None,
    *,
    ask_yes_no: Callable[[str, bool], bool] | None = None,
    yes: bool = False,
) -> int:
    definition = plugin_definition(plugin_id)
    data_path = plugin_data_path(vault_path, definition.id)
    data_path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_json_object(data_path)
    desired = desired_plugin_settings(definition.id, existing, config, vault_path=vault_path)

    if existing == desired:
        print(f"{definition.name} settings already match AI Obsidian config.")
        return 0

    print_settings_diff(data_path, existing, desired)
    should_write = yes
    if not should_write:
        if ask_yes_no is None:
            print("Refusing to write plugin settings without confirmation.")
            return 1
        should_write = ask_yes_no(f"Write {definition.name} settings to {data_path}?", default=True)
    if not should_write:
        print("Plugin settings unchanged.")
        return 1

    if data_path.exists():
        backup_existing_path(data_path)
    data_path.write_text(json.dumps(desired, indent=2) + "\n", encoding="utf-8")
    print(f"Configured {definition.name}: {data_path}")
    return 0


def verify_plugin(vault_path: Path, plugin_id: str | None = None) -> PluginVerification:
    return verify_plugin_with_config(vault_path, plugin_id, config=None)


def verify_plugin_with_config(
    vault_path: Path,
    plugin_id: str | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> PluginVerification:
    definition = plugin_definition(plugin_id)
    status = plugin_status(vault_path, definition.id)
    checks: list[tuple[str, bool, str]] = [
        ("installed", status.installed, str(status.plugin_dir)),
        ("enabled", status.enabled, str(enabled_plugins_path(vault_path))),
        ("configured", status.configured, str(plugin_data_path(vault_path, definition.id))),
    ]

    if status.installed:
        for asset in ("manifest.json", "main.js", "styles.css"):
            path = status.plugin_dir / asset
            checks.append((asset, path.exists(), str(path)))

    if definition.id == COMPANION_PLUGIN_ID:
        checks.extend(verify_companion_plugin(vault_path))
    elif definition.id in {DEFAULT_PLUGIN_ID, FALLBACK_PLUGIN_ID}:
        checks.extend(verify_llm_plugin(vault_path, definition.id, config))

    return PluginVerification(ok=all(ok for _, ok, _ in checks), checks=checks)


def verify_llm_plugin(vault_path: Path, plugin_id: str, config: dict[str, Any] | None) -> list[tuple[str, bool, str]]:
    data = read_json_object(plugin_data_path(vault_path, plugin_id))
    if config is None:
        if plugin_id == DEFAULT_PLUGIN_ID:
            llm_config = data.get("llmConfig")
            return [("llmConfig", isinstance(llm_config, dict), "present" if isinstance(llm_config, dict) else "missing")]
        return [("settings", bool(data), "present" if data else "missing")]

    omlx = (config or {}).get("omlx", {})
    expected_model = str(omlx.get("selected_model") or "")
    expected_base_url = str(omlx.get("base_url") or "http://localhost:8000/v1").rstrip("/")
    expected_openai_root = expected_base_url[:-3] if expected_base_url.endswith("/v1") else expected_base_url
    expected_api_key = str(omlx.get("api_key") or "")

    if plugin_id == DEFAULT_PLUGIN_ID:
        llm_config = data.get("llmConfig")
        checks: list[tuple[str, bool, str]] = [
            ("llmConfig", isinstance(llm_config, dict), "present" if isinstance(llm_config, dict) else "missing"),
        ]
        llm = llm_config if isinstance(llm_config, dict) else {}
        checks.append(("base URL", llm.get("baseUrl") == expected_openai_root, str(llm.get("baseUrl") or "missing")))
        checks.append(("model", bool(expected_model) and llm.get("model") == expected_model, str(llm.get("model") or "missing")))
        if expected_api_key:
            checks.append(("API key", llm.get("apiKey") == expected_api_key, "configured" if llm.get("apiKey") else "missing"))
        available = data.get("availableModels")
        checks.append(
            (
                "availableModels",
                isinstance(available, list) and expected_model in available,
                "contains model" if isinstance(available, list) and expected_model in available else "missing model",
            )
        )
        soul_text = read_soul(vault_path)
        if soul_text:
            prompt = str(data.get("systemPrompt") or "")
            checks.append(
                (
                    "soul prompt",
                    prompt_has_current_soul(prompt, soul_text),
                    "synced" if prompt_has_current_soul(prompt, soul_text) else "missing managed block",
                )
            )
        return checks

    checks = [
        ("server address", data.get("serverAddress") == expected_base_url, str(data.get("serverAddress") or "missing")),
        ("model", bool(expected_model) and data.get("llmModel") == expected_model, str(data.get("llmModel") or "missing")),
    ]
    if expected_api_key:
        checks.append(("API key", data.get("openAIApiKey") == expected_api_key, "configured" if data.get("openAIApiKey") else "missing"))
    return checks


def verify_companion_plugin(vault_path: Path) -> list[tuple[str, bool, str]]:
    data = read_json_object(plugin_data_path(vault_path, COMPANION_PLUGIN_ID))
    cli_path = str(data.get("cliPath") or "")
    language = str(data.get("language") or "")
    target_mode = str(data.get("targetMode") or "")
    insert_mode = str(data.get("insertMode") or "")

    checks: list[tuple[str, bool, str]] = [
        ("language", language in {"auto", "ru", "en"}, language or "missing"),
        ("target mode", target_mode in {"smart", "note", "chat"}, target_mode or "missing"),
        ("insert mode", insert_mode in {"cursor", "append"}, insert_mode or "missing"),
        ("confirm before insert", isinstance(data.get("confirmBeforeInsert"), bool), str(data.get("confirmBeforeInsert"))),
    ]

    resolved_cli = resolve_executable(cli_path)
    checks.append(("AI Obsidian CLI", resolved_cli is not None, cli_path or "missing"))
    if resolved_cli:
        checks.append(("voice command", command_help_ok([resolved_cli, "voice", "transcribe", "--help"]), resolved_cli))

    checks.append(("ffmpeg", shutil.which("ffmpeg") is not None, shutil.which("ffmpeg") or "missing"))
    checks.append(("mlx-whisper", has_mlx_whisper_runtime(), "python package or mlx_whisper command"))
    return checks


def resolve_executable(command: str) -> str | None:
    if not command:
        return None
    path = Path(command).expanduser()
    if path.is_absolute() or "/" in command:
        return str(path) if path.exists() and path.is_file() else None
    return shutil.which(command)


def command_help_ok(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def has_mlx_whisper_runtime() -> bool:
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


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def desired_plugin_settings(
    plugin_id: str,
    existing: dict[str, Any],
    config: dict[str, Any],
    *,
    vault_path: Path | None = None,
) -> dict[str, Any]:
    omlx = config.get("omlx", {})
    base_url = str(omlx.get("base_url") or "http://localhost:8000/v1").rstrip("/")
    openai_root = base_url[:-3] if base_url.endswith("/v1") else base_url
    model = str(omlx.get("selected_model") or "")
    api_key = str(omlx.get("api_key") or "")

    desired = json.loads(json.dumps(existing))
    if plugin_id == DEFAULT_PLUGIN_ID:
        llm_config = dict(desired.get("llmConfig") or {})
        llm_config.update(
            {
                "framework": "lm-studio",
                "baseUrl": openai_root,
                "model": model,
            }
        )
        if api_key:
            llm_config["apiKey"] = api_key
        else:
            llm_config.pop("apiKey", None)
        desired["llmConfig"] = llm_config
        desired["llmVerified"] = False
        if model:
            available = desired.get("availableModels")
            if not isinstance(available, list):
                available = []
            if model not in available:
                available.append(model)
            desired["availableModels"] = available
        desired.setdefault("saveChatHistory", True)
        desired.setdefault("hideWorkspaceFolder", True)
        desired.setdefault("mcpServers", [])
        if vault_path is not None:
            soul_text = read_soul(vault_path)
            if soul_text:
                desired["systemPrompt"] = sync_soul_managed_block(str(desired.get("systemPrompt") or ""), soul_text)
        return desired

    if plugin_id == COMPANION_PLUGIN_ID:
        companion = dict(config.get("companion", {}))
        voice = dict(config.get("voice", {}))
        desired.update(
            {
                "cliPath": str(companion.get("cli_path") or default_ai_obsidian_binary()),
                "language": str(voice.get("language") or "auto"),
                "targetMode": str(companion.get("target_mode") or "smart"),
                "insertMode": str(companion.get("insert_mode") or "cursor"),
                "confirmBeforeInsert": bool(companion.get("confirm_before_insert", True)),
            }
        )
        return desired

    desired.update(
        {
            "providerType": "openai",
            "serverAddress": base_url,
            "llmModel": model,
            "openAIApiKey": api_key or "not-needed",
        }
    )
    desired.setdefault("embeddingServerAddress", "")
    desired.setdefault("embeddingModelName", "nomic-embed-text")
    return desired


def default_ai_obsidian_binary() -> str:
    path = shutil.which("ai-obsidian")
    if path:
        return path
    sibling = Path(sys.executable).parent / "ai-obsidian"
    if sibling.exists() and sibling.is_file():
        return str(sibling)
    launcher = Path(__file__).resolve().parents[2] / "ai-obsidian"
    if launcher.exists():
        return str(launcher)
    return "ai-obsidian"


def print_settings_diff(path: Path, existing: dict[str, Any], desired: dict[str, Any]) -> None:
    before = json.dumps(existing, indent=2, sort_keys=True).splitlines()
    after = json.dumps(desired, indent=2, sort_keys=True).splitlines()
    print(f"Proposed plugin settings change: {path}")
    for line in difflib.unified_diff(before, after, fromfile="current", tofile="desired", lineterm=""):
        print(line)


def backup_existing_path(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak-{int(time.time())}")
    if path.is_dir():
        shutil.copytree(path, backup)
    elif path.exists():
        shutil.copy2(path, backup)
    print(f"Backup created: {backup}")
    return backup


def obsidian_app_config_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"


def register_obsidian_vault(vault_path: Path) -> None:
    if sys.platform != "darwin":
        return

    registry_path = obsidian_app_config_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry = read_json_object(registry_path)
    vaults = registry.get("vaults")
    if not isinstance(vaults, dict):
        vaults = {}

    resolved = str(vault_path.expanduser().resolve())
    for entry in vaults.values():
        if isinstance(entry, dict) and entry.get("path") == resolved:
            return

    if registry_path.exists():
        backup_existing_path(registry_path)

    vault_id = stable_vault_id(resolved, vaults)
    vaults[vault_id] = {
        "path": resolved,
        "ts": int(time.time() * 1000),
        "open": True,
    }
    registry["vaults"] = vaults
    registry_path.write_text(json.dumps(registry, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Registered vault in Obsidian: {resolved}")


def stable_vault_id(path: str, existing_vaults: dict[str, Any]) -> str:
    base = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    if base not in existing_vaults:
        return base
    index = 1
    while True:
        candidate = hashlib.sha256(f"{path}:{index}".encode("utf-8")).hexdigest()[:16]
        if candidate not in existing_vaults:
            return candidate
        index += 1


def open_obsidian_vault(vault_path: Path) -> int:
    resolved = vault_path.expanduser().resolve()
    if sys.platform == "darwin":
        register_obsidian_vault(resolved)

        command = ["open", "-a", "Obsidian", str(resolved)]
        print(f"Running: {' '.join(command)}")
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            return 0

        fallback = ["open", f"obsidian://open?path={quote(str(resolved), safe='')}"]
        print(f"Running: {' '.join(fallback)}")
        return subprocess.run(fallback, check=False).returncode

    print(f"Open this vault in Obsidian: {resolved}")
    return 0
