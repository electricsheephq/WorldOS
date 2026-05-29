import Foundation

enum DependencyChecker {
    static let required: [(String, String)] = [
        ("python3", "viewer"),
        ("claude", "Claude provider"),
        ("uv", "engine/rules/voice servers"),
        ("jq", "play scripts"),
        ("curl", "viewer health checks")
    ]

    static func check() -> [DependencyStatus] {
        required.map { command, use in
            DependencyStatus(command: command, requiredFor: use, path: Shell.which(command))
        }
    }
}
