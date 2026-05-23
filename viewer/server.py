#!/usr/bin/env python3
"""ClawDnD read-only play-view — a local web projection of campaign state (P3.6).

Run it for the PLAYER to *see* the adventure while they play through Claude Code:
the current location/map, party vitals, who's in the scene (with voices), the
quest log, and a live roll/event feed. The AI never reads this — it reads the
same state via the engine's MCP tools. This server is a **pure downstream
reader**: stdlib only (no deps, runs anywhere). The sole write path is `POST /move`,
which appends a player *move intent* (NOT campaign state) to the append-only log at
$CLAWDND_PLAYER_MOVES — and is inert (refuses, writes nothing) unless that env is set.
It can be deleted without touching the engine.

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


def _moves_path() -> Path | None:
    """The single write target: $CLAWDND_PLAYER_MOVES, an append-only log of player
    *move intents* (NOT campaign state). Unset ⇒ no live game ⇒ no write path."""
    env = os.environ.get("CLAWDND_PLAYER_MOVES")
    return Path(env) if env else None


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


def _activity_items(obj: dict) -> list[dict]:
    """Flatten one stream-json event into watchable activity items: an agent's
    narration (assistant text), each tool call it makes, and the turns fed to it
    (user/player messages). Tool-result and system noise is dropped."""
    items: list[dict] = []
    t = obj.get("type")
    msg = obj.get("message") or {}
    if t == "assistant":
        for blk in msg.get("content") or []:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text" and (blk.get("text") or "").strip():
                items.append({"kind": "narration", "label": "DM", "detail": blk["text"].strip()})
            elif blk.get("type") == "tool_use":
                name = (blk.get("name") or "").split("__")[-1]
                inp = json.dumps(blk.get("input") or {}, separators=(",", ":"))
                items.append({"kind": "tool", "label": name, "detail": inp[:160]})
    # (user-type events in a --resume stream are tool-results / skill-system noise,
    #  not the player agent's turns — the orchestrator's prompts aren't echoed —
    #  so we surface only the agent's tool calls + narration here. A true two-sided
    #  player+DM activity log is a follow-up that emits both agents' turns.)
    return items


def _read_activity(since: int) -> tuple[list[dict], int]:
    """Tail the configured agent transcript (a stream-json .jsonl, e.g. a QA run's),
    returning new activity items after line `since`. Mirrors _read_events' tolerance
    of a half-written trailing line. Empty when no transcript is configured."""
    path = _Handler.transcript_path
    if not path:
        return [], since
    f = Path(path)
    if not f.exists():
        return [], since
    lines = f.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    consumed = since
    for raw in lines[since:]:
        stripped = raw.strip()
        if not stripped:
            consumed += 1
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            break  # partial trailing line — re-read next poll
        out.extend(_activity_items(obj))
        consumed += 1
    return out, consumed


def _read_chat(since: int) -> tuple[list[dict], int]:
    """Tail the two-sided conversation log (a duo run's <run>.chat.jsonl: one
    {"role":"player"|"dm","text":...} per line) so the dashboard can show the
    PROTAGONIST acting alongside the DM, not just DM narration. Empty when none
    is configured. Tolerates a half-written trailing line like the other tails."""
    path = _Handler.chat_path
    if not path or not Path(path).exists():
        return [], since
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    consumed = since
    for raw in lines[since:]:
        stripped = raw.strip()
        if not stripped:
            consumed += 1
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError:
            break
        consumed += 1
    return out, consumed


class _Handler(BaseHTTPRequestHandler):
    campaign_id = ""  # set on the class before serving
    transcript_path = ""  # optional agent-transcript .jsonl to tail for /activity
    chat_path = ""  # optional two-sided <run>.chat.jsonl to tail for /chat

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
        elif route in ("/dashboard", "/dashboard.html"):
            html = (_HERE / "dashboard.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/state":
            self._json(_read_snapshot(self.campaign_id))
        elif route == "/events":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            entries, nxt = _read_events(self.campaign_id, since)
            self._json({"entries": entries, "next": nxt})
        elif route == "/activity":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            items, nxt = _read_activity(since)
            self._json({"items": items, "next": nxt, "live": bool(self.transcript_path)})
        elif route == "/chat":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            items, nxt = _read_chat(since)
            self._json({"items": items, "next": nxt, "live": bool(self.chat_path)})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        """The ONLY write path. `/move` appends one player *move intent* (a JSON
        line) to $CLAWDND_PLAYER_MOVES — mirroring the engine's player facade, NOT
        touching campaign state. No env ⇒ no live game ⇒ refuse and write nothing."""
        if urlparse(self.path).path != "/move":
            self._send(404, b"not found", "text/plain")
            return
        dest = _moves_path()
        if dest is None:
            self._json({"ok": False, "reason": "read-only (no live game)"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            move = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "reason": "bad move payload"})
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(move, separators=(",", ":")) + "\n")
        except OSError as exc:
            self._json({"ok": False, "reason": f"write failed: {exc}"})
            return
        self._json({"ok": True})

    def log_message(self, *_args) -> None:  # quiet
        pass


def main() -> int:
    campaign_id = _pick_campaign(sys.argv[1] if len(sys.argv) > 1 else None)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    if not campaign_id:
        print(f"No campaign found under {_campaigns_dir()} — start one first.", file=sys.stderr)
        return 1
    _Handler.campaign_id = campaign_id
    # Optional agent-transcript to tail in the "Agent activity" panel — point it at a
    # QA run's stream-json (e.g. qa/transcripts/<run>.jsonl) to WATCH the agents play.
    _Handler.transcript_path = os.environ.get("CLAWDND_VIEWER_TRANSCRIPT") or (
        sys.argv[3] if len(sys.argv) > 3 else ""
    )
    # Optional two-sided conversation log to show the player+DM exchange in the chat.
    _Handler.chat_path = os.environ.get("CLAWDND_VIEWER_CHAT") or ""
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"ClawDnD play-view: http://127.0.0.1:{port}  (campaign: {campaign_id})")
    if _Handler.transcript_path:
        print(f"Watching agent transcript: {_Handler.transcript_path}")
    moves = _moves_path()
    print(
        f"Player moves → appending to: {moves}"
        if moves
        else "Player moves: DISABLED (set CLAWDND_PLAYER_MOVES to enable POST /move)."
    )
    print("Downstream projection of the live campaign — Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
