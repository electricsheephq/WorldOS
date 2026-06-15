import AppKit
import SwiftUI

@main
struct WorldOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.openWindow) private var openWindow
    @StateObject private var processService = AppProcessService()
    @StateObject private var campaignStore = CampaignStore()

    init() {
        // #892: launched from Finder/Dock/`open -n`, the app inherits the bare LaunchServices
        // PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) and cannot find the provider CLIs
        // (claude/codex/openclaw) or the engine's uv/node — every Shell.which guard returns nil,
        // so no provider can start and the launch wedges (`no_launcher`). Resolve the login-shell
        // PATH once here, before any provider detection / Shell.which runs.
        EnvironmentBootstrap.ensureLoginPATH()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(processService)
                .environmentObject(campaignStore)
                .frame(minWidth: 1120, minHeight: 720)
                .onAppear {
                    // Hand the delegate a reference so it can reap child processes
                    // (viewer/provider) when the app terminates. Without this, the
                    // local python3 viewer is orphaned across quit/relaunch and holds ports.
                    appDelegate.processService = processService
                }
        }
        WindowGroup("Debug Control Center", id: "debug-control-center") {
            DebugControlCenterView()
                .environmentObject(processService)
                .environmentObject(campaignStore)
                .frame(minWidth: 1120, minHeight: 720)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("Open Debug Control Center") {
                    openWindow(id: "debug-control-center")
                }
                .keyboardShortcut("d", modifiers: [.command, .option])

                Button("Copy Diagnostics") {
                    Diagnostics.copy(processService: processService)
                }
                .keyboardShortcut("d", modifiers: [.command, .shift])
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    // Set by the App once the scene appears; used only to reap child processes on quit.
    weak var processService: AppProcessService?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Reap the local viewer and any running provider so quitting/relaunching the
        // app never leaves orphaned `python3 viewer/server.py` processes holding ports.
        // applicationWillTerminate runs on the main thread, matching AppProcessService's
        // @MainActor isolation, so these calls are safe synchronously.
        MainActor.assumeIsolated {
            processService?.stopViewer()
            processService?.stopProvider()
        }
    }
}
