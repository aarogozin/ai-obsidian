from __future__ import annotations

import json
from http.client import IncompleteRead
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import urlopen


HF_MODELS_API = "https://huggingface.co/api/models"
TARGETED_SEARCHES = ("Qwen3.6", "gemma-4", "Qwen3.5", "Qwen3", "Llama-3.2")


@dataclass
class ModelChoice:
    repo_id: str
    label: str
    min_ram_gb: int
    family: str
    note: str


SIZE_BUCKETS = [
    ("small", "Small / fast", "16 GB RAM: good for MacBook Air, quick summaries, short note cleanup."),
    ("balanced", "Balanced", "32 GB RAM: better default for meeting notes and longer vault context."),
    ("large", "Large / reasoning", "64 GB+ RAM: stronger reasoning, slower and heavier."),
]


FALLBACK_MODELS = [
    ModelChoice("mlx-community/Qwen3-1.7B-4bit", "Qwen3 1.7B 4-bit", 16, "qwen", "Offline fallback for small Macs."),
    ModelChoice("mlx-community/Qwen2.5-3B-Instruct-4bit", "Qwen2.5 3B Instruct 4-bit", 16, "qwen", "Offline fallback for fast note work."),
    ModelChoice("mlx-community/gemma-3-4b-it-4bit", "Gemma 3 4B IT 4-bit", 16, "gemma", "Offline fallback Gemma option."),
    ModelChoice("mlx-community/Qwen3-8B-4bit", "Qwen3 8B 4-bit", 32, "qwen", "Offline fallback balanced option."),
    ModelChoice("mlx-community/Qwen2.5-14B-Instruct-4bit", "Qwen2.5 14B Instruct 4-bit", 32, "qwen", "Offline fallback larger option."),
]


def load_curated_models() -> list[ModelChoice]:
    return FALLBACK_MODELS


def fetch_huggingface_models(searches: tuple[str, ...] | None = None) -> list[ModelChoice]:
    payload = fetch_huggingface_payload(searches)
    choices = [choice for item in payload if (choice := model_choice_from_hf_item(item))]
    choices.sort(key=model_rank, reverse=True)
    return merge_model_choices(choices, [])


