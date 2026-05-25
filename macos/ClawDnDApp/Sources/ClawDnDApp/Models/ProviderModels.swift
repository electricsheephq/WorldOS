import Foundation

enum ProviderKind: String, CaseIterable, Identifiable {
    case claude
    case codex
    case openclaw

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .claude: "Claude"
        case .codex: "Codex"
        case .openclaw: "OpenClaw"
        }
    }

    var symbolName: String {
        switch self {
        case .claude: "sparkles"
        case .codex: "terminal"
        case .openclaw: "link"
        }
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
}

struct ProviderLaunchRequest {
    let name: String
    let executable: String
    let arguments: [String]
    let environment: [String: String]
    let workingDirectory: URL
    let message: String
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
