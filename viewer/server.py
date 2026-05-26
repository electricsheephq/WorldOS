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
import mimetypes
import os
import subprocess
import sys
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

_HERE = Path(__file__).resolve().parent
# servers/voice lives two levels up from viewer/ (repo root / servers / voice).
_VOICE_DIR = _HERE.parent / "servers" / "voice"
_OPENWORLDS_DIR_ENV = "CLAWDND_OPENWORLDS_DIR"
_OPENWORLDS_ROUTE = "/openworlds"
_OPENWORLDS_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jsx": "text/babel; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".ttf": "font/ttf",
}


def _openworlds_dir() -> Path:
    """OpenWorlds asset root.

    The native app can ship a bundled copy of the UI and point the repo-backed
    viewer at it. Engine/viewer data still comes from the configured checkout.
    """
    override = os.environ.get(_OPENWORLDS_DIR_ENV, "").strip()
    return Path(override).expanduser() if override else _HERE / "openworlds"

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


def _resolved(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _session_count(campaign_dir: Path) -> int:
    sessions = campaign_dir / "sessions"
    if not sessions.is_dir():
        return 0
    return sum(1 for p in sessions.glob("*.jsonl") if p.is_file())


def _catalog_run_id(state_root: Path) -> str:
    """Stable display id for a state root.

    Product runs live under play-state/<run-id> and QA under qa/state/<run-id>.
    A bare CLAWDND_STATE_DIR (for example ~/.clawdnd/state, or a temp dir in
    tests) is the viewer's active state root rather than a named run, so expose it
    as "state" instead of leaking a random local folder name into the UI.
    """
    if state_root.parent.name in ("play-state", "state"):
        return state_root.name
    return "state"


def _campaign_catalog_roots() -> list[dict]:
    """Read-only roots for the OpenWorlds campaign shelf.

    The native app and the exact OpenWorlds surface need the same product answer:
    "what local play or QA runs exist?"  We scan the viewer's active state dir,
    repo-local play-state/*, and repo-local qa/state/* without importing the
    engine and without opening any write path.
    """
    roots: list[dict] = []
    seen: set[str] = set()

    def add(source: str, run_id: str, state_root: Path, campaigns_dir: Path, *, current_state: bool = False) -> None:
        key = _resolved(campaigns_dir)
        if key in seen:
            return
        seen.add(key)
        roots.append({
            "source": source,
            "run_id": run_id,
            "state_root": state_root,
            "campaigns_dir": campaigns_dir,
            "current_state": current_state,
        })

    state_root = _state_dir()
    add("play", _catalog_run_id(state_root), state_root, state_root / "campaigns", current_state=True)

    play_state = _HERE.parent / "play-state"
    if play_state.is_dir():
        for run in sorted(play_state.iterdir()):
            cdir = run / "campaigns"
            if cdir.is_dir():
                add("play", run.name, run, cdir)

    qa_state = _HERE.parent / "qa" / "state"
    if qa_state.is_dir():
        for run in sorted(qa_state.iterdir()):
            cdir = run / "campaigns"
            if cdir.is_dir():
                add("qa", run.name, run, cdir)
    return roots


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def _openworlds_day_label(snapshot: dict) -> str:
    day = snapshot.get("day")
    time_of_day = _text(snapshot.get("time_of_day"))
    if isinstance(day, int):
        return f"Day {day}" + (f" · {time_of_day}" if time_of_day else "")
    return time_of_day or "Unknown time"


def _party_cards(snapshot: dict) -> list[dict]:
    chars = snapshot.get("characters")
    party = snapshot.get("party")
    if not isinstance(chars, dict) or not isinstance(party, list):
        return []
    out: list[dict] = []
    for cid in party:
        if not isinstance(cid, str):
            continue
        ch = chars.get(cid)
        if not isinstance(ch, dict):
            out.append({"id": cid, "name": cid, "short": "portrait"})
            continue
        card = {
            "id": cid,
            "name": _text(ch.get("name"), cid),
            "short": "portrait",
            "kind": _text(ch.get("kind")),
        }
        hp = ch.get("current_hp")
        hp_max = ch.get("max_hp")
        if isinstance(hp, int) and isinstance(hp_max, int):
            card["hp"] = f"{hp}/{hp_max}"
        if ch.get("dead"):
            card["dead"] = True
        out.append(card)
    return out


def _class_summary(ch: dict) -> tuple[str, int | None]:
    classes = ch.get("classes")
    if isinstance(classes, list) and classes:
        names: list[str] = []
        levels: list[int] = []
        for row in classes:
            if not isinstance(row, dict):
                continue
            name = _text(row.get("name") or row.get("class_name"))
            if name:
                names.append(name)
            level = row.get("level")
            if isinstance(level, int) and not isinstance(level, bool):
                levels.append(level)
        if names:
            return " / ".join(names), sum(levels) if levels else None
    klass = _text(ch.get("class") or ch.get("klass"), "Adventurer")
    level = ch.get("level")
    return klass, level if isinstance(level, int) and not isinstance(level, bool) else None


def _session_location(snapshot: dict) -> dict:
    loc_id = _text(snapshot.get("current_location_id"))
    locs = snapshot.get("locations")
    loc = locs.get(loc_id) if isinstance(locs, dict) and loc_id else None
    loc = loc if isinstance(loc, dict) else {}
    name = _text(loc.get("name"), loc_id or "Unknown location")
    return {
        "id": loc_id,
        "name": name,
        "region": _text(loc.get("region"), name),
        "description": _text(loc.get("description")),
    }


def _session_party_cards(snapshot: dict) -> list[dict]:
    chars = snapshot.get("characters")
    party = snapshot.get("party")
    if not isinstance(chars, dict) or not isinstance(party, list):
        return []
    out: list[dict] = []
    for cid in party:
        if not isinstance(cid, str):
            continue
        ch = chars.get(cid)
        if not isinstance(ch, dict):
            continue
        klass, level = _class_summary(ch)
        cur_hp = _num(ch.get("current_hp"))
        max_hp = _num(ch.get("max_hp"))
        hp = cur_hp if cur_hp is not None else (max_hp if max_hp is not None else 1)
        hp_max = max_hp if max_hp is not None else (cur_hp if cur_hp is not None else 1)
        card = {
            "id": cid,
            "name": _text(ch.get("name"), cid),
            "short": "portrait",
            "level": level or 1,
            "class": klass,
            "hp": hp,
            "hpMax": hp_max if hp_max else 1,
            "kind": _text(ch.get("kind")),
            "conditions": [str(c) for c in ch.get("conditions", []) if str(c)]
            if isinstance(ch.get("conditions"), list)
            else [],
        }
        ac = _num(ch.get("armor_class"))
        if ac is not None:
            card["ac"] = ac
        if bool(ch.get("dead")):
            card["dead"] = True
        out.append(card)
    return out


def _session_conditions(party: list[dict]) -> list[dict]:
    out: list[dict] = []
    for card in party:
        for condition in card.get("conditions", []):
            name = _text(condition).replace("_", " ").title()
            if not name:
                continue
            out.append({
                "id": f"{card.get('id', '')}:{name.lower().replace(' ', '-')}",
                "icon": "◆",
                "name": name,
                "who": _text(card.get("name"), "Unknown"),
                "detail": "Active condition",
                "tone": "royal" if name.lower() in {"blessed", "inspired"} else "",
            })
    return out


def _session_active_quests(snapshot: dict) -> list[dict]:
    quests = snapshot.get("quests")
    locs = snapshot.get("locations")
    out: list[dict] = []
    if not isinstance(quests, dict):
        return out
    for qid, row in quests.items():
        if not isinstance(row, dict):
            continue
        status = _text(row.get("status"), "active").lower()
        if status in {"completed", "complete", "resolved", "failed", "closed"}:
            continue
        objectives = row.get("objectives")
        completed = row.get("completed_objectives")
        completed_set = {str(o) for o in completed} if isinstance(completed, list) else set()
        objective = ""
        if isinstance(objectives, list):
            for item in objectives:
                item_text = _text(item)
                if item_text and item_text not in completed_set:
                    objective = item_text
                    break
        if not objective:
            objective = _text(row.get("description"), "Continue the investigation.")
        location_id = _text(row.get("location_id"))
        location = ""
        if isinstance(locs, dict) and location_id:
            loc = locs.get(location_id)
            if isinstance(loc, dict):
                location = _text(loc.get("name"), location_id)
        out.append({
            "id": _text(qid),
            "title": _text(row.get("title"), _text(qid, "Quest")),
            "label": status.title() if status else "Active",
            "objective": objective,
            "status": status or "active",
            "tone": "royal",
            "location": location,
        })
    return out[:8]


def _session_quick_inventory(snapshot: dict) -> list[dict]:
    chars = snapshot.get("characters")
    party = snapshot.get("party")
    if not isinstance(chars, dict) or not isinstance(party, list):
        return []
    out: list[dict] = []
    for cid in party:
        if not isinstance(cid, str):
            continue
        ch = chars.get(cid)
        if not isinstance(ch, dict):
            continue
        inventory = ch.get("inventory")
        if not isinstance(inventory, list):
            continue
        for idx, item in enumerate(inventory):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"))
            if not name:
                continue
            qty = item.get("quantity", item.get("qty", item.get("count", 1)))
            qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else 1
            out.append({
                "id": f"{cid}:{idx}:{name}",
                "name": name,
                "glyph": _text(item.get("glyph"), "item"),
                "qty": qty,
                "type": _text(item.get("type"), "item"),
            })
            if len(out) >= 12:
                return out
    return out


def _session_available_actions(action_model: dict) -> list[dict]:
    out: list[dict] = []
    groups = action_model.get("groups")
    if not isinstance(groups, list):
        return out
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = _text(group.get("id"))
        group_label = _text(group.get("label"))
        actions = group.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            item = {
                "id": _text(action.get("id")),
                "label": _text(action.get("label")),
                "group": group_id,
                "groupLabel": group_label,
                "available": bool(action.get("available")),
                "disabled_reason": action.get("disabled_reason") if isinstance(action.get("disabled_reason"), str) else None,
            }
            move = action.get("move")
            if isinstance(move, dict):
                item["move"] = {
                    k: v for k, v in move.items()
                    if k in _MOVE_FIELDS or k == "kind"
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool)
                }
            ui = action.get("ui")
            if isinstance(ui, str) and ui.strip():
                item["ui"] = ui.strip()
            out.append(item)
    return out


