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
    let availability: ProviderAvailability
    let detail: String
    let detectedPath: String?

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
    let codexCommand: String
    let openClawCommand: String
    let budget: String
    let sessionBudget: String
    let maxTurns: String
    let artRepoPath: String
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
