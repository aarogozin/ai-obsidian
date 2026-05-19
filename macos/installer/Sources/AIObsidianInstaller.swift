import AppKit
import Foundation
import SwiftUI

struct SetupProfile: Codable {
    struct Omlx: Codable {
        var mode: String
        var api_key: String
        var model_dir: String
        var selected_model: String
    }

    struct Vault: Codable {
        var mode: String
        var name: String
        var path: String
    }

    struct Chat: Codable {
        var default_engine: String
    }

    struct Plugins: Codable {
        var install_hub: Bool
        var install_companion: Bool
    }

    struct Launch: Codable {
        var start_stack: Bool
        var open_obsidian: Bool
    }

    var omlx: Omlx
    var vault: Vault
    var chat: Chat
    var plugins: Plugins
    var launch: Launch
}

@MainActor
final class InstallerState: ObservableObject {
    @Published var step = 0
    @Published var log = "Ready.\n"
    @Published var isRunning = false
    @Published var runState = "Ready"
    @Published var statusText = "CLI is not installed yet."
    @Published var lastError = ""
    @Published var installDir = "\(NSHomeDirectory())/.local/share/ai-obsidian"
    @Published var binDir = "\(NSHomeDirectory())/.local/bin"
    @Published var vaultMode = "create"
    @Published var vaultName = "Main"
    @Published var vaultPath = "\(NSHomeDirectory())/Documents/Obsidian/Main"
    @Published var modelDir = "\(NSHomeDirectory())/.omlx/models"
    @Published var selectedModel = "mlx-community/Qwen3-1.7B-4bit"
    @Published var omlxMode = "service"
    @Published var apiKey = ""
    @Published var chatEngine = "builtin"
    @Published var installHub = true
    @Published var installCompanion = true
    @Published var startStack = true
    @Published var openObsidian = true
    @Published var downloadedModels: [String] = []
    @Published var remoteModels: [String] = []

    let installerScript: String
    let bundledArchive: String?

    init() {
        if let resource = Bundle.main.path(forResource: "install", ofType: "sh") {
            installerScript = resource
        } else {
            installerScript = "\(FileManager.default.currentDirectoryPath)/scripts/install.sh"
        }
        bundledArchive = Bundle.main.path(forResource: "ai-obsidian-bundled", ofType: "tar.gz")
    }

    var cliPath: String {
        "\(binDir)/ai-obsidian"
    }

    var releaseVersion: String {
        guard let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String else {
            return "latest"
        }
        if version == "latest" || version.hasPrefix("v") {
            return version
        }
        return "v\(version)"
    }

    var subprocessEnvironment: [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let existingPath = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        let additions = [
            "\(NSHomeDirectory())/.local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        environment["PATH"] = (additions + [existingPath]).joined(separator: ":")
        return environment
    }

    var canApply: Bool {
        !vaultPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !selectedModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !modelDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var stepTitle: String {
        Self.steps[step].title
    }

    static let steps: [(title: String, detail: String)] = [
        ("1. Install CLI & Dependencies", "Install the local command and required stack dependencies."),
        ("2. Choose Vault", "Pick where your Obsidian vault should live."),
        ("3. Choose Model", "Select an existing local MLX model or enter a model id."),
        ("4. Chat & Plugins", "Choose terminal chat and Obsidian plugin options."),
        ("5. Apply", "Review and run the setup."),
    ]

    func appendLog(_ text: String) {
        log += text
        if !text.hasSuffix("\n") {
            log += "\n"
        }
    }

    func copyLog() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(log, forType: .string)
        statusText = "Command log copied."
    }

    func goBack() {
        step = max(0, step - 1)
    }

    func goNext() {
        step = min(Self.steps.count - 1, step + 1)
    }

    func installCLI() {
        var arguments = [
            installerScript,
            "--yes",
            "--no-init",
            "--install-dir",
            installDir,
            "--bin-dir",
            binDir,
        ]
        if let archive = bundledArchive {
            arguments.append(contentsOf: ["--archive", archive])
        } else {
            arguments.append(contentsOf: ["--version", releaseVersion])
        }
        run(
            executable: "/bin/bash",
            arguments: arguments,
            successMessage: "CLI installed."
        ) {
            self.statusText = "CLI installed at \(self.cliPath)"
            self.loadStatus()
            self.loadModels()
        }
    }

    func installDependencies() {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            fail("Install the CLI before installing dependencies.")
            return
        }
        run(
            executable: cliPath,
            arguments: ["install", "--execute", "--yes"],
            successMessage: "Core dependencies installed or already available."
        ) {
            self.statusText = "Core dependencies installed or already available."
            self.loadStatus()
        }
    }

