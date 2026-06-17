import SwiftUI

struct ProvidersView: View {
    @EnvironmentObject private var processService: AppProcessService

    @Binding var repoPath: String
    @Binding var artRepoPath: String
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

    @State private var statuses: [ProviderStatus] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Providers")
                        .font(.title2.weight(.semibold))
                    Text("Launch adapters can only start processes that speak through WorldOS's existing engine/player paths.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    refresh()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .padding(16)
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(statuses) { status in
                        ProviderCard(status: status)
                    }

                    GroupBox("Current provider diagnostics") {
                        Text(Diagnostics.providerLaunchSummary(processService.providerLaunchMetadata))
                            .font(.caption.monospaced())
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }

                    GroupBox("Configured provider commands") {
                        VStack(alignment: .leading, spacing: 10) {
                            TextField("Codex launch command", text: $codexProviderCommand)
                                .textFieldStyle(.roundedBorder)
                            TextField("Codex home", text: $codexHome)
                                .textFieldStyle(.roundedBorder)
                            TextField("OpenClaw launch command", text: $openClawProviderCommand)
                                .textFieldStyle(.roundedBorder)
                            Text("Configured commands receive WORLDOS_* provider, world, run, port, companion, model, auth-surface, and budget variables. They must route moves through existing engine/player contracts.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    GroupBox("Provider model defaults") {
                        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                            GridRow {
                                Text("Family").font(.caption.weight(.semibold))
                                Text("DM").font(.caption.weight(.semibold))
                                Text("QA player").font(.caption.weight(.semibold))
                                Text("QA scorer").font(.caption.weight(.semibold))
                            }
                            providerModelRow("Claude", dm: $claudeDMModel, player: $claudePlayerModel, scorer: $claudeScorerModel)
                            providerModelRow("Codex", dm: $codexDMModel, player: $codexPlayerModel, scorer: $codexScorerModel)
                            providerModelRow("OpenClaw", dm: $openClawDMModel, player: $openClawPlayerModel, scorer: $openClawScorerModel)
                        }
                    }

                    GroupBox("Session caps") {
                        HStack {
                            TextField("Per-turn budget", text: $budget)
                            TextField("Session budget", text: $sessionBudget)
                            TextField("Max turns", text: $maxTurns)
                        }
                        .textFieldStyle(.roundedBorder)
                    }
                }
                .padding(16)
            }
        }
        .onAppear(perform: refresh)
        .onChange(of: repoPath) { _ in refresh() }
        .onChange(of: artRepoPath) { _ in refresh() }
        .onChange(of: codexProviderCommand) { _ in refresh() }
        .onChange(of: codexHome) { _ in refresh() }
        .onChange(of: openClawProviderCommand) { _ in refresh() }
        .onChange(of: claudeDMModel) { _ in refresh() }
        .onChange(of: codexDMModel) { _ in refresh() }
        .onChange(of: openClawDMModel) { _ in refresh() }
        .onChange(of: claudePlayerModel) { _ in refresh() }
        .onChange(of: codexPlayerModel) { _ in refresh() }
        .onChange(of: openClawPlayerModel) { _ in refresh() }
        .onChange(of: claudeScorerModel) { _ in refresh() }
        .onChange(of: codexScorerModel) { _ in refresh() }
        .onChange(of: openClawScorerModel) { _ in refresh() }
    }

    @ViewBuilder
    private func providerModelRow(
        _ label: String,
        dm: Binding<String>,
        player: Binding<String>,
        scorer: Binding<String>
    ) -> some View {
        GridRow {
            Text(label)
            TextField("DM model", text: dm)
            TextField("QA player", text: player)
            TextField("QA scorer", text: scorer)
        }
        .textFieldStyle(.roundedBorder)
    }

    private func refresh() {
        statuses = processService.providerStatuses(
            repoPath: repoPath,
            preferences: ProviderPreferences(
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
        )
    }
}

struct ProviderCard: View {
    let status: ProviderStatus

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: status.kind.symbolName)
                .font(.title2)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(status.kind.displayName)
                        .font(.headline)
                    Text(status.availability.rawValue.capitalized)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(statusColor.opacity(0.16), in: Capsule())
                        .foregroundStyle(statusColor)
                }
                Text(status.detail)
                    .foregroundStyle(.secondary)
                if let detectedPath = status.detectedPath {
                    Text(detectedPath)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Text("\(status.providerFamily) · \(status.authSurface) · DM \(status.dmModel.isEmpty ? "default" : status.dmModel) · QA player \(status.playerModel.isEmpty ? "default" : status.playerModel) · scorer \(status.scorerModel.isEmpty ? "default" : status.scorerModel)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Spacer()
        }
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private var statusColor: Color {
        switch status.availability {
        case .configured, .installed: .green
        case .missing: .orange
        case .error: .red
        }
    }
}
