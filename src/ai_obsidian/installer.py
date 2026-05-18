from __future__ import annotations

import platform
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any

from .model_catalog import (
    ModelChoice,
    SIZE_BUCKETS,
    filter_model_choices,
    load_model_choices,
    model_local_dir,
    model_version,
    size_bucket_for_model,
    versions_for_choices,
)
from .prerequisites import check_prerequisites, ensure_prerequisites, print_prerequisite_status
from .prerequisites import find_hf_cli
from .soul import ensure_soul


DEFAULT_VAULTS_ROOT = Path.home() / "Documents" / "Obsidian"
DEFAULT_MODEL_DIR = Path.home() / ".omlx" / "models"
CHAT_ENGINES = [
    ("builtin", "Built-in ai-obsidian chat through oMLX"),
    ("hermes", "Hermes CLI one-shot chat provider, if installed"),
    ("claude", "Claude Code CLI one-shot chat provider, if installed"),
    ("codex", "Codex adapter, planned"),
    ("opencode", "OpenCode adapter, planned"),
]
MODEL_FAMILIES = [
    ("qwen", "Qwen: strong general assistant/coding family; my default recommendation."),
    ("gemma", "Gemma: compact Google family; good for note cleanup and summaries."),
    ("llama", "Llama: broad ecosystem and solid general assistant behavior."),
    ("mistral", "Mistral: efficient models, often good for concise tasks."),
    ("granite", "Granite: IBM family, useful when available in MLX."),
]
MODEL_SEARCHES_BY_FAMILY = {
    "qwen": ("Qwen3.6", "Qwen3.5", "Qwen3", "Qwen2.5"),
    "gemma": ("gemma-4", "gemma-3"),
    "llama": ("Llama-3.2", "Llama-3.1"),
    "mistral": ("Mistral",),
    "granite": ("Granite",),
}
OMLX_MODES = [
    ("service", "Homebrew background service"),
    ("manual", "Manual CLI start"),
    ("menubar", "oMLX macOS menu bar app"),
]


@dataclass
class ModelDirCandidate:
    id: str
    label: str
    path: Path | None
    note: str
    compatible: bool = True


@dataclass
class DownloadedMlxModel:
    id: str
    source: str
    model_dir: Path
    path: Path
    size_bytes: int
    safetensor_count: int


@dataclass
class InitModelSelection:
    model: ModelChoice
    model_dir: Path | None = None
    downloaded: bool = False

    @property
    def repo_id(self) -> str:
        return self.model.repo_id


def run_init(load_remote_models: bool = True) -> tuple[int, dict[str, Any] | None]:
    print("AI Obsidian init")
    print("Press Enter to accept defaults.\n")

    print_phase(1, "System check")
    prerequisites = check_prerequisites()
    print_prerequisite_status(prerequisites)

    print_phase(2, "Installing prerequisites")
    prerequisite_status = ensure_prerequisites(
        interactive=True,
        ask_yes_no=ask_yes_no,
        start_omlx_service=False,
        status=prerequisites,
        print_status=False,
    )
    if prerequisite_status != 0:
        return prerequisite_status, None

    config: dict[str, Any] = {}
    print_phase(3, "oMLX configuration")
    omlx_installed = is_omlx_installed()
    if omlx_installed:
        print("Detected existing oMLX installation.")
    else:
        print("oMLX is not installed yet. This setup will prepare a new configuration.")

    model_dir = ask_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    omlx_mode = ask_choice("How should oMLX run?", OMLX_MODES, default=0)
    api_key = ask_api_key(omlx_installed)

    print_phase(4, "Vault configuration")
    vaults_root = ask_path("Where should Obsidian vaults live?", DEFAULT_VAULTS_ROOT)
    vaults_root.mkdir(parents=True, exist_ok=True)

    default_vault_name = ask_text("Default vault name", "Main")
    default_vault_path = vaults_root / default_vault_name
    default_vault_existed = default_vault_path.exists()
    if ask_yes_no(f"Create/register default vault at {default_vault_path}?", default=True):
        default_vault_path.mkdir(parents=True, exist_ok=True)
        (default_vault_path / ".obsidian").mkdir(exist_ok=True)
        created_soul = ensure_soul(
            default_vault_path,
            ask_yes_no=ask_yes_no,
            force_prompt=default_vault_existed,
        )
        if created_soul:
            print(f"Created vault instructions: {default_vault_path / 'soul.md'}")
        config.setdefault("vaults", {})[default_vault_name] = {
            "name": default_vault_name,
            "path": str(default_vault_path),
        }

    print_phase(5, "Chat configuration")
    chat_engine = ask_choice("Default terminal chat engine?", CHAT_ENGINES, default=0)

    print_phase(6, "Model selection")
    selected = choose_model(load_remote_models, current_model_dir=model_dir)
    selected_model = selected.model
    if selected.model_dir is not None:
        model_dir = selected.model_dir
        model_dir.mkdir(parents=True, exist_ok=True)

    config.update(
        {
            "omlx": {
                "mode": omlx_mode,
                "base_url": "http://localhost:8000/v1",
                "api_key": api_key,
                "model_dir": str(model_dir),
                "selected_model": selected_model.repo_id,
            },
            "vaults_root": str(vaults_root),
            "default_vault": default_vault_name,
            "chat": {
                "default_engine": chat_engine,
            },
        }
    )

    print("\nPlanned setup:")
    print(f"- oMLX mode: {omlx_mode}")
    print(f"- oMLX model dir: {model_dir}")
    print(f"- selected model: {selected_model.repo_id}")
    print(f"- vaults root: {vaults_root}")
    print(f"- default vault: {default_vault_name}")
    print(f"- terminal chat: {chat_engine}")
    warn_if_model_dir_needs_manual_service_config(omlx_mode, model_dir)

    warn_if_model_exceeds_memory(selected_model)

    print_phase(7, "Save and launch")
    if ask_yes_no("Save this configuration?", default=True):
        return 0, config
    print("Configuration discarded.")
    return 1, None


