#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -x "$SCRIPT_DIR/install.sh" ]; then
  exec "$SCRIPT_DIR/install.sh" "$@"
fi

REPO="${AI_OBSIDIAN_REPO:-aarogozin/ai-obsidian}"
curl -fsSL "https://github.com/${REPO}/releases/latest/download/install.sh" | /bin/bash
