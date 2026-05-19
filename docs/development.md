# Development and Release

Clone the repo and run the root launcher:

```bash
./ai-obsidian --help
```

The launcher creates `.venv` and installs the local package automatically.

## Manual Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,release]'
.venv/bin/python -m pytest -q
```

## Installer Smoke Checks

```bash
bash -n scripts/install.sh
bash -n scripts/build-macos-installer.sh
bash -n "scripts/AI Obsidian Installer.command"
scripts/install.sh --dry-run --yes --no-init --source-dir "$PWD"
```

## Build

```bash
.venv/bin/python -m build
```

## macOS GUI Installer

The native installer lives in `macos/installer` and is intentionally a thin SwiftUI wrapper over the CLI setup API. Keep it mouse-first and lightweight: visible wizard steps, native folder pickers, copyable command log, and no duplicated install/model/plugin logic in Swift.

Build it on macOS:

```bash
scripts/build-macos-installer.sh
```

The script creates:

- `release/AI-Obsidian-Installer-macos-arm64.dmg`
- `release/AI-Obsidian-Installer-macos-arm64.zip`

The first GUI artifact is ad-hoc signed only. It is suitable for testing and GitHub Release distribution with Gatekeeper instructions. Developer ID signing and notarization should be added later with Apple Developer credentials and GitHub Actions secrets.

The GUI should call:

```bash
scripts/install.sh --yes --no-init
ai-obsidian install --execute --yes
ai-obsidian install --execute --yes --only-hermes
ai-obsidian setup status --json
ai-obsidian setup models --json
ai-obsidian setup apply --profile profile.json --yes
```

Do not duplicate setup behavior in Swift. Add backend behavior to the Python CLI first, then call it from the app. Optional external engines such as Hermes should stay explicit user actions, separate from the required Obsidian/oMLX stack.

## GitHub Actions

The repository includes GitHub Actions workflows for:

- Python tests on Linux and macOS;
- macOS installer dry-run smoke tests;
- tag-based release asset publishing.

CI does not perform real Homebrew, Obsidian, oMLX, or model installs. It uses dry-run and mocked installer paths.

## Release

Create a version tag:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The release workflow uploads:

- `install.sh`
- `AI Obsidian Installer.command`
- `AI-Obsidian-Installer-macos-arm64.dmg`
- `AI-Obsidian-Installer-macos-arm64.zip`
- `ai-obsidian-<version>.tar.gz`
- `ai-obsidian.tar.gz`
- wheel and sdist artifacts
- `checksums.txt`
