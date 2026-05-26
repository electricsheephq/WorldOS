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
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.9.2")
    ],
    targets: [
        .executableTarget(
            name: "ClawDnDApp",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle")
            ]
        )
    ]
)