    func installHermes() {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            fail("Install the CLI before installing Hermes.")
            return
        }
        run(
            executable: cliPath,
            arguments: ["install", "--execute", "--yes", "--only-hermes"],
            successMessage: "Hermes installation finished."
        ) {
            self.statusText = "Hermes installation finished. Run hermes setup later if provider configuration is needed."
            self.loadStatus()
        }
    }

    func loadStatus() {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            runState = "Ready"
            statusText = "CLI is not installed yet."
            return
        }
        run(
            executable: cliPath,
            arguments: ["setup", "status", "--json"],
            successMessage: "Setup status refreshed.",
            showOutputInLog: true
        )
    }

    func loadModels() {
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            fail("Install the CLI before loading model suggestions.")
            return
        }
        let command = [cliPath, "setup", "models", "--json", "--model-dir", modelDir]
        runCapturingJSON(executable: command[0], arguments: Array(command.dropFirst())) { data in
            guard
                let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else {
                self.appendLog("Could not parse model suggestions.")
                return
            }
            let downloadedRows = json["downloaded"] as? [[String: Any]] ?? []
            let downloaded = Self.uniqueModelIds(
                downloadedRows
                    .filter { ($0["format"] as? String) == "MLX" }
                    .compactMap { $0["id"] as? String }
            )
            let remote = (json["remote"] as? [[String: Any]] ?? []).compactMap { $0["repo_id"] as? String }
            let otherLocalCount = max(0, downloadedRows.count - downloaded.count)
            self.downloadedModels = downloaded
            self.remoteModels = remote
            if self.selectedModel.isEmpty {
                self.selectedModel = downloaded.first ?? remote.first ?? self.selectedModel
            }
            self.runState = "Complete"
            self.lastError = ""
            self.statusText = "Model choices loaded."
            self.appendLog("Loaded \(downloaded.count) direct MLX models, ignored \(otherLocalCount) Ollama/GGUF/duplicate local entries, and loaded \(remote.count) remote suggestions.")
        }
    }

    static func uniqueModelIds(_ ids: [String]) -> [String] {
        var seen = Set<String>()
        var unique: [String] = []
        for id in ids {
            if seen.insert(id).inserted {
                unique.append(id)
            }
        }
        return unique
    }

    func applySetup() {
        guard canApply else {
            fail("Vault path, model directory, and selected model are required.")
            return
        }
        guard FileManager.default.isExecutableFile(atPath: cliPath) else {
            fail("Install the CLI before applying setup.")
            return
        }
        do {
            let profile = SetupProfile(
                omlx: .init(mode: omlxMode, api_key: apiKey, model_dir: modelDir, selected_model: selectedModel),
                vault: .init(mode: vaultMode, name: vaultName, path: vaultPath),
                chat: .init(default_engine: chatEngine),
                plugins: .init(install_hub: installHub, install_companion: installCompanion),
                launch: .init(start_stack: startStack, open_obsidian: openObsidian)
            )
            let data = try JSONEncoder().encode(profile)
            let profileURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("ai-obsidian-setup-profile-\(UUID().uuidString).json")
            try data.write(to: profileURL)
            run(
                executable: cliPath,
                arguments: ["setup", "apply", "--profile", profileURL.path, "--yes"],
                successMessage: "AI Obsidian setup complete."
            )
        } catch {
            fail("Could not write setup profile: \(error.localizedDescription)")
        }
    }

    func chooseDirectory(assign: @escaping (String) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            assign(url.path)
        }
    }

    private func run(
        executable: String,
        arguments: [String],
        successMessage: String,
        showOutputInLog: Bool = true,
        onSuccess: (() -> Void)? = nil
    ) {
        beginCommand()
        appendLog("$ \(executable) \(arguments.joined(separator: " "))")
        runProcess(executable: executable, arguments: arguments) { line in
            if showOutputInLog {
                self.appendLog(line)
            }
        } completion: { code in
            self.isRunning = false
            if code == 0 {
                self.runState = "Complete"
                self.lastError = ""
                self.statusText = successMessage
                self.appendLog(successMessage)
                onSuccess?()
            } else {
                self.fail("Command failed with exit code \(code).")
            }
        }
    }

    private func runCapturingJSON(
        executable: String,
        arguments: [String],
        completion: @escaping (Data) -> Void
    ) {
        beginCommand()
        appendLog("$ \(executable) \(arguments.joined(separator: " "))")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = subprocessEnvironment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
        } catch {
            fail("Could not start command: \(error.localizedDescription)")
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                self.isRunning = false
                if process.terminationStatus == 0 {
                    completion(data)
                } else {
                    let detail = String(data: data, encoding: .utf8) ?? "Command failed."
                    self.fail(self.friendlyCommandError(detail))
                }
            }
        }
    }

    private func runProcess(
        executable: String,
        arguments: [String],
        onLine: @escaping (String) -> Void,
        completion: @escaping (Int32) -> Void
    ) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = subprocessEnvironment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                onLine(text)
            }
        }
        process.terminationHandler = { process in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                completion(process.terminationStatus)
            }
        }
        do {
            try process.run()
        } catch {
            fail("Could not start command: \(error.localizedDescription)")
        }
    }

    private func beginCommand() {
        isRunning = true
        runState = "Running"
        lastError = ""
        statusText = "Running command..."
    }

    private func fail(_ message: String) {
        isRunning = false
        runState = "Failed"
        lastError = message
        statusText = message
        appendLog(message)
    }

    private func friendlyCommandError(_ output: String) -> String {
        if output.contains("invalid choice: 'setup'") {
            return "Installed CLI is too old for this installer. Click Install / Update CLI, then retry."
        }
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Command failed." : trimmed
    }
}

