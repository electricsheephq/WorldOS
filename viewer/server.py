#!/usr/bin/env python3
"""ClawDnD read-only play-view — a local web projection of campaign state (P3.6).

Run it for the PLAYER to *see* the adventure while they play through Claude Code:
the current location/map, party vitals, who's in the scene (with voices), the
quest log, and a live roll/event feed. The AI never reads this — it reads the
same state via the engine's MCP tools. This server is a **pure downstream
reader**: stdlib only (no deps, runs anywhere). It has exactly two side effects,
both opt-in and local:
- `POST /move` appends a player *move intent* (NOT campaign state) to the
  append-only log at $CLAWDND_PLAYER_MOVES — inert (refuses, writes nothing)
  unless that env is set.
- `POST /speak` shells out to the existing voice server (servers/voice) to
  synthesize + play one line of narration audio. It NEVER writes game state and
  NEVER hangs the page: it returns audio-or-null cleanly (ok:false when the voice
  backend is null/unavailable). It can be deleted without touching the engine.

It reads the engine's on-disk truth directly:
- `snapshot.json` is written atomically (temp + os.replace), so reads are always
  a whole, valid file — no lock needed.
- the active session's `sessions/<id>.jsonl` is append-only; we tolerate a
  half-written trailing line (skip it; it completes on the next poll).

Usage:  python3 viewer/server.py [campaign_id] [port]
        (CLAWDND_STATE_DIR is honored, mirroring the engine's store.state_dir())
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent
# servers/voice lives two levels up from viewer/ (repo root / servers / voice).
_VOICE_DIR = _HERE.parent / "servers" / "voice"

# The constrained move palette — the SAME lane the engine facade enforces. A human
# acting via the dashboard must not be able to POST DM-side narration ("the dragon
# dies"): only declared PLAYER moves of a known kind are accepted (H5). These are the
# kinds the dashboard emits (say/do free-text, check/save/combat/attack palette) plus
# the facade's cast/use_item.
_MOVE_KINDS = {"say", "do", "check", "save", "combat", "attack", "cast", "use_item"}
_MOVE_FIELDS = ("text", "name", "skill", "target", "weapon", "dc")
_MOVE_MAXLEN = 2000


def sanitize_move(raw: object) -> tuple[Optional[dict], str]:
    """Validate + normalize a /move payload to the constrained palette. Returns
    ``(move, "")`` on success or ``(None, reason)`` on rejection. role is forced to
    'player' (no impersonating dm/system); kind must be whitelisted; text/name are
    length-capped; unknown keys are dropped (so a 'narration' overwrite can't ride
    along in an extra field)."""
    if not isinstance(raw, dict):
        return None, "move must be a JSON object"
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in _MOVE_KINDS:
        return None, f"unknown move kind {kind!r}"
    move: dict = {"role": "player", "kind": kind}
    for f in _MOVE_FIELDS:
        v = raw.get(f)
        if isinstance(v, str) and v.strip():
            move[f] = v.strip()[:_MOVE_MAXLEN]
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            move[f] = v
    if "text" not in move and "name" not in move:
        return None, "move needs a 'text' or 'name'"
    return move, ""


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


def _live_play() -> bool:
    """True when the dashboard's action layer (Say/Do/Continue + the dice/skill/save/
    combat palette + click-to-travel) can actually land a move — i.e. POST /move will
    accept it. That requires $CLAWDND_PLAYER_MOVES to be set AND its target writable
    (the sink dir exists-or-can-be-made and is writable, or the file already exists and
    is writable). When false the dashboard is the read-only "director's view" and grays
    the palette out instead of letting clicks silently fail. Mirrors the do_POST /move
    gate exactly (dest is None ⇒ refuse) plus a writability probe — no engine import."""
    dest = _moves_path()
    if dest is None:
        return False
    try:
        if dest.exists():
            return os.access(dest, os.W_OK)
        # File not created yet: do_POST mkdirs the parent then appends, so the live
        # check passes when the nearest existing ancestor is a writable directory.
        probe = dest.parent
        while True:
            if probe.exists():
                return probe.is_dir() and os.access(probe, os.W_OK)
            if probe.parent == probe:  # reached filesystem root without an existing dir
                return False
            probe = probe.parent
    except OSError:
        return False


# ---- image cache projection (#34 / S2.2) -------------------------------------
# The engine's imagegen layer writes a small JSON *descriptor* per generated image
# under <state_dir>/images/<scope>/<hash>.json (see servers/engine/imagegen.py),
# carrying one of: "path" (a file on disk), "url", or "bytes_b64"+"mime_type". We
# stay a pure downstream reader of that derived cache: stdlib only, NEVER importing
# the engine. GET /image?scope=<scope> finds the most-recent descriptor for a scope
# and serves the pixels; absent scope/descriptor → 404 so the dashboard falls back
# to its luxe placeholder.

def _safe_scope(scope: Optional[str]) -> str:
    """Reduce a caller-supplied scope id to a single safe path segment, mirroring
    imagegen._safe_scope so we resolve the same cache dir AND can't be walked out of
    images/ via path traversal (only alnum/-/_ survive; length-capped)."""
    if not scope:
        return ""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(scope))[:128]


def _images_dir(scope: Optional[str]) -> Path:
    """Cache dir for one scope: <state_dir>/images/<safe-scope>. Mirrors
    imagegen._images_dir (which roots at store.state_dir()/images)."""
    root = _state_dir() / "images"
    seg = _safe_scope(scope)
    return root / seg if seg else root


def _latest_descriptor(scope: Optional[str]) -> Optional[dict]:
    """Most-recently-written *.json descriptor under the scope's cache dir, parsed.
    Returns None when the scope dir is absent, holds no descriptors, or the newest
    one won't parse (the cache is rebuildable, never load-bearing — a bad entry is
    just a miss, exactly like imagegen.cache_read)."""
    seg = _safe_scope(scope)
    if not seg:
        return None
    cdir = _images_dir(scope)
    if not cdir.is_dir():
        return None
    newest: Optional[Path] = None
    newest_mtime = -1.0
    for p in cdir.glob("*.json"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_mtime:
            newest, newest_mtime = p, m
    if newest is None:
        return None
    try:
        d = json.loads(newest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return d if isinstance(d, dict) else None


def _campaign_recency(snap_path: Path) -> float:
    """Most-recent-activity time for a campaign (#38: auto-follow the live run).

    Prefer the active session log's mtime — that's what advances turn-by-turn while
    you play — and fall back to the snapshot's mtime (a fresh campaign with no
    session yet). Taking the max of the two means a campaign whose snapshot was
    written once but whose session is being appended to *right now* still sorts to
    the top, so the viewer follows wherever the story is actually moving."""
    cdir = snap_path.parent
    best = snap_path.stat().st_mtime
    sessions = cdir / "sessions"
    if sessions.is_dir():
        for log in sessions.glob("*.jsonl"):
            try:
                best = max(best, log.stat().st_mtime)
            except OSError:
                pass
    return best


def _pick_campaign(arg: str | None) -> str | None:
    """Resolve which campaign to project. An explicit arg wins; otherwise pick the
    most-recently-ACTIVE campaign by recency (#38) so launching the viewer follows
    whatever run is live without a relaunch. Snapshots that fail to parse are
    skipped so a half-written/corrupt one can't win the race and blank the view."""
    if arg:
        return arg
    cdir = _campaigns_dir()
    if not cdir.is_dir():
        return None
    snaps: list[tuple[str, float]] = []
    for p in cdir.glob("*/snapshot.json"):
        try:
            if not json.loads(p.read_text(encoding="utf-8")):
                continue  # empty/`{}` snapshot — nothing to show; don't let it win
        except (json.JSONDecodeError, OSError):
            continue
        snaps.append((p.parent.name, _campaign_recency(p)))
    return max(snaps, key=lambda x: x[1])[0] if snaps else None


def _campaign_dir(campaign_id: str) -> Path:
    return _campaigns_dir() / campaign_id


def _safe_campaign_id(campaign_id: Optional[str]) -> Optional[str]:
    """Validate a caller-supplied ?campaign id and return the real existing campaign
    dir's name, or None. Reuses the same path-containment guard /image uses for `path`:
    resolve the candidate dir and confirm it sits *directly under* the campaigns dir (so
    a tampered id like '../../etc' or 'a/b' can't escape it). Empty/unknown ⇒ None so the
    caller falls back to the lazily-attached campaign. Read-only: just a filesystem check."""
    if not campaign_id:
        return None
    root = _campaigns_dir()
    try:
        cand = (root / campaign_id).resolve()
        # must be an existing dir whose PARENT is exactly the campaigns dir (no traversal,
        # no nesting) — mirrors the _serve_image containment check.
        if cand.is_dir() and cand.parent == root.resolve():
            return cand.name
    except OSError:
        return None
    return None


def _list_campaigns() -> list[dict]:
    """All projectable campaigns under the campaigns dir, newest-active first (#H3 switcher).

    One entry per campaign: {id, name, day, last_played, current}. `current` marks the
    one currently *attached* (_Handler.campaign_id). `name` is the snapshot's world title
    (falling back to the dir id). Empty/unparseable snapshots are skipped — the SAME guard
    _pick_campaign uses, so a half-written/`{}` snapshot never shows as a pickable game.
    Sorted by recency descending. Pure reader: no writes, no engine import."""
    cdir = _campaigns_dir()
    out: list[dict] = []
    if not cdir.is_dir():
        return out
    attached = _Handler.campaign_id
    for snap in cdir.glob("*/snapshot.json"):
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not data:
            continue  # empty/`{}` snapshot — nothing to show (mirror _pick_campaign)
        cid = snap.parent.name
        out.append({
            "id": cid,
            "name": str(data.get("title") or cid),
            "day": data.get("day"),
            "last_played": _campaign_recency(snap),
            "current": cid == attached,
        })
    out.sort(key=lambda c: c["last_played"], reverse=True)
    return out


def _read_snapshot(campaign_id: str) -> dict:
    snap = _campaign_dir(campaign_id) / "snapshot.json"
    if not snap.exists():
        return {}
    try:
        return json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _viewer_config() -> dict:
    """Read-only runtime facts for the quick-settings modal — voice backend + whether
    the voice server is present, and whether a live move sink is configured. Pure
    reader: no writes, no engine import, just env + filesystem the viewer already
    knows. Campaign settings (pacing_mode, leveling_mode) come from /state instead."""
    backend = os.environ.get("CLAWDND_TTS_BACKEND", "kokoro").strip().lower() or "kokoro"
    voice_ready = _VOICE_DIR.is_dir() and backend != "null"
    return {
        "voice": {"backend": backend, "ready": voice_ready},
        # The engine's image provider runs server-side; the viewer can only say whether
        # any cached art exists for this state dir (a non-empty images/ tree).
        "image": {"cache_present": (_state_dir() / "images").is_dir()},
        "moves_enabled": _moves_path() is not None,
    }


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


# Roll-result detection (#35): the dice tool's *result* (total / nat-d20 / crit)
# lives in the tool_RESULT, not the tool_use input — so to headline a real number
# with crit/miss coloring we mine results too. A roll result is a JSON dict that
# carries a numeric `total` plus the tell-tale `expression`+`rolls` shape.
def _parse_roll_result(content: object) -> dict | None:
    """Extract a roll outcome from a tool_result's content (str or text blocks).
    Returns a compact dict the dice widget can headline, or None if it isn't a
    roll. Stays defensive: anything that doesn't look exactly like the engine's
    roll payload is ignored (so a truncated/odd result never throws)."""
    if isinstance(content, list):
        text = "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    else:
        text = content if isinstance(content, str) else ""
    text = text.strip()
    if not text or "total" not in text or "expression" not in text:
        return None
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict) or "total" not in d or "rolls" not in d:
        return None
    total = d.get("total")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return None
    out = {
        "kind": "roll",
        "expression": str(d.get("expression") or ""),
        "total": total,
        "natural": d.get("natural") if isinstance(d.get("natural"), int) else None,
        "crit": bool(d.get("crit")),
        "fumble": bool(d.get("fumble")),
        "detail": str(d.get("detail") or ""),
        "reason": str(d.get("reason") or "")[:140],
    }
    return out


