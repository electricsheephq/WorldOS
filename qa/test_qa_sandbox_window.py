#!/usr/bin/env python3
"""Units for the WINDOWED, non-hijacking sandbox player launch (#1672, incident 2026-09-02).

The incident: `qa/qa_sandbox.py up` launched the second player instance with NO Unity window args,
so it inherited the SHARED plist's "Screenmanager Fullscreen mode"=1 + "Resolution Use Native"=1,
came up fullscreen on the developer's only display during a gate run, and the Mac had to be rebooted.

Round 2 (two adversarial reviews + a live launch on 2026-09-02) added the properties the first cut
got wrong, and they are the load-bearing half of this file: the teardown restores the SHARED plist
only for values THIS rig provably wrote, focus is handed back by PID (never `open -a`, which would
LAUNCH the owner's player), the geometry assertion tests a coverage bound instead of an exact size,
`-screen-width` is treated as BACKING PIXELS, the takeover watchdog runs on every poll, and a
recorded pid is re-verified against its recorded pgid before anything is signalled.

NOTHING here launches a player, opens a port, or touches the real
~/Library/Preferences/com.worldos.WorldOSPlayer.plist — every `defaults` / `ioreg` / `lsof` /
osascript / CoreGraphics call is monkeypatched.

Run: uv run --directory servers/engine python -m pytest qa/test_qa_sandbox_window.py -q -p no:xdist
"""
from __future__ import annotations

import ast
import json
import plistlib
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

# The measured host, 2026-09-02: a 2x display. POINTS is what CGWindowList speaks; BACKING PIXELS is
# what Unity's -screen-width/-screen-height and /health speak.
HOST_POINTS = (1512, 835)
HOST_BACKING = (3024, 1670)


def _proc(stdout="", rc=0):
    return types.SimpleNamespace(stdout=stdout, stderr="", returncode=rc)


def _display(monkeypatch, points=HOST_POINTS, backing=HOST_BACKING):
    monkeypatch.setattr(LQW, "main_display_points", lambda: points)
    monkeypatch.setattr(LQW, "main_display_backing", lambda: backing)
    monkeypatch.setattr(LQW, "backing_scale", lambda: (backing[0] / points[0]) if points[0] else 1.0)


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


# ── launch args: -screen-* are BACKING PIXELS, the desktop budget is POINTS ────────────────────────
def test_player_windowed_args_defaults_on_a_1x_display(monkeypatch):
    """The exact Unity arg contract, in order: fullscreen OFF, then a fitted size, then the log."""
    _display(monkeypatch, points=(1492, 817), backing=(1492, 817))
    args = qa_sandbox._player_windowed_args("/x.log")
    assert args == ["-screen-fullscreen", "0", "-screen-width", "1280",
                    "-screen-height", "697", "-logFile", "/x.log"]
    # the flag is worthless if its value drifts away from it
    assert args[args.index("-screen-fullscreen") + 1] == "0"


