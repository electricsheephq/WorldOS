// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "ClawDnDApp",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "ClawDnDApp", targets: ["ClawDnDApp"])
    ],
    targets: [
        .executableTarget(name: "ClawDnDApp")
    ]
)
