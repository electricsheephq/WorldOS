#!/usr/bin/env python3
"""ClawDnD read-only play-view — a local web projection of campaign state (P3.6).

Run it for the PLAYER to *see* the adventure while they play through Claude Code:
the current location/map, party vitals, who's in the scene (with voices), the
quest log, and a live roll/event feed. The AI never reads this — it reads the
same state via the engine's MCP tools. This server is a **pure downstream
reader**. It starts with stdlib only; the optional `/build-options` endpoint
imports the engine planner lazily and degrades to JSON errors if unavailable.
It has exactly two side effects,
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
- `GET /build-options` validates campaign/character scope and calls the
  engine-owned read-only build planner; it never calls level_up or saves.

Usage:  python3 viewer/server.py [campaign_id] [port]
        (CLAWDND_STATE_DIR is honored, mirroring the engine's store.state_dir())
"""

from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
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
# the facade's cast/use_item, plus `clarify` (ask the DM a question before acting — a
# question, never a world-assertion, so it's a safe player-side move kind).
_MOVE_KINDS = {"say", "do", "check", "save", "combat", "attack", "cast", "use_item", "clarify"}
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


_ENGINE_SERVER = None
_ENGINE_IMPORT_ERROR = ""


def _install_fastmcp_shim() -> None:
    """Let the plain-stdlib viewer import engine/server.py for direct function calls.

    The dashboard command runs with system python, not the engine's MCP dependency
    environment. engine.server only needs FastMCP at import time to decorate tools;
    for this read-only bridge a no-op decorator is enough and keeps the planner code
    itself engine-owned.
    """
    if "mcp.server.fastmcp" in sys.modules:
        return

    class FastMCP:  # noqa: D401 - tiny import shim, not the real MCP server.
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self, *args, **_kwargs):
            if args and callable(args[0]):
                return args[0]
            return lambda fn: fn

    mcp_mod = sys.modules.get("mcp") or types.ModuleType("mcp")
    server_mod = sys.modules.get("mcp.server") or types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FastMCP
    mcp_mod.server = server_mod
    server_mod.fastmcp = fastmcp_mod
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


