import Foundation

enum RepositoryLocator {
    static func defaultRepoPath() -> String {
        if let env = ProcessInfo.processInfo.environment["CLAWDND_REPO_ROOT"],
           looksLikeRepo(URL(fileURLWithPath: env)) {
            return env
        }

        let bundleURL = Bundle.main.bundleURL
        var cursor = bundleURL.deletingLastPathComponent()
        for _ in 0..<8 {
            if looksLikeRepo(cursor) {
                return cursor.path
            }
            cursor.deleteLastPathComponent()
        }

        let lexar = URL(fileURLWithPath: "/Volumes/LEXAR/repos/ClawDnD")
        if looksLikeRepo(lexar) {
            return lexar.path
        }

        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        if looksLikeRepo(cwd) {
            return cwd.path
        }

        return lexar.path
    }

    static func looksLikeRepo(_ url: URL) -> Bool {
        let viewer = url.appendingPathComponent("viewer/server.py").path
        let plugin = url.appendingPathComponent(".claude-plugin/plugin.json").path
        return FileManager.default.fileExists(atPath: viewer)
            && FileManager.default.fileExists(atPath: plugin)
    }
}
