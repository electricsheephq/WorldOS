// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "WorldOSApp",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "WorldOSApp", targets: ["WorldOSApp"])
    ],
    targets: [
        .executableTarget(name: "WorldOSApp"),
        // Additive: unit tests for the pure helpers (PATH augmentation / removable-volume env
        // filtering / port availability) via `@testable import WorldOSApp`. The app target is
        // otherwise unchanged; removing this target restores today's behavior exactly.
        .testTarget(
            name: "WorldOSAppTests",
            dependencies: ["WorldOSApp"]
        )
    ]
)
