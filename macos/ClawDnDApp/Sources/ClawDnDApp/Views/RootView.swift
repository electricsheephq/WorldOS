import AppKit
import Foundation
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore
    @EnvironmentObject private var updaterService: UpdaterService

    @AppStorage("repoPath") private var repoPath: String = RepositoryLocator.defaultRepoPath() ?? ""
    @AppStorage("preferredPort") private var preferredPort: Int = 8765
    @AppStorage("stateDir") private var stateDir: String = ""
    @AppStorage("selectedProvider") private var selectedProviderRaw: String = ProviderKind.claude.rawValue
    @AppStorage("defaultWorld") private var defaultWorld: String = "baldurs-gate"
    @AppStorage("codexProviderCommand") private var codexProviderCommand: String = ""
    @AppStorage("openClawProviderCommand") private var openClawProviderCommand: String = ""
    @AppStorage("budget") private var budget: String = "1.50"
    @AppStorage("sessionBudget") private var sessionBudget: String = "15.00"
    @AppStorage("maxTurns") private var maxTurns: String = "40"
    @AppStorage("voiceBackend") private var voiceBackend: String = "null"

    @State private var webURL: URL?
    @State private var webViewErrorMessage: String?
    @State private var launchMessage = "Opening OpenWorlds"
    @State private var launchError: String?
    @State private var isStarting = false
    @State private var launchTask: Task<Void, Never>?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let webURL, webViewErrorMessage == nil {
                WebView(
                    url: webURL,
                    navigationError: $webViewErrorMessage,
                    nativeRequestHandler: handleNativeRequest
                )
                .ignoresSafeArea()
            } else {
                OpenWorldsLaunchOverlay(
                    message: launchError ?? launchMessage,
                    isError: launchError != nil,
                    isStarting: isStarting,
                    retry: startOpenWorlds
                )
            }

            if let webViewErrorMessage {
                WebViewErrorView(message: webViewErrorMessage) {
                    self.webViewErrorMessage = nil
                    startOpenWorlds()
                }
                .background(.black.opacity(0.82))
            }
        }
        .overlay(alignment: .top) {
            OpenWorldsDragStrip()
                .frame(width: 560, height: 36)
                .accessibilityHidden(true)
        }
        .onAppear {
            refresh()
            startOpenWorlds()
        }
        .onChange(of: repoPath) { _ in
            refresh()
            startOpenWorlds()
        }
        .onDisappear {
            launchTask?.cancel()
        }
        .background(OpenWorldsWindowChrome())
    }

    private func refresh() {
        processService.refreshDependencies()
        if let resolved = resolveAndPersistRepoPath() {
            campaignStore.reload(repoPath: resolved)
        } else {
            campaignStore.reload(repoPath: repoPath)
        }
    }

    private func startOpenWorlds() {
        launchTask?.cancel()
        launchTask = Task { @MainActor in
            isStarting = true
            launchError = nil
            webViewErrorMessage = nil
            webURL = nil
            launchMessage = "Starting the local viewer"
            do {
                let resolvedRepoPath = try requireRepoPath()
                let url = try processService.startViewer(
                    repoPath: resolvedRepoPath,
                    preferredPort: preferredPort,
                    stateDir: stateDir
                )
                launchMessage = "Waiting for OpenWorlds"
                try await waitForOpenWorlds(url)
                guard !Task.isCancelled else { return }
                webURL = url
                launchMessage = "OpenWorlds ready"
            } catch {
                guard !Task.isCancelled else { return }
                launchError = error.localizedDescription
            }
            isStarting = false
        }
    }

    private func waitForOpenWorlds(_ url: URL) async throws {
        let deadline = Date().addingTimeInterval(8)
        var lastError = "not ready"

        while Date() < deadline {
            try Task.checkCancellation()
            do {
                var request = URLRequest(url: url)
                request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
                request.timeoutInterval = 1
                let (_, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse else {
                    lastError = "non-HTTP response"
                    continue
                }
                switch http.statusCode {
                case 200..<300:
                    return
                case 300..<400:
                    lastError = "HTTP \(http.statusCode) redirect"
                case 400..<500:
                    throw ProviderError.configuration(
                        "Viewer returned HTTP \(http.statusCode) at \(url.absoluteString)"
                    )
                default:
                    lastError = "HTTP \(http.statusCode)"
                }
            } catch let error as ProviderError {
                throw error
            } catch {
                lastError = error.localizedDescription
            }
            try await Task.sleep(nanoseconds: 250_000_000)
        }

        throw ProviderError.configuration("Viewer did not become ready at \(url.absoluteString): \(lastError)")
    }

    private func handleNativeRequest(_ request: NativeBridgeRequest) async -> NativeBridgeReply {
        do {
            let payload = try await nativePayload(for: request)
            return .success(request: request, payload: payload)
        } catch {
            return .failure(request: request, error: error.localizedDescription)
        }
    }

    private func nativePayload(for request: NativeBridgeRequest) async throws -> [String: Any] {
        switch request.type {
        case "appStatus":
            return appStatusPayload()
        case "dependencyStatus":
            processService.refreshDependencies()
            return ["dependencies": dependencyPayload()]
        case "providerStatuses":
            return ["providers": providerStatusesPayload()]
        case "startViewer":
            return try await startViewerFromBridge(request.payload)
        case "stopViewer":
            processService.stopViewer()
            webURL = nil
            return appStatusPayload()
        case "startProviderSession":
            return try await startProviderFromBridge(request.payload)
        case "stopProvider":
            processService.stopProvider()
            return appStatusPayload()
        case "diagnostics":
            return ["diagnostics": processService.diagnostics]
        case "copyDiagnostics":
            Diagnostics.copy(processService: processService)
            return ["copied": true]
        case "updaterStatus":
            return ["updater": updaterService.statusPayload]
        case "checkForUpdates":
            return ["updater": try updaterService.checkForUpdates()]
        case "windowCommand":
            return try performWindowCommand(request.payload)
        case "openFallbackDashboard":
            let dashboardURL = try await ensureDashboardURL()
            NSWorkspace.shared.open(dashboardURL)
            return ["url": dashboardURL.absoluteString]
        default:
            throw ProviderError.configuration("Unknown native bridge request: \(request.type)")
        }
    }

    private func startViewerFromBridge(_ payload: [String: Any]) async throws -> [String: Any] {
        let campaignID = stringPayload(payload, "campaignID").flatMap { $0.isEmpty ? nil : $0 }
        let resolvedRepoPath = try requireRepoPath()
        let url = try processService.startViewer(
            repoPath: resolvedRepoPath,
            preferredPort: preferredPort,
            stateDir: stateDir,
            campaignID: campaignID
        )
        try await waitForOpenWorlds(url)
        webURL = url
        return appStatusPayload(extra: ["url": url.absoluteString])
    }

    private func startProviderFromBridge(_ payload: [String: Any]) async throws -> [String: Any] {
        let providerRaw = stringPayload(payload, "provider") ?? selectedProviderRaw
        let provider = ProviderKind(rawValue: providerRaw) ?? .claude
        let world = stringPayload(payload, "world") ?? defaultWorld
        let runId = stringPayload(payload, "runId").flatMap { $0.isEmpty ? nil : $0 } ?? Self.newRunID()
        let companions = stringPayload(payload, "companions") ?? ""
        let resolvedRepoPath = try requireRepoPath()
        let url = try processService.startProviderSession(
            kind: provider,
            repoPath: resolvedRepoPath,
            world: world,
            runId: runId,
            preferredPort: preferredPort,
            companions: companions,
            stateDir: stateDir,
            preferences: providerPreferences
        )
        try await waitForOpenWorlds(url)
        webURL = url
        return appStatusPayload(extra: ["url": url.absoluteString, "runId": runId])
    }

    private func ensureDashboardURL() async throws -> URL {
        if let endpoint = processService.viewerEndpoint {
            return endpoint.dashboardURL
        }
        let resolvedRepoPath = try requireRepoPath()
        let url = try processService.startViewer(
            repoPath: resolvedRepoPath,
            preferredPort: preferredPort,
            stateDir: stateDir
        )
        try await waitForOpenWorlds(url)
        webURL = url
        return processService.viewerEndpoint?.dashboardURL ?? url
    }

    private func appStatusPayload(extra: [String: Any] = [:]) -> [String: Any] {
        let currentRepoPath = resolvedRepoPath() ?? repoPath
        var payload: [String: Any] = [
            "repoPath": currentRepoPath,
            "stateDir": stateDir.isEmpty ? "default" : stateDir,
            "preferredPort": preferredPort,
            "defaultWorld": defaultWorld,
            "selectedProvider": selectedProviderRaw,
            "voiceBackend": voiceBackend,
            "viewer": endpointPayload(processService.viewerEndpoint) as Any,
            "activeCampaign": processService.activeCampaignID ?? "",
            "runningProvider": processService.runningProvider?.rawValue ?? "",
            "lastError": processService.lastError ?? "",
            "preferences": [
                "repoPath": currentRepoPath,
                "preferredPort": preferredPort,
                "stateDir": stateDir,
                "selectedProvider": selectedProviderRaw,
                "defaultWorld": defaultWorld,
                "budget": budget,
                "sessionBudget": sessionBudget,
                "maxTurns": maxTurns,
                "voiceBackend": voiceBackend,
            ],
            "providers": providerStatusesPayload(),
            "dependencies": dependencyPayload(),
            "providerDiagnostics": Diagnostics.providerLaunchSummary(processService.providerLaunchMetadata),
            "openWorldsAssetsPath": processService.openWorldsAssetsPath ?? "",
            "updater": updaterService.statusPayload,
        ]
        extra.forEach { payload[$0.key] = $0.value }
        return payload
    }

    private func performWindowCommand(_ payload: [String: Any]) throws -> [String: Any] {
        guard let rawCommand = stringPayload(payload, "command")?.lowercased(), !rawCommand.isEmpty else {
            throw ProviderError.configuration("windowCommand requires a command.")
        }
        guard let window = NSApp.keyWindow ?? NSApp.mainWindow ?? NSApp.windows.first(where: { $0.isVisible }) else {
            throw ProviderError.configuration("No active app window is available.")
        }
        switch rawCommand {
        case "close":
            DispatchQueue.main.async { window.performClose(nil) }
        case "minimize":
            DispatchQueue.main.async { window.miniaturize(nil) }
        case "zoom":
            DispatchQueue.main.async { window.zoom(nil) }
        default:
            throw ProviderError.configuration("Unsupported window command: \(rawCommand)")
        }
        return ["command": rawCommand, "performed": true]
    }

    private func endpointPayload(_ endpoint: LocalEndpoint?) -> [String: Any] {
        guard let endpoint else {
            return ["status": EndpointStatus.stopped.rawValue]
        }
        return [
            "name": endpoint.name,
            "url": endpoint.url.absoluteString,
            "openWorldsURL": endpoint.openWorldsURL.absoluteString,
            "dashboardURL": endpoint.dashboardURL.absoluteString,
            "monitorURL": endpoint.monitorURL.absoluteString,
            "healthPath": endpoint.healthPath,
            "status": endpoint.status.rawValue,
            "port": endpoint.port,
        ]
    }

    private func dependencyPayload() -> [[String: Any]] {
        processService.dependencies.map {
            [
                "command": $0.command,
                "requiredFor": $0.requiredFor,
                "path": $0.path ?? "",
                "installed": $0.isInstalled,
            ]
        }
    }

    private func providerStatusesPayload() -> [[String: Any]] {
        let currentRepoPath = resolvedRepoPath() ?? repoPath
        return processService.providerStatuses(repoPath: currentRepoPath, preferences: providerPreferences).map {
            [
                "kind": $0.kind.rawValue,
                "displayName": $0.kind.displayName,
                "availability": $0.availability.rawValue,
                "detail": $0.detail,
                "detectedPath": $0.detectedPath ?? "",
                "launchable": $0.isLaunchable,
            ]
        }
    }

    private func requireRepoPath() throws -> String {
        if let resolved = resolveAndPersistRepoPath() {
            return resolved
        }
        throw ProviderError.configuration(
            "Repo path is not a ClawDnD checkout. Choose a checkout in Settings or set CLAWDND_REPO_ROOT."
        )
    }

    private func resolveAndPersistRepoPath() -> String? {
        guard let resolved = resolvedRepoPath() else { return nil }
        if resolved != repoPath {
            repoPath = resolved
        }
        return resolved
    }

    private func resolvedRepoPath() -> String? {
        if let compatible = RepositoryLocator.openWorldsRepoPath(repoPath) {
            return compatible
        }
        if let discovered = RepositoryLocator.defaultOpenWorldsRepoPath() {
            return discovered
        }
        return nil
    }

    private var providerPreferences: ProviderPreferences {
        ProviderPreferences(
            codexCommand: codexProviderCommand,
            openClawCommand: openClawProviderCommand,
            budget: budget,
            sessionBudget: sessionBudget,
            maxTurns: maxTurns
        )
    }

    private func stringPayload(_ payload: [String: Any], _ key: String) -> String? {
        guard let value = payload[key] else { return nil }
        if let string = value as? String {
            return string.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return "\(value)".trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static let runIDFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()

    private static func newRunID() -> String {
        "play-\(runIDFormatter.string(from: Date()))"
    }
}

private struct OpenWorldsWindowChrome: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        OpenWorldsChromeHostView(frame: .zero)
    }

    func updateNSView(_ view: NSView, context: Context) {
        OpenWorldsChromeHostView.configure(view.window)
    }
}

