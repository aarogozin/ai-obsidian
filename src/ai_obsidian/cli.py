from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .chat import run_builtin_chat, run_external_chat
from .chat_providers import external_engine_statuses
from .installer import (
    DEFAULT_VAULTS_ROOT,
    ask_yes_no,
    ask_choice,
    ask_path,
    ask_text,
    allowed_size_buckets_for_memory,
    choose_model,
    discover_model_dir_candidates,
    download_model_repo,
    model_dir_label,
    run_init,
    system_memory_gb,
)
from .model_catalog import load_model_choices, size_bucket_for_model
from .obsidian_plugin import (
    COMPANION_PLUGIN_ID,
    DEFAULT_PLUGIN_ID,
    FALLBACK_PLUGIN_ID,
    configure_plugin,
    enable_plugin,
    install_plugin,
    open_obsidian_vault,
    plugin_definition,
    plugin_status,
    print_plugin_status,
    print_plugin_verification,
    verify_plugin,
    verify_plugin_with_config,
)
from .omlx import OmlxClient, OmlxError, resolve_model_id
from .prerequisites import ensure_prerequisites, is_supported_macos
from .soul import create_soul, read_soul, soul_path, soul_status
from .voice import DEFAULT_STT_MODEL, VALID_LANGUAGES, transcribe_audio


DEFAULT_OMLX_BASE_URL = "http://localhost:8000/v1"
DEFAULT_OMLX_ADMIN_CHAT_URL = "http://localhost:8000/admin/chat"


@dataclass
class Vault:
    name: str
    path: str


@dataclass
class LocalModel:
    id: str
    path: Path
    size_bytes: int
    has_config: bool
    safetensor_count: int


@dataclass
class DownloadedModel:
    id: str
    source: str
    format: str
    path: Path
    size_bytes: int
    note: str = ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-obsidian",
        description="Install and operate a local Obsidian + oMLX AI workspace.",
    )
    parser.set_defaults(func=cmd_menu)
    subparsers = parser.add_subparsers(required=False)

    init = subparsers.add_parser("init", help="Interactive first-run configuration.")
    init.add_argument("--offline", action="store_true", help="Do not fetch model suggestions from Hugging Face.")
    init.set_defaults(func=cmd_init)

    doctor = subparsers.add_parser("doctor", help="Check local system readiness.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable health status.")
    doctor.set_defaults(func=cmd_doctor)

    repair = subparsers.add_parser("repair", help="Safely repair AI Obsidian runtime/plugin drift.")
    repair.add_argument("--vault", help="Registered vault name or filesystem path to repair.")
    repair.add_argument("--yes", action="store_true", help="Apply safe repairs without prompting.")
    repair.set_defaults(func=cmd_repair, interactive=True)

    install = subparsers.add_parser("install", help="Install or plan the local stack.")
    install.add_argument("--dry-run", action="store_true", help="Show actions without changing the system.")
    install.add_argument("--execute", action="store_true", help="Run supported install actions.")
    install.add_argument("--yes", action="store_true", help="Allow non-interactive bootstrap actions such as Homebrew install.")
    install.set_defaults(func=cmd_install)

    service = subparsers.add_parser("service", help="Manage the oMLX service.")
    service.add_argument("action", choices=["status", "start", "stop", "restart"])
    service.set_defaults(func=cmd_service)

    stack = subparsers.add_parser("stack", help="Start, stop, and inspect the full local AI Obsidian stack.")
    stack_sub = stack.add_subparsers(required=True)
    stack_start = stack_sub.add_parser("start", help="Download the selected model if needed and start oMLX.")
    stack_start.add_argument("--vault", help="Registered vault name or filesystem path for the printed chat command.")
    stack_start.set_defaults(func=cmd_stack_start)
    stack_status = stack_sub.add_parser("status", help="Show system, oMLX, model, and vault readiness.")
    stack_status.add_argument("--vault", help="Registered vault name or filesystem path to check.")
    stack_status.set_defaults(func=cmd_stack_status)
    stack_stop = stack_sub.add_parser("stop", help="Stop the oMLX Homebrew service without touching models or vaults.")
    stack_stop.set_defaults(func=cmd_stack_stop)

    vault = subparsers.add_parser("vault", help="Create, register, and list Obsidian vaults.")
    vault_sub = vault.add_subparsers(required=True)
    vault_add = vault_sub.add_parser("add", help="Register an existing vault.")
    vault_add.add_argument("path", nargs="?")
    vault_add.add_argument("--name")
    vault_add.set_defaults(func=cmd_vault_add)
    vault_create = vault_sub.add_parser("create", help="Create and register a new vault.")
    vault_create.add_argument("path", nargs="?")
    vault_create.add_argument("--name")
    vault_create.set_defaults(func=cmd_vault_create)
    vault_list = vault_sub.add_parser("list", help="List registered vaults.")
    vault_list.set_defaults(func=cmd_vault_list)

    soul = subparsers.add_parser("soul", help="Manage vault-level AI instructions.")
    soul_sub = soul.add_subparsers(required=True)
    for action in ("status", "init", "show"):
        soul_cmd = soul_sub.add_parser(action, help=f"{action.title()} vault soul.md instructions.")
        soul_cmd.add_argument("--vault", help="Registered vault name or filesystem path.")
        soul_cmd.set_defaults(func=cmd_soul, action=action)

    models = subparsers.add_parser("models", help="Inspect and manage model configuration.")
    models.add_argument("action", choices=["list", "status", "use", "download", "dirs", "local", "downloaded"])
    models.add_argument("model", nargs="?", help="Model repo/id for `models use` or `models download`.")
    models.set_defaults(func=cmd_models)

    plugin = subparsers.add_parser("plugin", help="Install, configure, and open the Obsidian AI plugin.")
    plugin_sub = plugin.add_subparsers(required=True)
    for action in ("status", "install", "configure", "verify", "open"):
        plugin_cmd = plugin_sub.add_parser(action, help=f"{action.title()} the Obsidian AI plugin.")
        plugin_cmd.add_argument("--vault", help="Registered vault name or filesystem path.")
        plugin_cmd.add_argument(
            "--plugin",
            choices=[DEFAULT_PLUGIN_ID, FALLBACK_PLUGIN_ID, COMPANION_PLUGIN_ID, "hub", "helper", "companion"],
            default=DEFAULT_PLUGIN_ID,
            help="Obsidian plugin integration to use.",
        )
        plugin_cmd.add_argument("--yes", action="store_true", help="Apply safe plugin configuration changes without prompting.")
        plugin_cmd.set_defaults(func=cmd_plugin, action=action)

    chat = subparsers.add_parser("chat", help="Start a local AI chat over an Obsidian vault.")
    chat.add_argument("--vault", help="Registered vault name or filesystem path.")
    chat.add_argument("--engine", choices=["builtin", "hermes", "claude", "opencode", "codex"])
    chat.add_argument("--model", help="Model name visible through oMLX /v1/models.")
    chat.add_argument("--base-url", help="OpenAI-compatible oMLX base URL.")
    chat.add_argument("--api-key", default=os.environ.get("OMLX_API_KEY"), help="oMLX API key. Defaults to OMLX_API_KEY.")
    chat.add_argument("--max-files", type=int, default=30, help="Maximum markdown files loaded into v1 context.")
    chat.add_argument("--once", help="Ask one question and exit instead of opening the interactive loop.")
    chat.set_defaults(func=cmd_chat)

    voice = subparsers.add_parser("voice", help="Record and transcribe voice input for notes.")
    voice_sub = voice.add_subparsers(required=True)
    transcribe = voice_sub.add_parser("transcribe", help="Transcribe an audio file with local MLX Whisper.")
    transcribe.add_argument("audio_file", help="Path to an audio file recorded by Obsidian or another app.")
    transcribe.add_argument(
        "--language",
        choices=sorted(VALID_LANGUAGES),
        default="auto",
        help="Language hint for speech recognition.",
    )
    transcribe.add_argument("--model", default=DEFAULT_STT_MODEL, help="MLX Whisper model id to use.")
    transcribe.set_defaults(func=cmd_voice_transcribe)

    return parser


