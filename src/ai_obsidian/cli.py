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
from .docker_runtime import (
    DEFAULT_DMR_BASE_URL,
    RUNTIME_DOCKER_MODEL_RUNNER,
    RUNTIME_NATIVE_OMLX,
    docker_model_runner_suggestion_for_model,
    docker_model_list,
    docker_model_pull,
    docker_model_run_detached,
    docker_status,
    ensure_docker_model_runner,
    is_docker_model_id,
    is_docker_runtime,
    is_native_mlx_repo_id,
    runtime_mode,
)
from .installer import (
    DEFAULT_VAULTS_ROOT,
    DEFAULT_MODEL_DIR,
    MODEL_SEARCHES_BY_FAMILY,
    DownloadedMlxModel,
    ask_yes_no,
    ask_choice,
    ask_path,
    ask_text,
    allowed_size_buckets_for_memory,
    choose_model,
    discover_downloaded_mlx_models,
    discover_model_dir_candidates,
    model_dir_stats,
    download_model_repo,
    model_dir_label,
    run_init,
    system_memory_gb,
)
from .model_catalog import ModelChoice, load_model_choices, model_version, size_bucket_for_model
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
from .prerequisites import check_prerequisites, ensure_hermes_cli_installed, ensure_prerequisites, is_supported_macos
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


DOCKER_MODEL_SUGGESTIONS = [
    {
        "repo_id": "ai/smollm2",
        "label": "SmolLM2",
        "min_ram_gb": 8,
        "family": "smollm",
        "version": "2",
        "size_bucket": "small",
        "note": "Fast default for Docker Model Runner smoke tests and light notes.",
    },
]


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

    setup = subparsers.add_parser("setup", help="Machine-readable setup API for the macOS installer UI.")
    setup_sub = setup.add_subparsers(required=True)
    setup_status = setup_sub.add_parser("status", help="Print setup readiness and current configuration.")
    setup_status.add_argument("--json", action="store_true", help="Print machine-readable setup status.")
    setup_status.set_defaults(func=cmd_setup_status)
    setup_models = setup_sub.add_parser("models", help="Print local and safe remote model choices.")
    setup_models.add_argument("--json", action="store_true", help="Print machine-readable model choices.")
    setup_models.add_argument("--offline", action="store_true", help="Use bundled fallback models only.")
    setup_models.add_argument("--family", choices=["qwen", "gemma", "llama", "mistral", "granite", "other", "local"])
    setup_models.add_argument("--version", help="Filter remote suggestions by parsed model version.")
    setup_models.add_argument("--size", choices=["small", "balanced", "large"], help="Filter remote suggestions by size bucket.")
    setup_models.add_argument("--model-dir", help="Additional model directory to inspect first.")
    setup_models.add_argument(
        "--runtime",
        choices=[RUNTIME_NATIVE_OMLX, RUNTIME_DOCKER_MODEL_RUNNER],
        default=RUNTIME_NATIVE_OMLX,
        help="Return model choices for the selected runtime.",
    )
    setup_models.set_defaults(func=cmd_setup_models)
    setup_apply = setup_sub.add_parser("apply", help="Apply a setup profile produced by the macOS installer UI.")
    setup_apply.add_argument("--profile", required=True, help="Path to a JSON setup profile.")
    setup_apply.add_argument("--yes", action="store_true", help="Apply safe setup changes without prompting.")
    setup_apply.add_argument("--dry-run", action="store_true", help="Validate and print the planned setup without changing files.")
    setup_apply.set_defaults(func=cmd_setup_apply)

    install = subparsers.add_parser("install", help="Install or plan the local stack.")
    install.add_argument("--dry-run", action="store_true", help="Show actions without changing the system.")
    install.add_argument("--execute", action="store_true", help="Run supported install actions.")
    install.add_argument("--yes", action="store_true", help="Allow non-interactive bootstrap actions such as Homebrew install.")
    install.add_argument("--with-hermes", action="store_true", help="Also install the optional Hermes Agent CLI.")
    install.add_argument("--only-hermes", action="store_true", help="Install only the optional Hermes Agent CLI.")
    install.set_defaults(func=cmd_install)

    service = subparsers.add_parser("service", help="Manage the oMLX service.")
    service.add_argument("action", choices=["status", "start", "stop", "restart"])
    service.set_defaults(func=cmd_service)

    docker = subparsers.add_parser("docker", help="Manage the Docker Model Runner runtime.")
    docker.add_argument("action", choices=["init", "status", "start", "stop", "doctor"])
    docker.add_argument("--yes", action="store_true", help="Run docker init without confirmation where possible.")
    docker.add_argument("--vault", help="Vault path for docker init.")
    docker.add_argument("--vault-name", default="Main", help="Vault registration name for docker init.")
    docker.add_argument("--model", default="ai/smollm2", help="Docker Model Runner model id for docker init.")
    docker.add_argument("--chat-engine", choices=["builtin", "hermes", "claude"], default="builtin")
    docker.add_argument("--no-open", action="store_true", help="Do not open Obsidian after docker init.")
    docker.add_argument("--dry-run", action="store_true", help="Print docker init actions without changing files.")
    docker.set_defaults(func=cmd_docker)

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
    last_status = 0
    while True:
        print("\nAI Obsidian")
        print("Choose what you want to do. Press Enter for the recommended next step.\n")
        choice = ask_choice("Main menu", main_menu_options(), default=0)
        if choice == "quit":
            return last_status
        last_status = run_menu_choice(choice)
        print(f"\nCommand finished with exit code {last_status}.")
        if not ask_yes_no("Return to the main menu?", default=True):
            return last_status


