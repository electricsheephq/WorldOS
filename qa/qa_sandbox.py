#!/usr/bin/env python3
"""Sandboxed QA lane — full walk sweeps WITHOUT driving the owner's live campaign (#1596).

Epic #1581, red-team finding 2 (adopted): `walk_test.py` drives a player over its QA channel, and the
owner's player is bound to their LIVE campaign — a full sweep appends hundreds of real moves to the
owner's session, and the scale loop (#1588) can't hammer the owner's player at all. This module
provisions a disposable, fully parallel stack:

  cloned state dir (fresh seed)  →  second engine/viewer on :8866  →  second player instance
  (same .app binary, own WORLDOS_QA_INPUT_PORT :8972)  →  run gates  →  teardown (kill OWN pids only)

POLICY (runbook): small probe sets on the owner instance are acceptable (return the party after);
FULL sweeps + all scale-loop gating run here. The owner's campaign is not a test rig.

Launch pattern mirrors qa/player_smoke.sh:145-187 (direct binary exec + env), with two deliberate
differences: (1) NO `osascript quit app "WorldOSPlayer"` — that kills EVERY instance including the
owner's; teardown signals only the PIDs this module started. (2) The engine runs through
`uv run --directory servers/engine` so it gets the real venv (the plist pattern).

QA RIG vs THE OWNER'S GAME (#1672, incident 2026-09-02) — read this before changing the launch:
- The rig is launched WINDOWED and never fullscreen: `-screen-fullscreen 0 -screen-width W
  -screen-height H` (Unity standalone args, honored before the app's own window setup). This lane
  was the ONLY player launch site that skipped the #1456 windowed contract in
  qa/lib_native_player_boot.sh:81-88, so it inherited the SHARED plist's "Screenmanager Fullscreen
  mode"=1 + "Resolution Use Native"=1 and took over the developer's only display.
- An owner-active guard (ioreg HIDIdleTime, FORCE_PLAYER_QA=1 override, exit 75) refuses to launch
  while someone is using the Mac.
- A permission-free CGWindowList watchdog (qa/lib_qa_window.py) proves from the window server's own
  BOUNDS — not from the player's /health — that the window is neither fullscreen nor offscreen, and
  tears the run down within ~25s if it is.
- Bundle id is SHARED with the owner's game (ProjectSettings.asset:171), so the rig NEVER writes
  ~/Library/Preferences/com.worldos.WorldOSPlayer.plist itself — but UNITY does, on quit. `down`
  snapshots the whole domain, diffs it, and restores ATTRIBUTION-SAFELY: a key is rewritten only if
  it changed AND still holds the exact value this rig deterministically produces. Anything else
  (including the cosmetic window-position keys) is reported and left alone — a blind restore would
  silently revert the owner's own settings.
- CFFIXED_USER_HOME=<rundir>/home gives the rig its OWN Application Support, so /shot frames land in
  the run dir instead of racing the owner's instance over the shared shot directory. Measured
  2026-09-02: it DOES redirect Application.persistentDataPath; it does NOT redirect PlayerPrefs
  (~/Library/Preferences is still shared) — hence the restore logic above.
- TELLING THEM APART TODAY: window geometry, `lsof -nP -iTCP:8972 -sTCP:LISTEN -t` (owner = 8971),
  and sandbox.json. The rig's window TITLE is still "WorldOSPlayer" — the #1672 badge needs the
  Phase 2 C# change (docs/qa/QA-RIG-WINDOW-BADGE.md) and an Editor rebuild.

Caveats:
- The two instances no longer share a shot directory (CFFIXED_USER_HOME, above), but they DO share
  the preferences domain and the GPU. The owner-active guard is a courtesy, NOT an interlock.
- 16 GB host: ONE sandbox at a time; always teardown (the overnight-loop RAM discipline).

Usage:
  qa/qa_sandbox.py up   --run scale1            # seed + engine + player; prints the endpoints
  qa/qa_sandbox.py status --run scale1
  qa/qa_sandbox.py down --run scale1            # kill own pids; state/logs kept for evidence
  qa/qa_sandbox.py watchdog --run scale1        # ONE takeover check; kills + exits 3 if fullscreen
  qa/qa_sandbox.py orphans                      # rig leftovers on :8972 / stray player windows
  # or via the orchestrator:  qa/room_pipeline.py --room crypt --sandbox
Custom seed (new rooms): --seed-cmd "uv run --directory servers/engine python qa/my_seed.py {state}"
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_qa_window as LQW  # noqa: E402  (read-only CGWindowList sensors; see its module docstring)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = Path(os.environ.get("WORLDOS_QA_SANDBOX_ROOT", "/tmp/worldos-qa-sandbox"))
DEFAULT_APP = Path(os.environ.get("WORLDOS_PLAYER_APP",
                                  str(Path.home() / "Applications" / "WorldOSPlayer.app")))
DEFAULT_CAMPAIGN = "registered_world_v1"   # what qa/seed_gfx_registered_world.py seeds
ENGINE_PORT, QA_PORT = 8866, 8972          # owner runs 8766/8971 — never collide

# ── #1672 / incident 2026-09-02: WINDOWED, non-hijacking launch ────────────────────────────────────
# Same env NAMES as the shell helper (qa/lib_native_player_boot.sh:83-85) so all three player lanes
# are one knob. These are BACKING PIXELS (a Unity resolution), not points: 1280x700 rendered a
# 640x382 POINT window on the measured 2x host, and /health reported 1280x700. The value is chosen
# for the VISUAL stage, not for fit — 1280x700 is aspect 1.829, >= this display's 1.826, so the ortho
# crop (walk_test.world_to_window_px) shows at least as much world as the fullscreen baseline and no
# sample cell that was in frame falls out. WIN_MIN_* is the floor below which a grid cell shrinks
# under the pixel-diff threshold.
WIN_W, WIN_H = 1280, 700
WIN_MIN_W, WIN_MIN_H = 640, 400
CAFFEINATE_FLAGS = os.environ.get("WORLDOS_QA_CAFFEINATE_FLAGS", "-is").split()
OWNER_ACTIVE_RC = 75                      # parity with PLAYER_QA_OWNER_ACTIVE_RC (shell lane)
WATCHDOG_RC = 3                           # the rig had the display; killed and failed CLOSED
# /health reports the player's RESOLUTION (backing pixels). At or above this fraction of the
# display's backing-pixel area the rig is effectively taking the screen, whatever it was asked for.
MAX_SCREEN_COVER_FRAC = float(os.environ.get("WORLDOS_QA_MAX_SCREEN_FRAC", "0.6"))
PLIST_DOMAIN = "com.worldos.WorldOSPlayer"    # SHARED with the owner's game (same bundle id)
# Keys that CHURN on every launch — excluded from the leak diff or every run looks like a leak.
CHURN_KEYS = ("unity.player_session_count", "unity.player_sessionid")
CHURN_PREFIXES = ("unity_connect.",)
# Reported by the leak check, NEVER rewritten: the owner drags the window and Unity records it, so a
# change here is as likely theirs as ours and it costs them nothing to keep.
NEVER_RESTORE = ("Screenmanager Window Position X", "Screenmanager Window Position Y")
_INT_RE = re.compile(r"-?\d+")


# ── owner-active guard (Python port of qa/lib_native_player_boot.sh:51-71) ─────────────────────────
def owner_active_guard() -> None:
    """Refuse to launch while the owner is using the Mac. SystemExit(75) when they are.

    HIDIdleTime is nanoseconds since the last HID event. A missing/unreadable value is treated as
    idle (proceed) — headless-box parity with the shell lane, which must not block on a box with no
    console user.
    """
    if os.environ.get("FORCE_PLAYER_QA", "0") == "1":
        print("[guard] FORCE_PLAYER_QA=1 — bypassing owner-active guard.", file=sys.stderr)
        return
    try:
        threshold = int(os.environ.get("WORLDOS_PLAYER_IDLE_THRESHOLD") or 120)
    except ValueError:
        threshold = 120
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=20).stdout or ""
    except Exception:  # noqa: BLE001
        out = ""
    idle_ns = None
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            tok = line.split()[-1].strip()
            if tok.isdigit():
                idle_ns = int(tok)
            break
    if idle_ns is None:
        print("[guard] WARN: could not read HIDIdleTime — proceeding (assume idle).", file=sys.stderr)
        return
    idle_s = idle_ns // 1_000_000_000
    if idle_s < threshold:
        print(f"SANDBOX-DEFERRED (owner active): last input {idle_s}s ago (< {threshold}s). "
              f"Set FORCE_PLAYER_QA=1 to override.", file=sys.stderr)
        raise SystemExit(OWNER_ACTIVE_RC)
    print(f"[guard] owner idle {idle_s}s (>= {threshold}s) — OK to launch the sandbox player.",
          file=sys.stderr)


def _win_size_from_env() -> tuple:
    raw_w = str(os.environ.get("WORLDOS_PLAYER_WIN_W") or WIN_W)
    raw_h = str(os.environ.get("WORLDOS_PLAYER_WIN_H") or WIN_H)
    if not (raw_w.isdigit() and raw_h.isdigit()):
        raise SystemExit(f"[sandbox] WORLDOS_PLAYER_WIN_W/H must be integers "
                         f"(got {raw_w!r}/{raw_h!r})")
    w, h = int(raw_w), int(raw_h)
    if w < WIN_MIN_W or h < WIN_MIN_H:
        raise SystemExit(f"[sandbox] window {w}x{h} is below the {WIN_MIN_W}x{WIN_MIN_H} floor — a "
                         f"tiny window shrinks a grid cell below the pixel-diff threshold and the "
                         f"visual stage goes false-RED.")
    return w, h


def _player_windowed_args(logfile) -> list:
    """Unity standalone args that force a WINDOWED player at a display-fitted size.

    -screen-fullscreen/-screen-width/-screen-height are Unity's own built-in standalone args, honored
    before the app's own window setup — no C# change and no plist write needed, and they win over
    whatever the SHARED plist holds. -logFile puts THIS run's player log in the run dir instead of
    the shared, overwritten-every-launch default.

    UNITS (measured 2026-09-02, and the reason the first cut of this clamp was wrong): -screen-width
    and -screen-height are a RESOLUTION — BACKING PIXELS — while LQW.main_display_points() and every
    CGWindowList bound are POINTS. Asking for 1280x700 on this 2x display produced a 640x382 POINT
    window and /health reported 1280x700. So the desktop budget is computed in points (menu bar +
    margin, where the window actually has to fit) and converted to pixels with the display's backing
    scale before clamping. Host values: 1512x835 points, 3024x1670 pixels, scale 2.

    The fit clamp keeps the window inside the real desktop while never letting the aspect drop BELOW
    the display's: a narrower window would crop horizontal world out of the ortho frame and cells
    that were in frame at fullscreen would project outside the capture.
    """
    w, h = _win_size_from_env()
    dw, dh = LQW.main_display_points()
    if dw > 0 and dh > 0:
        scale = LQW.backing_scale() or 1.0
        h = min(h, int((dh - 120) * scale))      # point budget -> pixel budget
        w = max(w, round(h * dw / dh))           # aspect is scale-free
        w = min(w, int((dw - 160) * scale))
    args = ["-screen-fullscreen", "0", "-screen-width", str(int(w)), "-screen-height", str(int(h))]
    if logfile:
        args += ["-logFile", str(logfile)]
    return args


# ── shared-plist leak check (#1672 item 3) ─────────────────────────────────────────────────────────
def _is_churn(key: str) -> bool:
    return key in CHURN_KEYS or any(key.startswith(pre) for pre in CHURN_PREFIXES)


def _plist_snapshot() -> dict | None:
    """The WHOLE shared domain as {key: str}, minus the churn keys. READ ONLY.

    Whole-domain, not just the Screenmanager keys: the leak CHECK must be able to report any key the
    rig moved, including one nobody has thought of yet. `defaults export` + plistlib is one read-only
    process (`defaults read <domain> <key>` per key was 11 processes and still could not see an
    unexpected key).
    """
    try:
        p = subprocess.run(["defaults", "export", PLIST_DOMAIN, "-"],
                           capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    try:
        data = plistlib.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in sorted(data.items()) if not _is_churn(str(k))}


def _plist_diff(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {k: {"before": before.get(k), "after": after.get(k)}
            for k in keys if before.get(k) != after.get(k) and not _is_churn(k)}


def _rig_written_values(win_w, win_h) -> dict:
    """Exactly what a rig launch deterministically leaves in the SHARED domain.

    Unity writes these on quit from the `-screen-*` args we passed: mode 3 = FullScreenMode.Windowed,
    the requested resolution, native resolution off. Measured 2026-09-02 (1->3, 3024->1280,
    1670->700, 1->0). Window Position X/Y also moved and are deliberately NOT here — see
    NEVER_RESTORE.
    """
    try:
        w, h = int(win_w), int(win_h)
    except (TypeError, ValueError):
        return {}
    return {"Screenmanager Fullscreen mode": "3",
            "Screenmanager Resolution Width": str(w),
            "Screenmanager Resolution Height": str(h),
            "Screenmanager Resolution Use Native": "0"}


def _plist_restore(diff: dict, *, foreign_alive: bool, rig_values: dict) -> dict:
    """Put back ONLY what this rig provably wrote. Restoring the rest would be its own regression.

    ATTRIBUTION (the round-2 finding): the first cut rewrote every changed key from the snapshot. The
    domain is SHARED, so any key the owner changed while the sweep ran — in their own game, in a
    settings menu, by dragging the window — would be silently reverted by our teardown, and the rig
    would be a a bug for the owner instead of a fix. A key is now restored only when BOTH hold:
      (a) it changed since the up() snapshot, and
      (b) it STILL holds the exact value this rig deterministically writes (rig_values).
    Anything else is reported as skipped, with the reason, and left exactly as found. Values are
    integer-validated before any `defaults write` — a non-integer is reported, never written.

    Ordering constraint: the caller must confirm OUR pids are dead first — a live instance writes on
    quit and cfprefsd caches the domain, so an early restore is silently clobbered. For the same
    reason we refuse to write while a FOREIGN player is alive.
    """
    report = {"restored": {}, "skipped": {}, "note": ""}
    if not diff:
        return report
    if os.environ.get("WORLDOS_QA_PLIST_RESTORE", "1") != "1":
        report["note"] = "WORLDOS_QA_PLIST_RESTORE=0 — detect only; the shared plist was NOT restored."
        report["skipped"] = {k: "restore disabled (WORLDOS_QA_PLIST_RESTORE=0)" for k in diff}
        return report
    if foreign_alive:
        report["note"] = ("a foreign WorldOSPlayer is still running — NOT writing the shared domain "
                          "(cfprefsd would clobber the write when that instance quits). Re-run "
                          "`qa/qa_sandbox.py down --run <run>` once it has exited.")
        report["skipped"] = {k: "a foreign WorldOSPlayer is alive" for k in diff}
        return report
    for key, ba in sorted(diff.items()):
        old, new = ba.get("before"), ba.get("after")
        if key in NEVER_RESTORE:
            report["skipped"][key] = ("cosmetic window position — owner-attributable, never "
                                      "rewritten by the rig")
            continue
        want = rig_values.get(key)
        if want is None:
            report["skipped"][key] = "not a key this rig writes — the change is not ours to undo"
            continue
        if new != want:
            report["skipped"][key] = (f"current value {new!r} is not the value this rig writes "
                                      f"({want!r}) — someone else wrote it last")
            continue
        if old is None:
            cmd, restored = ["defaults", "delete", PLIST_DOMAIN, key], None
        else:
            if not _INT_RE.fullmatch(str(old)):
                report["skipped"][key] = (f"snapshot value {old!r} is not an integer — refusing to "
                                          f"`defaults write -int` it")
                continue
            cmd, restored = ["defaults", "write", PLIST_DOMAIN, key, "-int", str(old)], old
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            report["skipped"][key] = (f"`defaults {cmd[1]}` failed (rc={result.returncode}) — "
                                       "the rig value is STILL in the domain")
        else:
            report["restored"][key] = restored
    return report


def _player_pids(exe: str = "WorldOSPlayer") -> set:
    try:
        p = subprocess.run(["pgrep", "-x", exe], capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return set()
    return {int(t) for t in p.stdout.split() if t.strip().isdigit()}


def _kill_group(pid: int, label: str = "", *, expect_pgid=None) -> str:
    """SIGTERM then SIGKILL the process GROUP. Never terminate() — see the caffeinate note in up().

    IDENTITY (the round-2 finding): a recorded pid is not an identity. Between `up` and `down` the
    rig's player can exit and the OS can hand its number to something else — signalling a whole
    process GROUP on a stale number is how a teardown takes out an unrelated tree. So the pgid
    recorded at launch is re-verified against the live one and a mismatch NEVER signals.
    Returns "" (signalled/already gone) or a short reason string.
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return ""
    except PermissionError:
        print(f"[sandbox] cannot read pgid of {label or 'pid'} {pid}", file=sys.stderr)
        return "no permission to read the process group"
    if expect_pgid is not None and pgid != int(expect_pgid):
        print(f"[sandbox] REFUSING to signal {label or 'pid'} {pid}: its process group is {pgid}, "
              f"we recorded {expect_pgid} — the pid was REUSED and is not ours.", file=sys.stderr)
        return f"pid reused (pgid {pgid} != recorded {expect_pgid})"
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
            time.sleep(1.5)
        except ProcessLookupError:
            return ""
        except PermissionError:
            print(f"[sandbox] cannot signal {label or 'pid'} {pid}", file=sys.stderr)
            return "no permission to signal the process group"
    return ""


