import AppKit
import SwiftUI

@main
struct ClawDnDApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var processService = AppProcessService()
    @StateObject private var campaignStore = CampaignStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(processService)
                .environmentObject(campaignStore)
                .frame(minWidth: 1120, minHeight: 720)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
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
