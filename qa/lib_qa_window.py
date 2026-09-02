#!/usr/bin/env python3
"""READ-ONLY macOS window sensors for the QA sandbox lane (#1672 / incident 2026-09-02).

Why this exists: `qa/qa_sandbox.py` launches a SECOND instance of the owner's player. On
2026-09-02 it came up FULLSCREEN on the developer's only display and took over the machine.
The launch-side fix is Unity's own `-screen-fullscreen 0 -screen-width/-screen-height`, but a
flag that is ignored (a future Unity, a bad build, a plist regression) must not be able to lock
the machine again. This module is the INDEPENDENT sensor that proves the window really is
windowed and really is on the display, from the window server's own bounds — not from the
player's self-reported /health.

Hard design rules, all load-bearing:
  * READ ONLY. Nothing here moves, resizes, minimizes, hides, activates or quits a window.
  * NO Accessibility (AX) / `System Events` window scripting. AX availability here is
    NONDETERMINISTIC (TCC attributes the grant to the responsible parent app: the same probe
    returned `-1719 not allowed assistive access` from one launch context and `83, 30` rc=0 from
    another), and `ProjectSettings.asset:103 resizableWindow = 0` makes AXSize unsettable anyway.
    Sizing comes from the CLI flags; this module only VERIFIES.
  * NO pyobjc (`import Quartz` is ModuleNotFoundError on this host's system python3) — ctypes
    against CoreGraphics/CoreFoundation only.
  * `CGWindowListCopyWindowInfo` needs NO TCC grant for bounds/pid/layer. Only kCGWindowName is
    withheld without Screen Recording, so every check here keys on BOUNDS, never on a title.
  * Identity is always a PID we started. Never a process name — the owner's live game is the same
    executable with the same bundle id.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import time

# fraction of the main display's area at or above which a window counts as a takeover
FULLSCREEN_COVER_FRAC = float(os.environ.get("WORLDOS_QA_MAX_COVER_FRAC", "0.85"))
# a window must keep at least this much of itself on the display, or ScreenCapture can hand back
# black/stale frames with no HTTP error (runInBackground governs the update loop, not presentation)
MIN_ONSCREEN_W, MIN_ONSCREEN_H = 220, 120

kCGWindowListOptionOnScreenOnly = 1 << 0
kCGWindowListExcludeDesktopElements = 1 << 4
kCFStringEncodingUTF8 = 0x08000100
kCFNumberDoubleType = 13


def _load():
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
    cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
    cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
    return cf, cg


try:                                   # a non-macOS/CI host must import cleanly, not explode
    _CF, _CG = _load()
except Exception:                      # noqa: BLE001
    _CF = _CG = None


def _cfstr(py: str):
    return _CF.CFStringCreateWithCString(None, py.encode("utf-8"), kCFStringEncodingUTF8)


def _to_str(ref) -> str:
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(1024)
    if _CF.CFStringGetCString(ref, buf, 1024, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8", "replace")
    return ""


def _to_num(ref) -> float:
    if not ref:
        return 0.0
    out = ctypes.c_double(0.0)
    _CF.CFNumberGetValue(ref, kCFNumberDoubleType, ctypes.byref(out))
    return float(out.value)


def main_display_points() -> tuple:
    """(width, height) of the MAIN display in POINTS (the current mode's logical size).

    On this host: (1492, 817) — one display, "Screen Sharing Virtual Display", 2984x1634 backing.
    Returns (0, 0) when CoreGraphics is unavailable; every caller treats that as "cannot judge".
    """
    if _CG is None:
        return (0, 0)
    try:
        did = _CG.CGMainDisplayID()
        return (int(_CG.CGDisplayPixelsWide(did)), int(_CG.CGDisplayPixelsHigh(did)))
    except Exception:  # noqa: BLE001
        return (0, 0)


def cg_windows(pid=None, owner=None) -> list:
    """On-screen windows as {pid, owner, layer, name, bounds:{X,Y,Width,Height}}.

    Needs NO TCC grant for pid/layer/bounds. `name` is often "" without Screen Recording — that is
    expected, and is exactly why nothing in this module keys on a window title.
    """
    if _CF is None or _CG is None:
        return []
    opt = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    arr = _CG.CGWindowListCopyWindowInfo(opt, 0)
    if not arr:
        return []
    keys = [_cfstr(k) for k in ("kCGWindowOwnerPID", "kCGWindowOwnerName", "kCGWindowLayer",
                                "kCGWindowName", "kCGWindowBounds", "X", "Y", "Width", "Height")]
    k_pid, k_owner, k_layer, k_name, k_bounds, k_x, k_y, k_w, k_h = keys
    out: list = []
    try:
        for i in range(_CF.CFArrayGetCount(arr)):
            d = _CF.CFArrayGetValueAtIndex(arr, i)
            wpid = int(_to_num(_CF.CFDictionaryGetValue(d, k_pid)))
            wown = _to_str(_CF.CFDictionaryGetValue(d, k_owner))
            if pid is not None and wpid != pid:
                continue
            if owner is not None and wown != owner:
                continue
            b = _CF.CFDictionaryGetValue(d, k_bounds)
            bounds = {"X": _to_num(_CF.CFDictionaryGetValue(b, k_x)) if b else 0.0,
                      "Y": _to_num(_CF.CFDictionaryGetValue(b, k_y)) if b else 0.0,
                      "Width": _to_num(_CF.CFDictionaryGetValue(b, k_w)) if b else 0.0,
                      "Height": _to_num(_CF.CFDictionaryGetValue(b, k_h)) if b else 0.0}
            out.append({"pid": wpid, "owner": wown,
                        "layer": int(_to_num(_CF.CFDictionaryGetValue(d, k_layer))),
                        "name": _to_str(_CF.CFDictionaryGetValue(d, k_name)), "bounds": bounds})
    finally:
        for ref in [arr, *keys]:
            try:
                _CF.CFRelease(ref)
            except Exception:  # noqa: BLE001
                pass
    return out


def _content_windows(pid: int) -> list:
    return [w for w in cg_windows(pid=pid) if w.get("layer", 0) == 0]


def _descendants(pid: int) -> list:
    seen, todo = [pid], [pid]
    while todo:
        cur = todo.pop()
        try:
            kids = subprocess.run(["pgrep", "-P", str(cur)], capture_output=True, text=True,
                                  timeout=5).stdout.split()
        except Exception:  # noqa: BLE001
            kids = []
        for k in kids:
            if k.isdigit() and int(k) not in seen:
                seen.append(int(k))
                todo.append(int(k))
    return seen


def resolve_player_pid(popen_pid: int, exe: str = "WorldOSPlayer", timeout: float = 8.0) -> int:
    """Defensive fallback: find `exe` among popen_pid's descendants.

    With the current launch shape the Popen pid IS the Unity process (caffeinate is a SIBLING with
    `-w <pid>`, not the parent), so this normally returns popen_pid unchanged. It stays for the case
    where some future caller wraps the binary in something.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for pid in _descendants(popen_pid):
            try:
                comm = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                      capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:  # noqa: BLE001
                continue
            if comm.rsplit("/", 1)[-1] == exe:
                return pid
        time.sleep(0.5)
    return popen_pid


def wait_for_window(pid: int, timeout: float = 25.0) -> list:
    """Poll until `pid` owns a real content window (layer 0, > 200x200 points). [] on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = [w for w in _content_windows(pid)
                if w["bounds"]["Width"] > 200 and w["bounds"]["Height"] > 200]
        if wins:
            return wins
        time.sleep(0.5)
    return []


def fullscreen_verdict(pid: int, cover_frac=None):
    """The offending window if `pid` covers >= cover_frac of the main display, else None."""
    frac = FULLSCREEN_COVER_FRAC if cover_frac is None else cover_frac
    dw, dh = main_display_points()
    if dw <= 0 or dh <= 0:
        return None
    for w in _content_windows(pid):
        b = w["bounds"]
        if b["Width"] * b["Height"] >= frac * dw * dh:
            return w
    return None


def offscreen_verdict(pid: int, min_w: int = MIN_ONSCREEN_W, min_h: int = MIN_ONSCREEN_H):
    """The offending window if it does not keep >= min_w x min_h points ON the main display.

    Offscreen is as disqualifying as fullscreen: a window with no on-screen surface hands
    ScreenCapture a black or frozen backbuffer and /shot still returns 200, so walk_test reads
    "the actor never moved" instead of "the harness is broken".
    """
    dw, dh = main_display_points()
    if dw <= 0 or dh <= 0:
        return None
    for w in _content_windows(pid):
        b = w["bounds"]
        ix = max(0.0, min(b["X"] + b["Width"], float(dw)) - max(b["X"], 0.0))
        iy = max(0.0, min(b["Y"] + b["Height"], float(dh)) - max(b["Y"], 0.0))
        if ix < min_w or iy < min_h:
            return w
    return None


def front_app_name():
    """Name of the frontmost app, or None. Uses `System Events` process frontmost (no AX grant)."""
    try:
        p = subprocess.run(
            ["/usr/bin/osascript", "-e",
             'tell application "System Events" to get name of first application process '
             'whose frontmost is true'],
            capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    name = p.stdout.strip()
    return name if p.returncode == 0 and name else None


def restore_front(name) -> bool:
    """Re-front `name` via Launch Services (`open -a`). No TCC, no AX, best-effort."""
    if not name:
        return False
    try:
        return subprocess.run(["/usr/bin/open", "-a", name],
                              capture_output=True, text=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":   # tiny read-only probe: `python3 qa/lib_qa_window.py [pid]`
    import json
    import sys
    _pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps({"display_points": main_display_points(),
                      "windows": cg_windows(pid=_pid, owner=None if _pid else "WorldOSPlayer")},
                     indent=2))
