#!/usr/bin/env python3
"""Units for the WINDOWED, non-hijacking sandbox player launch (#1672, incident 2026-09-02).

The incident: `qa/qa_sandbox.py up` launched the second player instance with NO Unity window args,
so it inherited the SHARED plist's "Screenmanager Fullscreen mode"=1 + "Resolution Use Native"=1,
came up fullscreen on the developer's only display during a gate run, and the Mac had to be rebooted.

NOTHING here launches a player, opens a port, or touches the real
~/Library/Preferences/com.worldos.WorldOSPlayer.plist — every `defaults` / `ioreg` / `lsof` /
CoreGraphics call is monkeypatched.

Run: uv run --directory servers/engine python -m pytest qa/test_qa_sandbox_window.py -q -p no:xdist
"""
from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib_qa_window as LQW  # noqa: E402
import qa_sandbox  # noqa: E402

SANDBOX_SRC = (HERE / "qa_sandbox.py").read_text(encoding="utf-8")
WINDOW_SRC = (HERE / "lib_qa_window.py").read_text(encoding="utf-8")
CSC = HERE.parent / "extensions" / "renderers" / "unity" / "scripts" / "CombatSurfaceClient.cs"


def _proc(stdout="", rc=0):
    return types.SimpleNamespace(stdout=stdout, stderr="", returncode=rc)


def _string_literals(path: Path) -> list:
    """Every string literal in a module EXCEPT docstrings — i.e. strings the code actually uses.

    Comment/docstring prose in these modules deliberately mentions the forbidden commands ("NEVER
    osascript quit app ..."), so a raw substring scan would fail on the very warning that documents
    the rule. This scans what would actually be executed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("WORLDOS_PLAYER_WIN_W", "WORLDOS_PLAYER_WIN_H", "WORLDOS_PLAYER_IDLE_THRESHOLD",
              "FORCE_PLAYER_QA", "WORLDOS_QA_PLIST_RESTORE"):
        monkeypatch.delenv(k, raising=False)


# ── launch args ───────────────────────────────────────────────────────────────────────────────────
def test_player_windowed_args_defaults(monkeypatch):
    """The exact Unity arg contract, in order: fullscreen OFF, then a fitted size, then the log."""
    monkeypatch.setattr(LQW, "main_display_points", lambda: (1492, 817))
    args = qa_sandbox._player_windowed_args("/x.log")
    assert args == ["-screen-fullscreen", "0", "-screen-width", "1280",
                    "-screen-height", "697", "-logFile", "/x.log"]
    # the flag is worthless if its value drifts away from it
    assert args[args.index("-screen-fullscreen") + 1] == "0"


def test_win_env_override_and_fit_clamp(monkeypatch):
    """An oversized request is clamped INTO the desktop, and never below the display's aspect."""
    monkeypatch.setattr(LQW, "main_display_points", lambda: (1492, 817))
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_W", "1600")
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_H", "1000")
    args = qa_sandbox._player_windowed_args(None)
    w = int(args[args.index("-screen-width") + 1])
    h = int(args[args.index("-screen-height") + 1])
    assert h == 697, "height must fit under the menu bar (817 - 120)"
    assert w <= 1332, "width must stay inside the desktop (1492 - 160)"
    # a NARROWER aspect than the display crops horizontal world out of the ortho frame, so a cell
    # that was in frame at the fullscreen baseline would project outside the capture.
    assert w / h >= 1492 / 817
    assert "-logFile" not in args


@pytest.mark.parametrize("w,h", [("notanint", "700"), ("1280", "7e2"), ("320", "700"),
                                 ("1280", "200")])
def test_win_env_rejects_garbage_and_tiny(monkeypatch, w, h):
    monkeypatch.setattr(LQW, "main_display_points", lambda: (1492, 817))
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_W", w)
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_H", h)
    with pytest.raises(SystemExit):
        qa_sandbox._player_windowed_args(None)


# ── owner-active guard ────────────────────────────────────────────────────────────────────────────
def _ioreg(idle_ns) -> str:
    return ('+-o IOHIDSystem  <class IOHIDSystem>\n'
            '    | {\n'
            f'    |   "HIDIdleTime" = {idle_ns}\n'
            '    | }\n')


def test_owner_active_guard(monkeypatch):
    calls = {}

    def fake_run(cmd, *a, **kw):
        calls["cmd"] = cmd
        return _proc(stdout=calls["out"])

    monkeypatch.setattr(qa_sandbox.subprocess, "run", fake_run)

    calls["out"] = _ioreg(int(30e9))                    # owner touched the Mac 30s ago
    with pytest.raises(SystemExit) as ei:
        qa_sandbox.owner_active_guard()
    assert ei.value.code == 75

    calls["out"] = _ioreg(int(300e9))                   # idle 300s -> proceed
    assert qa_sandbox.owner_active_guard() is None
    assert calls["cmd"][:3] == ["ioreg", "-c", "IOHIDSystem"]

    calls["out"] = _ioreg(0)                            # active, but explicitly forced
    monkeypatch.setenv("FORCE_PLAYER_QA", "1")
    assert qa_sandbox.owner_active_guard() is None
    monkeypatch.delenv("FORCE_PLAYER_QA")

    calls["out"] = '    |   "HIDIdleTime" = <garbage>\n'   # unreadable -> assume idle (headless box)
    assert qa_sandbox.owner_active_guard() is None
    calls["out"] = ""
    assert qa_sandbox.owner_active_guard() is None


