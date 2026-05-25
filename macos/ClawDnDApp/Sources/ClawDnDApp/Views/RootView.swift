import SwiftUI

struct RootView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore

    @AppStorage("repoPath") private var repoPath: String = RepositoryLocator.defaultRepoPath() ?? ""
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
        ZStack {
            OpenWorldsWindowBackground()
            VStack(spacing: 0) {
                OpenWorldsTitleBar(
                    campaign: currentCampaign?.title ?? "No Chronicle Selected",
                    location: (selection ?? .play).title,
                    day: currentCampaign?.dayLabel ?? "local"
                )
                HStack(spacing: 0) {
                    SidebarView(selection: $selection)
                    VStack(spacing: 0) {
                        detailView
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                        StatusStrip(repoPath: repoPath, stateDir: stateDir)
                            .environmentObject(processService)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: OpenWorldsTheme.panelRadius))
                }
            }
            .padding(14)
        }
        .frame(minWidth: 1120, minHeight: 720)
        .onAppear(perform: refresh)
        .onChange(of: repoPath) { _ in refresh() }
    }

    private var currentCampaign: CampaignSummary? {
        campaignStore.campaigns.first { $0.id == processService.activeCampaignID }
            ?? campaignStore.campaigns.first
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
        VStack(spacing: 12) {
            ForEach(AppSection.allCases) { section in
                Button {
                    selection = section
                } label: {
                    VStack(spacing: 6) {
                        Image(systemName: section.symbolName)
                            .font(.system(size: 20, weight: .medium))
                        Text(section.title)
                            .font(.caption2.weight(.semibold))
                            .lineLimit(1)
                    }
                    .frame(width: 58, height: 58)
                    .foregroundStyle(selection == section ? OpenWorldsTheme.ink800 : OpenWorldsTheme.brass200)
                    .background(selection == section ? OpenWorldsTheme.brass100.opacity(0.86) : OpenWorldsTheme.walnut100.opacity(0.58))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay {
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(selection == section ? OpenWorldsTheme.brass300 : OpenWorldsTheme.brass600.opacity(0.5), lineWidth: 1)
                    }
                }
                .buttonStyle(.plain)
                .help(section.title)
                .accessibilityLabel(section.title)
                .accessibilityValue(selection == section ? "Selected" : "Not selected")
            }
            Spacer(minLength: 12)
        }
        .padding(.vertical, 12)
        .frame(width: OpenWorldsTheme.railWidth)
        .background(
            LinearGradient(
                colors: [OpenWorldsTheme.walnut300, OpenWorldsTheme.walnut400],
                startPoint: .top,
                endPoint: .bottom
            )
        )
        .overlay(alignment: .trailing) {
            Rectangle()
                .fill(OpenWorldsTheme.brass600.opacity(0.52))
                .frame(width: 1)
        }
    }
}

struct OpenWorldsTitleBar: View {
    let campaign: String
    let location: String
    let day: String

    var body: some View {
        HStack(spacing: 16) {
            HStack(spacing: 7) {
                statusJewel(OpenWorldsTheme.crimson)
                statusJewel(OpenWorldsTheme.brass300)
                statusJewel(OpenWorldsTheme.emerald)
            }
            .accessibilityHidden(true)

            Spacer()
            Text("CLAWDND")
                .font(.caption.weight(.bold))
                .tracking(4)
                .foregroundStyle(OpenWorldsTheme.brass100)
            Text("·")
                .foregroundStyle(OpenWorldsTheme.brass300)
            Text(campaign)
                .font(.caption.weight(.semibold))
                .tracking(2)
                .lineLimit(1)
                .foregroundStyle(OpenWorldsTheme.brass200)
            Text("·")
                .foregroundStyle(OpenWorldsTheme.brass300)
            Text(location)
                .font(.caption.weight(.semibold))
                .tracking(2)
                .lineLimit(1)
                .foregroundStyle(OpenWorldsTheme.brass100)
            Spacer()

            Text(day)
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(OpenWorldsTheme.brass200)
                .lineLimit(1)
                .frame(width: 130, alignment: .trailing)
        }
        .frame(height: 38)
        .padding(.horizontal, 16)
    }

    private func statusJewel(_ color: Color) -> some View {
        Circle()
            .fill(color)
            .frame(width: 12, height: 12)
            .overlay(Circle().stroke(.black.opacity(0.45), lineWidth: 1))
            .shadow(color: color.opacity(0.35), radius: 2)
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
        .foregroundStyle(OpenWorldsTheme.ink700)
        .background(OpenWorldsTheme.parchment200)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(OpenWorldsTheme.parchmentEdge.opacity(0.35))
                .frame(height: 1)
        }
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
