// native_input.swift — the macOS PRIMITIVES behind the T3 native-window palette (issue #1436/#1322).
//
// The browser palette (qa/playwright/palette_server.js) drives a Playwright page. The T3 gate needs
// the SAME blind-player tool surface pointed at the NATIVE WorldOSPlayer.app window instead. This
// helper is the thin CoreGraphics/Quartz layer that the native palette server shells out to for the
// things a browser gives you for free: FIND the player's window, CAPTURE it, CLICK/press inside it,
// and PREFLIGHT the macOS TCC permissions those need.
//
// #1456: the `capture` subcommand images a window via ScreenCaptureKit (SCScreenshotManager) — which
// rasterizes a window on ANY Space WITHOUT activating it (no focus theft, no Space switch), unlike
// `screencapture -l` which macOS 15+ refuses for off-current-Space windows. So this helper now DOES
// capture pixels and DOES need the Screen-Recording grant (SCK enforces it, same as screencapture).
//
// It is deliberately a SINGLE self-contained file with no third-party deps so it both:
//   - runs interpreted:  `swift native_input.swift <subcommand> …`   (no build step; what the server uses)
//   - compiles:          `swiftc native_input.swift -o native_input`  (the harness self-check asserts this)
//
// Subcommands (each prints ONE line of JSON to stdout; exit 0 on success, 2 on a usage/lookup miss):
//   checkperms                      -> {"screen_recording":bool,"accessibility":bool}
//   winfind  <ownerName>            -> {"found":bool,"window_id":int,"x":n,"y":n,"w":n,"h":n,"owner":s,"title":s}
//   capture  <ownerName> <outPath>  -> {"ok":bool,"window_id":int,"x":n,"y":n,"w":n,"h":n,"px_w":n,"px_h":n,"scale":f,"on_screen":bool}
//                                      (ScreenCaptureKit PNG of the owner's largest window — ANY Space, NO activation; macOS 14+)
//   click    <globalX> <globalY> [double] [--owner NAME] [--activate-fallback]
//                                   -> {"ok":true,"delivery":"pid|hid"}   (left click at GLOBAL screen points, top-left origin)
//   key      <name> [--owner NAME] [--activate-fallback]
//                                   -> {"ok":true,"delivery":"pid|hid"}   (Return/Escape/Tab/Space/Arrow*/Delete or a single char)
//   type     <text…> [--owner NAME] [--activate-fallback]
//                                   -> {"ok":true,"delivery":"pid|hid"}   (synthesize a unicode string into the focused field)
//
// #1466 (completes #1456): input delivery is the twin of the no-activation CAPTURE. A HID-tap
// CGEvent (.cghidEventTap) at a global point is only routed into an app's Input system while that
// app is ACTIVE/key — but player QA must NEVER activate the owner (no focus theft, no Space switch),
// so those clicks landed on the window pixels yet never reached Unity's Input.GetMouseButtonDown
// (the T3/smoke "clicks do nothing" symptom). When an owner NAME is supplied we resolve its PID
// (kCGWindowOwnerPID of the largest layer-0 window, same pick as winfind) and DELIVER THE EVENT
// DIRECTLY to that process via CGEvent.postToPid — which reaches an unfocused, off-current-Space app
// without activating it. No owner -> the legacy HID-tap path (unchanged). `--activate-fallback` is the
// documented escape if PID delivery proves unreliable for Unity's input polling: a BRIEF
// activate->post->restore-previous-frontmost (sub-second, current Space only) — OFF by default; the
// PID path is tried first and this is opt-in with evidence.
//
// Coordinate contract: Quartz CGWindow bounds AND CGEvent cursor positions are BOTH global screen
// POINTS with a top-left origin, so they compose directly. The server maps window-relative screenshot
// PIXELS to global points via the window origin + the backing scale (pixel_w / point_w) before calling
// `click` — this helper only ever sees final global points and never guesses scale.

import Foundation
import CoreGraphics
import ImageIO
#if canImport(ApplicationServices)
import ApplicationServices
#endif
#if canImport(ScreenCaptureKit)
import ScreenCaptureKit
#endif
#if canImport(AppKit)
import AppKit   // #1466: activate->click->restore fallback (NSRunningApplication) only, no Space switch
#endif