def test_owner_active_guard_prints_the_documented_line(monkeypatch, capsys):
    monkeypatch.setattr(qa_sandbox.subprocess, "run", lambda *a, **k: _proc(stdout=_ioreg(int(5e9))))
    with pytest.raises(SystemExit):
        qa_sandbox.owner_active_guard()
    assert "SANDBOX-DEFERRED (owner active)" in capsys.readouterr().err


# ── shared-plist leak check ───────────────────────────────────────────────────────────────────────
def _fake_defaults(monkeypatch, values: dict, log: list):
    def fake_run(cmd, *a, **kw):
        log.append(list(cmd))
        if cmd[:2] == ["defaults", "read"]:
            key = cmd[3]
            return _proc(stdout=values[key], rc=0) if key in values else _proc(rc=1)
        return _proc(rc=0)

    monkeypatch.setattr(qa_sandbox.subprocess, "run", fake_run)


def test_plist_snapshot_and_diff(monkeypatch):
    log: list = []
    vals = {"Screenmanager Fullscreen mode": "1", "Screenmanager Resolution Width": "3024"}
    _fake_defaults(monkeypatch, vals, log)
    before = qa_sandbox._plist_snapshot()
    assert before["Screenmanager Fullscreen mode"] == "1"
    assert before["UnitySelectMonitor"] is None, "an absent key reads None, not ''"

    after = dict(before, **{"Screenmanager Fullscreen mode": "3",
                            "unity.player_session_count": "42",
                            "unity_connect.access_token": "x"})
    diff = qa_sandbox._plist_diff(before, after)
    assert set(diff) == {"Screenmanager Fullscreen mode"}
    assert diff["Screenmanager Fullscreen mode"] == {"before": "1", "after": "3"}
    # churn keys must never be reported, or every run looks like a leak
    assert not {"unity.player_session_count", "unity.player_sessionid"} & set(diff)


