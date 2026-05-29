import AppKit
import Foundation
import SwiftUI

struct RootView: View {
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
        campaignStore.reload(repoPath: repoPath)
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
                let url = try processService.startViewer(
                    repoPath: repoPath,
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
        // Host strain can push the viewer's first bind well past the old 8s budget even
        // when the port is free; 25s tolerates a slow start instead of falsely reporting
        // "could not connect" while the viewer is still coming up.
        let start = Date()
        let deadline = start.addingTimeInterval(25)
        let slowStartThreshold: TimeInterval = 8
        var announcedSlowStart = false
        var lastError = "not ready"

        while Date() < deadline {
            try Task.checkCancellation()
            if !announcedSlowStart, Date().timeIntervalSince(start) > slowStartThreshold {
                announcedSlowStart = true
                launchMessage = "Still starting the viewer (host is busy)…"
            }
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
        let url = try processService.startViewer(
            repoPath: repoPath,
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
        // Optional authored-hero spec (JSON) from the Creation wizard's Bind. When present, the
        // play script pre-seeds this exact PC via the engine before the DM's first turn; when
        // absent (the launcher's Begin/Resume path), the DM invents the PC as before.
        let hero = stringPayload(payload, "hero") ?? ""
        let url = try processService.startProviderSession(
            kind: provider,
            repoPath: repoPath,
            world: world,
            runId: runId,
            preferredPort: preferredPort,
            companions: companions,
            hero: hero,
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
        let url = try processService.startViewer(
            repoPath: repoPath,
            preferredPort: preferredPort,
            stateDir: stateDir
        )
        try await waitForOpenWorlds(url)
        webURL = url
        return processService.viewerEndpoint?.dashboardURL ?? url
    }

    private func appStatusPayload(extra: [String: Any] = [:]) -> [String: Any] {
        var payload: [String: Any] = [
            "repoPath": repoPath,
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
                "repoPath": repoPath,
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
        ]
        extra.forEach { payload[$0.key] = $0.value }
        return payload
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
        processService.providerStatuses(repoPath: repoPath, preferences: providerPreferences).map {
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
            // Keep the immersive edge-to-edge look (transparent, hidden title text,
            // content under the title bar) BUT keep the window a real titled window
            // so the native traffic lights actually WORK — previously .titled was
            // removed and all three standard buttons were hidden, leaving close /
            // minimize / zoom inert (the chrome.jsx "lights" are decorative CSS).
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.styleMask.insert(.titled)
            window.styleMask.insert(.fullSizeContentView)
            window.styleMask.insert(.resizable)
            window.styleMask.insert(.miniaturizable)
            window.styleMask.insert(.closable)
            window.toolbar = nil
            window.backgroundColor = .black
            window.isOpaque = true
            window.isMovableByWindowBackground = true

            // Show the real, functional macOS traffic lights (they float at top-left
            // over the immersive content thanks to fullSizeContentView).
            [
                NSWindow.ButtonType.closeButton,
                .miniaturizeButton,
                .zoomButton
            ].forEach { button in
                window.standardWindowButton(button)?.isHidden = false
            }
        }
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
                Text("WorldOS")
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
