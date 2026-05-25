import Foundation

enum CampaignSource: String, Codable {
    case play
    case qa
}

struct CampaignSummary: Identifiable, Equatable {
    let id: String
    let runID: String
    let source: CampaignSource
    let snapshotPath: URL
    let stateRoot: URL
    let title: String
    let world: String
    let day: Int?
    let timeOfDay: String
    let location: String
    let party: [String]
    let provider: String
    let lastUpdate: Date
    let isLive: Bool

    var sourceLabel: String {
        switch source {
        case .play: "Play"
        case .qa: "QA"
        }
    }

    var partyLabel: String {
        party.isEmpty ? "No party yet" : party.joined(separator: ", ")
    }

    var dayLabel: String {
        if let day {
            return "Day \(day), \(timeOfDay)"
        }
        return timeOfDay.isEmpty ? "Unknown time" : timeOfDay
    }
}