def _assert_windowed(qa_port: int, w: int, h: int, *, timeout_s: float = 20.0,
                     interval: float = 1.0, on_poll=None) -> tuple:
    """POLL /health until the player reports a rendered window size, then assert it is SAFE.

    Must POLL, never one-shot: CombatSurfaceClient caches _screenW/_screenH inside the QA-click
    block and after the `_busy` early-out (CombatSurfaceClient.cs:2320-2326), so they read 0 until a
    non-busy QA frame has run — while /debug already answers from the listener thread.

    SAFETY, NOT IDENTITY (the round-2 finding): the first cut demanded exactly (w,h) or (2w,2h) and
    aborted otherwise. Unity is entitled to adjust a requested resolution (mode snap, DPI clamp,
    minimum size), and a rig that came up at 1268x700 instead of 1280x700 is perfectly safe — failing
    it teaches the next person to delete the check. What must hold is that the rig CANNOT have the
    display: /health's resolution covers less than MAX_SCREEN_COVER_FRAC of the display's BACKING
    PIXEL area (both are backing pixels — see _player_windowed_args), and the window server itself
    agrees the window is not covering the display.
    """
    deadline = time.time() + timeout_s
    last = None
    bw, bh = LQW.main_display_backing()
    area = float(bw * bh)
    while time.time() < deadline:
        if on_poll:
            on_poll()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{qa_port}/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                d = json.loads(r.read().decode("utf-8"))
            sw, sh = int(d.get("screenW") or 0), int(d.get("screenH") or 0)
        except Exception:  # noqa: BLE001
            sw = sh = 0
        if sw > 0 and sh > 0:
            last = (sw, sh)
            if (sw, sh) != (w, h):
                print(f"[sandbox] NOTE: Unity adjusted the requested {w}x{h} to {sw}x{sh} — "
                      f"tolerated; only the coverage bound below is gating.", file=sys.stderr)
            if area > 0:
                frac = (sw * sh) / area
                if frac >= MAX_SCREEN_COVER_FRAC:
                    raise SystemExit(
                        f"[sandbox] player is NOT windowed: /health reports {sw}x{sh}, which is "
                        f"{frac:.0%} of the {bw}x{bh} backing-pixel display (limit "
                        f"{MAX_SCREEN_COVER_FRAC:.0%}). The -screen-* args were ignored — do NOT run "
                        f"a sweep against this instance.")
            else:
                print("[sandbox] WARN: display size unknown — /health coverage bound NOT applied.",
                      file=sys.stderr)
            if on_poll:
                on_poll()      # window-server confirmation of what /health just self-reported
            return last
        time.sleep(interval)
    raise SystemExit(
        f"[sandbox] /health never reported a rendered window size within {timeout_s:.0f}s "
        f"(last={last}) — the QA channel is up but no QA frame has run; treat the geometry as "
        f"UNVERIFIED and tear the run down.")


