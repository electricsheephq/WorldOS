import Foundation

protocol ProviderAdapter {
    var kind: ProviderKind { get }

    func detect(repoPath: URL, preferences: ProviderPreferences) -> ProviderStatus
    func startSession(
        world: String,
        runId: String,
        port: Int,
        companions: String,
        hero: String,
        repoPath: URL,
        preferences: ProviderPreferences
    ) throws -> ProviderLaunchRequest
    func stop(runId: String)
    func tailLogs(runId: String) -> String
}

struct ClaudeProvider: ProviderAdapter {
    let kind: ProviderKind = .claude

    func detect(repoPath: URL, preferences: ProviderPreferences) -> ProviderStatus {
        guard let claudePath = Shell.which("claude") else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "Install the Claude CLI to run the existing plugin play path.",
                detectedPath: nil
            )
        }
        let plugin = repoPath.appendingPathComponent(".claude-plugin/plugin.json").path
        guard FileManager.default.fileExists(atPath: plugin) else {
            return ProviderStatus(
                kind: kind,
                availability: .error,
                detail: "Claude CLI is installed, but .claude-plugin/plugin.json is missing from this repo.",
                detectedPath: claudePath
            )
        }
        return ProviderStatus(
            kind: kind,
            availability: .installed,
            detail: "Ready. Uses scripts/play_party.sh and the existing Claude plugin path.",
            detectedPath: claudePath
        )
    }

    func startSession(
        world: String,
        runId: String,
        port: Int,
        companions: String,
        hero: String,
        repoPath: URL,
        preferences: ProviderPreferences
    ) throws -> ProviderLaunchRequest {
        guard Shell.which("claude") != nil else {
            throw ProviderError.missingDependency("Claude CLI is missing. Install claude, then start the session again.")
        }

        var args = ["scripts/play_party.sh", world, runId, String(port)]
        if !companions.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            args.append(companions)
        }

        // An authored-hero spec (from the Creation wizard) rides as an ENV var, not a positional
        // arg — adding a 5th positional would shift the optional companion-spec slot. play.sh
        // reads CLAWDND_PLAY_HERO and pre-seeds that exact PC before the DM's first turn.
        var environment = budgetEnvironment(preferences)
        let trimmedHero = hero.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedHero.isEmpty {
            environment["CLAWDND_PLAY_HERO"] = trimmedHero
        }

        return ProviderLaunchRequest(
            name: "Claude game",
            executable: "/usr/bin/env",
            arguments: ["bash"] + args,
            environment: environment,
            workingDirectory: repoPath,
            message: "Claude session starting on port \(port)."
        )
    }

    func stop(runId: String) {}

    func tailLogs(runId: String) -> String {
        "Claude logs are captured through the app supervisor and play-state/<run-id>/dm*.jsonl."
    }
}

struct CodexProvider: ProviderAdapter {
    let kind: ProviderKind = .codex

    func detect(repoPath: URL, preferences: ProviderPreferences) -> ProviderStatus {
        let cli = Shell.which("codex")
        let wrapper = repoPath.appendingPathComponent("scripts/play_codex_actor.sh")
        let configuredCommand = preferences.codexCommand.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let cli else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "Codex CLI was not found. The Codex provider fails closed until codex is available.",
                detectedPath: nil
            )
        }

        guard FileManager.default.fileExists(atPath: wrapper.path) else {
            return ProviderStatus(
                kind: kind,
                availability: .error,
                detail: "Codex CLI found, but scripts/play_codex_actor.sh is missing from this checkout.",
                detectedPath: cli
            )
        }

        return ProviderStatus(
            kind: kind,
            availability: .configured,
            detail: configuredCommand.isEmpty
                ? "Ready. Launches the checked-in Codex wrapper with the WorldOS provider environment and player-facade-only tool surface."
                : "Ready. Launches your configured Codex command with the WorldOS provider environment and player-facade-only tool surface.",
            detectedPath: configuredCommand.isEmpty ? wrapper.path : cli
        )
    }

    func startSession(
        world: String,
        runId: String,
        port: Int,
        companions: String,
        hero: String,
        repoPath: URL,
        preferences: ProviderPreferences
    ) throws -> ProviderLaunchRequest {
        // hero (authored-PC spec) is only consumed by the Claude play path today; accepted here
        // to satisfy the protocol and ignored.
        _ = hero
        guard Shell.which("codex") != nil else {
            throw ProviderError.missingDependency("Codex CLI is missing. Install codex before starting a Codex provider session.")
        }

        let wrapper = repoPath.appendingPathComponent("scripts/play_codex_actor.sh")
        guard FileManager.default.fileExists(atPath: wrapper.path) else {
            throw ProviderError.configuration("Codex provider wrapper is missing: scripts/play_codex_actor.sh")
        }

        let configuredCommand = preferences.codexCommand.trimmingCharacters(in: .whitespacesAndNewlines)
        let command = configuredCommand.isEmpty ? defaultCodexCommand : configuredCommand
        return ProviderLaunchRequest(
            name: "Codex game",
            executable: "/bin/zsh",
            arguments: ["-lc", command],
            environment: providerEnvironment(
                kind: kind,
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                preferences: preferences
            ),
            workingDirectory: repoPath,
            message: "Codex provider command launched on port \(port)."
        )
    }

    func stop(runId: String) {}

    func tailLogs(runId: String) -> String {
        "Codex provider output is captured through the app supervisor and play-state/<run-id>/codex-provider/."
    }

    private var defaultCodexCommand: String {
        "scripts/play_codex_actor.sh"
    }
}