def cmd_menu(_: argparse.Namespace) -> int:
    print("AI Obsidian")
    print("Choose what you want to do. Press Enter for the recommended next step.\n")
    options = [
        ("start", "Start / open AI Obsidian workspace"),
        ("init", "Init / repair setup"),
        ("vault", "Choose default vault"),
        ("model", "Choose default model"),
        ("stack", "Start/stop stack"),
        ("plugin", "Install/configure/open Obsidian AI plugin"),
        ("soul", "Manage vault soul.md instructions"),
        ("voice", "Transcribe a voice recording"),
        ("downloaded", "Show downloaded models"),
        ("doctor", "Doctor / troubleshoot"),
        ("chat", "CLI chat fallback"),
        ("quit", "Quit"),
    ]
    choice = ask_choice("Main menu", options, default=0)
    if choice == "start":
        status = cmd_stack_start(argparse.Namespace(vault=None, interactive=True))
        if status == 0:
            return cmd_plugin(argparse.Namespace(action="open", vault=None, plugin=DEFAULT_PLUGIN_ID, yes=False))
        return status
    if choice == "init":
        return cmd_init(argparse.Namespace(offline=False))
    if choice == "vault":
        return choose_default_vault()
    if choice == "model":
        return cmd_models(argparse.Namespace(action="use", model=None))
    if choice == "stack":
        stack_choice = ask_choice(
            "Stack",
            [("start", "Start"), ("status", "Status"), ("stop", "Stop")],
            default=0,
        )
        if stack_choice == "start":
            return cmd_stack_start(argparse.Namespace(vault=None, interactive=True))
        if stack_choice == "status":
            return cmd_stack_status(argparse.Namespace(vault=None))
        return cmd_stack_stop(argparse.Namespace())
    if choice == "plugin":
        plugin_choice = ask_choice(
            "Plugin",
            [("status", "Status"), ("install", "Install"), ("configure", "Configure"), ("open", "Open Obsidian vault")],
            default=0,
        )
        return cmd_plugin(argparse.Namespace(action=plugin_choice, vault=None, plugin=DEFAULT_PLUGIN_ID, yes=False))
    if choice == "soul":
        soul_choice = ask_choice(
            "Soul",
            [("status", "Status"), ("init", "Create if missing"), ("show", "Show")],
            default=0,
        )
        return cmd_soul(argparse.Namespace(action=soul_choice, vault=None))
    if choice == "voice":
        audio = ask_path("Audio file to transcribe", Path.home() / "Desktop" / "recording.webm")
        return cmd_voice_transcribe(argparse.Namespace(audio_file=str(audio), language="auto", model=DEFAULT_STT_MODEL))
    if choice == "downloaded":
        return cmd_models_local()
    if choice == "doctor":
        return cmd_doctor(argparse.Namespace())
    if choice == "chat":
        return cmd_chat(
            argparse.Namespace(
                vault=None,
                engine=None,
                model=None,
                base_url=None,
                api_key=None,
                max_files=30,
                once=None,
            )
        )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    health = collect_health()
    if getattr(args, "json", False):
        print(json.dumps(health, indent=2))
        return 0 if health.get("ok") else 1

    checks = [
        ("Architecture", platform.machine(), platform.machine() == "arm64"),
        ("macOS", platform.mac_ver()[0] or "unknown", is_supported_macos()),
        ("Homebrew", shutil.which("brew") or "missing", shutil.which("brew") is not None),
        ("Python", platform.python_version(), sys.version_info >= (3, 10)),
    ]

    for label, value, ok in checks:
        marker = "ok" if ok else "needs attention"
        print(f"{label}: {value} [{marker}]")

    print_omlx_status()
    print_external_engine_status(health)
    print_soul_health_summary(health)
    print_plugin_health_summary(health)
    return 0 if health.get("ok") else 1


def cmd_init(args: argparse.Namespace) -> int:
    status, config = run_init(load_remote_models=not args.offline)
    if config is None:
        return status

    saved = merge_config(load_config(), config)
    save_config(saved)
    print(f"Saved configuration: {config_path()}")

    default_vault = saved.get("default_vault")
    vault_path = resolve_vault(default_vault) if default_vault else None
    if vault_path and not soul_path(vault_path).exists():
        if ask_yes_no(f"Create vault instructions at {soul_path(vault_path)}?", default=True):
            create_soul(vault_path)
            print(f"Created vault instructions: {soul_path(vault_path)}")
    if vault_path and ask_yes_no("Install and configure Obsidian AI plugin now?", default=True):
        plugin_status = ensure_plugin_ready(vault_path, saved, plugin_id=DEFAULT_PLUGIN_ID, yes=False)
        if plugin_status != 0:
            print("Plugin setup did not complete. You can retry with `./ai-obsidian plugin install`.")
    if vault_path and ask_yes_no("Install push-to-talk companion plugin now?", default=True):
        companion_status = ensure_plugin_ready(vault_path, saved, plugin_id=COMPANION_PLUGIN_ID, yes=False)
        if companion_status != 0:
            print("Companion plugin setup did not complete. You can retry with `./ai-obsidian plugin install --plugin companion`.")

    if ask_yes_no("Start AI Obsidian stack now?", default=True):
        start_status = cmd_stack_start(argparse.Namespace(vault=default_vault, interactive=True))
        if start_status == 0 and vault_path and ask_yes_no("Open Obsidian now?", default=True):
            return open_obsidian_vault(vault_path)
        return start_status

    print_next_steps(saved, default_vault)
    return status