struct ContentView: View {
    @StateObject private var state = InstallerState()

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(state: state)

            TabView(selection: $state.step) {
                WelcomeView(state: state).tag(0).tabItem { Text("1. CLI") }
                PathsView(state: state).tag(1).tabItem { Text("2. Vault") }
                ModelView(state: state).tag(2).tabItem { Text("3. Model") }
                ChatView(state: state).tag(3).tabItem { Text("4. Chat") }
                ReviewView(state: state).tag(4).tabItem { Text("5. Apply") }
            }
            .padding([.horizontal, .bottom])

            Divider()
            VStack(alignment: .leading) {
                HStack {
                    Text("Command Log").font(.headline)
                    Spacer()
                    Button("Copy Log") { state.copyLog() }
                }
                ScrollView {
                    Text(state.log)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 150)
            }
            .padding()
            Divider()
            NavigationBar(state: state)
        }
        .frame(minWidth: 860, minHeight: 720)
    }
}

struct HeaderView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("AI Obsidian Installer")
                        .font(.title2)
                        .bold()
                    Text(state.stepTitle)
                        .font(.headline)
                    Text(InstallerState.steps[state.step].detail)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                StatusBadge(state: state.runState)
                if state.isRunning {
                    ProgressView()
                        .controlSize(.small)
                }
            }
            StepProgressView(currentStep: state.step)
            if !state.lastError.isEmpty {
                Text(state.lastError)
                    .foregroundStyle(.red)
                    .font(.callout)
                    .textSelection(.enabled)
            } else {
                Text(state.statusText)
                    .foregroundStyle(.secondary)
                    .font(.callout)
                    .textSelection(.enabled)
            }
        }
        .padding()
    }
}

struct StatusBadge: View {
    let state: String

    var color: Color {
        switch state {
        case "Running":
            return .blue
        case "Failed":
            return .red
        case "Complete":
            return .green
        default:
            return .secondary
        }
    }

    var body: some View {
        Text(state)
            .font(.caption)
            .bold()
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

struct StepProgressView: View {
    let currentStep: Int

    var body: some View {
        HStack(spacing: 8) {
            ForEach(0..<InstallerState.steps.count, id: \.self) { index in
                RoundedRectangle(cornerRadius: 3)
                    .fill(index <= currentStep ? Color.accentColor : Color.secondary.opacity(0.25))
                    .frame(height: 6)
            }
        }
    }
}

struct NavigationBar: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        HStack {
            Button("Back") { state.goBack() }
                .disabled(state.step == 0 || state.isRunning)
            Spacer()
            Text("\(state.step + 1) of \(InstallerState.steps.count)")
                .foregroundStyle(.secondary)
            Spacer()
            if state.step < InstallerState.steps.count - 1 {
                Button("Next") { state.goNext() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(state.isRunning)
            } else {
                Button("Apply Setup") { state.applySetup() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(state.isRunning || !state.canApply)
            }
        }
        .padding()
    }
}

struct WelcomeView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        Form {
            Text("Start here. Install the CLI first, then install the stack dependencies: Homebrew, Obsidian, oMLX, Hugging Face CLI, ffmpeg, and mlx-whisper.")
            Text(state.statusText).foregroundStyle(.secondary)
            TextField("Install directory", text: $state.installDir)
            TextField("Binary directory", text: $state.binDir)
            HStack {
                Button("Install / Update CLI") { state.installCLI() }
                Button("Install Core Dependencies") { state.installDependencies() }
                Button("Refresh Status") { state.loadStatus() }
            }
            .disabled(state.isRunning)
        }
    }
}

