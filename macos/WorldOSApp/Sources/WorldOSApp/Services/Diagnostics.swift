import AppKit
import Foundation

enum Diagnostics {
    static let sensitiveKeyFragments = ["KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "COOKIE"]
    private static let sensitiveFlags = [
        "--api-key",
        "--apikey",
        "--auth",
        "--authorization",
        "--cookie",
        "--password",
        "--secret",
        "--token"
    ]

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

    static func redactedText(_ text: String) -> String {
        var redacted = text
        let replacements = [
            (#"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|AUTH|COOKIE)[A-Z0-9_]*)=(?:"[^"]*"|'[^']*'|[^\s'"]+)"#, "$1=<redacted>"),
            (#"(?i)(--[A-Za-z0-9_-]*(?:api[-_]?key|auth(?:orization)?|cookie|password|secret|token)[A-Za-z0-9_-]*(?:=|\s+))(?:"[^"]*"|'[^']*'|[^\s'"]+)"#, "$1<redacted>"),
            (#"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"#, "$1 <redacted>"),
            (#"\b(sk-[A-Za-z0-9_-]{8,})\b"#, "<redacted>"),
            (#"\b(github_pat_[A-Za-z0-9_]{20,})\b"#, "<redacted>"),
            (#"\b(gh[pousr]_[A-Za-z0-9_]{20,})\b"#, "<redacted>"),
            (#"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"#, "<redacted>")
        ]
        for (pattern, template) in replacements {
            redacted = replacing(pattern: pattern, in: redacted, with: template)
        }
        return redacted
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
        Provider family: \(metadata.providerFamily)
        Auth surface: \(metadata.authSurface)
        DM model: \(metadata.dmModel.isEmpty ? "default" : metadata.dmModel)
        Player/test model: \(metadata.playerModel.isEmpty ? "default" : metadata.playerModel)
        Scorer model: \(metadata.scorerModel.isEmpty ? "default" : metadata.scorerModel)
        Process: \(metadata.processName)
        PID: \(metadata.processID.map(String.init) ?? "none")
        Run ID: \(metadata.runId)
        World: \(metadata.world)
        Port: \(metadata.port)
        Repo path: \(metadata.workingDirectory.path)
        State path: \(statePath)
        Executable: \(metadata.executable)
        Arguments: \(redactedArguments(metadata.arguments).joined(separator: " "))
        Command: \(redactedCommandLine(executable: metadata.executable, arguments: metadata.arguments))
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
        return redactedText(value)
    }

    private static func redactedArguments(_ arguments: [String]) -> [String] {
        var shouldRedactNext = false
        var shouldRedactShellCommand = false
        return arguments.map { argument in
            if shouldRedactShellCommand {
                shouldRedactShellCommand = false
                return "<configured command redacted>"
            }
            if shouldRedactNext {
                shouldRedactNext = false
                return "<redacted>"
            }
            if argument == "-c" || argument == "-lc" {
                shouldRedactShellCommand = true
                return argument
            }
            if sensitiveFlags.contains(argument.lowercased()) {
                shouldRedactNext = true
                return argument
            }
            return redactedText(argument)
        }
    }

    private static func redactedCommandLine(executable: String, arguments: [String]) -> String {
        ([executable] + redactedArguments(arguments)).joined(separator: " ")
    }

    private static func replacing(pattern: String, in text: String, with template: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return text
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return regex.stringByReplacingMatches(in: text, range: range, withTemplate: template)
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
