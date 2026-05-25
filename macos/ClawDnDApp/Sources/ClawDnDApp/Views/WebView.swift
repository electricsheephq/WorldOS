import SwiftUI
import WebKit

struct WebView: NSViewRepresentable {
    let url: URL?

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsBackForwardNavigationGestures = true
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard let url else { return }
        if view.url != url {
            view.load(URLRequest(url: url))
        }
    }
}