// ---- tiny JSON emit (no Codable ceremony for one-line results) --------------
func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    } else {
        print("{\"ok\":false,\"error\":\"json-encode-failed\"}")
    }
}

// ---- permission preflight ---------------------------------------------------
// The native palette needs TWO TCC grants at RUNTIME, attached to the responsible app (the terminal /
// process that launched the harness): Screen Recording (screencapture of the window) + Accessibility
// (synthetic CGEvent input). We report both so the server can FAIL LOUD with the exact Settings pane.
// Owners that are always present in the on-screen list REGARDLESS of a Screen-Recording grant (system
// overlays live on high layers and leak their owner even when normal windows are redacted). If, after
// excluding these, ZERO normal (layer-0) windows are visible, the process is redacted == no effective
// grant. This EMPIRICAL signal is authoritative: on macOS 14+/15+ CGPreflightScreenCaptureAccess can
// return true while the actual window list (and screencapture) is still redacted (observed on this box).
let SYSTEM_OWNERS: Set<String> = ["Window Server", "Dock", "Control Center", "Spotlight", "Notification Center"]

func normalWindowCount() -> Int {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { return 0 }
    var n = 0
    for w in list {
        let layer = w[kCGWindowLayer as String] as? Int ?? -1
        let owner = w[kCGWindowOwnerName as String] as? String ?? ""
        if layer == 0 && !owner.isEmpty && !SYSTEM_OWNERS.contains(owner) { n += 1 }
    }
    return n
}

func checkPerms() {
    var preflight = true
    // CGPreflightScreenCaptureAccess is macOS 10.15+; treat older OSes as "assume granted" (true).
    if #available(macOS 10.15, *) {
        preflight = CGPreflightScreenCaptureAccess()
    }
    let normals = normalWindowCount()
    // Trust the EMPIRICAL probe: a grant means normal windows are enumerable. `preflight` is reported
    // too for diagnostics, but `screen_recording` is the ground truth the server gates on.
    let screen = preflight && normals > 0
    let ax = AXIsProcessTrusted()
    emit(["screen_recording": screen, "accessibility": ax,
          "preflight": preflight, "normal_windows": normals])
}

// ---- window lookup ----------------------------------------------------------
// Owner name (kCGWindowOwnerName) is available WITHOUT Screen Recording; only window TITLES and
// captured PIXELS require the grant — so a missing grant still lets us find + click the window, and
// the screenshot (not this helper) is where the loud failure surfaces. We pick the largest on-screen,
// non-zero-layer window owned by the target app (the game view, not a tiny helper/status window).
//
// #1443: Mission Control SPACES blind spot. `.optionOnScreenOnly` only enumerates windows on the
// CURRENT Space — if WorldOSPlayer opened on (or got dragged to) a different Space, the on-screen
// pass returns nothing even though the window is very much alive, and the T3 gate silently died
// (winfind found:false -> every screenshot/click no-ops). Fix: try on-screen-only FIRST (cheap, and
// the common case), and if the owner isn't in that list, fall back to a search WITHOUT
// .optionOnScreenOnly (every Space). The result carries "on_screen" so callers know which capture
// path applies: #1456 makes ScreenCaptureKit (`capture`) the PRIMARY path — it images a window on
// ANY Space without activation, so on_screen:false is no longer a dead end — and `screencapture -l`
// (which macOS 15+ refuses off the current Space) is only a fallback for on_screen:true windows.
// Either way callers NEVER activate the owner: QA must not steal the user's focus or switch Spaces.
func winFind(_ owner: String) {
    func listWithOwner(_ opts: CGWindowListOption) -> [[String: Any]]? {
        guard let raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { return nil }
        return raw.contains { ($0[kCGWindowOwnerName as String] as? String) == owner } ? raw : nil
    }
    let onScreenOpts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    let allSpacesOpts: CGWindowListOption = [.excludeDesktopElements]
    var onScreen = true
    guard let infoList = listWithOwner(onScreenOpts) ?? { onScreen = false; return listWithOwner(allSpacesOpts) }() else {
        emit(["found": false, "error": "window-list-unavailable"]); exit(2)
    }
    var best: [String: Any]? = nil
    var bestArea: CGFloat = -1
    for win in infoList {
        guard let o = win[kCGWindowOwnerName as String] as? String, o == owner else { continue }
        // layer 0 == normal app windows; skip menubar/overlay layers.
        if let layer = win[kCGWindowLayer as String] as? Int, layer != 0 { continue }
        guard let b = win[kCGWindowBounds as String] as? [String: Any],
              let w = b["Width"] as? CGFloat, let h = b["Height"] as? CGFloat else { continue }
        let area = w * h
        if area > bestArea { bestArea = area; best = win }
    }
    guard let win = best,
          let b = win[kCGWindowBounds as String] as? [String: Any],
          let x = b["X"] as? CGFloat, let y = b["Y"] as? CGFloat,
          let w = b["Width"] as? CGFloat, let h = b["Height"] as? CGFloat,
          let wid = win[kCGWindowNumber as String] as? Int else {
        emit(["found": false, "owner": owner]); exit(2)
    }
    emit([
        "found": true, "window_id": wid,
        "x": Int(x), "y": Int(y), "w": Int(w), "h": Int(h),
        "owner": owner, "title": (win[kCGWindowName as String] as? String) ?? "",
        "on_screen": onScreen,
    ])
}