def cmd_install(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.execute:
        print("Use --dry-run to preview or --execute to run supported install actions.")
        return 2

    steps = [
        "Check Apple Silicon and macOS version.",
        "Install Homebrew if missing, or use the existing brew.",
        "Install Obsidian through Homebrew Cask.",
        "Tap and install oMLX from jundot/omlx.",
        "Install ffmpeg and mlx-whisper for local push-to-talk transcription.",
        "Configure oMLX model directory.",
        "Select an Apple Silicon MLX model.",
        "Create or register an Obsidian vault.",
        "Install and configure the Obsidian AI plugin and push-to-talk companion plugin.",
        "Save configuration.",
        "Run `stack start` to download the model if needed, start oMLX, and open Obsidian.",
    ]

    if args.dry_run:
        print("Install plan:")
        for index, step in enumerate(steps, start=1):
            print(f"{index}. {step}")
        return 0

    status = ensure_prerequisites(
        interactive=False,
        start_omlx_service=True,
        allow_homebrew_install=args.yes,
    )
    if status != 0:
        return status
    print("Base installation attempted. Run `ai-obsidian init` to configure models, vaults, and chat.")
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    brew = shutil.which("brew")
    if not brew:
        print("Homebrew is not installed or not on PATH.")
        return 1

    command = [brew, "services", args.action, "omlx"]
    if args.action == "status":
        command = [brew, "services", "info", "omlx"]

    sys.stdout.flush()
    result = subprocess.run(command, check=False)
    if result.returncode == 0 and args.action in {"start", "restart"}:
        print_service_next_steps()
    return result.returncode


def cmd_stack_start(args: argparse.Namespace) -> int:
    config = load_config()
    omlx = config.get("omlx", {})
    if not omlx:
        print("AI Obsidian is not configured yet. Run `./ai-obsidian init` first.")
        return 1

    selected = omlx.get("selected_model")
    if not selected:
        print("No model is configured yet. Run `./ai-obsidian init` or `./ai-obsidian models use <model-id>`.")
        return 1

    model_dir = Path(omlx.get("model_dir") or Path.home() / ".omlx" / "models")
    base_url = omlx.get("base_url", DEFAULT_OMLX_BASE_URL)
    api_key = omlx.get("api_key") or os.environ.get("OMLX_API_KEY")
    vault = args.vault or config.get("default_vault")
    if not vault and getattr(args, "interactive", True):
        selected_vault = choose_vault_from_config()
        vault = selected_vault[0] if selected_vault else None

    print("AI Obsidian stack start")
    print(f"Configured model: {selected}")
    print(f"Model directory: {model_dir}")

    client = OmlxClient(base_url=base_url, api_key=api_key)
    served_before_start, existing_api_error = list_models_if_reachable(client)
    if served_before_start is not None:
        print(f"oMLX API is already reachable at {base_url} [{len(served_before_start)} models].")

    if local_model_available(selected, model_dir):
        print("Selected model is already available locally. Skipping download.")
    elif served_before_start is not None and any(model_matches(selected, model) for model in served_before_start):
        print("Selected model is already available through oMLX. Skipping download.")
    elif is_huggingface_repo_id(selected):
        print("Selected model is not available locally or through oMLX. Downloading it now.")
        if download_model_repo(selected, model_dir) != 0:
            print("Model download failed. Re-run `./ai-obsidian stack start` after fixing the download issue.")
            return 1
    else:
        print("Configured model is not a Hugging Face repo id, so it cannot be downloaded automatically.")
        print("I will start oMLX and check whether the model is already served.")

    if served_before_start is not None:
        served_models = served_before_start
    else:
        if existing_api_error:
            print(f"oMLX API is not ready yet: {existing_api_error}")
        if start_configured_omlx(omlx, base_url) != 0:
            return 1

        served_models = wait_for_omlx_models(client, selected_model=selected)
        if served_models is None:
            print("oMLX did not become ready in time.")
            print("Try: ./ai-obsidian service status")
            return 1

    if not any(model_matches(selected, model) for model in served_models):
        print("oMLX is running, but the configured model is not visible through /v1/models.")
        print(f"Configured model: {selected}")
        print("Served models:")
        for model in served_models:
            print(f"- {model}")
        print("Run `./ai-obsidian models use <served-model-id>` with one of the ids above, or stop the other oMLX process and start the configured service.")
        return 1

    active_model = reconcile_configured_model(config, selected, served_models, interactive=getattr(args, "interactive", True))
    config.setdefault("omlx", {})["selected_model"] = active_model
    sync_status = sync_obsidian_plugins_after_stack_ready(config, vault)
    if sync_status != 0:
        return sync_status
    print_ready(config, vault)
    return 0


def cmd_stack_status(args: argparse.Namespace) -> int:
    print("AI Obsidian stack status")
    doctor_status = cmd_doctor(argparse.Namespace())

    print("\noMLX service:")
    service_status = cmd_service(argparse.Namespace(action="status"))

    print("\nModels:")
    model_status = cmd_models_status()

    print("\nVault:")
    vault_name = args.vault or load_config().get("default_vault")
    if vault_name:
        vault_path = resolve_vault(vault_name)
        if vault_path:
            print(f"{vault_name}: {vault_path} [ok]")
            vault_status = 0
        else:
            print(f"{vault_name}: not found [needs attention]")
            vault_status = 1
    else:
        print("No default vault configured. Run `./ai-obsidian init` or `./ai-obsidian vault add <path>`.")
        vault_status = 1

    plugin_status_code = 0
    if vault_status == 0:
        plugin_status_code = cmd_repair(argparse.Namespace(vault=vault_name, yes=False, interactive=False))

    ready = doctor_status == service_status == model_status == vault_status == plugin_status_code == 0
    if ready:
        print_ready(load_config(), vault_name)
    return 0 if ready else 1


def cmd_stack_stop(_: argparse.Namespace) -> int:
    print("Stopping oMLX Homebrew service. Models, vaults, and config are left untouched.")
    return cmd_service(argparse.Namespace(action="stop"))


def model_available(selected: str, model_dir: Path, client: OmlxClient) -> bool:
    if local_model_available(selected, model_dir):
        return True
    try:
        served_models = client.list_models()
    except OmlxError:
        return False
    return any(model_matches(selected, model) for model in served_models)


def list_models_if_reachable(client: OmlxClient) -> tuple[list[str] | None, OmlxError | None]:
    try:
        return client.list_models(), None
    except OmlxError as exc:
        return None, exc


def local_model_available(selected: str, model_dir: Path) -> bool:
    return any(model_matches(selected, model.id) for model in discover_local_models(model_dir))


def is_huggingface_repo_id(model_id: str) -> bool:
    return "/" in model_id and not model_id.startswith("/")


def start_configured_omlx(omlx: dict[str, Any], base_url: str = DEFAULT_OMLX_BASE_URL) -> int:
    mode = omlx.get("mode", "service")
    if mode != "service":
        print(f"oMLX mode is `{mode}`. Start oMLX yourself, then run `./ai-obsidian stack status`.")
        print(f"Manual command: omlx serve --model-dir {omlx.get('model_dir') or Path.home() / '.omlx' / 'models'}")
        return 0

    listener = listener_for_base_url(base_url)
    if listener:
        print(f"Cannot start oMLX Homebrew service because {base_url} is already in use.")
        print(f"Listener: {listener}")
        print("Quit that oMLX process/app, or configure AI Obsidian to use the already-running oMLX mode.")
        return 1

    print("Starting oMLX Homebrew service.")
    return cmd_service(argparse.Namespace(action="start"))


def listener_for_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[1] if len(lines) > 1 else None


def wait_for_omlx_models(
    client: OmlxClient,
    selected_model: str | None = None,
    timeout_seconds: int = 60,
    interval_seconds: float = 2.0,
) -> list[str] | None:
    print("Waiting for oMLX /v1/models ...")
    deadline = time.monotonic() + timeout_seconds
    last_error: OmlxError | None = None
    while time.monotonic() < deadline:
        try:
            models = client.list_models()
            if not selected_model or any(model_matches(selected_model, model) for model in models):
                print(f"oMLX API is reachable at {client.base_url} [{len(models)} models].")
                return models
            print(f"oMLX is reachable, waiting for selected model to appear: {selected_model}")
        except OmlxError as exc:
            last_error = exc
        time.sleep(interval_seconds)

    if last_error:
        print(last_error)
    return None


def reconcile_configured_model(
    config: dict[str, Any],
    selected: str,
    served_models: list[str],
    *,
    interactive: bool,
) -> str:
    resolved = resolve_model_id(selected, served_models)
    if not resolved or resolved == selected:
        return selected

    print(f"oMLX exposes the selected model as: {resolved}")
    if not interactive or ask_yes_no("Save this served model id in the config?", default=True):
        config.setdefault("omlx", {})["selected_model"] = resolved
        save_config(config)
        print(f"Configured default model: {resolved}")
        return resolved
    print(f"Tip: run `./ai-obsidian models use {resolved}` if chat cannot resolve the repo id.")
    return selected


def print_ready(config: dict[str, Any], vault: str | None) -> None:
    print("\nYou are ready.")
    print(f"oMLX API: {config.get('omlx', {}).get('base_url', DEFAULT_OMLX_BASE_URL)}")
    print(f"oMLX browser chat: {DEFAULT_OMLX_ADMIN_CHAT_URL} (diagnostic)")
    print_next_steps(config, vault)


def print_service_next_steps() -> None:
    config = load_config()
    omlx = config.get("omlx", {})
    if not omlx:
        print("oMLX service command completed. Run `./ai-obsidian init` to configure AI Obsidian.")
        return

    client = OmlxClient(
        base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    selected = omlx.get("selected_model")
    served_models, error = list_models_if_reachable(client)
    if served_models is not None and selected and any(model_matches(selected, model) for model in served_models):
        active_model = resolve_model_id(selected, served_models) or selected
        config.setdefault("omlx", {})["selected_model"] = active_model
        sync_obsidian_plugins_after_stack_ready(config, config.get("default_vault"))
        print_ready(config, config.get("default_vault"))
        return

    print("oMLX service command completed.")
    if error:
        print(f"oMLX API is not ready yet: {error}")
    elif selected:
        print(f"Configured model is not visible yet: {selected}")
    print("Run `./ai-obsidian stack start` for model/download checks and final chat links.")


def print_next_steps(config: dict[str, Any], vault: str | None) -> None:
    vault_name = vault or config.get("default_vault") or "<vault>"
    vault_path = resolve_vault(vault_name) if vault_name != "<vault>" else None
    print("Next steps:")
    if vault_path:
        print(f"  Open Obsidian vault: {vault_path}")
        print("  Open AI chat: use the Obsidian ribbon or Command Palette for Local LLM Hub / Local LLM Helper.")
        print("  Voice input: use the AI Obsidian microphone ribbon icon or Command Palette -> AI Obsidian: Push to Talk.")
        print("  Plugin setup: ./ai-obsidian plugin status")
    print(f"  CLI fallback: ./ai-obsidian chat --vault {vault_name}")


def sync_obsidian_plugins_after_stack_ready(config: dict[str, Any], vault: str | None) -> int:
    if not vault:
        return 0
    vault_path = resolve_vault(vault)
    if not vault_path:
        print(f"Could not sync Obsidian plugin settings because the vault was not found: {vault}")
        return 1

    print("Syncing Obsidian AI plugin settings with the active oMLX model.")
    status = ensure_plugin_ready(vault_path, config, plugin_id=DEFAULT_PLUGIN_ID, yes=True)
    if status != 0:
        print("Obsidian AI plugin sync failed. Re-run `./ai-obsidian plugin configure --yes` after fixing the issue.")
        return status

    companion_status = plugin_status(vault_path, COMPANION_PLUGIN_ID)
    if companion_status.installed:
        companion_config_status = ensure_plugin_ready(vault_path, config, plugin_id=COMPANION_PLUGIN_ID, yes=True)
        if companion_config_status != 0:
            return companion_config_status
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    config = load_config()
    vault_name = args.vault or config.get("default_vault")
    if not vault_name:
        print("No default vault configured. Run `./ai-obsidian init` first.")
        return 1

    repaired_config = repair_served_model_id(config)
    vault_path = resolve_vault(vault_name)
    if not vault_path:
        print(f"Cannot repair plugins because the vault was not found: {vault_name}")
        return 1

    soul = soul_status(vault_path)
    if not soul.exists:
        should_create = bool(getattr(args, "yes", False))
        if not should_create and getattr(args, "interactive", False):
            should_create = ask_yes_no(f"Create vault instructions at {soul.path}?", default=True)
        if should_create:
            create_soul(vault_path)
            print(f"Created vault instructions: {soul.path}")
        else:
            print(f"Vault instructions are missing: {soul.path}")

    status = sync_obsidian_plugins_after_stack_ready(repaired_config, vault_name)
    if status == 0:
        print("AI Obsidian repair complete.")
    return status


def repair_served_model_id(config: dict[str, Any]) -> dict[str, Any]:
    omlx = config.get("omlx", {})
    selected = omlx.get("selected_model")
    if not selected:
        return config
    client = OmlxClient(
        base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    served_models, _ = list_models_if_reachable(client)
    if not served_models:
        return config
    resolved = resolve_model_id(selected, served_models)
    if resolved and resolved != selected:
        config.setdefault("omlx", {})["selected_model"] = resolved
        save_config(config)
        print(f"Repaired configured model id: {selected} -> {resolved}")
    return config


def collect_health() -> dict[str, Any]:
    config = load_config()
    health: dict[str, Any] = {
        "system": {
            "architecture": platform.machine(),
            "architecture_ok": platform.machine() == "arm64",
            "macos": platform.mac_ver()[0] or "unknown",
            "macos_ok": is_supported_macos(),
            "homebrew": shutil.which("brew"),
            "python": platform.python_version(),
            "python_ok": sys.version_info >= (3, 10),
        },
        "omlx": {},
        "vault": {},
        "soul": {},
        "plugins": {},
        "external_engines": external_engine_statuses(),
    }

    omlx = config.get("omlx", {})
    client = OmlxClient(
        base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    served_models, error = list_models_if_reachable(client)
    selected = omlx.get("selected_model")
    health["omlx"] = {
        "base_url": omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
        "configured_model": selected,
        "reachable": served_models is not None,
        "served_models": served_models or [],
        "error": str(error) if error else None,
        "model_visible": bool(served_models and selected and any(model_matches(selected, model) for model in served_models)),
    }

    vault_name = config.get("default_vault")
    vault_path = resolve_vault(vault_name) if vault_name else None
    health["vault"] = {
        "default": vault_name,
        "path": str(vault_path) if vault_path else None,
        "ok": vault_path is not None,
    }

    if vault_path:
        current_soul = soul_status(vault_path)
        health["soul"] = {
            "path": str(current_soul.path),
            "exists": current_soul.exists,
            "readable": current_soul.readable,
            "ok": current_soul.exists and current_soul.readable,
            "detail": current_soul.detail,
        }
    else:
        health["soul"] = {
            "path": None,
            "exists": False,
            "readable": False,
            "ok": False,
            "detail": "no vault",
        }

    if vault_path:
        for plugin_id in (DEFAULT_PLUGIN_ID, COMPANION_PLUGIN_ID):
            verification = verify_plugin_with_config(vault_path, plugin_id, config=config)
            health["plugins"][plugin_id] = {
                "ok": verification.ok,
                "checks": [
                    {"name": label, "ok": ok, "detail": detail}
                    for label, ok, detail in verification.checks
                ],
            }

    health["ok"] = (
        health["system"]["architecture_ok"]
        and health["system"]["macos_ok"]
        and bool(health["system"]["homebrew"])
        and health["system"]["python_ok"]
        and health["omlx"].get("reachable")
        and health["omlx"].get("model_visible")
        and health["vault"].get("ok")
        and health["soul"].get("ok")
        and all(plugin.get("ok") for plugin in health["plugins"].values())
    )
    return health


def print_plugin_health_summary(health: dict[str, Any]) -> None:
    if not health.get("plugins"):
        return
    print("Obsidian plugins:")
    for plugin_id, plugin in health["plugins"].items():
        marker = "ok" if plugin.get("ok") else "needs attention"
        print(f"- {plugin_id}: {marker}")
        for check in plugin.get("checks", []):
            if not check.get("ok"):
                print(f"  - {check['name']}: {check.get('detail')}")


def print_external_engine_status(health: dict[str, Any]) -> None:
    engines = health.get("external_engines", {})
    if not engines:
        return
    print("External chat engines:")
    for engine, status in engines.items():
        marker = "available" if status.get("available") else "not configured"
        detail = status.get("executable") or status.get("detail")
        print(f"- {engine}: {detail} [{marker}]")


def print_soul_health_summary(health: dict[str, Any]) -> None:
    soul = health.get("soul", {})
    if not soul:
        return
    marker = "ok" if soul.get("ok") else "needs attention"
    detail = soul.get("detail") or "unknown"
    path = soul.get("path") or "missing"
    print(f"Vault soul: {path} [{marker}: {detail}]")


def cmd_vault_add(args: argparse.Namespace) -> int:
    if not args.path:
        args.path = str(ask_path("Existing Obsidian vault path", DEFAULT_VAULTS_ROOT / "Main"))
    path = Path(args.path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        print(f"Vault path does not exist or is not a directory: {path}")
        return 1

    name = args.name or path.name
    config = load_config()
    config.setdefault("vaults", {})[name] = asdict(Vault(name=name, path=str(path)))
    save_config(config)
    print(f"Registered vault {name}: {path}")
    return 0


def cmd_vault_create(args: argparse.Namespace) -> int:
    if not args.path:
        args.path = str(ask_path("New Obsidian vault path", DEFAULT_VAULTS_ROOT / "Main"))
    path = Path(args.path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / ".obsidian").mkdir(exist_ok=True)
    args.path = str(path)
    return cmd_vault_add(args)


def cmd_vault_list(_: argparse.Namespace) -> int:
    config = load_config()
    vaults = config.get("vaults", {})
    if not vaults:
        print("No vaults registered yet.")
        return 0

    for name, vault in vaults.items():
        print(f"{name}: {vault['path']}")
    return 0


def cmd_soul(args: argparse.Namespace) -> int:
    vault_path = resolve_plugin_vault(args.vault)
    if not vault_path:
        print("No Obsidian vault is configured. Run `./ai-obsidian init` or `./ai-obsidian vault add`.")
        return 1

    if args.action == "status":
        status = soul_status(vault_path)
        marker = "ok" if status.exists and status.readable else "needs attention"
        print(f"Vault: {vault_path}")
        print(f"Soul file: {status.path}")
        print(f"Status: {status.detail} [{marker}]")
        return 0 if status.exists and status.readable else 1

    if args.action == "init":
        created = create_soul(vault_path)
        if created:
            print(f"Created vault instructions: {soul_path(vault_path)}")
        else:
            print(f"Vault instructions already exist: {soul_path(vault_path)}")
        return 0

    if args.action == "show":
        text = read_soul(vault_path)
        if not text:
            print(f"Vault instructions are missing or unreadable: {soul_path(vault_path)}")
            return 1
        print(text)
        return 0

    return 2


def cmd_plugin(args: argparse.Namespace) -> int:
    vault_path = resolve_plugin_vault(args.vault)
    if not vault_path:
        print("No Obsidian vault is configured. Run `./ai-obsidian init` or `./ai-obsidian vault add`.")
        return 1

    if args.action == "status":
        print_plugin_status(plugin_status(vault_path, args.plugin))
        return 0
    if args.action == "install":
        return install_plugin(vault_path, args.plugin, enable=True)
    if args.action == "configure":
        config = load_config()
        return configure_plugin(vault_path, config, args.plugin, ask_yes_no=ask_yes_no, yes=args.yes)
    if args.action == "verify":
        verification = verify_plugin_with_config(vault_path, args.plugin, config=load_config())
        print_plugin_verification(verification)
        if not verification.ok and args.plugin in {"companion", COMPANION_PLUGIN_ID}:
            print("Run `./ai-obsidian install --execute` to install missing voice dependencies.")
        elif not verification.ok:
            print("Run `./ai-obsidian repair` to safely sync Obsidian plugin settings.")
        return 0 if verification.ok else 1
    if args.action == "open":
        status = plugin_status(vault_path, args.plugin)
        if not status.installed:
            print(f"{status.definition.name} is not installed yet. Run `./ai-obsidian plugin install`.")
        else:
            print(status.definition.command_hint)
        return open_obsidian_vault(vault_path)
    return 2


def ensure_plugin_ready(vault_path: Path, config: dict[str, Any], *, plugin_id: str, yes: bool = False) -> int:
    try:
        definition = plugin_definition(plugin_id)
    except ValueError as exc:
        print(exc)
        return 1

    status = plugin_status(vault_path, definition.id)
    if not status.installed:
        install_status = install_plugin(vault_path, definition.id, enable=True)
        if install_status != 0:
            if definition.id == DEFAULT_PLUGIN_ID:
                print("Trying fallback plugin: Local LLM Helper.")
                return ensure_plugin_ready(vault_path, config, plugin_id=FALLBACK_PLUGIN_ID, yes=yes)
            return install_status
    else:
        print(f"{definition.name} is already installed.")
        if not status.enabled:
            enable_plugin(vault_path, definition.id)

    configure_status = configure_plugin(vault_path, config, definition.id, ask_yes_no=ask_yes_no, yes=yes)
    if configure_status != 0:
        return configure_status
    if definition.id == COMPANION_PLUGIN_ID:
        verification = verify_plugin(vault_path, definition.id)
        print_plugin_verification(verification)
        if not verification.ok:
            print("Companion plugin is installed, but voice is not fully ready yet.")
            print("Run `./ai-obsidian install --execute` to install missing voice dependencies.")
            return 1
    return 0


def choose_default_vault() -> int:
    selected = choose_vault_from_config()
    if not selected:
        print("No registered vaults found. Use `./ai-obsidian vault add` or `./ai-obsidian vault create`.")
        return 1
    name, path = selected
    config = load_config()
    config["default_vault"] = name
    save_config(config)
    print(f"Default vault: {name} ({path})")
    return 0


def choose_vault_from_config() -> tuple[str, Path] | None:
    config = load_config()
    vaults = config.get("vaults", {})
    valid: list[tuple[str, Path]] = []
    for name, vault in vaults.items():
        path = Path(vault.get("path", "")).expanduser()
        if path.exists() and path.is_dir():
            valid.append((name, path.resolve()))
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    default_name = config.get("default_vault")
    default_index = next((index for index, (name, _) in enumerate(valid) if name == default_name), 0)
    options = [(name, f"{name}: {path}") for name, path in valid]
    selected = ask_choice("Choose Obsidian vault", options, default=default_index)
    return next(item for item in valid if item[0] == selected)


def resolve_plugin_vault(name_or_path: str | None) -> Path | None:
    if name_or_path:
        return resolve_vault(name_or_path)
    selected = choose_vault_from_config()
    if selected:
        return selected[1]
    return None


def choose_model_id_interactively() -> str | None:
    config = load_config()
    omlx = config.get("omlx", {})
    options: list[tuple[str, str]] = []
    seen: set[str] = set()

    for model in discover_downloaded_models():
        if model.format != "MLX":
            continue
        if model.id in seen:
            continue
        seen.add(model.id)
        options.append(
            (
                model.id,
                f"{model.id} | {model.format} | {format_bytes(model.size_bytes)} | {model.source}",
            )
        )

    if omlx:
        try:
            served = OmlxClient(
                base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
                api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
            ).list_models()
        except OmlxError:
            served = []
        for model in served:
            if model in seen:
                continue
            seen.add(model)
            options.append((model, f"{model} | served by oMLX"))

    if not options:
        print("No downloaded MLX models or served oMLX models found.")
        if ask_yes_no("Choose and download a remote model now?", default=True):
            status = cmd_models_download_interactive()
            if status == 0:
                return load_config().get("omlx", {}).get("selected_model")
        return None

    selected = ask_choice("Choose default model", options, default=0)
    return selected


def cmd_models_download_interactive() -> int:
    config = load_config()
    model_dir = Path(config.get("omlx", {}).get("model_dir") or Path.home() / ".omlx" / "models")
    try:
        selected = choose_model(load_remote_models=True, current_model_dir=model_dir)
    except RuntimeError as exc:
        print(exc)
        return 1

    config.setdefault("omlx", {})["selected_model"] = selected.repo_id
    if selected.model_dir is not None:
        model_dir = selected.model_dir
        config.setdefault("omlx", {})["model_dir"] = str(model_dir)
    save_config(config)

    if selected.downloaded:
        print(f"Selected already downloaded model: {selected.repo_id}")
        return 0
    return download_model_repo(selected.repo_id, model_dir)


def cmd_models(args: argparse.Namespace) -> int:
    if args.action == "status":
        return cmd_models_status()
    if args.action == "dirs":
        return cmd_models_dirs()
    if args.action in {"local", "downloaded"}:
        return cmd_models_local()
    if args.action == "use":
        if not args.model:
            model = choose_model_id_interactively()
            if not model:
                return 1
            args.model = model
        return cmd_models_use(args.model)
    if args.action == "download":
        if not args.model:
            return cmd_models_download_interactive()
        config = load_config()
        model_dir = Path(config.get("omlx", {}).get("model_dir") or Path.home() / ".omlx" / "models")
        return download_model_repo(args.model, model_dir)
    if args.action != "list":
        return 2

    choices, source = load_model_choices(load_remote_models=True)
    ram_gb = system_memory_gb()
    allowed_buckets = allowed_size_buckets_for_memory(ram_gb)
    choices = [choice for choice in choices if size_bucket_for_model(choice) in allowed_buckets]
    ram_text = f"about {ram_gb} GB RAM" if ram_gb else "unknown RAM"
    print(f"Apple Silicon MLX model suggestions ({source}, filtered for {ram_text}):")
    for choice in choices[:10]:
        print(f"- {choice.repo_id} ({choice.min_ram_gb} GB+) - {choice.note}")
    return 0


def cmd_voice_transcribe(args: argparse.Namespace) -> int:
    try:
        result = transcribe_audio(Path(args.audio_file), language=args.language, model=args.model)
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.text)
    return 0


def cmd_models_dirs() -> int:
    print("Known model directories:")
    for candidate in discover_model_dir_candidates():
        print(f"- {model_dir_label(candidate)}")
    return 0


def cmd_models_local() -> int:
    models = discover_downloaded_models()
    if not models:
        print("No downloaded models found in known provider directories.")
        print("Run `./ai-obsidian models dirs` to inspect searched locations.")
        return 0

    print("Downloaded/local models:")
    for model in sorted(models, key=lambda item: (item.source, item.id)):
        suffix = f" | {model.note}" if model.note else ""
        print(
            f"- {model.id} | {model.format} | {format_bytes(model.size_bytes)} | "
            f"{model.source} | {model.path}{suffix}"
        )
    return 0


def discover_downloaded_models() -> list[DownloadedModel]:
    config = load_config()
    configured_dir = config.get("omlx", {}).get("model_dir")
    candidates = discover_model_dir_candidates()
    if configured_dir:
        configured_path = Path(configured_dir).expanduser()
        if not any(candidate.path and candidate.path.expanduser() == configured_path for candidate in candidates):
            candidates.append(
                type("ConfiguredCandidate", (), {
                    "label": "Configured oMLX model dir",
                    "path": configured_path,
                })()
            )

    results: list[DownloadedModel] = []
    seen_paths: set[Path] = set()
    seen_ollama: set[str] = set()
    for candidate in candidates:
        path = getattr(candidate, "path", None)
        if path is None:
            continue
        source = getattr(candidate, "label", path.name)
        root = Path(path).expanduser()
        for model in discover_local_models(root):
            resolved = model.path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            results.append(
                DownloadedModel(
                    id=model.id,
                    source=source,
                    format="MLX",
                    path=model.path,
                    size_bytes=model.size_bytes,
                    note=f"{model.safetensor_count} safetensors",
                )
            )
        for gguf in discover_gguf_models(root):
            resolved = gguf.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            results.append(
                DownloadedModel(
                    id=gguf.stem,
                    source=source,
                    format="GGUF",
                    path=gguf,
                    size_bytes=safe_file_size(gguf),
                    note="not directly served by oMLX MLX mode",
                )
            )
        for ollama in discover_ollama_manifest_models(root):
            if ollama.id in seen_ollama:
                continue
            seen_ollama.add(ollama.id)
            results.append(ollama)
    return results


def cmd_models_status() -> int:
    config = load_config()
    omlx = config.get("omlx", {})
    model_dir = Path(omlx.get("model_dir") or Path.home() / ".omlx" / "models")
    selected = omlx.get("selected_model")
    base_url = omlx.get("base_url", DEFAULT_OMLX_BASE_URL)
    api_key = omlx.get("api_key") or os.environ.get("OMLX_API_KEY")

    print(f"Model directory: {model_dir}")
    print(f"Configured model: {selected or '(not set)'}")

    local_models = discover_local_models(model_dir)
    if local_models:
        print("Local model directories:")
        for model in local_models:
            markers = []
            if model_matches(selected, model.id):
                markers.append("configured")
            status = f" [{', '.join(markers)}]" if markers else ""
            print(
                f"- {model.id}{status} | {format_bytes(model.size_bytes)} | "
                f"{model.safetensor_count} safetensors | {model.path}"
            )
    else:
        print("Local model directories: none found")

    client = OmlxClient(base_url=base_url, api_key=api_key)
    try:
        served_models = client.list_models()
    except OmlxError as exc:
        print(f"oMLX API: {exc}")
        return 1

    print("oMLX /v1/models:")
    for model in served_models:
        local_match = next((local for local in local_models if model_matches(model, local.id)), None)
        markers = []
        if model_matches(selected, model):
            markers.append("configured")
        if local_match:
            markers.append("local")
        status = f" [{', '.join(markers)}]" if markers else ""
        print(f"- {model}{status}")

    local_ids = [model.id for model in local_models]
    selected_is_local = selected and any(model_matches(selected, model) for model in local_ids)
    selected_is_served = selected and any(model_matches(selected, model) for model in served_models)
    if selected and not selected_is_local and not selected_is_served:
        print("Warning: configured model does not match any local or served model.")
        print("Run `./ai-obsidian models use <model-id>` with one of the local/oMLX ids above.")
        return 1
    if selected and selected_is_local and not selected_is_served:
        print("Warning: configured model is local, but the active oMLX server does not expose it.")
        print("Run `./ai-obsidian models use <served-model-id>` with one of the oMLX ids above, or stop the other oMLX process and start the configured service.")
        return 1
    return 0


def cmd_models_use(model: str) -> int:
    config = load_config()
    omlx = config.get("omlx", {})
    model_dir = Path(omlx.get("model_dir") or Path.home() / ".omlx" / "models")
    local_models = discover_local_models(model_dir)
    served_models: list[str] = []
    if omlx:
        try:
            served_models = OmlxClient(
                base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
                api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
            ).list_models()
        except OmlxError:
            served_models = []

    known_ids = [local.id for local in local_models] + served_models
    if known_ids and not any(model_matches(model, known) for known in known_ids):
        print("Warning: this model does not match any local model directory or oMLX /v1/models id.")

    config.setdefault("omlx", {})["selected_model"] = model
    save_config(config)
    print(f"Configured default model: {model}")
    return 0


def discover_local_models(model_dir: Path) -> list[LocalModel]:
    if not model_dir.exists():
        return []

    models: list[LocalModel] = []
    for path in sorted(model_dir.iterdir()):
        if not path.is_dir():
            continue
        if has_model_files(path):
            models.append(build_local_model(path.name, path))
            continue
        for child in sorted(path.iterdir()):
            if child.is_dir() and has_model_files(child):
                models.append(build_local_model(f"{path.name}/{child.name}", child))
    return models


def discover_gguf_models(model_dir: Path) -> list[Path]:
    if not model_dir.exists():
        return []
    try:
        return sorted(path for path in model_dir.rglob("*.gguf") if path.is_file())
    except OSError:
        return []


def discover_ollama_manifest_models(model_dir: Path) -> list[DownloadedModel]:
    manifests = model_dir / "manifests"
    if not manifests.exists():
        return []

    models: list[DownloadedModel] = []
    try:
        manifest_files = [path for path in manifests.rglob("*") if path.is_file()]
    except OSError:
        return []

    for manifest in sorted(manifest_files):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        model_id = ollama_model_id_from_manifest(manifests, manifest)
        if not model_id:
            continue
        size = ollama_manifest_model_size(payload)
        models.append(
            DownloadedModel(
                id=model_id,
                source="Ollama",
                format="Ollama manifest",
                path=manifest,
                size_bytes=size,
                note="may require Ollama or conversion for oMLX",
            )
        )
    return models


def ollama_model_id_from_manifest(root: Path, manifest: Path) -> str | None:
    try:
        relative = manifest.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 3:
        return None
    registry = parts[0]
    namespace = parts[1]
    name = "/".join(parts[2:-1])
    tag = parts[-1]
    if namespace == "library":
        return f"{name}:{tag}"
    return f"{registry}/{namespace}/{name}:{tag}"


def ollama_manifest_model_size(payload: dict[str, Any]) -> int:
    total = 0
    for layer in payload.get("layers", []):
        media_type = str(layer.get("mediaType", ""))
        if ".model" not in media_type and ".tensor" not in media_type:
            continue
        try:
            total += int(layer.get("size") or 0)
        except (TypeError, ValueError):
            continue
    return total


def build_local_model(model_id: str, path: Path) -> LocalModel:
    safetensors = list(path.glob("*.safetensors"))
    return LocalModel(
        id=model_id,
        path=path,
        size_bytes=directory_size(path),
        has_config=(path / "config.json").exists(),
        safetensor_count=len(safetensors),
    )


def has_model_files(path: Path) -> bool:
    return (
        (path / "config.json").exists()
        and (any(path.glob("*.safetensors")) or any(path.glob("model-*.safetensors")))
    )


def directory_size(path: Path) -> int:
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def model_matches(configured: str | None, actual: str) -> bool:
    if not configured:
        return False
    configured_tail = configured.rsplit("/", maxsplit=1)[-1]
    actual_tail = actual.rsplit("/", maxsplit=1)[-1]
    return configured == actual or configured_tail == actual_tail


def cmd_chat(args: argparse.Namespace) -> int:
    vault_name = args.vault or load_config().get("default_vault")
    if not vault_name:
        selected = choose_vault_from_config()
        vault_name = selected[0] if selected else None
    if not vault_name:
        print("No vault configured. Run `./ai-obsidian vault add` or `./ai-obsidian init`.")
        return 1

    vault_path = resolve_vault(vault_name)
    if not vault_path:
        print(f"Unknown vault: {vault_name}")
        return 1

    config = load_config()
    engine = args.engine or config.get("chat", {}).get("default_engine", "builtin")
    model = args.model or config.get("omlx", {}).get("selected_model")
    base_url = args.base_url or config.get("omlx", {}).get("base_url", DEFAULT_OMLX_BASE_URL)
    api_key = args.api_key or config.get("omlx", {}).get("api_key") or os.environ.get("OMLX_API_KEY")

    if engine in {"hermes", "claude"}:
        return run_external_chat(
            vault_path=vault_path,
            engine=engine,
            max_files=args.max_files,
            once=args.once,
        )

    if engine != "builtin":
        print(f"Engine adapter is not implemented yet: {engine}")
        print("Use --engine builtin, --engine hermes, or --engine claude.")
        return 2

    client = OmlxClient(base_url=base_url, api_key=api_key)
    return run_builtin_chat(
        vault_path=vault_path,
        client=client,
        model=model,
        max_files=args.max_files,
        once=args.once,
    )


def print_omlx_status() -> None:
    config = load_config()
    omlx = config.get("omlx", {})
    client = OmlxClient(
        base_url=omlx.get("base_url", DEFAULT_OMLX_BASE_URL),
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    try:
        model_count = len(client.list_models())
        print(f"oMLX API: reachable at {DEFAULT_OMLX_BASE_URL} [{model_count} models]")
    except OmlxError as exc:
        print(f"oMLX API: {exc} [needs attention]")


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    ensure_config_permissions(path)
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def config_path() -> Path:
    return Path.home() / ".ai-obsidian" / "config.json"


def ensure_config_permissions(path: Path) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode != 0o600:
        path.chmod(0o600)


def merge_config(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_vault(name_or_path: str) -> Path | None:
    config = load_config()
    vault = config.get("vaults", {}).get(name_or_path)
    if vault:
        path = Path(vault["path"])
        return path if path.exists() else None

    candidate = Path(name_or_path).expanduser()
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()

    return None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    raise SystemExit(main())