def _activity_items(obj: dict) -> list[dict]:
    """Flatten one stream-json event into watchable activity items: an agent's
    narration (assistant text), each tool call it makes, and — for the dice widget
    — the *outcome* of roll tools (mined from tool_results). System noise is
    dropped."""
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
    elif t == "user":
        # user-type events in a --resume stream are tool-results / skill-system
        # noise — NOT the player agent's turns (the orchestrator's prompts aren't
        # echoed). We surface only one thing from them: a roll *outcome*, which
        # the dice widget headlines. Everything else here stays dropped.
        for blk in msg.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                roll = _parse_roll_result(blk.get("content"))
                if roll:
                    items.append(roll)
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


# ---- voice playback (#36) ----------------------------------------------------
# /speak shells out to the EXISTING voice server (servers/voice) rather than
# importing it: the Kokoro backend pulls in PyTorch and lives in that server's own
# venv + `kokoro` dependency group, which this stdlib-only reader must not depend
# on. `uv run --directory servers/voice` gives us the right interpreter + deps +
# import path (cwd-relative `import registry` / `from adapters... import` resolve
# there, exactly as the voice server itself and playtest_voice.py do). The backend
# is selected by CLAWDND_TTS_BACKEND, mirroring servers/voice/server.py._get_backend.

# Runs in the voice server's environment. Selects the backend the same way the voice
# server does, speaks one line (play=True ⇒ afplay on macOS), and prints ONE compact
# JSON line to stdout. Stays silent on stderr-only failures so our parse is clean.
_SPEAK_SNIPPET = r"""
import json, sys
import registry
def _backend(name):
    if name == "null":
        from adapters.null import NullBackend; return NullBackend()
    if name == "elevenlabs":
        from adapters.elevenlabs import ElevenLabsBackend; return ElevenLabsBackend()
    from adapters.kokoro import KokoroBackend; return KokoroBackend()
def main():
    raw = sys.stdin.read()
    req = json.loads(raw) if raw.strip() else {}
    text = (req.get("text") or "").strip()
    voice_id = (req.get("voice_id") or "narrator-dm").strip() or "narrator-dm"
    name = (req.get("backend") or "kokoro").strip().lower() or "kokoro"
    if not text:
        print(json.dumps({"ok": False, "played": False, "backend": name, "detail": "empty text"})); return
    b = _backend(name)
    voice = registry.resolve(voice_id, b.name)
    r = b.speak(text, voice, play=True)
    print(json.dumps({"ok": bool(r.ok), "played": bool(r.played), "backend": r.backend, "detail": r.detail or ""}))
try:
    main()
except Exception as exc:  # never let an exception escape — caller maps to ok:false
    print(json.dumps({"ok": False, "played": False, "backend": "?", "detail": "speak error: " + str(exc)[:200]}))
"""

