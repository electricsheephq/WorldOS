import SwiftUI

struct PlayView: View {
    @EnvironmentObject private var processService: AppProcessService

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
    @Binding var webURL: URL?

    @State private var runID: String = PlayView.newRunID()
    @State private var companions: String = ""
    @State private var alertMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            controlBar
            Divider()
            webSurface
        }
        .alert("ClawDnD could not start", isPresented: alertBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(alertMessage ?? "")
        }
    }

    private var controlBar: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Play")
                        .font(.title2.weight(.semibold))
                    dependencySummary
                }
                Spacer()
                Picker("Provider", selection: $selectedProviderRaw) {
                    ForEach(ProviderKind.allCases) { provider in
                        Label(provider.displayName, systemImage: provider.symbolName)
                            .tag(provider.rawValue)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 320)
            }

            HStack(spacing: 10) {
                TextField("World", text: $defaultWorld)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)
                TextField("Run ID", text: $runID)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
                TextField("Companions", text: $companions)
                    .textFieldStyle(.roundedBorder)
                Button {
                    startViewer()
                } label: {
                    Label("Open Dashboard", systemImage: "rectangle.on.rectangle")
                }
                Button {
                    startProvider()
                } label: {
                    Label("Start Game", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    processService.stopProvider()
                    processService.stopViewer()
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
            }
        }
        .padding(16)
        .background(.thinMaterial)
    }

    @ViewBuilder
    private var dependencySummary: some View {
        let missing = processService.dependencies.filter { !$0.isInstalled }
        if missing.isEmpty {
            Label("Dependencies ready", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        } else {
            HStack(spacing: 6) {
                Label("Missing", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text(missing.map(\.command).joined(separator: ", "))
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var webSurface: some View {
        if let webURL {
            WebView(url: webURL)
        } else {
            VStack(spacing: 18) {
                Image(systemName: "gamecontroller")
                    .font(.system(size: 54, weight: .regular))
                    .foregroundStyle(.secondary)
                Text("Start a viewer or game session")
                    .font(.title3.weight(.semibold))
                Text(repoPath)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func startViewer() {
        do {
            webURL = try processService.startViewer(
                repoPath: repoPath,
                preferredPort: preferredPort,
                stateDir: stateDir
            )
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func startProvider() {
        do {
            let provider = ProviderKind(rawValue: selectedProviderRaw) ?? .claude
            let cleanRunID = runID.trimmingCharacters(in: .whitespacesAndNewlines)
            webURL = try processService.startProviderSession(
                kind: provider,
                repoPath: repoPath,
                world: defaultWorld,
                runId: cleanRunID.isEmpty ? Self.newRunID() : cleanRunID,
                preferredPort: preferredPort,
                companions: companions,
                preferences: providerPreferences
            )
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private var providerPreferences: ProviderPreferences {
        ProviderPreferences(
            codexCommand: codexProviderCommand,
            openClawCommand: openClawProviderCommand,
            budget: budget,
            sessionBudget: sessionBudget,
            maxTurns: maxTurns
        )
    }

    private var alertBinding: Binding<Bool> {
        Binding(
            get: { alertMessage != nil },
            set: { if !$0 { alertMessage = nil } }
        )
    }

    private static func newRunID() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "play-\(formatter.string(from: Date()))"
    }
}