struct OpenClawProvider: ProviderAdapter {
    let kind: ProviderKind = .openclaw

    func detect(repoPath: URL, preferences: ProviderPreferences) -> ProviderStatus {
        let cli = Shell.which("openclaw")
        let config = Shell.fileExists("~/.openclaw")
        if preferences.openClawCommand.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if let cli {
                return ProviderStatus(
                    kind: kind,
                    availability: .installed,
                    detail: "OpenClaw CLI found. Configure a local WorldOS launch command before starting sessions.",
                    detectedPath: cli
                )
            }
            if config {
                return ProviderStatus(
                    kind: kind,
                    availability: .installed,
                    detail: "OpenClaw config detected. Configure a local launch command to enable game starts.",
                    detectedPath: "~/.openclaw"
                )
            }
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "OpenClaw was not found. This adapter fails closed until a valid local command exists.",
                detectedPath: nil
            )
        }
        return ProviderStatus(
            kind: kind,
            availability: .configured,
            detail: "Configured. The app will launch your command with WorldOS provider environment variables.",
            detectedPath: cli
        )
    }

    func startSession(
        world: String,
        runId: String,
        port: Int,
        companions: String,
        hero: String,
        repoPath: URL,
        preferences: ProviderPreferences
    ) throws -> ProviderLaunchRequest {
        // hero (authored-PC spec) is only consumed by the Claude play path today; accepted here
        // to satisfy the protocol and ignored.
        _ = hero
        let command = preferences.openClawCommand.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else {
            throw ProviderError.configuration("OpenClaw provider is not launch-configured. Set an OpenClaw provider command in Settings.")
        }
        return ProviderLaunchRequest(
            name: "OpenClaw game",
            executable: "/bin/zsh",
            arguments: ["-lc", command],
            environment: providerEnvironment(
                kind: kind,
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                preferences: preferences
            ),
            workingDirectory: repoPath,
            message: "OpenClaw provider command launched on port \(port)."
        )
    }

    func stop(runId: String) {}

    func tailLogs(runId: String) -> String {
        "OpenClaw provider output is captured through the app supervisor."
    }
}

struct ProviderRegistry {
    private let adapters: [ProviderKind: ProviderAdapter] = [
        .claude: ClaudeProvider(),
        .codex: CodexProvider(),
        .openclaw: OpenClawProvider()
    ]

    func adapter(for kind: ProviderKind) -> ProviderAdapter {
        guard let adapter = adapters[kind] else {
            preconditionFailure("No ProviderAdapter registered for \(kind.rawValue)")
        }
        return adapter
    }

    func detectAll(repoPath: URL, preferences: ProviderPreferences) -> [ProviderStatus] {
        ProviderKind.allCases.map { adapter(for: $0).detect(repoPath: repoPath, preferences: preferences) }
    }
}

private func budgetEnvironment(_ preferences: ProviderPreferences) -> [String: String] {
    var env: [String: String] = [:]
    if !preferences.budget.isEmpty {
        env["CLAWDND_PLAY_BUDGET"] = preferences.budget
    }
    if !preferences.sessionBudget.isEmpty {
        env["CLAWDND_PLAY_SESSION_BUDGET"] = preferences.sessionBudget
    }
    if !preferences.maxTurns.isEmpty {
        env["CLAWDND_PLAY_MAX_TURNS"] = preferences.maxTurns
    }
    if !preferences.artRepoPath.isEmpty {
        env["WORLDOS_ART_REPO_ROOT"] = preferences.artRepoPath
        env["CLAWDND_ART_REPO_ROOT"] = preferences.artRepoPath
    }
    return env
}

private func providerEnvironment(
    kind: ProviderKind,
    world: String,
    runId: String,
    port: Int,
    companions: String,
    preferences: ProviderPreferences
) -> [String: String] {
    var env = budgetEnvironment(preferences)
    env["CLAWDND_PROVIDER"] = kind.rawValue
    env["CLAWDND_WORLD"] = world
    env["CLAWDND_RUN_ID"] = runId
    env["CLAWDND_PLAY_PORT"] = String(port)
    env["CLAWDND_PLAY_COMPANIONS"] = companions
    return env
}
