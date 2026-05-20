#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
SHIM="${BIN_DIR}/ai-obsidian-docker"
MODEL="ai/smollm2"
VAULT_NAME="Main"
VAULT_PATH="${HOME}/Documents/Obsidian/Main"
CHAT_ENGINE="builtin"
ASSUME_YES=0
DRY_RUN=0
OPEN_OBSIDIAN=1
BUILD_LOCAL=0
TIMEOUT_SECONDS="${AI_OBSIDIAN_DOCKER_TIMEOUT:-90}"

usage() {
  cat <<'EOF'
Usage: scripts/docker-bootstrap.sh [options]

Bootstrap AI Obsidian in Docker-first mode without installing host oMLX.

Options:
  --yes                   Do not prompt where safe.
  --dry-run               Print actions without changing files.
  --vault PATH            Vault path to create/register. Default: ~/Documents/Obsidian/Main
  --vault-name NAME       Vault name in AI Obsidian config. Default: Main
  --model MODEL           Docker Model Runner model id. Default: ai/smollm2
  --chat-engine ENGINE    builtin, hermes, or claude. Default: builtin
  --build-local           Build ai-obsidian:local instead of pulling the published image.
  --no-open               Do not open Obsidian at the end.
  -h, --help              Show this help.
EOF
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

run() {
  echo "Running: $*"
  if [ "$DRY_RUN" -eq 0 ]; then
    "$@"
  fi
}

confirm() {
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  printf '%s [Y/n] ' "$1"
  read -r answer
  case "${answer:-y}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      ASSUME_YES=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --vault)
      [ "$#" -ge 2 ] || { echo "--vault requires a path" >&2; exit 2; }
      VAULT_PATH="$(expand_path "$2")"
      shift 2
      ;;
    --vault-name)
      [ "$#" -ge 2 ] || { echo "--vault-name requires a value" >&2; exit 2; }
      VAULT_NAME="$2"
      shift 2
      ;;
    --model)
      [ "$#" -ge 2 ] || { echo "--model requires a model id" >&2; exit 2; }
      MODEL="$2"
      shift 2
      ;;
    --chat-engine)
      [ "$#" -ge 2 ] || { echo "--chat-engine requires builtin, hermes, or claude" >&2; exit 2; }
      CHAT_ENGINE="$2"
      case "$CHAT_ENGINE" in builtin|hermes|claude) ;; *) echo "Invalid chat engine: $CHAT_ENGINE" >&2; exit 2 ;; esac
      shift 2
      ;;
    --build-local)
      BUILD_LOCAL=1
      shift
      ;;
    --no-open)
      OPEN_OBSIDIAN=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

cat <<EOF
AI Obsidian Docker-first bootstrap
- Runtime: Docker Model Runner
- Model: $MODEL
- Vault: $VAULT_PATH
- Host oMLX install: skipped
EOF

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is missing. Install Docker Desktop for Mac first: https://www.docker.com/products/docker-desktop/" >&2
  exit 1
fi

if [ ! -d "/Applications/Obsidian.app" ] && [ ! -d "${HOME}/Applications/Obsidian.app" ]; then
  echo "Obsidian.app was not found. Install Obsidian first, then rerun this script." >&2
  echo "Download: https://obsidian.md/download" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop..."
  if ! run docker desktop start; then
    echo "Could not start Docker Desktop automatically. Open Docker Desktop, then rerun this script." >&2
    exit 1
  fi
fi

echo "Waiting for Docker daemon..."
deadline=$((SECONDS + TIMEOUT_SECONDS))
until docker info >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "Docker daemon did not become ready in ${TIMEOUT_SECONDS}s." >&2
    exit 1
  fi
  sleep 2
done

if ! docker model status >/dev/null 2>&1; then
  echo "Docker Model Runner is not ready. Enable it in Docker Desktop, then rerun this script." >&2
  exit 1
fi

if ! curl -fsS --max-time 5 http://localhost:12434/engines/v1/models >/dev/null 2>&1; then
  run docker desktop enable model-runner --tcp=12434
fi

INSTALL_ARGS=("$ROOT_DIR/scripts/docker-install.sh" --yes --bin-dir "$BIN_DIR")
if [ "$BUILD_LOCAL" -eq 1 ]; then
  INSTALL_ARGS+=(--build-local)
fi
run "${INSTALL_ARGS[@]}"

case "$MODEL" in
  mlx-community/*|unsloth/*)
    echo "Refusing to pull native oMLX/MLX repo id as a Docker model: $MODEL" >&2
    echo "Use a Docker Model Runner id such as ai/smollm2, or a supported hf.co/... id." >&2
    exit 1
    ;;
esac

run docker model pull "$MODEL"

PROFILE="$(mktemp "${TMPDIR:-/tmp}/ai-obsidian-docker-profile.XXXXXX")"
cat > "$PROFILE" <<EOF
{
  "runtime": {
    "mode": "docker-model-runner"
  },
  "omlx": {
    "mode": "docker-model-runner",
    "base_url": "http://localhost:12434/engines/v1",
    "api_key": "",
    "model_dir": "",
    "selected_model": "$MODEL"
  },
  "vault": {
    "mode": "create",
    "name": "$VAULT_NAME",
    "path": "$VAULT_PATH"
  },
  "chat": {
    "default_engine": "$CHAT_ENGINE"
  },
  "plugins": {
    "install_hub": true,
    "install_companion": true
  },
  "launch": {
    "start_stack": false,
    "open_obsidian": false
  }
}
EOF

export AI_OBSIDIAN_SKIP_OBSIDIAN_APP_CHECK=1
run "$SHIM" setup apply --profile "$PROFILE" --yes
run "$SHIM" docker start

if [ "$OPEN_OBSIDIAN" -eq 1 ]; then
  run open -a Obsidian "$VAULT_PATH"
fi

cat <<EOF

AI Obsidian Docker mode is ready.

Daily commands:
  $SHIM stack start
  open -a Obsidian "$VAULT_PATH"

Useful checks:
  $SHIM docker status
  $SHIM setup models --runtime docker-model-runner --json
EOF
