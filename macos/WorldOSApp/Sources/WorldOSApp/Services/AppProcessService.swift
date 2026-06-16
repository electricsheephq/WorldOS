import Foundation

@MainActor
final class AppProcessService: ObservableObject {
    @Published var viewerEndpoint: LocalEndpoint?
    @Published var runningProvider: ProviderKind?
    @Published var activeCampaignID: String?
    @Published var dependencies: [DependencyStatus] = DependencyChecker.check()
    @Published var supervisorLog: String = ""
    @Published var providerLog: String = ""
    @Published var lastError: String?
    @Published var providerLaunchMetadata: ProviderLaunchMetadata?

    private var viewerProcess: ManagedProcess?
    private var providerProcess: ManagedProcess?
    private var intentionallyStoppingProviderPIDs: Set<Int32> = []
    private let registry = ProviderRegistry()
    private let maxLogCharacters = 120_000

    var activeProviderOpenWorldsURL: URL? {
        guard runningProvider != nil,
              let endpoint = viewerEndpoint,
              endpoint.name == "Provider viewer",
              endpoint.status != .stopped
        else {
            return nil
        }
        return endpoint.openWorldsURL
    }

    var diagnostics: String {
        """
        WorldOS Native App Diagnostics
        Viewer: \(viewerEndpoint?.url.absoluteString ?? "stopped")
        Viewer status: \(viewerEndpoint?.status.rawValue ?? "stopped")
        Active campaign: \(activeCampaignID ?? "none")
        Running provider: \(runningProvider?.rawValue ?? "none")
        Last error: \(lastError ?? "none")

        \(Diagnostics.providerLaunchSummary(providerLaunchMetadata))

        Provider last log lines:
        \(Diagnostics.lastLines(providerLog))

        Dependencies:
        \(dependencies.map { "\($0.command): \($0.path ?? "missing")" }.joined(separator: "\n"))

        Supervisor log:
        \(supervisorLog)

        Provider log:
        \(providerLog)
        """
    }

    func refreshDependencies() {
        dependencies = DependencyChecker.check()
    }

    func providerStatuses(repoPath: String, preferences: ProviderPreferences) -> [ProviderStatus] {
        registry.detectAll(repoPath: URL(fileURLWithPath: repoPath), preferences: preferences)
    }

