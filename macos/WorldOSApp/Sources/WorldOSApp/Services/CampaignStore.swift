import Foundation

@MainActor
final class CampaignStore: ObservableObject {
    @Published var campaigns: [CampaignSummary] = []
    @Published var lastError: String?

    private var reloadTask: Task<Void, Never>?

    /// Load the campaign shelf. `stateDir` is the .app's per-USER play-state ROOT (the same value
    /// passed to startViewer / startProviderSession; default ~/.worldos/state). The shipped game
    /// lane (play.sh) now writes the user's real campaigns under <stateDir>/<run-id>/campaigns, so
    /// the launcher must scan there — NOT only the dev repo's play-state/. We scan the user dir
    /// AND the repo-local play-state/qa-state (so an in-tree dev build still lists repo campaigns),
    /// de-duping by snapshot path. Passing an empty `stateDir` (the default) falls back to the
    /// per-user home, so callers that don't thread the setting still point at the user dir.
    func reload(repoPath: String, stateDir: String = "") {
        reloadTask?.cancel()
        let repoURL = URL(fileURLWithPath: repoPath)
        let trimmedStateDir = stateDir.trimmingCharacters(in: .whitespacesAndNewlines)
        let userStateRoot = trimmedStateDir.isEmpty
            ? RepositoryLocator.defaultUserStateDir()
            : (trimmedStateDir as NSString).expandingTildeInPath
        reloadTask = Task.detached(priority: .userInitiated) { [weak self] in
            do {
                // The per-USER play-state root the shipped app + play.sh use. Scanned the same
                // per-<run> way as the repo-local play-state/.
                let userPlay = try Self.loadCampaigns(
                    root: URL(fileURLWithPath: userStateRoot),
                    source: .play
                )
                let play = try Self.loadCampaigns(
                    root: repoURL.appendingPathComponent("play-state"),
                    source: .play
                )
                let qa = try Self.loadCampaigns(
                    root: repoURL.appendingPathComponent("qa/state"),
                    source: .qa
                )
                // De-dup by snapshot path: an in-tree dev build whose repo IS the user dir would
                // otherwise list every campaign twice.
                var seenSnapshots = Set<String>()
                let merged = (userPlay + play + qa)
                    .filter { seenSnapshots.insert($0.snapshotPath.standardizedFileURL.path).inserted }
                    .sorted { $0.lastUpdate > $1.lastUpdate }
                guard !Task.isCancelled else { return }
                await self?.finishReload(campaigns: merged, lastError: nil)
            } catch {
                guard !Task.isCancelled else { return }
                await self?.finishReload(campaigns: [], lastError: error.localizedDescription)
            }
        }
    }

    private func finishReload(campaigns: [CampaignSummary], lastError: String?) {
        self.campaigns = campaigns
        self.lastError = lastError
    }

    private nonisolated static func loadCampaigns(root: URL, source: CampaignSource) throws -> [CampaignSummary] {
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
                Self.loadSnapshot(
                    snapshotURL: campaignURL.appendingPathComponent("snapshot.json"),
                    stateRoot: runURL,
                    source: source
                )
            }
        }
    }

    private nonisolated static func loadSnapshot(
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
        let title = (root["title"] as? String).flatMap(Self.nonEmpty) ?? id
        let world = (root["world_id"] as? String).flatMap(Self.nonEmpty) ?? "unknown"
        let timeOfDay = (root["time_of_day"] as? String).flatMap(Self.nonEmpty) ?? ""
        let day = root["day"] as? Int
        let location = Self.currentLocationName(root)
        let party = Self.partyNames(root)
        let lastUpdate = Self.campaignRecency(snapshotURL: snapshotURL)
        let isLive = Date().timeIntervalSince(lastUpdate) < 120
        let provider = Self.inferProvider(stateRoot: stateRoot, source: source)

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

    private nonisolated static func currentLocationName(_ root: [String: Any]) -> String {
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

    private nonisolated static func partyNames(_ root: [String: Any]) -> [String] {
        guard let party = root["party"] as? [String],
              let characters = root["characters"] as? [String: Any] else {
            return []
        }
        return party.compactMap { id in
            guard let character = characters[id] as? [String: Any] else { return id }
            return (character["name"] as? String).flatMap(Self.nonEmpty) ?? id
        }
    }

    private nonisolated static func inferProvider(stateRoot: URL, source: CampaignSource) -> String {
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

    private nonisolated static func campaignRecency(snapshotURL: URL) -> Date {
        var best = Self.fileDate(snapshotURL)
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
            let date = Self.fileDate(log)
            if date > best {
                best = date
            }
        }
        return best
    }

    private nonisolated static func fileDate(_ url: URL) -> Date {
        let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
        return values?.contentModificationDate ?? .distantPast
    }

    private nonisolated static func nonEmpty(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
