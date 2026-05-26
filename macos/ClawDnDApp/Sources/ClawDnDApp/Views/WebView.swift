import AppKit
import Foundation
import SwiftUI
import WebKit

struct NativeBridgeRequest {
    let requestId: String
    let type: String
    let payload: [String: Any]

    init?(body: Any) {
        guard let dict = body as? [String: Any],
              let type = dict["type"] as? String,
              !type.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            return nil
        }
        self.requestId = (dict["requestId"] as? String) ?? UUID().uuidString
        self.type = type
        self.payload = dict["payload"] as? [String: Any] ?? [:]
    }
}

struct NativeBridgeReply {
    let ok: Bool
    let requestId: String
    let type: String
    let payload: [String: Any]
    let error: String?

    static func success(request: NativeBridgeRequest, payload: [String: Any]) -> NativeBridgeReply {
        NativeBridgeReply(ok: true, requestId: request.requestId, type: request.type, payload: payload, error: nil)
    }

    static func failure(request: NativeBridgeRequest, error: String) -> NativeBridgeReply {
        NativeBridgeReply(ok: false, requestId: request.requestId, type: request.type, payload: [:], error: error)
    }

    static func malformed() -> NativeBridgeReply {
        NativeBridgeReply(ok: false, requestId: UUID().uuidString, type: "malformed", payload: [:], error: "Malformed native bridge request.")
    }

    var dictionary: [String: Any] {
        var dict: [String: Any] = [
            "ok": ok,
            "requestId": requestId,
            "type": type,
            "payload": payload,
        ]
        if let error {
            dict["error"] = error
        }
        return dict
    }
}

struct WebView: NSViewRepresentable {
    typealias NativeRequestHandler = @MainActor (NativeBridgeRequest, NSWindow?) async -> NativeBridgeReply

    let url: URL?
    @Binding var navigationError: String?
    let nativeRequestHandler: NativeRequestHandler?

    init(
        url: URL?,
        navigationError: Binding<String?>,
        nativeRequestHandler: NativeRequestHandler? = nil
    ) {
        self.url = url
        _navigationError = navigationError
        self.nativeRequestHandler = nativeRequestHandler
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        if nativeRequestHandler != nil {
            configuration.userContentController.addUserScript(Self.nativeBridgeScript)
            configuration.userContentController.add(context.coordinator, name: "clawdnd")
            context.coordinator.hasNativeMessageHandler = true
        }
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsBackForwardNavigationGestures = true
        view.navigationDelegate = context.coordinator
        context.coordinator.webView = view
        context.coordinator.nativeRequestHandler = nativeRequestHandler
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.nativeRequestHandler = nativeRequestHandler
        guard let url else { return }
        if view.url != url {
            navigationError = nil
            view.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(navigationError: $navigationError)
    }

    static func dismantleNSView(_ nsView: WKWebView, coordinator: Coordinator) {
        if coordinator.hasNativeMessageHandler {
            nsView.configuration.userContentController.removeScriptMessageHandler(forName: "clawdnd")
        }
    }

    private static let nativeBridgeScript = WKUserScript(
        source: """
        (function () {
          if (window.ClawDnDNative && window.ClawDnDNative.__installed) return;
          const callbacks = {};
          function uuid() {
            if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
            return "native-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
          }
          window.ClawDnDNative = {
            __installed: true,
            request: function (type, payload) {
              const requestId = uuid();
              const body = { requestId: requestId, type: type, payload: payload || {} };
              return new Promise(function (resolve, reject) {
                callbacks[requestId] = { resolve: resolve, reject: reject };
                window.webkit.messageHandlers.clawdnd.postMessage(body);
              });
            },
            _reply: function (message) {
              const callback = callbacks[message.requestId];
              if (callback) {
                delete callbacks[message.requestId];
                if (message.ok) callback.resolve(message.payload || {});
                else callback.reject(new Error(message.error || "Native bridge request failed."));
              }
              window.dispatchEvent(new CustomEvent("clawdnd:native-reply", { detail: message }));
            }
          };
          window.dispatchEvent(new CustomEvent("clawdnd:native-ready"));
        })();
        """,
        injectionTime: .atDocumentStart,
        forMainFrameOnly: true
    )

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        private let navigationError: Binding<String?>
        weak var webView: WKWebView?
        var nativeRequestHandler: NativeRequestHandler?
        var hasNativeMessageHandler = false

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
            navigationError.wrappedValue = "Surface failed to load: \(error.localizedDescription)"
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "clawdnd" else { return }
            guard let request = NativeBridgeRequest(body: message.body) else {
                send(.malformed())
                return
            }
            guard let nativeRequestHandler else {
                send(.failure(request: request, error: "Native bridge is unavailable."))
                return
            }
            let sourceWindow = webView?.window
            Task { @MainActor in
                let reply = await nativeRequestHandler(request, sourceWindow)
                self.send(reply)
            }
        }

        private func send(_ reply: NativeBridgeReply) {
            let dictionary = reply.dictionary
            guard JSONSerialization.isValidJSONObject(dictionary) else {
                NSLog("ClawDnD native bridge produced an invalid JSON reply: \(dictionary)")
                return
            }
            do {
                let data = try JSONSerialization.data(withJSONObject: dictionary)
                guard let json = String(data: data, encoding: .utf8) else {
                    NSLog("ClawDnD native bridge failed to encode reply as UTF-8: \(dictionary)")
                    return
                }
                webView?.evaluateJavaScript("window.ClawDnDNative && window.ClawDnDNative._reply(\(json));")
            } catch {
                NSLog("ClawDnD native bridge reply serialization failed: \(error); reply=\(dictionary)")
            }
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
            Text("Surface Unavailable")
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
