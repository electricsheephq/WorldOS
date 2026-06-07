import Foundation

enum ProviderKind: String, CaseIterable, Identifiable {
    case claude
    case codex
    case openclaw
    case scripted

    static var allCases: [ProviderKind] {
        var cases: [ProviderKind] = [.claude, .codex, .openclaw]
        if scriptedProviderEnabled {
            cases.append(.scripted)
        }
        return cases
    }

    static var scriptedProviderEnabled: Bool {
        if ProcessInfo.processInfo.environment["WORLDOS_ENABLE_SCRIPTED_PROVIDER"] == "1" {
            return true
        }
        if let bundled = Bundle.main.object(forInfoDictionaryKey: "WorldOSEnableScriptedProvider") as? Bool {
            return bundled
        }
        return false
    }

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .claude: "Claude"
        case .codex: "Codex"
        case .openclaw: "OpenClaw"
        case .scripted: "Scripted"
        }
    }

    var symbolName: String {
        switch self {
        case .claude: "sparkles"
        case .codex: "terminal"
        case .openclaw: "link"
        case .scripted: "scroll"
        }
    }

    var providerFamily: String {
        switch self {
        case .claude: "anthropic"
        case .codex: "codex-openai"
        case .openclaw: "openclaw"
        case .scripted: "scripted"
        }
    }

    var authSurface: String {
        switch self {
        case .claude: "claude-cli"
        case .codex: "codex-cli"
        case .openclaw: "openclaw-cli"
        case .scripted: "dev-scripted"
        }
    }

    var isLaunchEnabled: Bool {
        self != .scripted || Self.scriptedProviderEnabled
    }
}

enum ProviderAvailability: String, Equatable {
    case installed
    case configured
    case missing
    case error
}

struct ProviderStatus: Identifiable, Equatable {
    var id: String { kind.rawValue }
    let kind: ProviderKind
    let providerFamily: String
    let authSurface: String
    let dmModel: String
    let playerModel: String
    let scorerModel: String
    let commandOverride: String
    let availability: ProviderAvailability
    let detail: String
    let detectedPath: String?

    init(
        kind: ProviderKind,
        availability: ProviderAvailability,
        detail: String,
        detectedPath: String?,
        preferences: ProviderPreferences,
        commandOverride: String = ""
    ) {
        self.kind = kind
        self.providerFamily = kind.providerFamily
        self.authSurface = kind.authSurface
        self.dmModel = preferences.dmModel(for: kind)
        self.playerModel = preferences.playerModel(for: kind)
        self.scorerModel = preferences.scorerModel(for: kind)
        self.commandOverride = commandOverride.trimmingCharacters(in: .whitespacesAndNewlines)
        self.availability = availability
        self.detail = detail
        self.detectedPath = detectedPath
    }

    var isLaunchable: Bool {
        availability == .configured || (kind == .claude && availability == .installed)
    }
}

struct ProviderRun: Identifiable, Equatable {
    let id: String
    let provider: ProviderKind
    let processID: Int32?
    let message: String
    let startedAt: Date
}

struct ProviderPreferences {
    static let defaultClaudeDMModel = "opus"
    static let defaultCodexDMModel = "gpt-5.5"
    static let defaultOpenClawDMModel = ""
    static let defaultClaudePlayerModel = "sonnet"
    static let defaultCodexPlayerModel = "gpt-5.5"
    static let defaultOpenClawPlayerModel = ""
    static let defaultClaudeScorerModel = "sonnet"
    static let defaultCodexScorerModel = "gpt-5.5"
    static let defaultOpenClawScorerModel = ""

    let codexCommand: String
    let openClawCommand: String
    let claudeDMModel: String
    let codexDMModel: String
    let openClawDMModel: String
    let claudePlayerModel: String
    let codexPlayerModel: String
    let openClawPlayerModel: String
    let claudeScorerModel: String
    let codexScorerModel: String
    let openClawScorerModel: String
    let budget: String
    let sessionBudget: String
    let maxTurns: String
    let artRepoPath: String

    func dmModel(for kind: ProviderKind) -> String {
        switch kind {
        case .claude: trimmedOrDefault(claudeDMModel, Self.defaultClaudeDMModel)
        case .codex: trimmedOrDefault(codexDMModel, Self.defaultCodexDMModel)
        case .openclaw: trimmedOrDefault(openClawDMModel, Self.defaultOpenClawDMModel)
        case .scripted: "scripted"
        }
    }

    func playerModel(for kind: ProviderKind) -> String {
        switch kind {
        case .claude: trimmedOrDefault(claudePlayerModel, Self.defaultClaudePlayerModel)
        case .codex: trimmedOrDefault(codexPlayerModel, Self.defaultCodexPlayerModel)
        case .openclaw: trimmedOrDefault(openClawPlayerModel, Self.defaultOpenClawPlayerModel)
        case .scripted: "scripted"
        }
    }

    func scorerModel(for kind: ProviderKind) -> String {
        switch kind {
        case .claude: trimmedOrDefault(claudeScorerModel, Self.defaultClaudeScorerModel)
        case .codex: trimmedOrDefault(codexScorerModel, Self.defaultCodexScorerModel)
        case .openclaw: trimmedOrDefault(openClawScorerModel, Self.defaultOpenClawScorerModel)
        case .scripted: "deterministic"
        }
    }

    private func trimmedOrDefault(_ value: String, _ defaultValue: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? defaultValue : trimmed
    }
}

struct ProviderLaunchRequest {
    let name: String
    let executable: String
    let arguments: [String]
    let environment: [String: String]
    let workingDirectory: URL
    let message: String
}

struct ProviderLaunchMetadata: Equatable {
    let kind: ProviderKind
    let processName: String
    let executable: String
    let arguments: [String]
    let workingDirectory: URL
    let environment: [String: String]
    let providerFamily: String
    let authSurface: String
    let dmModel: String
    let playerModel: String
    let scorerModel: String
    let world: String
    let runId: String
    let port: Int
    let statePath: String?
    let message: String
    var processID: Int32?
    var launchedAt: Date?
    var exitedAt: Date?
    var exitStatus: Int32?
    var lastError: String?

    var commandLine: String {
        ([executable] + arguments).joined(separator: " ")
    }
}

enum ProviderError: LocalizedError {
    case missingDependency(String)
    case configuration(String)

    var errorDescription: String? {
        switch self {
        case .missingDependency(let message), .configuration(let message):
            message
        }
    }
}
