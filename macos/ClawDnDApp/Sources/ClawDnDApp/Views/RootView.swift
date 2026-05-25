import SwiftUI

struct RootView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore

    @AppStorage("repoPath") private var repoPath: String = RepositoryLocator.defaultRepoPath()
    @AppStorage("preferredPort") private var preferredPort: Int = 8765
    @AppStorage("stateDir") private var stateDir: String = ""
    @AppStorage("selectedProvider") private var selectedProviderRaw: String = ProviderKind.claude.rawValue
    @AppStorage("defaultWorld") private var defaultWorld: String = "baldurs-gate"
    @AppStorage("codexProviderCommand") private var codexProviderCommand: String = ""
    @AppStorage("openClawProviderCommand") private var openClawProviderCommand: String = ""
    @AppStorage("budget") private var budget: String = "1.50"
    @AppStorage("sessionBudget") private var sessionBudget: String = "15.00"
    @AppStorage("maxTurns") private var maxTurns: String = "40"
    @AppStorage("voiceBackend") private var voiceBackend: String = "null"

    @State private var selection: AppSection? = .play
    @State private var webURL: URL?

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: $selection)
        } detail: {
            VStack(spacing: 0) {
                detailView
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                StatusStrip(repoPath: repoPath, stateDir: stateDir)
                    .environmentObject(processService)
            }
        }
        .onAppear(perform: refresh)
        .onChange(of: repoPath) { _ in refresh() }
    }

    @ViewBuilder
    private var detailView: some View {
        switch selection ?? .play {
        case .play:
            PlayView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                selectedProviderRaw: $selectedProviderRaw,
                defaultWorld: $defaultWorld,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns,
                webURL: $webURL
            )
        case .campaigns:
            CampaignsView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                webURL: $webURL
            )
        case .monitor:
            MonitorView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                webURL: $webURL
            )
        case .providers:
            ProvidersView(
                repoPath: $repoPath,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns
            )
        case .settings:
            SettingsView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                selectedProviderRaw: $selectedProviderRaw,
                defaultWorld: $defaultWorld,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns,
                voiceBackend: $voiceBackend
            )
        case .logs:
            LogsView()
        }
    }

    private func refresh() {
        processService.refreshDependencies()
        campaignStore.reload(repoPath: repoPath)
    }
}

struct SidebarView: View {
    @Binding var selection: AppSection?

    var body: some View {
        List(AppSection.allCases, selection: $selection) { section in
            Label(section.title, systemImage: section.symbolName)
                .tag(section)
        }
        .navigationSplitViewColumnWidth(min: 180, ideal: 210)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("ClawDnD")
                    .font(.headline)
            }
        }
    }
}

struct StatusStrip: View {
    @EnvironmentObject private var processService: AppProcessService
    let repoPath: String
    let stateDir: String

    var body: some View {
        HStack(spacing: 14) {
            statusItem("Viewer", processService.viewerEndpoint.map { "\($0.port) \($0.status.rawValue)" } ?? "stopped")
            statusItem("State", stateDir.isEmpty ? "default" : URL(fileURLWithPath: stateDir).lastPathComponent)
            statusItem("Campaign", processService.activeCampaignID ?? "auto")
            statusItem("Provider", processService.runningProvider?.displayName ?? "none")
            if let lastError = processService.lastError {
                Label(lastError, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
                    .lineLimit(1)
            } else {
                Spacer(minLength: 8)
            }
        }
        .font(.caption)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func statusItem(_ label: String, _ value: String) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .lineLimit(1)
        }
    }
}