def main_menu_options() -> list[tuple[str, str]]:
    return [
        ("start", "Start / open AI Obsidian workspace"),
        ("init", "Init / repair setup"),
        ("vault", "Choose default vault"),
        ("model", "Choose default model"),
        ("stack", "Start/stop stack"),
        ("docker", "Docker Model Runner runtime"),
        ("plugin", "Install/configure/open Obsidian AI plugin"),
        ("soul", "Manage vault soul.md instructions"),
        ("voice", "Transcribe a voice recording"),
        ("downloaded", "Show downloaded models"),
        ("doctor", "Doctor / troubleshoot"),
        ("chat", "CLI chat fallback"),
        ("quit", "Quit"),
    ]


def run_menu_choice(choice: str) -> int:
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
    if choice == "docker":
        docker_choice = ask_choice(
            "Docker",
            [("status", "Status"), ("start", "Start"), ("stop", "Stop"), ("doctor", "Doctor")],
            default=0,
        )
        return cmd_docker(argparse.Namespace(action=docker_choice))
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
    print_docker_health_summary(health)
    print_external_engine_status(health)
    print_soul_health_summary(health)
    print_plugin_health_summary(health)
    return 0 if health.get("ok") else 1


def cmd_setup_status(args: argparse.Namespace) -> int:
    status = collect_setup_status()
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2))
        return 0 if status.get("platform_ok") else 1

    print("AI Obsidian setup status")
    prereq = status["prerequisites"]
    print(f"Platform: {status['platform']['system']} {status['platform']['macos']} / {status['platform']['machine']}")
    print(f"Homebrew: {prereq.get('brew_path') or 'missing'}")
    print(f"Obsidian: {'installed' if prereq.get('obsidian_installed') else 'missing'}")
    print(f"oMLX: {'installed' if prereq.get('omlx_installed') else 'missing'}")
    print(f"Hugging Face CLI: {prereq.get('hf_cli_path') or 'missing'}")
    print(f"ffmpeg: {prereq.get('ffmpeg_path') or 'missing'}")
    print(f"mlx-whisper: {'available' if prereq.get('mlx_whisper_available') else 'missing'}")
    print(f"Registered vaults: {len(status['vaults'])}")
    print(f"Downloaded/local models: {len(status['downloaded_models'])}")
    return 0 if status.get("platform_ok") else 1