# ── G. the takeover watchdog (permission-free CGWindowList read) ───────────────────────────────
def _watchdog(pid: int, *, pgid=None, kill: bool = True) -> None:
    """One read of the window server's own bounds. Fails CLOSED; returns None when the rig is safe.

    Runs on EVERY readiness poll and from `qa/qa_sandbox.py watchdog` during a sweep, because one
    check at boot only proves the first second: a mode change, a plist race or a stray
    Screen.SetResolution can turn the rig fullscreen at any later moment, and THAT is the failure
    that cost a reboot on 2026-09-02. A fullscreen verdict kills OUR player first and asks questions
    afterwards — every extra second is the owner's Mac.
    """
    bad = LQW.fullscreen_verdict(pid)
    if bad:
        dsp = LQW.main_display_points()
        killed = _kill_group(pid, "player", expect_pgid=pgid) if kill else "not killed (kill=False)"
        print(f"[sandbox] WATCHDOG — the rig player (pid {pid}) is COVERING the main display {dsp}: "
              f"window bounds {bad['bounds']}. {killed or 'Killed the rig player group.'} The "
              f"-screen-fullscreen 0 arg was ignored or overridden; do not re-run until that is "
              f"understood.", file=sys.stderr)
        raise SystemExit(WATCHDOG_RC)
    bad = LQW.offscreen_verdict(pid)
    if bad:
        raise SystemExit(f"[sandbox] ABORT — player window {bad['bounds']} is effectively OFFSCREEN "
                         f"on display {LQW.main_display_points()}: an offscreen window returns "
                         f"black/stale ScreenCapture frames with no HTTP error, which the visual "
                         f"stage would read as 'the actor never moved'.")


