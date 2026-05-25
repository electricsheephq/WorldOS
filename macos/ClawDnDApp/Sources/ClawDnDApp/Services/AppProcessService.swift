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

    private var viewerProcess: ManagedProcess?
    private var providerProcess: ManagedProcess?
    private let registry = ProviderRegistry()
    private let maxLogCharacters = 120_000

    var diagnostics: String {
        """
        ClawDnD Native App Diagnostics
        Viewer: \(viewerEndpoint?.url.absoluteString ?? "stopped")
        Viewer status: \(viewerEndpoint?.status.rawValue ?? "stopped")
        Active campaign: \(activeCampaignID ?? "none")
        Running provider: \(runningProvider?.rawValue ?? "none")
        Last error: \(lastError ?? "none")

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
        campaignID: String? = nil
    ) throws -> URL {
        let repoURL = URL(fileURLWithPath: repoPath)
        guard RepositoryLocator.looksLikeRepo(repoURL) else {
            try throwAndRecord("Repo path is not a ClawDnD checkout: \(repoPath)")
        }
        guard Shell.which("python3") != nil else {
            try throwAndRecord("python3 is missing. Install Python 3 before launching the viewer.")
        }

        stopViewer()

        guard let port = PortFinder.firstFreePort(startingAt: preferredPort) else {
            try throwAndRecord("Could not find a free viewer port near \(preferredPort).")
        }
        let baseURL = URL(string: "http://127.0.0.1:\(port)")!
        let dashboard = baseURL.appendingPathComponent("dashboard")
        var env: [String: String] = [:]
        if !stateDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            env["CLAWDND_STATE_DIR"] = (stateDir as NSString).expandingTildeInPath
        }
        let args = ["python3", "viewer/server.py", campaignID ?? "", String(port)]
        let managed = try launchManagedProcess(
            name: "viewer",
            executable: "/usr/bin/env",
            arguments: args,
            workingDirectory: repoURL,
            environment: env,
            stream: .supervisor
        )
        viewerProcess = managed
        activeCampaignID = campaignID
        viewerEndpoint = LocalEndpoint(
            name: "Viewer",
            url: baseURL,
            healthPath: "/state",
            status: .running
        )
        append("Started viewer pid \(managed.pid) on \(dashboard.absoluteString)", stream: .supervisor)
        return dashboard
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
        preferences: ProviderPreferences
    ) throws -> URL {
        let repoURL = URL(fileURLWithPath: repoPath)
        guard RepositoryLocator.looksLikeRepo(repoURL) else {
            try throwAndRecord("Repo path is not a ClawDnD checkout: \(repoPath)")
        }

        guard let port = PortFinder.firstFreePort(startingAt: preferredPort) else {
            try throwAndRecord("Could not find a free provider viewer port near \(preferredPort).")
        }
        let adapter = registry.adapter(for: kind)
        let request = try adapter.startSession(
            world: world,
            runId: runId,
            port: port,
            companions: companions,
            repoPath: repoURL,
            preferences: preferences
        )

        providerProcess?.terminate()
        providerProcess = nil
        runningProvider = nil
        providerLog = ""
        let managed = try launchManagedProcess(
            name: request.name,
            executable: request.executable,
            arguments: request.arguments,
            workingDirectory: request.workingDirectory,
            environment: request.environment,
            stream: .provider
        )
        providerProcess = managed
        runningProvider = kind
        let baseURL = URL(string: "http://127.0.0.1:\(port)")!
        let dashboard = baseURL.appendingPathComponent("dashboard")
        viewerEndpoint = LocalEndpoint(
            name: "Provider viewer",
            url: baseURL,
            healthPath: "/state",
            status: .starting
        )
        append("\(request.message) pid \(managed.pid)", stream: .provider)
        return dashboard
    }

    func stopProvider() {
        providerProcess?.terminate()
        providerProcess = nil
        runningProvider = nil
        stopProviderViewerEndpointIfNeeded()
    }

    private func launchManagedProcess(
        name: String,
        executable: String,
        arguments: [String],
        workingDirectory: URL,
        environment: [String: String],
        stream: LogStream
    ) throws -> ManagedProcess {
        var mergedEnvironment = ProcessInfo.processInfo.environment
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
            return managed
        } catch {
            let message = "\(name) failed to launch: \(error.localizedDescription)"
            lastError = message
            append(message, stream: stream)
            throw error
        }
    }

    private func append(_ text: String, stream: LogStream, prefix: String? = nil) {
        let line = prefix.map { "[\($0)] \(text)" } ?? text
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
