import Foundation

enum AppSection: String, CaseIterable, Identifiable {
    case play
    case campaigns
    case monitor
    case providers
    case settings
    case logs

    var id: String { rawValue }

    var title: String {
        switch self {
        case .play: "Play"
        case .campaigns: "Campaigns"
        case .monitor: "Monitor"
        case .providers: "Providers"
        case .settings: "Settings"
        case .logs: "Logs"
        }
    }

    var symbolName: String {
        switch self {
        case .play: "play.circle"
        case .campaigns: "books.vertical"
        case .monitor: "waveform.path.ecg.rectangle"
        case .providers: "person.2.wave.2"
        case .settings: "gearshape"
        case .logs: "doc.text.magnifyingglass"
        }
    }
}
