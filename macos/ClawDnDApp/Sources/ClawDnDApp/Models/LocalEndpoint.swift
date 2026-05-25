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
    var port: Int
    var url: URL
    var healthPath: String
    var status: EndpointStatus

    var dashboardURL: URL {
        URL(string: "http://127.0.0.1:\(port)/dashboard")!
    }

    var monitorURL: URL {
        URL(string: "http://127.0.0.1:\(port)/monitor")!
    }
}
