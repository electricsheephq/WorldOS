import Foundation
import Sparkle

@MainActor
final class UpdaterService: ObservableObject {
    @Published private(set) var lastCheckRequestedAt: Date?
    @Published private(set) var lastError: String?

    private let updaterController: SPUStandardUpdaterController?

    init() {
        if Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String == nil {
            updaterController = nil
            lastError = "Sparkle feed URL is missing from Info.plist."
        } else if Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String == nil {
            updaterController = nil
            lastError = "Sparkle public key is missing from Info.plist."
        } else {
            updaterController = SPUStandardUpdaterController(
                startingUpdater: true,
                updaterDelegate: nil,
                userDriverDelegate: nil
            )
        }
    }

    var statusPayload: [String: Any] {
        let info = Bundle.main.infoDictionary ?? [:]
        var payload: [String: Any] = [
            "version": info["CFBundleShortVersionString"] as? String ?? "0.0.0",
            "build": info["CFBundleVersion"] as? String ?? "0",
            "bundleIdentifier": info["CFBundleIdentifier"] as? String ?? "",
            "feedURL": info["SUFeedURL"] as? String ?? "",
            "channel": info["ClawDnDUpdateChannel"] as? String ?? "local-beta",
            "canCheckForUpdates": updaterController?.updater.canCheckForUpdates ?? false,
            "publicKeyConfigured": (info["SUPublicEDKey"] as? String)?.isEmpty == false,
            "status": updaterController == nil ? "unavailable" : "ready",
            "lastError": lastError ?? "",
        ]
        if let lastCheckRequestedAt {
            payload["lastCheckRequestedAt"] = Self.isoDateFormatter.string(from: lastCheckRequestedAt)
        }
        return payload
    }

    func checkForUpdates() throws -> [String: Any] {
        guard let updaterController else {
            throw ProviderError.configuration(lastError ?? "Sparkle updater is unavailable.")
        }
        guard updaterController.updater.canCheckForUpdates else {
            throw ProviderError.configuration("Sparkle updater is not ready to check for updates yet.")
        }
        lastCheckRequestedAt = Date()
        lastError = nil
        updaterController.checkForUpdates(nil)
        return statusPayload
    }

    private static let isoDateFormatter = ISO8601DateFormatter()
}