// ---- ScreenCaptureKit capture (#1456) --------------------------------------
// Image the owner's largest window to a PNG via SCScreenshotManager. Unlike `screencapture -l`,
// SCK rasterizes a window on ANY Space with NO activation — the whole point of #1456: player QA
// must never steal the user's focus or switch Spaces. Needs the Screen-Recording grant (SCK
// enforces it) and macOS 14+ (SCScreenshotManager.captureImage / SCContentFilter.pointPixelScale);
// on an older OS we emit ok:false so the JS caller falls back to `screencapture -l`.
func writePNG(_ image: CGImage, _ path: String) -> Bool {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let dest = CGImageDestinationCreateWithURL(url, "public.png" as CFString, 1, nil) else { return false }
    CGImageDestinationAddImage(dest, image, nil)
    return CGImageDestinationFinalize(dest)
}

func captureWindowSCK(_ owner: String, _ outPath: String) {
#if canImport(ScreenCaptureKit)
    if #available(macOS 14.0, *) {
        // Force the CoreGraphics/WindowServer connection to initialize on the MAIN thread first.
        // SCK's SCShareableContent/SCContentFilter otherwise trip `CGS_REQUIRE_INIT` when their
        // first CGS call happens on the background Task thread (observed in this headless CLI).
        _ = CGMainDisplayID()
        _ = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID)
        let sem = DispatchSemaphore(value: 0)
        var out: [String: Any] = ["ok": false, "error": "sck-init"]
        Task {
            defer { sem.signal() }
            do {
                // onScreenWindowsOnly:false so windows on OTHER Spaces are enumerated too.
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                let matches = content.windows.filter {
                    ($0.owningApplication?.applicationName == owner) && $0.frame.width > 1 && $0.frame.height > 1
                }
                guard let win = matches.max(by: { ($0.frame.width * $0.frame.height) < ($1.frame.width * $1.frame.height) }) else {
                    out = ["ok": false, "error": "window-not-found", "owner": owner]; return
                }
                let filter = SCContentFilter(desktopIndependentWindow: win)
                let cfg = SCStreamConfiguration()
                let scale = filter.pointPixelScale                 // px per point (2 on Retina)
                let rect = filter.contentRect                       // window content rect, points
                cfg.width = Int((rect.width * CGFloat(scale)).rounded())
                cfg.height = Int((rect.height * CGFloat(scale)).rounded())
                cfg.showsCursor = false
                let img = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                guard writePNG(img, outPath) else { out = ["ok": false, "error": "png-write-failed"]; return }
                let f = win.frame
                out = [
                    "ok": true, "window_id": Int(win.windowID),
                    "x": Int(f.origin.x), "y": Int(f.origin.y), "w": Int(f.width), "h": Int(f.height),
                    "px_w": img.width, "px_h": img.height, "scale": Double(scale),
                    "on_screen": win.isOnScreen, "owner": owner, "path": outPath,
                ]
            } catch {
                out = ["ok": false, "error": "sck-capture-failed: \(error)"]
            }
        }
        sem.wait()
        emit(out)
        exit((out["ok"] as? Bool) == true ? 0 : 2)
    }