def fetch_huggingface_payload(searches: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    targeted_searches = searches or TARGETED_SEARCHES
    queries = [
        {"author": "mlx-community", "sort": "lastModified", "direction": "-1", "limit": "80"},
        {"author": "mlx-community", "sort": "downloads", "direction": "-1", "limit": "40"},
    ]
    queries.extend(
        {"author": "mlx-community", "search": search, "sort": "lastModified", "direction": "-1", "limit": "20"}
        for search in targeted_searches
    )

    for query in queries:
        try:
            payload.extend(fetch_huggingface_query(query))
        except (OSError, TimeoutError, IncompleteRead, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return dedupe_hf_items(payload)


def fetch_huggingface_query(query: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{HF_MODELS_API}?{urlencode(query)}"
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, IncompleteRead, json.JSONDecodeError, UnicodeDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def dedupe_hf_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        repo_id = item.get("id")
        if not isinstance(repo_id, str) or repo_id in seen:
            continue
        seen.add(repo_id)
        deduped.append(item)
    return deduped


def model_choice_from_hf_item(item: dict[str, Any]) -> ModelChoice | None:
    repo_id = item.get("id")
    if not isinstance(repo_id, str):
        return None
    if not is_apple_silicon_mlx_model(item):
        return None
    lower = repo_id.lower()
    label = repo_id.removeprefix("mlx-community/").replace("-", " ")
    modified = str(item.get("lastModified", ""))
    downloads = int(item.get("downloads") or 0)
    return ModelChoice(
        repo_id=repo_id,
        label=label,
        min_ram_gb=guess_ram(lower),
        family=guess_family(lower),
        note=(
            f"Live Hugging Face: updated {format_modified_date(modified)}, "
            f"{downloads:,} downloads. Apple Silicon MLX candidate."
        ),
    )


def is_apple_silicon_mlx_model(item: dict[str, Any]) -> bool:
    repo_id = item.get("id")
    if not isinstance(repo_id, str):
        return False

    searchable = " ".join(
        [
            repo_id,
            str(item.get("library_name", "")),
            str(item.get("pipeline_tag", "")),
            " ".join(str(tag) for tag in item.get("tags", []) if isinstance(tag, str)),
        ]
    ).lower()

    blocked = (
        "embedding",
        "rerank",
        "uncensored",
        "abliterated",
        "heretic",
        "gguf",
        "gptq",
        "awq",
        "exl2",
        "onnx",
        "tflite",
        "coreml",
    )
    if any(marker in searchable for marker in blocked):
        return False

    if not any(name in searchable for name in ("qwen", "gemma", "granite", "mistral", "llama")):
        return False

    mlx_signal = (
        repo_id.startswith("mlx-community/")
        or "mlx" in searchable
        or "mlx-lm" in searchable
        or "mlx-vlm" in searchable
    )
    if not mlx_signal:
        return False

    apple_ready_signal = any(
        marker in searchable
        for marker in (
            "mlx",
            "safetensors",
            "4bit",
            "8bit",
            "optiq",
            "mxfp8",
            "nvfp4",
            "bf16",
        )
    )
    return apple_ready_signal


def model_rank(choice: ModelChoice) -> tuple[int, int, int, str]:
    lower = choice.repo_id.lower()
    recency_score = 0
    match = re.search(r"updated (\d{4}-\d{2}-\d{2})", choice.note)
    if match:
        recency_score = parse_date_score(match.group(1))
    preferred_family = 1 if any(name in lower for name in ("qwen3.6", "gemma-4", "qwen3.5")) else 0
    quality = 1 if any(marker in lower for marker in ("optiq", "4bit")) else 0
    return preferred_family, recency_score, quality, choice.repo_id


def families_for_choices(choices: list[ModelChoice]) -> list[str]:
    preferred = ["qwen", "gemma", "llama", "mistral", "granite", "other"]
    present = {choice.family for choice in choices}
    ordered = [family for family in preferred if family in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def versions_for_choices(choices: list[ModelChoice], family: str) -> list[str]:
    versions = {model_version(choice.repo_id) for choice in choices if choice.family == family}
    preferred = ["3.6", "4", "3.5", "3", "2.5", "3.2", "unknown"]
    ordered = [version for version in preferred if version in versions]
    ordered.extend(sorted(versions - set(ordered), reverse=True))
    return ordered


def filter_model_choices(
    choices: list[ModelChoice],
    family: str,
    version: str,
    size_bucket: str,
) -> list[ModelChoice]:
    filtered = [
        choice
        for choice in choices
        if choice.family == family
        and model_version(choice.repo_id) == version
        and size_bucket_for_model(choice) == size_bucket
    ]
    if filtered:
        return filtered
    return [
        choice
        for choice in choices
        if choice.family == family and model_version(choice.repo_id) == version
    ]


def model_version(repo_id: str) -> str:
    lower = repo_id.lower()
    patterns = {
        "qwen": r"qwen(?:-|)(\d+(?:\.\d+)?)",
        "gemma": r"gemma-(\d+(?:\.\d+)?)",
        "llama": r"llama-(\d+(?:\.\d+)?)",
        "mistral": r"mistral(?:-|)(\d+(?:\.\d+)?)",
        "granite": r"granite(?:-|)(\d+(?:\.\d+)?)",
    }
    for pattern in patterns.values():
        match = re.search(pattern, lower)
        if match:
            return match.group(1)
    return "unknown"


def size_bucket_for_model(choice: ModelChoice) -> str:
    largest = largest_model_size(choice.repo_id.lower())
    if largest <= 4:
        return "small"
    if largest <= 14:
        return "balanced"
    return "large"


def largest_model_size(lower_repo_id: str) -> float:
    sizes = [float(match.group(1)) for match in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)b(?!it)", lower_repo_id)]
    return max(sizes, default=0)


def parse_date_score(date_text: str) -> int:
    try:
        parsed = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return int(parsed.timestamp())


def format_modified_date(value: str) -> str:
    if not value:
        return "unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10] or "unknown"


def load_model_choices(load_remote_models: bool, searches: tuple[str, ...] | None = None) -> tuple[list[ModelChoice], str]:
    if load_remote_models:
        remote = fetch_huggingface_models(searches)
        if remote:
            return remote, "live Hugging Face mlx-community list"
    return load_curated_models(), "offline fallback list"


def merge_model_choices(primary: list[ModelChoice], secondary: list[ModelChoice]) -> list[ModelChoice]:
    seen: set[str] = set()
    merged: list[ModelChoice] = []
    for choice in primary + secondary:
        if choice.repo_id in seen:
            continue
        seen.add(choice.repo_id)
        merged.append(choice)
    return merged


def model_local_dir(model_dir: str, repo_id: str) -> str:
    return f"{model_dir.rstrip('/')}/{quote(repo_id, safe='/')}"


def guess_family(lower_repo_id: str) -> str:
    if "qwen" in lower_repo_id:
        return "qwen"
    if "gemma" in lower_repo_id:
        return "gemma"
    if "granite" in lower_repo_id:
        return "granite"
    if "mistral" in lower_repo_id:
        return "mistral"
    return "other"


def guess_ram(lower_repo_id: str) -> int:
    largest = largest_model_size(lower_repo_id)
    if largest <= 4:
        return 16
    if largest <= 14:
        return 32
    return 64
