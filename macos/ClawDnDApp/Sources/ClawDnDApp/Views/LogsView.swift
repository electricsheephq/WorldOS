import SwiftUI

struct LogsView: View {
    @EnvironmentObject private var processService: AppProcessService

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Logs")
                        .font(.title2.weight(.semibold))
                    Text("Supervisor, provider, and last-error diagnostics.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Diagnostics.copy(processService: processService)
                } label: {
                    Label("Copy Diagnostics", systemImage: "doc.on.doc")
                }
            }
            .padding(16)
            Divider()
            TabView {
                LogText(title: "Viewer", text: processService.supervisorLog)
                    .tabItem { Text("Viewer") }
                LogText(title: "Provider", text: processService.providerLog)
                    .tabItem { Text("Provider") }
                LogText(title: "Diagnostics", text: processService.diagnostics)
                    .tabItem { Text("Diagnostics") }
            }
            .padding(12)
        }
    }
}

struct LogText: View {
    let title: String
    let text: String

    var body: some View {
        ScrollView {
            Text(text.isEmpty ? "\(title) log is empty." : text)
                .font(.system(.caption, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .padding(12)
        }
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
