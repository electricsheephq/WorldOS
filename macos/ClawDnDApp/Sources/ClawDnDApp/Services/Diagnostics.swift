import AppKit
import Foundation

enum Diagnostics {
    @MainActor
    static func copy(processService: AppProcessService) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(processService.diagnostics, forType: .string)
    }
}
