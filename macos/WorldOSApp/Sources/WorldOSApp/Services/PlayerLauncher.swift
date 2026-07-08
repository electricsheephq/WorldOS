import Foundation
#if canImport(AppKit)
import AppKit
#endif

/// THE launch contract between the macOS app and the standalone Unity **player build** (issue
/// #1322 / W5; formalizes docs/roadmap/RENDER-DELIVERY-DECISION.md — Unity launched BESIDE the app,
/// renderer = PURE CONSUMER, input = the existing `POST /move` kinds only).
///
/// The app hands the campaign off through **environment variables set on the launched player
/// process** — the SAME mechanism `AppProcessService` already uses to hand the campaign id + roots
/// to the viewer/provider children (`launchManagedProcess`'s env overlay). We deliberately pick env
/// vars over a plist/config-file beside the `.app`:
///   - it mirrors the existing bridge idiom exactly (one env dict, applied at launch);
///   - nothing is written to disk, so there is no stale-config hazard, no cleanup, and nothing to
///     reconcile on an idempotent relaunch (keeps the pure-consumer invariant crisp — the player
///     is not even a *disk* writer);
///   - each handoff carries a fresh base URL + campaign id atomically with the launch.
///
/// The player reads these two vars at startup (`System.Environment.GetEnvironmentVariable(...)`) and
/// renders the engine surfaces (`/combat-surface`, `/events`) at `BASE_URL` for `CAMPAIGN_ID`,
/// posting move-intents through the existing `/move` kinds. Absent → the player shows its own idle
/// / no-campaign state. It NEVER writes engine state and defines NO new endpoint.
enum PlayerLaunchContract {
    /// Engine/viewer base origin the player consumes (e.g. `http://127.0.0.1:8765`).
    static let baseURLKey = "WORLDOS_ENGINE_BASE_URL"
    /// Active campaign id to render (omitted when there is no active campaign).
    static let campaignIDKey = "WORLDOS_CAMPAIGN_ID"
    /// Default player bundle name, looked up under `~/Applications` then `/Applications`.
    static let defaultAppName = "WorldOSPlayer.app"
}

/// A fully-resolved, side-effect-free description of ONE player launch: which bundle to open, the
/// env payload to hand it, and whether to spawn a fresh instance. Pure value type so tests can
/// assert the exact invocation without touching LaunchServices.
struct PlayerLaunchInvocation: Equatable {
    let appURL: URL
    let environment: [String: String]
    /// Always `false`: an already-running player is ACTIVATED (brought forward), never duplicated —
    /// this is what makes a repeat launch idempotent at the LaunchServices layer.
    let createsNewInstance: Bool
}

/// Outcome of a launch request. `.notConfigured` is the additive-by-default identity: when no
/// player `.app` is installed, NOTHING is launched and the app behaves exactly as it does on `main`
/// today (the rendered tier is simply absent).
enum PlayerLaunchOutcome: Equatable {
    case notConfigured
    case launch(PlayerLaunchInvocation)
}

/// Launches the standalone Unity player BESIDE the app and hands off the campaign. The player is an
/// INDEPENDENT LaunchServices process: the app holds no handle to it and wires no termination
/// callback, so the player's exit can never touch the engine (viewer/provider) processes, and the
/// engine's lifecycle never depends on the player.
@MainActor
final class PlayerLauncher: ObservableObject {
    /// The bundle URL of the most recently launched player (nil until a successful launch), for
    /// diagnostics / UI affordances.
    @Published private(set) var launchedPlayerURL: URL?

    private let fileExists: (String) -> Bool
    private let openInvocation: (PlayerLaunchInvocation) -> Void

    /// - Parameters:
    ///   - fileExists: probes whether a candidate `.app` bundle is present (injectable for tests).
    ///   - open: performs the real LaunchServices launch (injectable seam; defaults to NSWorkspace).
    init(
        fileExists: @escaping (String) -> Bool = { FileManager.default.fileExists(atPath: $0) },
        open: @escaping (PlayerLaunchInvocation) -> Void = PlayerLauncher.openWithWorkspace
    ) {
        self.fileExists = fileExists
        self.openInvocation = open
    }

    /// Resolve + launch. Returns the outcome so the caller can surface it (e.g. log a no-op when the
    /// player tier is absent). A `.notConfigured` result performs no side effects at all.
    @discardableResult
    func launch(configuredPath: String, baseURL: URL, campaignID: String?) -> PlayerLaunchOutcome {
        let outcome = PlayerLauncher.resolveOutcome(
            configuredPath: configuredPath,
            baseURL: baseURL,
            campaignID: campaignID,
            homeDirectory: FileManager.default.homeDirectoryForCurrentUser,
            fileExists: fileExists
        )
        if case let .launch(invocation) = outcome {
            openInvocation(invocation)
            launchedPlayerURL = invocation.appURL
        }
        return outcome
    }

    // MARK: - Pure resolution (hermetic; no filesystem/LaunchServices side effects)

    /// Build the launch outcome from inputs. Pure aside from the injected `fileExists` probe.
    static func resolveOutcome(
        configuredPath: String,
        baseURL: URL,
        campaignID: String?,
        homeDirectory: URL,
        fileExists: (String) -> Bool
    ) -> PlayerLaunchOutcome {
        guard let appURL = locatePlayerApp(
            configuredPath: configuredPath,
            homeDirectory: homeDirectory,
            fileExists: fileExists
        ) else {
            return .notConfigured
        }
        var environment = [PlayerLaunchContract.baseURLKey: baseURL.absoluteString]
        let campaign = campaignID?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !campaign.isEmpty {
            environment[PlayerLaunchContract.campaignIDKey] = campaign
        }
        return .launch(
            PlayerLaunchInvocation(appURL: appURL, environment: environment, createsNewInstance: false)
        )
    }

    /// First existing candidate wins: an explicit configured path (tilde-expanded), then
    /// `~/Applications/WorldOSPlayer.app`, then `/Applications/WorldOSPlayer.app`. Returns nil when
    /// none exist → `.notConfigured` (today's behavior, byte-identical).
    static func locatePlayerApp(
        configuredPath: String,
        homeDirectory: URL,
        fileExists: (String) -> Bool
    ) -> URL? {
        var candidates: [URL] = []
        let trimmed = configuredPath.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            candidates.append(URL(fileURLWithPath: (trimmed as NSString).expandingTildeInPath))
        }
        candidates.append(homeDirectory.appendingPathComponent("Applications/\(PlayerLaunchContract.defaultAppName)"))
        candidates.append(URL(fileURLWithPath: "/Applications/\(PlayerLaunchContract.defaultAppName)"))
        return candidates.first { fileExists($0.path) }
    }

    // MARK: - Real launch seam

    /// Launch via `NSWorkspace.openApplication(at:configuration:)`, carrying the campaign handoff in
    /// `configuration.environment`. `createsNewApplicationInstance = false` makes a repeat launch
    /// idempotent (an already-running player is activated, not duplicated). The completion handler
    /// intentionally holds NO reference to any engine process — player launch is fire-and-forget and
    /// fully decoupled from the viewer/provider lifecycle.
    nonisolated private static func openWithWorkspace(_ invocation: PlayerLaunchInvocation) {
        #if canImport(AppKit)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.environment = invocation.environment
        configuration.createsNewApplicationInstance = invocation.createsNewInstance
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: invocation.appURL, configuration: configuration) { _, _ in
            // No-op: independent process; a launch error surfaces only via the returned outcome path,
            // and player exit never affects the engine.
        }
        #endif
    }
}
