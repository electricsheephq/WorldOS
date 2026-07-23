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

Caveats:
- Two player instances share Application Support/com.worldos.WorldOSPlayer — /shot writes collide.
  Don't run owner-instance probes and a sandbox sweep at the same moment.
- 16 GB host: ONE sandbox at a time; always teardown (the overnight-loop RAM discipline).

Usage:
  qa/qa_sandbox.py up   --run scale1            # seed + engine + player; prints the endpoints
  qa/qa_sandbox.py status --run scale1
  qa/qa_sandbox.py down --run scale1            # kill own pids; state/logs kept for evidence
  # or via the orchestrator:  qa/room_pipeline.py --room crypt --sandbox
Custom seed (new rooms): --seed-cmd "uv run --directory servers/engine python qa/my_seed.py {state}"
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = Path(os.environ.get("WORLDOS_QA_SANDBOX_ROOT", "/tmp/worldos-qa-sandbox"))
DEFAULT_APP = Path(os.environ.get("WORLDOS_PLAYER_APP",
                                  str(Path.home() / "Applications" / "WorldOSPlayer.app")))
DEFAULT_CAMPAIGN = "registered_world_v1"   # what qa/seed_gfx_registered_world.py seeds
ENGINE_PORT, QA_PORT = 8866, 8972          # owner runs 8766/8971 — never collide


def _http_ok(url: str, post: bool = False, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, data=b"{}" if post else None,
                                     headers={"Content-Type": "application/json"} if post else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def _wait(label: str, url: str, *, post: bool, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _http_ok(url, post=post):
            return True
        time.sleep(2)
    print(f"[sandbox] TIMEOUT waiting for {label} ({url})", file=sys.stderr)
    return False


def _rundir(run: str) -> Path:
    return ROOT / run


def _meta_path(run: str) -> Path:
    return _rundir(run) / "sandbox.json"


def _qa_roots_in_app(app: Path) -> set:
    """Names of QA kit-scene roots (KitRoom_*) baked into the app's level files.

    build_room_kit.cs assembles kit rooms in the open scene; a capture flow that saves the scene
    bakes them into the next build, which then draws grey kit masses in front of every plate.
    Unity level files store GameObject names as plain bytes, so a byte scan is a reliable,
    dependency-free detector (the same check as `strings level0 | grep KitRoom_`).
    """
    import re
    found: set = set()
    data_dir = app / "Contents" / "Resources" / "Data"
    for lvl in sorted(data_dir.glob("level*")):
        try:
            found.update(m.decode() for m in re.findall(rb"KitRoom_[A-Za-z0-9_]+", lvl.read_bytes()))
        except OSError:
            continue
    # child helper objects (KitRoom_Fire etc.) ride along with a real root; the root name is the signal
    return {n for n in found if not n.startswith(("KitRoom_Fire", "KitRoom_TombGlow", "KitRoom_CoolKey"))} or found


def up(run: str, *, campaign: str, engine_port: int, qa_port: int,
       seed_cmd: str, app: Path) -> dict:
    rd = _rundir(run)
    state = rd / "state"
    if _meta_path(run).exists():
        raise SystemExit(f"[sandbox] run '{run}' already provisioned — `down` it first ({rd})")
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
    if not _wait("engine", f"http://127.0.0.1:{engine_port}/combat-surface", post=False, timeout_s=90):
        engine.terminate()
        raise SystemExit(f"[sandbox] engine never came up — see {rd/'engine.log'}")

    # 3) a SECOND player instance — direct binary exec (open -n drops env), own QA port.
    #    ⚠ never `osascript quit app` here (kills the OWNER's instance too) — pid-scoped only.
    pbin = app / "Contents" / "MacOS" / "WorldOSPlayer"
    if not pbin.exists():
        engine.terminate()
        raise SystemExit(f"[sandbox] player binary missing: {pbin}")
    contaminated = _qa_roots_in_app(app)
    if contaminated:
        engine.terminate()
        raise SystemExit(
            f"[sandbox] app CONTAMINATED — QA kit roots baked into the build: {sorted(contaminated)} "
            f"({app}). A KitRoom_* scene root was saved into the canonical scene at build time; it "
            f"renders grey kit masses over every plate (kit-tavern 2026-07-23). Rebuild via "
            f"BuildMacOSPlayer (auto-strips + reports strippedQARoots in build-report.txt).")
    penv = dict(os.environ,
                WORLDOS_ENGINE_BASE_URL=f"http://127.0.0.1:{engine_port}",
                WORLDOS_CAMPAIGN_ID=campaign,
                WORLDOS_QA_INPUT="1",
                WORLDOS_QA_INPUT_PORT=str(qa_port))
    ply_log = open(rd / "player.log", "w")  # noqa: SIM115
    # caffeinate -disu: a background/occluded sandbox player can App-Nap -> throttled rendering ->
    # delayed /shot frames and glide stalls mid-sweep (sidecar review). caffeinate exits with the child.
    player = subprocess.Popen(["/usr/bin/caffeinate", "-disu", str(pbin)], env=penv, stdout=ply_log,
                              stderr=subprocess.STDOUT, start_new_session=True)
    if not _wait("player QA channel", f"http://127.0.0.1:{qa_port}/debug", post=True, timeout_s=120):
        player.terminate()
        engine.terminate()
        raise SystemExit(f"[sandbox] player QA channel never came up — see {rd/'player.log'}")

    meta = {"run": run, "campaign": campaign, "state": str(state),
            "engine": f"http://127.0.0.1:{engine_port}", "qa": f"http://127.0.0.1:{qa_port}",
            "pids": {"engine": engine.pid, "player": player.pid}}
    _meta_path(run).write_text(json.dumps(meta, indent=2) + "\n")
    print(f"[sandbox] UP — engine {meta['engine']}  qa {meta['qa']}  (pids {meta['pids']})")
    return meta


def down(run: str) -> None:
    mp = _meta_path(run)
    if not mp.exists():
        print(f"[sandbox] no sandbox.json for '{run}' — nothing to stop")
        return
    meta = json.loads(mp.read_text())
    for name, pid in meta.get("pids", {}).items():
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(pid), sig)   # own session group only (start_new_session)
                time.sleep(1.5)
            except ProcessLookupError:
                break
            except PermissionError:
                print(f"[sandbox] cannot signal {name} pid {pid}")
                break
        print(f"[sandbox] stopped {name} (pid {pid})")
    mp.rename(mp.with_suffix(".json.stopped"))
    print(f"[sandbox] DOWN — state/logs kept at {_rundir(run)} for evidence")


def status(run: str) -> dict:
    mp = _meta_path(run)
    if not mp.exists():
        return {"run": run, "up": False}
    meta = json.loads(mp.read_text())
    meta["engine_ok"] = _http_ok(meta["engine"] + "/combat-surface")
    meta["qa_ok"] = _http_ok(meta["qa"] + "/debug", post=True)
    meta["up"] = meta["engine_ok"] and meta["qa_ok"]
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "down", "status"])
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
        down(args.run)
    else:
        print(json.dumps(status(args.run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