def test_player_windowed_args_on_the_measured_2x_host(monkeypatch):
    """The live 2026-09-02 launch: 1280x700 asked for, 1280x700 reported by /health, 640x382 points
    on screen. The default must survive the clamp untouched on the real host."""
    _display(monkeypatch)
    args = qa_sandbox._player_windowed_args(None)
    assert args == ["-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "700"]


def test_fit_clamp_budget_is_in_backing_pixels_not_points(monkeypatch):
    """UNITS (round-2 finding): the clamp compares a PIXEL request against a POINT desktop. On a 2x
    display the usable budget is (835-120)*2 = 1430 pixels tall; clamping to 715 would halve the
    window for no reason, and on a hypothetical 0.5x it would double it."""
    _display(monkeypatch)
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_W", "4000")
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_H", "3000")
    args = qa_sandbox._player_windowed_args(None)
    w = int(args[args.index("-screen-width") + 1])
    h = int(args[args.index("-screen-height") + 1])
    assert h == (835 - 120) * 2, "height budget is a POINT budget converted to pixels"
    assert w == (1512 - 160) * 2, "width budget likewise"
    # a NARROWER aspect than the display crops horizontal world out of the ortho frame, so a cell
    # that was in frame at the fullscreen baseline would project outside the capture.
    assert w / h >= HOST_POINTS[0] / HOST_POINTS[1]


def test_fit_clamp_is_inert_when_the_display_cannot_be_measured(monkeypatch):
    _display(monkeypatch, points=(0, 0), backing=(0, 0))
    args = qa_sandbox._player_windowed_args(None)
    assert args[args.index("-screen-width") + 1] == str(qa_sandbox.WIN_W)
    assert args[args.index("-screen-height") + 1] == str(qa_sandbox.WIN_H)


@pytest.mark.parametrize("w,h", [("notanint", "700"), ("1280", "7e2"), ("320", "700"),
                                 ("1280", "200")])
def test_win_env_rejects_garbage_and_tiny(monkeypatch, w, h):
    _display(monkeypatch)
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_W", w)
    monkeypatch.setenv("WORLDOS_PLAYER_WIN_H", h)
    with pytest.raises(SystemExit):
        qa_sandbox._player_windowed_args(None)


def test_geometry_is_preflighted_before_anything_is_spawned():
    """The step-0 comment promises the preflight runs 'before anything is created or spawned'. It
    used to be computed inside step 3, so a bad WORLDOS_PLAYER_WIN_W/H raised only after the seed had
    run and the engine was already up."""
    i_win = SANDBOX_SRC.index("win_args = _player_windowed_args")
    assert i_win < SANDBOX_SRC.index("seed = subprocess.run")
    assert i_win < SANDBOX_SRC.index("engine = subprocess.Popen")
    assert i_win < SANDBOX_SRC.index("player = subprocess.Popen")


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


# ── shared-plist leak check + ATTRIBUTION-SAFE restore ────────────────────────────────────────────
def _fake_defaults(monkeypatch, domain: dict, log: list):
    """`defaults export <domain> -` returns the whole domain as an XML plist on stdout (bytes)."""
    def fake_run(cmd, *a, **kw):
        log.append(list(cmd))
        if cmd[:2] == ["defaults", "export"]:
            return types.SimpleNamespace(stdout=plistlib.dumps(domain), stderr=b"", returncode=0)
        return _proc(rc=0)

    monkeypatch.setattr(qa_sandbox.subprocess, "run", fake_run)


def _writes(log):
    return [c for c in log if c[:2] in (["defaults", "write"], ["defaults", "delete"])]


def test_plist_snapshot_is_whole_domain_minus_churn(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {"Screenmanager Fullscreen mode": 1,
                                 "Screenmanager Resolution Width": 3024,
                                 "unity.player_session_count": 41,
                                 "unity.player_sessionid": 12345,
                                 "unity_connect.access_token": "x",
                                 "SomeOwnerSetting": 7}, log)
    snap = qa_sandbox._plist_snapshot()
    assert snap["Screenmanager Fullscreen mode"] == "1"
    assert snap["SomeOwnerSetting"] == "7", "the leak check is whole-domain, not a fixed key list"
    assert not {"unity.player_session_count", "unity.player_sessionid",
                "unity_connect.access_token"} & set(snap)
    assert _writes(log) == [], "the snapshot is READ-ONLY"


def test_plist_snapshot_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(qa_sandbox.subprocess, "run", lambda *args, **kwargs: _proc(rc=1))
    assert qa_sandbox._plist_snapshot() is None


def test_plist_snapshot_empty_export_of_existing_domain_is_unreadable(monkeypatch, tmp_path):
    prefs = tmp_path / "Library" / "Preferences"
    prefs.mkdir(parents=True)
    (prefs / f"{qa_sandbox.PLIST_DOMAIN}.plist").write_bytes(plistlib.dumps({"stale": 1}))
    monkeypatch.setattr(qa_sandbox.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(qa_sandbox.subprocess, "run",
                        lambda *args, **kwargs: _proc(stdout=plistlib.dumps({})))
    assert qa_sandbox._plist_snapshot() is None


def test_plist_diff_reports_added_removed_and_changed():
    before = {"a": "1", "gone": "2"}
    after = {"a": "3", "new": "9", "unity.player_session_count": "42"}
    diff = qa_sandbox._plist_diff(before, after)
    assert diff["a"] == {"before": "1", "after": "3"}
    assert diff["gone"] == {"before": "2", "after": None}
    assert diff["new"] == {"before": None, "after": "9"}
    assert "unity.player_session_count" not in diff, "churn keys must never look like a leak"


def _rig_diff():
    """The five keys the live 2026-09-02 run actually moved, as a down()-shaped diff."""
    return {"Screenmanager Fullscreen mode": {"before": "1", "after": "3"},
            "Screenmanager Resolution Width": {"before": "3024", "after": "1280"},
            "Screenmanager Resolution Height": {"before": "1670", "after": "700"},
            "Screenmanager Resolution Use Native": {"before": "1", "after": "0"},
            "Screenmanager Window Position Y": {"before": "0", "after": "60"}}


def test_restore_writes_only_keys_that_still_hold_the_rig_value(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    rep = qa_sandbox._plist_restore(_rig_diff(), foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    assert sorted(rep["restored"]) == ["Screenmanager Fullscreen mode",
                                       "Screenmanager Resolution Height",
                                       "Screenmanager Resolution Use Native",
                                       "Screenmanager Resolution Width"]
    assert _writes(log) == [
        ["defaults", "write", qa_sandbox.PLIST_DOMAIN, "Screenmanager Fullscreen mode", "-int", "1"],
        ["defaults", "write", qa_sandbox.PLIST_DOMAIN, "Screenmanager Resolution Height", "-int", "1670"],
        ["defaults", "write", qa_sandbox.PLIST_DOMAIN, "Screenmanager Resolution Use Native", "-int", "1"],
        ["defaults", "write", qa_sandbox.PLIST_DOMAIN, "Screenmanager Resolution Width", "-int", "3024"],
    ]


def test_restore_never_touches_the_window_position_keys(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    rep = qa_sandbox._plist_restore(_rig_diff(), foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    for key in qa_sandbox.NEVER_RESTORE:
        assert key not in rep["restored"]
    assert "cosmetic" in rep["skipped"]["Screenmanager Window Position Y"]
    assert not [c for c in _writes(log) if "Position" in c[3]]


def test_restore_skips_a_key_the_owner_wrote_last(monkeypatch):
    """ATTRIBUTION (the round-2 finding): the owner changed their own resolution while the sweep ran.
    The value is no longer the one this rig writes, so reverting it would be OUR regression."""
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    diff = {"Screenmanager Resolution Width": {"before": "3024", "after": "1920"},
            "SomeOwnerSetting": {"before": "7", "after": "9"}}
    rep = qa_sandbox._plist_restore(diff, foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    assert rep["restored"] == {}
    assert "not the value this rig writes" in rep["skipped"]["Screenmanager Resolution Width"]
    assert "not a key this rig writes" in rep["skipped"]["SomeOwnerSetting"]
    assert _writes(log) == []


def test_restore_refuses_a_non_integer_snapshot_value(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    diff = {"Screenmanager Fullscreen mode": {"before": "windowed", "after": "3"}}
    rep = qa_sandbox._plist_restore(diff, foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    assert rep["restored"] == {} and _writes(log) == []
    assert "not an integer" in rep["skipped"]["Screenmanager Fullscreen mode"]


def test_restore_deletes_a_key_that_did_not_exist_before(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    diff = {"Screenmanager Resolution Use Native": {"before": None, "after": "0"}}
    rep = qa_sandbox._plist_restore(diff, foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    assert rep["restored"] == {"Screenmanager Resolution Use Native": None}
    assert _writes(log) == [["defaults", "delete", qa_sandbox.PLIST_DOMAIN,
                             "Screenmanager Resolution Use Native"]]


def test_restore_does_not_claim_failed_defaults_operations(monkeypatch):
    log: list = []

    def failed_defaults(cmd, *args, **kwargs):
        log.append(list(cmd))
        return _proc(rc=7)

    monkeypatch.setattr(qa_sandbox.subprocess, "run", failed_defaults)
    diff = {
        "Screenmanager Resolution Use Native": {"before": None, "after": "0"},
        "Screenmanager Resolution Width": {"before": "3024", "after": "1280"},
    }
    rep = qa_sandbox._plist_restore(diff, foreign_alive=False,
                                    rig_values=qa_sandbox._rig_written_values(1280, 700))
    assert rep["restored"] == {}
    assert set(rep["skipped"]) == set(diff)
    assert all("failed (rc=7)" in why and "STILL in the domain" in why
               for why in rep["skipped"].values())
    assert len(_writes(log)) == 2


def test_down_skips_restore_when_plist_snapshot_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "sandbox.json").write_text(json.dumps({
        "run": "r1", "win": [1280, 700], "pids": {},
        "plist_before": {"Screenmanager Resolution Width": "3024"}}))
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot", lambda: None)
    monkeypatch.setattr(qa_sandbox, "_player_pids", lambda exe="WorldOSPlayer": set())
    monkeypatch.setattr(qa_sandbox, "_orphan_report",
                        lambda **kwargs: {"lines": [], "leaks": [], "port": 8972, "port_pids": []})
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)
    log: list = []
    _fake_defaults(monkeypatch, {}, log)

    assert qa_sandbox.down("r1") == 1
    assert _writes(log) == [], "an unavailable snapshot must never turn into a delete"
    report = json.loads((rd / "prefs_leak.json").read_text())
    assert "unavailable" in report["note"]
    assert (rd / "sandbox.json").exists()
    assert not (rd / "sandbox.json.stopped").exists()


def test_down_renames_meta_for_foreign_player_when_diff_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "sandbox.json").write_text(json.dumps({
        "run": "r1", "win": [1280, 700], "pids": {},
        "plist_before": {"SomeOwnerSetting": "7"}}))
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot", lambda: {"SomeOwnerSetting": "7"})
    monkeypatch.setattr(qa_sandbox, "_player_pids", lambda exe="WorldOSPlayer": {999})
    monkeypatch.setattr(qa_sandbox, "_orphan_report",
                        lambda **kwargs: {"lines": [], "leaks": [], "port": 8972,
                                          "port_pids": []})
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)

    assert qa_sandbox.down("r1") == 0
    assert not (rd / "sandbox.json").exists()
    assert (rd / "sandbox.json.stopped").exists()


def test_down_keeps_meta_and_returns_one_for_foreign_player_with_diff(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "sandbox.json").write_text(json.dumps({
        "run": "r1", "win": [1280, 700], "pids": {},
        "plist_before": {"Screenmanager Fullscreen mode": "1"}}))
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot",
                        lambda: {"Screenmanager Fullscreen mode": "3"})
    monkeypatch.setattr(qa_sandbox, "_player_pids", lambda exe="WorldOSPlayer": {999})
    monkeypatch.setattr(qa_sandbox, "_orphan_report",
                        lambda **kwargs: {"lines": [], "leaks": [], "port": 8972,
                                          "port_pids": []})
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)

    assert qa_sandbox.down("r1") == 1
    assert (rd / "sandbox.json").exists()
    assert not (rd / "sandbox.json.stopped").exists()
    err = capsys.readouterr().err
    assert ("cleanup INCOMPLETE (0 leak(s), foreign [999], snapshot_ok=True)"
            in err)
    assert f"keeping {rd / 'sandbox.json'}" in err
    assert "re-run `down --run r1`" in err


def test_down_renames_legacy_meta_without_plist_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "sandbox.json").write_text(json.dumps({"run": "r1", "pids": {}}))
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot",
                        lambda: pytest.fail("legacy metadata must not snapshot"))
    monkeypatch.setattr(qa_sandbox, "_player_pids", lambda exe="WorldOSPlayer": set())
    monkeypatch.setattr(qa_sandbox, "_orphan_report",
                        lambda **kwargs: {"lines": [], "leaks": [], "port": 8972,
                                          "port_pids": []})
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)

    assert qa_sandbox.down("r1") == 0
    assert (rd / "sandbox.json.stopped").exists()


def test_restore_gating_foreign_player_and_opt_out(monkeypatch):
    log: list = []
    _fake_defaults(monkeypatch, {}, log)
    rig = qa_sandbox._rig_written_values(1280, 700)

    rep = qa_sandbox._plist_restore(_rig_diff(), foreign_alive=True, rig_values=rig)
    assert _writes(log) == []
    assert rep["restored"] == {} and rep["note"], "a live foreign player must block the write"
    assert rep["skipped"], "and every changed key must be reported as skipped, with a reason"

    log.clear()
    monkeypatch.setenv("WORLDOS_QA_PLIST_RESTORE", "0")
    rep = qa_sandbox._plist_restore(_rig_diff(), foreign_alive=False, rig_values=rig)
    assert _writes(log) == []
    assert rep["restored"] == {} and "detect only" in rep["note"]


# ── teardown identity: a recorded pid is not an identity ──────────────────────────────────────────
def test_kill_group_refuses_a_reused_pid(monkeypatch):
    killed: list = []
    monkeypatch.setattr(qa_sandbox.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(qa_sandbox.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)

    why = qa_sandbox._kill_group(999, "player", expect_pgid=1111)
    assert "reused" in why and killed == [], "a pgid mismatch must NEVER signal a group"

    assert qa_sandbox._kill_group(999, "player", expect_pgid=4242) == ""
    assert [sig for _pg, sig in killed] == [qa_sandbox.signal.SIGTERM, qa_sandbox.signal.SIGKILL]


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


def test_boot_unwinds_on_any_abort_including_ctrl_c():
    """KeyboardInterrupt/SystemExit are BaseExceptions: an `except Exception` here leaves a live
    player and engine behind with only a sandbox.json to prove it."""
    tail = SANDBOX_SRC.split("player = subprocess.Popen(argv", 1)[1]
    assert "except BaseException:" in tail
    body = tail.split("except BaseException:", 1)[1]
    assert "down(run)" in body.split("raise", 1)[0]
    assert "finally:" in tail and "LQW.restore_front(front_before)" in tail.split("finally:", 1)[1]


def test_per_run_home_isolates_the_shot_directory():
    """CFFIXED_USER_HOME=<rundir>/home: /shot frames land in the run dir instead of racing the owner
    instance in the shared Application Support. Consumers copy the ABSOLUTE path /shot returns, so
    none of them may hardcode the shared location."""
    assert 'CFFIXED_USER_HOME=str(home)' in SANDBOX_SRC
    assert 'home = rd / "home"' in SANDBOX_SRC and "home.mkdir(" in SANDBOX_SRC
    for lane in ("walk_test.py", "adventure_walk.py", "player_cert.py"):
        src = (HERE / lane).read_text(encoding="utf-8")
        assert "Application Support" not in src, f"{lane} must not hardcode the shot directory"


# ── focus restore: by PID, never `open -a` ────────────────────────────────────────────────────────
def test_restore_front_never_uses_launch_services():
    """`open -a WorldOSPlayer` resolves a NAME to an installed bundle: if the owner's game was
    frontmost (or had since quit), the 'restore' would LAUNCH a fresh fullscreen player — the exact
    hijack this lane exists to prevent."""
    lits = _string_literals(HERE / "lib_qa_window.py")
    assert "/usr/bin/open" not in lits and "open" not in lits
    assert "-a" not in lits


def test_restore_front_fronts_by_unix_id(monkeypatch):
    seen: list = []
    monkeypatch.setattr(LQW.subprocess, "run",
                        lambda cmd, *a, **k: (seen.append(cmd), _proc(rc=0))[1])
    assert LQW.restore_front({"name": "Terminal", "pid": 4321}) is True
    script = seen[0][-1]
    assert seen[0][0] == "/usr/bin/osascript"
    assert "unix id is 4321" in script and "set frontmost" in script


def test_restore_front_skips_a_player_or_a_missing_record(monkeypatch, capsys):
    seen: list = []
    monkeypatch.setattr(LQW.subprocess, "run",
                        lambda cmd, *a, **k: (seen.append(cmd), _proc(rc=0))[1])
    assert LQW.restore_front(None) is False
    assert LQW.restore_front({"name": "WorldOSPlayer", "pid": 55}) is False
    assert seen == [], "neither case may run anything"
    err = capsys.readouterr().err
    assert "no frontmost app was recorded" in err and "a player" in err


def test_front_app_parses_name_and_pid(monkeypatch):
    monkeypatch.setattr(LQW.subprocess, "run", lambda *a, **k: _proc(stdout="Terminal, 4321\n"))
    assert LQW.front_app() == {"name": "Terminal", "pid": 4321}
    monkeypatch.setattr(LQW.subprocess, "run", lambda *a, **k: _proc(stdout="", rc=1))
    assert LQW.front_app() is None


# ── achieved-geometry assertion: a SAFETY bound, not an exact size ────────────────────────────────
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


def test_health_assertion_polls_until_a_frame_has_run(monkeypatch):
    """/health must be POLLED: CombatSurfaceClient.cs:2320-2326 caches _screenW/_screenH only on a
    non-_busy QA frame, so it reads 0 while /debug already answers."""
    _display(monkeypatch)
    zero = {"screenW": 0, "screenH": 0}
    _fake_health(monkeypatch, [zero, zero, {"screenW": 1280, "screenH": 700}])
    assert qa_sandbox._assert_windowed(8972, 1280, 700, timeout_s=5, interval=0) == (1280, 700)

    _fake_health(monkeypatch, [])                                     # permanently 0
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._assert_windowed(8972, 1280, 700, timeout_s=0.2, interval=0)
    assert "never reported" in str(ei.value)


def test_health_assertion_tolerates_a_unity_adjusted_size(monkeypatch):
    """Unity may snap a requested resolution. 1268x700 instead of 1280x700 is SAFE, and failing it
    only teaches the next person to delete the check."""
    _display(monkeypatch)
    _fake_health(monkeypatch, [{"screenW": 1268, "screenH": 700}])
    assert qa_sandbox._assert_windowed(8972, 1280, 700, timeout_s=5, interval=0) == (1268, 700)


def test_health_assertion_fails_on_the_incident_geometry(monkeypatch):
    _display(monkeypatch)
    _fake_health(monkeypatch, [{"screenW": 3024, "screenH": 1670}])   # the incident: the whole screen
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._assert_windowed(8972, 1280, 700, timeout_s=5, interval=0)
    assert "NOT windowed" in str(ei.value)


def test_health_assertion_runs_the_watchdog_every_poll(monkeypatch):
    _display(monkeypatch)
    polls: list = []
    zero = {"screenW": 0, "screenH": 0}
    _fake_health(monkeypatch, [zero, zero, {"screenW": 1280, "screenH": 700}])
    qa_sandbox._assert_windowed(8972, 1280, 700, timeout_s=5, interval=0,
                                on_poll=lambda: polls.append(1))
    assert len(polls) >= 3, "the window server is re-read on every poll, not once at the end"


def test_wait_runs_the_watchdog_every_poll(monkeypatch):
    polls: list = []
    monkeypatch.setattr(qa_sandbox, "_http_ok", lambda *a, **k: bool(polls) and len(polls) >= 2)
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)
    waited = qa_sandbox._wait("x", "http://x", post=True, timeout_s=5,
                              on_poll=lambda: polls.append(1))
    assert waited is True
    assert len(polls) >= 2


# ── the takeover watchdog ─────────────────────────────────────────────────────────────────────────
def _win(x, y, w, h, pid=999, layer=0):
    return {"pid": pid, "owner": "WorldOSPlayer", "layer": layer, "name": "",
            "bounds": {"X": float(x), "Y": float(y), "Width": float(w), "Height": float(h)}}


def test_watchdog_kills_the_rig_player_and_exits_3(monkeypatch, capsys):
    _display(monkeypatch, points=(1492, 817), backing=(2984, 1634))
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(0, 0, 1492, 817)])
    killed: list = []
    monkeypatch.setattr(qa_sandbox, "_kill_group",
                        lambda pid, label="", expect_pgid=None: killed.append((pid, expect_pgid)) or "")
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._watchdog(999, pgid=777)
    assert ei.value.code == qa_sandbox.WATCHDOG_RC == 3
    assert killed == [(999, 777)], "the rig's OWN player group, identity-checked, killed first"
    assert "WATCHDOG" in capsys.readouterr().err


