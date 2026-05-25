import SwiftUI

struct OpenWorldsPanel<Content: View>: View {
    let title: String?
    let subtitle: String?
    let icon: String?
    let content: Content

    init(
        title: String? = nil,
        subtitle: String? = nil,
        icon: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if title != nil || subtitle != nil || icon != nil {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    if let icon {
                        Image(systemName: icon)
                            .foregroundStyle(OpenWorldsTheme.crimson)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        if let title {
                            Text(title)
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(OpenWorldsTheme.ink800)
                        }
                        if let subtitle {
                            Text(subtitle)
                                .font(.caption)
                                .foregroundStyle(OpenWorldsTheme.ink600)
                        }
                    }
                    Spacer()
                }
                OpenWorldsDivider()
            }
            content
        }
        .padding(18)
        .background(OpenWorldsParchmentBackground())
        .clipShape(RoundedRectangle(cornerRadius: OpenWorldsTheme.panelRadius))
        .overlay {
            RoundedRectangle(cornerRadius: OpenWorldsTheme.panelRadius)
                .stroke(OpenWorldsTheme.parchmentEdge.opacity(0.62), lineWidth: 1)
        }
        .overlay(alignment: .topLeading) {
            OpenWorldsCornerOrnament()
                .frame(width: 28, height: 28)
                .padding(7)
        }
        .overlay(alignment: .topTrailing) {
            OpenWorldsCornerOrnament()
                .rotationEffect(.degrees(90))
                .frame(width: 28, height: 28)
                .padding(7)
        }
        .shadow(color: .black.opacity(0.24), radius: 16, x: 0, y: 8)
    }
}


struct OpenWorldsDivider: View {
    var body: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(OpenWorldsTheme.parchmentEdge.opacity(0.42))
                .frame(height: 1)
            Diamond()
                .fill(OpenWorldsTheme.brass400)
                .frame(width: 7, height: 7)
            Rectangle()
                .fill(OpenWorldsTheme.parchmentEdge.opacity(0.42))
                .frame(height: 1)
        }
    }
}

struct Diamond: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.midY))
        path.closeSubpath()
        return path
    }
}

struct OpenWorldsCornerOrnament: View {
    var body: some View {
        GeometryReader { proxy in
            Path { path in
                let w = proxy.size.width
                let h = proxy.size.height
                path.move(to: CGPoint(x: 2, y: h * 0.34))
                path.addCurve(
                    to: CGPoint(x: w * 0.34, y: 2),
                    control1: CGPoint(x: 2, y: 8),
                    control2: CGPoint(x: 8, y: 2)
                )
                path.move(to: CGPoint(x: 2, y: h * 0.58))
                path.addCurve(
                    to: CGPoint(x: w * 0.58, y: 2),
                    control1: CGPoint(x: 2, y: 10),
                    control2: CGPoint(x: 10, y: 2)
                )
                path.move(to: CGPoint(x: w * 0.22, y: h * 0.22))
                path.addCurve(
                    to: CGPoint(x: w * 0.48, y: h * 0.12),
                    control1: CGPoint(x: w * 0.34, y: h * 0.24),
                    control2: CGPoint(x: w * 0.42, y: h * 0.18)
                )
            }
            .stroke(OpenWorldsTheme.brass600.opacity(0.64), style: StrokeStyle(lineWidth: 1.1, lineCap: .round))
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct OpenWorldsPill: View {
    let text: String
    var tone: Tone = .neutral

    enum Tone {
        case neutral
        case live
        case warning
        case danger
        case royal
    }

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(foreground)
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(background)
            .clipShape(Capsule())
    }

    private var foreground: Color {
        switch tone {
        case .neutral: OpenWorldsTheme.ink700
        case .live: .white
        case .warning: OpenWorldsTheme.ink800
        case .danger: .white
        case .royal: .white
        }
    }

    private var background: Color {
        switch tone {
        case .neutral: OpenWorldsTheme.parchment300.opacity(0.9)
        case .live: OpenWorldsTheme.emerald
        case .warning: OpenWorldsTheme.brass300
        case .danger: OpenWorldsTheme.crimson
        case .royal: OpenWorldsTheme.royal
        }
    }
}

struct OpenWorldsBrassButtonStyle: ButtonStyle {
    var prominent = false
    var danger = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.semibold))
            .foregroundStyle(danger || prominent ? Color.white : OpenWorldsTheme.ink800)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(minHeight: 32)
            .background(background(configuration: configuration))
            .clipShape(RoundedRectangle(cornerRadius: 5))
            .overlay {
                RoundedRectangle(cornerRadius: 5)
                    .stroke(OpenWorldsTheme.brass600.opacity(0.55), lineWidth: 1)
            }
            .opacity(configuration.isPressed ? 0.78 : 1)
    }

    private func background(configuration: Configuration) -> some View {
        let base: [Color]
        if danger {
            base = [OpenWorldsTheme.crimson, OpenWorldsTheme.crimson.opacity(0.82)]
        } else if prominent {
            base = [OpenWorldsTheme.royal, OpenWorldsTheme.royal.opacity(0.82)]
        } else {
            base = [OpenWorldsTheme.brass100, OpenWorldsTheme.brass300]
        }
        return LinearGradient(colors: base, startPoint: .top, endPoint: .bottom)
    }
}