#endif
    emit(["ok": false, "error": "screencapturekit-unavailable (needs macOS 14+)", "owner": owner])
    exit(2)
}

// ---- synthetic input --------------------------------------------------------
// #1466: how a synthesized CGEvent is delivered. `.pid` posts DIRECTLY to a resolved process
// (CGEvent.postToPid) so an UNFOCUSED, off-current-Space app still receives it — the no-activation
// input twin of the SCK capture. `.hid` is the legacy global HID-tap (only routed to the active app).
enum Delivery {
    case hid
    case pid(pid_t)
    var label: String { switch self { case .hid: return "hid"; case .pid: return "pid" } }
    func post(_ ev: CGEvent?) {
        guard let ev = ev else { return }
        switch self {
        case .hid: ev.post(tap: .cghidEventTap)
        case .pid(let p): ev.postToPid(p)
        }
    }
}

// Resolve the owner app's PID the SAME way winfind picks its window: the largest layer-0 window owned
// by `owner`, on the current Space first (cheap, common case) then any Space. Returns nil if the owner
// has no such window (caller then keeps the HID path). Owner name + PID are readable WITHOUT the
// Screen-Recording grant, so this never depends on a capture permission.
func ownerPID(_ owner: String) -> pid_t? {
    func pick(_ opts: CGWindowListOption) -> pid_t? {
        guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { return nil }
        var best: pid_t? = nil
        var bestArea: CGFloat = -1
        for w in list {
            guard let o = w[kCGWindowOwnerName as String] as? String, o == owner else { continue }
            if let layer = w[kCGWindowLayer as String] as? Int, layer != 0 { continue }
            guard let b = w[kCGWindowBounds as String] as? [String: Any],
                  let ww = b["Width"] as? CGFloat, let hh = b["Height"] as? CGFloat,
                  let pidNum = w[kCGWindowOwnerPID as String] as? Int else { continue }
            let area = ww * hh
            if area > bestArea { bestArea = area; best = pid_t(pidNum) }
        }
        return best
    }
    return pick([.optionOnScreenOnly, .excludeDesktopElements]) ?? pick([.excludeDesktopElements])
}

// #1466 fallback (opt-in): briefly activate the owner, run `body` (an HID post), then restore the
// previously-frontmost app. Sub-second, CURRENT Space only — never a Space switch. Returns true if it
// could actually run (AppKit + a resolvable owner app); false lets the caller keep the plain path.
func withBriefActivation(_ owner: String, _ body: () -> Void) -> Bool {
#if canImport(AppKit)
    let apps = NSWorkspace.shared.runningApplications
    guard let target = apps.first(where: { $0.localizedName == owner }) else { return false }
    let prev = NSWorkspace.shared.frontmostApplication
    target.activate(options: [])
    usleep(120_000)          // let the activation settle before the click routes
    body()
    usleep(30_000)
    if let prev = prev, prev.processIdentifier != target.processIdentifier { prev.activate(options: []) }
    return true
#else
    return false
#endif
}

// Choose delivery: an owner with a resolvable PID -> direct PID post; else HID. The activate-fallback
// is handled by the caller (it wraps an HID post), so here we only pick pid-vs-hid.
func deliveryFor(_ owner: String?) -> Delivery {
    if let owner = owner, !owner.isEmpty, let pid = ownerPID(owner) { return .pid(pid) }
    return .hid
}

func postClick(_ gx: Double, _ gy: Double, doubleClick: Bool, owner: String?, activateFallback: Bool) {
    let pt = CGPoint(x: gx, y: gy)
    let src = CGEventSource(stateID: .hidSystemState)
    func sequence(_ delivery: Delivery) {
        // move first so hover/enter handlers see the cursor, then down/up.
        delivery.post(CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left))
        func clickOnce(_ count: Int64) {
            let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left)
            let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left)
            if count > 1 { down?.setIntegerValueField(.mouseEventClickState, value: count); up?.setIntegerValueField(.mouseEventClickState, value: count) }
            delivery.post(down)
            delivery.post(up)
        }
        clickOnce(1)
        if doubleClick { clickOnce(2) }
    }
    if activateFallback, let owner = owner, withBriefActivation(owner, { sequence(.hid) }) {
        emit(["ok": true, "delivery": "activate", "owner": owner]); return
    }
    let delivery = deliveryFor(owner)
    sequence(delivery)
    emit(["ok": true, "delivery": delivery.label, "owner": owner ?? ""])
}

