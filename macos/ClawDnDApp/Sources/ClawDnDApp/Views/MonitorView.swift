import SwiftUI

struct MonitorView: View {
    @EnvironmentObject private var processService: AppProcessService
    @Binding var repoPath: String
    @Binding var preferredPort: Int
    @Binding var stateDir: String
    @Binding var webURL: URL?
    @State private var alertMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Monitor")
                        .font(.title2.weight(.semibold))
                    Text("Read-only dashboard monitor for local play and QA snapshots.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    openMonitor()
                } label: {
                    Label("Open Monitor", systemImage: "waveform.path.ecg.rectangle")
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(16)
            Divider()
            if let webURL {
                WebView(url: webURL)
            } else {
                EmptyStateView(title: "No Monitor Open", symbolName: "waveform.path.ecg.rectangle")
            }
        }
        .alert("Monitor could not start", isPresented: alertBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(alertMessage ?? "")
        }
    }

    private func openMonitor() {
        do {
            let dashboard = try processService.startViewer(
                repoPath: repoPath,
                preferredPort: preferredPort,
                stateDir: stateDir
            )
            let port = processService.viewerEndpoint?.port ?? URLComponents(url: dashboard, resolvingAgainstBaseURL: false)?.port ?? preferredPort
            webURL = URL(string: "http://127.0.0.1:\(port)/monitor")
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private var alertBinding: Binding<Bool> {
        Binding(get: { alertMessage != nil }, set: { if !$0 { alertMessage = nil } })
    }
}