def print_phase(index: int, label: str) -> None:
    print(f"\n[{index}/7] {label}")


def ask_model_dir() -> Path:
    candidates = discover_model_dir_candidates()
    default_index = recommended_model_dir_index(candidates)
    options = [(candidate.id, model_dir_label(candidate)) for candidate in candidates]
    choice = ask_choice("Where should oMLX look for models?", options, default=default_index)
    if choice == "custom":
        return ask_path("Custom model directory", DEFAULT_MODEL_DIR)
    selected = next(candidate for candidate in candidates if candidate.id == choice)
    if selected.path is None:
        return DEFAULT_MODEL_DIR.expanduser().resolve()
    return selected.path.expanduser().resolve()


def discover_model_dir_candidates() -> list[ModelDirCandidate]:
    home = Path.home()
    candidates = [
        ModelDirCandidate("omlx", "oMLX default", DEFAULT_MODEL_DIR, "best default for Homebrew service"),
        ModelDirCandidate(
            "omlx_app",
            "oMLX menu bar app",
            home / ".ollama" / "models" / ".omlx" / "models",
            "used by the oMLX macOS app when it stores data under Ollama",
        ),
        ModelDirCandidate(
            "ollama",
            "Ollama",
            home / ".ollama" / "models",
            "usually GGUF/blobs; useful to inspect, not always directly MLX-compatible",
            compatible=False,
        ),
        ModelDirCandidate(
            "lmstudio",
            "LM Studio",
            home / ".lmstudio" / "models",
            "current LM Studio model store",
        ),
        ModelDirCandidate(
            "lmstudio_app_support",
            "LM Studio",
            home / "Library" / "Application Support" / "LM Studio" / "models",
            "older/metadata LM Studio model store; MLX compatibility depends on files",
        ),
        ModelDirCandidate("custom", "Custom path", None, "enter another directory manually"),
    ]
    return dedupe_model_dir_candidates(candidates)


def dedupe_model_dir_candidates(candidates: list[ModelDirCandidate]) -> list[ModelDirCandidate]:
    seen: set[Path] = set()
    deduped: list[ModelDirCandidate] = []
    for candidate in candidates:
        if candidate.path is None:
            deduped.append(candidate)
            continue
        resolved = candidate.path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


def recommended_model_dir_index(candidates: list[ModelDirCandidate]) -> int:
    for index, candidate in enumerate(candidates):
        if candidate.path and candidate.compatible and model_dir_stats(candidate.path)["mlx_models"] > 0:
            return index
    return 0


