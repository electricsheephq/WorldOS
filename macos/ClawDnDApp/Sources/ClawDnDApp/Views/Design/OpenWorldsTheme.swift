import SwiftUI

enum OpenWorldsTheme {
    static let parchment100 = Color(hex: 0xF6ECD2)
    static let parchment200 = Color(hex: 0xF2E7CE)
    static let parchment300 = Color(hex: 0xEAD9B5)
    static let parchment500 = Color(hex: 0xC8B287)
    static let parchmentEdge = Color(hex: 0x8A7146)

    static let walnut100 = Color(hex: 0x5A3E2B)
    static let walnut200 = Color(hex: 0x4B3425)
    static let walnut300 = Color(hex: 0x3A281B)
    static let walnut400 = Color(hex: 0x2A1D14)

    static let brass100 = Color(hex: 0xF0D8A0)
    static let brass200 = Color(hex: 0xD4B97A)
    static let brass300 = Color(hex: 0xC9A66B)
    static let brass400 = Color(hex: 0xB08D57)
    static let brass600 = Color(hex: 0x5D4827)

    static let ink900 = Color(hex: 0x1C130A)
    static let ink800 = Color(hex: 0x2C1F17)
    static let ink700 = Color(hex: 0x4A3325)
    static let ink600 = Color(hex: 0x6A4D35)

    static let royal = Color(hex: 0x22305E)
    static let crimson = Color(hex: 0x6E1D1D)
    static let emerald = Color(hex: 0x2F5A3A)
    static let goldGlow = Color(hex: 0xF4D27B)

    static let railWidth: CGFloat = 92
    static let outerRadius: CGFloat = 8
    static let panelRadius: CGFloat = 7
}


extension Color {
    init(hex: UInt, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255,
            opacity: opacity
        )
    }
}

struct OpenWorldsWindowBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    OpenWorldsTheme.walnut200,
                    OpenWorldsTheme.walnut300,
                    OpenWorldsTheme.walnut400
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            OpenWorldsTheme.ink900
                .opacity(0.22)
                .blendMode(.multiply)
            OpenWorldsTheme.brass400
                .opacity(0.26)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .mask(
                    RoundedRectangle(cornerRadius: OpenWorldsTheme.outerRadius)
                        .stroke(lineWidth: 2)
                        .padding(8)
                )
        }
        .ignoresSafeArea()
    }
}

struct OpenWorldsParchmentBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    OpenWorldsTheme.parchment100,
                    OpenWorldsTheme.parchment200,
                    OpenWorldsTheme.parchment300.opacity(0.92)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            OpenWorldsTheme.brass100.opacity(0.12)
                .blendMode(.softLight)
        }
    }
}
