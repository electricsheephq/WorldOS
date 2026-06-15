import Foundation

/// Augments this process's PATH at startup with the standard macOS tool directories, so child
/// processes — the provider CLIs (`claude`/`codex`/`openclaw`), the engine's `uv`/`node`/`python3`,
/// and the `Shell.which()` availability guards — can be found.
///
/// WHY (#892): launched from Finder/Dock or `open -n`, the app inherits the bare LaunchServices
/// PATH (`/usr/bin:/bin:/usr/sbin:/sbin`; `launchctl getenv PATH` is unset). Every provider CLI
/// lives outside it (`claude` in `~/.local/bin`; `codex`/`openclaw`/`uv`/`node` in
/// `/opt/homebrew/bin`), so `Shell.which()` returns nil → each provider guard throws "<CLI> is
/// missing" → no session mints → the launch wedges (`no_launcher`), and a real user can't start a
/// game.
///
/// We deliberately do NOT resolve PATH by running a login shell (`zsh -lc`). A login shell sources
/// the user's profile (`~/.zshenv`/`~/.zprofile`/`~/.zlogin`), which can have arbitrary side
/// effects — keychain access, auto-starting other tools, touching removable volumes — so *merely
/// launching the app* would fire all of them (observed: a keychain/codesign prompt and a removable
/// -disk access request just from app startup). Instead we prepend a STATIC allowlist of well-known
/// tool directories that exist. It is deterministic, runs **zero user code**, and spawns no
/// subprocess.
enum EnvironmentBootstrap {
    /// Prepend the standard tool dirs (that exist) to this process's PATH via `setenv`, so every
    /// child + every `Shell.which` sees them. Idempotent. MUST run before any provider detection.
    static func ensureToolPath() {
        let current = ProcessInfo.processInfo.environment["PATH"] ?? ""
        let merged = augmentedPATH(currentPATH: current, extraDirs: standardToolDirs())
        if merged != current && !merged.isEmpty {
            setenv("PATH", merged, 1)
        }
    }

    /// Pure + testable: prepend `extraDirs` to `currentPATH`, deduped in order (extras first so the
    /// user's installed tools win — e.g. a Homebrew `python3` over the system copy, matching the
    /// PATH a login shell would have produced). Returns `currentPATH` unchanged when there is
    /// nothing new to add.
    static func augmentedPATH(currentPATH: String, extraDirs: [String]) -> String {
        let ordered = extraDirs + currentPATH.split(separator: ":").map(String.init)
        var seen = Set<String>()
        var merged: [String] = []
        for dir in ordered where !dir.isEmpty && seen.insert(dir).inserted {
            merged.append(dir)
        }
        return merged.joined(separator: ":")
    }

    /// The well-known macOS dev-tool install locations, filtered to those that actually exist on
    /// this machine. Covers the overwhelming majority of setups: `claude`/`uv` via the standard
    /// installer (`~/.local/bin`) and `codex`/`openclaw`/`uv`/`node`/`gh`/`python3` via Homebrew
    /// (Apple-Silicon `/opt/homebrew`, Intel `/usr/local`). Computing this runs no user code.
    private static func standardToolDirs() -> [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let fileManager = FileManager.default
        return [
            "\(home)/.local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
        ].filter { fileManager.fileExists(atPath: $0) }
    }
}
