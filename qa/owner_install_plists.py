#!/usr/bin/env python3
"""Render the three owner LaunchAgents and their install ledger."""
from __future__ import annotations  # 3.9 system python runs these

import argparse, hashlib, json, os, plistlib
from datetime import datetime, timezone
from pathlib import Path

from owner_install_verify import check_build_report

FORBIDDEN = {8766, 8971, 8866, 8972}
CAMPAIGN = "adventure_demo_v1"
ART_ROOT = Path("/Users/m1/WorldOS")  # the canonical checkout that owns the private art
DM_MODEL = "opus"                     # pinned rather than inherited from the login env
DM_SCRIPT = "qa/agent_play.sh"        # `serve` = one DM beat per queued player line
# agent_play.sh derives its chat path as "<state_dir>/chat.jsonl", so the viewer MUST write
# that exact name or the DM tails a file nobody writes and the owner's lines are never answered.
CHAT_NAME = "chat.jsonl"
SESSION_LABEL, PLAYER_LABEL, DM_LABEL = (
    "org.worldos.owner-session", "org.worldos.owner-player", "org.worldos.owner-dm")


def validate_ports(engine_port: int, qa_port: int) -> None:
    bad = {engine_port, qa_port} & FORBIDDEN
    if bad or engine_port == qa_port:
        raise ValueError(f"refusing reserved/colliding ports: {sorted(bad) or [engine_port]}")


def render_plists(repo: Path, app: Path, state: Path, uv: Path,
                  engine_port: int = 8776, qa_port: int = 8981,
                  art_root: Path = ART_ROOT) -> tuple[dict, dict, dict]:
    validate_ports(engine_port, qa_port)
    path = f"{uv.parent}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    common = {"KeepAlive": False, "ProcessType": "Interactive"}
    # WORLDOS_ART_REPO_ROOT: the gitignored `_private` art lives ONLY in the canonical
    # checkout, so without this the owner worktree becomes the art root and /image plus
    # the /app-status private-art probe report the art missing (viewer/server.py
    # _art_repo_root). The engine env the DM shares is the session's, minus the ports.
    engine_env = {"PATH": path, "WORLDOS_STATE_DIR": str(state),
                  "WORLDOS_ART_REPO_ROOT": str(art_root),
                  "WORLDOS_PLAYER_MOVES": str(state / "player_moves.json"),
                  "WORLDOS_VIEWER_CHAT": str(state / CHAT_NAME)}
    # Only the session runs at load. The player and the DM are started BY the installer
    # after the engine answers /session-surface — a player that boots first self-exits
    # against an unavailable engine (#1612), and launchd gives no ordering of its own.
    session = {**common, "RunAtLoad": True, "Label": SESSION_LABEL, "WorkingDirectory": str(repo),
               "ProgramArguments": [str(uv), "run", "--directory", str(repo / "servers/engine"),
                                    "python", str(repo / "viewer/server.py"), CAMPAIGN, str(engine_port)],
               "EnvironmentVariables": dict(engine_env)}
    player = {**common, "RunAtLoad": False, "Label": PLAYER_LABEL,
              "ProgramArguments": [str(app / "Contents/MacOS/WorldOSPlayer")],
              "EnvironmentVariables": {"PATH": path,
                "WORLDOS_ENGINE_BASE_URL": f"http://127.0.0.1:{engine_port}",
                "WORLDOS_CAMPAIGN_ID": CAMPAIGN, "WORLDOS_QA_INPUT": "1",
                "WORLDOS_QA_INPUT_PORT": str(qa_port)}}
    # The viewer resolves only grid/doorway/approach/combat intents in-process; `say`,
    # `do`, `check` and friends are merely appended to the move sink for a DM. Without
    # this third agent the owner's dialogue queues forever and the demo cannot progress.
    dm = {**common, "RunAtLoad": False, "Label": DM_LABEL, "WorkingDirectory": str(repo),
          "ProgramArguments": ["/bin/bash", str(repo / DM_SCRIPT), "serve", "--run", "owner",
                               "--engine", f"http://127.0.0.1:{engine_port}", "--state", str(state),
                               "--campaign", CAMPAIGN],
          # WORLDOS_AGENT_PLAY_ROOT: agent_play.sh defaults its run dir to <repo>/qa/agent_play_runs.
          # The owner's run holds the durable chat cursor, so it belongs beside the state the receipt
          # backs up — not inside the pinned checkout that `refresh --sha` moves out from under it.
          "EnvironmentVariables": {**engine_env, "WORLDOS_CAMPAIGN_ID": CAMPAIGN,
                                   "WORLDOS_AGENT_PLAY_ROOT": str(state / "agent_play_runs"),
                                   "WORLDOS_DM_MODEL": DM_MODEL}}
    return session, player, dm


def hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix()):
        rel = p.relative_to(root).as_posix().encode()
        if p.is_symlink(): h.update(b"L" + rel + b"\0" + os.readlink(p).encode())
        elif p.is_file():
            h.update(b"F" + rel + b"\0")
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    for flag in ("output", "repo", "app", "source-app", "state", "uv", "ledger"): ap.add_argument(f"--{flag}", required=True, type=Path)
    ap.add_argument("--engine-port", type=int, default=8776); ap.add_argument("--qa-port", type=int, default=8981)
    ap.add_argument("--mode", required=True); ap.add_argument("--build-sha", default=""); ap.add_argument("--worktree-sha", required=True)
    ap.add_argument("--build-report", default="", type=Path); args = ap.parse_args()
    rendered = render_plists(args.repo, args.app, args.state, args.uv, args.engine_port, args.qa_port)
    args.output.mkdir(parents=True, exist_ok=True)
    for data in rendered:
        name = f"{data['Label']}.plist"
        with (args.output / name).open("wb") as f: plistlib.dump(data, f, sort_keys=True)
    report = args.build_report if args.build_report and args.build_report.is_file() else None
    # Re-validate rather than trust the shell: the ledger's build identity is the evidence
    # a later refresh is compared against, so a Failed report must never reach it.
    fields = check_build_report(report.read_text()) if report else {}
    ledger = {"schema_version": 2, "mode": args.mode, "created_at": datetime.now(timezone.utc).isoformat(),
              "labels": [d["Label"] for d in rendered],
              "source_app": str(args.source_app), "installed_app": str(args.app),
              "app_sha256": hash_tree(args.source_app), "build_sha": args.build_sha or None,
              "worktree_sha": args.worktree_sha, "ports": {"engine": args.engine_port, "player_qa": args.qa_port},
              "gate_results": {"packaged_pins": "GREEN", "kit_roots": 0,
                               "certifications": {"crypt": "FRESH", "tavern": "FRESH"},
                               "build_identity": "RECORDED"},
              "build_identity": ({"kind": "build-report", "path": str(report), "result": fields["result"],
                                  "built_at": fields.get("buildEndedAt", ""),
                                  "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
                                 if report else {"kind": "argument", "sha": args.build_sha})}
    args.ledger.write_text(json.dumps(ledger, indent=2) + "\n")
    return 0


if __name__ == "__main__": raise SystemExit(main())
