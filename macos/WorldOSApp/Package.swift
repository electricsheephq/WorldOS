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
        .executableTarget(name: "WorldOSApp")
    ]
)