def cmd_setup_models(args: argparse.Namespace) -> int:
    payload = collect_setup_models(
        load_remote_models=not getattr(args, "offline", False),
        family=getattr(args, "family", None),
        version=getattr(args, "version", None),
        size=getattr(args, "size", None),
        model_dir=Path(args.model_dir).expanduser() if getattr(args, "model_dir", None) else None,
        runtime=getattr(args, "runtime", RUNTIME_NATIVE_OMLX),
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return 0

    if payload.get("runtime") == RUNTIME_DOCKER_MODEL_RUNNER:
        print("Docker Model Runner models:")
        for model in payload["docker_models"]:
            print(f"- {model['id']} | pulled")
        print(f"\nDocker Model Runner suggestions ({payload['remote_source']}):")
        for model in payload["remote"]:
            print(f"- {model['repo_id']} | {model['note']}")
        return 0

    print("Downloaded/local models:")
    for model in payload["downloaded"]:
        print(f"- {model['id']} | {model['format']} | {model['source']} | {model['path']}")
    print(f"\nRemote Apple Silicon MLX suggestions ({payload['remote_source']}):")
    for model in payload["remote"]:
        print(f"- {model['repo_id']} | {model['family']} {model['version']} | {model['size_bucket']} | {model['min_ram_gb']} GB+")
    return 0


def cmd_setup_apply(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser()
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read setup profile: {exc}", file=sys.stderr)
        return 1

    try:
        plan = normalize_setup_profile(profile)
    except ValueError as exc:
        print(f"Invalid setup profile: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps({"dry_run": True, "plan": plan}, indent=2))
        return 0

    if not args.yes:
        print("Refusing to apply a GUI setup profile without --yes.")
        return 2

    vault_path = Path(plan["vault"]["path"])
    if plan["vault"]["mode"] == "existing" and (not vault_path.exists() or not vault_path.is_dir()):
        print(f"Existing vault path does not exist: {vault_path}", file=sys.stderr)
        return 1

    if plan["runtime"]["mode"] == RUNTIME_DOCKER_MODEL_RUNNER:
        prerequisite_status = ensure_docker_setup_prerequisites(plan["omlx"]["base_url"])
    else:
        prerequisite_status = ensure_prerequisites(
            interactive=False,
            start_omlx_service=False,
            allow_homebrew_install=True,
        )
    if prerequisite_status != 0:
        return prerequisite_status

    config = merge_config(load_config(), config_from_setup_plan(plan))
    if plan["vault"]["mode"] == "create":
        vault_path.mkdir(parents=True, exist_ok=True)
        (vault_path / ".obsidian").mkdir(exist_ok=True)

    created_soul = create_soul(vault_path)
    if created_soul:
        print(f"Created vault instructions: {soul_path(vault_path)}")

    save_config(config)
    print(f"Saved configuration: {config_path()}")

    if plan["plugins"]["install_hub"]:
        status = ensure_plugin_ready(vault_path, config, plugin_id=DEFAULT_PLUGIN_ID, yes=True)
        if status != 0:
            return status
    if plan["plugins"]["install_companion"]:
        status = ensure_plugin_ready(vault_path, config, plugin_id=COMPANION_PLUGIN_ID, yes=True)
        if status != 0:
            return status

    if plan["launch"]["start_stack"]:
        status = cmd_stack_start(argparse.Namespace(vault=plan["vault"]["name"], interactive=False))
        if status != 0:
            return status
    if plan["launch"]["open_obsidian"]:
        return open_obsidian_vault(vault_path)
    return 0


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

    with_hermes = getattr(args, "with_hermes", False)
    only_hermes = getattr(args, "only_hermes", False)

    steps = []
    if not only_hermes:
        steps.extend(
            [
                "Check Apple Silicon and macOS version.",
                "Install Homebrew if missing, or use the existing brew.",
                "Install Obsidian through Homebrew Cask.",
                "Tap and install oMLX from jundot/omlx.",
                "Install Hugging Face CLI for model lookup/downloads.",
                "Install ffmpeg and mlx-whisper for local push-to-talk transcription.",
                "Configure oMLX model directory.",
                "Select an Apple Silicon MLX model.",
                "Create or register an Obsidian vault.",
                "Install and configure the Obsidian AI plugin and push-to-talk companion plugin.",
                "Save configuration.",
                "Run `stack start` to download the model if needed, start oMLX, and open Obsidian.",
            ]
        )
    if with_hermes or only_hermes:
        steps.append("Install optional Hermes Agent CLI using the official NousResearch installer.")
        steps.append("Run `hermes setup` later if Hermes needs provider/API-key configuration.")
    if not with_hermes and not only_hermes:
        steps.append("Detect optional Hermes/Claude Code terminal engines if they are already installed.")

    if args.dry_run:
        print("Install plan:")
        for index, step in enumerate(steps, start=1):
            print(f"{index}. {step}")
        return 0

    if not only_hermes:
        status = ensure_prerequisites(
            interactive=False,
            start_omlx_service=True,
            allow_homebrew_install=args.yes,
        )
        if status != 0:
            return status
    if with_hermes or only_hermes:
        status = ensure_hermes_cli_installed(allow_install=args.yes)
        if status != 0:
            return status
    if only_hermes:
        print("Hermes installation attempted. Run `hermes setup` if provider configuration is needed.")
    else:
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


def cmd_docker(args: argparse.Namespace) -> int:
    if args.action == "init":
        return cmd_docker_init(args)

    config = load_config()
    omlx = config.get("omlx", {})
    base_url = omlx.get("base_url") if runtime_mode(config) == RUNTIME_DOCKER_MODEL_RUNNER else DEFAULT_DMR_BASE_URL
    status = docker_status(base_url=docker_reachability_base_url(config, base_url))

    if args.action in {"status", "doctor"}:
        print("Docker runtime status")
        print(f"Docker CLI: {status.docker_cli or 'missing'}")
        print(f"Docker Desktop: {'running' if status.desktop_running else 'not running or unavailable'}")
        print(f"Docker daemon: {'running' if status.daemon_running else 'not reachable'}")
        print(f"Docker Model Runner: {'running' if status.model_runner_running else 'not ready'}")
        print(f"OpenAI endpoint: {'reachable' if status.api_reachable else 'not reachable'}")
        print(f"OpenAI-compatible API: {status.base_url}")
        if status.backends:
            print("Docker Model Runner backends:")
            for backend, backend_status in status.backends.items():
                details = backend_status.get("details") or ""
                print(f"- {backend}: {backend_status.get('status', 'unknown')} {details}".rstrip())
        if status.models:
            print("Docker Model Runner models:")
            for model in status.models:
                print(f"- {model}")
        else:
            print("Docker Model Runner models: none visible")
        if status.error:
            print(f"Detail: {status.error}")
        if args.action == "doctor" and not status.ok:
            print("Next step: start Docker Desktop and enable Docker Model Runner.")
        return 0 if status.ok else 1

    if args.action == "start":
        start_status = ensure_docker_model_runner(base_url=docker_reachability_base_url(config, base_url))
        if start_status != 0:
            return start_status
        if runtime_mode(config) != RUNTIME_DOCKER_MODEL_RUNNER:
            models = docker_model_list()
            print("Docker Model Runner is ready.")
            if models:
                print("Available Docker models:")
                for model in models:
                    print(f"- {model}")
            else:
                print("No Docker models are pulled yet.")
                print("Try: docker model pull ai/smollm2")
            print("AI Obsidian is still configured for native oMLX.")
            print("Use a setup profile with runtime.mode=docker-model-runner to make stack start use Docker.")
            return 0
        selected = omlx.get("selected_model")
        if selected:
            if not is_docker_model_id(selected):
                print("Configured model is not a Docker Model Runner model id.")
                print(f"Configured model: {selected}")
                print(docker_model_runner_suggestion_for_model(selected, status.backends) or "Choose a Docker model such as `ai/smollm2`.")
                return 1
            models = docker_model_list()
            if any(model_matches(selected, model) for model in models):
                print(f"Docker model is visible through the API: {selected}")
                return 0
            if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
                print(f"Docker model is not visible through the API: {selected}")
                print(f"Run on macOS: docker model pull {selected}")
                return 1
            if not any(model_matches(selected, model) for model in models):
                print(f"Docker model is not pulled yet: {selected}")
                pull_status = docker_model_pull(selected)
                if pull_status != 0:
                    return pull_status
            return docker_model_run_detached(selected)
        print("Docker Model Runner is ready. No model is configured yet.")
        return 0

    if args.action == "stop":
        docker = status.docker_cli
        if not docker:
            print("Docker CLI is not installed.")
            return 1
        print("Stopping Docker Model Runner loaded models. Pulled models and vaults are left untouched.")
        return subprocess.run([docker, "model", "unload", "--all"], check=False).returncode

    return 2


def cmd_docker_init(args: argparse.Namespace) -> int:
    script = repo_root() / "scripts" / "docker-bootstrap.sh"
    if not script.exists():
        print("Docker bootstrap script was not found in this checkout.")
        print("From a source checkout, run: scripts/docker-bootstrap.sh")
        return 1

    command = [str(script), "--model", args.model, "--vault-name", args.vault_name, "--chat-engine", args.chat_engine]
    if args.yes:
        command.append("--yes")
    if args.dry_run:
        command.append("--dry-run")
    if args.vault:
        command.extend(["--vault", args.vault])
    if args.no_open:
        command.append("--no-open")
    print(f"Running: {' '.join(command)}")
    sys.stdout.flush()
    return subprocess.run(command, check=False).returncode

    return 2


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

    if is_docker_runtime(config):
        return cmd_stack_start_docker(args, config)

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

    config = load_config()
    if is_docker_runtime(config):
        print("\nDocker Model Runner:")
        service_status = cmd_docker(argparse.Namespace(action="status"))
    else:
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
    if is_docker_runtime(load_config()):
        return cmd_docker(argparse.Namespace(action="stop"))
    print("Stopping oMLX Homebrew service. Models, vaults, and config are left untouched.")
    return cmd_service(argparse.Namespace(action="stop"))


def cmd_stack_start_docker(args: argparse.Namespace, config: dict[str, Any]) -> int:
    omlx = config.get("omlx", {})
    selected = omlx.get("selected_model")
    base_url = omlx.get("base_url") or DEFAULT_DMR_BASE_URL
    client_base_url = dmr_client_base_url(config)
    vault = args.vault or config.get("default_vault")
    if not vault and getattr(args, "interactive", True):
        selected_vault = choose_vault_from_config()
        vault = selected_vault[0] if selected_vault else None

    print("AI Obsidian Docker stack start")
    print(f"Runtime: {RUNTIME_DOCKER_MODEL_RUNNER}")
    print(f"Configured model: {selected}")
    print(f"Docker Model Runner API: {base_url}")
    if client_base_url != base_url:
        print(f"Container access URL: {client_base_url}")

    if not is_docker_model_id(selected):
        status = docker_status(base_url=client_base_url)
        print("Configured model is not a Docker Model Runner model id.")
        print(f"Configured model: {selected}")
        print(docker_model_runner_suggestion_for_model(selected, status.backends) or "Choose a Docker model such as `ai/smollm2`.")
        return 1

    if ensure_docker_model_runner(base_url=client_base_url) != 0:
        return 1

    pulled_models = docker_model_list()
    if not any(model_matches(selected, model) for model in pulled_models):
        print(f"Selected Docker model is not pulled yet: {selected}")
        if docker_model_pull(selected) != 0:
            print("Docker model pull failed. Re-run `./ai-obsidian docker status` after fixing Docker Model Runner.")
            return 1
    else:
        print("Selected Docker model is already pulled. Skipping pull.")

    client = OmlxClient(base_url=client_base_url, api_key=None)
    served_before_start, existing_api_error = list_models_if_reachable(client)
    if served_before_start is None:
        if existing_api_error:
            print(f"Docker Model Runner API is not ready yet: {existing_api_error}")
        if docker_model_run_detached(selected) != 0:
            return 1
        served_models = wait_for_omlx_models(client, selected_model=selected, service_label="Docker Model Runner")
        if served_models is None:
            print("Docker Model Runner did not become ready in time.")
            return 1
    else:
        served_models = served_before_start
        print(f"Docker Model Runner API is reachable at {base_url} [{len(served_models)} models].")

    if not any(model_matches(selected, model) for model in served_models):
        print("Docker Model Runner is running, but the configured model is not visible through /models.")
        print(f"Configured model: {selected}")
        print("Visible models:")
        for model in served_models:
            print(f"- {model}")
        print("Run `./ai-obsidian docker status` and choose one of the visible model ids.")
        return 1

    active_model = reconcile_configured_model(config, selected, served_models, interactive=getattr(args, "interactive", True))
    config.setdefault("omlx", {})["selected_model"] = active_model
    config.setdefault("runtime", {})["mode"] = RUNTIME_DOCKER_MODEL_RUNNER
    sync_status = sync_obsidian_plugins_after_stack_ready(config, vault)
    if sync_status != 0:
        return sync_status
    print_ready(config, vault)
    return 0


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
    service_label: str = "oMLX",
) -> list[str] | None:
    print(f"Waiting for {service_label} /models ...")
    deadline = time.monotonic() + timeout_seconds
    last_error: OmlxError | None = None
    while time.monotonic() < deadline:
        try:
            models = client.list_models()
            if not selected_model or any(model_matches(selected_model, model) for model in models):
                print(f"{service_label} API is reachable at {client.base_url} [{len(models)} models].")
                return models
            print(f"{service_label} is reachable, waiting for selected model to appear: {selected_model}")
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
    omlx = config.get("omlx", {})
    if is_docker_runtime(config):
        print(f"Docker Model Runner API: {omlx.get('base_url', DEFAULT_DMR_BASE_URL)}")
        print("Docker Model Runner has no separate browser chat in AI Obsidian v1.")
    else:
        print(f"oMLX API: {omlx.get('base_url', DEFAULT_OMLX_BASE_URL)}")
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
    default_base_url = DEFAULT_DMR_BASE_URL if is_docker_runtime(config) else DEFAULT_OMLX_BASE_URL
    client = OmlxClient(
        base_url=dmr_client_base_url(config) if is_docker_runtime(config) else omlx.get("base_url", default_base_url),
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


def collect_setup_status() -> dict[str, Any]:
    prerequisites = check_prerequisites()
    config = load_config()
    vaults = []
    for name, vault in config.get("vaults", {}).items():
        path = Path(vault.get("path", "")).expanduser()
        vaults.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists() and path.is_dir(),
                "is_default": name == config.get("default_vault"),
            }
        )

    model_dirs = []
    for candidate in discover_model_dir_candidates():
        stats = model_dir_stats(candidate.path) if candidate.path else {"mlx_models": 0, "gguf_files": 0}
        model_dirs.append(
            {
                "id": candidate.id,
                "label": candidate.label,
                "path": str(candidate.path.expanduser()) if candidate.path else None,
                "exists": bool(candidate.path and candidate.path.expanduser().exists()),
                "compatible": candidate.compatible,
                "note": candidate.note,
                **stats,
            }
        )

    return {
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "macos": platform.mac_ver()[0] or "unknown",
            "memory_gb": system_memory_gb(),
        },
        "platform_ok": prerequisites.arch_ok and prerequisites.macos_ok,
        "prerequisites": asdict(prerequisites),
        "runtime": collect_setup_runtime(config),
        "docker": docker_status(
            base_url=docker_reachability_base_url(
                config,
                config.get("omlx", {}).get("base_url", DEFAULT_DMR_BASE_URL)
                if is_docker_runtime(config)
                else DEFAULT_DMR_BASE_URL,
            )
        ).to_json(),
        "config": sanitized_config(config),
        "vaults": vaults,
        "model_dirs": model_dirs,
        "downloaded_models": [downloaded_model_to_json(model) for model in discover_downloaded_models()],
        "external_engines": external_engine_statuses(),
    }