def model_dir_label(candidate: ModelDirCandidate) -> str:
    if candidate.path is None:
        return f"{candidate.label}: {candidate.note}"
    stats = model_dir_stats(candidate.path)
    exists = "exists" if candidate.path.exists() else "will be created"
    compatibility = "MLX-ready" if candidate.compatible else "provider cache / may need conversion"
    return (
        f"{candidate.label}: {candidate.path} | {exists} | "
        f"{stats['mlx_models']} MLX models, {stats['gguf_files']} GGUF files | "
        f"{compatibility}; {candidate.note}"
    )


def model_dir_stats(path: Path) -> dict[str, int]:
    if not path.exists() or not path.is_dir():
        return {"mlx_models": 0, "gguf_files": 0}
    mlx_models = 0
    gguf_files = 0
    scanned = 0
    try:
        iterator = path.rglob("*")
        for child in iterator:
            scanned += 1
            if scanned > 20000:
                break
            if child.is_file() and child.suffix.lower() == ".gguf":
                gguf_files += 1
            if child.is_dir() and has_mlx_model_files(child):
                mlx_models += 1
    except OSError:
        return {"mlx_models": mlx_models, "gguf_files": gguf_files}
    return {"mlx_models": mlx_models, "gguf_files": gguf_files}


def has_mlx_model_files(path: Path) -> bool:
    return (path / "config.json").exists() and (
        any(path.glob("*.safetensors")) or any(path.glob("model-*.safetensors"))
    )


def warn_if_model_dir_needs_manual_service_config(omlx_mode: str, model_dir: Path) -> None:
    if omlx_mode != "service":
        return
    if model_dir.expanduser().resolve() == DEFAULT_MODEL_DIR.expanduser().resolve():
        return
    print("Warning: Homebrew's default oMLX service starts with `omlx serve` and normally reads ~/.omlx/models.")
    print("If this selected directory is not exposed by /v1/models, use the menu bar/manual mode or configure oMLX service arguments.")


def download_model(model: ModelChoice, model_dir: Path) -> int:
    return download_model_repo(model.repo_id, model_dir)


def download_model_repo(repo_id: str, model_dir: Path) -> int:
    destination = Path(model_local_dir(str(model_dir), repo_id))
    destination.parent.mkdir(parents=True, exist_ok=True)

    hf_cli = find_hf_cli()
    if hf_cli:
        command = [hf_cli, "download", repo_id, "--local-dir", str(destination)]
    else:
        print("Hugging Face CLI is not installed.")
        print("Install it later with: python3 -m pip install 'huggingface_hub[cli]'")
        print(f"Then download: hf download {repo_id} --local-dir {destination}")
        return 1

    print(f"Running: {' '.join(command)}")
    sys.stdout.flush()
    return subprocess.run(command, check=False).returncode


def choose_model(load_remote_models: bool, current_model_dir: Path | None = None) -> InitModelSelection:
    downloaded = choose_downloaded_model(current_model_dir)
    if downloaded is not None:
        return downloaded

    ram_gb = system_memory_gb()
    allowed_buckets = allowed_size_buckets_for_memory(ram_gb)
    family = choose_model_family()
    choices, source = load_model_choices(load_remote_models, searches=MODEL_SEARCHES_BY_FAMILY.get(family))
    print(f"Apple Silicon MLX model suggestions source: {source}")

    choices = [
        choice
        for choice in choices
        if choice.family == family and size_bucket_for_model(choice) in allowed_buckets
    ]
    if not choices:
        print("No models matched that family and this Mac's memory. Falling back to offline safe options.")
        choices = [
            choice
            for choice in load_model_choices(False)[0]
            if choice.family == family and size_bucket_for_model(choice) in allowed_buckets
        ]
    if not choices:
        raise RuntimeError(f"No Apple Silicon MLX models found for {family} that fit this Mac.")

    preferred_bucket = preferred_size_bucket(allowed_buckets)
    version = choose_model_version(choices, family, preferred_bucket)
    version_choices = [
        choice
        for choice in choices
        if model_version(choice.repo_id) == version
    ]
    size_bucket = choose_model_size(
        sorted({size_bucket_for_model(choice) for choice in version_choices}, key=size_bucket_sort_key),
        ram_gb,
    )
    filtered = filter_model_choices(choices, family, version, size_bucket)

    options = [(choice.repo_id, model_option_label(choice)) for choice in filtered]
    selected_repo = ask_choice("Choose initial oMLX model", options, default=0)
    return InitModelSelection(next(choice for choice in filtered if choice.repo_id == selected_repo))