_SPEAK_TIMEOUT = 90  # seconds — Kokoro's first call loads a model; well above that, never unbounded


def _speak(text: str, voice_id: str = "narrator-dm") -> dict:
    """Synthesize + play one line via the voice server; return a UI-ready verdict.

    Contract (never hangs, never errors the page):
    - audio actually played            -> {"ok": True, ...}
    - null backend / nothing played    -> {"ok": False, "reason": "voice backend null", ...}
    - voice dir/uv/timeout/crash       -> {"ok": False, "reason": <human reason>, ...}
    The heavy lifting (model load, afplay) runs in a bounded subprocess so a wedged
    backend can't tie up the viewer thread.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty text"}
    if not _VOICE_DIR.is_dir():
        return {"ok": False, "reason": "voice server not found"}
    backend = os.environ.get("CLAWDND_TTS_BACKEND", "kokoro").strip().lower() or "kokoro"
    req = json.dumps({"text": text[:_MOVE_MAXLEN], "voice_id": voice_id, "backend": backend})
    cmd = [
        "uv", "run", "--directory", str(_VOICE_DIR), "--no-project",
        "python", "-c", _SPEAK_SNIPPET,
    ]
    try:
        proc = subprocess.run(
            cmd, input=req, capture_output=True, text=True, timeout=_SPEAK_TIMEOUT
        )
    except FileNotFoundError:
        return {"ok": False, "reason": "uv not installed", "backend": backend}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "voice backend timed out", "backend": backend}
    except Exception as exc:  # noqa: BLE001 — last-resort guard; /speak must never 500
        return {"ok": False, "reason": f"voice error: {exc}", "backend": backend}
    line = (proc.stdout or "").strip().splitlines()
    res: dict = {}
    if line:
        try:
            res = json.loads(line[-1])  # the snippet's final line is its JSON verdict
        except json.JSONDecodeError:
            res = {}
    if not res:
        # backend produced no parseable verdict (e.g. import/env failure on stderr)
        reason = (proc.stderr or "").strip().splitlines()
        return {"ok": False, "reason": (reason[-1][:160] if reason else "voice backend unavailable"), "backend": backend}
    if res.get("ok") and res.get("played"):
        return {"ok": True, "backend": res.get("backend", backend), "detail": res.get("detail", "")}
    # ok-but-not-played (null backend, or no afplay) — surface as a clean false.
    nm = (res.get("backend") or backend)
    reason = "voice backend null" if nm == "null" else (res.get("detail") or "audio not played")
    return {"ok": False, "reason": reason, "backend": nm}


class _Handler(BaseHTTPRequestHandler):
    campaign_id = ""  # set on the class before serving
    transcript_path = ""  # optional agent-transcript .jsonl to tail for /activity
    chat_path = ""  # optional two-sided <run>.chat.jsonl to tail for /chat

    @classmethod
    def _resolve_campaign(cls) -> str:
        """Lazily (re-)attach to a campaign. The viewer may launch BEFORE any campaign
        exists on disk — e.g. `scripts/play.sh` opens the dashboard, then the DM's first
        turn mints the world. Rather than refuse to start (the old behavior crashed with
        "No campaign found"), we bind the port immediately and re-resolve on each request,
        auto-attaching the instant a campaign appears. Once bound, it sticks (recency only
        decides the FIRST attach, so the view doesn't hop between games mid-session)."""
        if not cls.campaign_id:
            cid = _pick_campaign(None)
            if cid:
                cls.campaign_id = cid
        return cls.campaign_id

    def _view_campaign(self, query: dict) -> str:
        """Which campaign THIS request projects (#H3 switcher). An explicit, validated
        ?campaign=<id> is a per-request VIEW OVERRIDE — it lets the dashboard look at any
        campaign without a relaunch but does NOT change the attached default (recency still
        decides that). Falls back to the lazily-(re-)attached campaign otherwise. The id is
        path-validated against the campaigns dir so a tampered value can't escape it."""
        override = _safe_campaign_id((query.get("campaign") or [""])[0])
        if override:
            return override
        return self._resolve_campaign()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode("utf-8"), "application/json")

    def _serve_image(self, scope: str) -> None:
        """GET /image?scope=<scope> — serve the most-recent cached image for a scope.

        Resolves the newest descriptor under <state_dir>/images/<scope>/ and serves
        its pixels: a `path` on disk (read+send the bytes), `bytes_b64` (decode+send),
        or a `url` (302 redirect). 404s cleanly when the scope/descriptor is absent or
        carries no servable image, so the dashboard keeps its placeholder. Content-Type
        comes from the descriptor's `mime_type` (default image/png). Pure reader — never
        imports the engine, never writes."""
        desc = _latest_descriptor(scope)
        if not desc:
            self._send(404, b"no image", "text/plain")
            return
        ctype = desc.get("mime_type")
        ctype = ctype if isinstance(ctype, str) and ctype.strip() else "image/png"
        # 1) a real file on disk — ONLY if it's contained under an expected image root
        # (the derived cache, or the OpenClaw gateway media dir where it writes generated
        # images). The viewer is the documented "pure reader": a descriptor's `path` must
        # never let /image serve an arbitrary file (e.g. /etc/passwd) even if tampered.
        path = desc.get("path")
        if isinstance(path, str) and path:
            _oh = os.environ.get("OPENCLAW_HOME")
            roots = [
                _state_dir() / "images",
                Path(os.environ.get("CLAWDND_OPENCLAW_MEDIA_DIR")
                     or ((Path(_oh) if _oh else Path.home() / ".openclaw") / "media" / "tool-image-generation")),
            ]
            data = None
            try:
                rp = Path(path).resolve()
                if any(rp == r.resolve() or r.resolve() in rp.parents for r in roots):
                    data = rp.read_bytes()
            except OSError:
                data = None
            if data:
                self._send(200, data, ctype)
                return
        # 2) inline base64 bytes
        b64 = desc.get("bytes_b64")
        if isinstance(b64, str) and b64:
            try:
                data = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                data = None
            if data:
                self._send(200, data, ctype)
                return
        # 3) a remote URL — hand the browser a redirect (we don't proxy bytes)
        url = desc.get("url")
        if isinstance(url, str) and url:
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # descriptor exists but carries no servable image (e.g. null placeholder)
        self._send(404, b"no image", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        self._resolve_campaign()  # lazily attach if we launched before a game existed
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            html = (_HERE / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route in ("/dashboard", "/dashboard.html"):
            html = (_HERE / "dashboard.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/campaigns":
            # Read-only list for the topbar switcher (#H3): every projectable campaign,
            # newest-active first, with the attached one marked `current`. Lets the
            # dashboard offer a picker instead of silently auto-following recency.
            self._json({"campaigns": _list_campaigns()})
        elif route == "/state":
            # The raw campaign snapshot, plus one viewer-only flag: `live` says whether
            # the dashboard's action layer can land a move (POST /move accepted). The
            # snapshot is a fresh dict per read, so this transient key never persists.
            # An explicit ?campaign=<id> is a per-request view override (#H3); otherwise
            # we project the lazily-attached campaign.
            cid = self._view_campaign(parse_qs(parsed.query))
            if not cid:
                # No game on disk yet — serve a graceful empty state (the dashboard shows
                # "Waiting for the story to begin…") instead of a blank read; we attach
                # automatically once the DM mints the campaign.
                self._json({"empty": True, "live": _live_play()})
                return
            snap = _read_snapshot(cid)
            snap["live"] = _live_play()
            self._json(snap)
        elif route == "/config":
            self._json(_viewer_config())
        elif route == "/events":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            entries, nxt = _read_events(self._view_campaign(qs), since)
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
        elif route == "/image":
            qs = parse_qs(parsed.query)
            self._serve_image((qs.get("scope") or [""])[0])
        else:
            self._send(404, b"not found", "text/plain")

    def _read_post_json(self) -> object:
        """Read + JSON-parse the request body. Returns the parsed object, or the
        sentinel ``...`` (Ellipsis) on a malformed/undecodable body."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return ...

    def do_POST(self) -> None:  # noqa: N802
        """The write/effect paths. `/move` appends one player *move intent* (a JSON
        line) to $CLAWDND_PLAYER_MOVES — mirroring the engine's player facade, NOT
        touching campaign state. `/speak` plays narration audio via the voice server
        (no state write). Anything else 404s."""
        route = urlparse(self.path).path
        if route == "/speak":
            self._do_speak()
            return
        if route != "/move":
            self._send(404, b"not found", "text/plain")
            return
        dest = _moves_path()
        if dest is None:
            self._json({"ok": False, "reason": "read-only (no live game)"})
            return
        payload = self._read_post_json()
        if payload is ...:
            self._json({"ok": False, "reason": "bad move payload"})
            return
        move, why = sanitize_move(payload)
        if move is None:
            self._json({"ok": False, "reason": why})
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(move, separators=(",", ":")) + "\n")
        except OSError as exc:
            self._json({"ok": False, "reason": f"write failed: {exc}"})
            return
        self._json({"ok": True})

    def _do_speak(self) -> None:
        """POST /speak — play one line of narration audio via the voice server.
        Reads {"text", "voice_id"?}. Always 200 with a JSON verdict (audio-or-null);
        never hangs (bounded subprocess) and never errors the page."""
        payload = self._read_post_json()
        if payload is ... or not isinstance(payload, dict):
            self._json({"ok": False, "reason": "bad speak payload"})
            return
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            self._json({"ok": False, "reason": "speak needs 'text'"})
            return
        voice = payload.get("voice_id")
        voice_id = voice.strip() if isinstance(voice, str) and voice.strip() else "narrator-dm"
        self._json(_speak(text, voice_id))

    def log_message(self, *_args) -> None:  # quiet
        pass


def main() -> int:
    campaign_id = _pick_campaign(sys.argv[1] if len(sys.argv) > 1 else None)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    # Bind even with NO campaign yet: a fresh launch (or scripts/play.sh starting the
    # dashboard before the DM mints the world) must not crash with "No campaign found".
    # We serve a graceful empty state and attach automatically once a campaign appears.
    _Handler.campaign_id = campaign_id or ""
    # Optional agent-transcript to tail in the "Agent activity" panel — point it at a
    # QA run's stream-json (e.g. qa/transcripts/<run>.jsonl) to WATCH the agents play.
    _Handler.transcript_path = os.environ.get("CLAWDND_VIEWER_TRANSCRIPT") or (
        sys.argv[3] if len(sys.argv) > 3 else ""
    )
    # Optional two-sided conversation log to show the player+DM exchange in the chat.
    _Handler.chat_path = os.environ.get("CLAWDND_VIEWER_CHAT") or ""
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    if campaign_id:
        print(f"ClawDnD play-view: http://127.0.0.1:{port}  (campaign: {campaign_id})")
    else:
        print(f"ClawDnD play-view: http://127.0.0.1:{port}  (no game yet — the view attaches automatically once one starts)")
    if _Handler.transcript_path:
        print(f"Watching agent transcript: {_Handler.transcript_path}")
    moves = _moves_path()
    print(
        f"Player moves → appending to: {moves}"
        if moves
        else "Player moves: DISABLED (set CLAWDND_PLAYER_MOVES to enable POST /move)."
    )
    tts = os.environ.get("CLAWDND_TTS_BACKEND", "kokoro")
    if _VOICE_DIR.is_dir():
        print(f"Voice (POST /speak): backend={tts} via {_VOICE_DIR}")
    else:
        print("Voice (POST /speak): voice server not found — /speak returns ok:false.")
    print("Downstream projection of the live campaign — Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