def collect_setup_runtime(config: dict[str, Any]) -> dict[str, Any]:
    omlx = config.get("omlx", {})
    mode = runtime_mode(config)
    default_base_url = DEFAULT_DMR_BASE_URL if mode == RUNTIME_DOCKER_MODEL_RUNNER else DEFAULT_OMLX_BASE_URL
    base_url = omlx.get("base_url", default_base_url)
    selected = omlx.get("selected_model")
    client = OmlxClient(
        base_url=dmr_client_base_url(config) if mode == RUNTIME_DOCKER_MODEL_RUNNER else base_url,
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    served_models, error = list_models_if_reachable(client)
    return {
        "mode": mode,
        "omlx": {
            "base_url": base_url,
            "configured_model": selected,
            "reachable": served_models is not None,
            "served_models": served_models or [],
            "model_visible": bool(served_models and selected and any(model_matches(selected, model) for model in served_models)),
            "error": str(error) if error else None,
        },
        "docker_model_runner": docker_status(
            base_url=docker_reachability_base_url(
                config,
                base_url if mode == RUNTIME_DOCKER_MODEL_RUNNER else DEFAULT_DMR_BASE_URL,
            )
        ).to_json(),
    }


def collect_setup_models(
    *,
    load_remote_models: bool,
    family: str | None,
    version: str | None,
    size: str | None,
    model_dir: Path | None,
    runtime: str = RUNTIME_NATIVE_OMLX,
) -> dict[str, Any]:
    if runtime == RUNTIME_DOCKER_MODEL_RUNNER:
        docker_models = [
            {
                "id": model,
                "source": "Docker Model Runner",
                "format": "Docker Model Runner",
                "path": "",
                "size_bytes": 0,
                "note": "managed by Docker Model Runner",
            }
            for model in docker_model_list()
        ]
        suggestions = []
        for suggestion in DOCKER_MODEL_SUGGESTIONS:
            if family and suggestion["family"] != family:
                continue
            if version and suggestion["version"] != version:
                continue
            if size and suggestion["size_bucket"] != size:
                continue
            suggestions.append(suggestion)
        return {
            "runtime": RUNTIME_DOCKER_MODEL_RUNNER,
            "memory_gb": system_memory_gb(),
            "allowed_size_buckets": ["small", "balanced", "large"],
            "docker_models": docker_models,
            "downloaded": [],
            "remote_source": "Docker Model Runner curated defaults",
            "remote": suggestions if load_remote_models else [],
        }

    remote_choices, source = load_model_choices(
        load_remote_models,
        searches=MODEL_SEARCHES_BY_FAMILY.get(family) if family else None,
    )
    allowed_buckets = allowed_size_buckets_for_memory(system_memory_gb())
    filtered_remote = []
    for choice in remote_choices:
        bucket = size_bucket_for_model(choice)
        parsed_version = model_version(choice.repo_id)
        if bucket not in allowed_buckets:
            continue
        if family and choice.family != family:
            continue
        if version and parsed_version != version:
            continue
        if size and bucket != size:
            continue
        filtered_remote.append(model_choice_to_json(choice))

    downloaded = [downloaded_model_to_json(model) for model in discover_downloaded_models()]
    docker_models = [
        {
            "id": model,
            "source": "Docker Model Runner",
            "format": "Docker Model Runner",
            "path": "",
            "size_bytes": 0,
            "note": "managed by Docker Model Runner",
        }
        for model in docker_model_list()
    ]
    if model_dir is not None:
        configured = [
            downloaded_mlx_model_to_json(model)
            for model in discover_downloaded_mlx_models(model_dir)
            if not any(existing["path"] == str(model.path) for existing in downloaded)
        ]
        downloaded = configured + downloaded

    return {
        "runtime": RUNTIME_NATIVE_OMLX,
        "memory_gb": system_memory_gb(),
        "allowed_size_buckets": allowed_buckets,
        "docker_models": docker_models,
        "downloaded": downloaded,
        "remote_source": source,
        "remote": filtered_remote[:50],
    }


def normalize_setup_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("profile must be a JSON object")

    omlx = profile.get("omlx") if isinstance(profile.get("omlx"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    vault = profile.get("vault") if isinstance(profile.get("vault"), dict) else {}
    chat = profile.get("chat") if isinstance(profile.get("chat"), dict) else {}
    plugins = profile.get("plugins") if isinstance(profile.get("plugins"), dict) else {}
    launch = profile.get("launch") if isinstance(profile.get("launch"), dict) else {}

    vault_path_value = vault.get("path")
    if not isinstance(vault_path_value, str) or not vault_path_value.strip():
        raise ValueError("vault.path is required")
    vault_path = Path(vault_path_value).expanduser().resolve()
    vault_name = vault.get("name") if isinstance(vault.get("name"), str) and vault.get("name").strip() else vault_path.name
    vault_mode = vault.get("mode", "create")
    if vault_mode not in {"create", "existing"}:
        raise ValueError("vault.mode must be create or existing")

    selected_model = omlx.get("selected_model")
    if not isinstance(selected_model, str) or not selected_model.strip():
        raise ValueError("omlx.selected_model is required")
    runtime_mode_value = runtime.get("mode", omlx.get("mode", "service"))
    if runtime_mode_value in {"service", "manual", "menubar"}:
        runtime_mode_value = RUNTIME_NATIVE_OMLX
    if runtime_mode_value not in {RUNTIME_NATIVE_OMLX, RUNTIME_DOCKER_MODEL_RUNNER}:
        raise ValueError("runtime.mode must be native-omlx or docker-model-runner")

    model_dir_value = omlx.get("model_dir") or str(DEFAULT_MODEL_DIR)
    if not isinstance(model_dir_value, str):
        raise ValueError("omlx.model_dir must be a path string")
    mode = omlx.get("mode", "service")
    if runtime_mode_value == RUNTIME_DOCKER_MODEL_RUNNER:
        mode = RUNTIME_DOCKER_MODEL_RUNNER
    elif mode not in {"service", "manual", "menubar"}:
        raise ValueError("omlx.mode must be service, manual, or menubar")

    chat_engine = chat.get("default_engine", "builtin")
    if chat_engine not in {"builtin", "hermes", "claude"}:
        raise ValueError("chat.default_engine must be builtin, hermes, or claude")

    return {
        "runtime": {
            "mode": runtime_mode_value,
        },
        "omlx": {
            "mode": mode,
            "base_url": str(
                omlx.get("base_url")
                or (DEFAULT_DMR_BASE_URL if runtime_mode_value == RUNTIME_DOCKER_MODEL_RUNNER else DEFAULT_OMLX_BASE_URL)
            ),
            "api_key": "" if runtime_mode_value == RUNTIME_DOCKER_MODEL_RUNNER else str(omlx.get("api_key") or ""),
            "model_dir": str(Path(model_dir_value).expanduser().resolve()),
            "selected_model": selected_model.strip(),
        },
        "vault": {
            "mode": vault_mode,
            "name": vault_name.strip(),
            "path": str(vault_path),
        },
        "chat": {
            "default_engine": chat_engine,
        },
        "plugins": {
            "install_hub": bool(plugins.get("install_hub", True)),
            "install_companion": bool(plugins.get("install_companion", True)),
        },
        "launch": {
            "start_stack": bool(launch.get("start_stack", True)),
            "open_obsidian": bool(launch.get("open_obsidian", True)),
        },
    }


def config_from_setup_plan(plan: dict[str, Any]) -> dict[str, Any]:
    vault = plan["vault"]
    omlx = dict(plan["omlx"])
    if not omlx.get("api_key"):
        omlx.pop("api_key", None)
    return {
        "omlx": omlx,
        "runtime": plan["runtime"],
        "vaults_root": str(Path(vault["path"]).parent),
        "default_vault": vault["name"],
        "vaults": {
            vault["name"]: {
                "name": vault["name"],
                "path": vault["path"],
            }
        },
        "chat": plan["chat"],
    }


def sanitized_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(config))
    omlx = sanitized.get("omlx")
    if isinstance(omlx, dict) and "api_key" in omlx:
        omlx["api_key_configured"] = bool(omlx.get("api_key"))
        omlx["api_key"] = ""
    return sanitized


def ensure_docker_setup_prerequisites(base_url: str) -> int:
    reachability_base_url = (
        os.environ.get("AI_OBSIDIAN_DMR_CONTAINER_BASE_URL", "http://host.docker.internal:12434/engines/v1")
        if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1"
        else base_url
    )
    status = docker_status(base_url=reachability_base_url)
    if not status.docker_cli:
        print(status.error)
        return 1
    if not status.daemon_running:
        print(status.error or "Docker daemon is not running. Start Docker Desktop.")
        return 1
    if not status.model_runner_running:
        print(status.error or "Docker Model Runner is not enabled.")
        print("Enable Docker Model Runner in Docker Desktop, then retry.")
        return 1
    if os.environ.get("AI_OBSIDIAN_SKIP_OBSIDIAN_APP_CHECK") == "1":
        return 0
    if not obsidian_app_available():
        print("Obsidian.app was not found. Install Obsidian before applying Docker mode setup.")
        return 1
    return 0


def obsidian_app_available() -> bool:
    return (
        Path("/Applications/Obsidian.app").exists()
        or (Path.home() / "Applications" / "Obsidian.app").exists()
        or check_prerequisites().obsidian_installed
    )


def dmr_client_base_url(config: dict[str, Any]) -> str:
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        return os.environ.get("AI_OBSIDIAN_DMR_CONTAINER_BASE_URL", "http://host.docker.internal:12434/engines/v1")
    return config.get("omlx", {}).get("base_url", DEFAULT_DMR_BASE_URL)


def docker_reachability_base_url(config: dict[str, Any], fallback: str = DEFAULT_DMR_BASE_URL) -> str:
    if os.environ.get("AI_OBSIDIAN_IN_CONTAINER") == "1":
        return os.environ.get("AI_OBSIDIAN_DMR_CONTAINER_BASE_URL", "http://host.docker.internal:12434/engines/v1")
    return fallback or config.get("omlx", {}).get("base_url", DEFAULT_DMR_BASE_URL)


def downloaded_model_to_json(model: DownloadedModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "source": model.source,
        "format": model.format,
        "path": str(model.path),
        "size_bytes": model.size_bytes,
        "note": model.note,
    }


def downloaded_mlx_model_to_json(model: DownloadedMlxModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "source": model.source,
        "format": "MLX",
        "path": str(model.path),
        "size_bytes": model.size_bytes,
        "note": f"{model.safetensor_count} safetensors",
    }


def model_choice_to_json(choice: Any) -> dict[str, Any]:
    return {
        "repo_id": choice.repo_id,
        "label": choice.label,
        "min_ram_gb": choice.min_ram_gb,
        "family": choice.family,
        "version": model_version(choice.repo_id),
        "size_bucket": size_bucket_for_model(choice),
        "note": choice.note,
    }


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
        "docker": {},
    }

    omlx = config.get("omlx", {})
    mode = runtime_mode(config)
    default_base_url = DEFAULT_DMR_BASE_URL if mode == RUNTIME_DOCKER_MODEL_RUNNER else DEFAULT_OMLX_BASE_URL
    client = OmlxClient(
        base_url=dmr_client_base_url(config) if mode == RUNTIME_DOCKER_MODEL_RUNNER else omlx.get("base_url", default_base_url),
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    served_models, error = list_models_if_reachable(client)
    selected = omlx.get("selected_model")
    health["omlx"] = {
        "base_url": omlx.get("base_url", default_base_url),
        "configured_model": selected,
        "reachable": served_models is not None,
        "served_models": served_models or [],
        "error": str(error) if error else None,
        "model_visible": bool(served_models and selected and any(model_matches(selected, model) for model in served_models)),
    }
    health["runtime"] = {"mode": mode}
    health["docker"] = docker_status(
        base_url=docker_reachability_base_url(config, omlx.get("base_url", DEFAULT_DMR_BASE_URL))
    ).to_json()

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

    runtime_ready = (
        bool(health["docker"].get("ok"))
        if mode == RUNTIME_DOCKER_MODEL_RUNNER
        else bool(health["system"]["homebrew"])
    )
    health["ok"] = (
        health["system"]["architecture_ok"]
        and health["system"]["macos_ok"]
        and health["system"]["python_ok"]
        and runtime_ready
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


def print_docker_health_summary(health: dict[str, Any]) -> None:
    docker = health.get("docker", {})
    if not docker:
        return
    mode = health.get("runtime", {}).get("mode", RUNTIME_NATIVE_OMLX)
    marker = "ok" if docker.get("ok") else "needs attention"
    print(f"Docker runtime: {mode}")
    print(f"- Docker CLI: {docker.get('docker_cli') or 'missing'}")
    print(f"- Docker Model Runner: {marker}")
    if docker.get("error"):
        print(f"  - {docker['error']}")


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

    if is_docker_runtime(config):
        for model in docker_model_list():
            if model in seen:
                continue
            seen.add(model)
            options.append((model, f"{model} | Docker Model Runner"))

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
        if is_docker_runtime(config):
            if not is_docker_model_id(args.model):
                print("Refusing to pull a native oMLX/MLX repo id as a Docker model.")
                print(docker_model_runner_suggestion_for_model(args.model) or "Choose a Docker model such as `ai/smollm2`.")
                return 1
            return docker_model_pull(args.model)
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
    if is_docker_runtime(config):
        return cmd_docker_models_status(config)
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


def cmd_docker_models_status(config: dict[str, Any]) -> int:
    omlx = config.get("omlx", {})
    selected = omlx.get("selected_model")
    base_url = omlx.get("base_url", DEFAULT_DMR_BASE_URL)
    client_base_url = dmr_client_base_url(config)
    status = docker_status(base_url=client_base_url)
    print(f"Docker Model Runner API: {base_url}")
    if client_base_url != base_url:
        print(f"Container access URL: {client_base_url}")
    print(f"Configured model: {selected or '(not set)'}")
    if not status.ok:
        print(f"Docker Model Runner: {status.error or 'not ready'}")
        return 1

    print("Docker Model Runner pulled models:")
    if status.models:
        for model in status.models:
            marker = " [configured]" if model_matches(selected, model) else ""
            print(f"- {model}{marker}")
    else:
        print("- none")

    client = OmlxClient(base_url=client_base_url, api_key=None)
    served_models, error = list_models_if_reachable(client)
    if served_models is None:
        print(f"Docker Model Runner API: {error}")
        return 1
    print("Docker Model Runner /models:")
    for model in served_models:
        marker = " [configured]" if model_matches(selected, model) else ""
        print(f"- {model}{marker}")
    if selected and not any(model_matches(selected, model) for model in status.models + served_models):
        print("Warning: configured model does not match any pulled or served Docker Model Runner model.")
        return 1
    return 0


def cmd_models_use(model: str) -> int:
    config = load_config()
    omlx = config.get("omlx", {})
    if is_docker_runtime(config):
        known_ids = docker_model_list()
        if known_ids and not any(model_matches(model, known) for known in known_ids):
            print("Warning: this model does not match any Docker Model Runner model id.")
        config.setdefault("omlx", {})["selected_model"] = model
        config.setdefault("omlx", {})["base_url"] = omlx.get("base_url", DEFAULT_DMR_BASE_URL)
        config.setdefault("runtime", {})["mode"] = RUNTIME_DOCKER_MODEL_RUNNER
        save_config(config)
        print(f"Configured default Docker Model Runner model: {model}")
        return 0
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
    configured_normalized = normalize_model_match_id(configured)
    actual_normalized = normalize_model_match_id(actual)
    configured_tail = configured_normalized.rsplit("/", maxsplit=1)[-1]
    actual_tail = actual_normalized.rsplit("/", maxsplit=1)[-1]
    return (
        configured == actual
        or configured_normalized == actual_normalized
        or configured_tail == actual_tail
        or configured_tail.split(":", maxsplit=1)[0] == actual_tail.split(":", maxsplit=1)[0]
    )


def normalize_model_match_id(model_id: str) -> str:
    normalized = model_id.removeprefix("docker.io/")
    if normalized.endswith(":latest"):
        normalized = normalized[: -len(":latest")]
    return normalized


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
    default_base_url = DEFAULT_DMR_BASE_URL if is_docker_runtime(config) else DEFAULT_OMLX_BASE_URL
    base_url = args.base_url or config.get("omlx", {}).get("base_url", default_base_url)
    if is_docker_runtime(config) and not args.base_url:
        base_url = dmr_client_base_url(config)
    api_key = None if is_docker_runtime(config) else (args.api_key or config.get("omlx", {}).get("api_key") or os.environ.get("OMLX_API_KEY"))

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
    label = "Docker Model Runner API" if is_docker_runtime(config) else "oMLX API"
    default_base_url = DEFAULT_DMR_BASE_URL if is_docker_runtime(config) else DEFAULT_OMLX_BASE_URL
    base_url = omlx.get("base_url", default_base_url)
    client = OmlxClient(
        base_url=dmr_client_base_url(config) if is_docker_runtime(config) else base_url,
        api_key=omlx.get("api_key") or os.environ.get("OMLX_API_KEY"),
    )
    try:
        model_count = len(client.list_models())
        print(f"{label}: reachable at {base_url} [{model_count} models]")
    except OmlxError as exc:
        print(f"{label}: {exc} [needs attention]")


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
    if os.environ.get("AI_OBSIDIAN_CONFIG_DIR"):
        return Path(os.environ["AI_OBSIDIAN_CONFIG_DIR"]).expanduser() / "config.json"
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
