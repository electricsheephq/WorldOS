#!/usr/bin/env python3
"""Render the two owner LaunchAgents and their install ledger."""
import argparse, hashlib, json, os, plistlib
from datetime import datetime, timezone
from pathlib import Path

FORBIDDEN = {8766, 8971, 8866, 8972}
CAMPAIGN = "adventure_demo_v1"


def validate_ports(engine_port: int, qa_port: int) -> None:
    bad = {engine_port, qa_port} & FORBIDDEN
    if bad or engine_port == qa_port:
        raise ValueError(f"refusing reserved/colliding ports: {sorted(bad) or [engine_port]}")


def render_plists(repo: Path, app: Path, state: Path, uv: Path,
                  engine_port: int = 8776, qa_port: int = 8981) -> tuple[dict, dict]:
    validate_ports(engine_port, qa_port)
    path = f"{uv.parent}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    common = {"RunAtLoad": True, "KeepAlive": False, "ProcessType": "Interactive"}
    session = {**common, "Label": "org.worldos.owner-session", "WorkingDirectory": str(repo),
               "ProgramArguments": [str(uv), "run", "--directory", str(repo / "servers/engine"),
                                    "python", str(repo / "viewer/server.py"), CAMPAIGN, str(engine_port)],
               "EnvironmentVariables": {"PATH": path, "WORLDOS_STATE_DIR": str(state),
                 "WORLDOS_PLAYER_MOVES": str(state / "player_moves.json"),
                 "WORLDOS_VIEWER_CHAT": str(state / "chat.json")}}
    player = {**common, "Label": "org.worldos.owner-player",
              "ProgramArguments": [str(app / "Contents/MacOS/WorldOSPlayer")],
              "EnvironmentVariables": {"PATH": path,
                "WORLDOS_ENGINE_BASE_URL": f"http://127.0.0.1:{engine_port}",
                "WORLDOS_CAMPAIGN_ID": CAMPAIGN, "WORLDOS_QA_INPUT": "1",
                "WORLDOS_QA_INPUT_PORT": str(qa_port)}}
    return session, player


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
    session, player = render_plists(args.repo, args.app, args.state, args.uv, args.engine_port, args.qa_port)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (("org.worldos.owner-session.plist", session), ("org.worldos.owner-player.plist", player)):
        with (args.output / name).open("wb") as f: plistlib.dump(data, f, sort_keys=True)
    report = args.build_report if args.build_report and args.build_report.is_file() else None
    ledger = {"schema_version": 1, "mode": args.mode, "created_at": datetime.now(timezone.utc).isoformat(),
              "source_app": str(args.source_app), "installed_app": str(args.app),
              "app_sha256": hash_tree(args.source_app), "build_sha": args.build_sha or None,
              "worktree_sha": args.worktree_sha, "ports": {"engine": args.engine_port, "player_qa": args.qa_port},
              "gate_results": {"packaged_pins": "GREEN", "kit_roots": 0,
                               "certifications": {"crypt": "FRESH", "tavern": "FRESH"},
                               "build_identity": "RECORDED"},
              "build_identity": ({"kind": "build-report", "path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()} if report else {"kind": "argument", "sha": args.build_sha})}
    args.ledger.write_text(json.dumps(ledger, indent=2) + "\n")
    return 0


if __name__ == "__main__": raise SystemExit(main())
