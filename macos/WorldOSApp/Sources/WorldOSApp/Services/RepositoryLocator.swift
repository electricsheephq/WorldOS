import Foundation

enum RepositoryLocator {
    static func launchRepoPathOverride() -> String? {
        guard preferLaunchRoots() else { return nil }
        let environment = ProcessInfo.processInfo.environment
        let candidates: [String?] = [
            environment["WORLDOS_REPO_ROOT"],
            Bundle.main.object(forInfoDictionaryKey: "WorldOSRepoRoot") as? String,
        ]
        for raw in candidates {
            guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                continue
            }
            let expanded = (raw as NSString).expandingTildeInPath
            if looksLikeRepo(URL(fileURLWithPath: expanded)) {
                return expanded
            }
        }
        return nil
    }

    static func launchArtRepoPathOverride() -> String? {
        guard preferLaunchRoots() else { return nil }
        let environment = ProcessInfo.processInfo.environment
        let candidates: [String?] = [
            environment["WORLDOS_ART_REPO_ROOT"],
            Bundle.main.object(forInfoDictionaryKey: "WorldOSArtRepoRoot") as? String,
        ]
        for raw in candidates {
            guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                continue
            }
            let expanded = (raw as NSString).expandingTildeInPath
            if looksLikeArtRepo(URL(fileURLWithPath: expanded)) {
                return expanded
            }
        }
        return nil
    }

    static func defaultRepoPath() -> String? {
        // Read WORLDOS_REPO_ROOT, else walk up from the bundle to find the repo root.
        
        let environment = ProcessInfo.processInfo.environment
        if let env = environment["WORLDOS_REPO_ROOT"] {
            let expanded = (env as NSString).expandingTildeInPath
            if looksLikeRepo(URL(fileURLWithPath: expanded)) {
                return expanded
            }
        }

        let bundleURL = Bundle.main.bundleURL
        var cursor = bundleURL.deletingLastPathComponent()
        for _ in 0..<8 {
            if looksLikeRepo(cursor) {
                return cursor.path
            }
            cursor.deleteLastPathComponent()
        }

        let homeRepo = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("repos/WorldOS")
        if looksLikeRepo(homeRepo) {
            return homeRepo.path
        }

        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        if looksLikeRepo(cwd) {
            return cwd.path
        }

        return nil
    }

    static func defaultArtRepoPath() -> String? {
        let environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates: [String?] = [
            environment["WORLDOS_ART_REPO_ROOT"],
            Bundle.main.object(forInfoDictionaryKey: "WorldOSArtRepoRoot") as? String,
            defaultRepoPath(),
            home.appendingPathComponent("WorldOS").path,
            home.appendingPathComponent("repos/WorldOS").path,
        ]
        for raw in candidates {
            guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                continue
            }
            let expanded = (raw as NSString).expandingTildeInPath
            if looksLikeArtRepo(URL(fileURLWithPath: expanded)) {
                return expanded
            }
        }
        return nil
    }

    static func looksLikeRepo(_ url: URL) -> Bool {
        let viewer = url.appendingPathComponent("viewer/server.py").path
        let plugin = url.appendingPathComponent(".claude-plugin/plugin.json").path
        return FileManager.default.fileExists(atPath: viewer)
            && FileManager.default.fileExists(atPath: plugin)
    }

    static func looksLikeArtRepo(_ url: URL) -> Bool {
        let privateWorlds = url.appendingPathComponent("content/worlds/_private").path
        return FileManager.default.fileExists(atPath: privateWorlds)
    }

    /// The DEFAULT play-state ROOT for a SHIPPED .app. A shipped app must NOT read or write the
    /// dev repo's play-state/, so the default state dir is the engine's OWN per-user home —
    /// mirroring servers/engine/store.state_dir() + viewer/server.py:_state_dir():
    ///   1. an explicit WORLDOS_STATE_DIR env (a power user / QA override),
    ///   2. else ~/.worldos/state (created lazily by the engine on first write).
    /// play.sh nests each game under <root>/<run-id>, so this is the play-state ROOT (the same role
    /// the repo's play-state/ plays for a dev build). The result is passed to startViewer /
    /// startProviderSession as `stateDir`, which exports WORLDOS_STATE_DIR for the viewer + play.sh.
    static func defaultUserStateDir() -> String {
        let environment = ProcessInfo.processInfo.environment
        if let raw = environment["WORLDOS_STATE_DIR"],
           !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return (raw as NSString).expandingTildeInPath
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".worldos").appendingPathComponent("state").path
    }

    private static func preferLaunchRoots() -> Bool {
        let environment = ProcessInfo.processInfo.environment
        if let raw = environment["WORLDOS_PREFER_LAUNCH_ROOTS"] {
            return boolValue(raw)
        }
        if let value = Bundle.main.object(forInfoDictionaryKey: "WorldOSPreferLaunchRoots") {
            if let bool = value as? Bool {
                return bool
            }
            return boolValue("\(value)")
        }
        return false
    }

    private static func boolValue(_ raw: String) -> Bool {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "1", "true", "yes", "on":
            return true
        default:
            return false
        }
    }
}
