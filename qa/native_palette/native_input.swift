// native_input.swift — the macOS PRIMITIVES behind the T3 native-window palette (issue #1436/#1322).
//
// The browser palette (qa/playwright/palette_server.js) drives a Playwright page. The T3 gate needs
// the SAME blind-player tool surface pointed at the NATIVE WorldOSPlayer.app window instead. This
// helper is the thin CoreGraphics/Quartz layer that the native palette server shells out to for the
// three things a browser gives you for free: FIND the player's window, CLICK/press inside it, and
// PREFLIGHT the macOS TCC permissions those need. Screenshots are taken by /usr/sbin/screencapture
// (see the server) — this helper never captures pixels, so it needs no Screen-Recording grant itself.
//
// It is deliberately a SINGLE self-contained file with no third-party deps so it both:
//   - runs interpreted:  `swift native_input.swift <subcommand> …`   (no build step; what the server uses)
//   - compiles:          `swiftc native_input.swift -o native_input`  (the harness self-check asserts this)
//
// Subcommands (each prints ONE line of JSON to stdout; exit 0 on success, 2 on a usage/lookup miss):
//   checkperms                      -> {"screen_recording":bool,"accessibility":bool}
//   winfind  <ownerName>            -> {"found":bool,"window_id":int,"x":n,"y":n,"w":n,"h":n,"owner":s,"title":s}
//   click    <globalX> <globalY> [double]   -> {"ok":true}   (left click at GLOBAL screen points, top-left origin)
//   key      <name>                 -> {"ok":true}   (Return/Escape/Tab/Space/Arrow*/Delete or a single char)
//   type     <text…>                -> {"ok":true}   (synthesize a unicode string into the focused field)
//
// Coordinate contract: Quartz CGWindow bounds AND CGEvent cursor positions are BOTH global screen
// POINTS with a top-left origin, so they compose directly. The server maps window-relative screenshot
// PIXELS to global points via the window origin + the backing scale (pixel_w / point_w) before calling
// `click` — this helper only ever sees final global points and never guesses scale.

import Foundation
import CoreGraphics
#if canImport(ApplicationServices)
import ApplicationServices
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
// .optionOnScreenOnly (every Space). The result carries "on_screen" so callers (the palette server's
// screencaptureWindow) know whether `screencapture -l <id>` will actually work from HERE: macOS 15
// refuses to rasterize a window that isn't on the current Space, so a caller that sees
// on_screen:false must activate the owner (switch to its Space) before capturing, or fall back to a
// full-screen grab + crop.
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

// ---- synthetic input --------------------------------------------------------
func postClick(_ gx: Double, _ gy: Double, doubleClick: Bool) {
    let pt = CGPoint(x: gx, y: gy)
    let src = CGEventSource(stateID: .hidSystemState)
    // move first so hover/enter handlers see the cursor, then down/up.
    CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
    func clickOnce(_ count: Int64) {
        let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left)
        let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left)
        if count > 1 { down?.setIntegerValueField(.mouseEventClickState, value: count); up?.setIntegerValueField(.mouseEventClickState, value: count) }
        down?.post(tap: .cghidEventTap)
        up?.post(tap: .cghidEventTap)
    }
    clickOnce(1)
    if doubleClick { clickOnce(2) }
    emit(["ok": true])
}

// Named virtual keycodes (US layout) for the keys the palette contract uses. Anything not here that is
// a single character is typed via the unicode path.
let NAMED_KEYS: [String: CGKeyCode] = [
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "left": 123, "arrowleft": 123, "right": 124, "arrowright": 124,
    "down": 125, "arrowdown": 125, "up": 126, "arrowup": 126,
]

func postKey(_ name: String) {
    let src = CGEventSource(stateID: .hidSystemState)
    let key = name.lowercased()
    if let code = NAMED_KEYS[key] {
        CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)?.post(tap: .cghidEventTap)
        CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)?.post(tap: .cghidEventTap)
        emit(["ok": true]); return
    }
    // single unnamed char -> unicode keystroke
    postType(name)
}

func postType(_ text: String) {
    let src = CGEventSource(stateID: .hidSystemState)
    let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
    let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
    var chars = Array(text.utf16)
    down?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
    up?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
    down?.post(tap: .cghidEventTap)
    up?.post(tap: .cghidEventTap)
    emit(["ok": true])
}

// ---- dispatch ---------------------------------------------------------------
let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else {
    emit(["ok": false, "error": "usage: native_input <checkperms|winfind|click|key|type> …"]); exit(2)
}
switch cmd {
case "checkperms":
    checkPerms()
case "winfind":
    guard args.count >= 2 else { emit(["found": false, "error": "usage: winfind <ownerName>"]); exit(2) }
    winFind(args[1])
case "click":
    guard args.count >= 3, let gx = Double(args[1]), let gy = Double(args[2]) else {
        emit(["ok": false, "error": "usage: click <globalX> <globalY> [double]"]); exit(2)
    }
    postClick(gx, gy, doubleClick: args.count >= 4 && args[3] == "double")
case "key":
    guard args.count >= 2 else { emit(["ok": false, "error": "usage: key <name>"]); exit(2) }
    postKey(args[1])
case "type":
    postType(args.dropFirst().joined(separator: " "))
default:
    emit(["ok": false, "error": "unknown subcommand: \(cmd)"]); exit(2)
}