private final class OpenWorldsChromeHostView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        Self.configure(window)
    }

    static func configure(_ window: NSWindow?) {
        guard let window else { return }
        DispatchQueue.main.async {
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.styleMask.insert(.fullSizeContentView)
            window.styleMask.remove(.titled)
            window.styleMask.insert(.resizable)
            window.toolbar = nil
            window.backgroundColor = .black
            window.isOpaque = true
            window.isMovableByWindowBackground = true

            [
                NSWindow.ButtonType.closeButton,
                .miniaturizeButton,
                .zoomButton
            ].forEach { button in
                window.standardWindowButton(button)?.isHidden = true
            }
        }
    }
}

private struct OpenWorldsDragStrip: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        OpenWorldsDragStripView(frame: .zero)
    }

    func updateNSView(_ view: NSView, context: Context) {}
}

private final class OpenWorldsDragStripView: NSView {
    override var acceptsFirstResponder: Bool { false }

    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}

private struct OpenWorldsLaunchOverlay: View {
    let message: String
    let isError: Bool
    let isStarting: Bool
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Text("Open Worlds")
                .font(.system(size: 28, weight: .semibold, design: .serif))
                .foregroundStyle(Color(red: 0.94, green: 0.82, blue: 0.52))
                .tracking(6)
            Text(message)
                .font(.callout)
                .foregroundStyle(isError ? .orange : .secondary)
                .multilineTextAlignment(.center)
            if isStarting {
                ProgressView()
                    .controlSize(.small)
            }
            if isError {
                Button("Retry", action: retry)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            LinearGradient(
                colors: [Color(red: 0.06, green: 0.035, blue: 0.02), Color(red: 0.15, green: 0.09, blue: 0.05)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }
}

struct DebugControlCenterView: View {
    @EnvironmentObject private var processService: AppProcessService
    @EnvironmentObject private var campaignStore: CampaignStore

    @AppStorage("repoPath") private var repoPath: String = RepositoryLocator.defaultRepoPath() ?? ""
    @AppStorage("preferredPort") private var preferredPort: Int = 8765
    @AppStorage("stateDir") private var stateDir: String = ""
    @AppStorage("selectedProvider") private var selectedProviderRaw: String = ProviderKind.claude.rawValue
    @AppStorage("defaultWorld") private var defaultWorld: String = "baldurs-gate"
    @AppStorage("codexProviderCommand") private var codexProviderCommand: String = ""
    @AppStorage("openClawProviderCommand") private var openClawProviderCommand: String = ""
    @AppStorage("budget") private var budget: String = "1.50"
    @AppStorage("sessionBudget") private var sessionBudget: String = "15.00"
    @AppStorage("maxTurns") private var maxTurns: String = "40"
    @AppStorage("voiceBackend") private var voiceBackend: String = "null"

    @State private var selection: AppSection? = .play
    @State private var webURL: URL?

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: $selection)
        } detail: {
            VStack(spacing: 0) {
                detailView
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                StatusStrip(repoPath: repoPath, stateDir: stateDir)
                    .environmentObject(processService)
            }
        }
        .onAppear(perform: refresh)
        .onChange(of: repoPath) { _ in refresh() }
    }

    @ViewBuilder
    private var detailView: some View {
        switch selection ?? .play {
        case .play:
            PlayView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                selectedProviderRaw: $selectedProviderRaw,
                defaultWorld: $defaultWorld,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns,
                webURL: $webURL
            )
        case .campaigns:
            CampaignsView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                webURL: $webURL
            )
        case .monitor:
            MonitorView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                webURL: $webURL
            )
        case .providers:
            ProvidersView(
                repoPath: $repoPath,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns
            )
        case .settings:
            SettingsView(
                repoPath: $repoPath,
                preferredPort: $preferredPort,
                stateDir: $stateDir,
                selectedProviderRaw: $selectedProviderRaw,
                defaultWorld: $defaultWorld,
                codexProviderCommand: $codexProviderCommand,
                openClawProviderCommand: $openClawProviderCommand,
                budget: $budget,
                sessionBudget: $sessionBudget,
                maxTurns: $maxTurns,
                voiceBackend: $voiceBackend
            )
        case .logs:
            LogsView()
        }
    }

    private func refresh() {
        processService.refreshDependencies()
        campaignStore.reload(repoPath: repoPath)
    }
}

struct SidebarView: View {
    @Binding var selection: AppSection?

    var body: some View {
        List(AppSection.allCases, selection: $selection) { section in
            Label(section.title, systemImage: section.symbolName)
                .tag(section)
        }
        .navigationSplitViewColumnWidth(min: 180, ideal: 210)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("ClawDnD")
                    .font(.headline)
            }
        }
    }
}

struct StatusStrip: View {
    @EnvironmentObject private var processService: AppProcessService
    let repoPath: String
    let stateDir: String

    var body: some View {
        HStack(spacing: 14) {
            statusItem("Viewer", processService.viewerEndpoint.map { "\($0.port) \($0.status.rawValue)" } ?? "stopped")
            statusItem("State", stateDir.isEmpty ? "default" : URL(fileURLWithPath: stateDir).lastPathComponent)
            statusItem("Campaign", processService.activeCampaignID ?? "auto")
            statusItem("Provider", processService.runningProvider?.displayName ?? "none")
            if let lastError = processService.lastError {
                Label(lastError, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
                    .lineLimit(1)
            } else {
                Spacer(minLength: 8)
            }
        }
        .font(.caption)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func statusItem(_ label: String, _ value: String) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .lineLimit(1)
        }
    }
}
