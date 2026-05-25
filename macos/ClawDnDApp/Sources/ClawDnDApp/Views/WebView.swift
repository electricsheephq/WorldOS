import SwiftUI
import WebKit

struct WebView: NSViewRepresentable {
    let url: URL?
    @Binding var navigationError: String?

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsBackForwardNavigationGestures = true
        view.navigationDelegate = context.coordinator
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard let url else { return }
        if view.url != url {
            navigationError = nil
            view.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(navigationError: $navigationError)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private let navigationError: Binding<String?>

        init(navigationError: Binding<String?>) {
            self.navigationError = navigationError
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            navigationError.wrappedValue = nil
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        private func report(_ error: Error) {
            let nsError = error as NSError
            guard nsError.domain != NSURLErrorDomain || nsError.code != NSURLErrorCancelled else { return }
            navigationError.wrappedValue = "Dashboard failed to load: \(error.localizedDescription)"
        }
    }
}

struct WebViewErrorView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 42, weight: .regular))
                .foregroundStyle(.orange)
            Text("Dashboard Unavailable")
                .font(.title3.weight(.semibold))
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .lineLimit(3)
                .frame(maxWidth: 520)
            Button {
                retry()
            } label: {
                Label("Retry", systemImage: "arrow.clockwise")
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