def test_watchdog_passes_a_windowed_player_and_fails_an_offscreen_one(monkeypatch):
    _display(monkeypatch, points=(1492, 817), backing=(2984, 1634))
    monkeypatch.setattr(qa_sandbox, "_kill_group", lambda *a, **k: "")
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(100, 60, 640, 382)])
    assert qa_sandbox._watchdog(999, pgid=777) is None

    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(1480, 800, 640, 382)])
    with pytest.raises(SystemExit) as ei:
        qa_sandbox._watchdog(999, pgid=777)
    assert "OFFSCREEN" in str(ei.value)


# ── window-server verdicts (pure; no CoreGraphics call) ───────────────────────────────────────────
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


def test_verdicts_ignore_the_players_small_aux_windows(monkeypatch):
    """Measured 2026-09-02: the player also owns four 1492x30 layer-0 strips, off-screen. Unfiltered
    they make offscreen_verdict fire on every healthy run — wait_for_window already filtered them, so
    the verdicts must use the SAME filter or the boot check and the watchdog disagree."""
    monkeypatch.setattr(LQW, "main_display_points", lambda: (1512, 835))
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [
        _win(0, 30, 640, 382), _win(0, -1000, 1492, 30), _win(0, -1000, 1492, 30)])
    assert LQW.offscreen_verdict(999) is None
    assert LQW.fullscreen_verdict(999) is None
    assert len(LQW.wait_for_window(999, timeout=0.1)) == 1


