import AppKit
import SwiftUI

@main
struct ClawDnDApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.openWindow) private var openWindow
    @StateObject private var processService = AppProcessService()
    @StateObject private var campaignStore = CampaignStore()
    @StateObject private var updaterService = UpdaterService()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(processService)
                .environmentObject(campaignStore)
                .environmentObject(updaterService)
                .frame(minWidth: 1120, minHeight: 720)
        }
        WindowGroup("Debug Control Center", id: "debug-control-center") {
            DebugControlCenterView()
                .environmentObject(processService)
                .environmentObject(campaignStore)
                .environmentObject(updaterService)
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
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}
