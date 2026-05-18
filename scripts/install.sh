#!/usr/bin/env bash
set -euo pipefail

APP_NAME="AI Obsidian"
PROJECT_NAME="ai-obsidian"
DEFAULT_REPO="aarogozin/ai-obsidian"
INSTALL_DIR="${HOME}/.local/share/ai-obsidian"
BIN_DIR="${HOME}/.local/bin"
VERSION="latest"
ASSUME_YES=0
DRY_RUN=0
NO_INIT=0
SOURCE_DIR="${AI_OBSIDIAN_SOURCE_DIR:-}"
REPO="${AI_OBSIDIAN_REPO:-$DEFAULT_REPO}"

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Install AI Obsidian into a user-local virtualenv and expose `ai-obsidian` in PATH.

Options:
  --yes                 Answer yes to installer prompts.
  --dry-run             Print what would happen without changing the system.
  --install-dir PATH    Install directory. Default: ~/.local/share/ai-obsidian
  --bin-dir PATH        Shim directory. Default: ~/.local/bin
  --version VERSION     GitHub release version. Default: latest
  --source-dir PATH     Install from a local source checkout instead of GitHub.
  --no-init             Do not offer to run `ai-obsidian init` after install.
  -h, --help            Show this help.

Environment:
  AI_OBSIDIAN_REPO      GitHub repo, e.g. owner/repo. Default: aarogozin/ai-obsidian
  AI_OBSIDIAN_SOURCE_DIR
                        Local source checkout for testing or development installs.
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  log "Error: $*" >&2
  exit 1
}

expand_path() {
  local value="$1"
  case "$value" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${value#~/}" ;;
    *) printf '%s\n' "$value" ;;
  esac
}

run_cmd() {
  log "Running: $*"
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  "$@"
}

run_shell() {
  log "Running: $*"
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  /bin/bash -c "$*"
}

confirm() {
  local prompt="$1"
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  printf '%s [Y/n] ' "$prompt"
  read -r answer
  case "${answer:-y}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

parse_args() {
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
      --install-dir)
        [ "$#" -ge 2 ] || die "--install-dir requires a path"
        INSTALL_DIR="$(expand_path "$2")"
        shift 2
        ;;
      --bin-dir)
        [ "$#" -ge 2 ] || die "--bin-dir requires a path"
        BIN_DIR="$(expand_path "$2")"
        shift 2
        ;;
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value"
        VERSION="$2"
        shift 2
        ;;
      --source-dir)
        [ "$#" -ge 2 ] || die "--source-dir requires a path"
        SOURCE_DIR="$(expand_path "$2")"
        shift 2
        ;;
      --no-init)
        NO_INIT=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

uname_s() {
  printf '%s\n' "${AI_OBSIDIAN_TEST_UNAME_S:-$(uname -s)}"
}

uname_m() {
  printf '%s\n' "${AI_OBSIDIAN_TEST_UNAME_M:-$(uname -m)}"
}

macos_version() {
  if [ -n "${AI_OBSIDIAN_TEST_MACOS_VERSION:-}" ]; then
    printf '%s\n' "$AI_OBSIDIAN_TEST_MACOS_VERSION"
    return 0
  fi
  sw_vers -productVersion
}

detect_platform() {
  log "$APP_NAME installer"
  if [ "$(uname_s)" != "Darwin" ]; then
    die "AI Obsidian currently supports macOS only."
  fi
  if [ "$(uname_m)" != "arm64" ]; then
    die "AI Obsidian requires Apple Silicon (arm64)."
  fi

  local version major
  version="$(macos_version)"
  major="${version%%.*}"
  if [ "${major:-0}" -lt 15 ]; then
    die "AI Obsidian requires macOS 15 or newer. Detected: $version"
  fi
  log "Platform: macOS $version on Apple Silicon"
}

resolve_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
    return 0
  fi
  if [ "${AI_OBSIDIAN_TEST_NO_DEFAULT_BREW:-0}" = "1" ]; then
    return 1
  fi
  if [ -x /opt/homebrew/bin/brew ]; then
    printf '%s\n' /opt/homebrew/bin/brew
    return 0
  fi
  return 1
}

