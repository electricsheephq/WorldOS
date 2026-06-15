import Foundation

/// Resolves the user's login-shell PATH at startup and injects it into this process's
/// environment, so child processes — the provider CLIs (`claude`/`codex`/`openclaw`), the
/// engine's `uv`/`node`/`python3`, and the `Shell.which()` availability guards — can find
/// tools installed outside the bare LaunchServices PATH.
///
/// WHY (#892): when the `.app` is launched from Finder/Dock or `open -n`, it inherits the bare
/// LaunchServices PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) — `launchctl getenv PATH` is unset and
/// nothing here re-resolved it. Every provider CLI lives OUTSIDE that PATH (`claude` in
/// `~/.local/bin`; `codex`/`openclaw`/`uv`/`node` in `/opt/homebrew/bin`), so `Shell.which()`
/// returns nil → each provider's guard throws "<CLI> is missing" → no session ever mints → the
/// native launch wedges (the part-A gate reports `no_launcher`, and a real user can't start a
/// game). The Codex/OpenClaw lanes partly dodged this only because they exec via `/bin/zsh -lc`
/// (a login shell self-heals the child's PATH) — but their `Shell.which` guards run against the
/// bare PATH and fail first. Resolving the login PATH once at startup fixes every lane uniformly.
/// This is the canonical macOS GUI-app PATH fix.
enum EnvironmentBootstrap {
    /// Capture the login-shell PATH and merge it into this process's PATH via `setenv`, so that
    /// `ProcessInfo.processInfo.environment["PATH"]` (and thus every child + every `Shell.which`)
    /// carries it. Idempotent — merging an already-merged PATH yields the same set, so a second
    /// call is a no-op. MUST run before any provider detection / `Shell.which`.
    static func ensureLoginPATH(
        runLoginShell: (String) -> String? = EnvironmentBootstrap.defaultRunLoginShell
    ) {
        let current = ProcessInfo.processInfo.environment["PATH"] ?? ""
        let login = runLoginShell(resolvedShellPath())
        let merged = augmentedPATH(currentPATH: current, loginPATH: login)
        if merged != current && !merged.isEmpty {
            setenv("PATH", merged, 1)
        }
    }

    /// Pure + testable: merge the login-shell PATH with the current PATH — login dirs first (so
    /// the user's tools win), the existing PATH appended, de-duped in order. Returns `currentPATH`
    /// unchanged when the login shell yields nothing usable (fail-safe: never make PATH worse).
    static func augmentedPATH(currentPATH: String, loginPATH: String?) -> String {
        guard let loginRaw = loginPATH?.trimmingCharacters(in: .whitespacesAndNewlines),
              !loginRaw.isEmpty else {
            return currentPATH
        }
        let ordered = loginRaw.split(separator: ":").map(String.init)
            + currentPATH.split(separator: ":").map(String.init)
        var seen = Set<String>()
        var merged: [String] = []
        for dir in ordered where !dir.isEmpty && seen.insert(dir).inserted {
            merged.append(dir)
        }
        return merged.joined(separator: ":")
    }

    /// Prefer the user's `$SHELL` (covers bash users whose PATH lives in `.bash_profile`); fall
    /// back to `/bin/zsh`, the macOS default and what the provider adapters already assume.
    private static func resolvedShellPath() -> String {
        if let shell = ProcessInfo.processInfo.environment["SHELL"],
           shell.hasPrefix("/"),
           FileManager.default.isExecutableFile(atPath: shell) {
            return shell
        }
        return "/bin/zsh"
    }

    /// Run `<shell> -lc 'printf %s "$PATH"'` (login, non-interactive — matches the provider
    /// adapters; sources the profile without the interactive-shell hang risk) and return its
    /// stdout. A 5s watchdog guarantees a misconfigured profile can never hang app launch.
    private static func defaultRunLoginShell(_ shellPath: String) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: shellPath)
        process.arguments = ["-lc", "printf %s \"$PATH\""]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = Pipe()
        do {
            try process.run()
        } catch {
            return nil
        }
        // Drain stdout CONCURRENTLY with the process, then wait — never read after waiting.
        // readDataToEndOfFile blocks until the child closes stdout (at exit) while continuously
        // emptying the pipe, so a chatty login profile that prints >64KB of banner/MOTD to stdout
        // can't fill the OS pipe buffer and deadlock the wait (which would silently fall back to
        // the bare PATH and re-introduce the very wedge this fixes). The captured bytes are read
        // back only after the semaphore barrier, which publishes the background writes here.
        let reader = out.fileHandleForReading
        let captured = NSMutableData()
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            captured.append(reader.readDataToEndOfFile())
            process.waitUntilExit()
            done.signal()
        }
        if done.wait(timeout: .now() + .seconds(5)) == .timedOut {
            process.terminate()
            return nil
        }
        guard process.terminationStatus == 0 else { return nil }
        return String(data: captured as Data, encoding: .utf8)
    }
}
