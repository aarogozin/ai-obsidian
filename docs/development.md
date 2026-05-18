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
bash -n "scripts/AI Obsidian Installer.command"
scripts/install.sh --dry-run --yes --no-init --source-dir "$PWD"
```

## Build

```bash
.venv/bin/python -m build
```

## GitHub Actions

The repository includes GitHub Actions workflows for:

- Python tests on Linux and macOS;
- macOS installer dry-run smoke tests;
- tag-based release asset publishing.

CI does not perform real Homebrew, Obsidian, oMLX, or model installs. It uses dry-run and mocked installer paths.

## Release

Create a version tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The release workflow uploads:

- `install.sh`
- `AI Obsidian Installer.command`
- `ai-obsidian-<version>.tar.gz`
- `ai-obsidian.tar.gz`
- wheel and sdist artifacts
- `checksums.txt`
