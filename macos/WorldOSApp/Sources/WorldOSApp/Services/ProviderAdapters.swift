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
                detectedPath: nil,
                preferences: preferences
            )
        }
        let plugin = repoPath.appendingPathComponent(".claude-plugin/plugin.json").path
        guard FileManager.default.fileExists(atPath: plugin) else {
            return ProviderStatus(
                kind: kind,
                availability: .error,
                detail: "Claude CLI is installed, but .claude-plugin/plugin.json is missing from this repo.",
                detectedPath: claudePath,
                preferences: preferences
            )
        }
        return ProviderStatus(
            kind: kind,
            availability: .installed,
            detail: "Ready. Uses scripts/play_party.sh and the existing Claude plugin path.",
            detectedPath: claudePath,
            preferences: preferences
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
        // reads WORLDOS_PLAY_HERO and pre-seeds that exact PC before the DM's first turn.
        var environment = budgetEnvironment(preferences)
        let trimmedHero = hero.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedHero.isEmpty {
            environment["WORLDOS_PLAY_HERO"] = trimmedHero
        }

        // Tell the move-sink viewer which provider is driving the run. The viewer's
        // /app-status readiness gates ALL play controls on a non-empty PROVIDER that is in
        // {codex, claude, openclaw, scripted} (viewer/server.py: provider_ready + ready_for_play);
        // without it, /session-surface reports can_act:true but /app-status reports
        // no_provider, so every action button stays locked ("live provider move sink is not
        // ready"). The Codex/OpenClaw/scripted lanes set this via providerEnvironment(); the
        // Claude lane builds its own environment, so set it directly here. We set both the
        // WORLDOS_ name the viewer's env_var() reads
        // back to, mirroring how the other lanes (and budgetEnvironment) carry both prefixes.
        environment["WORLDOS_PROVIDER"] = kind.rawValue
        setProviderModelEnvironment(kind: kind, preferences: preferences, environment: &environment)

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
        let wrapper = repoPath.appendingPathComponent("scripts/play_codex_dm.sh")
        let actorHelper = repoPath.appendingPathComponent("scripts/play_codex_actor.sh")
        let configuredCommand = preferences.codexCommand.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let cli else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "Codex CLI was not found. The Codex provider fails closed until codex is available.",
                detectedPath: nil,
                preferences: preferences,
                commandOverride: configuredCommand
            )
        }

        if configuredCommand.isEmpty {
            guard FileManager.default.fileExists(atPath: wrapper.path) else {
                return ProviderStatus(
                    kind: kind,
                    availability: .error,
                    detail: "Codex CLI found, but scripts/play_codex_dm.sh is missing from this checkout.",
                    detectedPath: cli,
                    preferences: preferences
                )
            }
        }

        return ProviderStatus(
            kind: kind,
            availability: .configured,
            detail: configuredCommand.isEmpty
                ? "Ready. Launches the checked-in Codex DM wrapper with the WorldOS provider environment. Actor helper: \(actorHelper.path)."
                : "Ready. Launches your configured Codex command with the WorldOS provider environment.",
            detectedPath: configuredCommand.isEmpty ? wrapper.path : configuredCommand,
            preferences: preferences,
            commandOverride: configuredCommand
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
        guard Shell.which("codex") != nil else {
            throw ProviderError.missingDependency("Codex CLI is missing. Install codex before starting a Codex provider session.")
        }

        let configuredCommand = preferences.codexCommand.trimmingCharacters(in: .whitespacesAndNewlines)
        if configuredCommand.isEmpty {
            let wrapper = repoPath.appendingPathComponent("scripts/play_codex_dm.sh")
            guard FileManager.default.fileExists(atPath: wrapper.path) else {
                throw ProviderError.configuration("Codex provider wrapper is missing: scripts/play_codex_dm.sh")
            }
        }
        let command = configuredCommand.isEmpty ? defaultCodexCommand : configuredCommand
        return ProviderLaunchRequest(
            name: "Codex game",
            // #892: `-f -c` skips ALL rc files (incl. ~/.zshenv) — a login/rc shell would source the
            // user's profile and fire its side effects (keychain/Eva/removable-disk prompts). The CLIs
            // are on PATH via the startup tool-dir prepend (EnvironmentBootstrap), inherited here.
            executable: "/bin/zsh",
            arguments: ["-f", "-c", command],
            environment: providerEnvironment(
                kind: kind,
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                hero: hero,
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
        "scripts/play_codex_dm.sh"
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
                    detectedPath: cli,
                    preferences: preferences
                )
            }
            if config {
                return ProviderStatus(
                    kind: kind,
                    availability: .installed,
                    detail: "OpenClaw config detected. Configure a local launch command to enable game starts.",
                    detectedPath: "~/.openclaw",
                    preferences: preferences
                )
            }
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "OpenClaw was not found. This adapter fails closed until a valid local command exists.",
                detectedPath: nil,
                preferences: preferences
            )
        }
        return ProviderStatus(
            kind: kind,
            availability: .configured,
            detail: "Configured. The app will launch your command with WorldOS provider environment variables.",
            detectedPath: cli,
            preferences: preferences,
            commandOverride: preferences.openClawCommand
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
        let command = preferences.openClawCommand.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else {
            throw ProviderError.configuration("OpenClaw provider is not launch-configured. Set an OpenClaw provider command in Settings.")
        }
        return ProviderLaunchRequest(
            name: "OpenClaw game",
            // #892: `-f -c` skips ALL rc files (incl. ~/.zshenv) so app/game start never sources the
            // user's profile (keychain/Eva/removable-disk side effects). CLIs are on PATH via the
            // startup tool-dir prepend (EnvironmentBootstrap), inherited here.
            executable: "/bin/zsh",
            arguments: ["-f", "-c", command],
            environment: providerEnvironment(
                kind: kind,
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                hero: hero,
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

struct ScriptedProvider: ProviderAdapter {
    let kind: ProviderKind = .scripted

    func detect(repoPath: URL, preferences: ProviderPreferences) -> ProviderStatus {
        guard ProviderKind.scriptedProviderEnabled else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "Hidden. Set WORLDOS_ENABLE_SCRIPTED_PROVIDER=1 to expose deterministic smoke.",
                detectedPath: nil,
                preferences: preferences
            )
        }
        let script = repoPath.appendingPathComponent("scripts/play_scripted_dm.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            return ProviderStatus(
                kind: kind,
                availability: .error,
                detail: "Scripted provider helper is missing: scripts/play_scripted_dm.sh",
                detectedPath: nil,
                preferences: preferences
            )
        }
        guard Shell.which("python3") != nil else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "python3 is required for deterministic scripted smoke.",
                detectedPath: script.path,
                preferences: preferences
            )
        }
        guard Shell.which("uv") != nil else {
            return ProviderStatus(
                kind: kind,
                availability: .missing,
                detail: "uv is required for deterministic scripted smoke.",
                detectedPath: script.path,
                preferences: preferences
            )
        }
        return ProviderStatus(
            kind: kind,
            availability: .configured,
            detail: "Ready. Dev/test-only deterministic smoke provider; no Claude, Codex, or OpenClaw required.",
            detectedPath: script.path,
            preferences: preferences
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
        guard ProviderKind.scriptedProviderEnabled else {
            throw ProviderError.configuration("Scripted provider is disabled. Set WORLDOS_ENABLE_SCRIPTED_PROVIDER=1 for dev/test smoke.")
        }
        guard Shell.which("python3") != nil else {
            throw ProviderError.missingDependency("python3 is required for deterministic scripted smoke.")
        }
        guard Shell.which("uv") != nil else {
            throw ProviderError.missingDependency("uv is required for deterministic scripted smoke.")
        }
        let script = repoPath.appendingPathComponent("scripts/play_scripted_dm.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw ProviderError.configuration("Scripted provider helper is missing: scripts/play_scripted_dm.sh")
        }
        return ProviderLaunchRequest(
            name: "Scripted smoke game",
            // #892: `-f -c` skips ALL rc files so launching never sources the user's profile.
            executable: "/bin/zsh",
            arguments: ["-f", "-c", "scripts/play_scripted_dm.sh"],
            environment: providerEnvironment(
                kind: kind,
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                hero: hero,
                preferences: preferences
            ),
            workingDirectory: repoPath,
            message: "Scripted smoke provider launched on port \(port)."
        )
    }

    func stop(runId: String) {}

    func tailLogs(runId: String) -> String {
        "Scripted provider output is captured in play-state/<run-id>/scripted-provider/."
    }
}

