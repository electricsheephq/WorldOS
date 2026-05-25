import SwiftUI

struct PlayView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore

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
    @State private var selectedCampaignID: CampaignSummary.ID?
    @State private var alertMessage: String?
    @State private var webViewErrorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            if webURL != nil {
                controlBar
                OpenWorldsDivider()
            }
            webSurface
        }
        .background(OpenWorldsParchmentBackground())
        .onAppear {
            campaignStore.reload(repoPath: repoPath)
            selectFirstCampaignIfNeeded()
        }
        .onChange(of: campaignStore.campaigns) { _ in
            selectFirstCampaignIfNeeded()
        }
        .alert("ClawDnD could not start", isPresented: alertBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(alertMessage ?? "")
        }
    }

    private var controlBar: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("The Table")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(OpenWorldsTheme.ink800)
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
            Button {
                startViewer()
            } label: {
                Label("Dashboard", systemImage: "rectangle.on.rectangle")
            }
            .buttonStyle(OpenWorldsBrassButtonStyle())
            Button {
                startProvider()
            } label: {
                Label("Start", systemImage: "play.fill")
            }
            .buttonStyle(OpenWorldsBrassButtonStyle(prominent: true))
            Button {
                processService.stopProvider()
                processService.stopViewer()
                webURL = nil
            } label: {
                Label("Stop", systemImage: "stop.fill")
            }
            .buttonStyle(OpenWorldsBrassButtonStyle(danger: true))
        }
        .padding(16)
        .background(OpenWorldsTheme.parchment200.opacity(0.96))
    }

    @ViewBuilder
    private var dependencySummary: some View {
        let missing = processService.dependencies.filter { !$0.isInstalled }
        if missing.isEmpty {
            Label("Dependencies ready", systemImage: "checkmark.circle.fill")
                .font(.caption)
                .foregroundStyle(OpenWorldsTheme.emerald)
        } else {
            HStack(spacing: 6) {
                Label("Missing", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(OpenWorldsTheme.crimson)
                Text(missing.map(\.command).joined(separator: ", "))
                    .font(.caption)
                    .foregroundStyle(OpenWorldsTheme.ink600)
                    .lineLimit(1)
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
            ChroniclesLauncherSurface(
                campaigns: campaignStore.campaigns,
                selectedCampaignID: $selectedCampaignID,
                selectedProviderRaw: $selectedProviderRaw,
                defaultWorld: $defaultWorld,
                runID: $runID,
                companions: $companions,
                repoPath: repoPath,
                dependencySummary: AnyView(dependencySummary),
                openCampaign: openCampaign,
                refreshCampaigns: { campaignStore.reload(repoPath: repoPath) },
                startViewer: startViewer,
                startProvider: startProvider
            )
        }
    }

    private func openCampaign(_ campaign: CampaignSummary) {
        do {
            webViewErrorMessage = nil
            webURL = try processService.startViewer(
                repoPath: repoPath,
                preferredPort: preferredPort,
                stateDir: campaign.stateRoot.path,
                campaignID: campaign.id
            )
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func startViewer() {
        do {
            webViewErrorMessage = nil
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
            webViewErrorMessage = nil
            let provider = ProviderKind(rawValue: selectedProviderRaw) ?? .claude
            let cleanRunID = runID.trimmingCharacters(in: .whitespacesAndNewlines)
            let resolvedRunID = cleanRunID.isEmpty ? Self.newRunID() : cleanRunID
            runID = resolvedRunID
            webURL = try processService.startProviderSession(
                kind: provider,
                repoPath: repoPath,
                world: defaultWorld,
                runId: resolvedRunID,
                preferredPort: preferredPort,
                companions: companions,
                stateDir: stateDir,
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

    private func selectFirstCampaignIfNeeded() {
        guard selectedCampaignID == nil || !campaignStore.campaigns.contains(where: { $0.id == selectedCampaignID }) else {
            return
        }
        selectedCampaignID = campaignStore.campaigns.first?.id
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

struct ChroniclesLauncherSurface: View {
    let campaigns: [CampaignSummary]
    @Binding var selectedCampaignID: CampaignSummary.ID?
    @Binding var selectedProviderRaw: String
    @Binding var defaultWorld: String
    @Binding var runID: String
    @Binding var companions: String
    let repoPath: String
    let dependencySummary: AnyView
    let openCampaign: (CampaignSummary) -> Void
    let refreshCampaigns: () -> Void
    let startViewer: () -> Void
    let startProvider: () -> Void

    private var selectedCampaign: CampaignSummary? {
        campaigns.first { $0.id == selectedCampaignID } ?? campaigns.first
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                HStack(alignment: .top, spacing: 18) {
                    campaignList
                        .frame(minWidth: 310, idealWidth: 360, maxWidth: 430)
                    campaignPreview
                        .frame(minWidth: 380, maxWidth: .infinity)
                    launchPanel
                        .frame(width: 330)
                }
            }
            .padding(22)
        }
    }

    private var header: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Chronicles")
                    .font(.system(size: 34, weight: .semibold, design: .serif))
                    .foregroundStyle(OpenWorldsTheme.ink800)
                Text("Choose a running world, resume a save, or light a fresh table.")
                    .foregroundStyle(OpenWorldsTheme.ink600)
            }
            Spacer()
            dependencySummary
            Button {
                refreshCampaigns()
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .buttonStyle(OpenWorldsBrassButtonStyle())
        }
    }

    private var campaignList: some View {
        OpenWorldsPanel(title: "I. Chronicles", subtitle: "Local saves and observed runs", icon: "books.vertical") {
            if campaigns.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Image(systemName: "book.closed")
                        .font(.largeTitle)
                        .foregroundStyle(OpenWorldsTheme.ink600)
                    Text("No campaigns found")
                        .font(.headline)
                    Text(repoPath)
                        .font(.caption)
                        .foregroundStyle(OpenWorldsTheme.ink600)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 20)
            } else {
                VStack(spacing: 10) {
                    ForEach(campaigns) { campaign in
                        ChronicleCard(
                            campaign: campaign,
                            selected: selectedCampaign?.id == campaign.id
                        ) {
                            selectedCampaignID = campaign.id
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var campaignPreview: some View {
        if let selectedCampaign {
            OpenWorldsPanel(title: selectedCampaign.title, subtitle: selectedCampaign.id, icon: "map") {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 10) {
                        OpenWorldsPill(text: selectedCampaign.sourceLabel, tone: .royal)
                        OpenWorldsPill(text: selectedCampaign.isLive ? "Live" : "Stale", tone: selectedCampaign.isLive ? .live : .neutral)
                        OpenWorldsPill(text: selectedCampaign.provider, tone: .neutral)
                    }
                    detailGrid(selectedCampaign)
                    OpenWorldsDivider()
                    VStack(alignment: .leading, spacing: 8) {
                        Text("The Party")
                            .font(.headline)
                        Text(selectedCampaign.partyLabel)
                            .foregroundStyle(OpenWorldsTheme.ink600)
                            .textSelection(.enabled)
                    }
                    Spacer(minLength: 8)
                    HStack {
                        Button {
                            openCampaign(selectedCampaign)
                        } label: {
                            Label("Resume Chronicle", systemImage: "play.fill")
                        }
                        .buttonStyle(OpenWorldsBrassButtonStyle(prominent: true))
                        Button {
                            selectedCampaignID = selectedCampaign.id
                        } label: {
                            Label("Select", systemImage: "bookmark")
                        }
                        .buttonStyle(OpenWorldsBrassButtonStyle())
                    }
                }
            }
        } else {
            OpenWorldsPanel(title: "Where last we stood", subtitle: "No campaign selected", icon: "scroll") {
                Text("Start a new table or refresh the campaign catalogue.")
                    .foregroundStyle(OpenWorldsTheme.ink600)
            }
        }
    }

    private func detailGrid(_ campaign: CampaignSummary) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 9) {
            detailRow("World", campaign.world)
            detailRow("Time", campaign.dayLabel)
            detailRow("Location", campaign.location)
            detailRow("Run", campaign.runID)
            detailRow("Updated", campaign.lastUpdate.formatted(date: .abbreviated, time: .standard))
            detailRow("State root", campaign.stateRoot.path)
        }
        .font(.callout)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label)
                .foregroundStyle(OpenWorldsTheme.ink600)
            Text(value)
                .foregroundStyle(OpenWorldsTheme.ink800)
                .textSelection(.enabled)
                .lineLimit(2)
        }
    }

    private var launchPanel: some View {
        OpenWorldsPanel(title: "II. Light the Lantern", subtitle: "Provider-backed local play", icon: "sparkles") {
            VStack(alignment: .leading, spacing: 12) {
                Picker("Provider", selection: $selectedProviderRaw) {
                    ForEach(ProviderKind.allCases) { provider in
                        Label(provider.displayName, systemImage: provider.symbolName)
                            .tag(provider.rawValue)
                    }
                }
                .pickerStyle(.menu)

                labeledTextField("World", text: $defaultWorld)
                labeledTextField("Run ID", text: $runID)
                labeledTextField("Companions", text: $companions)

                HStack {
                    Button {
                        startViewer()
                    } label: {
                        Label("Dashboard", systemImage: "rectangle.on.rectangle")
                    }
                    .buttonStyle(OpenWorldsBrassButtonStyle())
                    Button {
                        startProvider()
                    } label: {
                        Label("Start Game", systemImage: "play.fill")
                    }
                    .buttonStyle(OpenWorldsBrassButtonStyle(prominent: true))
                }

                Text("Ready the table, gather the party, then begin local play.")
                    .font(.caption)
                    .foregroundStyle(OpenWorldsTheme.ink600)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func labeledTextField(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(OpenWorldsTheme.ink600)
            TextField(label, text: text)
                .textFieldStyle(.roundedBorder)
        }
    }
}

struct ChronicleCard: View {
    let campaign: CampaignSummary
    let selected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text(campaign.title)
                        .font(.headline)
                        .foregroundStyle(OpenWorldsTheme.ink800)
                        .lineLimit(1)
                    Spacer()
                    OpenWorldsPill(text: campaign.isLive ? "Live" : campaign.sourceLabel, tone: campaign.isLive ? .live : .neutral)
                }
                HStack(spacing: 9) {
                    Label(campaign.world, systemImage: "map")
                    Label(campaign.dayLabel, systemImage: "clock")
                }
                .font(.caption)
                .foregroundStyle(OpenWorldsTheme.ink600)
                Text(campaign.location)
                    .font(.caption)
                    .foregroundStyle(OpenWorldsTheme.ink700)
                    .lineLimit(1)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? OpenWorldsTheme.brass100.opacity(0.7) : OpenWorldsTheme.parchment100.opacity(0.66))
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .stroke(selected ? OpenWorldsTheme.brass400 : OpenWorldsTheme.parchmentEdge.opacity(0.35), lineWidth: selected ? 1.5 : 1)
            }
        }
        .buttonStyle(.plain)
    }
}
