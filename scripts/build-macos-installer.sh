#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${AI_OBSIDIAN_VERSION:-0.2.0}"
BUILD_DIR="${ROOT}/build/macos-installer"
RELEASE_DIR="${ROOT}/release"
APP_NAME="AI Obsidian Installer"
APP_DIR="${BUILD_DIR}/${APP_NAME}.app"
EXECUTABLE="AIObsidianInstaller"
SOURCE="${ROOT}/macos/installer/Sources/AIObsidianInstaller.swift"
BUNDLED_ARCHIVE="${APP_DIR}/Contents/Resources/ai-obsidian-bundled.tar.gz"

log() {
  printf '%s\n' "$*"
}

require_macos() {
  if [ "$(uname -s)" != "Darwin" ]; then
    log "Skipping macOS installer build: this script requires macOS."
    exit 0
  fi
  if ! command -v swiftc >/dev/null 2>&1; then
    log "Skipping macOS installer build: swiftc was not found."
    exit 0
  fi
}

write_info_plist() {
  cat > "${APP_DIR}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>${EXECUTABLE}</string>
  <key>CFBundleIdentifier</key>
  <string>dev.ai-obsidian.installer</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key>
  <string>15.0</string>
  <key>NSHumanReadableCopyright</key>
  <string>Copyright © 2026 AI Obsidian contributors</string>
</dict>
</plist>
EOF
}

build_app() {
  rm -rf "$BUILD_DIR"
  mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources" "$RELEASE_DIR"
  write_info_plist
  cp "${ROOT}/scripts/install.sh" "${APP_DIR}/Contents/Resources/install.sh"
  chmod +x "${APP_DIR}/Contents/Resources/install.sh"
  bundle_source_archive

  log "Building ${APP_NAME}.app"
  swiftc \
    -target arm64-apple-macosx15.0 \
    -O \
    -parse-as-library \
    "$SOURCE" \
    -o "${APP_DIR}/Contents/MacOS/${EXECUTABLE}"

  codesign --force --deep --sign - "$APP_DIR"
}

bundle_source_archive() {
  local staging
  staging="$(mktemp -d)"
  mkdir -p "${staging}/ai-obsidian"
  rsync -a \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".pytest_cache/" \
    --exclude "__pycache__/" \
    --exclude "build/" \
    --exclude "release/" \
    --exclude "dist/" \
    --exclude "*.egg-info/" \
    --exclude ".DS_Store" \
    "${ROOT}/" "${staging}/ai-obsidian/"
  tar -czf "$BUNDLED_ARCHIVE" -C "$staging" ai-obsidian
  rm -rf "$staging"
}

package_app() {
  local zip_path="${RELEASE_DIR}/AI-Obsidian-Installer-macos-arm64.zip"
  local dmg_path="${RELEASE_DIR}/AI-Obsidian-Installer-macos-arm64.dmg"
  local dmg_staging="${BUILD_DIR}/dmg"

  detach_existing_dmg
  rm -f "$zip_path" "$dmg_path"
  log "Creating $zip_path"
  ditto -c -k --keepParent "$APP_DIR" "$zip_path"

  log "Creating $dmg_path"
  rm -rf "$dmg_staging"
  mkdir -p "$dmg_staging"
  cp -R "$APP_DIR" "$dmg_staging/"
  hdiutil create \
    -volname "AI Obsidian Installer" \
    -fs HFS+ \
    -srcfolder "$dmg_staging" \
    -ov \
    -format UDZO \
    "$dmg_path"
}

detach_existing_dmg() {
  local volume="/Volumes/AI Obsidian Installer"
  if [ ! -e "$volume" ]; then
    return 0
  fi
  log "Detaching existing mounted installer volume"
  hdiutil detach "$volume" >/dev/null 2>&1 || hdiutil detach -force "$volume" >/dev/null 2>&1 || true
}

require_macos
build_app
package_app
log "Built macOS installer artifacts in $RELEASE_DIR"
