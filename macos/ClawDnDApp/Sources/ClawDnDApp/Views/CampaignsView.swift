import SwiftUI

struct CampaignsView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore

    @Binding var repoPath: String
    @Binding var preferredPort: Int
    @Binding var webURL: URL?

    @State private var selectedCampaignID: CampaignSummary.ID?
    @State private var alertMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Campaigns")
                        .font(.title2.weight(.semibold))
                    Text("Read-only snapshots from play-state and qa/state.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    campaignStore.reload(repoPath: repoPath)
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .padding(16)
            Divider()

            if campaignStore.campaigns.isEmpty {
                EmptyStateView(title: "No Campaigns Found", symbolName: "books.vertical")
            } else {
                HSplitView {
                    List(selection: $selectedCampaignID) {
                        ForEach(campaignStore.campaigns) { campaign in
                            CampaignRow(campaign: campaign)
                                .tag(campaign.id)
                                .contextMenu {
                                    Button("Open in Dashboard") {
                                        open(campaign)
                                    }
                                }
                        }
                    }
                    .frame(minWidth: 360, idealWidth: 460)

                    CampaignDetail(
                        campaign: campaignStore.campaigns.first { $0.id == selectedCampaignID },
                        openAction: { campaign in open(campaign) }
                    )
                    .frame(minWidth: 420)
                }
            }
        }
        .onAppear {
            campaignStore.reload(repoPath: repoPath)
            selectedCampaignID = selectedCampaignID ?? campaignStore.campaigns.first?.id
        }
        .alert("Campaign could not open", isPresented: alertBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(alertMessage ?? "")
        }
    }

    private func open(_ campaign: CampaignSummary) {
        do {
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

    private var alertBinding: Binding<Bool> {
        Binding(get: { alertMessage != nil }, set: { if !$0 { alertMessage = nil } })
    }
}

struct CampaignRow: View {
    let campaign: CampaignSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(campaign.title)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                if campaign.isLive {
                    Text("Live")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.green)
                }
            }
            HStack(spacing: 8) {
                Label(campaign.sourceLabel, systemImage: campaign.source == .play ? "play.circle" : "testtube.2")
                Label(campaign.world, systemImage: "map")
                Label(campaign.dayLabel, systemImage: "clock")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            Text(campaign.location)
                .font(.caption)
                .lineLimit(1)
        }
        .padding(.vertical, 6)
    }
}

struct CampaignDetail: View {
    let campaign: CampaignSummary?
    let openAction: (CampaignSummary) -> Void

    var body: some View {
        if let campaign {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(campaign.title)
                            .font(.title2.weight(.semibold))
                        Text(campaign.id)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        openAction(campaign)
                    } label: {
                        Label("Open", systemImage: "rectangle.on.rectangle")
                    }
                    .buttonStyle(.borderedProminent)
                }

                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                    detailRow("Source", campaign.sourceLabel)
                    detailRow("Run", campaign.runID)
                    detailRow("World", campaign.world)
                    detailRow("Time", campaign.dayLabel)
                    detailRow("Location", campaign.location)
                    detailRow("Provider", campaign.provider)
                    detailRow("Updated", campaign.lastUpdate.formatted(date: .abbreviated, time: .standard))
                    detailRow("State root", campaign.stateRoot.path)
                }
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    Text("Party")
                        .font(.headline)
                    Text(campaign.partyLabel)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Spacer()
            }
            .padding(20)
        } else {
            EmptyStateView(title: "Select a Campaign", symbolName: "books.vertical")
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .textSelection(.enabled)
                .lineLimit(2)
        }
    }
}
