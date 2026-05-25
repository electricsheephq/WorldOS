import AppKit
import Foundation

enum Diagnostics {
    static let sensitiveKeyFragments = ["KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "COOKIE"]

    @MainActor
    static func copy(processService: AppProcessService) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(processService.diagnostics, forType: .string)
    }

    static func redactedEnvironmentSummary(_ environment: [String: String]) -> String {
        guard !environment.isEmpty else { return "none" }
        return environment
            .keys
            .sorted()
            .map { key in
                "\(key)=\(redactedValue(forKey: key, value: environment[key] ?? ""))"
            }
            .joined(separator: "\n")
    }

    static func providerLaunchSummary(_ metadata: ProviderLaunchMetadata?) -> String {
        guard let metadata else {
            return "Provider launch: none"
        }

        let launchedAt = metadata.launchedAt.map(Self.formatDate) ?? "not launched"
        let exitedAt = metadata.exitedAt.map(Self.formatDate) ?? "not exited"
        let exitStatus = metadata.exitStatus.map(String.init) ?? "running or unknown"
        let statePath = normalizedOptional(metadata.statePath)

        return """
        Provider launch:
        Kind: \(metadata.kind.rawValue)
        Process: \(metadata.processName)
        PID: \(metadata.processID.map(String.init) ?? "none")
        Run ID: \(metadata.runId)
        World: \(metadata.world)
        Port: \(metadata.port)
        Repo path: \(metadata.workingDirectory.path)
        State path: \(statePath)
        Executable: \(metadata.executable)
        Arguments: \(metadata.arguments.joined(separator: " "))
        Command: \(metadata.commandLine)
        Environment overrides:
        \(redactedEnvironmentSummary(metadata.environment))
        Launched at: \(launchedAt)
        Exited at: \(exitedAt)
        Exit status: \(exitStatus)
        Last error: \(metadata.lastError ?? "none")
        Message: \(metadata.message)
        """
    }

    static func lastLines(_ text: String, limit: Int = 40) -> String {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        guard !lines.isEmpty else { return "none" }
        return lines.suffix(limit).joined(separator: "\n")
    }

    private static func redactedValue(forKey key: String, value: String) -> String {
        let uppercasedKey = key.uppercased()
        if sensitiveKeyFragments.contains(where: { uppercasedKey.contains($0) }) {
            return "<redacted>"
        }
        return value
    }

    private static func normalizedOptional(_ value: String?) -> String {
        guard let value,
              !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            return "none"
        }
        return value
    }

    private static func formatDate(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
}
