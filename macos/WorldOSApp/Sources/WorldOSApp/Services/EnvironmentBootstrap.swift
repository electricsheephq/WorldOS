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

    /// Strip inherited env vars whose VALUE points at a removable volume (`/Volumes/...`) before
    /// they reach a child process. A child handed such a var enumerates the volume to read it,
    /// which fires a modal "WorldOS would like to access files on a removable volume" TCC prompt.
    ///
    /// WHY (the P0 this fixes): the launching shell's `~/.zshenv` can export a foreign tool's path
    /// onto a removable disk — observed: `GBRAIN_SKILLS_DIR=/Volumes/LEXAR/repos/eva-brain/skills`.
    /// `.zshenv` is sourced by EVERY zsh, so any shell-launched app (`open -n` from a build/QA
    /// script) inherits it; we then merge the full inherited environment into every viewer/provider
    /// we spawn (`AppProcessService.launchManagedProcess`), and the provider's skills loader reads
    /// `GBRAIN_SKILLS_DIR` → enumerates `/Volumes/LEXAR` → the prompt. It can't be answered
    /// headlessly, so it blocks unattended/CI builds and every shell-launched GUI run, and the
    /// modal stalls viewer startup ("viewer did not become ready"). The app needs NONE of these
    /// foreign vars.
    ///
    /// The app's OWN repo/art roots — which may legitimately live on a removable-volume worktree —
    /// are re-applied by the caller's explicit `environment` overlay (merged AFTER this filter), so
    /// an intentional `/Volumes` WorldOS root still survives. We match `hasPrefix("/Volumes/")` on
    /// the VALUE: this targets bare single-path vars (the real-world offender) and never touches
    /// PATH or any `:`-separated list (those don't START with `/Volumes/`), so tool discovery is
    /// unaffected. Pure + deterministic.
    static func withoutRemovableVolumeLeaks(_ environment: [String: String]) -> [String: String] {
        environment.filter { _, value in !value.hasPrefix("/Volumes/") }
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