struct PathsView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        Form {
            Text("Choose a new vault folder or point at an existing Obsidian vault. AI Obsidian will not delete notes.")
                .foregroundStyle(.secondary)
            Picker("Vault mode", selection: $state.vaultMode) {
                Text("Create or reuse folder").tag("create")
                Text("Existing vault").tag("existing")
            }
            TextField("Vault name", text: $state.vaultName)
            HStack {
                TextField("Vault path", text: $state.vaultPath)
                Button("Choose...") { state.chooseDirectory { state.vaultPath = $0 } }
            }
            HStack {
                TextField("Model directory", text: $state.modelDir)
                Button("Choose...") { state.chooseDirectory { state.modelDir = $0 } }
            }
        }
    }
}

struct ModelView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        Form {
            Text("Downloaded MLX models are listed first. If nothing appears, enter a Hugging Face model id manually or load remote suggestions.")
                .foregroundStyle(.secondary)
            Picker("oMLX mode", selection: $state.omlxMode) {
                Text("Homebrew service").tag("service")
                Text("Manual").tag("manual")
                Text("Menu bar app").tag("menubar")
            }
            SecureField("oMLX API key (optional)", text: $state.apiKey)
            TextField("Selected model id", text: $state.selectedModel)
            if !state.downloadedModels.isEmpty {
                Picker("Downloaded models", selection: $state.selectedModel) {
                    ForEach(state.downloadedModels, id: \.self) { Text($0).tag($0) }
                }
            }
            if !state.remoteModels.isEmpty {
                Picker("Remote suggestions", selection: $state.selectedModel) {
                    ForEach(state.remoteModels, id: \.self) { Text($0).tag($0) }
                }
            }
            if state.downloadedModels.isEmpty && state.remoteModels.isEmpty {
                Text("No models loaded yet. Click Load Models after installing the CLI.")
                    .foregroundStyle(.secondary)
            }
            Button("Load Models") { state.loadModels() }
                .disabled(state.isRunning)
        }
    }
}

struct ChatView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        Form {
            Text("Obsidian plugin chat remains the primary UI. Terminal chat is a fallback and diagnostics path.")
                .foregroundStyle(.secondary)
            Picker("Terminal chat engine", selection: $state.chatEngine) {
                Text("Built-in oMLX").tag("builtin")
                Text("Hermes").tag("hermes")
                Text("Claude Code").tag("claude")
            }
            Text("Hermes is optional. Install it here if you want AI Obsidian to call the Hermes CLI, then run hermes setup later if Hermes needs provider/API-key configuration.")
                .foregroundStyle(.secondary)
            HStack {
                Button("Install Hermes CLI") { state.installHermes() }
                Button("Refresh Status") { state.loadStatus() }
            }
            .disabled(state.isRunning)
            Toggle("Install Local LLM Hub in Obsidian", isOn: $state.installHub)
            Toggle("Install AI Obsidian Companion push-to-talk", isOn: $state.installCompanion)
            Toggle("Start stack after setup", isOn: $state.startStack)
            Toggle("Open Obsidian after setup", isOn: $state.openObsidian)
        }
    }
}

struct ReviewView: View {
    @ObservedObject var state: InstallerState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Review").font(.headline)
            Text("Apply Setup will run the backend setup command and leave this window open with the full log.")
                .foregroundStyle(.secondary)
            Text("Vault: \(state.vaultName) at \(state.vaultPath)")
            Text("Model directory: \(state.modelDir)")
            Text("Selected model: \(state.selectedModel)")
            Text("Chat engine: \(state.chatEngine)")
            Text("Plugins: Local LLM Hub \(state.installHub ? "on" : "off"), Companion \(state.installCompanion ? "on" : "off")")
            Spacer()
            HStack {
                Button("Apply Setup") { state.applySetup() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(state.isRunning || !state.canApply)
                Button("Refresh Status") { state.loadStatus() }
                    .disabled(state.isRunning)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top)
    }
}

@main
struct AIObsidianInstallerApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
