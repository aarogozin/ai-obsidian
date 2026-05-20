# Docker Deployment Mode

Docker mode is a hybrid deployment path for users who want less Python/Homebrew state on the host.

It keeps these pieces native:

- Docker Desktop for Mac
- Obsidian
- the Obsidian vault on the host filesystem

It moves these pieces into Docker:

- AI Obsidian CLI/control-plane commands
- setup/doctor/repair orchestration
- optional voice transcription command execution through the Docker shim

Model inference is handled by Docker Model Runner, not by raw MLX inside a normal Linux container. On Apple Silicon, Docker Model Runner provides an OpenAI-compatible API at:

```text
http://localhost:12434/engines/v1
```

## One-Command Bootstrap

From a checkout:

```bash
scripts/docker-bootstrap.sh
```

The bootstrap:

- starts Docker Desktop if possible;
- enables Docker Model Runner TCP on port `12434`;
- pulls the published AI Obsidian control-plane image;
- installs `~/.local/bin/ai-obsidian-docker`;
- pulls the selected Docker model, defaulting to `ai/smollm2`;
- applies a Docker runtime setup profile;
- installs/configures the Obsidian plugins in the selected vault.

It does not install host oMLX, Hugging Face CLI, ffmpeg, or mlx-whisper.

Image selection is tag-aware:

- `AI_OBSIDIAN_DOCKER_IMAGE` overrides everything.
- exact `v*` git tag checkout uses the matching tag, for example `mrrogozin/obsidian-omlx:v0.2.1`;
- normal `main` or branch checkout uses `mrrogozin/obsidian-omlx:edge`;
- source archives without `.git` use the `pyproject.toml` version, for example `mrrogozin/obsidian-omlx:0.2.1`.

Useful non-interactive example:

```bash
scripts/docker-bootstrap.sh --yes --vault ~/Documents/Obsidian/Main --model ai/smollm2
```

For development from a checkout before a published image is available:

```bash
scripts/docker-bootstrap.sh --build-local
```

## Install Only the Docker Shim

From a checkout:

```bash
scripts/docker-install.sh --yes
```

This pulls the tag-aware Docker Hub image and writes:

```text
~/.local/bin/ai-obsidian-docker
```

If Docker Hub is unavailable, the installer tries the same tag in the GHCR mirror, for example `ghcr.io/aarogozin/ai-obsidian:v0.2.1`.

To use another image:

```bash
scripts/docker-install.sh --yes --image ghcr.io/aarogozin/ai-obsidian:latest
```

To build locally:

```bash
scripts/docker-install.sh --yes --build-local
```

The shim runs AI Obsidian through Docker Compose while bind-mounting your home directory so it can see vaults and write Obsidian plugin settings.

The container talks to Docker Model Runner through its OpenAI-compatible HTTP API. Model pulls still run on the host through `docker model pull`, because Docker Model Runner is a Docker Desktop host feature.

## Configure Docker Runtime

If you do not use the bootstrap, create a profile:

```json
{
  "runtime": { "mode": "docker-model-runner" },
  "omlx": {
    "selected_model": "ai/smollm2"
  },
  "vault": {
    "mode": "existing",
    "name": "Main",
    "path": "/Users/you/Documents/Obsidian/Main"
  },
  "chat": {
    "default_engine": "builtin"
  },
  "plugins": {
    "install_hub": true,
    "install_companion": true
  },
  "launch": {
    "start_stack": true,
    "open_obsidian": true
  }
}
```

Apply it:

```bash
ai-obsidian-docker setup apply --profile docker-profile.json --yes
```

## Daily Commands

```bash
ai-obsidian-docker docker status
ai-obsidian-docker models status
docker model pull ai/smollm2
ai-obsidian-docker stack start
ai-obsidian-docker plugin open
```

## Important Limits

- Docker mode is Docker-first, not Docker-only.
- Obsidian remains native because it is the desktop UI.
- Docker Model Runner must be enabled in Docker Desktop.
- Normal Linux containers should not be treated as a reliable way to access Apple Metal/MLX directly.
- The native oMLX path remains the default stable path until Docker mode has more field testing.
