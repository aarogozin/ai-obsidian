# AI Obsidian

AI Obsidian is a Mac-centered local-first installer and launcher for an Obsidian workspace powered by oMLX and Apple Silicon MLX models.

It helps you set up:

- Obsidian
- oMLX as a local OpenAI-compatible model server
- RAM-safe local MLX model selection
- Obsidian vault registration or creation
- vault-level AI behavior instructions through `soul.md`
- Local LLM Hub inside Obsidian
- AI Obsidian Companion push-to-talk voice input inside Obsidian
- a terminal chat fallback over Markdown notes
- optional Hermes and Claude Code terminal chat providers

The project is currently focused on Apple Silicon Macs. Linux is used only for fast CI checks.

## Requirements

- Apple Silicon Mac
- macOS 15 or newer
- Internet access for first install and model/plugin downloads
- Homebrew-compatible setup

If Homebrew is missing, the installer asks before running the official Homebrew installer.

## Install

### GUI Installer

The recommended mouse-first install path is the macOS GUI installer published as a GitHub Release artifact:

```text
AI-Obsidian-Installer-macos-arm64.dmg
```

Download it from the latest release, open the app, and follow the wizard. It installs the same CLI backend, has a dedicated button for core stack dependencies (Homebrew, Obsidian, oMLX, Hugging Face CLI, ffmpeg, and mlx-whisper), lets you choose vault and model locations with native pickers, configures Obsidian plugins, starts oMLX, and can open Obsidian at the end. The window stays open after each action and keeps a copyable command log for troubleshooting.

Hermes is optional. The GUI includes a separate `Install Hermes CLI` action, then you can run `hermes setup` if Hermes needs provider/API-key configuration.

The GUI installer is unsigned in the first release. macOS may require right-click -> Open or System Settings -> Privacy & Security -> Open Anyway. Developer ID signing and notarization are planned after the release flow is stable.

### CLI Installer

The intended user install path is the GitHub Release installer:

```bash
curl -fsSL https://github.com/aarogozin/ai-obsidian/releases/latest/download/install.sh | bash
```

The installer writes AI Obsidian to:

```text
~/.local/share/ai-obsidian
```

and creates this PATH shim:

```text
~/.local/bin/ai-obsidian
```

If `~/.local/bin` is not in your `PATH`, the installer prints the exact `.zshrc` line to add.

Useful installer options:

```bash
curl -fsSL https://github.com/aarogozin/ai-obsidian/releases/latest/download/install.sh | bash -s -- --yes
curl -fsSL https://github.com/aarogozin/ai-obsidian/releases/latest/download/install.sh | bash -s -- --dry-run
curl -fsSL https://github.com/aarogozin/ai-obsidian/releases/latest/download/install.sh | bash -s -- --install-dir ~/.local/share/ai-obsidian --bin-dir ~/.local/bin
```

## Quickstart

After install:

```bash
ai-obsidian init
ai-obsidian stack start
ai-obsidian plugin open
```

For a development checkout:

```bash
./ai-obsidian init
./ai-obsidian stack start
./ai-obsidian plugin open
```

Running `ai-obsidian` or `./ai-obsidian` with no arguments opens an interactive menu.

## What Init Does

`ai-obsidian init` is the main first-run flow:

1. checks Apple Silicon, macOS, Homebrew, Obsidian, oMLX, Hugging Face CLI, ffmpeg, and mlx-whisper;
2. installs missing prerequisites with visible native command output;
3. asks how oMLX should run;
4. asks where models and vaults live;
5. creates `soul.md` vault instructions when missing;
6. shows already downloaded MLX models before remote choices;
7. filters remote model suggestions by detected Mac memory;
8. installs and configures the Obsidian AI plugin and optional push-to-talk companion plugin;
9. offers to start the stack and open Obsidian.

