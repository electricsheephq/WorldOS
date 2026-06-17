import XCTest

@testable import WorldOSApp

/// Unit tests for the two pure, P0-causing helpers in `EnvironmentBootstrap`:
///   - `augmentedPATH(currentPATH:extraDirs:)`  (login-PATH resolution, #892)
///   - `withoutRemovableVolumeLeaks(_:)`         (strip `/Volumes/*` env leaks)
///
/// These are deliberately hermetic: they exercise ONLY the pure string/dict logic and never
/// touch the filesystem, the network, env mutation (`setenv`), or a real app launch.
final class EnvironmentBootstrapTests: XCTestCase {

    // MARK: - augmentedPATH

    /// Extras are prepended ahead of the existing PATH so a user-installed tool wins over the
    /// system copy (the whole point of #892: a Homebrew `python3` must shadow `/usr/bin/python3`).
    func testAugmentedPATHPrependsExtrasAheadOfExisting() {
        let result = EnvironmentBootstrap.augmentedPATH(
            currentPATH: "/usr/bin:/bin",
            extraDirs: ["/opt/homebrew/bin", "/Users/me/.local/bin"]
        )
        XCTAssertEqual(result, "/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin:/bin")
    }

    /// The bare LaunchServices PATH (#892 reproduction): Finder/`open -n` hands the app
    /// `/usr/bin:/bin:/usr/sbin:/sbin` with none of the tool dirs. After augmentation the tool
    /// dirs must be present AND ordered first.
    func testAugmentedPATHFixesBareLaunchServicesPATH() {
        let bare = "/usr/bin:/bin:/usr/sbin:/sbin"
        let result = EnvironmentBootstrap.augmentedPATH(
            currentPATH: bare,
            extraDirs: ["/Users/me/.local/bin", "/opt/homebrew/bin"]
        )
        let entries = result.split(separator: ":").map(String.init)
        XCTAssertEqual(entries.first, "/Users/me/.local/bin")
        XCTAssertTrue(entries.contains("/opt/homebrew/bin"))
        // Original entries are preserved (nothing dropped).
        for original in bare.split(separator: ":").map(String.init) {
            XCTAssertTrue(entries.contains(original), "dropped existing PATH entry \(original)")
        }
    }

