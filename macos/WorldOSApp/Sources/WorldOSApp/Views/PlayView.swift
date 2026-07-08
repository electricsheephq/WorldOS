import SwiftUI

struct PlayView: View {
    @EnvironmentObject private var processService: AppProcessService

    @Binding var repoPath: String
    @Binding var artRepoPath: String
    @Binding var preferredPort: Int
    @Binding var stateDir: String
    @Binding var selectedProviderRaw: String
    @Binding var defaultWorld: String
    @Binding var codexProviderCommand: String
    @Binding var codexHome: String
    @Binding var openClawProviderCommand: String
    @Binding var claudeDMModel: String
    @Binding var codexDMModel: String
    @Binding var openClawDMModel: String
    @Binding var claudePlayerModel: String
    @Binding var codexPlayerModel: String
    @Binding var openClawPlayerModel: String
    @Binding var claudeScorerModel: String
    @Binding var codexScorerModel: String
    @Binding var openClawScorerModel: String
    @Binding var budget: String
    @Binding var sessionBudget: String
    @Binding var maxTurns: String
    @Binding var webURL: URL?

    // Additive (issue #1322 / W5): path to the standalone Unity player `.app`. Empty → the default
    // ~/Applications/WorldOSPlayer.app is used; if neither exists the launch is a defaulted no-op.
    // Read directly from UserDefaults so no binding has to be threaded through RootView.
    @AppStorage("playerAppPath") private var playerAppPath: String = ""

    @State private var runID: String = PlayView.newRunID()
    @State private var companions: String = ""
    @State private var alertMessage: String?
    @State private var webViewErrorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            controlBar
            Divider()
            webSurface
        }
        .alert("WorldOS could not start", isPresented: alertBinding) {
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
                    Label("Open Play Surface", systemImage: "rectangle.on.rectangle")
                }
                Button {
                    startProvider()
                } label: {
                    Label("Start Game", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    openInPlayer()
                } label: {
                    Label("Open in Player", systemImage: "cube.transparent")
                }
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
            if let webViewErrorMessage {
                WebViewErrorView(message: webViewErrorMessage) {
                    self.webViewErrorMessage = nil
                }
            } else {
                WebView(url: webURL, navigationError: $webViewErrorMessage)
            }
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
            webViewErrorMessage = nil
            webURL = try processService.startViewer(
                repoPath: repoPath,
                preferredPort: preferredPort,
                stateDir: stateDir,
                artRepoPath: artRepoPath
            )
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func startProvider() {
        do {
            webViewErrorMessage = nil
            let provider = ProviderKind(rawValue: selectedProviderRaw) ?? .claude
            guard provider.isLaunchEnabled else {
                throw ProviderError.configuration("Scripted provider is disabled. Set WORLDOS_ENABLE_SCRIPTED_PROVIDER=1 for dev/test smoke.")
            }
            let cleanRunID = runID.trimmingCharacters(in: .whitespacesAndNewlines)
            webURL = try processService.startProviderSession(
                kind: provider,
                repoPath: repoPath,
                world: defaultWorld,
                runId: cleanRunID.isEmpty ? Self.newRunID() : cleanRunID,
                preferredPort: preferredPort,
                companions: companions,
                stateDir: stateDir,
                artRepoPath: artRepoPath,
                preferences: providerPreferences
            )
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    /// Hand the running engine's campaign off to the standalone Unity player (issue #1322 / W5).
    /// A defaulted no-op when no player `.app` is installed; surfaces a hint if there is no engine.
    private func openInPlayer() {
        let outcome = processService.launchPlayer(playerAppPath: playerAppPath)
        if case .notConfigured = outcome, processService.viewerEndpoint?.status == nil {
            alertMessage = "Start a viewer or game session before opening the player."
        }
    }

    private var providerPreferences: ProviderPreferences {
        ProviderPreferences(
            codexCommand: codexProviderCommand,
            codexHome: codexHome,
            openClawCommand: openClawProviderCommand,
            claudeDMModel: claudeDMModel,
            codexDMModel: codexDMModel,
            openClawDMModel: openClawDMModel,
            claudePlayerModel: claudePlayerModel,
            codexPlayerModel: codexPlayerModel,
            openClawPlayerModel: openClawPlayerModel,
            claudeScorerModel: claudeScorerModel,
            codexScorerModel: codexScorerModel,
            openClawScorerModel: openClawScorerModel,
            budget: budget,
            sessionBudget: sessionBudget,
            maxTurns: maxTurns,
            artRepoPath: artRepoPath
        )
    }

    private var alertBinding: Binding<Bool> {
        Binding(
            get: { alertMessage != nil },
            set: { if !$0 { alertMessage = nil } }
        )
    }

    private static let runIDFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()

    private static func newRunID() -> String {
        return "play-\(runIDFormatter.string(from: Date()))"
    }
}