def choose_downloaded_model(current_model_dir: Path | None = None) -> InitModelSelection | None:
    local_models = discover_downloaded_mlx_models(current_model_dir)
    if not local_models:
        return None

    print("Found already downloaded Apple Silicon MLX models.")
    options = [
        (str(index), downloaded_model_label(model))
        for index, model in enumerate(local_models)
    ]
    options.append(("download", "Download or choose another model from Hugging Face"))
    choice = ask_choice("Use an already downloaded model?", options, default=0)
    if choice == "download":
        return None

    selected = local_models[int(choice)]
    model = ModelChoice(
        repo_id=selected.id,
        label=selected.id.rsplit("/", maxsplit=1)[-1],
        min_ram_gb=estimated_min_ram_for_model_size(selected.size_bytes),
        family=family_from_model_id(selected.id),
        note=f"Already downloaded in {selected.source}.",
    )
    return InitModelSelection(model=model, model_dir=selected.model_dir, downloaded=True)


def discover_downloaded_mlx_models(current_model_dir: Path | None = None) -> list[DownloadedMlxModel]:
    candidates = discover_model_dir_candidates()
    if current_model_dir is not None:
        current = current_model_dir.expanduser().resolve()
        if not any(candidate.path and candidate.path.expanduser().resolve() == current for candidate in candidates):
            candidates.insert(0, ModelDirCandidate("selected", "Selected model directory", current, "currently selected"))

    models: list[DownloadedMlxModel] = []
    seen_paths: set[Path] = set()
    for candidate in candidates:
        if not candidate.compatible or candidate.path is None:
            continue
        root = candidate.path.expanduser()
        if not root.exists() or not root.is_dir():
            continue
        try:
            children = sorted(path for path in root.rglob("*") if path.is_dir())
        except OSError:
            continue
        for child in children:
            if not has_mlx_model_files(child):
                continue
            resolved = child.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            models.append(
                DownloadedMlxModel(
                    id=model_id_from_path(root, child),
                    source=candidate.label,
                    model_dir=root.resolve(),
                    path=resolved,
                    size_bytes=directory_size(child),
                    safetensor_count=count_safetensors(child),
                )
            )
    return sorted(models, key=lambda item: (item.source, item.id))


def model_id_from_path(root: Path, model_path: Path) -> str:
    try:
        relative = model_path.relative_to(root)
    except ValueError:
        return model_path.name
    return "/".join(relative.parts)


def downloaded_model_label(model: DownloadedMlxModel) -> str:
    return (
        f"{model.id} | {format_bytes(model.size_bytes)} | {model.source} | "
        f"{model.safetensor_count} safetensors | {model.path}"
    )


def estimated_min_ram_for_model_size(size_bytes: int) -> int:
    size_gb = size_bytes / 1024 / 1024 / 1024
    if size_gb <= 8:
        return 16
    if size_gb <= 20:
        return 32
    return max(64, ceil(size_gb))


def family_from_model_id(model_id: str) -> str:
    lowered = model_id.lower()
    for family, _ in MODEL_FAMILIES:
        if family in lowered:
            return family
    return "local"


def directory_size(path: Path) -> int:
    total = 0
    try:
        iterator = path.rglob("*")
        for file_path in iterator:
            if not file_path.is_file():
                continue
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def count_safetensors(path: Path) -> int:
    try:
        return len({file_path for file_path in path.glob("*.safetensors")})
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


def choose_model_family() -> str:
    return ask_choice("Which model family do you want?", MODEL_FAMILIES, default=0)


def choose_model_version(choices: list[ModelChoice], family: str, preferred_bucket: str | None = None) -> str:
    versions = versions_for_choices(choices, family)
    labels = [(version, version_label(family, version)) for version in versions]
    return ask_choice(
        "Which model version do you want?",
        labels,
        default=recommended_version_default_index(choices, family, versions, preferred_bucket),
    )


def choose_model_size(allowed_buckets: list[str], ram_gb: int | None) -> str:
    options = [
        (value, f"{label}: {description}")
        for value, label, description in SIZE_BUCKETS
        if value in allowed_buckets
    ]
    ram_text = f"Detected RAM: about {ram_gb} GB." if ram_gb else "Could not detect RAM; using safest options."
    print(ram_text)
    return ask_choice("How large should the model be?", options, default=recommended_size_default_index(allowed_buckets))