ensure_homebrew() {
  if BREW_BIN="$(resolve_brew)"; then
    log "Homebrew: $BREW_BIN"
    return 0
  fi

  log "Homebrew is missing."
  if ! confirm "Homebrew is missing. Install Homebrew now using the official installer?"; then
    die "Homebrew is required. Install it from https://brew.sh and re-run this installer."
  fi

  run_shell 'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  if [ "$DRY_RUN" -eq 1 ]; then
    BREW_BIN="/opt/homebrew/bin/brew"
    return 0
  fi
  BREW_BIN="$(resolve_brew)" || die "Homebrew install finished, but brew was not found at /opt/homebrew/bin/brew."
  log "Homebrew installed: $BREW_BIN"
}

python_ok() {
  local python_bin="$1"
  "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

resolve_python() {
  if command -v python3 >/dev/null 2>&1 && python_ok "$(command -v python3)"; then
    command -v python3
    return 0
  fi
  return 1
}

ensure_python() {
  if PYTHON_BIN="$(resolve_python)"; then
    log "Python: $PYTHON_BIN"
    return 0
  fi

  log "Python 3.10+ is missing or too old."
  run_cmd "$BREW_BIN" install python
  if [ "$DRY_RUN" -eq 1 ]; then
    PYTHON_BIN="/opt/homebrew/bin/python3"
    return 0
  fi
  PYTHON_BIN="$(resolve_python)" || die "Homebrew Python installed, but python3 3.10+ was not found."
  log "Python installed: $PYTHON_BIN"
}

download_release() {
  RELEASE_SRC_DIR="$INSTALL_DIR/src"
  if [ -n "$SOURCE_DIR" ]; then
    [ -d "$SOURCE_DIR" ] || die "Source directory does not exist: $SOURCE_DIR"
    log "Installing from local source: $SOURCE_DIR"
    RELEASE_SRC_DIR="$SOURCE_DIR"
    return 0
  fi

  local asset url tmp
  if [ "$VERSION" = "latest" ]; then
    asset="ai-obsidian.tar.gz"
    url="https://github.com/${REPO}/releases/latest/download/${asset}"
  else
    asset="ai-obsidian-${VERSION#v}.tar.gz"
    url="https://github.com/${REPO}/releases/download/${VERSION}/${asset}"
  fi

  log "Downloading release: $url"
  if [ "$DRY_RUN" -eq 1 ]; then
    RELEASE_SRC_DIR="$INSTALL_DIR/src"
    return 0
  fi

  tmp="$(mktemp -d)"
  mkdir -p "$INSTALL_DIR/src"
  curl -fL "$url" -o "$tmp/$asset"
  tar -xzf "$tmp/$asset" --strip-components=1 -C "$INSTALL_DIR/src"
  rm -rf "$tmp"
  RELEASE_SRC_DIR="$INSTALL_DIR/src"
}

create_venv() {
  VENV_DIR="$INSTALL_DIR/.venv"
  if [ -x "$VENV_DIR/bin/python" ]; then
    log "Virtualenv already exists: $VENV_DIR"
    return 0
  fi
  run_cmd mkdir -p "$INSTALL_DIR"
  run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"
}

install_package() {
  log "Installing Python package into $VENV_DIR"
  run_cmd "$VENV_DIR/bin/python" -m pip install --upgrade pip
  run_cmd "$VENV_DIR/bin/python" -m pip install "$RELEASE_SRC_DIR"
}

install_shim() {
  local shim="$BIN_DIR/ai-obsidian"
  run_cmd mkdir -p "$BIN_DIR"
  log "Writing PATH shim: $shim"
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  cat > "$shim" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/ai-obsidian" "\$@"
EOF
  chmod +x "$shim"
}

print_next_steps() {
  log ""
  log "AI Obsidian is installed."
  log "Command: $BIN_DIR/ai-obsidian"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      log ""
      log "$BIN_DIR is not in PATH."
      log "Add it for zsh with:"
      log "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc"
      log "  source ~/.zshrc"
      ;;
  esac
  log ""
  log "Next command:"
  log "  ai-obsidian init"
}

maybe_run_init() {
  if [ "$NO_INIT" -eq 1 ]; then
    return 0
  fi
  if confirm "Run ai-obsidian init now?"; then
    run_cmd "$BIN_DIR/ai-obsidian" init
  fi
}

main() {
  parse_args "$@"
  detect_platform
  ensure_homebrew
  ensure_python
  download_release
  create_venv
  install_package
  install_shim
  print_next_steps
  maybe_run_init
}

main "$@"