    func startViewer(
        repoPath: String,
        preferredPort: Int,
        stateDir: String,
        artRepoPath: String = "",
        campaignID: String? = nil
    ) throws -> URL {
        let repoURL = URL(fileURLWithPath: repoPath)
        guard RepositoryLocator.looksLikeRepo(repoURL) else {
            try throwAndRecord("Repo path is not a WorldOS checkout: \(repoPath)")
        }
        guard Shell.which("python3") != nil else {
            try throwAndRecord("python3 is missing. Install Python 3 before launching the viewer.")
        }

        stopViewer()

        guard let port = PortFinder.firstFreePort(startingAt: preferredPort) else {
            try throwAndRecord("Could not find a free viewer port near \(preferredPort).")
        }
        let baseURL = URL(string: "http://127.0.0.1:\(port)")!
        var env: [String: String] = [:]
        if !stateDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            // Set BOTH names; the viewer/engine prefer WORLDOS_* and keep CLAWDND_*
            // as the v1.x warn-only fallback (issue #295, W0-E).
            let expandedStateDir = (stateDir as NSString).expandingTildeInPath
            env["WORLDOS_STATE_DIR"] = expandedStateDir
            env["CLAWDND_STATE_DIR"] = expandedStateDir
        }
        // IMPORTANT: launch the viewer with an ABSOLUTE script path and an
        // internal-disk working directory — NOT a relative path with cwd=repoURL.
        // When the repo lives on an external/removable volume (e.g. /Volumes/...),
        // Python's interpreter init calls getcwd() to absolutize a *relative* script
        // arg; that getcwd enumerates the volume mount and can hang indefinitely in
        // the kernel (open$NOCANCEL) under TCC removable-volume gating or an
        // Endpoint-Security file-scanner, because the app is launched via
        // LaunchServices with an ad-hoc signature. An absolute script path + a cwd
        // on the internal disk avoids that volume enumeration entirely; server.py
        // resolves all of its assets from __file__, so cwd is irrelevant to it.
        env["WORLDOS_REPO_ROOT"] = repoURL.path
        env["CLAWDND_REPO_ROOT"] = repoURL.path
        if let artRepo = try resolvedArtRepoPath(artRepoPath, repoURL: repoURL) {
            env["WORLDOS_ART_REPO_ROOT"] = artRepo
            env["CLAWDND_ART_REPO_ROOT"] = artRepo
        }
        let serverScript = repoURL.appendingPathComponent("viewer/server.py").path
        let safeCWD = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let args = ["python3", serverScript, campaignID ?? "", String(port)]
        let managed = try launchManagedProcess(
            name: "viewer",
            executable: "/usr/bin/env",
            arguments: args,
            workingDirectory: safeCWD,
            environment: env,
            stream: .supervisor
        )
        viewerProcess = managed
        activeCampaignID = campaignID
        let endpoint = LocalEndpoint(
            name: "Viewer",
            url: baseURL,
            healthPath: "/state",
            status: .running
        )
        viewerEndpoint = endpoint
        append("Started viewer pid \(managed.pid) on \(endpoint.openWorldsURL.absoluteString)", stream: .supervisor)
        return endpoint.openWorldsURL
    }

    func stopViewer() {
        viewerProcess?.terminate()
        viewerProcess = nil
        if var endpoint = viewerEndpoint {
            endpoint.status = .stopped
            viewerEndpoint = endpoint
        }
    }

    func startProviderSession(
        kind: ProviderKind,
        repoPath: String,
        world: String,
        runId: String,
        preferredPort: Int,
        companions: String,
        hero: String = "",
        stateDir: String,
        artRepoPath: String = "",
        resumeCampaignID: String? = nil,
        preferences: ProviderPreferences
    ) throws -> URL {
        let repoURL = URL(fileURLWithPath: repoPath)
        guard RepositoryLocator.looksLikeRepo(repoURL) else {
            try throwAndRecord("Repo path is not a WorldOS checkout: \(repoPath)")
        }

        guard let port = PortFinder.firstFreePort(startingAt: preferredPort) else {
            try throwAndRecord("Could not find a free provider viewer port near \(preferredPort).")
        }
        let adapter = registry.adapter(for: kind)
        if providerProcess == nil {
            providerLaunchMetadata = nil
        }
        let requestedArtRepoPath: String
        if artRepoPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            requestedArtRepoPath = preferences.artRepoPath
        } else {
            requestedArtRepoPath = artRepoPath
        }
        let resolvedArtRepo = try resolvedArtRepoPath(requestedArtRepoPath, repoURL: repoURL)
        let launchPreferences = ProviderPreferences(
            codexCommand: preferences.codexCommand,
            codexHome: preferences.codexHome,
            openClawCommand: preferences.openClawCommand,
            claudeDMModel: preferences.claudeDMModel,
            codexDMModel: preferences.codexDMModel,
            openClawDMModel: preferences.openClawDMModel,
            claudePlayerModel: preferences.claudePlayerModel,
            codexPlayerModel: preferences.codexPlayerModel,
            openClawPlayerModel: preferences.openClawPlayerModel,
            claudeScorerModel: preferences.claudeScorerModel,
            codexScorerModel: preferences.codexScorerModel,
            openClawScorerModel: preferences.openClawScorerModel,
            budget: preferences.budget,
            sessionBudget: preferences.sessionBudget,
            maxTurns: preferences.maxTurns,
            artRepoPath: resolvedArtRepo ?? ""
        )
        let request: ProviderLaunchRequest
        do {
            request = try adapter.startSession(
                world: world,
                runId: runId,
                port: port,
                companions: companions,
                hero: hero,
                repoPath: repoURL,
                preferences: launchPreferences
            )
        } catch {
            let message = error.localizedDescription
            lastError = message
            append(message, stream: .provider)
            throw error
        }

        // Inject the state-dir override into the launched game subprocess (the play.sh DM),
        // EXACTLY as startViewer does for the viewer. The adapter builds budget/provider/model
        // env but never the state dir, so without this the play script falls back to its own
        // "$ROOT/play-state" default — i.e. it reads/writes the DEV REPO instead of the user's
        // state dir. play.sh honors WORLDOS_STATE_DIR (legacy CLAWDND_STATE_DIR) as the play-state
        // ROOT and nests this run under $RUN. We set BOTH names (the viewer/engine prefer
        // WORLDOS_*; CLAWDND_* is the v1.x warn-only fallback, issue #295). When stateDir is empty
        // we add nothing, so dev/QA-harness runs are byte-identical (no key written → script default).
        var launchEnvironment = request.environment
        let trimmedStateDir = stateDir.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedStateDir.isEmpty {
            let expandedStateDir = (trimmedStateDir as NSString).expandingTildeInPath
            launchEnvironment["WORLDOS_STATE_DIR"] = expandedStateDir
            launchEnvironment["CLAWDND_STATE_DIR"] = expandedStateDir
        }
        // RESUME re-attach: when the launcher's Resume passes a saved campaign id (alongside its
        // saved runId, which the caller already routed to `runId` → the per-run state dir), tell the
        // play script to RE-OPEN that existing campaign instead of cold-opening a new empty world.
        // play.sh reads WORLDOS_RESUME_CAMPAIGN (legacy CLAWDND_RESUME_CAMPAIGN), confirms the
        // snapshot is on disk under the run's state dir, and re-attaches it (writable move sink,
        // saved party/progress). Absent → a fresh cold open, byte-identical to before.
        if let resume = resumeCampaignID?.trimmingCharacters(in: .whitespacesAndNewlines), !resume.isEmpty {
            launchEnvironment["WORLDOS_RESUME_CAMPAIGN"] = resume
            launchEnvironment["CLAWDND_RESUME_CAMPAIGN"] = resume
        }

        if let providerProcess {
            intentionallyStoppingProviderPIDs.insert(providerProcess.pid)
            providerProcess.terminate()
        }
        providerProcess = nil
        runningProvider = nil
        providerLaunchMetadata = nil
        providerLog = ""
        let metadata = ProviderLaunchMetadata(
            kind: kind,
            processName: request.name,
            executable: request.executable,
            arguments: request.arguments,
            workingDirectory: request.workingDirectory,
            environment: launchEnvironment,
            providerFamily: kind.providerFamily,
            authSurface: kind.authSurface,
            dmModel: launchPreferences.dmModel(for: kind),
            playerModel: launchPreferences.playerModel(for: kind),
            scorerModel: launchPreferences.scorerModel(for: kind),
            world: world,
            runId: runId,
            port: port,
            statePath: expandedPathIfPresent(stateDir),
            message: request.message
        )
        let managed = try launchManagedProcess(
            name: request.name,
            executable: request.executable,
            arguments: request.arguments,
            workingDirectory: request.workingDirectory,
            environment: launchEnvironment,
            stream: .provider,
            providerMetadata: metadata
        )
        providerProcess = managed
        runningProvider = kind
        let baseURL = URL(string: "http://127.0.0.1:\(port)")!
        let endpoint = LocalEndpoint(
            name: "Provider viewer",
            url: baseURL,
            healthPath: "/state",
            status: .starting
        )
        viewerEndpoint = endpoint
        append("\(request.message) pid \(managed.pid)", stream: .provider)
        return endpoint.openWorldsURL
    }

    func markProviderViewerReady() {
        guard var endpoint = viewerEndpoint, endpoint.name == "Provider viewer" else { return }
        endpoint.status = .running
        viewerEndpoint = endpoint
    }

    func stopProvider() {
        if let providerProcess {
            intentionallyStoppingProviderPIDs.insert(providerProcess.pid)
            providerProcess.terminate()
        }
        providerProcess = nil
        runningProvider = nil
        if var metadata = providerLaunchMetadata, metadata.exitStatus == nil {
            metadata.exitedAt = Date()
            metadata.lastError = nil
            providerLaunchMetadata = metadata
        }
        stopProviderViewerEndpointIfNeeded()
    }

    private func launchManagedProcess(
        name: String,
        executable: String,
        arguments: [String],
        workingDirectory: URL,
        environment: [String: String],
        stream: LogStream,
        providerMetadata: ProviderLaunchMetadata? = nil
    ) throws -> ManagedProcess {
        // Drop foreign env vars that point at a removable volume (e.g. GBRAIN_SKILLS_DIR=/Volumes/…
        // inherited from a shell launch's ~/.zshenv) BEFORE merging the caller's overlay. A child
        // reading one enumerates the volume → a modal removable-volume TCC prompt that can't be
        // answered headlessly: it blocks every shell-launched GUI run and stalls viewer startup.
        // The caller's `environment` (the app's own WORLDOS_* roots, which may intentionally be on
        // /Volumes for a removable-volume worktree) is applied AFTER, so it survives.
        var mergedEnvironment = EnvironmentBootstrap.withoutRemovableVolumeLeaks(ProcessInfo.processInfo.environment)
        environment.forEach { key, value in mergedEnvironment[key] = value }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = workingDirectory
        process.environment = mergedEnvironment

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        let managed = ManagedProcess(name: name, process: process)
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            let text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
            Task { @MainActor in
                self?.append(text, stream: stream, prefix: name)
            }
        }

        process.terminationHandler = { [weak self, weak managed] process in
            Task { @MainActor in
                managed?.close()
                self?.append("\(name) exited with status \(process.terminationStatus)", stream: stream)
                if stream == .provider {
                    let processID = process.processIdentifier
                    let stoppedByUser = self?.intentionallyStoppingProviderPIDs.remove(processID) != nil
                    self?.recordProviderExit(status: process.terminationStatus, stoppedByUser: stoppedByUser, processID: processID)
                    if self?.providerProcess === managed {
                        self?.providerProcess = nil
                        self?.runningProvider = nil
                        self?.stopProviderViewerEndpointIfNeeded()
                    }
                } else if self?.viewerProcess === managed {
                    self?.viewerProcess = nil
                    if var endpoint = self?.viewerEndpoint {
                        endpoint.status = .stopped
                        self?.viewerEndpoint = endpoint
                    }
                }
            }
        }

        do {
            try process.run()
            if var providerMetadata {
                providerMetadata.processID = process.processIdentifier
                providerMetadata.launchedAt = Date()
                providerLaunchMetadata = providerMetadata
                append(Diagnostics.providerLaunchSummary(providerMetadata), stream: .provider)
            }
            return managed
        } catch {
            let message = "\(name) failed to launch: \(error.localizedDescription)"
            lastError = message
            if var providerMetadata {
                providerMetadata.lastError = message
                providerLaunchMetadata = providerMetadata
                append(Diagnostics.providerLaunchSummary(providerMetadata), stream: stream)
            }
            append(message, stream: stream)
            throw error
        }
    }

    private func append(_ text: String, stream: LogStream, prefix: String? = nil) {
        let safeText = stream == .provider ? Diagnostics.redactedText(text) : text
        let line = prefix.map { "[\($0)] \(safeText)" } ?? safeText
        switch stream {
        case .supervisor:
            supervisorLog += line.hasSuffix("\n") ? line : line + "\n"
            trimLogIfNeeded(&supervisorLog)
        case .provider:
            providerLog += line.hasSuffix("\n") ? line : line + "\n"
            trimLogIfNeeded(&providerLog)
        }
    }

    private func trimLogIfNeeded(_ log: inout String) {
        guard log.count > maxLogCharacters else { return }
        let suffix = String(log.suffix(maxLogCharacters))
        if let newline = suffix.firstIndex(of: "\n"),
           suffix.index(after: newline) < suffix.endIndex {
            log = String(suffix[suffix.index(after: newline)...])
        } else {
            log = suffix
        }
    }

    private func stopProviderViewerEndpointIfNeeded() {
        guard var endpoint = viewerEndpoint, endpoint.name == "Provider viewer" else { return }
        endpoint.status = .stopped
        viewerEndpoint = endpoint
    }

    private func recordProviderExit(status: Int32, stoppedByUser: Bool, processID: Int32) {
        guard var metadata = providerLaunchMetadata else { return }
        guard metadata.processID == processID else { return }
        metadata.exitStatus = status
        metadata.exitedAt = Date()
        if stoppedByUser {
            metadata.lastError = nil
        } else if status != 0 {
            let message = "\(metadata.processName) exited with status \(status)"
            metadata.lastError = message
            lastError = message
        }
        providerLaunchMetadata = metadata
    }

    private func expandedPathIfPresent(_ path: String) -> String? {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return (trimmed as NSString).expandingTildeInPath
    }

    private func resolvedArtRepoPath(_ artRepoPath: String, repoURL: URL) throws -> String? {
        if let expanded = expandedPathIfPresent(artRepoPath) {
            guard RepositoryLocator.looksLikeArtRepo(URL(fileURLWithPath: expanded)) else {
                try throwAndRecord("Private art repo path does not contain content/worlds/_private: \(expanded)")
            }
            return expanded
        }
        if RepositoryLocator.looksLikeArtRepo(repoURL) {
            return repoURL.path
        }
        return RepositoryLocator.defaultArtRepoPath()
    }

    private func throwAndRecord(_ message: String) throws -> Never {
        lastError = message
        append(message, stream: .supervisor)
        throw ProviderError.configuration(message)
    }
}

private enum LogStream {
    case supervisor
    case provider
}

final class ManagedProcess {
    let name: String
    let process: Process

    init(name: String, process: Process) {
        self.name = name
        self.process = process
    }

    var pid: Int32 { process.processIdentifier }

    func terminate() {
        guard process.isRunning else {
            close()
            return
        }
        process.terminate()
        close()
    }

    func close() {
        (process.standardOutput as? Pipe)?.fileHandleForReading.readabilityHandler = nil
        (process.standardError as? Pipe)?.fileHandleForReading.readabilityHandler = nil
    }
}