def _session_recent_events(raw_events: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for row in raw_events or []:
        if not isinstance(row, dict):
            continue
        kind = _text(row.get("kind") or row.get("type"), "narration")
        label = _text(row.get("label") or row.get("who") or row.get("source"))
        text = _text(row.get("text") or row.get("detail") or row.get("summary"))
        if not text:
            continue
        item = {"kind": kind, "text": text[:1000]}
        if label:
            item["label"] = label[:120]
        out.append(item)
        if len(out) >= 12:
            break
    return out


def _safe_session_id(session_id: object) -> str:
    if not isinstance(session_id, str):
        return ""
    sid = session_id.strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return ""
    if Path(sid).name != sid:
        return ""
    if not all(ch.isalnum() or ch in "._-" for ch in sid):
        return ""
    return sid


def _tail_text_lines(path: Path, limit: int, chunk_size: int = 8192) -> list[str]:
    if limit <= 0:
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= limit:
                size = min(chunk_size, pos)
                pos -= size
                f.seek(pos)
                data = f.read(size) + data
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _session_event_tail_from_dir(campaign_dir: Path, snapshot: dict, limit: int = 12) -> list[dict]:
    sid = snapshot.get("active_session_id")
    if not sid:
        session_ids = snapshot.get("session_ids")
        if isinstance(session_ids, list) and session_ids:
            sid = session_ids[-1]
    sid = _safe_session_id(sid)
    if not sid:
        return []
    try:
        sessions_dir = (campaign_dir / "sessions").resolve()
        log = (sessions_dir / f"{sid}.jsonl").resolve()
    except OSError:
        return []
    if log.parent != sessions_dir or not log.is_file():
        return []
    out: list[dict] = []
    for raw in _tail_text_lines(log, limit):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _session_event_tail(campaign_id: str, limit: int = 12) -> list[dict]:
    snapshot = _read_snapshot(campaign_id)
    return _session_event_tail_from_dir(_campaign_dir(campaign_id), snapshot, limit)


def _session_surface_catalog_ref(query: dict) -> tuple[str, dict, Path, bool] | None:
    source = _text((query.get("source") or [""])[0])
    run_id = _text((query.get("run") or [""])[0])
    campaign_id = _text((query.get("campaign") or [""])[0])
    if not source or not run_id or not campaign_id:
        return None
    for root in _campaign_catalog_roots():
        if str(root.get("source")) != source or str(root.get("run_id")) != run_id:
            continue
        cdir = root.get("campaigns_dir")
        if not isinstance(cdir, Path) or not cdir.is_dir():
            return None
        try:
            campaign_dir = (cdir / campaign_id).resolve()
            if not campaign_dir.is_dir() or campaign_dir.parent != cdir.resolve():
                return None
            snap = campaign_dir / "snapshot.json"
            data = json.loads(snap.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        root_is_current = bool(root.get("current_state")) and _resolved(cdir) == _resolved(_campaigns_dir())
        return campaign_dir.name, data, campaign_dir, root_is_current
    return None


def build_session_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
    recent_events: list[dict] | None = None,
) -> dict:
    """Project a browser-safe OpenWorlds table surface from engine-owned state.

    This read model intentionally copies only player-facing fields into a stable
    shape for the OpenWorlds session screen. The browser may render it and submit
    enabled player intents to `/move`; it must never infer or write campaign
    state directly.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    location = _session_location(snapshot)
    party = _session_party_cards(snapshot)
    action_model = build_action_model(snapshot, live=live, is_live_view=is_live_view)
    combat_view = build_combat_view(snapshot)
    actions = _session_available_actions(action_model)
    combat_active = bool(combat_view.get("active"))
    round_no = combat_view.get("round")
    summary = _text(snapshot.get("summary"))
    if not summary:
        summary = _text(location.get("description"), f"The party is gathered near {location['name']}.")

    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "day": snapshot.get("day") if isinstance(snapshot.get("day"), int) else None,
        "time_of_day": _text(snapshot.get("time_of_day")),
        "dayLabel": _openworlds_day_label(snapshot),
        "location": location,
        "scene": {
            "summary": summary,
            "caption": location["name"],
            "imageScope": f"location:{location['id']}" if location["id"] else "",
        },
        "party": party,
        "conditions": _session_conditions(party),
        "activeQuests": _session_active_quests(snapshot),
        "quickInventory": _session_quick_inventory(snapshot),
        "encounter": {
            "active": combat_active,
            "summary": f"Combat round {round_no}" if combat_active and round_no else ("Combat" if combat_active else summary),
            "actions": actions[:8],
        },
        "roundOrder": [
            {
                "id": _text(row.get("id")),
                "name": _text(row.get("name"), "Unknown"),
                "init": row.get("initiative"),
                "active": bool(row.get("is_current")),
                "foe": _text(row.get("kind")).lower() in {"monster", "enemy", "foe"},
            }
            for row in combat_view.get("order", [])
            if isinstance(row, dict)
        ],
        "availableActions": actions,
        "recentEvents": _session_recent_events(recent_events),
        "actionModel": action_model,
        "combatView": combat_view,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


def _combat_team(kind: object) -> str:
    value = _text(kind).lower()
    if value in {"monster", "enemy", "foe", "hostile"}:
        return "foe"
    return "ally"


def _combat_initial(name: str, fallback: str = "?") -> str:
    for part in name.replace("-", " ").split():
        if part:
            return part[0].upper()
    return fallback[:1].upper() if fallback else "?"


def _combat_health_label(cur_hp: object, max_hp: object) -> str:
    cur = _num(cur_hp)
    maxv = _num(max_hp)
    if cur is None or maxv is None or maxv <= 0:
        return "unknown"
    if cur <= 0:
        return "down"
    ratio = cur / maxv
    if ratio <= 0.5:
        return "bloodied"
    if ratio < 1:
        return "wounded"
    return "steady"


def _combat_public_stat(ch: dict, *names: str) -> bool:
    return any(bool(ch.get(name)) for name in names)


def _combat_row_positions(snapshot: dict) -> dict[str, dict]:
    combat = snapshot.get("combat") if isinstance(snapshot, dict) else None
    order = combat.get("order") if isinstance(combat, dict) else None
    if not isinstance(order, list):
        return {}
    out: dict[str, dict] = {}
    for row in order:
        if not isinstance(row, dict):
            continue
        cid = row.get("character_id")
        if isinstance(cid, str) and cid.strip():
            out[cid.strip()] = row
    return out


def _combat_display_position(
    row: dict,
    *,
    idx: int,
    zones: list[dict],
    zone_offsets: dict[str, int],
) -> tuple[int, int, str]:
    x = _num(row.get("x") or row.get("col") or row.get("grid_x"))
    y = _num(row.get("y") or row.get("row") or row.get("grid_y"))
    if x is not None and y is not None:
        return max(1, min(16, int(x))), max(1, min(10, int(y))), "grid"
    zone_name = _text(row.get("zone"))
    zone_index = next((i for i, z in enumerate(zones) if z.get("name") == zone_name), idx)
    offset = zone_offsets.get(zone_name, 0)
    zone_offsets[zone_name] = offset + 1
    base_x = 3 + (zone_index % 4) * 4
    base_y = 3 + (zone_index // 4) * 3
    return max(1, min(16, base_x + offset)), max(1, min(10, base_y + (offset % 2))), "zone"


def _combat_tokens(snapshot: dict, combat_view: dict) -> tuple[list[dict], list[dict], list[dict], str, str]:
    chars = snapshot.get("characters") if isinstance(snapshot, dict) else {}
    chars = chars if isinstance(chars, dict) else {}
    raw_rows = _combat_row_positions(snapshot)
    zones = [dict(z) for z in combat_view.get("zones", []) if isinstance(z, dict)]
    zone_occupants: dict[str, list[str]] = {z.get("name", ""): [] for z in zones}
    zone_offsets: dict[str, int] = {}
    tokens: list[dict] = []
    initiative: list[dict] = []
    position_sources: set[str] = set()

    for idx, row in enumerate(combat_view.get("order", [])):
        if not isinstance(row, dict):
            continue
        cid = _text(row.get("id"))
        raw = raw_rows.get(cid, {})
        name = _text(row.get("name"), cid or "Unknown")
        kind = _text(row.get("kind"))
        team = _combat_team(kind)
        token = {
            "id": cid,
            "name": name,
            "initial": _combat_initial(name, cid),
            "short": f"{_combat_initial(name, cid)} portrait",
            "team": team,
            "initiative": row.get("initiative"),
            "isCurrent": bool(row.get("is_current")),
            "reactionAvailable": bool(row.get("reaction_available")),
            "conditions": [str(c) for c in row.get("conditions", []) if str(c)]
            if isinstance(row.get("conditions"), list)
            else [],
        }
        zone = _text(row.get("zone"))
        if zone:
            token["zone"] = zone
            if zone in zone_occupants:
                zone_occupants[zone].append(cid)
        x, y, source = _combat_display_position(raw, idx=idx, zones=zones, zone_offsets=zone_offsets)
        token["x"] = x
        token["y"] = y
        position_sources.add(source)

        ch = chars.get(cid)
        ch = ch if isinstance(ch, dict) else {}
        hp = row.get("hp") if isinstance(row.get("hp"), dict) else {}
        cur_hp = hp.get("current") if isinstance(hp, dict) else None
        max_hp = hp.get("max") if isinstance(hp, dict) else None
        hp_known = team != "foe" or _combat_public_stat(ch, "hp_known", "known_hp", "player_known_hp")
        token["hpKnown"] = bool(hp_known)
        token["health"] = _combat_health_label(cur_hp, max_hp)
        if hp_known:
            token["hp"] = cur_hp if cur_hp is not None else 1
            token["hpMax"] = max_hp if max_hp not in (None, 0) else token["hp"] or 1
        ac = row.get("ac")
        if team != "foe" or _combat_public_stat(ch, "ac_known", "known_ac", "player_known_ac"):
            if ac is not None:
                token["ac"] = ac
        tokens.append(token)
        initiative.append({
            "id": cid,
            "name": name,
            "init": row.get("initiative"),
            "active": bool(row.get("is_current")),
            "team": team,
            "health": token["health"],
            "reactionAvailable": token["reactionAvailable"],
        })

    for zone in zones:
        name = _text(zone.get("name"))
        zone["occupants"] = zone_occupants.get(name, [])
    mode = "grid" if "grid" in position_sources else "zones"
    selected = next((t["id"] for t in tokens if t.get("isCurrent")), tokens[0]["id"] if tokens else "")
    return tokens, initiative, zones, selected, mode


def _combat_action_bar(action_model: dict, combat_active: bool) -> list[dict]:
    by_id = {a["id"]: a for a in _session_available_actions(action_model) if a.get("id")}
    live = bool(action_model.get("live"))
    is_live_view = bool(action_model.get("is_live_view"))
    actor = action_model.get("actor")
    combat = action_model.get("combat") if isinstance(action_model.get("combat"), dict) else {}

    def base_reason() -> str | None:
        if not combat_active:
            return "not in combat"
        if actor is None:
            return "no active character"
        if not live:
            return "no live move sink"
        if not is_live_view:
            return "viewing non-live campaign"
        if not combat.get("is_current_turn"):
            return "not current turn"
        return None

    def from_model(action_id: str, label: str, icon: str, fallback_reason: str | None = None) -> dict:
        item = dict(by_id.get(action_id) or {
            "id": action_id,
            "label": label,
            "available": False,
            "disabled_reason": fallback_reason or base_reason() or "not engine-backed yet",
        })
        item.setdefault("id", action_id)
        item.setdefault("label", label)
        item["icon"] = icon
        item.setdefault("available", False)
        item.setdefault("disabled_reason", None if item.get("available") else fallback_reason or base_reason())
        return item

    end_reason = base_reason()
    end_turn = {
        "id": "end-turn",
        "label": "End turn",
        "icon": "⊘",
        "available": end_reason is None,
        "disabled_reason": end_reason,
    }
    if end_reason is None:
        end_turn["move"] = {"kind": "combat", "name": "End Turn"}

    return [
        {
            "id": "move",
            "label": "Move",
            "icon": "↗",
            "available": False,
            "disabled_reason": "movement destinations not projected yet",
        },
        from_model("attack", "Attack", "⚔", base_reason()),
        {
            "id": "cast",
            "label": "Cast",
            "icon": "✦",
            "available": False,
            "disabled_reason": "spell choices not projected yet",
        },
        from_model("bonus-action", "Bonus", "◈", base_reason()),
        {
            "id": "item",
            "label": "Item",
            "icon": "◊",
            "available": False,
            "disabled_reason": "inventory combat actions not projected yet",
        },
        from_model("reaction", "Reaction", "✺", base_reason()),
        end_turn,
    ]


def _combat_ref(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    rid = _text(value.get("id"))
    name = _text(value.get("name"), rid)
    if not rid and not name:
        return None
    out = {"name": name}
    if rid:
        out["id"] = rid
    return out


def _combat_log_meta(label: str, value: object) -> dict | None:
    if value in (None, ""):
        return None
    return {"label": label, "value": value}


def _combat_battle_log(raw_events: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for row in raw_events or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        text = _text(row.get("text") or row.get("detail") or row.get("summary"))
        item = {"event": _text(row.get("kind") or row.get("type"), "combat"), "text": text[:1000]}
        if isinstance(payload, dict) and payload.get("schema") == "clawdnd.combat_event.v1":
            event = _text(payload.get("event"), "combat")
            actor = _combat_ref(payload.get("actor"))
            target = _combat_ref(payload.get("target"))
            title = "Combat"
            if actor and target:
                title = f"{actor['name']} -> {target['name']}"
            elif actor or target:
                title = (actor or target or {}).get("name", "Combat")
            meta: list[dict] = []
            roll = payload.get("roll")
            if isinstance(roll, dict):
                meta.extend(x for x in [
                    _combat_log_meta("d20", roll.get("natural")),
                    _combat_log_meta("roll", roll.get("total")),
                ] if x)
            damage = payload.get("damage")
            if isinstance(damage, dict):
                dmg = damage.get("total")
                dtype = _text(damage.get("type"))
                meta.append({"label": "Damage", "value": f"{dmg}{' ' + dtype if dtype else ''}"})
            item = {
                "event": event,
                "title": title,
                "text": text[:1000] if text else title,
                "actor": actor,
                "target": target,
                "meta": meta[:5],
            }
        if item.get("text") or item.get("title"):
            out.append(item)
        if len(out) >= 12:
            break
    return out


def build_combat_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
    recent_events: list[dict] | None = None,
) -> dict:
    """Project a browser-safe OpenWorlds combat board from engine-owned state."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    location = _session_location(snapshot)
    combat_view = build_combat_view(snapshot)
    action_model = build_action_model(snapshot, live=live, is_live_view=is_live_view)
    combat_active = bool(combat_view.get("active"))
    tokens, initiative, zones, selected, mode = _combat_tokens(snapshot, combat_view)
    action_bar = _combat_action_bar(action_model, combat_active)
    can_act = bool(live and is_live_view and combat_active)
    battle_log = _combat_battle_log(recent_events)
    summary = _text(snapshot.get("summary"))
    if not summary:
        summary = (
            f"Combat round {combat_view.get('round')}"
            if combat_active and combat_view.get("round") else
            _text(location.get("description"), "No active combat.")
        )

    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "dayLabel": _openworlds_day_label(snapshot),
        "location": location,
        "encounter": {
            "active": combat_active,
            "name": location["name"] if combat_active else "No active encounter",
            "summary": summary,
            "round": combat_view.get("round") if combat_active else None,
            "warnings": combat_view.get("warnings", []),
        },
        "grid": {"mode": mode, "cols": 16, "rows": 10},
        "tokens": tokens,
        "initiative": initiative,
        "zones": zones,
        "selectedTokenId": selected,
        "actionEconomy": (combat_view.get("actions") if combat_active else {}) or {},
        "actionBar": action_bar,
        "battleLog": battle_log,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": can_act,
        "state_authority": "engine",
        "write_lane": "/move",
    }


