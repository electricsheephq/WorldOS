import Foundation

struct DependencyStatus: Identifiable, Equatable {
    var id: String { command }
    let command: String
    let requiredFor: String
    let path: String?

    var isInstalled: Bool { path != nil }
}