def size_bucket_sort_key(bucket: str) -> int:
    order = {"small": 0, "balanced": 1, "large": 2}
    return order.get(bucket, 99)


def preferred_size_bucket(allowed_buckets: list[str]) -> str:
    if "balanced" in allowed_buckets:
        return "balanced"
    return allowed_buckets[0]


def recommended_version_default_index(
    choices: list[ModelChoice],
    family: str,
    versions: list[str],
    preferred_bucket: str | None,
) -> int:
    if not preferred_bucket:
        return 0
    for index, version in enumerate(versions):
        if any(
            choice.family == family
            and model_version(choice.repo_id) == version
            and size_bucket_for_model(choice) == preferred_bucket
            for choice in choices
        ):
            return index
    return 0


def version_label(family: str, version: str) -> str:
    if family == "qwen" and version == "3.6":
        return "Qwen 3.6: newest/highest-end options when available."
    if family == "gemma" and version == "4":
        return "Gemma 4: newest Gemma options when available."
    if version == "unknown":
        return "Unknown version from repository name."
    return f"{family.title()} {version}"


def model_option_label(choice: ModelChoice) -> str:
    bucket = size_bucket_for_model(choice)
    return (
        f"{choice.label} | {choice.min_ram_gb} GB+ | "
        f"{choice.family} {model_version(choice.repo_id)} | {bucket} | {choice.note}"
    )


def warn_if_model_exceeds_memory(choice: ModelChoice) -> None:
    ram_gb = system_memory_gb()
    if ram_gb is None:
        return
    if choice.min_ram_gb > ram_gb:
        print(
            f"Warning: selected model is tagged {choice.min_ram_gb} GB+, "
            f"but this Mac reports about {ram_gb} GB RAM."
        )


def recommended_size_default_index(allowed_buckets: list[str] | None = None) -> int:
    allowed = allowed_buckets or allowed_size_buckets_for_memory(system_memory_gb())
    if "balanced" in allowed:
        return allowed.index("balanced")
    return 0


def allowed_size_buckets_for_memory(ram_gb: int | None) -> list[str]:
    if ram_gb is None or ram_gb < 32:
        return ["small"]
    if ram_gb < 64:
        return ["small", "balanced"]
    return ["small", "balanced", "large"]


def system_memory_gb() -> int | None:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        return round(int(result.stdout.strip()) / 1024 / 1024 / 1024)
    except (OSError, ValueError):
        return None


def is_omlx_installed() -> bool:
    if shutil.which("omlx"):
        return True
    brew = shutil.which("brew")
    if not brew:
        return False
    result = subprocess.run([brew, "list", "omlx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def ask_choice(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str:
    print(prompt)
    for index, (_, label) in enumerate(options, start=1):
        suffix = " [default]" if index - 1 == default else ""
        print(f"  {index}. {label}{suffix}")
    while True:
        answer = input("> ").strip()
        if not answer:
            return options[default][0]
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print("Choose a number from the list.")


def ask_path(prompt: str, default: Path) -> Path:
    answer = input(f"{prompt} [{default}]: ").strip()
    return Path(answer or default).expanduser().resolve()


def ask_text(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def ask_yes_no(prompt: str, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{marker}] ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def ask_api_key(omlx_installed: bool) -> str:
    generated = secrets.token_urlsafe(32)
    if omlx_installed:
        print("oMLX API key")
        print("Existing oMLX detected. If your server already uses an API key, enter that current key.")
        print("  1. Enter current oMLX key [default]")
        print("  2. Generate a new key")
        print("  3. Leave empty")
        answer = input("> ").strip()
        if answer in {"", "1"}:
            return input("Current oMLX API key: ").strip()
        if answer == "2":
            print("Generated new local API key. Configure oMLX with this key before using protected endpoints.")
            return generated
        return ""

    print("oMLX API key")
    print("New oMLX setup. Choose the key this project should use.")
    print("  1. Generate a new local key [default]")
    print("  2. Enter a key manually")
    print("  3. Leave empty")
    answer = input("> ").strip()
    if answer == "2":
        return input("API key: ").strip()
    if answer == "3":
        return ""
    print("Generated local API key.")
    return generated
