import Foundation

@MainActor
final class CampaignStore: ObservableObject {
    @Published var campaigns: [CampaignSummary] = []
    @Published var lastError: String?

    func reload(repoPath: String) {
        let repoURL = URL(fileURLWithPath: repoPath)
        do {
            let play = try loadCampaigns(
                root: repoURL.appendingPathComponent("play-state"),
                source: .play
            )
            let qa = try loadCampaigns(
                root: repoURL.appendingPathComponent("qa/state"),
                source: .qa
            )
            campaigns = (play + qa).sorted { $0.lastUpdate > $1.lastUpdate }
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func loadCampaigns(root: URL, source: CampaignSource) throws -> [CampaignSummary] {
        guard FileManager.default.fileExists(atPath: root.path) else { return [] }
        let runDirectories = try FileManager.default.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        )

        return runDirectories.flatMap { runURL -> [CampaignSummary] in
            let campaignsDir = runURL.appendingPathComponent("campaigns")
            guard FileManager.default.fileExists(atPath: campaignsDir.path) else { return [] }
            let campaignDirectories = (try? FileManager.default.contentsOfDirectory(
                at: campaignsDir,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )) ?? []
            return campaignDirectories.compactMap { campaignURL in
                loadSnapshot(
                    snapshotURL: campaignURL.appendingPathComponent("snapshot.json"),
                    stateRoot: runURL,
                    source: source
                )
            }
        }
    }

    private func loadSnapshot(
        snapshotURL: URL,
        stateRoot: URL,
        source: CampaignSource
    ) -> CampaignSummary? {
        guard let data = try? Data(contentsOf: snapshotURL),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = root["id"] as? String else {
            return nil
        }

        let runID = stateRoot.lastPathComponent
        let title = (root["title"] as? String).flatMap(nonEmpty) ?? id
        let world = (root["world_id"] as? String).flatMap(nonEmpty) ?? "unknown"
        let timeOfDay = (root["time_of_day"] as? String).flatMap(nonEmpty) ?? ""
        let day = root["day"] as? Int
        let location = currentLocationName(root)
        let party = partyNames(root)
        let lastUpdate = campaignRecency(snapshotURL: snapshotURL)
        let isLive = Date().timeIntervalSince(lastUpdate) < 120
        let provider = inferProvider(stateRoot: stateRoot, source: source)

        return CampaignSummary(
            id: id,
            runID: runID,
            source: source,
            snapshotPath: snapshotURL,
            stateRoot: stateRoot,
            title: title,
            world: world,
            day: day,
            timeOfDay: timeOfDay,
            location: location,
            party: party,
            provider: provider,
            lastUpdate: lastUpdate,
            isLive: isLive
        )
    }

    private func currentLocationName(_ root: [String: Any]) -> String {
        guard let locID = root["current_location_id"] as? String else {
            return "Unknown location"
        }
        if let locations = root["locations"] as? [String: Any],
           let location = locations[locID] as? [String: Any],
           let name = location["name"] as? String,
           !name.isEmpty {
            return name
        }
        return locID
    }

    private func partyNames(_ root: [String: Any]) -> [String] {
        guard let party = root["party"] as? [String],
              let characters = root["characters"] as? [String: Any] else {
            return []
        }
        return party.compactMap { id in
            guard let character = characters[id] as? [String: Any] else { return id }
            return (character["name"] as? String).flatMap(nonEmpty) ?? id
        }
    }

    private func inferProvider(stateRoot: URL, source: CampaignSource) -> String {
        switch source {
        case .qa:
            return "QA"
        case .play:
            if FileManager.default.fileExists(atPath: stateRoot.appendingPathComponent("companion_0.mcp.json").path) {
                return "Claude party"
            }
            if FileManager.default.fileExists(atPath: stateRoot.appendingPathComponent("dm.mcp.json").path) {
                return "Claude"
            }
            return "Local"
        }
    }

    private func campaignRecency(snapshotURL: URL) -> Date {
        var best = fileDate(snapshotURL)
        let sessionsURL = snapshotURL
            .deletingLastPathComponent()
            .appendingPathComponent("sessions")
        guard let logs = try? FileManager.default.contentsOfDirectory(
            at: sessionsURL,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else {
            return best
        }
        for log in logs where log.pathExtension == "jsonl" {
            let date = fileDate(log)
            if date > best {
                best = date
            }
        }
        return best
    }

    private func fileDate(_ url: URL) -> Date {
        let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
        return values?.contentModificationDate ?? .distantPast
    }

    private func nonEmpty(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