    /// De-dup is order-preserving: a dir already on PATH must not appear twice, and an extra that
    /// duplicates an existing entry keeps only its (earlier) prepended position.
    func testAugmentedPATHDeduplicatesPreservingOrder() {
        let result = EnvironmentBootstrap.augmentedPATH(
            currentPATH: "/opt/homebrew/bin:/usr/bin:/bin",
            extraDirs: ["/opt/homebrew/bin", "/usr/local/bin"]
        )
        XCTAssertEqual(result, "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
        // No entry appears more than once.
        let entries = result.split(separator: ":").map(String.init)
        XCTAssertEqual(entries.count, Set(entries).count)
    }

    /// Empty extras → identity (additive-by-default: empty == today's behavior).
    func testAugmentedPATHEmptyExtrasReturnsCurrentUnchanged() {
        let current = "/usr/bin:/bin"
        XCTAssertEqual(
            EnvironmentBootstrap.augmentedPATH(currentPATH: current, extraDirs: []),
            current
        )
    }

    /// Empty current PATH with extras → just the extras, no leading/trailing/empty separators.
    func testAugmentedPATHEmptyCurrentYieldsExtrasOnly() {
        let result = EnvironmentBootstrap.augmentedPATH(
            currentPATH: "",
            extraDirs: ["/opt/homebrew/bin", "/usr/local/bin"]
        )
        XCTAssertEqual(result, "/opt/homebrew/bin:/usr/local/bin")
        XCTAssertFalse(result.hasPrefix(":"))
        XCTAssertFalse(result.hasSuffix(":"))
    }

    /// Empty path components (e.g. a stray `::` or leading/trailing colon in the inherited PATH)
    /// are dropped, never re-emitted as empty entries.
    func testAugmentedPATHDropsEmptyComponents() {
        let result = EnvironmentBootstrap.augmentedPATH(
            currentPATH: "/usr/bin::/bin:",
            extraDirs: []
        )
        XCTAssertEqual(result, "/usr/bin:/bin")
    }

    // MARK: - withoutRemovableVolumeLeaks

    /// The exact real-world P0 offender: `GBRAIN_SKILLS_DIR=/Volumes/LEXAR/...` exported by a
    /// launching shell's `~/.zshenv` must be stripped, or a spawned provider enumerates the
    /// removable volume and fires the un-answerable TCC modal that wedges CI/QA launches.
    func testRemovableVolumeLeakStripsGBrainSkillsDir() {
        let env = [
            "GBRAIN_SKILLS_DIR": "/Volumes/LEXAR/repos/eva-brain/skills",
            "HOME": "/Users/me",
        ]
        let cleaned = EnvironmentBootstrap.withoutRemovableVolumeLeaks(env)
        XCTAssertNil(cleaned["GBRAIN_SKILLS_DIR"], "removable-volume env var was not stripped")
        XCTAssertEqual(cleaned["HOME"], "/Users/me", "non-/Volumes var must survive")
    }

    /// Only the VALUE prefix matters. Anything whose value starts with `/Volumes/` is dropped,
    /// regardless of the variable name.
    func testRemovableVolumeLeakStripsAnyVarPointingAtVolumes() {
        let env = [
            "SOME_TOOL_HOME": "/Volumes/USB/tool",
            "ANOTHER": "/Volumes/External/data/cache",
            "SAFE": "/Users/me/data",
        ]
        let cleaned = EnvironmentBootstrap.withoutRemovableVolumeLeaks(env)
        XCTAssertNil(cleaned["SOME_TOOL_HOME"])
        XCTAssertNil(cleaned["ANOTHER"])
        XCTAssertEqual(cleaned["SAFE"], "/Users/me/data")
    }

    /// PATH and other `:`-separated lists do NOT start with `/Volumes/`, so tool discovery is
    /// never collateral damage — even if some entry mid-list lives on a volume.
    func testRemovableVolumeLeakDoesNotTouchPATHList() {
        let path = "/opt/homebrew/bin:/Volumes/LEXAR/bin:/usr/bin"
        let env = ["PATH": path, "LDFLAGS": "-L/Volumes/LEXAR/lib"]
        let cleaned = EnvironmentBootstrap.withoutRemovableVolumeLeaks(env)
        XCTAssertEqual(cleaned["PATH"], path, "PATH must be left intact")
        // A flag value that merely CONTAINS /Volumes but does not START with it is preserved.
        XCTAssertEqual(cleaned["LDFLAGS"], "-L/Volumes/LEXAR/lib")
    }

    /// Empty environment → empty result (additive-by-default identity).
    func testRemovableVolumeLeakEmptyEnvironmentIsIdentity() {
        XCTAssertTrue(EnvironmentBootstrap.withoutRemovableVolumeLeaks([:]).isEmpty)
    }

    /// An env with no `/Volumes/` values round-trips unchanged.
    func testRemovableVolumeLeakLeavesCleanEnvironmentUnchanged() {
        let env = ["HOME": "/Users/me", "LANG": "en_US.UTF-8", "PWD": "/Users/me/project"]
        XCTAssertEqual(EnvironmentBootstrap.withoutRemovableVolumeLeaks(env), env)
    }

    /// A WorldOS root that legitimately lives under `/Volumes/...` IS stripped by this filter —
    /// which is intentional: the caller re-applies its own repo/art roots via an explicit
    /// `environment` overlay merged AFTER this filter, so this function alone must drop it.
    func testRemovableVolumeLeakStripsEvenWorldOSOwnRoot() {
        let env = ["WORLDOS_REPO": "/Volumes/LEXAR/WorldOS"]
        XCTAssertTrue(
            EnvironmentBootstrap.withoutRemovableVolumeLeaks(env).isEmpty,
            "filter alone must drop /Volumes values; caller re-applies intentional roots after"
        )
    }
}