def _load_engine_server():
    global _ENGINE_SERVER, _ENGINE_IMPORT_ERROR
    if _ENGINE_SERVER is not None:
        return _ENGINE_SERVER
    engine_dir = (_HERE.parent / "servers" / "engine").resolve()
    try:
        try:
            from mcp.server.fastmcp import FastMCP as _FastMCP  # noqa: F401
        except ModuleNotFoundError:
            _install_fastmcp_shim()
        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))
        spec = importlib.util.spec_from_file_location("_clawdnd_engine_server_for_viewer", engine_dir / "server.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load engine server module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _ENGINE_SERVER = module
        _ENGINE_IMPORT_ERROR = ""
        return module
    except Exception as exc:  # import/dependency failures become explicit JSON degradation
        _ENGINE_IMPORT_ERROR = str(exc)
        return None


def _engine_server():
    """Test-visible accessor for the engine-owned planner module."""
    return _load_engine_server()


def _clean_character_id(character_id: Optional[str]) -> Optional[str]:
    if not isinstance(character_id, str):
        return None
    cid = character_id.strip()
    if not cid or len(cid) > 160 or "\x00" in cid:
        return None
    return cid


def _progression_error(code: str, message: str, *, campaign_id: str = "", character_id: str = "") -> dict:
    return {
        "ok": False,
        "code": code,
        "campaign_id": campaign_id,
        "character_id": character_id,
        "source": "engine.build_options",
        "planner": None,
        "errors": [message],
    }


def build_options_response(campaign_id: Optional[str], character_id: Optional[str]) -> dict:
    """GET /build-options read model.

    The viewer validates the campaign path and character id, then calls the
    engine-owned read-only build_options planner. It never writes a snapshot and
    does not expose the mutating level_up path.
    """
    safe_campaign = _safe_campaign_id(campaign_id)
    if not safe_campaign:
        return _progression_error("invalid_campaign", "missing or unsafe campaign id")

    safe_character = _clean_character_id(character_id)
    if not safe_character:
        return _progression_error(
            "invalid_character",
            "missing or unsafe character id",
            campaign_id=safe_campaign,
        )

    snapshot = _read_snapshot(safe_campaign)
    if not snapshot:
        return _progression_error(
            "state_unavailable",
            "campaign snapshot is unavailable",
            campaign_id=safe_campaign,
            character_id=safe_character,
        )
    chars = snapshot.get("characters")
    if not isinstance(chars, dict) or safe_character not in chars:
        return _progression_error(
            "invalid_character",
            "character is not present in this campaign snapshot",
            campaign_id=safe_campaign,
            character_id=safe_character,
        )

    engine = _load_engine_server()
    if engine is None or not hasattr(engine, "build_options"):
        detail = _ENGINE_IMPORT_ERROR or "engine build_options is unavailable"
        return _progression_error(
            "engine_unavailable",
            f"engine build planner unavailable: {detail}",
            campaign_id=safe_campaign,
            character_id=safe_character,
        )

    try:
        planner = engine.build_options(safe_campaign, safe_character)
    except Exception as exc:
        return _progression_error(
            "engine_error",
            str(exc),
            campaign_id=safe_campaign,
            character_id=safe_character,
        )
    return {
        "ok": True,
        "code": "ok",
        "campaign_id": safe_campaign,
        "character_id": safe_character,
        "source": "engine.build_options",
        "planner": planner,
        "errors": [],
    }


def _display_location(snapshot: dict) -> str:
    loc_id = snapshot.get("current_location_id")
    locs = snapshot.get("locations")
    if isinstance(locs, dict) and isinstance(loc_id, str):
        loc = locs.get(loc_id)
        if isinstance(loc, dict):
            name = loc.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        if loc_id.strip():
            return loc_id.strip()
    return ""


def _party_names(snapshot: dict) -> list[str]:
    chars = snapshot.get("characters")
    party = snapshot.get("party")
    if not isinstance(chars, dict) or not isinstance(party, list):
        return []
    out: list[str] = []
    for cid in party:
        if not isinstance(cid, str):
            continue
        ch = chars.get(cid)
        if not isinstance(ch, dict):
            continue
        name = ch.get("name")
        out.append(str(name or cid))
    return out


def _active_quest_count(snapshot: dict) -> int:
    count = 0
    quests = snapshot.get("quests")
    if isinstance(quests, dict):
        for q in quests.values():
            if not isinstance(q, dict):
                continue
            status = str(q.get("status") or "active").strip().lower()
            if status in ("", "active", "open"):
                count += 1
    hooks = snapshot.get("quest_hooks")
    if isinstance(hooks, list):
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            status = str(hook.get("status") or "open").strip().lower()
            if status not in ("resolved", "completed", "failed", "closed"):
                count += 1
    return count


def build_campaign_summary(
    campaign_id: str,
    snapshot: dict,
    *,
    last_played: float,
    current: bool,
    live: bool,
) -> dict:
    """Read-only picker card for one save/campaign.

    This is intentionally derived from snapshot facts plus file recency only: no engine imports,
    no campaign creation, and no writer-path assumptions. The dashboard uses it to answer
    "which save is this?" before the player resumes or starts elsewhere.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    party = _party_names(snapshot)
    return {
        "id": campaign_id,
        "name": str(snapshot.get("title") or campaign_id),
        "world": str(snapshot.get("world_id") or ""),
        "day": snapshot.get("day"),
        "time_of_day": str(snapshot.get("time_of_day") or ""),
        "location": _display_location(snapshot),
        "party": party,
        "party_count": len(party),
        "active_quest_count": _active_quest_count(snapshot),
        "last_played": last_played,
        "current": bool(current),
        "live": bool(live),
    }


def _list_campaigns() -> list[dict]:
    """All projectable campaigns under the campaigns dir, newest-active first (#H3 switcher).

    One entry per campaign is a read-only save card with title, day, location, party, quest
    count, live/current flags, and last-played recency. Empty/unparseable snapshots are
    skipped — the SAME guard _pick_campaign uses, so a half-written/`{}` snapshot never
    shows as a pickable game. Sorted by recency descending. Pure reader: no writes, no
    engine import."""
    cdir = _campaigns_dir()
    out: list[dict] = []
    if not cdir.is_dir():
        return out
    attached = _Handler.campaign_id
    now = time.time()
    for snap in cdir.glob("*/snapshot.json"):
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not data:
            continue  # empty/`{}` snapshot — nothing to show (mirror _pick_campaign)
        cid = snap.parent.name
        recency = _campaign_recency(snap)
        out.append(build_campaign_summary(
            cid,
            data,
            last_played=recency,
            current=cid == attached,
            live=(now - recency) < 90,
        ))
    out.sort(key=lambda c: c["last_played"], reverse=True)
    return out


def _monitor_roots() -> list[tuple[str, Path]]:
    """(label, campaigns_dir) for EVERY campaign store the monitor scans: the main play store +
    each isolated QA run's store under <repo>/qa/state/<run>/campaigns. So one page shows live
    play AND every parallel test run at once (the QA runs write their snapshot each tool call, so
    they update live). Read-only discovery; never writes."""
    roots: list[tuple[str, Path]] = [("play", _campaigns_dir())]
    qa_state = _HERE.parent / "qa" / "state"
    if qa_state.is_dir():
        for run in sorted(qa_state.iterdir()):
            cdir = run / "campaigns"
            if cdir.is_dir():
                roots.append((f"qa:{run.name}", cdir))
    return roots


_QA_STATUS_UNKNOWN = "UNKNOWN"
_QA_STATUS_KEYS = ("status", "release", "fiction", "behavioral", "gate_status", "verdict")


def _qa_transcripts_dir() -> Path:
    return _HERE.parent / "qa" / "transcripts"


def _qa_sidecar_paths(run: str) -> dict[str, Path]:
    tdir = _qa_transcripts_dir()
    return {
        "mechanical": tdir / f"{run}.score.json",
        "story": tdir / f"{run}.tolkien.json",
        "behavioral_gate": tdir / f"{run}.gate.txt",
        "fiction": tdir / f"{run}.fiction.json",
        "release": tdir / f"{run}.release.json",
    }


def _qa_sidecar_signature(run: str) -> tuple[tuple[str, float], ...]:
    """Small cache key for QA sidecars. The monitor polls often; sidecars can arrive after the
    snapshot stops changing, so cache invalidation must include sidecar mtimes without reading
    transcripts or any large run artifact."""
    sig: list[tuple[str, float]] = []
    for kind, p in _qa_sidecar_paths(run).items():
        try:
            sig.append((kind, p.stat().st_mtime))
        except OSError:
            sig.append((kind, 0.0))
    return tuple(sig)


def _qa_status(value: object) -> str:
    s = str(value or "").strip().upper()
    aliases = {
        "OK": "PASS", "PASSED": "PASS", "READY": "PASS", "GREEN": "GREEN",
        "FAILED": "FAIL", "BLOCKED": "FAIL", "RED": "RED",
        "PENDING": "PENDING", "UNKNOWN": _QA_STATUS_UNKNOWN,
    }
    return aliases.get(s, s) if s else _QA_STATUS_UNKNOWN


def _qa_status_from_json(data: dict, default: str = _QA_STATUS_UNKNOWN) -> str:
    for key in _QA_STATUS_KEYS:
        if key in data:
            return _qa_status(data.get(key))
    if isinstance(data.get("passed"), bool):
        return "PASS" if data["passed"] else "FAIL"
    if isinstance(data.get("ok"), bool):
        return "PASS" if data["ok"] else "FAIL"
    return default


def _qa_blockers(data: dict) -> list[str]:
    raw = data.get("blockers") or data.get("failures") or data.get("defects") or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw[:8]:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                label = item.get("name") or item.get("id") or item.get("check") or item.get("title")
                detail = item.get("evidence") or item.get("reason") or item.get("detail")
                text = ": ".join(str(x) for x in (label, detail) if x)
                if text:
                    out.append(text)
    return out


def _read_json_sidecar(path: Path) -> tuple[dict, float]:
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, 0.0
    return (data, mtime) if isinstance(data, dict) else ({}, mtime)


def _read_behavioral_gate(path: Path) -> tuple[str, float]:
    try:
        mtime = path.stat().st_mtime
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _QA_STATUS_UNKNOWN, 0.0
    if "[FAIL]" in txt or "\nRED" in txt or txt.rstrip().endswith("RED"):
        return "RED", mtime
    if "[PASS]" in txt or "\nGREEN" in txt or txt.rstrip().endswith("GREEN"):
        return "GREEN", mtime
    return _QA_STATUS_UNKNOWN, mtime


def _qa_scores(run: str) -> dict:
    """Mechanical/story scores plus release-readiness statuses for a QA run. Missing readiness
    sidecars are explicit UNKNOWN values, never treated as pass. Read-only and bounded to small
    sidecar files; transcripts are not parsed on monitor polls."""
    out: dict = {"behavioral": _QA_STATUS_UNKNOWN, "fiction": _QA_STATUS_UNKNOWN, "release": _QA_STATUS_UNKNOWN}
    paths = _qa_sidecar_paths(run)
    mechanical, _ = _read_json_sidecar(paths["mechanical"])
    if mechanical:
        out["mechanical"] = mechanical.get("overall")
        out["behavioral"] = _qa_status_from_json(mechanical, out["behavioral"])
    story, _ = _read_json_sidecar(paths["story"])
    if story:
        out["story"] = story.get("overall")
    gate_status, _ = _read_behavioral_gate(paths["behavioral_gate"])
    if gate_status != _QA_STATUS_UNKNOWN:
        out["behavioral"] = gate_status
    for kind in ("fiction", "release"):
        data, _ = _read_json_sidecar(paths[kind])
        if data:
            out[kind] = _qa_status_from_json(data)
    return out


def _qa_release_readiness(run: str) -> dict:
    paths = _qa_sidecar_paths(run)
    out: dict = {"run_id": run, "blockers": []}
    updated = 0.0
    for p in paths.values():
        try:
            updated = max(updated, p.stat().st_mtime)
        except OSError:
            pass
    release, _ = _read_json_sidecar(paths["release"])
    fiction, _ = _read_json_sidecar(paths["fiction"])
    blockers = _qa_blockers(release) + _qa_blockers(fiction)
    if blockers:
        out["blockers"] = blockers
    cell = release.get("release_cell") or release.get("cell") or release.get("matrix_cell")
    if cell:
        out["release_cell"] = str(cell)
    if updated:
        out["readiness_updated_at"] = updated
    return out


def _transcript_path(run: str) -> Optional[Path]:
    """Resolve a QA run's distilled transcript (qa/transcripts/<run>.md), path-guarded so a
    tampered ?run can't escape the transcripts dir (same containment check /image + the
    campaign id use). Returns the file Path, or None for an unsafe/unknown run."""
    if not run or run in (".", "..") or not all(ch.isalnum() or ch in "._-" for ch in run):
        return None
    tdir = (_HERE.parent / "qa" / "transcripts").resolve()
    try:
        cand = (tdir / f"{run}.md").resolve()
    except OSError:
        return None
    return cand if cand.is_file() and cand.parent == tdir else None


def _transcript_html(run: str, md: str) -> str:
    """Wrap a distilled QA transcript (already-readable markdown) in a minimal dark page so
    the monitor can open a finished run's full story in a tab. Content is HTML-escaped and
    shown verbatim in a <pre> — no markdown rendering, no script, pure read-only."""
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>QA transcript · {esc(run)}</title>"
        "<style>body{background:#0f1115;color:#d8dee9;font:14px/1.6 ui-monospace,SFMono-Regular,"
        "Menlo,monospace;margin:0;padding:24px}a{color:#58d18b}h1{font-size:15px;color:#9ad;"
        "font-family:system-ui,sans-serif}pre{white-space:pre-wrap;word-wrap:break-word;max-width:980px}"
        "</style></head>"
        f"<body><h1>QA transcript · {esc(run)} &nbsp; <a href='/monitor'>← monitor</a></h1>"
        f"<pre>{esc(md)}</pre></body></html>"
    )


def _monitor_card(label: str, snap: Path, data: dict) -> dict:
    """A compact, read-only summary of one campaign for the monitor grid."""
    ws = data.get("world_state") or {}
    chars = data.get("characters") or {}
    locs = data.get("locations") or {}
    party = []
    for cid in (data.get("party") or []):
        ch = chars.get(cid)
        if not ch:
            continue
        party.append({"name": ch.get("name", cid), "kind": ch.get("kind", ""),
                      "hp": f"{ch.get('current_hp', '?')}/{ch.get('max_hp', '?')}",
                      "dead": bool(ch.get("dead"))})
    loc_id = data.get("current_location_id")
    updated = _campaign_recency(snap)
    card = {
        "root": label, "id": snap.parent.name,
        "name": str(data.get("title") or snap.parent.name),
        "world": data.get("world_id", ""), "ending": data.get("ending_id", ""),
        "day": data.get("day"), "tenor": ws.get("world_tenor", ""),
        "location": (locs.get(loc_id) or {}).get("name", "") if loc_id else "",
        "party": party,
        "npc_count": sum(1 for c in chars.values() if c.get("kind") == "npc"),
        "quest_hooks": len(data.get("quest_hooks") or []),
        "quest_outcomes": len(data.get("quest_outcomes") or {}),
        "updated_at": updated,
        "live": (time.time() - updated) < 90,  # touched in the last 90s -> a run in motion
    }
    if label.startswith("qa:"):
        run = label.split("qa:", 1)[1]
        card["scores"] = _qa_scores(run)
        card.update(_qa_release_readiness(run))
        # a distilled transcript exists once the run finished playing -> the card becomes a
        # link to read the full story (the monitor's "jump in to give feedback" affordance).
        card["transcript"] = (_HERE.parent / "qa" / "transcripts" / f"{run}.md").is_file()
    return card


# mtime-gated card cache: building a card parses the snapshot JSON + globs the session logs
# (for recency). The monitor polls every 3s; re-doing that for ALL campaigns (e.g. 56 × ~40KB
# = 2.2MB read + parse + a sessions glob each) every tick is pure waste when nothing changed and
# grows linearly with QA runs. Cache the built card per snapshot path, keyed by its mtime
# (save_campaign rewrites the snapshot on every mutation, so mtime bumps exactly when a campaign
# advances). On a hit we skip the read/parse/glob and only refresh the time-relative `live` flag.
_monitor_card_cache: dict[str, tuple[object, dict]] = {}


def _monitor_campaigns() -> list[dict]:
    """All campaigns across all roots (play + every QA run), newest-active first — the data behind
    the one-page monitor. Skips empty/half-written snapshots (the same guard the switcher uses)."""
    cards: list[dict] = []
    now = time.time()
    seen: set[str] = set()
    for label, cdir in _monitor_roots():
        if not cdir.is_dir():
            continue
        for snap in cdir.glob("*/snapshot.json"):
            key = str(snap)
            try:
                mtime = snap.stat().st_mtime
            except OSError:
                continue
            seen.add(key)
            sig: object = mtime
            if label.startswith("qa:"):
                sig = (mtime, _qa_sidecar_signature(label.split("qa:", 1)[1]))
            cached = _monitor_card_cache.get(key)
            if cached and cached[0] == sig:
                card = dict(cached[1])  # reuse the parsed card; only `live` is recomputed below
            else:
                try:
                    data = json.loads(snap.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(data, dict) or not data:
                    continue
                try:
                    card = _monitor_card(label, snap, data)
                except (OSError, TypeError, ValueError):
                    continue  # one malformed campaign must never blank the whole monitor
                _monitor_card_cache[key] = (sig, dict(card))
            card["live"] = (now - card.get("updated_at", 0)) < 90  # time-relative → always fresh
            cards.append(card)
    # Drop cache entries for snapshots that vanished (a deleted QA run) so it can't grow unbounded.
    for stale in [k for k in _monitor_card_cache if k not in seen]:
        _monitor_card_cache.pop(stale, None)
    # Order so the page is USEFUL, not a wall of dead Day-1 QA runs: LIVE first, then the owner's
    # own play campaigns, then everything else by recency. Without this the 40-card cap fills with
    # stale identically-titled QA snapshots and a real/live game is buried (the "locked on one
    # day" report).
    cards.sort(key=lambda c: (not c.get("live"), c.get("root") != "play", -c.get("updated_at", 0)))
    return cards


def _read_snapshot(campaign_id: str) -> dict:
    snap = _campaign_dir(campaign_id) / "snapshot.json"
    if not snap.exists():
        return {}
    try:
        return json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _num(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _combatant_status(ch: dict) -> dict:
    """Small read-only status slice for the viewer command center."""
    out: dict = {
        "id": str(ch.get("id") or ""),
        "name": str(ch.get("name") or "Unknown"),
        "kind": str(ch.get("kind") or ""),
    }
    cur_hp = _num(ch.get("current_hp"))
    max_hp = _num(ch.get("max_hp"))
    if cur_hp is not None or max_hp is not None:
        out["hp"] = {"current": cur_hp, "max": max_hp}
    ac = _num(ch.get("armor_class"))
    if ac is not None:
        out["ac"] = ac
    conditions = ch.get("conditions")
    if isinstance(conditions, list):
        out["conditions"] = [str(c) for c in conditions if str(c)]
    if bool(ch.get("dead")):
        out["dead"] = True
    if bool(ch.get("stable")):
        out["stable"] = True
    death_saves = ch.get("death_saves")
    if isinstance(death_saves, dict):
        out["death_saves"] = {
            "successes": _num(death_saves.get("successes")),
            "failures": _num(death_saves.get("failures")),
        }
    return out


def _combat_zones_view(zones: object, warnings: list[str]) -> list[dict]:
    """Return dashboard-safe tactical zones without trusting malformed snapshot rows."""
    if not isinstance(zones, list):
        return []
    out: list[dict] = []
    for idx, row in enumerate(zones):
        if not isinstance(row, dict):
            warnings.append(f"malformed zone at index {idx}")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"missing zone name at index {idx}")
            continue
        zone = {"name": name.strip()}
        description = row.get("description")
        if isinstance(description, str):
            zone["description"] = description.strip()
        adjacent = row.get("adjacent")
        if isinstance(adjacent, list):
            clean_adjacent = [a.strip() for a in adjacent if isinstance(a, str) and a.strip()]
            if len(clean_adjacent) != len(adjacent):
                warnings.append(f"malformed adjacency in zone {name.strip()!r}")
            if clean_adjacent:
                zone["adjacent"] = clean_adjacent
        elif adjacent not in (None, ""):
            warnings.append(f"malformed adjacency in zone {name.strip()!r}")
        out.append(zone)
    return out


def build_combat_view(snapshot: dict) -> dict:
    """Derive the dashboard's read-only combat command center projection.

    The engine snapshot remains the source of truth; this helper only repackages
    the current combat block and character sheets into a stable UI read model.
    Malformed combat rows become warnings so one bad combatant cannot 500 /state.
    """
    combat = snapshot.get("combat") if isinstance(snapshot, dict) else None
    if not isinstance(combat, dict) or not combat.get("active"):
        return {"active": False, "order": [], "warnings": []}

    chars = snapshot.get("characters") or {}
    if not isinstance(chars, dict):
        chars = {}
    raw_order = combat.get("order") or []
    if not isinstance(raw_order, list):
        raw_order = []

    warnings: list[str] = []
    order: list[dict] = []
    raw_turn_index = combat.get("turn_index")
    turn_index = raw_turn_index if isinstance(raw_turn_index, int) and not isinstance(raw_turn_index, bool) else None
    current_row = raw_order[turn_index] if turn_index is not None and 0 <= turn_index < len(raw_order) else None
    current_id = current_row.get("character_id") if isinstance(current_row, dict) else None

    for idx, row in enumerate(raw_order):
        if not isinstance(row, dict):
            warnings.append(f"malformed combatant at index {idx}")
            continue
        cid = row.get("character_id")
        if not isinstance(cid, str) or not cid.strip():
            warnings.append(f"missing character_id at index {idx}")
            continue
        cid = cid.strip()
        ch = chars.get(cid)
        entry = {
            "id": cid,
            "initiative": _num(row.get("initiative")),
            "is_current": cid == current_id,
            "reaction_available": not bool(row.get("reaction_used")),
        }
        zone = row.get("zone")
        if isinstance(zone, str) and zone.strip():
            entry["zone"] = zone.strip()
        if isinstance(ch, dict):
            entry.update(_combatant_status({**ch, "id": ch.get("id") or cid}))
        else:
            entry.update({"name": "Missing combatant", "kind": "", "conditions": []})
            warnings.append(f"missing character {cid}")
        order.append(entry)

    current = next((dict(o) for o in order if o.get("is_current")), None)
    if current is not None:
        current.pop("is_current", None)
    actions = {}
    if current is not None:
        actions = {
            "action_available": not bool(combat.get("action_used")),
            "bonus_available": not bool(combat.get("bonus_action_used")),
            "reaction_available": bool(current.get("reaction_available")),
        }

    view = {
        "active": True,
        "round": _num(combat.get("round")),
        "turn_index": turn_index,
        "current": current,
        "actions": actions,
        "order": order,
        "warnings": warnings,
    }
    zones = _combat_zones_view(combat.get("zones"), warnings)
    if zones:
        view["zones"] = zones
    return view


def _action_actor(snapshot: dict) -> dict | None:
    """Pick the player-facing actor for the action model from snapshot facts only."""
    chars = snapshot.get("characters") if isinstance(snapshot, dict) else None
    party = snapshot.get("party") if isinstance(snapshot, dict) else None
    if not isinstance(chars, dict) or not isinstance(party, list):
        return None
    candidates = [chars.get(cid) for cid in party if isinstance(cid, str)]
    actor = next((c for c in candidates if isinstance(c, dict) and c.get("kind") == "player"), None)
    if actor is None:
        actor = next((c for c in candidates if isinstance(c, dict)), None)
    if actor is None:
        return None
    aid = actor.get("id")
    if not isinstance(aid, str) or not aid.strip():
        for cid in party:
            if isinstance(cid, str) and chars.get(cid) is actor:
                aid = cid
                break
    if not isinstance(aid, str) or not aid.strip():
        return None
    return {
        "id": aid.strip(),
        "name": str(actor.get("name") or aid).strip() or aid.strip(),
        "kind": str(actor.get("kind") or "").strip(),
    }


def _combat_current_id(combat: object) -> str | None:
    if not isinstance(combat, dict) or not combat.get("active"):
        return None
    order = combat.get("order")
    turn_index = combat.get("turn_index")
    if not isinstance(order, list) or not isinstance(turn_index, int) or isinstance(turn_index, bool):
        return None
    if not 0 <= turn_index < len(order):
        return None
    row = order[turn_index]
    cid = row.get("character_id") if isinstance(row, dict) else None
    return cid.strip() if isinstance(cid, str) and cid.strip() else None


def _combat_row(combat: object, character_id: str | None) -> dict | None:
    if not character_id or not isinstance(combat, dict):
        return None
    order = combat.get("order")
    if not isinstance(order, list):
        return None
    for row in order:
        if isinstance(row, dict) and row.get("character_id") == character_id:
            return row
    return None


def _action_item(
    action_id: str,
    label: str,
    *,
    kind: str | None = None,
    name: str | None = None,
    text: str | None = None,
    disabled_reason: str | None = None,
    ui: str | None = None,
) -> dict:
    item = {
        "id": action_id,
        "label": label,
        "available": disabled_reason is None,
        "disabled_reason": disabled_reason,
    }
    move = {}
    if kind:
        move["kind"] = kind
    if name:
        move["name"] = name
    if text:
        move["text"] = text
    if move:
        item["move"] = move
    if ui:
        item["ui"] = ui
    return item


def build_action_model(snapshot: dict, *, live: bool, is_live_view: bool) -> dict:
    """Derive a read-only dashboard action model from snapshot + move-sink facts.

    This does not preview engine mutations. It only describes which existing
    dashboard move intents are sensible to offer, and why unavailable actions are
    disabled.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    viewer_keys = {"action_model", "combat_view", "empty", "is_live_view", "live"}
    has_campaign = any(k not in viewer_keys for k in snapshot) and not snapshot.get("empty")
    actor = _action_actor(snapshot) if has_campaign else None
    combat = snapshot.get("combat") if has_campaign else None
    combat_active = isinstance(combat, dict) and bool(combat.get("active"))
    current_id = _combat_current_id(combat)
    actor_id = actor.get("id") if actor else None
    is_current_turn = bool(actor_id and current_id and actor_id == current_id)
    actor_row = _combat_row(combat, actor_id)

    def global_reason() -> str | None:
        if not has_campaign:
            return "no active campaign"
        if actor is None:
            return "no active character"
        if not live:
            return "no live move sink"
        if not is_live_view:
            return "viewing non-live campaign"
        return None

    base_reason = global_reason()
    action_available = False
    bonus_available = False
    reaction_available = False
    if combat_active and actor is not None:
        action_available = is_current_turn and not bool(combat.get("action_used"))
        bonus_available = is_current_turn and not bool(combat.get("bonus_action_used"))
        reaction_available = actor_row is not None and not bool(actor_row.get("reaction_used"))

    def turn_action_reason(slot: str) -> str | None:
        if not has_campaign:
            return "no active campaign"
        if not combat_active:
            return "not in combat"
        if actor is None:
            return "no active character"
        if base_reason:
            return base_reason
        if not is_current_turn:
            return "not current turn"
        if slot == "action" and not action_available:
            return "action spent"
        if slot == "bonus" and not bonus_available:
            return "bonus action spent"
        return None

    def reaction_reason() -> str | None:
        if not has_campaign:
            return "no active campaign"
        if not combat_active:
            return "not in combat"
        if actor is None:
            return "no active character"
        if actor_row is None:
            return "not in combat"
        if base_reason:
            return base_reason
        if not reaction_available:
            return "reaction spent"
        return None

    model = {
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "actor": actor,
        "combat": {
            "active": combat_active,
            "round": _num(combat.get("round")) if isinstance(combat, dict) else None,
            "current_actor_id": current_id,
            "is_current_turn": is_current_turn,
        },
        "economy": {
            "action_available": action_available,
            "bonus_available": bonus_available,
            "reaction_available": reaction_available,
        },
        "groups": [
            {
                "id": "exploration",
                "label": "Explore",
                "actions": [
                    _action_item("continue", "Continue", kind="do", text="continue", disabled_reason=base_reason),
                    _action_item("say", "Say", disabled_reason=base_reason, ui="focus-say"),
                    _action_item("do", "Do", disabled_reason=base_reason, ui="focus-do"),
                    _action_item("check", "Check", disabled_reason=base_reason, ui="palette-skills"),
                    _action_item("save", "Save", disabled_reason=base_reason, ui="palette-saves"),
                ],
            },
            {
                "id": "combat",
                "label": "Combat",
                "actions": [
                    _action_item("attack", "Attack", kind="attack", name="Attack", disabled_reason=turn_action_reason("action")),
                    _action_item("bonus-action", "Bonus", kind="combat", name="Bonus Action", disabled_reason=turn_action_reason("bonus")),
                    _action_item("reaction", "Reaction", kind="combat", name="Reaction", disabled_reason=reaction_reason()),
                ],
            },
        ],
    }
    return model


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
    # Session ROTATION reset: when the engine opens a NEW session the log starts back at line 0,
    # but the client's cursor is still at the old session's high-water mark — so `lines[since:]`
    # is empty FOREVER and the feed freezes on stale beats while the campaign advances (the
    # "locked on one day" report). If the cursor is past the end of the current file, re-read it
    # from the top.
    if since > len(lines):
        since = 0
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
    pinned = False  # launched with an explicit campaign id -> never auto-follow recency

    @classmethod
    def _resolve_campaign(cls) -> str:
        """Lazily (re-)attach to a campaign. The viewer may launch BEFORE any campaign
        exists on disk — e.g. `scripts/play.sh` opens the dashboard, then the DM's first
        turn mints the world. We bind the port immediately and re-resolve on each request.

        Auto-follow (#38 / C3): when NOT launched for a specific campaign, track the
        most-recently-ACTIVE campaign so the dashboard picks up a NEWLY-started game instead
        of sticking forever to whatever existed at launch (the "stuck on a stale campaign"
        bug). The per-request ?campaign= switcher override (_view_campaign) is unaffected — it
        still wins for that request. When launched WITH an explicit id (`pinned`), stick to it."""
        if cls.pinned:
            return cls.campaign_id
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
        elif route == "/build-options":
            # Read-only progression planner bridge: path-safe campaign scope +
            # character id, then engine.build_options. It returns disabled/error
            # data for the dashboard to render and never exposes level_up.
            qs = parse_qs(parsed.query)
            cid = (qs.get("campaign") or [""])[0] or self._view_campaign(qs)
            character_id = (qs.get("character") or [""])[0]
            self._json(build_options_response(cid, character_id))
        elif route in ("/monitor", "/monitor.html"):
            # The MULTI-CAMPAIGN monitor: one live page showing EVERY campaign across the play
            # store + all parallel QA runs (watch the stress tests + any live game in one place).
            html = (_HERE / "monitor.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/monitor.json":
            # The monitor's data feed (polled): every campaign across all roots, newest-active
            # first, each with live state + (for QA runs) scores when written. Capped at the 40
            # most-recent so the page stays scannable; `total` reports the full count. Read-only.
            all_cards = _monitor_campaigns()
            self._json({"campaigns": all_cards[:40], "total": len(all_cards), "now": time.time()})
        elif route == "/transcript":
            # Read-only view of a finished QA run's distilled transcript (qa/transcripts/<run>.md),
            # opened from a monitor card so the owner can read the full played story to give
            # feedback. ?run is path-guarded; unknown/unsafe -> 404. Never writes, never imports engine.
            run = (parse_qs(parsed.query).get("run") or [""])[0]
            tp = _transcript_path(run)
            if tp is None:
                self._send(404, b"no such transcript", "text/plain; charset=utf-8")
            else:
                try:
                    md = tp.read_text(encoding="utf-8")
                except OSError:
                    self._send(404, b"unreadable transcript", "text/plain; charset=utf-8")
                else:
                    self._send(200, _transcript_html(run, md).encode("utf-8"), "text/html; charset=utf-8")
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
                live = _live_play()
                self._json({
                    "empty": True,
                    "live": live,
                    "combat_view": build_combat_view({}),
                    "action_model": build_action_model({}, live=live, is_live_view=False),
                })
                return
            raw_snap = _read_snapshot(cid)
            if not isinstance(raw_snap, dict):
                raw_snap = {}
            live = _live_play()
            is_live_view = live and cid == self.campaign_id
            snap = dict(raw_snap)
            snap["combat_view"] = build_combat_view(raw_snap)
            snap["live"] = live
            # is_live_view: the move sink (CLAWDND_PLAYER_MOVES) belongs to the ATTACHED campaign;
            # a move only makes sense when the VIEWED campaign IS that one. The dashboard grays the
            # palette when this is false, so the switcher can't send moves to the wrong run (#49).
            snap["is_live_view"] = is_live_view
            snap["action_model"] = build_action_model(raw_snap, live=live, is_live_view=is_live_view)
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
            # The activity/chat transcripts belong to the LAUNCHED live run, not whatever campaign
            # the switcher is viewing — only serve them when the viewed campaign IS the attached
            # one, else report not-live so the dashboard falls back to the campaign-scoped /events
            # feed instead of showing a stale/other run's narration (#49 C1).
            a_live = bool(self.transcript_path) and self._view_campaign(qs) == self.campaign_id
            items, nxt = _read_activity(since) if a_live else ([], since)
            self._json({"items": items, "next": nxt, "live": a_live})
        elif route == "/chat":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0])
            c_live = bool(self.chat_path) and self._view_campaign(qs) == self.campaign_id
            items, nxt = _read_chat(since) if c_live else ([], since)
            self._json({"items": items, "next": nxt, "live": c_live})
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
        # Bind the write to the LIVE campaign: the move sink belongs to the attached campaign,
        # so a move the client tagged for a DIFFERENT (viewed) campaign must be refused — never
        # misrouted into the live run. An untagged move (no `campaign`) is the live view. (#49)
        if isinstance(payload, dict):
            viewed = payload.get("campaign")
            if viewed and self.campaign_id and viewed != self.campaign_id:
                self._json({"ok": False, "reason": "viewing a non-live campaign — switch to the live run to act"})
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
    arg_campaign = sys.argv[1] if len(sys.argv) > 1 else None
    campaign_id = _pick_campaign(arg_campaign)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    # Bind even with NO campaign yet: a fresh launch (or scripts/play.sh starting the
    # dashboard before the DM mints the world) must not crash with "No campaign found".
    # We serve a graceful empty state and attach automatically once a campaign appears.
    _Handler.campaign_id = campaign_id or ""
    # An EXPLICIT campaign arg pins the view (don't auto-follow recency); launched bare (''),
    # the dashboard follows the most-recently-active game (#38 / C3 auto-follow).
    _Handler.pinned = bool(arg_campaign)
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
