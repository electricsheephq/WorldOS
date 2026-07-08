import XCTest

@testable import WorldOSApp

/// Unit tests for the standalone Unity **player** launch handoff (issue #1322 / W5). These exercise
/// the pure resolution logic (`resolveOutcome` / `locatePlayerApp`) and the injectable launch seam,
/// and are deliberately hermetic: they mock the `.app` presence via an injected `fileExists` probe
/// and capture the launch invocation via an injected `open` closure — never touching the real
/// filesystem, LaunchServices, or an actual app launch.
@MainActor
final class PlayerLauncherTests: XCTestCase {

    private let home = URL(fileURLWithPath: "/Users/tester")
    private let baseURL = URL(string: "http://127.0.0.1:8765")!

    // MARK: - resolveOutcome (pure)

    /// Configured player present → launch with the exact env payload (base URL + campaign id) and
    /// the configured bundle path. This is the primary contract assertion.
    func testResolveLaunchesConfiguredPlayerWithContractEnvironment() {
        let configured = "/Users/tester/Games/WorldOSPlayer.app"
        let outcome = PlayerLauncher.resolveOutcome(
            configuredPath: configured,
            baseURL: baseURL,
            campaignID: "camp-42",
            homeDirectory: home,
            fileExists: { $0 == configured }
        )
        guard case let .launch(invocation) = outcome else {
            return XCTFail("expected .launch, got \(outcome)")
        }
        XCTAssertEqual(invocation.appURL, URL(fileURLWithPath: configured))
        XCTAssertEqual(invocation.environment[PlayerLaunchContract.baseURLKey], "http://127.0.0.1:8765")
        XCTAssertEqual(invocation.environment[PlayerLaunchContract.campaignIDKey], "camp-42")
        // Idempotent-relaunch guarantee: never spawn a duplicate instance.
        XCTAssertFalse(invocation.createsNewInstance)
    }

    /// No configured path → fall back to the default `~/Applications/WorldOSPlayer.app`.
    func testResolveFallsBackToDefaultHomeApplications() {
        let expected = home.appendingPathComponent("Applications/WorldOSPlayer.app").path
        let outcome = PlayerLauncher.resolveOutcome(
            configuredPath: "",
            baseURL: baseURL,
            campaignID: "camp-1",
            homeDirectory: home,
            fileExists: { $0 == expected }
        )
        guard case let .launch(invocation) = outcome else {
            return XCTFail("expected .launch, got \(outcome)")
        }
        XCTAssertEqual(invocation.appURL.path, expected)
    }

    /// An explicit configured path is preferred over the default when both exist.
    func testResolvePrefersConfiguredOverDefault() {
        let configured = "/opt/WorldOSPlayer.app"
        let outcome = PlayerLauncher.resolveOutcome(
            configuredPath: configured,
            baseURL: baseURL,
            campaignID: nil,
            homeDirectory: home,
            fileExists: { _ in true } // everything "exists" → configured must still win
        )
        guard case let .launch(invocation) = outcome else {
            return XCTFail("expected .launch, got \(outcome)")
        }
        XCTAssertEqual(invocation.appURL, URL(fileURLWithPath: configured))
    }

    /// No campaign id → the campaign var is OMITTED (never emitted empty); base URL still handed over.
    func testResolveOmitsCampaignWhenAbsent() {
        let configured = "/opt/WorldOSPlayer.app"
        for campaign in [nil, "", "   "] as [String?] {
            let outcome = PlayerLauncher.resolveOutcome(
                configuredPath: configured,
                baseURL: baseURL,
                campaignID: campaign,
                homeDirectory: home,
                fileExists: { _ in true }
            )
            guard case let .launch(invocation) = outcome else {
                return XCTFail("expected .launch for campaign=\(String(describing: campaign))")
            }
            XCTAssertNil(invocation.environment[PlayerLaunchContract.campaignIDKey],
                         "campaign var must be omitted for \(String(describing: campaign))")
            XCTAssertEqual(invocation.environment[PlayerLaunchContract.baseURLKey], "http://127.0.0.1:8765")
        }
    }

    /// BYTE-IDENTITY / today's-behavior path: no player `.app` anywhere → `.notConfigured`, i.e.
    /// nothing is launched and the app behaves exactly as it does without the player tier.
    func testResolveNoPlayerInstalledIsNotConfigured() {
        let outcome = PlayerLauncher.resolveOutcome(
            configuredPath: "",
            baseURL: baseURL,
            campaignID: "camp-1",
            homeDirectory: home,
            fileExists: { _ in false } // nothing exists
        )
        XCTAssertEqual(outcome, .notConfigured)
    }

    // MARK: - launch() side-effect seam

    /// A resolvable launch invokes the injected open seam EXACTLY once with the resolved invocation.
    func testLaunchInvokesOpenSeamOnceWithInvocation() {
        var captured: [PlayerLaunchInvocation] = []
        let configured = "/opt/WorldOSPlayer.app"
        let launcher = PlayerLauncher(
            fileExists: { $0 == configured },
            open: { captured.append($0) }
        )
        let outcome = launcher.launch(configuredPath: configured, baseURL: baseURL, campaignID: "camp-9")

        XCTAssertEqual(captured.count, 1)
        XCTAssertEqual(captured.first?.appURL, URL(fileURLWithPath: configured))
        XCTAssertEqual(captured.first?.environment[PlayerLaunchContract.campaignIDKey], "camp-9")
        XCTAssertEqual(launcher.launchedPlayerURL, URL(fileURLWithPath: configured))
        if case .notConfigured = outcome { XCTFail("expected .launch") }
    }

    /// When there is no player, launch() performs ZERO side effects: the open seam is never called
    /// and no launched URL is recorded (defaulted no-op == today's behavior).
    func testLaunchNoPlayerIsPureNoOp() {
        var openCalls = 0
        let launcher = PlayerLauncher(
            fileExists: { _ in false },
            open: { _ in openCalls += 1 }
        )
        let outcome = launcher.launch(configuredPath: "", baseURL: baseURL, campaignID: "camp-1")

        XCTAssertEqual(outcome, .notConfigured)
        XCTAssertEqual(openCalls, 0)
        XCTAssertNil(launcher.launchedPlayerURL)
    }

    /// Idempotent relaunch: repeated launches always resolve `createsNewInstance == false`, so
    /// LaunchServices activates the existing player instead of duplicating it.
    func testRepeatedLaunchIsIdempotentSingleInstance() {
        var captured: [PlayerLaunchInvocation] = []
        let configured = "/opt/WorldOSPlayer.app"
        let launcher = PlayerLauncher(
            fileExists: { $0 == configured },
            open: { captured.append($0) }
        )
        launcher.launch(configuredPath: configured, baseURL: baseURL, campaignID: "camp-1")
        launcher.launch(configuredPath: configured, baseURL: baseURL, campaignID: "camp-1")

        XCTAssertEqual(captured.count, 2)
        XCTAssertTrue(captured.allSatisfy { !$0.createsNewInstance },
                      "every relaunch must reuse the running instance (idempotent)")
    }
}