struct ProviderRegistry {
    private let adapters: [ProviderKind: ProviderAdapter] = [
        .claude: ClaudeProvider(),
        .codex: CodexProvider(),
        .openclaw: OpenClawProvider(),
        .scripted: ScriptedProvider()
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
        env["WORLDOS_PLAY_BUDGET"] = preferences.budget
    }
    if !preferences.sessionBudget.isEmpty {
        env["WORLDOS_PLAY_SESSION_BUDGET"] = preferences.sessionBudget
    }
    if !preferences.maxTurns.isEmpty {
        env["WORLDOS_PLAY_MAX_TURNS"] = preferences.maxTurns
    }
    if !preferences.artRepoPath.isEmpty {
        env["WORLDOS_ART_REPO_ROOT"] = preferences.artRepoPath
    }
    return env
}

private func providerEnvironment(
    kind: ProviderKind,
    world: String,
    runId: String,
    port: Int,
    companions: String,
    hero: String = "",
    preferences: ProviderPreferences
) -> [String: String] {
    var env = budgetEnvironment(preferences)
    env["WORLDOS_PROVIDER"] = kind.rawValue
    env["WORLDOS_WORLD"] = world
    env["WORLDOS_RUN_ID"] = runId
    env["WORLDOS_PLAY_PORT"] = String(port)
    env["WORLDOS_PLAY_COMPANIONS"] = companions
    let trimmedHero = hero.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedHero.isEmpty {
        env["WORLDOS_PLAY_HERO"] = trimmedHero
    }
    let trimmedCodexHome = preferences.codexHome.trimmingCharacters(in: .whitespacesAndNewlines)
    if kind == .codex && !trimmedCodexHome.isEmpty {
        let expandedCodexHome = (trimmedCodexHome as NSString).expandingTildeInPath
        if expandedCodexHome.hasPrefix("/") {
            env["CODEX_HOME"] = expandedCodexHome
        }
    }
    setProviderModelEnvironment(kind: kind, preferences: preferences, environment: &env)
    return env
}

private func setProviderModelEnvironment(
    kind: ProviderKind,
    preferences: ProviderPreferences,
    environment: inout [String: String]
) {
    let dmModel = preferences.dmModel(for: kind)
    let playerModel = preferences.playerModel(for: kind)
    let scorerModel = preferences.scorerModel(for: kind)
    environment["WORLDOS_PROVIDER_FAMILY"] = kind.providerFamily
    environment["WORLDOS_AUTH_SURFACE"] = kind.authSurface
    if !dmModel.isEmpty {
        environment["WORLDOS_DM_MODEL"] = dmModel
        if kind == .codex {
            environment["WORLDOS_CODEX_MODEL"] = dmModel
        }
    }
    if !playerModel.isEmpty {
        environment["WORLDOS_ACTOR_MODEL"] = playerModel
    }
    if !scorerModel.isEmpty {
        environment["WORLDOS_SCORER_MODEL"] = scorerModel
    }
}