def test_plist_restore_gating(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    diff = {"Screenmanager Fullscreen mode": {"before": "1", "after": "3"},
            "Screenmanager Window Position X": {"before": None, "after": "900"}}

    rep = qa_sandbox._plist_restore(diff, foreign_alive=False)
    writes = [c for c in log if c[:2] == ["defaults", "write"]]
    deletes = [c for c in log if c[:2] == ["defaults", "delete"]]
    assert writes == [["defaults", "write", qa_sandbox.PLIST_DOMAIN,
                       "Screenmanager Fullscreen mode", "-int", "1"]]
    assert deletes == [["defaults", "delete", qa_sandbox.PLIST_DOMAIN,
                        "Screenmanager Window Position X"]]
    assert set(rep["restored"]) == set(diff)

    log.clear()
    rep = qa_sandbox._plist_restore(diff, foreign_alive=True)
    assert [c for c in log if c[:2] in (["defaults", "write"], ["defaults", "delete"])] == []
    assert rep["restored"] == {} and rep["note"], "a live foreign player must block the write"

    log.clear()
    monkeypatch.setenv("WORLDOS_QA_PLIST_RESTORE", "0")
    rep = qa_sandbox._plist_restore(diff, foreign_alive=False)
    assert [c for c in log if c[:2] in (["defaults", "write"], ["defaults", "delete"])] == []
    assert rep["restored"] == {} and "detect only" in rep["note"]


# ── launch SHAPE (source-level fences) ────────────────────────────────────────────────────────────
def test_meta_written_before_readiness_wait():
    """The anti-orphan fence: a failed boot must always leave a killable recorded pid on disk."""
    assert SANDBOX_SRC.index("_meta_path(run).write_text") < SANDBOX_SRC.index('"player QA channel"')


def test_launch_shape_is_not_caffeinate_parented():
    """caffeinate does NOT forward SIGTERM: a caffeinate-PARENTED player survives terminate() and
    reparents to launchd — that is how the incident stranded a fullscreen orphan. The player must be
    its own Popen, with caffeinate a sibling watcher (`-w <pid>`)."""
    assert "argv = [str(pbin), *win_args]" in SANDBOX_SRC
    assert "subprocess.Popen(argv, env=penv" in SANDBOX_SRC
    assert '"-w", str(player.pid)' in SANDBOX_SRC
    # -u ASSERTS USER ACTIVITY (wakes and holds the owner's display) and -d keeps it awake. Scan the
    # code's own string literals, not the prose that documents why the old flags are gone.
    lits = _string_literals(HERE / "qa_sandbox.py")
    assert "-disu" not in lits
    assert qa_sandbox.CAFFEINATE_FLAGS == ["-is"]
    assert "player.terminate()" not in SANDBOX_SRC
    assert "engine.terminate()" not in SANDBOX_SRC
    assert "os.killpg" in SANDBOX_SRC


def test_no_osascript_quit_and_no_ax():
    """`quit app "WorldOSPlayer"` kills EVERY instance including the owner's. And AX geometry is off
    the critical path: it is nondeterministic on this host and resizableWindow=0 blocks resize."""
    for path in (HERE / "qa_sandbox.py", HERE / "lib_qa_window.py"):
        assert not [s for s in _string_literals(path) if "quit app" in s], path
    for src in (SANDBOX_SRC, WINDOW_SRC):
        assert "set position of" not in src
        assert "set size of" not in src


# ── achieved-geometry assertion ───────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_health(monkeypatch, sequence):
    seq = list(sequence)

    def fake_urlopen(req, timeout=None):
        return _Resp(seq.pop(0) if seq else {"screenW": 0, "screenH": 0})

    monkeypatch.setattr(qa_sandbox.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)


def test_health_assertion_polls_and_accepts_1x_2x(monkeypatch):
    """/health must be POLLED: CombatSurfaceClient.cs:2320-2326 caches _screenW/_screenH only on a
    non-_busy QA frame, so it reads 0 while /debug already answers."""
    zero = {"screenW": 0, "screenH": 0}
    _fake_health(monkeypatch, [zero, zero, {"screenW": 1280, "screenH": 697}])
    assert qa_sandbox._assert_windowed(8972, 1280, 697, timeout_s=5, interval=0) == (1280, 697)

    _fake_health(monkeypatch, [{"screenW": 2560, "screenH": 1394}])
    assert qa_sandbox._assert_windowed(8972, 1280, 697, timeout_s=5, interval=0) == (2560, 1394)

    _fake_health(monkeypatch, [{"screenW": 2984, "screenH": 1634}])   # the incident geometry
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._assert_windowed(8972, 1280, 697, timeout_s=5, interval=0)
    assert "windowed" in str(ei.value)

    _fake_health(monkeypatch, [])                                     # permanently 0
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._assert_windowed(8972, 1280, 697, timeout_s=0.2, interval=0)
    assert "never reported" in str(ei.value)


# ── window-server verdicts (pure; no CoreGraphics call) ───────────────────────────────────────────
def _win(x, y, w, h, pid=999):
    return {"pid": pid, "owner": "WorldOSPlayer", "layer": 0, "name": "",
            "bounds": {"X": float(x), "Y": float(y), "Width": float(w), "Height": float(h)}}


def test_fullscreen_and_offscreen_verdicts(monkeypatch):
    monkeypatch.setattr(LQW, "main_display_points", lambda: (1492, 817))

    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(0, 0, 1492, 817)])
    assert LQW.fullscreen_verdict(999) is not None

    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(100, 60, 1280, 697)])
    assert LQW.fullscreen_verdict(999) is None
    assert LQW.offscreen_verdict(999) is None

    # dragged/parked off the bottom-right: only 12x17 points remain on the display
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(1480, 800, 1280, 697)])
    assert LQW.offscreen_verdict(999) is not None


def test_verdicts_are_inert_without_coregraphics(monkeypatch):
    """A host with no CoreGraphics must not manufacture a verdict out of (0, 0)."""
    monkeypatch.setattr(LQW, "main_display_points", lambda: (0, 0))
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(0, 0, 4000, 4000)])
    assert LQW.fullscreen_verdict(999) is None
    assert LQW.offscreen_verdict(999) is None


# ── Phase 2 (C# badge) — RED until the Editor rebuild lands ───────────────────────────────────────
@pytest.mark.xfail(reason="#1672 item 4: needs the CombatSurfaceClient.cs change + an Editor "
                          "rebuild (docs/qa/QA-RIG-WINDOW-BADGE.md). Flip this when it lands.",
                   strict=False)
def test_csharp_qa_window_policy():
    src = CSC.read_text(encoding="utf-8")
    for sym in ("ApplyQaWindowPolicy", "FullScreenMode.Windowed", "DrawQaBadge",
                "QaRestoreScreenPrefs"):
        assert sym in src, sym
    # OnGUI's advisory early-out would otherwise hide the badge whenever no advisory is up
    assert src.index("DrawQaBadge();") < src.index("if (string.IsNullOrEmpty(_advMsg)) return;")
    body = src.split("void DrawQaBadge()", 1)[1].split("\n    }", 1)[0]
    for banned in ("Time.", "frameCount", "DateTime"):
        assert banned not in body, ("an ANIMATED badge is a large stable-position diff blob that "
                                    "can win the nearest-neighbour race at walk_test.py:236-254")
