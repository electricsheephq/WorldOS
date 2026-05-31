import SwiftUI

struct SettingsView: View {
    @Binding var repoPath: String
    @Binding var artRepoPath: String
    @Binding var preferredPort: Int
    @Binding var stateDir: String
    @Binding var selectedProviderRaw: String
    @Binding var defaultWorld: String
    @Binding var codexProviderCommand: String
    @Binding var openClawProviderCommand: String
    @Binding var budget: String
    @Binding var sessionBudget: String
    @Binding var maxTurns: String
    @Binding var voiceBackend: String

    @State private var preferredPortText = ""

    var body: some View {
        Form {
            Section("Workspace") {
                ValidatedTextField("Repo path", text: $repoPath, error: repoPathError)
                ValidatedTextField("Private art repo path", text: $artRepoPath, error: artRepoPathError)
                TextField("State directory", text: $stateDir)
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        ValidatedTextField("Preferred port", text: $preferredPortText, error: preferredPortError)
                        Stepper("", value: $preferredPort, in: 1024...65535)
                            .labelsHidden()
                    }
                    if preferredPortError == nil {
                        Text("Preferred port: \(preferredPort)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section("Defaults") {
                TextField("Default world", text: $defaultWorld)
                Picker("Preferred provider", selection: $selectedProviderRaw) {
                    ForEach(ProviderKind.allCases) { provider in
                        Text(provider.displayName).tag(provider.rawValue)
                    }
                }
                .pickerStyle(.segmented)
                Picker("Voice backend", selection: $voiceBackend) {
                    Text("Null").tag("null")
                    Text("Kokoro").tag("kokoro")
                    Text("ElevenLabs").tag("elevenlabs")
                }
                .pickerStyle(.segmented)
            }

            Section("Caps") {
                ValidatedTextField("Per-turn budget", text: $budget, error: positiveDecimalError(for: budget, label: "Per-turn budget"))
                ValidatedTextField("Session budget", text: $sessionBudget, error: positiveDecimalError(for: sessionBudget, label: "Session budget"))
                ValidatedTextField("Max turns", text: $maxTurns, error: positiveIntegerError(for: maxTurns, label: "Max turns"))
            }

            Section("Provider commands") {
                ValidatedTextField("Codex command", text: $codexProviderCommand, error: commandError(for: codexProviderCommand, label: "Codex command"))
                ValidatedTextField("OpenClaw command", text: $openClawProviderCommand, error: commandError(for: openClawProviderCommand, label: "OpenClaw command"))
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .onAppear {
            preferredPortText = String(preferredPort)
        }
        .onChange(of: preferredPort) { newValue in
            if preferredPortText != String(newValue) {
                preferredPortText = String(newValue)
            }
        }
        .onChange(of: preferredPortText) { newValue in
            if let port = Self.validPort(from: newValue) {
                preferredPort = port
            }
        }
    }

    private var repoPathError: String? {
        let expanded = repoPath.trimmingCharacters(in: .whitespacesAndNewlines) as NSString
        let path = expanded.expandingTildeInPath
        guard !path.isEmpty else {
            return "Choose a WorldOS checkout folder."
        }

        guard path.hasPrefix("/") else {
            return "Use a full path, for example /Users/you/WorldOS."
        }

        guard RepositoryLocator.looksLikeRepo(URL(fileURLWithPath: path)) else {
            return "This folder does not look like a WorldOS checkout."
        }

        return nil
    }

    private var artRepoPathError: String? {
        let value = artRepoPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return nil
        }

        let expanded = (value as NSString).expandingTildeInPath
        guard expanded.hasPrefix("/") else {
            return "Use a full path, for example /Users/you/WorldOS."
        }

        guard RepositoryLocator.looksLikeArtRepo(URL(fileURLWithPath: expanded)) else {
            return "This folder does not have content/worlds/_private."
        }

        return nil
    }

    private var preferredPortError: String? {
        let value = preferredPortText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return "Enter a port from 1024 to 65535."
        }

        guard Int(value) != nil else {
            return "Port must be a whole number."
        }

        guard Self.validPort(from: value) != nil else {
            return "Port must be between 1024 and 65535."
        }

        return nil
    }

    private func positiveDecimalError(for rawValue: String, label: String) -> String? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return nil
        }

        guard let number = Decimal(string: value), number > 0 else {
            return "\(label) must be a positive number."
        }

        return nil
    }

    private func positiveIntegerError(for rawValue: String, label: String) -> String? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return nil
        }

        guard let number = Int(value), number > 0 else {
            return "\(label) must be a positive whole number."
        }

        return nil
    }

    private func commandError(for rawValue: String, label: String) -> String? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return nil
        }

        guard value.rangeOfCharacter(from: .newlines) == nil else {
            return "\(label) must be one shell command line."
        }

        guard value.rangeOfCharacter(from: .controlCharacters) == nil else {
            return "\(label) contains an unsupported control character."
        }

        return nil
    }

    private static func validPort(from rawValue: String) -> Int? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let port = Int(value), (1024...65535).contains(port) else {
            return nil
        }

        return port
    }
}

private struct ValidatedTextField: View {
    let title: String
    @Binding var text: String
    let error: String?

    init(_ title: String, text: Binding<String>, error: String?) {
        self.title = title
        _text = text
        self.error = error
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            TextField(title, text: $text)
                .textFieldStyle(.roundedBorder)
                .overlay {
                    if error != nil {
                        RoundedRectangle(cornerRadius: 5)
                            .stroke(.red, lineWidth: 1)
                    }
                }

            if let error {
                Label(error, systemImage: "exclamationmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