// Named virtual keycodes (US layout) for the keys the palette contract uses. Anything not here that is
// a single character is typed via the unicode path.
let NAMED_KEYS: [String: CGKeyCode] = [
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "left": 123, "arrowleft": 123, "right": 124, "arrowright": 124,
    "down": 125, "arrowdown": 125, "up": 126, "arrowup": 126,
]

func postKey(_ name: String, owner: String?, activateFallback: Bool) {
    let src = CGEventSource(stateID: .hidSystemState)
    let key = name.lowercased()
    guard let code = NAMED_KEYS[key] else {
        // single unnamed char -> unicode keystroke (same owner/PID delivery)
        postType(name, owner: owner, activateFallback: activateFallback); return
    }
    func sequence(_ delivery: Delivery) {
        delivery.post(CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true))
        delivery.post(CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false))
    }
    if activateFallback, let owner = owner, withBriefActivation(owner, { sequence(.hid) }) {
        emit(["ok": true, "delivery": "activate", "owner": owner]); return
    }
    let delivery = deliveryFor(owner)
    sequence(delivery)
    emit(["ok": true, "delivery": delivery.label, "owner": owner ?? ""])
}

func postType(_ text: String, owner: String?, activateFallback: Bool) {
    let src = CGEventSource(stateID: .hidSystemState)
    var chars = Array(text.utf16)
    func sequence(_ delivery: Delivery) {
        let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
        let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
        down?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        up?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        delivery.post(down)
        delivery.post(up)
    }
    if activateFallback, let owner = owner, withBriefActivation(owner, { sequence(.hid) }) {
        emit(["ok": true, "delivery": "activate", "owner": owner]); return
    }
    let delivery = deliveryFor(owner)
    sequence(delivery)
    emit(["ok": true, "delivery": delivery.label, "owner": owner ?? ""])
}

// ---- dispatch ---------------------------------------------------------------
// #1466: click/key/type accept two optional trailing flags — `--owner NAME` (PID-target delivery to
// the unfocused player) and `--activate-fallback` (opt-in brief activate->post->restore). Strip them
// out first so the positional parsing below is unchanged for callers that don't pass them.
var rawArgs = Array(CommandLine.arguments.dropFirst())
var optOwner: String? = nil
var optActivateFallback = false
do {
    var kept: [String] = []
    var i = 0
    while i < rawArgs.count {
        let a = rawArgs[i]
        if a == "--owner", i + 1 < rawArgs.count { optOwner = rawArgs[i + 1]; i += 2; continue }
        if a == "--activate-fallback" { optActivateFallback = true; i += 1; continue }
        kept.append(a); i += 1
    }
    rawArgs = kept
}
let args = rawArgs
guard let cmd = args.first else {
    emit(["ok": false, "error": "usage: native_input <checkperms|winfind|click|key|type> …"]); exit(2)
}
switch cmd {
case "checkperms":
    checkPerms()
case "winfind":
    guard args.count >= 2 else { emit(["found": false, "error": "usage: winfind <ownerName>"]); exit(2) }
    winFind(args[1])
case "capture":
    guard args.count >= 3 else { emit(["ok": false, "error": "usage: capture <ownerName> <outPath>"]); exit(2) }
    captureWindowSCK(args[1], args[2])
case "click":
    guard args.count >= 3, let gx = Double(args[1]), let gy = Double(args[2]) else {
        emit(["ok": false, "error": "usage: click <globalX> <globalY> [double] [--owner NAME] [--activate-fallback]"]); exit(2)
    }
    postClick(gx, gy, doubleClick: args.count >= 4 && args[3] == "double", owner: optOwner, activateFallback: optActivateFallback)
case "key":
    guard args.count >= 2 else { emit(["ok": false, "error": "usage: key <name> [--owner NAME] [--activate-fallback]"]); exit(2) }
    postKey(args[1], owner: optOwner, activateFallback: optActivateFallback)
case "type":
    postType(args.dropFirst().joined(separator: " "), owner: optOwner, activateFallback: optActivateFallback)
default:
    emit(["ok": false, "error": "unknown subcommand: \(cmd)"]); exit(2)
}
