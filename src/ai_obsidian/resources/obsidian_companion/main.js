const { MarkdownView, Notice, Plugin, PluginSettingTab, Setting } = require("obsidian");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const DEFAULT_SETTINGS = {
  cliPath: "ai-obsidian",
  language: "auto",
  targetMode: "smart",
  insertMode: "cursor",
  confirmBeforeInsert: true
};

module.exports = class AIObsidianCompanionPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.recorder = null;
    this.stream = null;
    this.chunks = [];
    this.ribbonIcon = this.addRibbonIcon("mic", "AI Obsidian: Push to Talk", () => this.toggleRecording());
    this.ribbonIcon.classList.add("ai-obsidian-recording");

    this.addCommand({
      id: "push-to-talk",
      name: "Push to Talk",
      callback: () => this.toggleRecording()
    });

    this.addSettingTab(new AIObsidianCompanionSettingTab(this.app, this));
  }

  async onunload() {
    await this.stopRecording();
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async toggleRecording() {
    if (this.recorder && this.recorder.state === "recording") {
      await this.stopRecording();
      return;
    }
    await this.startRecording();
  }

  async startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
      new Notice("AI Obsidian: microphone recording is not available in this Obsidian environment.");
      return;
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.chunks = [];
      this.recorder = new MediaRecorder(this.stream);
      this.recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          this.chunks.push(event.data);
        }
      };
      this.recorder.onstop = () => this.handleRecordingStopped();
      this.recorder.start();
      this.setRecordingState(true);
      new Notice("AI Obsidian: recording. Press the microphone again to stop.");
    } catch (error) {
      new Notice(`AI Obsidian: could not access microphone: ${error.message || error}`);
      this.cleanupStream();
    }
  }

  async stopRecording() {
    if (this.recorder && this.recorder.state === "recording") {
      this.recorder.stop();
      return;
    }
    this.cleanupStream();
    this.setRecordingState(false);
  }

  async handleRecordingStopped() {
    this.setRecordingState(false);
    this.cleanupStream();

    if (!this.chunks.length) {
      new Notice("AI Obsidian: no audio was recorded.");
      return;
    }

    try {
      const audioPath = await this.writeRecording();
      new Notice("AI Obsidian: transcribing...");
      const transcript = await this.transcribe(audioPath);
      await this.deliverTranscript(transcript);
      fs.rm(audioPath, { force: true }, () => {});
    } catch (error) {
      new Notice(`AI Obsidian: ${error.message || error}`);
    } finally {
      this.chunks = [];
      this.recorder = null;
    }
  }

  cleanupStream() {
    if (this.stream) {
      for (const track of this.stream.getTracks()) {
        track.stop();
      }
      this.stream = null;
    }
  }

  setRecordingState(isRecording) {
    if (!this.ribbonIcon) {
      return;
    }
    this.ribbonIcon.classList.toggle("is-active", isRecording);
    this.ribbonIcon.setAttribute("aria-label", isRecording ? "AI Obsidian: stop recording" : "AI Obsidian: push to talk");
  }

  async writeRecording() {
    const blob = new Blob(this.chunks, { type: this.chunks[0].type || "audio/webm" });
    const buffer = Buffer.from(await blob.arrayBuffer());
    const dir = path.join(this.vaultBasePath(), this.app.vault.configDir, "plugins", this.manifest.id, "tmp");
    await fs.promises.mkdir(dir, { recursive: true });
    const file = path.join(dir, `recording-${Date.now()}.webm`);
    await fs.promises.writeFile(file, buffer);
    return file;
  }

  vaultBasePath() {
    const adapter = this.app.vault.adapter;
    if (adapter && typeof adapter.getBasePath === "function") {
      return adapter.getBasePath();
    }
    throw new Error("desktop vault path is not available");
  }

  transcribe(audioPath) {
    return new Promise((resolve, reject) => {
      const args = ["voice", "transcribe", audioPath, "--language", this.settings.language];
      const child = spawn(this.settings.cliPath || "ai-obsidian", args, {
        cwd: this.vaultBasePath(),
        env: process.env
      });
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });
      child.on("error", reject);
      child.on("close", (code) => {
        if (code !== 0) {
          reject(new Error(stderr.trim() || stdout.trim() || `transcription exited with ${code}`));
          return;
        }
        const text = stdout.trim();
        if (!text) {
          reject(new Error("transcription returned no text"));
          return;
        }
        resolve(text);
      });
    });
  }

  async deliverTranscript(transcript) {
    if (this.settings.confirmBeforeInsert) {
      const preview = transcript.length > 160 ? `${transcript.slice(0, 160)}...` : transcript;
      if (!window.confirm(`Insert this transcript?\n\n${preview}`)) {
        new Notice("AI Obsidian: transcript was not inserted.");
        return;
      }
    }

    if (this.settings.targetMode === "note") {
      await this.insertTranscriptIntoNote(transcript);
      return;
    }

    if (this.settings.targetMode === "chat") {
      if (!this.insertIntoChatInput(transcript)) {
        throw new Error("open and focus the Obsidian chat input first");
      }
      new Notice("AI Obsidian: transcript inserted into chat input.");
      return;
    }

    if (this.insertIntoChatInput(transcript)) {
      new Notice("AI Obsidian: transcript inserted into chat input.");
      return;
    }
    await this.insertTranscriptIntoNote(transcript);
  }

  insertIntoChatInput(transcript) {
    const input = this.findChatInput();
    if (!input) {
      return false;
    }
    input.focus();

    if (input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {
      const start = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
      const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : input.value.length;
      const before = input.value.slice(0, start);
      const after = input.value.slice(end);
      const nextValue = `${before}${transcript}${after}`;
      const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) {
        setter.call(input, nextValue);
      } else {
        input.value = nextValue;
      }
      const cursor = before.length + transcript.length;
      input.setSelectionRange(cursor, cursor);
      this.dispatchInputEvents(input);
      return true;
    }

    if (input.isContentEditable) {
      document.execCommand("insertText", false, transcript);
      this.dispatchInputEvents(input);
      return true;
    }
    return false;
  }

  findChatInput() {
    const active = document.activeElement;
    if (this.isUsableChatInput(active)) {
      return active;
    }

    const activeLeaf = this.app.workspace.activeLeaf?.view?.containerEl;
    const searchRoots = [activeLeaf, this.app.workspace.containerEl, document.body].filter(Boolean);
    for (const root of searchRoots) {
      const candidates = Array.from(root.querySelectorAll("textarea, input[type='text'], [contenteditable='true']"));
      const candidate = candidates.find((element) => this.isUsableChatInput(element));
      if (candidate) {
        return candidate;
      }
    }
    return null;
  }

  isUsableChatInput(element) {
    if (!element || !(element instanceof HTMLElement)) {
      return false;
    }
    if (element.closest(".workspace-leaf-content[data-type='markdown']")) {
      return false;
    }
    if (element.matches("input[type='text'], textarea") && element.disabled) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  dispatchInputEvents(element) {
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: null }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async insertTranscriptIntoNote(transcript) {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view || !view.editor) {
      throw new Error("open a Markdown note before inserting voice text");
    }

    const editor = view.editor;
    if (this.settings.insertMode === "append") {
      const lastLine = editor.lastLine();
      const suffix = editor.getLine(lastLine).trim() ? "\n\n" : "";
      editor.setCursor({ line: lastLine, ch: editor.getLine(lastLine).length });
      editor.replaceRange(`${suffix}${transcript}\n`, editor.getCursor());
    } else {
      editor.replaceSelection(transcript);
    }
    new Notice("AI Obsidian: transcript inserted into note.");
  }
};

class AIObsidianCompanionSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "AI Obsidian Companion" });

    new Setting(containerEl)
      .setName("AI Obsidian CLI path")
      .setDesc("Command used for local transcription.")
      .addText((text) =>
        text
          .setPlaceholder("ai-obsidian")
          .setValue(this.plugin.settings.cliPath)
          .onChange(async (value) => {
            this.plugin.settings.cliPath = value.trim() || "ai-obsidian";
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Language")
      .setDesc("Speech recognition language hint.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("auto", "Auto")
          .addOption("ru", "Russian")
          .addOption("en", "English")
          .setValue(this.plugin.settings.language)
          .onChange(async (value) => {
            this.plugin.settings.language = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Target")
      .setDesc("Where push-to-talk text should go.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("smart", "Smart: chat input if active, otherwise note")
          .addOption("note", "Note")
          .addOption("chat", "Chat input")
          .setValue(this.plugin.settings.targetMode)
          .onChange(async (value) => {
            this.plugin.settings.targetMode = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Insert mode")
      .setDesc("Where transcribed text should go.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("cursor", "At cursor")
          .addOption("append", "Append to note")
          .setValue(this.plugin.settings.insertMode)
          .onChange(async (value) => {
            this.plugin.settings.insertMode = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Confirm before insert")
      .setDesc("Preview the transcript before modifying the note.")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.confirmBeforeInsert)
          .onChange(async (value) => {
            this.plugin.settings.confirmBeforeInsert = value;
            await this.plugin.saveSettings();
          })
      );
  }
}
