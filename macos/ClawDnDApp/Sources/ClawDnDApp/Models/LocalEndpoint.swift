import Foundation

enum EndpointStatus: String, Equatable {
    case stopped
    case starting
    case running
    case failed
}

struct LocalEndpoint: Identifiable, Equatable {
    let id = UUID()
    var name: String
    var url: URL
    var healthPath: String
    var status: EndpointStatus

    var port: Int {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?.port ?? 0
    }

    var dashboardURL: URL {
        url.appendingPathComponent("dashboard")
    }

    var monitorURL: URL {
        url.appendingPathComponent("monitor")
    }
}
