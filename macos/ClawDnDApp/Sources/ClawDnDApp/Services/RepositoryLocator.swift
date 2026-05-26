import Foundation

enum RepositoryLocator {
    static func defaultRepoPath() -> String? {
        if let openWorldsRepo = defaultOpenWorldsRepoPath() {
            return openWorldsRepo
        }

        for candidate in candidateURLs() where looksLikeRepo(candidate) {
            return candidate.standardizedFileURL.path
        }

        return nil
    }

    static func defaultOpenWorldsRepoPath() -> String? {
        for candidate in candidateURLs() where supportsOpenWorldsViewer(candidate) {
            if looksLikeRepo(candidate) {
                return candidate.standardizedFileURL.path
            }
        }
        return nil
    }

    static func validRepoPath(_ rawPath: String) -> String? {
        let trimmed = rawPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let expanded = (trimmed as NSString).expandingTildeInPath
        let url = URL(fileURLWithPath: expanded)
        return looksLikeRepo(url) ? url.standardizedFileURL.path : nil
    }

    static func openWorldsRepoPath(_ rawPath: String) -> String? {
        let trimmed = rawPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let expanded = (trimmed as NSString).expandingTildeInPath
        let url = URL(fileURLWithPath: expanded)
        return looksLikeRepo(url) && supportsOpenWorldsViewer(url) ? url.standardizedFileURL.path : nil
    }

    private static func candidateURLs() -> [URL] {
        var candidates: [URL] = []

        func append(_ url: URL?) {
            guard let url else { return }
            let standardized = url.standardizedFileURL
            guard !candidates.contains(standardized) else { return }
            candidates.append(standardized)
        }

        if let env = ProcessInfo.processInfo.environment["CLAWDND_REPO_ROOT"],
           let valid = validRepoPath(env) {
            append(URL(fileURLWithPath: valid))
        }

        let bundleURL = Bundle.main.bundleURL
        var cursor = bundleURL.deletingLastPathComponent()
        for _ in 0..<8 {
            append(cursor)
            cursor.deleteLastPathComponent()
        }

        append(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("repos/ClawDnD"))
        append(URL(fileURLWithPath: "/Volumes/LEXAR/repos/ClawDnD"))

        let lexarRepos = URL(fileURLWithPath: "/Volumes/LEXAR/repos", isDirectory: true)
        if let children = try? FileManager.default.contentsOfDirectory(
            at: lexarRepos,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) {
            let clawRepos = children
                .filter { $0.lastPathComponent.localizedCaseInsensitiveContains("ClawDnD") }
                .sorted { lhs, rhs in
                    if lhs.lastPathComponent == "ClawDnD" { return true }
                    if rhs.lastPathComponent == "ClawDnD" { return false }
                    return lhs.lastPathComponent < rhs.lastPathComponent
                }
            clawRepos.forEach { append($0) }
        }

        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        append(cwd)

        return candidates
    }

    static func looksLikeRepo(_ url: URL) -> Bool {
        let viewer = url.appendingPathComponent("viewer/server.py").path
        let plugin = url.appendingPathComponent(".claude-plugin/plugin.json").path
        return FileManager.default.fileExists(atPath: viewer)
            && FileManager.default.fileExists(atPath: plugin)
    }

    static func supportsOpenWorldsViewer(_ url: URL) -> Bool {
        let serverURL = url.appendingPathComponent("viewer/server.py")
        guard let source = try? String(contentsOf: serverURL, encoding: .utf8) else {
            return false
        }
        return source.contains("CLAWDND_OPENWORLDS_DIR")
            && source.contains("/openworlds/config.json")
    }
}