def test_verdicts_are_inert_without_coregraphics(monkeypatch):
    """A host with no CoreGraphics must not manufacture a verdict out of (0, 0)."""
    monkeypatch.setattr(LQW, "main_display_points", lambda: (0, 0))
    monkeypatch.setattr(LQW, "cg_windows", lambda pid=None, owner=None: [_win(0, 0, 4000, 4000)])
    assert LQW.fullscreen_verdict(999) is None
    assert LQW.offscreen_verdict(999) is None


# ── down(): the teardown ledger ───────────────────────────────────────────────────────────────────
def test_down_reports_every_restore_and_skip_in_the_stopped_metadata(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "sandbox.json").write_text(json.dumps({
        "run": "r1", "win": [1280, 700], "qa": "http://127.0.0.1:8972",
        "pids": {"engine": 101, "player": 102}, "pgids": {"engine": 101, "player": 102},
        "plist_before": {"Screenmanager Fullscreen mode": "1",
                         "Screenmanager Window Position Y": "0",
                         "SomeOwnerSetting": "7"}}))
    monkeypatch.setattr(qa_sandbox, "_kill_group",
                        lambda pid, label="", expect_pgid=None:
                        "pid reused (pgid 9 != recorded 101)" if pid == 101 else "")
    monkeypatch.setattr(qa_sandbox, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(qa_sandbox, "_player_pids", lambda exe="WorldOSPlayer": set())
    monkeypatch.setattr(qa_sandbox, "_orphan_report",
                        lambda **k: {"lines": ["[sandbox] QA port 8972 free."], "leaks": [],
                                     "port": 8972, "port_pids": []})
    monkeypatch.setattr(qa_sandbox.time, "sleep", lambda *_: None)
    log: list = []
    _fake_defaults(monkeypatch, {"Screenmanager Fullscreen mode": 3,     # ours: restore
                                 "Screenmanager Window Position Y": 60,  # cosmetic: leave
                                 "SomeOwnerSetting": 9}, log)            # theirs: leave

    rc = qa_sandbox.down("r1")
    assert rc == 1, "a refused (pid-reused) kill is a leak, not a clean teardown"
    assert (rd / "sandbox.json").exists(), "failed teardown keeps metadata retryable"
    assert not (rd / "sandbox.json.stopped").exists()
    stopped = json.loads((rd / "sandbox.json").read_text())["stopped"]
    assert stopped["plist"]["restored"] == {"Screenmanager Fullscreen mode": "1"}
    assert set(stopped["plist"]["skipped"]) == {"Screenmanager Window Position Y", "SomeOwnerSetting"}
    assert any("pid reused" in leak for leak in stopped["leaks"])
    assert _writes(log) == [["defaults", "write", qa_sandbox.PLIST_DOMAIN,
                             "Screenmanager Fullscreen mode", "-int", "1"]]
    captured = capsys.readouterr()
    out = captured.out
    assert "RESTORED" in out and "LEFT AS-IS" in out and "NOT restored" in out
    assert "cleanup INCOMPLETE" in captured.err


def test_caffeinate_failure_keeps_sandbox_launchable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    monkeypatch.setattr(qa_sandbox, "owner_active_guard", lambda: None)
    monkeypatch.setattr(qa_sandbox, "_player_windowed_args", lambda *_: [
        "-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "700"])
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot", lambda: {})
    monkeypatch.setattr(LQW, "front_app", lambda: None)
    monkeypatch.setattr(LQW, "wait_for_window", lambda *args, **kwargs: True)
    monkeypatch.setattr(LQW, "restore_front", lambda *args: False)
    monkeypatch.setattr(qa_sandbox, "_qa_roots_in_app", lambda app: set())
    monkeypatch.setattr(qa_sandbox, "_wait", lambda *args, **kwargs: True)
    monkeypatch.setattr(qa_sandbox, "_assert_windowed", lambda *args, **kwargs: (1280, 700))
    app = tmp_path / "WorldOSPlayer.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "WorldOSPlayer").write_text("")
    calls: list = []

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    def fake_popen(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "/usr/bin/caffeinate":
            raise OSError("caffeinate unavailable")
        return Proc(100 if "server.py" in str(cmd) else 200)

    monkeypatch.setattr(qa_sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(qa_sandbox.subprocess, "run", lambda *args, **kwargs: _proc())
    monkeypatch.setattr(qa_sandbox.os, "getpgid", lambda pid: pid)
    meta = qa_sandbox.up("r1", campaign="room", engine_port=8866, qa_port=8972,
                         seed_cmd="seed {state}", app=app.parent.parent)
    assert meta["caffeinate_pid"] is None
    assert len(calls) == 3
    assert "caffeinate unavailable" in capsys.readouterr().err


def test_owner_guard_rechecked_before_player_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_sandbox, "ROOT", tmp_path)
    calls = {"guard": 0, "popen": [], "killed": []}

    def guard():
        calls["guard"] += 1
        if calls["guard"] == 2:
            raise SystemExit(qa_sandbox.OWNER_ACTIVE_RC)

    monkeypatch.setattr(qa_sandbox, "owner_active_guard", guard)
    monkeypatch.setattr(qa_sandbox, "_player_windowed_args", lambda *_: [
        "-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "700"])
    monkeypatch.setattr(qa_sandbox, "_plist_snapshot", lambda: {})
    monkeypatch.setattr(LQW, "front_app", lambda: None)
    monkeypatch.setattr(qa_sandbox, "_qa_roots_in_app", lambda app: set())
    monkeypatch.setattr(qa_sandbox, "_wait", lambda *args, **kwargs: True)
    app = tmp_path / "WorldOSPlayer.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "WorldOSPlayer").write_text("")

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    def fake_popen(cmd, *args, **kwargs):
        calls["popen"].append(list(cmd))
        return Proc(100)

    monkeypatch.setattr(qa_sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(qa_sandbox.subprocess, "run", lambda *args, **kwargs: _proc())
    monkeypatch.setattr(qa_sandbox.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(qa_sandbox, "_kill_group",
                        lambda pid, label="", expect_pgid=None:
                        calls["killed"].append((pid, label, expect_pgid)) or "")
    with pytest.raises(SystemExit) as ei:
        qa_sandbox.up("r1", campaign="room", engine_port=8866, qa_port=8972,
                      seed_cmd="seed {state}", app=app.parent.parent)
    assert ei.value.code == qa_sandbox.OWNER_ACTIVE_RC
    assert calls["guard"] == 2
    assert len(calls["popen"]) == 1, "the second owner check must precede player/caffeinate Popen"
    assert calls["killed"] == [(100, "engine", 100)]


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