def _http_ok(url: str, post: bool = False, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, data=b"{}" if post else None,
                                     headers={"Content-Type": "application/json"} if post else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def _wait(label: str, url: str, *, post: bool, timeout_s: float, on_poll=None) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if on_poll:
            on_poll()      # the watchdog: it fails CLOSED, it never returns a verdict to ignore
        if _http_ok(url, post=post):
            return True
        time.sleep(2)
    print(f"[sandbox] TIMEOUT waiting for {label} ({url})", file=sys.stderr)
    return False


def _rundir(run: str) -> Path:
    return ROOT / run


def _meta_path(run: str) -> Path:
    return _rundir(run) / "sandbox.json"


# ── QA kit-scene contamination detector ────────────────────────────────────────────────────────────
# build_room_kit.cs assembles kit rooms as "KitRoom_<roomId>" roots in whatever scene is open; a capture
# flow that SAVED the scene baked them into the next build, and the player then drew grey kit masses in
# front of every plate (kit-tavern 2026-07-23). BuildMacOSPlayer now strips them at build time; this is
# the independent check that a given .app is actually clean before a sweep trusts it.
# The name grammar must match every id BuildRoomKit.Sanitize can emit: it keeps char.IsLetterOrDigit
# (Unicode, hence the UTF-8 high bytes), '_' and '-'. A narrower class does not merely miss a root, it
# TRUNCATES one — "KitRoom_Fire-qa" would match as "KitRoom_Fire", get subtracted as a helper light, and
# the contaminated app would pass.
_QA_ROOT_RE = re.compile(rb"KitRoom_[A-Za-z0-9_\-\x80-\xff]+")

# Unity may serialize a scene's objects into level*, into the shared-asset archives, or into the raw
# resource streams beside them. A level-only scan (the first cut of this check) therefore reads CLEAN on
# a contaminated app whose objects landed in sharedassets — so scan all three.
_SCAN_GLOBS = ("level*", "sharedassets*", "*.resS")
_SCAN_CHUNK = 8 << 20        # bounded: stream in 8 MiB chunks (a .resS is routinely GB-scale) ...
_SCAN_OVERLAP = 512          # carried between chunks so a name straddling a boundary is seen whole
_SCAN_BUDGET = 16 << 30      # ... under a total budget so a pathological build can't hang the gate


class KitScanIncomplete(RuntimeError):
    """The contamination scan could not read every byte it needed — verdict unknown, NOT clean."""

# Helper CHILD objects the kit rig creates UNDER a KitRoom_<roomId> root (brazier fires, tomb glow, cool
# key light). They carry the KitRoom_ prefix but are never roots, so a scan that matches ONLY these has
# found the rig's lights, not a kit room shipped inside the player. The names are PARSED from the C# that
# creates them, so the allowlist cannot drift from the names actually emitted; the tuple below is only the
# fallback for a checkout without the renderer extension, and qa/test_qa_sandbox_kit_roots.py asserts the
# two agree.
_KIT_BUILDER_CS = REPO / "extensions/renderers/unity/scripts/build_room_kit.cs"
_KIT_HELPER_FALLBACK = ("KitRoom_Fire", "KitRoom_TombGlow", "KitRoom_CoolKey")
_KIT_HELPER_RE = re.compile(r'KitHelper\w+\s*=\s*"(KitRoom_[A-Za-z0-9_\-]+)"')


def _kit_helper_names(source: Path = _KIT_BUILDER_CS) -> frozenset:
    """Helper child-object names, read from build_room_kit.cs's `KitHelper*` constants."""
    try:
        names = _KIT_HELPER_RE.findall(source.read_text())
    except OSError:
        names = []
    return frozenset(names or _KIT_HELPER_FALLBACK)


def _qa_roots_in_app(app: Path) -> set:
    """Names of QA kit-scene ROOTS (KitRoom_*) baked into the app's serialized data.

    Unity stores GameObject names as plain bytes, so a byte scan is a reliable, dependency-free
    detector (the same check as `strings level0 | grep KitRoom_`). Helper children are subtracted:
    a helper-only match is the light rig, not contamination.

    Raises KitScanIncomplete if the byte budget runs out before every file is read — an unscanned
    tail must never read as clean, or an oversized build silently invalidates the sweep evidence.
    """
    found: set = set()
    data_dir = app / "Contents" / "Resources" / "Data"
    scanned: set = set()
    budget = _SCAN_BUDGET
    for pattern in _SCAN_GLOBS:
        for f in sorted(data_dir.glob(pattern)):
            if f.name in scanned or not f.is_file():
                continue
            scanned.add(f.name)
            try:
                with f.open("rb") as fh:
                    buf = b""
                    while True:
                        if budget <= 0:
                            # Budget gone. Only an actual unread remainder is a problem — a file that
                            # ended exactly on the budget was fully scanned.
                            if not fh.read(1):
                                break
                            raise KitScanIncomplete(
                                f"[sandbox] contamination scan hit its {_SCAN_BUDGET} byte budget inside "
                                f"{f.name}; the rest of {app} is UNSCANNED, so this build's kit-root "
                                f"verdict is unknown — raise _SCAN_BUDGET or shrink the build, but do "
                                f"not treat this as clean.")
                        chunk = fh.read(min(_SCAN_CHUNK, budget))
                        if not chunk:
                            break
                        budget -= len(chunk)
                        buf += chunk
                        # A match touching the end of a non-final buffer may be TRUNCATED, and a truncated
                        # name can land exactly on a helper ("KitRoom_Fire" out of "KitRoom_Firepit"). Drop
                        # it here; the overlap carried forward re-finds it whole on the next pass.
                        found.update(m.group().decode("utf-8", "replace")
                                     for m in _QA_ROOT_RE.finditer(buf) if m.end() < len(buf))
                        buf = buf[-_SCAN_OVERLAP:]
                    found.update(m.group().decode("utf-8", "replace") for m in _QA_ROOT_RE.finditer(buf))
            except OSError as exc:
                # An archive we could not open or read cannot yield a clean verdict — fail CLOSED,
                # exactly like budget exhaustion: a permissions/corruption/I-O failure must never let
                # `up` accept a build it never actually scanned.
                raise KitScanIncomplete(
                    f"[sandbox] contamination scan could not read a level/sharedassets file: {exc} — "
                    f"the app's kit-root verdict is unknown; fix the file/permissions and re-run `up`.") from exc
    return found - _kit_helper_names()


def up(run: str, *, campaign: str, engine_port: int, qa_port: int,
       seed_cmd: str, app: Path) -> dict:
    rd = _rundir(run)
    state = rd / "state"
    if _meta_path(run).exists():
        raise SystemExit(f"[sandbox] run '{run}' already provisioned — `down` it first ({rd})")

    # 0a) #1672: never launch a QA player over an owner who is actively using the Mac.
    owner_active_guard()

    # 0b) PREFLIGHT the window geometry HERE, before anything is created or spawned — that is what
    #     the step-0 comment below promises. It used to be computed inside step 3, so a bad
    #     WORLDOS_PLAYER_WIN_W/H raised only after the seed had run and the engine was already up.
    win_args = _player_windowed_args(rd / "unity_player.log")
    win_w = int(win_args[win_args.index("-screen-width") + 1])
    win_h = int(win_args[win_args.index("-screen-height") + 1])

    plist_before = _plist_snapshot()      # SHARED domain — snapshot now, diff + restore in down()
    if plist_before is None:
        raise SystemExit("[sandbox] could not snapshot the shared plist — refusing to launch "
                         "(a failed read must never be restored as an empty domain).")
    front_before = LQW.front_app()        # {"name","pid"}: focus is handed back BY PID, not by name

    # 0c) PREFLIGHT the .app, still before anything is created or spawned. Both of these used to run after the
    #    engine was already up, so a rejected app still seeded state and left an engine behind that no
    #    run metadata could clean up (terminate() is non-blocking and `down` needs sandbox.json).
    pbin = app / "Contents" / "MacOS" / "WorldOSPlayer"
    if not pbin.exists():
        raise SystemExit(f"[sandbox] player binary missing: {pbin}")
    contaminated = _qa_roots_in_app(app)
    if contaminated:
        raise SystemExit(
            f"[sandbox] app CONTAMINATED — QA kit roots baked into the build: {sorted(contaminated)} "
            f"({app}). A KitRoom_* scene root was saved into the canonical scene and shipped; it renders "
            f"grey kit masses over every plate (kit-tavern 2026-07-23). Rebuild via BuildMacOSPlayer, "
            f"which strips them for the build and reports strippedQARoots in build-report.txt.")

    state.mkdir(parents=True, exist_ok=True)

    # 1) seed the cloned state dir (default: the 3-room registered world + Aldric)
    cmd = seed_cmd.format(state=str(state))
    print(f"[sandbox] seeding: {cmd}")
    seed = subprocess.run(shlex.split(cmd), cwd=REPO, capture_output=True, text=True, timeout=300)
    (rd / "seed.log").write_text(seed.stdout + "\n---STDERR---\n" + seed.stderr)
    if seed.returncode != 0:
        raise SystemExit(f"[sandbox] seed FAILED (rc={seed.returncode}) — see {rd/'seed.log'}")

    # 2) engine/viewer on its own port + state dir (the plist pattern, uv venv).
    # WORLDOS_PLAYER_MOVES is LOAD-BEARING: viewer/server.py _live_play() only accepts /move intents
    # when it is set AND writable — without it the viewer is a read-only projection and every walk
    # refuses (measured: sandbox sweep 0/29 reachable, token frozen, is_live_view False).
    env = dict(os.environ,
               WORLDOS_STATE_DIR=str(state),
               WORLDOS_PLAYER_MOVES=str(state / "player_moves.jsonl"),
               WORLDOS_VIEWER_CHAT=str(state / "chat.jsonl"))
    eng_log = open(rd / "engine.log", "w")  # noqa: SIM115 — long-lived child log
    engine = subprocess.Popen(
        ["uv", "run", "--directory", "servers/engine", "python",
         str(REPO / "viewer" / "server.py"), campaign, str(engine_port)],
        cwd=REPO, env=env, stdout=eng_log, stderr=subprocess.STDOUT,
        start_new_session=True)
    eng_pgid = os.getpgid(engine.pid)
    if not _wait("engine", f"http://127.0.0.1:{engine_port}/combat-surface", post=False, timeout_s=90):
        # killpg, NOT terminate(): engine.pid is the `uv` WRAPPER — SIGTERM to it leaves the real
        # viewer/server.py holding the port, and `down` has no sandbox.json to clean up with yet.
        _kill_group(engine.pid, "engine", expect_pgid=eng_pgid)
        raise SystemExit(f"[sandbox] engine never came up — see {rd/'engine.log'}")

    # 3) a SECOND player instance — direct binary exec (open -n drops env), own QA port.
    #    ⚠ never `osascript quit app` here (kills the OWNER's instance too) — pid-scoped only.
    #    (binary presence + contamination were preflighted at step 0, before anything was spawned.)
    home = rd / "home"
    home.mkdir(parents=True, exist_ok=True)
    penv = dict(os.environ,
                WORLDOS_ENGINE_BASE_URL=f"http://127.0.0.1:{engine_port}",
                WORLDOS_CAMPAIGN_ID=campaign,
                WORLDOS_QA_INPUT="1",
                WORLDOS_QA_INPUT_PORT=str(qa_port),
                # PER-RUN HOME. Measured 2026-09-02: CFFIXED_USER_HOME DOES redirect
                # Application.persistentDataPath, so this run's wos_shot_<n>.png land in
                # <rundir>/home/Library/Application Support/com.worldos.WorldOSPlayer and can no
                # longer collide with the owner instance's shots in the shared directory. No
                # consumer needs to change: walk_test._capture_shot copies the ABSOLUTE path /shot
                # returns, and the two lanes built on it (adventure_walk, player_cert) call that
                # same helper. It does NOT redirect PlayerPrefs — ~/Library/Preferences stays
                # shared, which is why down() still leak-checks and restores that domain.
                CFFIXED_USER_HOME=str(home))
    ply_log = open(rd / "player.log", "w")  # noqa: SIM115
    argv = [str(pbin), *win_args]
    print(f"[sandbox] player argv: {' '.join(argv)}")
    # The player is its OWN Popen — NOT a caffeinate child. caffeinate does not forward SIGTERM, so
    # the old `caffeinate -disu <player>` shape meant terminate() killed only caffeinate and the
    # Unity child reparented to launchd and SURVIVED — a fullscreen orphan nothing could clean up.
    # caffeinate is now a SIBLING watcher (`-w <pid>`, exits with the player) in the SAME process
    # group, and -d/-u are gone: -u ASSERTS USER ACTIVITY (wakes and holds the owner's display).
    # -i (idle) + -s (system sleep) still keep App-Nap -> throttled-render -> delayed /shot away.
    try:
        owner_active_guard()
    except SystemExit:
        _kill_group(engine.pid, "engine", expect_pgid=eng_pgid)
        raise
    player = subprocess.Popen(argv, env=penv, stdout=ply_log, stderr=subprocess.STDOUT,
                              start_new_session=True)
    player_pgid = os.getpgid(player.pid)
    try:
        caff = subprocess.Popen(["/usr/bin/caffeinate", *CAFFEINATE_FLAGS, "-w", str(player.pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                start_new_session=False)
    except Exception as exc:  # noqa: BLE001
        caff = None
        print(f"[sandbox] WARN: caffeinate unavailable ({exc}); continuing without it.",
              file=sys.stderr)

    meta = {"run": run, "campaign": campaign, "state": str(state), "status": "booting",
            "engine": f"http://127.0.0.1:{engine_port}", "qa": f"http://127.0.0.1:{qa_port}",
            "win": [win_w, win_h], "argv": argv, "screen": None, "home": str(home),
            "plist_before": plist_before, "front_before": front_before,
            "pids": {"engine": engine.pid, "player": player.pid},
            "pgids": {"engine": eng_pgid, "player": player_pgid},
            "caffeinate_pid": caff.pid if caff else None}
    # ANTI-ORPHAN FENCE: write the metadata BEFORE the readiness wait. Previously it was written
    # after, so a boot timeout raised with nothing on disk and `down` had no pids to kill.
    _meta_path(run).write_text(json.dumps(meta, indent=2) + "\n")

    def _guard() -> None:
        """The takeover watchdog, run on EVERY readiness poll (see _watchdog)."""
        _watchdog(player.pid, pgid=player_pgid)

    # ABORT UNWIND: everything past the Popen runs inside this. `except BaseException` is deliberate
    # — a Ctrl-C (KeyboardInterrupt) or a SystemExit from any check below used to leave a live
    # player and engine behind with only a sandbox.json to prove it, which on 2026-09-02 is exactly
    # the state that ended in a reboot. Whatever goes wrong, the run comes down.
    try:
        # window-server bounds, not the player's self-report: bound a flags-ignored regression to
        # ~25s instead of a machine lockup.
        if not LQW.wait_for_window(player.pid, timeout=25):
            print("[sandbox] WARN: CGWindowList shows no content window for the player yet — "
                  "geometry unverified at this point (the /health assertion below still gates).",
                  file=sys.stderr)
        _guard()
        if not _wait("player QA channel", f"http://127.0.0.1:{qa_port}/debug", post=True,
                     timeout_s=120, on_poll=_guard):
            raise SystemExit(f"[sandbox] player QA channel never came up — see {rd/'player.log'}")
        meta["screen"] = list(_assert_windowed(qa_port, win_w, win_h, on_poll=_guard))
        meta["status"] = "up"
        _meta_path(run).write_text(json.dumps(meta, indent=2) + "\n")
    except BaseException:
        try:
            down(run)  # safe: sandbox.json exists, so this kills both process GROUPS
        except BaseException as exc:   # noqa: BLE001 — a teardown error must not MASK the abort
            print(f"[sandbox] teardown after abort FAILED: {exc}", file=sys.stderr)
        raise
    finally:
        # In a finally, not on the success path only: a run that aborts has just as much stolen the
        # owner's focus as one that succeeds.
        LQW.restore_front(front_before)
    print(f"[sandbox] UP — engine {meta['engine']}  qa {meta['qa']}  (pids {meta['pids']})  "
          f"window {win_w}x{win_h} (screen {meta['screen'][0]}x{meta['screen'][1]})  "
          f"home {home}")
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def down(run: str) -> int:
    """Kill ONLY this run's process groups, verify they died, then leak-check + restore the SHARED
    plist. Returns 0 clean, non-zero if anything leaked. NEVER `osascript quit app "WorldOSPlayer"`
    — that kills EVERY instance, including the owner's live game."""
    mp = _meta_path(run)
    if not mp.exists():
        print(f"[sandbox] no sandbox.json for '{run}' — nothing to stop")
        return 0
    meta = json.loads(mp.read_text())
    pids = {n: int(p) for n, p in meta.get("pids", {}).items()}
    pgids = meta.get("pgids") or {}
    leaks: list = []
    for name, pid in pids.items():
        # IDENTITY FIRST: a recorded pid can have been recycled by the OS since `up`, and killpg on a
        # stale number takes out an unrelated process TREE. _kill_group refuses on a pgid mismatch.
        refused = _kill_group(pid, name, expect_pgid=pgids.get(name))
        if refused:
            leaks.append(f"NOT SIGNALLED: {name} pid {pid} — {refused}")
            continue
        deadline = time.time() + 5.0
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.5)
        if _pid_alive(pid):
            leaks.append(f"LEAK: {name} pid {pid} SURVIVED SIGKILL")
            print(f"[sandbox] LEAK: {name} pid {pid} SURVIVED SIGKILL", file=sys.stderr)
        else:
            print(f"[sandbox] stopped {name} (pid {pid})")

    # LEAK CHECK (#1672 item 3) — ORDER MATTERS: only diff after OUR pids are confirmed dead. A live
    # instance writes the domain on quit and cfprefsd caches it, so an earlier diff reads the wrong
    # state and an earlier restore is silently clobbered.
    time.sleep(2.0)
    before = meta.get("plist_before")
    after = _plist_snapshot() if before is not None else None
    snapshot_ok = before is not None and after is not None
    diff = _plist_diff(before, after) if snapshot_ok else {}
    foreign = _player_pids() - set(pids.values())
    win = meta.get("win") or []
    rig_values = _rig_written_values(*win[:2]) if len(win) >= 2 else {}
    report = (_plist_restore(diff, foreign_alive=bool(foreign), rig_values=rig_values)
              if snapshot_ok else {"restored": {}, "skipped": {},
                                   "note": "shared plist snapshot unavailable — NOT restored"})
    report.update({"domain": PLIST_DOMAIN, "before": before, "after": after, "changed": diff,
                   "rig_values": rig_values, "foreign_player_pids": sorted(foreign)})
    (_rundir(run) / "prefs_leak.json").write_text(json.dumps(report, indent=2) + "\n")
    if diff:
        print(f"[sandbox] shared plist CHANGED by this run: {sorted(diff)}")
        for key, old in sorted(report["restored"].items()):
            print(f"[sandbox]   RESTORED   {key!r} -> {old!r}")
        for key, why in sorted(report["skipped"].items()):
            print(f"[sandbox]   LEFT AS-IS {key!r}: {why}")
        unrestored = sorted(set(diff) - set(report["restored"]))
        if unrestored:
            print(f"[sandbox] LEAK CHECK: {len(unrestored)} changed key(s) NOT restored "
                  f"{unrestored} — see {_rundir(run)/'prefs_leak.json'}")
        if report["note"]:
            print(f"[sandbox] {report['note']}", file=sys.stderr)
    elif report["note"]:
        print(f"[sandbox] {report['note']}", file=sys.stderr)
    else:
        print("[sandbox] shared plist clean — no non-churn key changed.")

    # ORPHAN SCAN, two sensors. The QA port is the rig's EXACT identity (owner = 8971); every other
    # WorldOSPlayer pid is the OWNER's game — report it, never signal it.
    stray = _orphan_report(qa_url=meta.get("qa"), our_pids=set(pids.values()))
    leaks += stray["leaks"]
    for line in stray["lines"]:
        print(line)

    # the teardown ledger travels WITH the run metadata, not only in prefs_leak.json
    meta["stopped"] = {"plist": report, "orphans": stray["lines"], "leaks": leaks}
    mp.write_text(json.dumps(meta, indent=2) + "\n")
    if leaks or foreign or not snapshot_ok:
        print("[sandbox] cleanup INCOMPLETE — keeping sandbox.json; re-run `down --run <run>`",
              file=sys.stderr)
    else:
        mp.rename(mp.with_suffix(".json.stopped"))
    print(f"[sandbox] DOWN — state/logs kept at {_rundir(run)} for evidence")
    return 1 if leaks else 0


def _listeners(port: int) -> set:
    try:
        p = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return set()
    return {int(t) for t in p.stdout.split() if t.strip().isdigit()}


def _orphan_report(*, qa_url=None, our_pids: set = frozenset()) -> dict:
    port = QA_PORT
    if qa_url:
        try:
            port = int(str(qa_url).rsplit(":", 1)[-1].strip("/"))
        except ValueError:
            port = QA_PORT
    lines, leaks = [], []
    held = _listeners(port)
    if held:
        leaks.append(f"LEAK: QA port {port} still LISTENing (pids {sorted(held)})")
        lines.append(f"[sandbox] LEAK: QA port {port} still held by pids {sorted(held)} — that port "
                     f"is the rig's identity (owner = 8971), so those are ours to kill.")
    else:
        lines.append(f"[sandbox] QA port {port} free.")
    ours, theirs = [], []
    for w in LQW.cg_windows(owner="WorldOSPlayer"):
        (ours if w["pid"] in our_pids else theirs).append(w["pid"])
    if ours:
        leaks.append(f"LEAK: sandbox player window still on screen (pids {sorted(set(ours))})")
        lines.append(f"[sandbox] LEAK: sandbox player windows still on screen: {sorted(set(ours))}")
    if theirs:
        lines.append(f"[sandbox] NOTE: {len(set(theirs))} other WorldOSPlayer window(s) "
                     f"(pids {sorted(set(theirs))}) — that is the OWNER's game. NOT touched.")
    return {"lines": lines, "leaks": leaks, "port": port, "port_pids": sorted(held)}


def status(run: str) -> dict:
    mp = _meta_path(run)
    if not mp.exists():
        return {"run": run, "up": False}
    meta = json.loads(mp.read_text())
    meta["engine_ok"] = _http_ok(meta["engine"] + "/combat-surface")
    meta["qa_ok"] = _http_ok(meta["qa"] + "/debug", post=True)
    meta["up"] = meta["engine_ok"] and meta["qa_ok"]
    pid = int((meta.get("pids") or {}).get("player") or 0)
    bad = LQW.fullscreen_verdict(pid) if pid else None      # read-only; `watchdog` is the one that acts
    meta["takeover"] = bad["bounds"] if bad else None
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "down", "status", "watchdog", "orphans"])
    ap.add_argument("--run", default="default")
    ap.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    ap.add_argument("--engine-port", type=int, default=ENGINE_PORT)
    ap.add_argument("--qa-port", type=int, default=QA_PORT)
    ap.add_argument("--app", default=str(DEFAULT_APP))
    ap.add_argument("--seed-cmd",
                    default="uv run --directory servers/engine python "
                            + str(HERE / "seed_gfx_registered_world.py") + " {state}",
                    help="seed command; '{state}' expands to the sandbox state dir")
    args = ap.parse_args(argv)

    if args.cmd == "up":
        up(args.run, campaign=args.campaign, engine_port=args.engine_port,
           qa_port=args.qa_port, seed_cmd=args.seed_cmd, app=Path(args.app))
    elif args.cmd == "down":
        return down(args.run)
    elif args.cmd == "watchdog":
        # Run this periodically for the duration of a sweep: the boot checks only prove the first
        # second, and this is a permission-free CGWindowList read that costs nothing.
        mp = _meta_path(args.run)
        if not mp.exists():
            print(f"[sandbox] no sandbox.json for '{args.run}' — nothing to watch")
            return 0
        meta = json.loads(mp.read_text())
        pid = int((meta.get("pids") or {}).get("player") or 0)
        if not pid:
            print(f"[sandbox] no player pid recorded for '{args.run}'", file=sys.stderr)
            return 0
        try:
            _watchdog(pid, pgid=(meta.get("pgids") or {}).get("player"))
        except SystemExit:
            down(args.run)      # the player is already dead; this reclaims the engine + the plist
            raise
        print(f"[sandbox] watchdog OK — player {pid} is windowed and on-screen.")
        return 0
    elif args.cmd == "orphans":
        rep = _orphan_report(qa_url=f"http://127.0.0.1:{args.qa_port}")
        for line in rep["lines"]:
            print(line)
        return 1 if rep["leaks"] else 0
    else:
        print(json.dumps(status(args.run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
