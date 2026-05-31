import SwiftUI

struct ProvidersView: View {
    @EnvironmentObject private var processService: AppProcessService

    @Binding var repoPath: String
    @Binding var artRepoPath: String
    @Binding var codexProviderCommand: String
    @Binding var openClawProviderCommand: String
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
                            TextField("OpenClaw launch command", text: $openClawProviderCommand)
                                .textFieldStyle(.roundedBorder)
                            Text("Configured commands receive CLAWDND_PROVIDER, CLAWDND_WORLD, CLAWDND_RUN_ID, CLAWDND_PLAY_PORT, and CLAWDND_PLAY_COMPANIONS. They must route moves through existing engine/player contracts.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
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
        .onChange(of: openClawProviderCommand) { _ in refresh() }
    }

    private func refresh() {
        statuses = processService.providerStatuses(
            repoPath: repoPath,
            preferences: ProviderPreferences(
                codexCommand: codexProviderCommand,
                openClawCommand: openClawProviderCommand,
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
