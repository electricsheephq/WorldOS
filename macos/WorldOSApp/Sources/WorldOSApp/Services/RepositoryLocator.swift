import Foundation

enum RepositoryLocator {
    static func defaultRepoPath() -> String? {
        // Prefer WORLDOS_REPO_ROOT; fall back to the legacy CLAWDND_REPO_ROOT for
        // v1.x (issue #295, W0-E). Both resolve so existing launchers keep working.
        let environment = ProcessInfo.processInfo.environment
        if let env = environment["WORLDOS_REPO_ROOT"] ?? environment["CLAWDND_REPO_ROOT"] {
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

    static func looksLikeRepo(_ url: URL) -> Bool {
        let viewer = url.appendingPathComponent("viewer/server.py").path
        let plugin = url.appendingPathComponent(".claude-plugin/plugin.json").path
        return FileManager.default.fileExists(atPath: viewer)
            && FileManager.default.fileExists(atPath: plugin)
    }
}
