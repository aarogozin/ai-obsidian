#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
SHIM_NAME="ai-obsidian-docker"
DOCKERHUB_REPO="mrrogozin/obsidian-omlx"
GHCR_REPO="ghcr.io/aarogozin/ai-obsidian"
DOCKER_IMAGE="${AI_OBSIDIAN_DOCKER_IMAGE:-}"
IMAGE_EXPLICIT=0
if [ -n "$DOCKER_IMAGE" ]; then
  IMAGE_EXPLICIT=1
fi
ASSUME_YES=0
DRY_RUN=0
BUILD_LOCAL=0

usage() {
  cat <<'EOF'
Usage: docker-install.sh [options]

Install the AI Obsidian Docker control-plane shim.

Options:
  --yes              Do not prompt before writing the shim.
  --dry-run          Print actions without changing files.
  --image IMAGE      Docker image to use. Overrides tag-aware default.
  --build-local      Build ai-obsidian:local from this checkout instead of pulling.
  --bin-dir PATH     Shim directory. Default: ~/.local/bin
  --shim-name NAME   Shim command name. Default: ai-obsidian-docker
  -h, --help         Show this help.
EOF
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
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
    --image)
      [ "$#" -ge 2 ] || { echo "--image requires an image name" >&2; exit 2; }
      DOCKER_IMAGE="$2"
      IMAGE_EXPLICIT=1
      shift 2
      ;;
    --build-local)
      BUILD_LOCAL=1
      DOCKER_IMAGE="ai-obsidian:local"
      shift
      ;;
    --bin-dir)
      [ "$#" -ge 2 ] || { echo "--bin-dir requires a path" >&2; exit 2; }
      BIN_DIR="$(expand_path "$2")"
      shift 2
      ;;
    --shim-name)
      [ "$#" -ge 2 ] || { echo "--shim-name requires a value" >&2; exit 2; }
      SHIM_NAME="$2"
      shift 2
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

run() {
  echo "Running: $*"
  if [ "$DRY_RUN" -eq 0 ]; then
    "$@"
  fi
}

pyproject_version() {
  awk -F'"' '/^version = / { print $2; exit }' "$ROOT_DIR/pyproject.toml" 2>/dev/null || true
}

docker_image_tag() {
  local exact_tag=""
  local branch=""
  local version=""
  exact_tag="$(git -C "$ROOT_DIR" describe --tags --exact-match 2>/dev/null || true)"
  if [[ "$exact_tag" == v* ]] && git -C "$ROOT_DIR" diff-index --quiet HEAD -- 2>/dev/null; then
    printf '%s\n' "$exact_tag"
    return 0
  fi
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch="$(git -C "$ROOT_DIR" branch --show-current 2>/dev/null || true)"
    if [ -n "$branch" ]; then
      printf '%s\n' "edge"
      return 0
    fi
  fi
  version="$(pyproject_version)"
  if [ -n "$version" ]; then
    printf '%s\n' "$version"
    return 0
  fi
  printf '%s\n' "latest"
}

resolve_docker_image() {
  if [ -n "$DOCKER_IMAGE" ]; then
    printf '%s\n' "$DOCKER_IMAGE"
    return 0
  fi
  printf '%s:%s\n' "$DOCKERHUB_REPO" "$(docker_image_tag)"
}

fallback_image_for() {
  local image="$1"
  local tag="${image##*:}"
  printf '%s:%s\n' "$GHCR_REPO" "$tag"
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

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is missing. Install Docker Desktop for Mac first." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Start Docker Desktop, then retry." >&2
  exit 1
fi

export AI_OBSIDIAN_UID="$(id -u)"
export AI_OBSIDIAN_GID="$(id -g)"
if [ -n "${DOCKER_HOST:-}" ] && [[ "$DOCKER_HOST" == unix://* ]]; then
  export AI_OBSIDIAN_DOCKER_SOCKET="${DOCKER_HOST#unix://}"
elif [ -S "${HOME}/.docker/run/docker.sock" ]; then
  export AI_OBSIDIAN_DOCKER_SOCKET="${HOME}/.docker/run/docker.sock"
else
  export AI_OBSIDIAN_DOCKER_SOCKET="/var/run/docker.sock"
fi
DOCKER_IMAGE="$(resolve_docker_image)"
if [ "$BUILD_LOCAL" -eq 1 ]; then
  run docker build --platform linux/arm64 -f "$ROOT_DIR/docker/Dockerfile" -t "$DOCKER_IMAGE" "$ROOT_DIR"
else
  if ! run docker pull "$DOCKER_IMAGE"; then
    if [ "$IMAGE_EXPLICIT" -eq 0 ] && [[ "$DOCKER_IMAGE" == "$DOCKERHUB_REPO:"* ]]; then
      DOCKER_IMAGE="$(fallback_image_for "$DOCKER_IMAGE")"
      echo "Docker Hub image pull failed. Trying GHCR fallback: $DOCKER_IMAGE"
      run docker pull "$DOCKER_IMAGE"
    else
      exit 1
    fi
  fi
fi

SHIM_PATH="$BIN_DIR/$SHIM_NAME"
if [ -e "$SHIM_PATH" ] && ! confirm "Replace existing shim at $SHIM_PATH?"; then
  echo "Shim unchanged."
  exit 1
fi

echo "Writing shim: $SHIM_PATH"
if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$BIN_DIR"
  cat > "$SHIM_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export AI_OBSIDIAN_UID="\$(id -u)"
export AI_OBSIDIAN_GID="\$(id -g)"
if [ -n "\${DOCKER_HOST:-}" ] && [[ "\$DOCKER_HOST" == unix://* ]]; then
  export AI_OBSIDIAN_DOCKER_SOCKET="\${DOCKER_HOST#unix://}"
elif [ -S "\${HOME}/.docker/run/docker.sock" ]; then
  export AI_OBSIDIAN_DOCKER_SOCKET="\${HOME}/.docker/run/docker.sock"
else
  export AI_OBSIDIAN_DOCKER_SOCKET="/var/run/docker.sock"
fi
export AI_OBSIDIAN_DOCKER_IMAGE="$DOCKER_IMAGE"
exec docker compose -f "$ROOT_DIR/docker/compose.yaml" run --rm ai-obsidian "\$@"
EOF
  chmod +x "$SHIM_PATH"
fi

cat <<EOF

AI Obsidian Docker shim is ready.
Command:
  $SHIM_PATH --help
Image:
  $DOCKER_IMAGE

Recommended Docker runtime setup:
  $SHIM_PATH setup apply --profile <docker-profile.json> --yes
EOF
