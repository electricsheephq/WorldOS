import SwiftUI

struct SettingsView: View {
    @Binding var repoPath: String
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

    var body: some View {
        Form {
            Section("Workspace") {
                TextField("Repo path", text: $repoPath)
                TextField("State directory", text: $stateDir)
                Stepper(value: $preferredPort, in: 1024...65535) {
                    Text("Preferred port: \(preferredPort)")
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
                TextField("Per-turn budget", text: $budget)
                TextField("Session budget", text: $sessionBudget)
                TextField("Max turns", text: $maxTurns)
            }

            Section("Provider commands") {
                TextField("Codex command", text: $codexProviderCommand)
                TextField("OpenClaw command", text: $openClawProviderCommand)
            }
        }
        .formStyle(.grouped)
        .padding(20)
    }
}
