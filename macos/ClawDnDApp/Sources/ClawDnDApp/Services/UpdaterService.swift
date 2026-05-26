import Foundation
import Sparkle

@MainActor
final class UpdaterService: NSObject, ObservableObject, SPUUpdaterDelegate {
    @Published private(set) var lastCheckRequestedAt: Date?
    @Published private(set) var lastError: String?

    private var updaterController: SPUStandardUpdaterController?
    private var overrideFeedURL: URL?
    private var feedAvailable = false

    override init() {
        super.init()

        let feedURL = (Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let publicKey = (Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        if feedURL.isEmpty {
            updaterController = nil
            feedAvailable = false
            lastError = "Sparkle feed URL is missing or empty in Info.plist."
        } else if publicKey.isEmpty {
            updaterController = nil
            feedAvailable = false
            lastError = "Sparkle public key is missing or empty in Info.plist."
        } else {
            feedAvailable = true
            updaterController = SPUStandardUpdaterController(
                startingUpdater: true,
                updaterDelegate: self,
                userDriverDelegate: nil
            )
        }
    }

    func setFeedURL(_ feedURL: URL?, available: Bool = false) {
        overrideFeedURL = feedURL
        feedAvailable = available
        lastError = available ? nil : "Local beta appcast is unavailable."
    }

    var statusPayload: [String: Any] {
        let info = Bundle.main.infoDictionary ?? [:]
        var payload: [String: Any] = [
            "version": info["CFBundleShortVersionString"] as? String ?? "0.0.0",
            "build": info["CFBundleVersion"] as? String ?? "0",
            "bundleIdentifier": info["CFBundleIdentifier"] as? String ?? "",
            "feedURL": currentFeedURLString,
            "feedAvailable": feedAvailable,
            "channel": info["ClawDnDUpdateChannel"] as? String ?? "local-beta",
            "canCheckForUpdates": feedAvailable && (updaterController?.updater.canCheckForUpdates ?? false),
            "publicKeyConfigured": ((info["SUPublicEDKey"] as? String)?
                .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false),
            "status": updaterController == nil || !feedAvailable ? "unavailable" : "ready",
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
        guard feedAvailable else {
            lastError = "Local beta appcast is unavailable. Start the viewer from a packaged beta channel."
            throw ProviderError.configuration(lastError ?? "Local beta appcast is unavailable.")
        }
        guard updaterController.updater.canCheckForUpdates else {
            throw ProviderError.configuration("Sparkle updater is not ready to check for updates yet.")
        }
        guard let feedURL = currentFeedURL, ["http", "https"].contains(feedURL.scheme?.lowercased() ?? "") else {
            lastError = "Sparkle feed is not reachable over HTTP yet. Start the viewer first."
            throw ProviderError.configuration(lastError ?? "Sparkle feed is not reachable over HTTP yet.")
        }
        lastCheckRequestedAt = Date()
        lastError = nil
        updaterController.checkForUpdates(nil)
        return statusPayload
    }

    @objc(feedURLStringForUpdater:)
    func feedURLString(for updater: SPUUpdater) -> String? {
        overrideFeedURL?.absoluteString
    }

    @objc(updater:didAbortWithError:)
    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        lastError = error.localizedDescription
    }

    @objc(updater:didFinishUpdateCycleForUpdateCheck:error:)
    func updater(
        _ updater: SPUUpdater,
        didFinishUpdateCycleFor updateCheck: SPUUpdateCheck,
        error: Error?
    ) {
        lastError = error?.localizedDescription
    }

    private var currentFeedURLString: String {
        overrideFeedURL?.absoluteString
            ?? (Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String ?? "")
    }

    private var currentFeedURL: URL? {
        URL(string: currentFeedURLString.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private static let isoDateFormatter = ISO8601DateFormatter()
}