def _atlas_locations(snapshot: dict) -> dict[str, dict]:
    locs = snapshot.get("locations") if isinstance(snapshot, dict) else None
    return locs if isinstance(locs, dict) else {}


def _atlas_visible_location_ids(snapshot: dict) -> list[str]:
    locs = _atlas_locations(snapshot)
    current_id = _text(snapshot.get("current_location_id"))
    visible: list[str] = []
    for loc_id, row in locs.items():
        if not isinstance(row, dict):
            continue
        lid = _text(row.get("id"), _text(loc_id))
        if not lid:
            continue
        if lid == current_id:
            visible.append(lid)
            continue
        if bool(row.get("hidden")):
            continue
        discovered = row.get("discovered")
        if discovered is False and not bool(row.get("visited")):
            continue
        if bool(row.get("visited")) or discovered is True or discovered is None:
            visible.append(lid)
    return visible


def _atlas_hex_position(loc: dict, idx: int) -> tuple[int, int]:
    raw = loc.get("hex")
    q = r = None
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        q, r = _num(raw[0]), _num(raw[1])
    if q is not None and r is not None:
        x = 50 + int(q) * 18 + int(r) * 9
        y = 50 + int(r) * 14
        return max(8, min(92, x)), max(10, min(88, y))
    x = 24 + (idx % 4) * 18
    y = 24 + (idx // 4) * 18
    return max(8, min(92, x)), max(10, min(88, y))


def _atlas_tags(row: dict) -> list[str]:
    raw = row.get("tags")
    if isinstance(raw, list):
        return [_text(t).lower() for t in raw if _text(t)]
    out: list[str] = []
    for key in ("danger", "rest", "town", "camp", "safe"):
        if bool(row.get(key)):
            out.append(key)
    return out


def _atlas_known_locations(snapshot: dict) -> list[dict]:
    locs = _atlas_locations(snapshot)
    visible_ids = _atlas_visible_location_ids(snapshot)
    current_id = _text(snapshot.get("current_location_id"))
    out: list[dict] = []
    for idx, loc_id in enumerate(visible_ids):
        row = locs.get(loc_id)
        if not isinstance(row, dict):
            continue
        x, y = _atlas_hex_position(row, idx)
        name = _text(row.get("name"), loc_id)
        connections = row.get("connections")
        connections = connections if isinstance(connections, list) else []
        out.append({
            "id": loc_id,
            "name": name,
            "description": _text(row.get("description")),
            "region": _text(row.get("region"), _text(snapshot.get("world_id"), "World")),
            "visited": bool(row.get("visited")) or loc_id == current_id,
            "current": loc_id == current_id,
            "x": x,
            "y": y,
            "tags": _atlas_tags(row),
            "connections": [_text(c) for c in connections if _text(c) in visible_ids],
        })
    out.sort(key=lambda loc: (not loc["current"], loc["name"]))
    return out


def _atlas_edges(locations: list[dict]) -> list[dict]:
    known = {loc["id"] for loc in locations}
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for loc in locations:
        src = loc["id"]
        for dst in loc.get("connections", []):
            if dst not in known:
                continue
            key = tuple(sorted((src, dst)))
            if key in seen or src == dst:
                continue
            seen.add(key)
            out.append({"from": key[0], "to": key[1]})
    return out


def _atlas_move_reason(snapshot: dict, *, live: bool, is_live_view: bool) -> str | None:
    if not _atlas_locations(snapshot):
        return "no map data"
    if not live:
        return "no live move sink"
    if not is_live_view:
        return "viewing non-live campaign"
    if isinstance(snapshot.get("combat"), dict) and snapshot["combat"].get("active"):
        return "cannot travel during active combat"
    return None


def _atlas_travel_options(snapshot: dict, locations: list[dict], *, live: bool, is_live_view: bool) -> list[dict]:
    locs = _atlas_locations(snapshot)
    current_id = _text(snapshot.get("current_location_id"))
    current = locs.get(current_id) if current_id else None
    if not isinstance(current, dict):
        return []
    known = {loc["id"]: loc for loc in locations}
    travel_times = current.get("travel_times") if isinstance(current.get("travel_times"), dict) else {}
    disabled = _atlas_move_reason(snapshot, live=live, is_live_view=is_live_view)
    out: list[dict] = []
    for dst in current.get("connections", []) if isinstance(current.get("connections"), list) else []:
        dst_id = _text(dst)
        target = known.get(dst_id)
        if not target:
            continue
        minutes = travel_times.get(dst_id)
        minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None
        item = {
            "to": dst_id,
            "name": target["name"],
            "minutes": minutes,
            "available": disabled is None,
            "disabled_reason": disabled,
        }
        if disabled is None:
            item["move"] = {"kind": "do", "text": f"Travel to {target['name']}"}
        out.append(item)
    return out


def _atlas_faction_name(snapshot: dict, faction_id: str) -> str:
    factions = snapshot.get("factions")
    row = factions.get(faction_id) if isinstance(factions, dict) else None
    return _text(row.get("name"), faction_id) if isinstance(row, dict) else faction_id


def _atlas_quest_markers(snapshot: dict, visible_ids: set[str]) -> list[dict]:
    quests = snapshot.get("quests")
    if not isinstance(quests, dict):
        return []
    out: list[dict] = []
    for qid, row in quests.items():
        if not isinstance(row, dict):
            continue
        status = _text(row.get("status"), "active")
        if status not in {"active", "open"}:
            continue
        loc_id = _text(row.get("location_id"))
        if loc_id and loc_id not in visible_ids:
            continue
        objectives = row.get("objectives") if isinstance(row.get("objectives"), list) else []
        completed = set(row.get("completed_objectives") if isinstance(row.get("completed_objectives"), list) else [])
        objective = next((_text(o) for o in objectives if _text(o) and o not in completed), "")
        out.append({
            "id": _text(qid),
            "title": _text(row.get("title"), _text(qid)),
            "status": status,
            "location_id": loc_id,
            "objective": objective,
        })
    return out[:12]


def _atlas_strategic(snapshot: dict, visible_ids: set[str]) -> tuple[list[dict], list[dict], list[dict], int]:
    st = snapshot.get("strategic_state")
    if not isinstance(st, dict):
        return [], [], [], 0
    clocks: list[dict] = []
    for cid, row in (st.get("clocks") if isinstance(st.get("clocks"), dict) else {}).items():
        if not isinstance(row, dict):
            continue
        loc_id = _text(row.get("region_id") or row.get("location_id"))
        if loc_id and loc_id not in visible_ids:
            continue
        progress = _num(row.get("progress")) or 0
        target = _num(row.get("target")) or 1
        remaining = max(0, int(target) - int(progress))
        clocks.append({
            "id": _text(row.get("id"), _text(cid)),
            "title": _text(row.get("title"), _text(cid)),
            "kind": _text(row.get("kind"), "threat"),
            "location_id": loc_id,
            "progress": int(progress),
            "target": int(target),
            "remaining": remaining,
            "urgent": remaining <= 1 or (target > 0 and progress / target >= 0.75),
        })
    projects: list[dict] = []
    for pid, row in (st.get("projects") if isinstance(st.get("projects"), dict) else {}).items():
        if not isinstance(row, dict):
            continue
        loc_id = _text(row.get("location_id"))
        if loc_id and loc_id not in visible_ids:
            continue
        progress = _num(row.get("progress_days")) or 0
        duration = _num(row.get("duration_days")) or 1
        remaining = max(0, int(duration) - int(progress))
        status = _text(row.get("status"), "planned")
        projects.append({
            "id": _text(row.get("id"), _text(pid)),
            "title": _text(row.get("title"), _text(pid)),
            "kind": _text(row.get("kind"), "other"),
            "location_id": loc_id,
            "status": status,
            "progress_days": int(progress),
            "duration_days": int(duration),
            "remaining_days": remaining,
            "urgent": status in {"active", "paused"} and remaining <= 2,
        })
    regions: list[dict] = []
    for rid, row in (st.get("regions") if isinstance(st.get("regions"), dict) else {}).items():
        if not isinstance(row, dict):
            continue
        loc_id = _text(row.get("location_id"), _text(rid))
        if loc_id not in visible_ids:
            continue
        tags = row.get("tags")
        tags = tags if isinstance(tags, list) else []
        regions.append({
            "location_id": loc_id,
            "controller": _atlas_faction_name(snapshot, _text(row.get("controller_id"))),
            "stability": int(_num(row.get("stability")) or 0),
            "unrest": int(_num(row.get("unrest")) or 0),
            "tags": [_text(t) for t in tags if _text(t)],
        })
    last_tick_day = int(_num(st.get("last_tick_day")) or 0)
    clocks.sort(key=lambda c: (not c["urgent"], c["remaining"], c["title"]))
    projects.sort(key=lambda p: (not p["urgent"], p["remaining_days"], p["title"]))
    return clocks[:12], projects[:12], regions[:12], last_tick_day


def build_atlas_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project a browser-safe OpenWorlds atlas from engine-owned campaign state."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    locations = _atlas_known_locations(snapshot)
    visible_ids = {loc["id"] for loc in locations}
    current_id = _text(snapshot.get("current_location_id"))
    current = next((loc for loc in locations if loc["id"] == current_id), None)
    travel_options = _atlas_travel_options(snapshot, locations, live=live, is_live_view=is_live_view)
    clocks, projects, regions, last_tick_day = _atlas_strategic(snapshot, visible_ids)
    current_tags = set(current.get("tags", [])) if current else set()
    camp_available = bool(current and current_tags.intersection({"rest", "town", "safe", "camp"}))
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "dayLabel": _openworlds_day_label(snapshot),
        "current_location": current or {"id": "", "name": "Unknown location", "tags": []},
        "known_locations": locations,
        "edges": _atlas_edges(locations),
        "travel_options": travel_options,
        "quest_markers": _atlas_quest_markers(snapshot, visible_ids),
        "strategic_clocks": clocks,
        "downtime_projects": projects,
        "region_control": regions,
        "camp_available": camp_available,
        "last_world_tick": last_tick_day,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


def _relative_time_label(ts: float, *, now: float) -> str:
    if ts <= 0:
        return "unknown"
    delta = max(0, now - ts)
    if delta < 90:
        return "just now"
    if delta < 3600:
        minutes = max(1, int(delta // 60))
        return f"{minutes} min ago"
    if delta < 86400:
        hours = max(1, int(delta // 3600))
        return f"{hours} hr ago"
    if delta < 86400 * 7:
        days = max(1, int(delta // 86400))
        return f"{days} day{'s' if days != 1 else ''} ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _infer_catalog_provider(state_root: Path, source: str) -> str:
    if source == "qa":
        return "QA"
    if (state_root / "codex-provider").is_dir():
        return "Codex"
    if any(state_root.glob("companion_*.mcp.json")):
        return "Claude party"
    if (state_root / "dm.mcp.json").is_file():
        return "Claude"
    return "Local"


def build_openworlds_campaign_summary(
    source: str,
    run_id: str,
    campaign_id: str,
    snapshot: dict,
    *,
    campaign_dir: Path,
    state_root: Path,
    last_played: float,
    current: bool,
    can_resume: bool,
    now: float,
) -> dict:
    """Browser-safe OpenWorlds launcher row for one campaign.

    This deliberately projects only player-facing fields. It never includes local
    absolute paths, scene `dm_notes`, lore recall input, sealed agendas, or raw
    session transcripts; follow-on surfaces can add explicit read models when they
    need more data.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    live = (now - last_played) < 90
    location = _display_location(snapshot)
    world = _text(snapshot.get("world_id"), "unknown")
    title = _text(snapshot.get("title"), campaign_id)
    ruleset = _text(snapshot.get("ruleset"), "D&D 5e")
    day = _openworlds_day_label(snapshot)
    party = _party_cards(snapshot)
    active_quests = _active_quest_count(snapshot)
    source_label = "QA run" if source == "qa" else "Play save"
    where = location or world
    subtitle = f"{source_label} · {where}" if where else source_label
    recap = _text(snapshot.get("summary"))
    if not recap:
        if active_quests:
            recap = f"{active_quests} active quest{'s' if active_quests != 1 else ''} remain in motion."
        elif location:
            recap = f"The party is gathered near {location}."
        else:
            recap = "This chronicle is ready to continue."

    resume_url = f"/dashboard?campaign={quote(campaign_id)}" if can_resume else ""
    return {
        "id": f"{source}:{run_id}:{campaign_id}",
        "campaign_id": campaign_id,
        "source": source,
        "sourceLabel": "QA" if source == "qa" else "Play",
        "runId": run_id,
        "title": title,
        "subtitle": subtitle,
        "system": ruleset,
        "chapter": str(snapshot.get("day") or "I"),
        "lastPlayed": _relative_time_label(last_played, now=now),
        "last_played": last_played,
        "sessions": _session_count(campaign_dir),
        "region": location or world,
        "day": day,
        "world": world,
        "location": location,
        "party": party,
        "partyCount": len(party),
        "activeQuestCount": active_quests,
        "provider": _infer_catalog_provider(state_root, source),
        "live": live,
        "liveStatus": "live" if live else "stale",
        "current": bool(current),
        "canResume": bool(can_resume),
        "readOnly": not bool(can_resume),
        "resumeUrl": resume_url,
        "dashboardUrl": resume_url,
        "monitorUrl": "/monitor",
        "recap": recap,
    }


_openworlds_catalog_cache: tuple[object, list[dict]] | None = None


def _openworlds_catalog_index(
    roots: list[dict],
    attached_campaign: str,
) -> tuple[object, list[tuple[dict, Path, float]]]:
    """Return a small stat-based signature plus snapshots to project.

    The OpenWorlds launcher fetches once today, but this endpoint is a natural
    future poll target. Match the monitor's cache pattern: stat snapshots and
    session logs cheaply, then only re-read JSON when mtimes change.
    """
    current_campaigns_dir = _resolved(_campaigns_dir())
    sig_entries: list[tuple] = [("attached", attached_campaign), ("current", current_campaigns_dir)]
    snapshots: list[tuple[dict, Path, float]] = []
    for root in roots:
        cdir = root["campaigns_dir"]
        if not isinstance(cdir, Path) or not cdir.is_dir():
            continue
        for snap in cdir.glob("*/snapshot.json"):
            try:
                snap_mtime = snap.stat().st_mtime
                recency = _campaign_recency(snap)
            except OSError:
                continue
            sig_entries.append((
                str(root["source"]),
                str(root["run_id"]),
                _resolved(cdir),
                snap.parent.name,
                snap_mtime,
                recency,
            ))
            snapshots.append((root, snap, recency))
    return tuple(sig_entries), snapshots


def _refresh_openworlds_campaign_times(cards: list[dict], *, now: float) -> list[dict]:
    refreshed: list[dict] = []
    for card in cards:
        c = dict(card)
        last_played = c.get("last_played")
        last_played = (
            last_played
            if isinstance(last_played, (int, float)) and not isinstance(last_played, bool)
            else 0
        )
        live = (now - last_played) < 90
        c["live"] = live
        c["liveStatus"] = "live" if live else "stale"
        c["lastPlayed"] = _relative_time_label(last_played, now=now)
        refreshed.append(c)
    refreshed.sort(
        key=lambda c: (
            not c.get("current"),
            not c.get("live"),
            c.get("source") != "play",
            -c.get("last_played", 0),
        )
    )
    return refreshed


def _openworlds_campaigns(attached_campaign: str = "") -> dict:
    global _openworlds_catalog_cache
    now = time.time()
    roots = _campaign_catalog_roots()
    current_campaigns_dir = _resolved(_campaigns_dir())
    signature, snapshots = _openworlds_catalog_index(roots, attached_campaign)
    if _openworlds_catalog_cache and _openworlds_catalog_cache[0] == signature:
        out = _refresh_openworlds_campaign_times(_openworlds_catalog_cache[1], now=now)
    else:
        built: list[dict] = []
        for root, snap, recency in snapshots:
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict) or not data:
                continue
            cdir = root["campaigns_dir"]
            campaign_id = snap.parent.name
            root_is_current = bool(root["current_state"]) and _resolved(cdir) == current_campaigns_dir
            try:
                summary = build_openworlds_campaign_summary(
                    str(root["source"]),
                    str(root["run_id"]),
                    campaign_id,
                    data,
                    campaign_dir=snap.parent,
                    state_root=root["state_root"],
                    last_played=recency,
                    current=root_is_current and campaign_id == attached_campaign,
                    can_resume=root_is_current,
                    now=now,
                )
            except (OSError, TypeError, ValueError):
                continue
            built.append(summary)
        _openworlds_catalog_cache = (signature, built)
        out = _refresh_openworlds_campaign_times(built, now=now)
    return {
        "campaigns": out[:80],
        "total": len(out),
        "now": now,
        "state_authority": "engine",
        "write_lane": "/move",
    }


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


def _openworlds_config() -> dict:
    """Browser-safe metadata for the OpenWorlds shell.

    The UI bundle preserves the exported OpenWorlds surface, then binds the
    launcher to read-only viewer APIs. The remaining game screens still keep the
    prototype seed as a fallback until their own read models land.
    """
    return {
        "surface": "openworlds",
        "source": "OpenWorlds.zip",
        "mode": "viewer-read-model",
        "api_base": "",
        "campaign_catalog": "/openworlds/campaigns.json",
        "session_surface": "/session-surface",
        "combat_surface": "/combat-surface",
        "atlas_surface": "/atlas-surface",
        "state_authority": "engine",
        "write_lane": "/move",
        "demo_data": False,
        "demo_data_fallback": True,
    }


def _openworlds_mime(path: Path) -> str:
    ctype = _OPENWORLDS_MIME_TYPES.get(path.suffix.lower())
    if ctype:
        return ctype
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _openworlds_asset(route: str) -> Path | None:
    """Resolve a /openworlds asset path without allowing traversal outside the bundle."""
    openworlds_dir = _openworlds_dir()
    if route in (_OPENWORLDS_ROUTE, f"{_OPENWORLDS_ROUTE}/"):
        index = openworlds_dir / "index.html"
        return index if index.is_file() else None
    if not route.startswith(f"{_OPENWORLDS_ROUTE}/"):
        return None
    rel = unquote(route[len(_OPENWORLDS_ROUTE) + 1:])
    if not rel or rel.endswith("/"):
        rel = f"{rel}index.html"
    try:
        root = openworlds_dir.resolve()
        target = (root / rel).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


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

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        elif route == "/openworlds/config.json":
            self._json(_openworlds_config())
        elif route == "/openworlds/campaigns.json":
            self._json(_openworlds_campaigns(self.campaign_id))
        elif route == "/session-surface":
            qs = parse_qs(parsed.query)
            live = _live_play()
            catalog_ref = _session_surface_catalog_ref(qs)
            if catalog_ref is not None:
                cid, raw_snap, campaign_dir, root_is_current = catalog_ref
                self._json(build_session_surface(
                    raw_snap,
                    campaign_id=cid,
                    live=live,
                    is_live_view=bool(live and root_is_current and cid == self.campaign_id),
                    recent_events=_session_event_tail_from_dir(campaign_dir, raw_snap),
                ))
                return
            cid = self._view_campaign(qs)
            if not cid:
                self._json(build_session_surface({}, campaign_id="", live=live, is_live_view=False))
                return
            raw_snap = _read_snapshot(cid)
            if not isinstance(raw_snap, dict):
                raw_snap = {}
            self._json(build_session_surface(
                raw_snap,
                campaign_id=cid,
                live=live,
                is_live_view=live and cid == self.campaign_id,
                recent_events=_session_event_tail(cid),
            ))
        elif route == "/combat-surface":
            qs = parse_qs(parsed.query)
            live = _live_play()
            catalog_ref = _session_surface_catalog_ref(qs)
            if catalog_ref is not None:
                cid, raw_snap, campaign_dir, root_is_current = catalog_ref
                self._json(build_combat_surface(
                    raw_snap,
                    campaign_id=cid,
                    live=live,
                    is_live_view=bool(live and root_is_current and cid == self.campaign_id),
                    recent_events=_session_event_tail_from_dir(campaign_dir, raw_snap),
                ))
                return
            cid = self._view_campaign(qs)
            if not cid:
                self._json(build_combat_surface({}, campaign_id="", live=live, is_live_view=False))
                return
            raw_snap = _read_snapshot(cid)
            if not isinstance(raw_snap, dict):
                raw_snap = {}
            self._json(build_combat_surface(
                raw_snap,
                campaign_id=cid,
                live=live,
                is_live_view=live and cid == self.campaign_id,
                recent_events=_session_event_tail(cid),
            ))
        elif route == "/atlas-surface":
            qs = parse_qs(parsed.query)
            live = _live_play()
            catalog_ref = _session_surface_catalog_ref(qs)
            if catalog_ref is not None:
                cid, raw_snap, _campaign_dir_path, root_is_current = catalog_ref
                self._json(build_atlas_surface(
                    raw_snap,
                    campaign_id=cid,
                    live=live,
                    is_live_view=bool(live and root_is_current and cid == self.campaign_id),
                ))
                return
            cid = self._view_campaign(qs)
            if not cid:
                self._json(build_atlas_surface({}, campaign_id="", live=live, is_live_view=False))
                return
            raw_snap = _read_snapshot(cid)
            if not isinstance(raw_snap, dict):
                raw_snap = {}
            self._json(build_atlas_surface(
                raw_snap,
                campaign_id=cid,
                live=live,
                is_live_view=live and cid == self.campaign_id,
            ))
        elif route == _OPENWORLDS_ROUTE:
            suffix = f"?{parsed.query}" if parsed.query else ""
            self._redirect(f"{_OPENWORLDS_ROUTE}/{suffix}")
        elif route.startswith(f"{_OPENWORLDS_ROUTE}/"):
            asset = _openworlds_asset(route)
            if asset is None:
                self._send(404, b"not found", "text/plain")
            else:
                self._send(200, asset.read_bytes(), _openworlds_mime(asset))
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