The default Obsidian plugin is [Local LLM Hub](https://github.com/takeshy/obsidian-local-llm-hub). Local LLM Helper is kept as a fallback integration.

The bundled AI Obsidian Companion plugin adds a microphone ribbon button and `AI Obsidian: Push to Talk` command. It records audio inside Obsidian, calls local `ai-obsidian voice transcribe`, previews the transcript, and inserts it into a focused Obsidian chat input when available. If no chat input is focused, it falls back to the active note after confirmation.

## Daily Commands

```bash
ai-obsidian
ai-obsidian doctor
ai-obsidian doctor --json
ai-obsidian repair
ai-obsidian setup status --json
ai-obsidian setup models --json
ai-obsidian stack status
ai-obsidian soul status
ai-obsidian soul init
ai-obsidian soul show
ai-obsidian models downloaded
ai-obsidian models use
ai-obsidian plugin status
ai-obsidian plugin configure
ai-obsidian plugin install --plugin companion
ai-obsidian plugin verify --plugin companion
ai-obsidian voice transcribe recording.webm --language auto
ai-obsidian chat
ai-obsidian chat --engine hermes --once "Summarize my loaded vault context"
ai-obsidian chat --engine claude --once "Suggest a structure for these notes"
```

`chat` is a terminal fallback. The primary v1 UI is the Obsidian plugin.

Inside Obsidian, use the microphone ribbon icon or Command Palette -> `AI Obsidian: Push to Talk` to dictate Russian or English text into the focused chat input or current Markdown note.

Inside terminal chat:

- `/files` lists loaded Markdown notes.
- `/read <note.md>` prints a note.
- `/edit <note.md> <instruction>` shows a diff and writes only after confirmation.
- `/exit` leaves the chat.

`--engine builtin` is the default and uses local oMLX. `--engine hermes` and `--engine claude` call the installed Hermes or Claude Code CLI in one-shot mode. External engines are read-only inside AI Obsidian: `/files`, `/read`, and `/exit` work, but `/edit` is intentionally available only with `--engine builtin`.

## Guides

- [Vault Soul instructions](docs/vault-soul.md)
- [Model recommendations for Apple Silicon](docs/models.md)
- [Development and release notes](docs/development.md)

## Safety

AI Obsidian is designed to be conservative with user data:

- it does not delete Obsidian vaults;
- it does not overwrite notes silently;
- it never overwrites an existing `soul.md`;
- creating a vault only creates the folder and `.obsidian` directory;
- note edits require diff review and explicit confirmation;
- voice transcripts require confirmation before insertion by default;
- external terminal engines receive note context only and do not get direct AI Obsidian vault write access;
- existing registered vaults are preserved across `init`;
- plugin settings are shown as a diff before writing;
- existing plugin `data.json` files are backed up before confirmed writes;
- local config is stored at `~/.ai-obsidian/config.json` with `0600` permissions.

## Troubleshooting

### `ai-obsidian` is not found

Add the installer shim directory to your shell:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Then check:

```bash
ai-obsidian --help
```

### Homebrew is missing

Run:

```bash
ai-obsidian install --execute
```

Interactive flows ask before installing Homebrew. Non-interactive install can allow Homebrew bootstrap with:

```bash
ai-obsidian install --execute --yes
```

Optional Hermes Agent CLI support can be installed separately:

```bash
ai-obsidian install --execute --yes --only-hermes
```

### oMLX is not ready

Check:

```bash
ai-obsidian doctor
ai-obsidian service status
ai-obsidian stack status
```

If port `8000` is already in use, stop the other oMLX process or configure AI Obsidian to use that running server.

### Obsidian chat lost the model

Run the safe repair flow:

```bash
ai-obsidian repair
ai-obsidian stack status
```

`repair` syncs the Obsidian plugin settings with the active oMLX served model id, refreshes bundled companion plugin assets, and backs up plugin settings before writing. It does not modify Markdown notes.

### The selected model is not visible

Run:

```bash
ai-obsidian models status
ai-obsidian models downloaded
ai-obsidian models use
```

The picker shows downloaded MLX models first, then models served by oMLX.

### Obsidian opens but the plugin is missing

Run:

```bash
ai-obsidian plugin install
ai-obsidian plugin configure
ai-obsidian plugin open
```

Then use Obsidian's ribbon or Command Palette to open Local LLM Hub.

### Push-to-talk is missing or transcription fails

Install and configure the bundled companion plugin:

```bash
ai-obsidian plugin install --plugin companion
ai-obsidian plugin configure --plugin companion
ai-obsidian plugin verify --plugin companion
ai-obsidian plugin open
```

If transcription fails, check the local voice dependencies:

```bash
ai-obsidian install --execute
ai-obsidian voice transcribe /path/to/audio.webm --language ru
```

## Development

See [docs/development.md](docs/development.md).
