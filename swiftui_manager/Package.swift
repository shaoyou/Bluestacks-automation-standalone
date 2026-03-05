// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "BSManagerApp",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "BSManagerApp", targets: ["BSManagerApp"]),
    ],
    targets: [
        .executableTarget(
            name: "BSManagerApp",
            path: "Sources/BSManagerApp"
        ),
    ]
)
