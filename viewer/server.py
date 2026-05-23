#!/usr/bin/env python3
"""ClawDnD read-only play-view — a local web projection of campaign state (P3.6).

Run it for the PLAYER to *see* the adventure while they play through Claude Code:
the current location/map, party vitals, who's in the scene (with voices), the
quest log, and a live roll/event feed. The AI never reads this — it reads the
same state via the engine's MCP tools. This server is a **pure downstream
reader**: stdlib only (no deps, runs anywhere), read-only, no POST/write path,
and it can be deleted without touching the engine.

It reads the engine's on-disk truth directly:
- `snapshot.json` is written atomically (temp + os.replace), so reads are always
  a whole, valid file — no lock needed.
- the active session's `sessions/<id>.jsonl` is append-only; we tolerate a
  half-written trailing line (skip it; it completes on the next poll).

Usage:  python3 viewer/server.py [campaign_id] [port]
        (CLAWDND_STATE_DIR is honored, mirroring the engine's store.state_dir())
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent


def _state_dir() -> Path:
    """Mirror servers/engine/store.state_dir(): $CLAWDND_STATE_DIR or ~/.clawdnd/state."""
    env = os.environ.get("CLAWDND_STATE_DIR")
    return Path(env) if env else Path.home() / ".clawdnd" / "state"


def _campaigns_dir() -> Path:
    return _state_dir() / "campaigns"


def _pick_campaign(arg: str | None) -> str | None:
    if arg:
        return arg
    cdir = _campaigns_dir()
    if not cdir.is_dir():
        return None
    snaps = [(p.parent.name, p.stat().st_mtime) for p in cdir.glob("*/snapshot.json")]
    return max(snaps, key=lambda x: x[1])[0] if snaps else None


def _campaign_dir(campaign_id: str) -> Path:
    return _campaigns_dir() / campaign_id


def _read_snapshot(campaign_id: str) -> dict:
    snap = _campaign_dir(campaign_id) / "snapshot.json"
    if not snap.exists():
        return {}
    try:
        return json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_events(campaign_id: str, since: int) -> tuple[list[dict], int]:
    """Return (new story entries after line `since`, new line count). Drops a
    trailing partial line defensively (append-only writes can exceed PIPE_BUF)."""
    snap = _read_snapshot(campaign_id)
    sid = snap.get("active_session_id") or (snap.get("session_ids") or [None])[-1]
    if not sid:
        return [], since
    log = _campaign_dir(campaign_id) / "sessions" / f"{sid}.jsonl"
    if not log.exists():
        return [], since
    lines = log.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    consumed = since  # advance the cursor only past lines we actually finish
    for raw in lines[since:]:
        stripped = raw.strip()
        if not stripped:
            consumed += 1
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError:
            break  # half-written trailing line — DON'T advance past it; re-read next poll
        consumed += 1
    return out, consumed


class _Handler(BaseHTTPRequestHandler):
    campaign_id = ""  # set on the class before serving

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            html = (_HERE / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/state":
            self._json(_read_snapshot(self.campaign_id))
        elif route == "/events":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            entries, nxt = _read_events(self.campaign_id, since)
            self._json({"entries": entries, "next": nxt})
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *_args) -> None:  # quiet
        pass


def main() -> int:
    campaign_id = _pick_campaign(sys.argv[1] if len(sys.argv) > 1 else None)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    if not campaign_id:
        print(f"No campaign found under {_campaigns_dir()} — start one first.", file=sys.stderr)
        return 1
    _Handler.campaign_id = campaign_id
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"ClawDnD play-view: http://127.0.0.1:{port}  (campaign: {campaign_id})")
    print("Read-only projection of the live campaign — Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
