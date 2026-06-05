#!/usr/bin/env python3
"""WorldOS read-only play-view — a local web projection of campaign state (P3.6).

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
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

_HERE = Path(__file__).resolve().parent
# Make this dir importable so `import _env` resolves whether server.py is run as a
# script (sys.path[0] == viewer/) OR loaded via importlib.spec_from_file_location
# in the test suite (which does NOT add viewer/ to sys.path).
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _env import env_var, env_var_legacy  # noqa: E402  (after the path bootstrap above)
import ugc_store  # noqa: E402  GRAPHICS #453/#442 UGC profile store (sibling; path bootstrapped above)

# servers/voice lives two levels up from viewer/ (repo root / servers / voice).
_VOICE_DIR = _HERE.parent / "servers" / "voice"
# servers/engine — shelled (never imported) by POST /portrait-gen to generate a PC
# portrait through the engine's imagegen layer, mirroring how /speak shells the voice
# server and play.sh shells the engine. Keeps the viewer a pure reader of engine modules.
_ENGINE_DIR = _HERE.parent / "servers" / "engine"
_REPO_ROOT = _HERE.parent
_OPENWORLDS_DIR = _HERE / "openworlds"
_OPENWORLDS_ROUTE = "/openworlds"
_OPENWORLDS_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jsx": "text/babel; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".ttf": "font/ttf",
}

# The constrained move palette — the SAME lane the engine facade enforces. A human
# acting via the dashboard must not be able to POST DM-side narration ("the dragon
# dies"): only declared PLAYER moves of a known kind are accepted (H5). These are the
# kinds the dashboard emits (say/do free-text, check/save/combat/attack palette) plus
# the facade's cast/use_item, plus `clarify` (ask the DM a question before acting — a
# question, never a world-assertion, so it's a safe player-side move kind).
#
# Graphical-renderer intents (#429, graphics M0 — docs/roadmap/contracts/move-intents.md):
# `travel` (go to a known/adjacent location, target=engine_location_id), `inspect`/`examine`
# (look closely, target=location/actor/object id), `move_to_zone` (reposition within the
# current scene's named zones, target=zone name). These are PLAYER intents (never
# world-assertions): the renderer emits them on click; the DM reads them from the moves
# file and resolves them through the engine, exactly like say/do. They reuse the existing
# `target` field — no new field, so the anti-injection field-allowlist is untouched.
_MOVE_KINDS = {
    "say", "do", "check", "save", "combat", "attack", "cast", "use_item", "clarify",
    "travel", "inspect", "examine", "move_to_zone",
}
# Kinds whose payload is carried by `target` alone (no free `text`/`name`) — the graphical
# intents. Used to relax the "needs text or name" guard below for these click-driven moves.
_TARGET_ONLY_KINDS = {"travel", "inspect", "examine", "move_to_zone"}
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
    # The graphical intents (travel/inspect/examine/move_to_zone) are carried by `target`;
    # everything else needs a `text` or `name` so the DM has something to act on.
    if kind in _TARGET_ONLY_KINDS:
        if "target" not in move:
            return None, f"{kind!r} move needs a 'target'"
    elif "text" not in move and "name" not in move:
        return None, "move needs a 'text' or 'name'"
    return move, ""


def _state_dir() -> Path:
    """Mirror servers/engine/store.state_dir(): $WORLDOS_STATE_DIR (or the legacy
    $CLAWDND_STATE_DIR), else ~/.worldos/state if that home exists, else the legacy
    ~/.clawdnd/state."""
    env = env_var("STATE_DIR")
    if env:
        return Path(env)
    worldos_home = Path.home() / ".worldos" / "state"
    if worldos_home.parent.exists():
        return worldos_home
    return Path.home() / ".clawdnd" / "state"


def _campaigns_dir() -> Path:
    return _state_dir() / "campaigns"


def _ugc_root() -> Path:
    """GRAPHICS #453/#442 — server-owned UGC render-profile store (presentation artifacts, NOT
    game state, so the engine's sole-writership is untouched). Versioned append-only per owner."""
    return _state_dir() / "ugc" / "render-profiles"


def _moves_path() -> Path | None:
    """The single write target: $CLAWDND_PLAYER_MOVES, an append-only log of player
    *move intents* (NOT campaign state). Unset ⇒ no live game ⇒ no write path."""
    env = env_var("PLAYER_MOVES")
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
#
# W2b — ingested asset lookup:
# wiki_images.py writes a descriptor (wiki_ingest.json) under the gitignored path
#   content/worlds/_private/<world_id>/images/<safe-scope>/
# Resolution order for a scope:
#   1. Ingested asset (_private/<world_id>/images/<scope>/wiki_ingest.json) — newest wins
#   2. Generated imagegen cache (<state_dir>/images/<scope>/*.json)
#   3. 404 / placeholder fallback
# The _private root is added to the path-containment allowlist in _serve_image so file
# serving is safe even when descriptors carry absolute paths from a different machine.

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


def _art_repo_root() -> Path:
    """Repo root for gitignored private art.

    The macOS app and Lexar worktrees can run code from one checkout while the
    gitignored private art lives in the canonical checkout. Prefer the explicit
    WORLDOS_ART_REPO_ROOT/CLAWDND_ART_REPO_ROOT contract when it points at an art
    checkout. Fall back to WORLDOS_REPO_ROOT/CLAWDND_REPO_ROOT for v1.x launchers,
    then to the server.py parent checkout. Keep `_REPO_ROOT` as the fallback seam because
    the engine image tests patch it to isolate private-art descriptors.
    """
    for raw in (env_var("ART_REPO_ROOT"), env_var("REPO_ROOT")):
        if raw:
            candidate = Path(raw).expanduser()
            if (candidate / "content" / "worlds" / "_private").exists() or (candidate / "content" / "worlds").exists():
                return candidate
    return _REPO_ROOT


def _ingested_images_root() -> Path:
    """Root of the gitignored _private images tree (world-neutral).

    Returns content/worlds/_private/ inside the repo. This directory is covered by
    /content/worlds/_private/ in .gitignore so its contents are NEVER committed.
    """
    return _art_repo_root() / "content" / "worlds" / "_private"


_SCOPE_PREFIXES = {"portrait", "scene", "item", "map", "npc", "char", "pc", "loc", "location", "region", "scope", "faction", "creature", "class", "race"}


def _scope_key(scope: Optional[str]) -> str:
    """Normalize a scope to a separator/prefix-agnostic NAME key so the UI's engine-id
    scopes match the ingest manifest's readable slugs (the W2 integration seam): the screens
    fetch ``portrait-<character_id>`` / ``<location_id>`` while ingested art is keyed
    ``portrait:<slug>`` / ``scene:<slug>``. Lowercase; unify ``:`` / ``_`` / space to ``-``;
    drop LEADING kind/entity prefix tokens. e.g. ``portrait-npc-shadowheart`` and
    ``portrait:shadowheart`` both -> ``shadowheart``; ``loc-elfsong-tavern`` and
    ``scene:elfsong-tavern`` both -> ``elfsong-tavern``."""
    s = str(scope or "").lower().replace(":", "-").replace("_", "-").replace(" ", "-")
    toks = [t for t in s.split("-") if t]
    while toks and toks[0] in _SCOPE_PREFIXES:
        toks.pop(0)
    return "-".join(toks)


def _slug_variants(scope: Optional[str]) -> set[str]:
    """Generate normalized SLUG variants for a scope key so item-icon (and other) art
    resolves through common slug drift (the W2 integration's #2 art gap): US/UK spelling
    (armour/armor, colour/color, defence/defense), apostrophe-possessives folded by
    _scope_key into a stray `s` token (alchemist-s-fire / wavemother-s-robe), trailing
    numeric suffixes (rations-1, thieves-tools-1), and a singular/plural fold (rations /
    ration, boots / boot). Returns the base key PLUS every derived variant so the caller
    can match a requested scope against a descriptor's scope from either direction. Pure;
    a key with no drift just yields {itself}."""
    base = _scope_key(scope)
    if not base:
        return set()
    out: set[str] = {base}
    # Build from a working set so each transform composes (armour-1 -> armor).
    work = {base}

    def _add(transform) -> None:
        for v in list(work):
            nv = transform(v)
            if nv and nv != v:
                work.add(nv)
                out.add(nv)

    # US/UK spelling folds (apply to the whole key — substrings are fine, these are
    # word fragments that don't collide with unrelated slugs).
    def _spell(v: str) -> str:
        for uk, us in (("armour", "armor"), ("colour", "color"),
                       ("defence", "defense"), ("vapour", "vapor"), ("flavour", "flavor")):
            v = v.replace(uk, us)
        return v
    _add(_spell)
    # Drop a trailing numeric disambiguator (rations-1 -> rations).
    _add(lambda v: re.sub(r"-\d+$", "", v))
    # Fold a possessive `-s-` that _scope_key left as its own token
    # (alchemist-s-fire -> alchemist-fire; wavemother-s-robe -> wavemother-robe).
    _add(lambda v: v.replace("-s-", "-"))
    # Drop a possessive/plural `s` tail glued to ANY token (alchemists-fire ->
    # alchemist-fire; wavemothers-robe -> wavemother-robe), so a possessive whether
    # split (`-s-`) or glued resolves to the same base. Only strips a trailing `s` from a
    # token long enough to be a word and not ending in `ss` (so `glass`/`brass` survive).
    def _depossess_each(v: str) -> str:
        toks = v.split("-")
        return "-".join(
            t[:-1] if (len(t) > 3 and t.endswith("s") and not t.endswith("ss")) else t
            for t in toks
        )
    _add(_depossess_each)
    # Singular/plural fold on the LAST token (rations -> ration, boots -> boot), and the
    # inverse (ration -> rations) so a singular request hits a plural dir too.
    def _plural(v: str) -> str:
        toks = v.split("-")
        if toks and len(toks[-1]) > 3 and toks[-1].endswith("s") and not toks[-1].endswith("ss"):
            return "-".join(toks[:-1] + [toks[-1][:-1]])
        return v

    def _singular_to_plural(v: str) -> str:
        toks = v.split("-")
        if toks and len(toks[-1]) > 2 and not toks[-1].endswith("s"):
            return "-".join(toks[:-1] + [toks[-1] + "s"])
        return v
    _add(_plural)
    _add(_singular_to_plural)
    return out


def _load_ingested_descriptor(desc_path: Path) -> Optional[dict]:
    """Load a wiki_ingest.json + re-anchor its `path` field to live next to the
    descriptor. The ingest pipeline writes an absolute `path` at ingest time (for
    example, /path/to/sidecar/class_fighter/image.png); when the repo is cloned / moved /
    checked out on a different machine, that original absolute path no longer exists
    and `_serve_image`'s containment check correctly rejects it — even though the
    image bytes ARE present at the canonical location next to wiki_ingest.json
    inside _private/. The image always lives next to its descriptor, so we
    canonicalize on read. This makes ingested art portable across cross-disk clones
    without re-ingesting + without changing the on-disk descriptor files."""
    try:
        d = json.loads(desc_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(d, dict):
        return None
    p = d.get("path")
    if isinstance(p, str) and p:
        # Use only the basename, anchored at the descriptor's parent. The image is
        # always a sibling of wiki_ingest.json inside the same scope directory.
        d["path"] = str(desc_path.parent / Path(p).name)
    return d


def _ingested_descriptor(scope: Optional[str]) -> Optional[dict]:
    """Look up a wiki_ingest.json descriptor for scope across ALL world _private dirs.

    Searches content/worlds/_private/<any-world>/images/<safe-scope>/wiki_ingest.json.
    Returns the first readable descriptor found, or None. Path-traversal safe: scope
    is sanitised and the result is confirmed to sit inside _ingested_images_root().
    """
    seg = _safe_scope(scope)
    if not seg:
        return None
    root = _ingested_images_root()
    if not root.is_dir():
        return None
    for world_dir in root.iterdir():
        if not world_dir.is_dir():
            continue
        desc_path = world_dir / "images" / seg / "wiki_ingest.json"
        if not desc_path.exists():
            continue
        # Containment check: descriptor must resolve under _private root.
        try:
            if root.resolve() not in desc_path.resolve().parents:
                continue
        except OSError:
            continue
        d = _load_ingested_descriptor(desc_path)
        if d is not None:
            return d
    # Normalized-key fallback (W2 integration): the UI fetches engine-id scopes
    # (portrait-<character_id>, <location_id>) while ingested assets are keyed by manifest
    # slugs (portrait:<slug>, scene:<slug>). When the exact safe-scope dir misses, match by
    # normalized name-key so wiki art resolves regardless of separator/prefix. Same path
    # containment guard. This is what makes the render bridge SHOW the ingested portraits.
    #
    # SLUG-DRIFT fallback (P2 art gap): item-icon scopes 404 on US/UK spelling (armour/armor),
    # apostrophe-possessives, plurals, and trailing `-1` disambiguators even when the art is
    # present under a drifted dir name. So match against the VARIANT SET of both the requested
    # scope and the descriptor's scope — an overlap means the same item. Exact-key matches are
    # tried first (a same-key dir always wins); the variant overlap only rescues a miss.
    want = _scope_key(scope)
    if not want:
        return None
    want_variants = _slug_variants(scope)
    fuzzy_hit: Optional[dict] = None
    for world_dir in root.iterdir():
        images_dir = world_dir / "images"
        if not images_dir.is_dir():
            continue
        for sub in images_dir.iterdir():
            desc_path = sub / "wiki_ingest.json"
            if not desc_path.exists():
                continue
            try:
                if root.resolve() not in desc_path.resolve().parents:
                    continue
            except OSError:
                continue
            d = _load_ingested_descriptor(desc_path)
            if d is None:
                continue
            dkey = _scope_key(d.get("scope"))
            if dkey == want:
                return d  # exact normalized-key match wins outright
            if fuzzy_hit is None and (want_variants & _slug_variants(d.get("scope"))):
                fuzzy_hit = d  # remember the first slug-drift match; prefer an exact hit if one exists later
    return fuzzy_hit


def _newest_json_descriptor(cdir: Path) -> Optional[dict]:
    """Most-recently-written *.json under a directory, parsed. None on miss/error."""
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


def _latest_descriptor(scope: Optional[str]) -> Optional[dict]:
    """Resolve the best image descriptor for a scope.

    Resolution order (W2b):
      1. Ingested asset — content/worlds/_private/<world>/images/<scope>/wiki_ingest.json
      2. Generated imagegen cache — <state_dir>/images/<scope>/*.json (newest)
      3. None → 404

    The cache is rebuildable, never load-bearing — a bad entry is just a miss.
    """
    seg = _safe_scope(scope)
    if not seg:
        return None
    # 1. Ingested asset (wiki_images.py output) — takes priority over generated cache
    ingested = _ingested_descriptor(scope)
    if ingested is not None:
        return ingested
    # 2. Generated imagegen cache (existing behaviour)
    return _newest_json_descriptor(_images_dir(scope))


def _portrait_by_name(scope: Optional[str], campaign_id: str) -> Optional[dict]:
    """Resolve a ``portrait-<engine-id>`` scope by the character's NAME when the id itself
    has no art. PCs are minted with opaque engine ids (``char_09bfb0ec913c``) while canon
    NPCs keep readable slug ids (``npc-astarion``) that resolve directly. A canon character
    pulled into the party as a PC therefore requests ``portrait-char_…`` — a miss — even
    though their real ingested face lives at ``portrait-<name-slug>``. This bridges that gap:
    look the character up in the snapshot, then retry the lookup keyed by canon_id / name
    slug, so canon faces render on every screen. Returns None (→ silhouette placeholder)
    only when the character genuinely has no ingested face. Read-only; a miss stays a miss."""
    raw = (scope or "").strip().lower()
    if not raw.startswith(("portrait", "pc", "npc", "char")):
        return None
    key = _scope_key(scope)
    if not key or not campaign_id:
        return None
    snap = _read_snapshot(campaign_id)
    chars = snap.get("characters") if isinstance(snap, dict) else None
    if not isinstance(chars, dict):
        return None
    ch = chars.get(key)
    if not isinstance(ch, dict):
        for cid, cand in chars.items():
            if isinstance(cand, dict) and (_scope_key(cid) == key or _scope_key(cand.get("name", "")) == key):
                ch = cand
                break
    if not isinstance(ch, dict):
        return None
    tries: list[str] = []
    for fld in ("canon_id", "canonical_id", "slug"):
        v = ch.get(fld)
        if isinstance(v, str) and v.strip():
            tries.append("portrait-" + _scope_key(v))
    name = ch.get("name")
    if isinstance(name, str) and name.strip():
        tries.append("portrait-" + _scope_key(name))
    for cand_scope in tries:
        if _scope_key(cand_scope) == key:
            continue  # same miss we already tried
        desc = _latest_descriptor(cand_scope)
        if desc is not None:
            return desc
    return None


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


def build_bestiary_response(query: str = "", limit: int = 20, campaign_id: str = "", reference: bool = False) -> dict:
    """GET /bestiary-surface read model.

    Bridges to the engine-owned player-safe bestiary projection. It exposes no write
    path and does not import or call any campaign/combat mutation helper.

    When ``campaign_id`` resolves to a live snapshot, the campaign's earned ``bestiary_intel``
    (creature_slug -> max tier) is loaded READ-ONLY and threaded into the engine projection so
    the codex reveals stats per intel tier (#263) — sighted/engaged/slain. With no campaign (or
    no snapshot / no recorded intel) the surface stays the honest global SRD browse (tier-less
    preview), so an empty/new game is never a stat dump. The engine stays the projection
    authority and the sole writer; this only reads the snapshot and passes a dict in.

    When ``reference`` is set, the campaign intel is BYPASSED and the surface returns the global
    SRD browse (every match → names + tier-less preview stats). This is the codex "Browse all"
    reference mode: in real play the intel codex is perpetually fog-of-war (the party rarely
    slays enough to reveal much), so a player needs a way to read public monster facts (identity,
    CR, the preview stat line) without it being gated on kills. Still strictly read-only.
    """
    engine = _load_engine_server()
    if engine is None:
        return {
            "items": [],
            "validation_errors": [],
            "error": f"engine import failed: {_ENGINE_IMPORT_ERROR}",
        }
    intel: Optional[dict] = None
    safe = _safe_campaign_id(campaign_id) if campaign_id else ""
    if safe and not reference:
        snap = _read_snapshot(safe)
        raw = snap.get("bestiary_intel") if isinstance(snap, dict) else None
        if isinstance(raw, dict):
            # Coerce to the {slug: int-tier} shape the engine expects; ignore malformed rows.
            cleaned: dict[str, int] = {}
            for slug, tier in raw.items():
                if isinstance(slug, str) and isinstance(tier, int) and not isinstance(tier, bool):
                    cleaned[slug] = tier
            intel = cleaned
    return engine.bestiary.player_bestiary(query, limit, intel=intel)


# The default world the picker browses when no campaign scopes it yet (a brand-new game, the
# launcher's "Begin a chronicle" path). This is the shipped post-BG3 setting; a campaign with a
# real ``world_id`` overrides it below.
_DEFAULT_ROSTER_WORLD = "baldurs-gate"


def _roster_world_for_campaign(campaign_id: str) -> str:
    """Which world's canon roster the picker browses. A campaign with a snapshot carries the
    authoritative ``world_id``; otherwise (no/empty/unknown campaign — the new-game path) fall
    back to the shipped default world. Read-only: just inspects the snapshot."""
    safe = _safe_campaign_id(campaign_id) if campaign_id else ""
    if safe:
        snap = _read_snapshot(safe)
        if isinstance(snap, dict):
            wid = snap.get("world_id")
            if isinstance(wid, str) and wid.strip():
                return wid.strip()
    return _DEFAULT_ROSTER_WORLD


def build_roster_response(
    campaign_id: str = "",
    race: str = "",
    char_class: str = "",
    level: str = "",
    limit: int = 120,
    world_id: Optional[str] = "",
) -> dict:
    """GET /roster-surface read model — the canon-NPC PICKER ("reverse character creator").

    Bridges to the engine-owned canon-roster projection (``content.roster_surface``): the
    PLAYABLE roster (origins/legends EXCLUDED via the record ``playable`` flag), filtered by
    race / class / level, each row carrying {id, name, race, class, level, role, backstory
    snippet, portrait_scope}. Also returns the distinct race/class/level ``facets`` so the picker
    can offer filter chips. The world is resolved from the campaign's snapshot (or the shipped
    default for a brand-new game). READ-ONLY — exposes no write/seat path; the bind happens via
    the native startProviderSession bridge / load_canon_character, never here. Mirrors
    build_bestiary_response: a graceful empty payload when the engine can't be imported."""
    engine = _load_engine_server()
    if world_id is None:
        world_id = ""
    else:
        world_id = world_id.strip() if isinstance(world_id, str) else ""
        if not world_id:
            world_id = _roster_world_for_campaign(campaign_id)
    if engine is None or not hasattr(engine, "content_mod") or not hasattr(engine.content_mod, "roster_surface"):
        detail = _ENGINE_IMPORT_ERROR or "engine roster projection is unavailable"
        return {
            "world_id": world_id,
            "total": 0,
            "returned": 0,
            "characters": [],
            "facets": {"races": [], "classes": [], "levels": []},
            "error": f"engine import failed: {detail}",
        }
    try:
        return engine.content_mod.roster_surface(
            world_id,
            race=race,
            char_class=char_class,
            level=level,
            playable_only=True,
            limit=limit,
        )
    except Exception as exc:
        return {
            "world_id": world_id,
            "total": 0,
            "returned": 0,
            "characters": [],
            "facets": {"races": [], "classes": [], "levels": []},
            "error": str(exc),
        }


def _clean_slot(slot: Optional[str]) -> str:
    """Normalize a caller-supplied save-slot name to a flat, safe-ish slug (the engine's
    store.safe_path_segment is the authoritative guard; this just trims/defaults). Empty ⇒
    'quicksave', the only slot the Settings UI uses today."""
    s = (slot or "").strip() if isinstance(slot, str) else ""
    return s or "quicksave"


def _save_load_slot_response(action: str, campaign_id: Optional[str], slot: Optional[str]) -> dict:
    """POST /save-slot | /load-slot bridge — the ONLY viewer write path besides /move.

    The viewer never writes campaign state itself: it path-validates the campaign id, then calls
    the engine-owned save_slot / load_slot MCP tool in-process (same in-process engine bridge the
    read-only /build-options surface uses). The engine performs the snapshot copy/restore under
    its own campaign_lock + save_campaign, so the sole-writer invariant holds. `action` is
    'save' or 'load'. Returns the engine tool's verdict, or a structured {ok:false, reason} on a
    bad id / missing slot / engine-unavailable / engine error so the UI can toast a clear message."""
    safe_campaign = _safe_campaign_id(campaign_id)
    if not safe_campaign:
        return {"ok": False, "reason": "missing or unsafe campaign id"}
    safe_slot = _clean_slot(slot)

    engine = _load_engine_server()
    tool_name = "save_slot" if action == "save" else "load_slot"
    if engine is None or not hasattr(engine, tool_name):
        detail = _ENGINE_IMPORT_ERROR or f"engine {tool_name} is unavailable"
        return {"ok": False, "reason": f"engine save lane unavailable: {detail}"}

    try:
        result = getattr(engine, tool_name)(safe_campaign, safe_slot)
    except FileNotFoundError:
        # load of a slot that was never written — a clean, expected "nothing to restore".
        return {"ok": False, "reason": f"no '{safe_slot}' save to restore — make a quicksave first"}
    except Exception as exc:  # unknown campaign / corrupt slot / id mismatch / engine error
        return {"ok": False, "reason": str(exc)}
    if isinstance(result, dict):
        return result
    return {"ok": True, "campaign_id": safe_campaign, "slot": safe_slot}


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


def _list_campaigns(attached: str | None = None) -> list[dict]:
    """All projectable campaigns under the campaigns dir, newest-active first (#H3 switcher).

    One entry per campaign is a read-only save card with title, day, location, party, quest
    count, live/current flags, and last-played recency. Empty/unparseable snapshots are
    skipped — the SAME guard _pick_campaign uses, so a half-written/`{}` snapshot never
    shows as a pickable game. Sorted by recency descending. Pure reader: no writes, no
    engine import.

    `attached` names which campaign is the live/attached one (sets each card's `current`
    flag). It defaults to the served handler's attached id; callers that have ALREADY resolved
    it (the live-view recovery) pass it explicitly so the flag never depends on class-attribute
    shadowing or a mid-request re-resolve."""
    cdir = _campaigns_dir()
    out: list[dict] = []
    if not cdir.is_dir():
        return out
    if attached is None:
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


def _positive_int(value: object, default: int = 1) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _openworlds_calendar_projection(snapshot: dict) -> dict:
    calendar = snapshot.get("calendar") if isinstance(snapshot, dict) else None
    day = _positive_int(snapshot.get("day") if isinstance(snapshot, dict) else None)
    fallback = {
        "available": False,
        "canonical_day": day,
        "label": _openworlds_legacy_day_label(snapshot),
    }
    if not isinstance(calendar, dict):
        return fallback

    months_raw = calendar.get("months")
    months = [m for m in months_raw if isinstance(m, dict)] if isinstance(months_raw, list) else []
    if not months:
        return fallback

    elapsed = day - 1
    month_index = min(max(_positive_int(calendar.get("epoch_month"), 1) - 1, 0), len(months) - 1)
    day_of_month = _positive_int(calendar.get("epoch_day"), 1)
    first_days = _positive_int(months[month_index].get("days"), 1)
    day_of_month = min(day_of_month, first_days)
    year = calendar.get("epoch_year") if isinstance(calendar.get("epoch_year"), int) else 1
    remaining = elapsed
    while remaining:
        month_days = _positive_int(months[month_index].get("days"), 1)
        days_left = month_days - day_of_month
        if remaining <= days_left:
            day_of_month += remaining
            remaining = 0
        else:
            remaining -= days_left + 1
            day_of_month = 1
            month_index += 1
            if month_index >= len(months):
                month_index = 0
                year += 1

    month = months[month_index]
    weekdays_raw = calendar.get("weekdays")
    weekdays = [_text(w) for w in weekdays_raw if _text(w)] if isinstance(weekdays_raw, list) else []
    week_start = calendar.get("week_start_index") if isinstance(calendar.get("week_start_index"), int) else 0
    weekday = weekdays[(week_start + elapsed) % len(weekdays)] if weekdays else ""
    era = _text(calendar.get("era_suffix"))
    era_suffix = f" {era}" if era else ""
    month_name = _text(month.get("name"), "Month")
    date_core = f"{day_of_month} {month_name} {year}{era_suffix}"
    date_label = f"{weekday}, {date_core}" if weekday else date_core
    time_of_day = _text(snapshot.get("time_of_day"))
    label = date_label + (f" · {time_of_day}" if time_of_day else "")

    moons = []
    moons_raw = calendar.get("moons")
    if isinstance(moons_raw, list):
        for moon in moons_raw:
            if not isinstance(moon, dict):
                continue
            cycle = _positive_int(moon.get("cycle_days"), 1)
            epoch_phase = moon.get("epoch_phase_day") if isinstance(moon.get("epoch_phase_day"), int) else 0
            age = (max(epoch_phase, 0) + elapsed) % cycle
            phases_raw = moon.get("phase_names")
            phases = [_text(p) for p in phases_raw if _text(p)] if isinstance(phases_raw, list) else []
            if not phases:
                phases = ["new", "waxing", "full", "waning"]
            phase_index = min(len(phases) - 1, (age * len(phases)) // cycle)
            moons.append(
                {
                    "name": _text(moon.get("name"), "Moon"),
                    "age": age,
                    "cycle_days": cycle,
                    "phase": phases[phase_index],
                }
            )

    return {
        "available": True,
        "calendar": _text(calendar.get("name"), "Calendar"),
        "canonical_day": day,
        "year": year,
        "month": month_name,
        "day_of_month": day_of_month,
        "weekday": weekday,
        "season": _text(month.get("season")),
        "date_label": date_label,
        "label": label,
        "moons": moons,
    }


def _openworlds_legacy_day_label(snapshot: dict) -> str:
    day = snapshot.get("day")
    time_of_day = _text(snapshot.get("time_of_day"))
    if isinstance(day, int):
        return f"Day {day}" + (f" · {time_of_day}" if time_of_day else "")
    return time_of_day or "Unknown time"


def _openworlds_day_label(snapshot: dict) -> str:
    calendar = _openworlds_calendar_projection(snapshot)
    return _text(calendar.get("label")) if calendar.get("available") else _openworlds_legacy_day_label(snapshot)


# Map the engine's free-form ``time_of_day`` string onto the four atlas day/night phases
# the World Map shades by. Clock-driven: the viewer reads this off the live snapshot rather
# than sniffing the day label, so the Dawn/Day/Dusk/Night indicator can never disagree with
# the campaign clock. Unknown / empty → "day" (neutral, no tint).
def _openworlds_time_phase(snapshot: dict) -> str:
    raw = _text(snapshot.get("time_of_day") if isinstance(snapshot, dict) else "").lower()
    if not raw:
        return "day"
    # First substring match wins, so order specific phrases (e.g. "afternoon") ahead of
    # their broader roots ("noon"). Dawn/dusk/night roots are unambiguous.
    for needle, phase in (
        ("midnight", "night"), ("night", "night"),
        ("daybreak", "dawn"), ("dawn", "dawn"), ("sunrise", "dawn"), ("morning", "dawn"),
        ("twilight", "dusk"), ("dusk", "dusk"), ("sunset", "dusk"), ("evening", "dusk"),
        ("afternoon", "day"), ("midday", "day"), ("noon", "day"), ("day", "day"),
    ):
        if needle in raw:
            return phase
    return "day"


def _openworlds_calendar(snapshot: dict) -> dict:
    return _openworlds_calendar_projection(snapshot)


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
                "detail": _text(action.get("detail")),
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


def _session_action_buckets(actions: list[dict]) -> tuple[list[dict], list[dict]]:
    enabled: list[dict] = []
    blocked: list[dict] = []
    for action in actions:
        item = dict(action)
        if item.get("available"):
            item.pop("disabled_reason", None)
            enabled.append(item)
        else:
            item["disabled_reason"] = _text(item.get("disabled_reason"), "not available")
            blocked.append(item)
    return enabled, blocked


def _session_write_lane_metadata() -> dict:
    return {
        "endpoint": "/move",
        "method": "POST",
        "authority": "engine",
        "payload": "player_move_intent",
        "writesCampaignSnapshot": False,
        "allowedKinds": sorted(_MOVE_KINDS),
    }


def _session_consequence_context(snapshot: dict) -> dict:
    current_day = snapshot.get("day") if isinstance(snapshot.get("day"), int) else None
    consequences = snapshot.get("consequences")
    signals: list[dict] = []
    due_count = 0
    pending_count = 0
    if isinstance(consequences, list):
        for idx, consequence in enumerate(consequences):
            if not isinstance(consequence, dict):
                continue
            resolved = bool(consequence.get("resolved") or consequence.get("fired"))
            trigger_day = consequence.get("trigger_day", consequence.get("day"))
            trigger = trigger_day if isinstance(trigger_day, int) and not isinstance(trigger_day, bool) else None
            due = bool(not resolved and current_day is not None and trigger is not None and trigger <= current_day)
            if due:
                due_count += 1
            elif not resolved:
                pending_count += 1
            signal = {
                "id": _text(consequence.get("id"), f"consequence-{idx + 1}"),
                "status": "resolved" if resolved else ("due" if due else "pending"),
            }
            if trigger is not None:
                signal["triggerDay"] = trigger
            signals.append(signal)
            if len(signals) >= 6:
                break
    return {
        "dueCount": due_count,
        "pendingCount": pending_count,
        "signals": signals,
    }


def _session_action_context(snapshot: dict, location: dict, summary: str, quests: list[dict]) -> dict:
    return {
        "scene": {
            "summary": summary,
            "location": _text(location.get("name"), "Unknown location"),
            "time": _openworlds_day_label(snapshot),
        },
        "quests": [
            {
                "id": _text(q.get("id")),
                "title": _text(q.get("title")),
                "objective": _text(q.get("objective")),
                "status": _text(q.get("status"), "active"),
                "location": _text(q.get("location")),
            }
            for q in quests[:4]
            if isinstance(q, dict)
        ],
        "consequences": _session_consequence_context(snapshot),
    }


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
        # Carry the stable session-log line index (`seq`) through to the surface when present, so the
        # viewer's leading history band (recentEvents) de-dups against the live /events tail by ID,
        # not by prose. _session_event_tail_from_dir stamps it; an older snapshot/path without it
        # simply omits the key and the viewer falls back to its text-key dedup for that row.
        # BUG2: a bare line index is NOT unique across a session ROTATION (cold-open start_session +
        # the DM-turn-retry re-mint, 5e71f77) — the new session's log restarts at 0,1,2, the same
        # indices the prior session already claimed. So carry the resolved session id (`sid`) too; the
        # viewer composes `${sid}:${seq}` as the globally-unique dedup/order key, so a fresh session's
        # narration is no longer suppressed by collision with a prior session's seq 0,1,2.
        seq = row.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            item["seq"] = seq
        sid = row.get("sid")
        if isinstance(sid, str) and sid:
            item["sid"] = sid
        event_at = row.get("t")
        if isinstance(event_at, (int, float)) and not isinstance(event_at, bool):
            item["eventAt"] = event_at
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
    # Compute the ABSOLUTE line index of the first tailed line so each row can carry the SAME
    # stable `seq` key as the /events feed (both index the same session log). The tail returns only
    # the last `limit` lines, so its base index is (total non-truncated lines − len(tail)). A line's
    # absolute index is base+offset; this lets the leading history band (recentEvents) and the live
    # /events tail dedup against ONE id space — a paragraph in both bands matches by seq, never by
    # its (rewordable) prose. Best-effort: if the count read fails we fall back to no seq (the
    # client's text-key fallback still de-dups), so this never breaks the surface.
    tail = _tail_text_lines(log, limit)
    base = 0
    try:
        with log.open("r", encoding="utf-8", errors="replace") as fh:
            total = sum(1 for _ in fh)
        base = max(0, total - len(tail))
    except OSError:
        base = 0
    out: list[dict] = []
    for offset, raw in enumerate(tail):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            row.setdefault("seq", base + offset)
            # BUG2: stamp the resolved session id so recentEvents composes the SAME `${sid}:${seq}`
            # key the live /events tail does — a bare line index collides across a session rotation.
            row.setdefault("sid", sid)
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
    actor = action_model.get("actor") if isinstance(action_model.get("actor"), dict) else None
    combat_view = build_combat_view(snapshot)
    actions = _session_available_actions(action_model)
    enabled_actions, blocked_actions = _session_action_buckets(actions)
    combat_active = bool(combat_view.get("active"))
    round_no = combat_view.get("round")
    active_quests = _session_active_quests(snapshot)
    summary = _text(snapshot.get("summary"))
    if not summary:
        summary = _text(location.get("description"), f"The party is gathered near {location['name']}.")
    action_context = _session_action_context(snapshot, location, summary, active_quests)

    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "day": snapshot.get("day") if isinstance(snapshot.get("day"), int) else None,
        "time_of_day": _text(snapshot.get("time_of_day")),
        "dayLabel": _openworlds_day_label(snapshot),
        "calendar": _openworlds_calendar(snapshot),
        "location": location,
        "scene": {
            "summary": summary,
            "caption": location["name"],
            "imageScope": f"location:{location['id']}" if location["id"] else "",
        },
        "actor": actor,
        "party": party,
        "conditions": _session_conditions(party),
        "activeQuests": active_quests,
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
        "enabledActions": enabled_actions,
        "blockedActions": blocked_actions,
        "actionContext": action_context,
        "recentEvents": _session_recent_events(recent_events),
        "actionModel": action_model,
        "combatView": combat_view,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
        "writeLane": _session_write_lane_metadata(),
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
        # #432 (graphics M0): x/y here are a DERIVED render-hint, never authoritative state.
        # On the "zone" path they are synthesized from the engine's named zone (the engine
        # has no per-combatant coordinates — Combatant.zone is the only spatial truth). A
        # renderer/AI-loop MUST treat `zone` as authoritative and re-derive its own layout;
        # it must NOT persist x/y as state (the engine would silently overwrite it). The
        # `positionAuthority` flag makes that explicit so no downstream consumer mistakes the
        # hint for truth. ("grid" source = the engine actually supplied coords, a future
        # capability; "zone"/"theater" = derived.)
        token["positionAuthority"] = "engine" if source == "grid" else "derived"
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


def _combat_slot(available: object, spent_reason: str) -> dict:
    available_bool = bool(available)
    return {
        "available": available_bool,
        "spent": not available_bool,
        "reason": "" if available_bool else spent_reason,
    }


def _combat_death_save_text(death_saves: object) -> str:
    if not isinstance(death_saves, dict):
        return "0 success / 0 fail"
    successes = _num(death_saves.get("successes"))
    failures = _num(death_saves.get("failures"))
    return f"{int(successes or 0)} success / {int(failures or 0)} fail"


def _combat_character_cues(ch: dict, token: dict) -> list[dict]:
    cues: list[dict] = []
    cid = _text(token.get("id"))
    name = _text(token.get("name"), cid or "Combatant")
    concentration = _text(ch.get("concentration"))
    if concentration:
        cues.append({
            "type": "concentration",
            "severity": "info",
            "character_id": cid,
            "label": f"{name} concentrating",
            "text": concentration,
        })
    hp = _num(ch.get("current_hp"))
    dead = bool(ch.get("dead"))
    stable = bool(ch.get("stable"))
    if dead:
        cues.append({
            "type": "death",
            "severity": "danger",
            "character_id": cid,
            "label": f"{name} dead",
            "text": "dead",
        })
    elif hp == 0 and not stable:
        cues.append({
            "type": "death_saves",
            "severity": "danger",
            "character_id": cid,
            "label": f"{name} dying",
            "text": _combat_death_save_text(ch.get("death_saves")),
        })
    elif hp == 0 and stable:
        cues.append({
            "type": "stable",
            "severity": "warning",
            "character_id": cid,
            "label": f"{name} stable",
            "text": "0 HP, stable",
        })
    return cues


def _combat_multiattack_count(snapshot: dict, active_id: str) -> int:
    chars = snapshot.get("characters") if isinstance(snapshot, dict) else {}
    ch = chars.get(active_id) if isinstance(chars, dict) else None
    if isinstance(ch, dict):
        for key in ("multiattack", "attacks_per_turn"):
            value = _num(ch.get(key))
            if value is not None and value > 1:
                return int(value)
    combat = snapshot.get("combat") if isinstance(snapshot, dict) else None
    turn_brief = combat.get("turn_brief") if isinstance(combat, dict) else None
    if not isinstance(turn_brief, dict):
        turn_brief = snapshot.get("turn_brief") if isinstance(snapshot, dict) else None
    attack = turn_brief.get("attack") if isinstance(turn_brief, dict) else None
    if isinstance(attack, dict):
        value = _num(attack.get("attacks_per_turn"))
        if value is not None and value > 1:
            return int(value)
    return 0


def _combat_command_center(
    snapshot: dict,
    *,
    tokens: list[dict],
    initiative: list[dict],
    combat_view: dict,
    action_model: dict,
    battle_log: list[dict],
) -> dict:
    """Browser-safe command-center read model derived from engine-owned combat data."""
    combat = snapshot.get("combat") if isinstance(snapshot, dict) else {}
    combat = combat if isinstance(combat, dict) else {}
    chars = snapshot.get("characters") if isinstance(snapshot, dict) else {}
    chars = chars if isinstance(chars, dict) else {}
    active = next((t for t in tokens if t.get("isCurrent")), None)
    actor_id = _text(active.get("id")) if isinstance(active, dict) else ""
    actor_ch = chars.get(actor_id) if actor_id else {}
    actor_ch = actor_ch if isinstance(actor_ch, dict) else {}
    actions = combat_view.get("actions") if isinstance(combat_view.get("actions"), dict) else {}
    action_model_combat = action_model.get("combat") if isinstance(action_model.get("combat"), dict) else {}
    actor_team = _text(active.get("team")) if isinstance(active, dict) else ""

    active_actor = {}
    if isinstance(active, dict):
        active_actor = {
            "id": actor_id,
            "name": _text(active.get("name"), actor_id),
            "team": actor_team,
            "initiative": active.get("initiative"),
            "zone": _text(active.get("zone")),
            "conditions": [str(c) for c in active.get("conditions", []) if str(c)]
            if isinstance(active.get("conditions"), list)
            else [],
            "cues": _combat_character_cues(actor_ch, active),
        }
        concentration = _text(actor_ch.get("concentration"))
        if concentration:
            active_actor["concentration"] = concentration

    made = _num(combat.get("action_attacks_made")) or 0
    surge = _num(combat.get("surge_actions")) or 0
    extra = _num(actor_ch.get("extra_attacks")) or 0
    multiattack = _combat_multiattack_count(snapshot, actor_id)
    per_action = max(int(extra) + 1 if actor_id else 0, multiattack)
    allowed = per_action * (1 + max(0, int(surge))) if actor_id else 0
    remaining = max(0, allowed - int(made))

    cues: list[dict] = []
    targetability: list[dict] = []
    action_available = bool(actions.get("action_available")) and bool(action_model_combat.get("is_current_turn", True))
    for token in tokens:
        if not isinstance(token, dict):
            continue
        tid = _text(token.get("id"))
        ch = chars.get(tid) if tid else {}
        ch = ch if isinstance(ch, dict) else {}
        token_cues = _combat_character_cues(ch, token)
        cues.extend(token_cues)
        reason = ""
        targetable = False
        if tid == actor_id:
            reason = "self"
        elif _text(token.get("team")) == actor_team:
            reason = "ally"
        elif bool(ch.get("dead")):
            reason = "dead"
        elif not action_available:
            reason = "action unavailable"
        else:
            targetable = True
        targetability.append({
            "id": tid,
            "name": _text(token.get("name"), tid),
            "team": _text(token.get("team")),
            "zone": _text(token.get("zone")),
            "health": _text(token.get("health"), "unknown"),
            "conditions": [str(c) for c in token.get("conditions", []) if str(c)]
            if isinstance(token.get("conditions"), list)
            else [],
            "targetable": targetable,
            "reason": reason,
            "cues": token_cues,
        })

    return {
        "activeActor": active_actor,
        "initiativeLadder": initiative,
        "slots": {
            "action": _combat_slot(actions.get("action_available"), "action spent"),
            "bonusAction": _combat_slot(actions.get("bonus_available"), "bonus action spent"),
            "reaction": _combat_slot(actions.get("reaction_available"), "reaction spent"),
        },
        "attackBudget": {
            "made": int(made),
            "allowed": int(allowed),
            "remaining": int(remaining),
            "extraAttacks": int(extra),
            "surgeActions": int(surge),
            "multiattack": int(multiattack),
        },
        "targetability": targetability,
        "cues": cues,
        "eventCards": battle_log,
    }


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
    command_center = _combat_command_center(
        snapshot,
        tokens=tokens,
        initiative=initiative,
        combat_view=combat_view,
        action_model=action_model,
        battle_log=battle_log,
    )
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
        "commandCenter": command_center,
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


def _safe_str_list(raw: object) -> list[str]:
    return [_text(item) for item in raw if _text(item)] if isinstance(raw, list) else []


def _atlas_known_locations(snapshot: dict) -> list[dict]:
    locs = _atlas_locations(snapshot)
    visible_ids = _atlas_visible_location_ids(snapshot)
    current_id = _text(snapshot.get("current_location_id"))
    graph = _atlas_world_graph(snapshot)
    out: list[dict] = []
    for idx, loc_id in enumerate(visible_ids):
        row = locs.get(loc_id)
        if not isinstance(row, dict):
            continue
        node_meta = _atlas_node_meta(graph, loc_id)
        x, y = _atlas_hex_position(row, idx)
        name = _text(row.get("name"), loc_id)
        connections = row.get("connections")
        connections = connections if isinstance(connections, list) else []
        item = {
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
        }
        if node_meta:
            item.update({
                "biome": _text(node_meta.get("biome")),
                "terrain": _text(node_meta.get("terrain")),
                "danger": node_meta.get("danger") if isinstance(node_meta.get("danger"), int) else 0,
                "atlas_layer": _text(node_meta.get("atlas_layer"), "site"),
                "graph_tags": _safe_str_list(node_meta.get("tags")),
            })
        out.append(item)
    out.sort(key=lambda loc: (not loc["current"], loc["name"]))
    return out


def _atlas_world_graph(snapshot: dict) -> dict:
    graph = snapshot.get("world_graph")
    return graph if isinstance(graph, dict) else {}


def _atlas_node_meta(graph: dict, loc_id: str) -> dict:
    nodes = graph.get("nodes")
    row = nodes.get(loc_id) if isinstance(nodes, dict) else None
    return row if isinstance(row, dict) else {}


def _atlas_edge_meta(graph: dict, src: str, dst: str) -> dict:
    edges = graph.get("edges")
    if not isinstance(edges, list):
        return {}
    for row in edges:
        if not isinstance(row, dict):
            continue
        a = _text(row.get("from_id"))
        b = _text(row.get("to_id"))
        if {a, b} == {src, dst}:
            return row
    return {}


def _atlas_edge_payload(meta: dict) -> dict:
    if not meta:
        return {}
    out: dict = {}
    for key in ("route_kind", "difficulty"):
        value = _text(meta.get(key))
        if value:
            out[key] = value
    for key in ("minutes", "danger"):
        value = meta.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    tags = _safe_str_list(meta.get("tags"))
    if tags:
        out["tags"] = tags
    return out


def _atlas_edges(locations: list[dict], snapshot: dict | None = None) -> list[dict]:
    known = {loc["id"] for loc in locations}
    graph = _atlas_world_graph(snapshot or {})
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
            item = {"from": key[0], "to": key[1]}
            item.update(_atlas_edge_payload(_atlas_edge_meta(graph, src, dst)))
            out.append(item)
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
    graph = _atlas_world_graph(snapshot)
    disabled = _atlas_move_reason(snapshot, live=live, is_live_view=is_live_view)
    out: list[dict] = []
    for dst in current.get("connections", []) if isinstance(current.get("connections"), list) else []:
        dst_id = _text(dst)
        target = known.get(dst_id)
        if not target:
            continue
        minutes = travel_times.get(dst_id)
        minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None
        edge_meta = _atlas_edge_meta(graph, current_id, dst_id)
        edge_payload = _atlas_edge_payload(edge_meta)
        minutes = minutes if minutes is not None else edge_payload.get("minutes")
        item = {
            "to": dst_id,
            "name": target["name"],
            "minutes": minutes,
            "available": disabled is None,
            "disabled_reason": disabled,
        }
        for key in ("route_kind", "difficulty", "danger", "tags"):
            if key in edge_payload:
                item[key] = edge_payload[key]
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


def _atlas_settlements(snapshot: dict, visible_ids: set[str]) -> list[dict]:
    st = snapshot.get("strategic_state")
    if not isinstance(st, dict):
        return []
    raw = st.get("settlements")
    if not isinstance(raw, dict):
        return []

    out: list[dict] = []
    for sid, row in raw.items():
        if not isinstance(row, dict):
            continue
        loc_id = _text(row.get("location_id"), _text(sid))
        if loc_id not in visible_ids:
            continue
        faction_ids = row.get("public_faction_ids")
        faction_ids = faction_ids if isinstance(faction_ids, list) else []
        establishments = row.get("establishments")
        establishments = establishments if isinstance(establishments, list) else []
        public_npcs = row.get("public_npcs")
        public_npcs = public_npcs if isinstance(public_npcs, list) else []
        out.append({
            "location_id": loc_id,
            "settlement_type": _text(row.get("settlement_type"), "town"),
            "governance": _text(row.get("governance")),
            "public_safety": _text(row.get("public_safety")),
            "economy": _text(row.get("economy")),
            "unrest": int(_num(row.get("unrest")) or 0),
            "public_factions": [
                _atlas_faction_name(snapshot, _text(fid))
                for fid in faction_ids
                if _text(fid)
            ],
            "establishments": [_text(name) for name in establishments if _text(name)][:8],
            "public_npcs": [
                {
                    "npc_id": _text(npc.get("npc_id")),
                    "role": _text(npc.get("role")),
                    "pressure": _text(npc.get("pressure")),
                }
                for npc in public_npcs
                if isinstance(npc, dict) and (_text(npc.get("role")) or _text(npc.get("pressure")))
            ][:8],
        })
    out.sort(key=lambda s: (s["location_id"], s["settlement_type"]))
    return out[:12]


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
    settlements = _atlas_settlements(snapshot, visible_ids)
    current_tags = set(current.get("tags", [])) if current else set()
    camp_available = bool(current and current_tags.intersection({"rest", "town", "safe", "camp"}))
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "dayLabel": _openworlds_day_label(snapshot),
        "time_of_day": _text(snapshot.get("time_of_day")),
        "time_phase": _openworlds_time_phase(snapshot),
        "calendar": _openworlds_calendar(snapshot),
        "current_location": current or {"id": "", "name": "Unknown location", "tags": []},
        "known_locations": locations,
        "edges": _atlas_edges(locations, snapshot),
        "travel_options": travel_options,
        "quest_markers": _atlas_quest_markers(snapshot, visible_ids),
        "strategic_clocks": clocks,
        "downtime_projects": projects,
        "region_control": regions,
        "settlements": settlements,
        "camp_available": camp_available,
        "last_world_tick": last_tick_day,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


# ── Campaign Director advisory (issue #72) ────────────────────────────────────
# The viewer is a downstream reader. To surface the Campaign Director's structural
# debts (journal "GM Advisory" + the table widget) we PREFER the engine's own pure
# detectors (scene_debt.detect + director.compute) by building a Campaign model from
# the resolved snapshot — this gives the exact same ranked top-3 the DM's
# get_campaign_director tool returns, for ANY snapshot the viewer projects (play store
# OR a QA/catalog run). If the engine/pydantic isn't importable we degrade to a
# snapshot-only heuristic covering the cheap, structural debt kinds. Read-only: it
# detects + advises off in-memory facts, never mutating fiction or the snapshot.

def _director_advisory(snapshot: dict, *, limit: int = 3) -> dict:
    """Return ``{"debts": [...], "advisory": [...], "total_debts": int, "source": str}``
    for a snapshot. Each debt row is ``{id, kind, subject, detail, severity, nudge}``.
    Empty debts == no structural debts (or no snapshot)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if not snapshot:
        return {"debts": [], "advisory": [], "total_debts": 0, "source": "empty"}
    engine = _load_engine_server()
    if engine is not None:
        try:
            import models as _models  # the engine dir is on sys.path after _load_engine_server
            import scene_debt as _sd
            import director as _director

            campaign = _models.Campaign.model_validate(snapshot)
            ranked = _director.compute(campaign)  # already top-3, highest severity first
            all_live = _sd.detect(campaign)
            rows: list[dict] = []
            for debt, nudge in zip(ranked.get("debts", []), ranked.get("advisory", [])):
                if not isinstance(debt, dict):
                    continue
                rows.append({
                    "id": _text(debt.get("id")),
                    "kind": _text(debt.get("kind")),
                    "subject": _text(debt.get("subject")),
                    "detail": _text(debt.get("detail")),
                    "severity": _text(debt.get("severity"), "med"),
                    "nudge": _text(nudge),
                })
            return {
                "debts": rows[:limit],
                "advisory": [r["nudge"] for r in rows[:limit]],
                "total_debts": int(ranked.get("total_debts", len(all_live))),
                "source": "engine.director",
            }
        except Exception:
            pass  # fall through to the snapshot-only heuristic
    return _director_advisory_heuristic(snapshot, limit=limit)


_SEV_RANK = {"high": 0, "med": 1, "low": 2}


def _director_advisory_heuristic(snapshot: dict, *, limit: int = 3) -> dict:
    """Snapshot-only fallback for the Campaign Director (no engine/pydantic). Detects the
    cheap, purely-structural debt kinds the DM advisory cares about from raw snapshot
    facts: engaged-but-untracked hooks, overdue authored consequences, and active quests
    with no recent decision callback. Ranked high→med→low to mirror director.compute."""
    quests = snapshot.get("quests") if isinstance(snapshot.get("quests"), dict) else {}
    hooks = snapshot.get("quest_hooks") if isinstance(snapshot.get("quest_hooks"), list) else []
    consequences = snapshot.get("consequences") if isinstance(snapshot.get("consequences"), list) else []
    decisions = snapshot.get("decisions") if isinstance(snapshot.get("decisions"), list) else []
    day = snapshot.get("day") if isinstance(snapshot.get("day"), int) else 1

    def _decision_text() -> str:
        out: list[str] = []
        for d in decisions:
            if isinstance(d, dict):
                out.append(" ".join(_text(d.get(k)) for k in ("summary", "rationale", "chosen")))
                opts = d.get("options")
                if isinstance(opts, list):
                    out.append(" ".join(_text(o) for o in opts))
        return " ".join(out).lower()

    blob = _decision_text()
    rows: list[dict] = []

    # hook_untracked: a hook marked active (player bit) with no tracked quest referencing it.
    quest_titles = [_text(q.get("title")).lower() for q in quests.values() if isinstance(q, dict)]
    quest_blob = " ".join(filter(None, [*quest_titles, *quests.keys()])).lower()
    for h in hooks:
        if not isinstance(h, dict):
            continue
        status = _text(h.get("status"), "open").lower()
        if status == "resolved":
            continue
        hid = _text(h.get("id"))
        htitle = _text(h.get("title"))
        engaged = status == "active" or (hid and hid.lower() in blob) or (htitle and htitle.lower() in blob)
        tracked = (hid and hid.lower() in quest_blob) or (htitle and htitle.lower() in quest_blob)
        if engaged and not tracked:
            label = htitle or hid or "this hook"
            rows.append({
                "id": f"hook:{hid}", "kind": "hook_untracked", "subject": hid,
                "detail": f"Hook '{label}' is active but has no tracked Quest.",
                "severity": "high",
                "nudge": f"Untracked hook '{label}' — call add_quest to promote it into a tracked quest.",
            })

    # due_consequence: an authored (non-thread) consequence past its trigger day, not fired.
    for con in consequences:
        if not isinstance(con, dict) or _text(con.get("thread_id")) or bool(con.get("fired")):
            continue
        trigger = con.get("trigger_day")
        if isinstance(trigger, int) and trigger <= day:
            overdue = day - trigger
            note = _text(con.get("note") or con.get("text"))
            rows.append({
                "id": f"con:{_text(con.get('id'))}", "kind": "due_consequence", "subject": _text(con.get("id")),
                "detail": f"Consequence is due ({overdue}d overdue).",
                "severity": "high" if overdue >= 2 else "med",
                "nudge": f"Consequence due ({overdue}d overdue) — call check_consequences: '{note[:60]}'." if overdue else f"Consequence is due — call check_consequences: '{note[:60]}'.",
            })

    # quest_stalled: active quest with no decision callback (campaign must be past day 5).
    if day > 5:
        for qid, q in quests.items():
            if not isinstance(q, dict) or _text(q.get("status"), "active").lower() != "active":
                continue
            title = _text(q.get("title"))
            if (qid.lower() in blob) or (title and title.lower() in blob):
                continue
            rows.append({
                "id": f"quest:{qid}", "kind": "quest_stalled", "subject": _text(qid),
                "detail": f"Quest '{title or qid}' has no story callback recently.",
                "severity": "med",
                "nudge": f"Quest '{title or qid}' has stalled — weave an advancement beat to move it forward.",
            })

    rows.sort(key=lambda r: _SEV_RANK.get(r["severity"], 9))
    return {
        "debts": rows[:limit],
        "advisory": [r["nudge"] for r in rows[:limit]],
        "total_debts": len(rows),
        "source": "viewer.heuristic",
    }


def _journal_quests(snapshot: dict) -> list[dict]:
    """Project every quest into the journal's shape (id/title/label/tone/status/objective +
    a checklist of objectives with done flags + entries). Status is normalized to the
    journal's tabs: active / complete / rumor (a hook-less, open-only state)."""
    quests = snapshot.get("quests")
    locs = snapshot.get("locations")
    out: list[dict] = []
    if not isinstance(quests, dict):
        return out
    for qid, row in quests.items():
        if not isinstance(row, dict):
            continue
        raw_status = _text(row.get("status"), "active").lower()
        if raw_status in {"completed", "complete", "resolved"}:
            status, label, tone = "complete", "Resolved", "emerald"
        elif raw_status in {"failed"}:
            status, label, tone = "complete", "Failed", "crimson"
        else:
            status, label, tone = "active", "Active", "crimson"
        objectives_raw = row.get("objectives")
        completed = row.get("completed_objectives")
        completed_set = {str(o) for o in completed} if isinstance(completed, list) else set()
        objectives: list[dict] = []
        next_objective = ""
        if isinstance(objectives_raw, list):
            for item in objectives_raw:
                text = _text(item)
                if not text:
                    continue
                done = text in completed_set
                objectives.append({"text": text, "done": done})
                if not done and not next_objective:
                    next_objective = text
        if not next_objective:
            next_objective = _text(row.get("description"), "Continue the investigation.")
        location_id = _text(row.get("location_id"))
        region = ""
        if isinstance(locs, dict) and location_id:
            loc = locs.get(location_id)
            if isinstance(loc, dict):
                region = _text(loc.get("region")) or _text(loc.get("name"), location_id)
        # Rule-of-three evolution (#120): a quest carrying an `evolves_to` hook/seed will
        # echo back as a scheduled callback once resolved. Surface the badge display-only.
        evolves_to = _text(row.get("evolves_to"))
        callback_raw = _num(row.get("callback_in_days"))
        callback_in_days = int(callback_raw) if callback_raw is not None else 0
        out.append({
            "id": _text(qid),
            "title": _text(row.get("title"), _text(qid, "Quest")),
            "label": label,
            "tone": tone,
            "status": status,
            "region": region,
            "objective": next_objective,
            "entry": _text(row.get("description"), "No chronicle entry has been recorded for this quest yet."),
            "objectives": objectives,
            "entries": [],
            "location_id": location_id,
            "evolvesTo": evolves_to,
            "callbackInDays": callback_in_days if evolves_to else 0,
        })
    return out


def _journal_hooks(snapshot: dict) -> list[dict]:
    """Project unresolved quest_hooks as journal 'rumors' (the lore-derived seeds the DM
    hasn't promoted yet). Spine hooks are flagged; resolved ones are dropped."""
    hooks = snapshot.get("quest_hooks")
    out: list[dict] = []
    if not isinstance(hooks, list):
        return out
    for h in hooks:
        if not isinstance(h, dict):
            continue
        status = _text(h.get("status"), "open").lower()
        if status == "resolved":
            continue
        title = _text(h.get("title")) or _text(h.get("grievance")) or _text(h.get("id"), "A rumor")
        out.append({
            "id": _text(h.get("id"), title),
            "title": title,
            "label": "Spine" if bool(h.get("spine")) else "Rumor",
            "tone": "royal" if bool(h.get("spine")) else "",
            "status": "rumor",
            "spine": bool(h.get("spine")),
            "objective": _text(h.get("note")) or _text(h.get("arc_back")) or "An unverified thread the party has not yet pulled.",
            "entry": _text(h.get("note"), "Heard third-hand. The accounts vary."),
            "objectives": [],
            "entries": [],
        })
    return out


# The deterministic Consequence.note tag the engine writes when a resolved quest with an
# `evolves_to` hook schedules its follow-on (server._evolution_note -> "evolves_from:<id>").
# It's a stable contract — both authored by and guarded on by the engine — so the journal
# can project scheduled evolutions read-only by matching this prefix.
_EVOLUTION_NOTE_PREFIX = "evolves_from:"


def _journal_evolutions(snapshot: dict) -> list[dict]:
    """Project scheduled quest-evolution callbacks (#120) — the "this thread will return"
    threads. Reads `consequences` whose `note` is the engine's deterministic
    ``evolves_from:<quest_id>`` tag (server._maybe_schedule_quest_evolution), links each
    back to its resolved quest, and marks it due / pending against the current in-world
    day. Display-only: it never schedules, fires, or mutates anything. World-sim background
    beats (a non-empty ``thread_id``) are NOT evolutions and are skipped."""
    cons = snapshot.get("consequences")
    quests = snapshot.get("quests") if isinstance(snapshot.get("quests"), dict) else {}
    out: list[dict] = []
    if not isinstance(cons, list):
        return out
    day = _num(snapshot.get("day"))
    day = int(day) if day is not None else 0
    for con in cons:
        if not isinstance(con, dict):
            continue
        note = _text(con.get("note"))
        if not note.startswith(_EVOLUTION_NOTE_PREFIX):
            continue
        if _text(con.get("thread_id")):
            continue  # a worldsim background beat, not a quest evolution
        quest_id = note[len(_EVOLUTION_NOTE_PREFIX):].strip()
        q = quests.get(quest_id) if isinstance(quests.get(quest_id), dict) else {}
        trigger_day = _num(con.get("trigger_day"))
        trigger_day = int(trigger_day) if trigger_day is not None else day
        fired = bool(con.get("fired"))
        due = (not fired) and trigger_day <= day
        out.append({
            "id": _text(con.get("id"), note),
            "questId": quest_id,
            "questTitle": _text(q.get("title"), quest_id or "a resolved thread"),
            "evolvesTo": _text(q.get("evolves_to")),
            "triggerDay": trigger_day,
            "fired": fired,
            "due": due,
            "status": "fired" if fired else ("due" if due else "pending"),
            "label": "Echo paid" if fired else ("Echo due" if due else "Echo pending"),
            # Player-facing telegraph — never the engine's DM-only "weave a follow-on beat"
            # prompt text. Display-only.
            "note": (
                f"A resolved thread waits to return"
                + (f" on day {trigger_day}." if not due and not fired else
                   (" now." if due else "; it has already echoed back."))
            ),
        })
    return out


# --- World-Seed read model (#266) -------------------------------------------
# The mutable World-Seed parameters surfaced + edited by the OpenWorlds Seed screen.
# These MIRROR the engine's set_seed_param classification (servers/engine/server.py:
# SEED_PARAMS_FREE/GATED/LOCKED) so the policy stays one shape on both sides; the viewer
# stays a pure stdlib reader (no engine import) and the engine remains the enforcement
# point — this matrix is for honest UI rendering + the /seed-param sanitizer.
_SEED_MUTABILITY = {
    "tone": "free",
    "narration": "free",
    "gm_strictness": "free",
    "chronicle_voice": "free",
    "anachronism": "free",
    "chronicler_notes": "free",
    "difficulty": "gated",
    "permadeath": "gated",
    "fate_dice": "gated",
    "item_destruction": "gated",
    "system": "locked",
}
# Defaults == the engine SeedParams defaults (today's behavior) so a snapshot lacking a
# seed_params block projects honest defaults rather than blanks. difficulty defaults to the
# HouseRules default; system reflects the ruleset (read off the snapshot below).
_SEED_PARAM_DEFAULTS = {
    "tone": "Heroic",
    "narration": "florid",
    "gm_strictness": "standard",
    "chronicle_voice": "first_person_plural",
    "anachronism": True,
    "chronicler_notes": "",
    "permadeath": False,
    "fate_dice": True,
    "item_destruction": False,
    "difficulty": "standard",  # lives on house_rules.difficulty
}
# Free params whose value is a closed string set + bool params, for sanitize_seed_param.
_SEED_PARAM_STR_VALUES = {
    "tone": {"Heroic", "Grim", "Picaresque", "Mythic"},
    "narration": {"terse", "balanced", "florid", "almost_poetic"},
    "gm_strictness": {"permissive", "standard", "strict", "pedantic"},
    "chronicle_voice": {
        "first_person_singular", "first_person_plural", "second_person",
        "third_person_omniscient", "third_person_close",
    },
    "difficulty": {"easy", "standard", "hard"},
}
_SEED_PARAM_BOOLS = {"anachronism", "permadeath", "fate_dice", "item_destruction"}
_SEED_PARAM_FREETEXT = {"chronicler_notes"}
_SEED_NOTES_MAXLEN = 2000


def _seed_pattern(campaign_id: str) -> str:
    """A STABLE, decorative 'Pattern' fingerprint derived from the campaign id (S-10:
    'derive Pattern from the seed' instead of a hardcoded literal). Deterministic so it
    never re-rolls on a poll; cosmetic only (a sha1 slice formatted aaaa-bbbb-cccc)."""
    if not campaign_id:
        return ""
    h = hashlib.sha1(campaign_id.encode("utf-8")).hexdigest()
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}"


def _seed_identity(snapshot: dict, campaign_id: str) -> dict:
    """De-fake the screen-seed StatLine block (S-03) from REAL campaign fields. Every value
    is sourced from the snapshot — nothing hardcoded. `seeded` is the real-world sowing date
    (created_at); `era` is the in-world chronology; `engine` pairs ruleset + the engine SHA;
    `pattern` is the stable id fingerprint; `by` is the world the chronicle was sown from."""
    created = snapshot.get("created_at")
    seeded_epoch = float(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else None
    seeded = ""
    if seeded_epoch is not None:
        try:
            seeded = time.strftime("%d %b %Y", time.localtime(seeded_epoch))
        except (OSError, ValueError, OverflowError):
            seeded = ""
    ruleset = _text(snapshot.get("ruleset"), "SRD 5.2")
    engine_sha = _text(snapshot.get("engine_sha"))
    engine = f"{ruleset} · {engine_sha[:7]}" if engine_sha else ruleset
    return {
        "seeded": seeded,                                   # real-world sow date (created_at)
        "seeded_epoch": seeded_epoch,                       # raw, for the UI to reformat
        "by": _text(snapshot.get("world_id"), "the chronicle"),  # provenance (world bible)
        "era": _text(snapshot.get("era")),                  # in-world chronology
        "pattern": _seed_pattern(campaign_id),              # stable id fingerprint (S-10)
        "engine": engine,                                   # ruleset + real engine SHA
        "ending": _text(snapshot.get("ending_id")),         # optional post-state overlay label
    }


def build_seed_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project the OpenWorlds World-Seed read model (#266): the live seed IDENTITY (de-faking
    the hardcoded StatLine — S-03), the live `params` each control binds to, the `mutability`
    matrix (so the UI renders gates/warnings without hardcoding policy), and `session_started`
    (which escalates gated→needs-force). Pure projection from the snapshot — no engine import,
    no writes. ``present:false`` → the UI shows an honest empty-state (S-07)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    present = bool(campaign_id and snapshot)
    if not present:
        return {
            "campaign_id": campaign_id or "",
            "present": False,
            "title": "",
            "identity": {},
            "params": {},
            "mutability": _SEED_MUTABILITY,
            "session_started": False,
            "live": bool(live),
            "is_live_view": bool(is_live_view),
            "can_act": False,
            "state_authority": "engine",
            "write_lane": {"endpoint": "/seed-param", "method": "POST", "authority": "engine"},
        }

    seed_raw = snapshot.get("seed_params")
    seed_raw = seed_raw if isinstance(seed_raw, dict) else {}
    house_rules = snapshot.get("house_rules")
    house_rules = house_rules if isinstance(house_rules, dict) else {}

    # Build params from real state, defaulting to today's behavior where a key is absent
    # (an old snapshot with no seed_params block projects honest defaults, not blanks).
    params: dict = {}
    for key, default in _SEED_PARAM_DEFAULTS.items():
        if key == "difficulty":
            params[key] = _text(house_rules.get("difficulty"), default)
        elif key in _SEED_PARAM_BOOLS:
            val = seed_raw.get(key)
            params[key] = bool(val) if isinstance(val, bool) else default
        elif key in _SEED_PARAM_FREETEXT:
            params[key] = _text(seed_raw.get(key), default)
        else:  # closed-string params
            params[key] = _text(seed_raw.get(key), default)
    # system is the ruleset (locked); surface it so the UI shows it read-only.
    params["system"] = _text(snapshot.get("ruleset"), "SRD 5.2")

    session_ids = snapshot.get("session_ids")
    session_started = bool(isinstance(session_ids, list) and session_ids)

    return {
        "campaign_id": campaign_id,
        "present": True,
        "title": _text(snapshot.get("title"), campaign_id),
        "identity": _seed_identity(snapshot, campaign_id),
        "params": params,
        "mutability": _SEED_MUTABILITY,
        "session_started": session_started,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": {"endpoint": "/seed-param", "method": "POST", "authority": "engine"},
    }


def sanitize_seed_param(raw: object) -> tuple[Optional[dict], str]:
    """Validate + normalize a /seed-param payload into a `set_seed_param` INTENT line.
    Returns ``(intent, "")`` on success or ``(None, reason)`` on rejection. role is forced
    to 'player' (the DM agent is the trusted applier and re-validates server-side); the
    param must be a known seed param; the value is type-checked against its class (closed
    string set / bool / capped free text); a LOCKED param (system) is refused at the lane
    (the engine also raises). This is the SAME defense-in-depth shape as sanitize_move —
    the engine remains the SOLE writer; this only relays a validated request."""
    if not isinstance(raw, dict):
        return None, "seed-param must be a JSON object"
    param = str(raw.get("param", "")).strip()
    cls = _SEED_MUTABILITY.get(param)
    if cls is None:
        return None, f"unknown seed param {param!r}"
    if cls == "locked":
        return None, f"seed param {param!r} is locked post-seed"
    value = raw.get("value")
    if param in _SEED_PARAM_BOOLS:
        if not isinstance(value, bool):
            return None, f"{param!r} expects a boolean"
    elif param in _SEED_PARAM_FREETEXT:
        if not isinstance(value, str):
            return None, f"{param!r} expects a string"
        value = value[:_SEED_NOTES_MAXLEN]
    elif param in _SEED_PARAM_STR_VALUES:
        if not isinstance(value, str) or value not in _SEED_PARAM_STR_VALUES[param]:
            return None, f"{param!r} expects one of {sorted(_SEED_PARAM_STR_VALUES[param])}"
    else:  # should be unreachable given the matrix, but fail closed
        return None, f"{param!r} is not settable via this lane"
    intent: dict = {"role": "player", "kind": "set_seed_param", "param": param, "value": value}
    if bool(raw.get("force")):
        intent["force"] = True
    return intent, ""


def build_journal_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project a browser-safe OpenWorlds quest journal from engine-owned state: every
    tracked quest (active/complete), unresolved hooks as rumors, and the Campaign
    Director's top structural debts (issue #72) as a GM advisory."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    quests = _journal_quests(snapshot) + _journal_hooks(snapshot)
    advisory = _director_advisory(snapshot)
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "dayLabel": _openworlds_day_label(snapshot),
        "quests": quests,
        # Scheduled quest-evolution callbacks (#120) — the "Threads & Callbacks" sub-list.
        "threads": _journal_evolutions(snapshot),
        "directorAdvisory": advisory,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


def _acts_from_path(snapshot: dict) -> tuple[bool, str, list[dict], list[dict]]:
    """Project optional adventure-path state without inventing progress when it is absent."""
    path = snapshot.get("adventure_path")
    if not isinstance(path, dict):
        return False, "", [], []
    current = _text(path.get("current_act_id") or path.get("currentActId"))
    raw_acts = path.get("acts")
    diagnostics_raw = path.get("diagnostics")
    acts: list[dict] = []
    if isinstance(raw_acts, list):
        for index, act in enumerate(raw_acts, start=1):
            if not isinstance(act, dict):
                continue
            act_id = _text(act.get("id"), f"act-{index}")
            beats: list[dict] = []
            raw_beats = act.get("beats")
            if isinstance(raw_beats, list):
                for beat_index, beat in enumerate(raw_beats, start=1):
                    if not isinstance(beat, dict):
                        continue
                    beats.append({
                        "id": _text(beat.get("id"), f"{act_id}:beat-{beat_index}"),
                        "title": _text(beat.get("title"), "Untitled beat"),
                        "status": _text(beat.get("status"), "planned"),
                    })
            status = _text(act.get("status"), "planned")
            acts.append({
                "id": act_id,
                "title": _text(act.get("title") or act.get("name"), f"Act {index}"),
                "status": status,
                "current": act_id == current or status in {"active", "current"},
                "summary": _text(act.get("summary") or act.get("synopsis")),
                "beats": beats,
            })
    diagnostics = [
        {"message": _text(item)}
        for item in (diagnostics_raw if isinstance(diagnostics_raw, list) else [])
        if _text(item)
    ]
    tracked = bool(acts or current or diagnostics)
    if tracked and not current:
        active = next((a for a in acts if a.get("current")), None)
        current = _text(active.get("id")) if isinstance(active, dict) else ""
    return tracked, current, acts, diagnostics


def _acts_major_choices(snapshot: dict) -> list[dict]:
    decisions = snapshot.get("decisions")
    out: list[dict] = []
    if not isinstance(decisions, list):
        return out
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        day = _num(decision.get("day"))
        out.append({
            "id": _text(decision.get("id"), f"decision-{len(out) + 1}"),
            "day": int(day) if day is not None else None,
            "summary": _text(decision.get("summary"), "A choice was recorded."),
            "chosen": _text(decision.get("chosen")),
            "context": _text(decision.get("rationale")),
        })
    out.sort(key=lambda row: (row.get("day") if row.get("day") is not None else -1, row.get("id") or ""), reverse=True)
    return out[:12]


def build_acts_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Read-only chronicle/payoff surface for the OpenWorlds Acts screen.

    If no adventure-path state exists yet, the surface says so explicitly instead of
    pretending prototype acts are real campaign progress.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    tracked, current, acts, diagnostics = _acts_from_path(snapshot)
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "world": _text(snapshot.get("world_id"), "unknown"),
        "dayLabel": _openworlds_day_label(snapshot),
        "tracked": tracked,
        "currentActId": current,
        "acts": acts,
        "majorChoices": _acts_major_choices(snapshot),
        "threads": _journal_evolutions(snapshot),
        "directorAdvisory": _director_advisory(snapshot),
        "diagnostics": diagnostics,
        "emptyState": {
            "title": "Acts not tracked yet",
            "body": "The campaign director has not compiled act progress for this save yet.",
        },
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": False,
        "state_authority": "engine",
        "write_lane": "/move",
    }


# ── Character sheets surface (full party read model) ──────────────────────────

def _ability_mod(score: object) -> int:
    s = _num(score)
    return ((int(s) - 10) // 2) if s is not None else 0


def _norm_skills(v: object) -> list:
    """Normalize a snapshot skill list to the lowercase-underscore keys the projection compares
    against (mirrors models.Character._normalize_skill_case). Stale snapshots seated before that
    engine normalizer can carry CAPITALIZED canon names (e.g. ['Arcana','History']) — without this
    the case mismatch renders '0 proficient' and drops the proficiency bonus (QA 2026-06-03)."""
    if not isinstance(v, list):
        return []
    return [str(s).strip().lower().replace(" ", "_") for s in v if str(s).strip()]


def _skill_bonus_from_sheet(ch: dict, skill: str) -> int:
    """Mirror Character.skill_bonus off raw snapshot fields: ability modifier of the
    skill's governing ability + proficiency (doubled on expertise)."""
    ability = _SKILL_ABILITIES.get(skill)
    if ability is None:
        return 0
    abilities = ch.get("abilities") if isinstance(ch.get("abilities"), dict) else {}
    bonus = _ability_mod(abilities.get(ability))
    prof = _num(ch.get("proficiency_bonus"))
    prof = int(prof) if prof is not None else 2
    expertise = _norm_skills(ch.get("skill_expertise"))
    proficiencies = _norm_skills(ch.get("skill_proficiencies"))
    if skill in expertise:
        bonus += 2 * prof
    elif skill in proficiencies:
        bonus += prof
    return bonus


# Skill -> governing ability key (SRD 5.2), mirroring models.SKILL_ABILITIES.
_SKILL_ABILITIES = {
    "acrobatics": "dexterity", "animal_handling": "wisdom", "arcana": "intelligence",
    "athletics": "strength", "deception": "charisma", "history": "intelligence",
    "insight": "wisdom", "intimidation": "charisma", "investigation": "intelligence",
    "medicine": "wisdom", "nature": "intelligence", "perception": "wisdom",
    "performance": "charisma", "persuasion": "charisma", "religion": "intelligence",
    "sleight_of_hand": "dexterity", "stealth": "dexterity", "survival": "wisdom",
}

_ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def _equipped_items(ch: dict) -> list[dict]:
    """Projected list of a character's EQUIPPED gear for the heroes-screen paper-doll +
    the inventory equip slots. Each entry carries name/slot/glyph plus the item's REAL SRD
    combat stats (kind / damage / damageType / ac / rarity / attunement) when the catalog
    resolves the name — so the doll's slot tooltip can read "Main Hand · 1d8 slashing"
    honestly, and stays just the name when the catalog has no record. The engine carries no
    canonical slot on the Item model, so `slot` is whatever the snapshot recorded (else "Worn");
    the screen's name->slot inference places it in the doll cell (forward-compatible if the
    engine ever emits a real slot id)."""
    out: list[dict] = []
    for it in (ch.get("inventory") or []):
        if not isinstance(it, dict) or not bool(it.get("equipped")):
            continue
        name = _text(it.get("name"))
        if not name:
            continue
        meta = _catalog_meta(name)
        damage = _text(meta.get("damage")) if meta else ""
        ac_raw = meta.get("ac") if meta else None
        out.append({
            "slot": _text(it.get("slot"), "Worn"),
            "name": name,
            "glyph": name.lower(),
            "kind": _text(meta.get("kind")) if meta else "",
            "damage": damage,
            "damageType": _text(meta.get("damage_type")) if (meta and damage) else "",
            "ac": int(ac_raw) if isinstance(ac_raw, (int, float)) else None,
            "rarity": (_text(it.get("rarity")) or (_text(meta.get("rarity")) if meta else "")) or "",
            "attunement": bool(it.get("requires_attunement") or (meta.get("requires_attunement") if meta else False)),
        })
    return out


_CLASS_FEATURE_DESCS: "dict | None" = None


def _class_feature_desc_map() -> dict:
    """(class, name) -> SRD description for class/subclass features (data/srd/class_features.json).

    Lets the character read-model project feature DESCRIPTIONS instead of blank detail — the
    engine already authored these canon SRD entries (260 of them), the read-model was just
    dropping them, so the Abilities/Feats tabs rendered bare name-lists.

    CLASS-AWARE keying (the fix): a feature NAME like "Spellcasting" is SHARED across 7 caster
    classes with 7 DISTINCT descriptions ("Cast wizard spells using Intelligence…" vs "Cast bard
    spells using Charisma…"), and so are "Expertise"/"Fighting Style"/"Channel Divinity"/
    "Unarmored Defense"/"Subclass Feature". Keying by name alone collapsed all of them onto
    whichever class loaded first (bard, alphabetically), so EVERY caster's Spellcasting read out
    the bard's text — wrong class AND wrong ability. We key by ``(class_lower, name)`` so the
    description resolves against the character's ACTUAL class.

    The cache holds two sub-maps:
      - ``"by_class_feature"``: {(class_lower, name): desc} — the authoritative class-aware lookup.
      - ``"shared_names"``: the set of feature names that appear with >1 distinct desc across
        classes — i.e. names we must NEVER resolve without a class (the flat fallback skips them so
        a name-only lookup can't mislabel a shared feature).

    Built once and cached. Honest: a feature with no authored desc stays blank (never fabricated)."""
    global _CLASS_FEATURE_DESCS
    if _CLASS_FEATURE_DESCS is not None:
        return _CLASS_FEATURE_DESCS
    by_class_feature: dict = {}
    name_descs: dict = {}  # name -> set of distinct descs (to detect shared/ambiguous names)
    try:
        raw = json.loads((_REPO_ROOT / "data" / "srd" / "class_features.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for cls, by_level in raw.items():
                if not isinstance(by_level, dict):
                    continue
                cls_key = str(cls or "").strip().lower()
                for feats in by_level.values():
                    for f in (feats or []):
                        if isinstance(f, dict):
                            nm = str(f.get("name") or "").strip()
                            ds = str(f.get("desc") or "").strip()
                            if nm and ds:
                                by_class_feature.setdefault((cls_key, nm), ds)
                                name_descs.setdefault(nm, set()).add(ds)
    except Exception:
        by_class_feature, name_descs = {}, {}
    shared_names = {nm for nm, descs in name_descs.items() if len(descs) > 1}
    _CLASS_FEATURE_DESCS = {
        "by_class_feature": by_class_feature,
        "shared_names": shared_names,
        # Flat fallback for legacy/class-less resolution: only UNAMBIGUOUS names (one desc across
        # all classes) so we can never mislabel a shared feature when the class is unknown.
        "by_name": {nm: next(iter(descs)) for nm, descs in name_descs.items() if len(descs) == 1},
    }
    return _CLASS_FEATURE_DESCS


def _feature_desc(class_names: "list[str]", feature_name: str) -> str:
    """Resolve a class feature's SRD description for a character with class(es) ``class_names``.

    Tries the CLASS-AWARE map ((class, name) -> desc) for each of the character's classes first —
    so a Wizard's "Spellcasting" gets the wizard/Intelligence text and a Bard's gets the bard/
    Charisma text. Falls back to the unambiguous flat map only for a non-shared name (a feature
    whose description is identical across every class that has it, e.g. a uniquely-named class
    feature). A name that's shared with conflicting descriptions but doesn't match the character's
    class returns "" rather than guessing the wrong class — honest over fabricated."""
    maps = _class_feature_desc_map()
    by_cf = maps["by_class_feature"]
    for cls in class_names:
        ck = str(cls or "").strip().lower()
        if ck:
            ds = by_cf.get((ck, feature_name))
            if ds:
                return ds
    # No class matched. Only fall back for an unambiguous (non-shared) name.
    if feature_name in maps["shared_names"]:
        return ""
    return maps["by_name"].get(feature_name, "")


def _character_sheet(cid: str, ch: dict) -> dict:
    """One party character's full sheet for the heroes screen, mapping 5e snapshot fields
    into the shape screen-character.jsx renders (stats block, skills, spells, class
    resources, conditions, death saves). All-5e: six per-ability saving throws (mod +
    proficiency), AC, proficiency bonus, melee/ranged attack bonus, initiative, speed."""
    klass, level = _class_summary(ch)
    abilities = ch.get("abilities") if isinstance(ch.get("abilities"), dict) else {}
    stats = {k[:3]: (_num(abilities.get(k)) if _num(abilities.get(k)) is not None else 10) for k in _ABILITY_KEYS}
    ac = _num(ch.get("armor_class"))
    ac = int(ac) if ac is not None else 10
    dex_mod = _ability_mod(abilities.get("dexterity"))
    prof = _num(ch.get("proficiency_bonus"))
    prof = int(prof) if prof is not None else 2
    init_bonus = _num(ch.get("initiative_bonus"))
    speed = _num(ch.get("speed"))
    cur_hp = _num(ch.get("current_hp"))
    max_hp = _num(ch.get("max_hp"))
    # Saving throws: ability mod (+ proficiency for proficient abilities). The engine stores
    # saving_throw_proficiencies as the SRD short ability codes (str/dex/con/int/wis/cha),
    # so match on the 3-letter prefix of the full name as well as the full name itself.
    save_profs = {str(s).strip().lower() for s in (ch.get("saving_throw_proficiencies") or [])}
    def _save(ability: str) -> int:
        b = _ability_mod(abilities.get(ability))
        proficient = ability in save_profs or ability[:3] in save_profs
        return b + prof if proficient else b
    stats.update({
        "ac": ac,
        # 5e saving throws: one per ability (mod + proficiency where proficient). No
        # touch/flat-footed AC or CMB/CMD — those are Pathfinder/3.5, not 5e.
        "saves": {
            "str": _save("strength"),
            "dex": _save("dexterity"),
            "con": _save("constitution"),
            "int": _save("intelligence"),
            "wis": _save("wisdom"),
            "cha": _save("charisma"),
        },
        "proficiency_bonus": prof,
        "melee": prof + _ability_mod(abilities.get("strength")),
        "ranged": prof + dex_mod,
        "initiative": int(init_bonus) if init_bonus is not None else dex_mod,
        "speed": int(speed) if speed is not None else 30,
        # #depth: surface Hit Dice (short-rest attrition economy) + Passive Perception — both
        # derivable from data already on the character model (hit_dice/hit_dice_remaining;
        # perception skill bonus) but never previously emitted. Rendered as StatLines.
        "hitDice": str(ch.get("hit_dice") or ""),
        "hitDiceRemaining": int(_num(ch.get("hit_dice_remaining")) or 0),
        "passivePerception": 10 + _skill_bonus_from_sheet(ch, "perception"),
    })

    # Skills: project the SRD skill list with sheet-correct bonuses (proficient first).
    # Normalize case so capitalized canon names (['Arcana',...]) on a stale snapshot still match
    # the lowercase SKILL_ABILITIES keys — else the Skills tab shows "0 proficient" (QA 2026-06-03).
    proficiencies = _norm_skills(ch.get("skill_proficiencies"))
    expertise = _norm_skills(ch.get("skill_expertise"))
    skill_ids = list(dict.fromkeys([*proficiencies, *expertise, *_SKILL_ABILITIES.keys()]))
    skills = [
        {"name": sk.replace("_", " ").title(), "mod": _skill_bonus_from_sheet(ch, sk),
         "proficient": sk in proficiencies or sk in expertise, "expertise": sk in expertise}
        for sk in skill_ids if sk in _SKILL_ABILITIES
    ]

    # Spells: the snapshot stores spell NAMES only, but the engine bundles the full srd524
    # spell dump — so for each known/prepared spell we look up its REAL rules block (level,
    # school, range, casting time, duration, concentration, save ability, damage, components,
    # upcast, desc) via `_spell_card`. A spell the SRD doesn't carry degrades to just its name
    # (today's behavior). The per-spell save DC is the caster's computed DC (None for a non-
    # caster class), surfaced only for save-forcing spells. We group under "Prepared"/"Known"
    # so the distinction stays honest; a caster with none shows an honest empty list.
    spells_known = [s for s in (ch.get("spells_known") or []) if isinstance(s, str)]
    prepared_list = [s for s in (ch.get("spells_prepared") or []) if isinstance(s, str)]
    spells_prepared = set(prepared_list)
    save_dc = _spell_save_dc(ch)
    spells = []
    if prepared_list:
        spells.append({
            "level": "Prepared",
            "list": [_spell_card(s, "prepared", save_dc) for s in prepared_list],
        })
    # Known-but-not-prepared spells get their own group so the distinction stays honest.
    known_only = [s for s in spells_known if s not in spells_prepared]
    if known_only:
        spells.append({
            "level": "Known",
            "list": [_spell_card(s, "known", save_dc) for s in known_only],
        })

    # Spell slots: the engine owns ch.spell_slots {level: {maximum, used}}. Surface the
    # per-level slot track (total + remaining) so a caster's slots show even when no spell
    # NAMES are stored. Sorted ascending by level. Empty for non-casters.
    spell_slots_raw = ch.get("spell_slots")
    spell_slots = []
    if isinstance(spell_slots_raw, dict):
        def _slot_lvl(k):
            try:
                return int(k)
            except (TypeError, ValueError):
                return 99
        for lvl in sorted(spell_slots_raw.keys(), key=_slot_lvl):
            pool = spell_slots_raw.get(lvl)
            if not isinstance(pool, dict):
                continue
            mx = _num(pool.get("maximum"))
            used = _num(pool.get("used"))
            mx = int(mx) if mx is not None else 0
            used = int(used) if used is not None else 0
            if mx <= 0:
                continue
            spell_slots.append({
                "level": _slot_lvl(lvl),
                "max": mx,
                "used": used,
                "remaining": max(0, mx - used),
            })

    # Class resources (Rage/Ki/etc.) — depletable pools the sheet can show as features.
    class_resources = []
    cr = ch.get("class_resources")
    if isinstance(cr, dict):
        for rid, pool in cr.items():
            if not isinstance(pool, dict):
                continue
            mx = _num(pool.get("max"))
            used = _num(pool.get("used"))
            class_resources.append({
                "id": _text(rid),
                "name": _text(rid).replace("_", " ").title(),
                "max": int(mx) if mx is not None else 0,
                "used": int(used) if used is not None else 0,
                "remaining": (int(mx) - int(used)) if (mx is not None and used is not None) else None,
                "recharge": _text(pool.get("recharge"), "long"),
            })

    conditions = [str(c).replace("_", " ").title() for c in (ch.get("conditions") or []) if str(c)]
    death = ch.get("death_saves") if isinstance(ch.get("death_saves"), dict) else {}
    # The engine's `features` list holds CLASS/SUBCLASS features only (models.Character.features
    # is populated from srd_tables.features_through/features_at). Chosen FEATS are recorded
    # separately as "| feat: <Name>" markers in ch.notes (engine level_up path). Surface those
    # two distinct sources so the sheet's Feats tab and Class Features list are not identical
    # duplicates (#286): classFeatures <- features, feats <- notes feat-markers (empty when none).
    features = [_text(f) for f in (ch.get("features") or []) if _text(f)]
    # Class-aware feature descriptions: resolve each feature's SRD desc against the character's
    # ACTUAL class(es) so a shared name like "Spellcasting" carries the right class+ability text
    # (Wizard -> Intelligence, Bard -> Charisma), not whichever class loaded first. _class_feature_names
    # comes from the engine-set `classes` list (and falls back to the single `class`/`klass` field).
    _class_feature_names: list[str] = []
    _classes_list = ch.get("classes") if isinstance(ch.get("classes"), list) else []
    for _cl in _classes_list:
        if isinstance(_cl, dict):
            _cn = _text(_cl.get("name") or _cl.get("class_name"))
            if _cn:
                _class_feature_names.append(_cn)
    if not _class_feature_names:
        _fallback_cls = _text(ch.get("class") or ch.get("klass"))
        if _fallback_cls:
            _class_feature_names.append(_fallback_cls)
    feat_names: list[str] = []
    for seg in _text(ch.get("notes")).split("|"):
        seg = seg.strip()
        if seg.lower().startswith("feat:"):
            fname = seg.split(":", 1)[1].strip()
            if fname and fname not in feat_names:
                feat_names.append(fname)
    equipped = _equipped_items(ch)

    return {
        "id": cid,
        "name": _text(ch.get("name"), cid),
        "short": "portrait",
        "race": _text(ch.get("race")),
        "class": klass,
        "archetype": _text((ch.get("classes") or [{}])[0].get("subclass") if isinstance(ch.get("classes"), list) and ch.get("classes") else "") or _text(ch.get("background")),
        # #397 (read-model increment 1): flag a PENDING subclass choice — at/above the class's
        # subclass-selection level (3; warlock 6) with no subclass set — so the character screen can
        # offer the build-choice PICKER. Detect, do NOT auto-fill (#624's auto-fill pre-empts the
        # choice the optimizer wants; CI's build_options test confirmed the subclass must stay choosable).
        "pendingSubclass": bool((level or 1) >= (6 if klass.lower() == "warlock" else 3)
                                and not str((ch.get("classes") or [{}])[0].get("subclass") or "").strip()),
        "alignment": _text(ch.get("alignment"), "Unaligned"),
        "level": level or 1,
        "xp": int(_num(ch.get("xp")) or 0),
        "xpMax": _xp_for_next_level(level or 1),
        "hp": int(cur_hp) if cur_hp is not None else 1,
        "hpMax": int(max_hp) if max_hp is not None else 1,
        "tempHp": int(_num(ch.get("temp_hp")) or 0),
        # Live coin purse (cp/sp/ep/gp/pp) so the Market reads the SAME currency the Stash
        # does (engine = sole writer) — fixes the Market-vs-Stash coin contradiction where
        # the merchant showed a hardcoded 232gp. Same helper the inventory-surface uses.
        "currency": _currency_for(ch),
        "stats": stats,
        "skills": skills,
        "spells": spells,
        # Character-level casting summary (Spell Save DC + Spell Attack Bonus) for the top
        # of the Spells tab. None for a non-caster (Fighter/Rogue) — the screen omits it
        # rather than show a fake DC. Derived from the PC's casting ability + proficiency.
        "spellcasting": _character_spellcasting(ch),
        "spellSlots": spell_slots,
        "classResources": class_resources,
        "conditions": conditions,
        "exhaustion": int(_num(ch.get("exhaustion")) or 0),
        "concentration": _text(ch.get("concentration")),
        "deathSaves": {
            "successes": int(_num(death.get("successes")) or 0),
            "failures": int(_num(death.get("failures")) or 0),
        },
        "dead": bool(ch.get("dead")),
        "stable": bool(ch.get("stable")),
        "equipped": equipped,
        "feats": [{"name": f, "glyph": "feat", "detail": ""} for f in feat_names],
        "abilities": [],
        "proficiencies": features,
        "classFeatures": [{"name": f, "detail": _feature_desc(_class_feature_names, f)} for f in features],
        "traits": [],
        "dr": {"value": ", ".join(_text(x) for x in (ch.get("damage_resistances") or []) if _text(x)) or "None",
               "energy": ", ".join(_text(x) for x in (ch.get("damage_immunities") or []) if _text(x)) or "None"},
        # Lineage: race is the engine's authoritative lineage field; subrace/racial traits
        # are surfaced when the snapshot carries them (the engine model has none today, so
        # they degrade to empty honestly). `lineageNote` keeps any backstory/personality
        # flavor the character set; the screen falls back to an honest empty-state when
        # neither race nor flavor exists.
        "subrace": _text(ch.get("subrace")),
        "raceTraits": [_text(t) for t in (ch.get("racial_traits") or ch.get("race_traits") or []) if _text(t)],
        "lineageNote": _text(ch.get("backstory")) or _text(ch.get("personality")),
        "lineage": _text(ch.get("backstory")) or _text(ch.get("personality")) or "No lineage recorded.",
        # Loop-10 #383: player-authored identity from the Creation wizard. PR
        # #369 wired both into the bindHero spec; this projection is the read-
        # model the Character screen renders from. Empty strings are honest —
        # an authored hero with blank Family/House + Biography renders the
        # screen with no House line and no Biography paragraph (today's UX for
        # any unset narrative field, e.g. concentration, backstory).
        "house": _text(ch.get("house")),
        "biography": _text(ch.get("biography")),
    }


# SRD 5e XP thresholds per level (index 0 unused); used to fill the heroes screen XP bar.
_XP_THRESHOLDS = [0, 0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000, 85000,
                  100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000]


def _xp_for_next_level(level: int) -> int:
    if level < 1:
        return 300
    if level >= len(_XP_THRESHOLDS) - 1:
        return _XP_THRESHOLDS[-1]
    return _XP_THRESHOLDS[level + 1]


def _character_party(snapshot: dict) -> list[dict]:
    chars = snapshot.get("characters")
    party = snapshot.get("party")
    if not isinstance(chars, dict) or not isinstance(party, list):
        return []
    out: list[dict] = []
    for cid in party:
        if not isinstance(cid, str):
            continue
        ch = chars.get(cid)
        if isinstance(ch, dict):
            out.append(_character_sheet(cid, ch))
    return out


def build_character_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project the full party's character sheets from engine-owned state for the heroes
    screen. Each hero carries classes/skills/spells/class_resources/conditions/AC/death
    saves projected from the 5e snapshot into the screen's render shape."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "dayLabel": _openworlds_day_label(snapshot),
        "party": _character_party(snapshot),
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


# ── Inventory surface (per-character packs + currency) ────────────────────────

_ITEM_TYPE_HINTS = (
    (("longsword", "sword", "axe", "bow", "dagger", "mace", "spear", "rapier", "blade", "hammer", "staff", "club", "crossbow"), "weapon"),
    (("armor", "shield", "mail", "plate", "helm", "cloak", "leather", "buckler"), "armor"),
    (("potion", "scroll", "wand", "elixir", "draught", "vial", "oil", "reagent", "charm", "candle"), "spell"),
)


def _infer_item_type(name: str, item: dict) -> str:
    explicit = _text(item.get("type"))
    if explicit:
        return explicit
    low = name.lower()
    for needles, kind in _ITEM_TYPE_HINTS:
        if any(n in low for n in needles):
            return kind
    if item.get("requires_attunement") or item.get("attuned"):
        return "rare"
    return "common"


_ITEMCATALOG = None
_ITEMCATALOG_TRIED = False
_SPELLS_MOD = None
_SPELLS_TRIED = False


def _engine_module(modname: str):
    """Lazily import a pure engine helper module (itemcatalog / spells) so the viewer can
    enrich the read-model with the engine's REAL bundled SRD data. Returns the module or
    None; on None the surface degrades to today's behavior (the viewer stays a pure reader,
    never fabricating). Adds servers/engine to sys.path once (same pattern as _catalog_meta)."""
    try:
        engine_dir = (_HERE.parent / "servers" / "engine").resolve()
        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))
        return __import__(modname)
    except Exception:
        return None


def _spell_meta(name: str) -> dict:
    """Lazily resolve a spell's structured SRD record (level / school / range / duration /
    concentration / ritual / save ability / damage / components / upcast / desc) from the
    engine's bundled srd524 dump via ``spells.srd_spell``. Returns {} when the spell isn't
    in the SRD (an honest miss -> the screen shows just the name, today's behavior). The
    viewer never invents spell rules text — it only surfaces what the engine already owns."""
    global _SPELLS_MOD, _SPELLS_TRIED
    if not name:
        return {}
    if _SPELLS_MOD is None and not _SPELLS_TRIED:
        _SPELLS_TRIED = True
        _SPELLS_MOD = _engine_module("spells")
    if _SPELLS_MOD is None:
        return {}
    try:
        return _SPELLS_MOD.srd_spell(name) or {}
    except Exception:
        return {}


# Casting ability per SRD class (mirror of srd_tables._CASTING_ABILITY). A character's spell
# save DC / attack bonus key off their FIRST caster class's ability mod (engine _casting_mod).
# Non-caster classes (Fighter/Rogue/etc.) are absent -> no DC is computed (we never fabricate
# one; the per-spell `save` still names which save the TARGET rolls, which is class-independent).
_CASTING_ABILITY = {
    "bard": "charisma", "cleric": "wisdom", "druid": "wisdom", "paladin": "charisma",
    "ranger": "wisdom", "sorcerer": "charisma", "warlock": "charisma", "wizard": "intelligence",
}


def _casting_ability(ch: dict) -> str | None:
    """The full ability key (e.g. "intelligence") the character casts with, from their
    FIRST SRD caster class (mirror of engine srd_tables._CASTING_ABILITY). Returns None
    for a non-caster (Fighter/Rogue/NPC with stray spell names) so callers omit DC/attack
    rather than fabricate one. Honest: reads only the engine-set `classes` list."""
    classes = ch.get("classes") if isinstance(ch.get("classes"), list) else []
    for cl in classes:
        if isinstance(cl, dict):
            a = _CASTING_ABILITY.get(_text(cl.get("name")).lower())
            if a:
                return a
    return None


def _spell_save_dc(ch: dict) -> int | None:
    """A caster's spell save DC = 8 + proficiency + casting-ability modifier, mirroring
    engine ``server.spell_save_dc`` read-only from the snapshot. Returns None when the
    character has no SRD caster class (a Fighter with stray spell names, an NPC) — we then
    omit the DC rather than invent one. Honest: reads only engine-set abilities/prof."""
    ability = _casting_ability(ch)
    if ability is None:
        return None
    abilities = ch.get("abilities") if isinstance(ch.get("abilities"), dict) else {}
    prof = _num(ch.get("proficiency_bonus"))
    prof = int(prof) if prof is not None else 2
    return 8 + prof + _ability_mod(abilities.get(ability))


def _spell_attack_bonus(ch: dict) -> int | None:
    """A caster's spell attack bonus = proficiency + casting-ability modifier, mirroring
    engine ``server.spell_save_dc``'s `spell_attack_bonus` (server.py: prof + mod). Returns
    None for a non-caster (no SRD caster class) so the UI omits it rather than show a fake
    +0. Honest: reads only engine-set abilities/prof."""
    ability = _casting_ability(ch)
    if ability is None:
        return None
    abilities = ch.get("abilities") if isinstance(ch.get("abilities"), dict) else {}
    prof = _num(ch.get("proficiency_bonus"))
    prof = int(prof) if prof is not None else 2
    return prof + _ability_mod(abilities.get(ability))


def _character_spellcasting(ch: dict) -> dict | None:
    """Character-level spellcasting summary for the TOP of the Spells tab — the once-at-the-top
    Spell Save DC + Spell Attack Bonus a caster needs to plan (the way D&D Beyond shows them),
    derived from the PC's spellcasting ability + proficiency. Returns None for a non-caster
    (no SRD caster class) so the screen omits the block entirely — an honest Fighter shows
    nothing, never a fabricated DC 0. Reuses the same #410 formula helpers (no new math)."""
    ability = _casting_ability(ch)
    if ability is None:
        return None
    return {
        "ability": ability,
        # short SRD code (int/wis/cha) for a compact "INT" badge in the UI
        "abilityShort": ability[:3],
        "spellSaveDc": _spell_save_dc(ch),
        "spellAttackBonus": _spell_attack_bonus(ch),
    }


def _spell_card(name: str, time_label: str, save_dc: int | None) -> dict:
    """One spell's render card for the heroes screen: the name plus the engine's REAL SRD
    rules fields (level / school / range / casting time / duration / components / save / damage)
    when the spell resolves in srd524, and just the name (today's behavior) when it doesn't.

    `time_label` is the engine-derived prepared/known grouping. `save_dc` is the caster's
    computed DC (None for non-casters) — surfaced ONLY for spells that force a save, so the
    inspector can show "DC 15 DEX" honestly. Never fabricates a field the SRD record lacks."""
    meta = _spell_meta(name)
    card = {"name": name, "school": "—", "time": time_label, "glyph": "spell"}
    if not meta:
        return card
    school = _text(meta.get("school"))
    level_raw = meta.get("level")
    level = int(level_raw) if isinstance(level_raw, (int, float)) else None
    rng = _text(meta.get("range_text"))
    casting_time = _text(meta.get("casting_time"))
    duration = _text(meta.get("duration"))
    concentration = bool(meta.get("concentration"))
    ritual = bool(meta.get("ritual"))
    save_ability = _text(meta.get("saving_throw_ability"))
    attack_roll = bool(meta.get("attack_roll"))
    damage = _text(meta.get("damage_roll"))
    dtypes = [_text(t) for t in (meta.get("damage_types") or []) if _text(t)]
    # Components V/S/M -> a compact "V, S, M" string (only the ones present).
    comp = [c for c, on in (("V", meta.get("verbal")), ("S", meta.get("somatic")), ("M", meta.get("material"))) if on]
    card.update({
        "school": school.title() if school else "—",
        "level": level,
        # "Cantrip" reads better than "Level 0" for a 0-level spell.
        "levelLabel": "Cantrip" if level == 0 else (f"Level {level}" if level is not None else ""),
        "range": rng,
        "castingTime": casting_time,
        "duration": duration,
        "concentration": concentration,
        "ritual": ritual,
        "components": ", ".join(comp),
        "material": _text(meta.get("material_specified")),
        # save: which ability the TARGET rolls (class-independent SRD fact). saveDc: the
        # caster's DC (omitted when the character isn't a caster, or the spell forces no save).
        "save": save_ability,
        "saveDc": save_dc if (save_ability and save_dc is not None) else None,
        "attack": attack_roll,
        "damage": damage,
        "damageType": ", ".join(dtypes),
        "higherLevel": _text(meta.get("higher_level")),
        "desc": _text(meta.get("desc")),
    })
    return card


def _catalog_meta(name: str) -> dict:
    """Lazily resolve an item's SRD catalog metadata (weight/cost/rarity/type) so the
    inventory detail isn't all dashes for plain gear the DM granted by free text.
    Degrades to {} if itemcatalog is unavailable (viewer stays a pure reader)."""
    global _ITEMCATALOG, _ITEMCATALOG_TRIED
    if not name:
        return {}
    if _ITEMCATALOG is None and not _ITEMCATALOG_TRIED:
        _ITEMCATALOG_TRIED = True
        _ITEMCATALOG = _engine_module("itemcatalog")
    if _ITEMCATALOG is None:
        return {}
    try:
        return _ITEMCATALOG.resolve(name) or {}
    except Exception:
        return {}


# Weapon-property slugs the SRD catalog stamps on a Weapon record (`is_simple` ->
# "simple", `is_improvised` -> "improvised") and the attunement clause it stamps on a
# MagicItem ("attune:<detail>"). Rendered verbatim as pills; the leading "attune:" is
# stripped at display so the chip reads "Requires attunement (...)" cleanly.
def _catalog_property_chips(meta: dict) -> list[str]:
    """Human-readable property chips for an item from its SRD catalog record. Surfaces
    ONLY the real, item-specific data the catalog carries (weapon simple/improvised flags,
    the attunement clause). Never invents a property the catalog didn't stamp."""
    chips: list[str] = []
    for p in (meta.get("properties") or []):
        s = _text(p)
        if not s:
            continue
        if s.startswith("attune:"):
            detail = s.split(":", 1)[1].strip()
            chips.append(f"Attunement: {detail}" if detail else "Requires attunement")
        else:
            chips.append(s.replace("-", " "))
    return chips


def _inventory_items(cid: str, ch: dict) -> list[dict]:
    inventory = ch.get("inventory")
    out: list[dict] = []
    if not isinstance(inventory, list):
        return out
    for idx, item in enumerate(inventory):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        qty = _num(item.get("quantity"))
        weight = _num(item.get("weight"))
        cost = _num(item.get("cost"))
        rarity = _text(item.get("rarity"))
        # Resolve the SRD catalog record ONCE. It is the source of the item's stat block
        # (damage dice / damage type / base AC / weapon properties / attunement clause) and
        # also backfills weight/value/rarity the granted item omits. The engine stays the
        # source of truth — a granted item's own value always wins; the catalog only fills
        # gaps and supplies combat stats the Item model has no field for. A name the catalog
        # can't resolve (e.g. "Longsword +1", "Healing Potion") yields {} -> the stat fields
        # stay empty exactly as today (HONEST: never fabricate a number the engine lacks).
        meta = _catalog_meta(name)
        if meta:
            if weight is None or weight <= 0:
                weight = _num(meta.get("weight"))
            if cost is None or cost <= 0:
                cost = _num(meta.get("cost"))
            if not rarity:
                rarity = _text(meta.get("rarity"))
        # Damage / AC: real catalog stats. Damage type is only meaningful with a dice expr.
        damage = _text(meta.get("damage")) if meta else ""
        damage_type = _text(meta.get("damage_type")) if (meta and damage) else ""
        ac_raw = meta.get("ac") if meta else None
        ac = int(ac_raw) if isinstance(ac_raw, (int, float)) else None
        # Catalog `kind` ("weapon"/"armor"/"wondrous"/"potion"/"ring"/…) is the SRD's own
        # classification; carry it through as the item's category alongside the coarse `type`
        # the grid filters on (so the detail can read "Martial Weapon" / "Wondrous Item").
        kind = _text(meta.get("kind")) if meta else ""
        attunement = bool(item.get("requires_attunement") or (meta.get("requires_attunement") if meta else False))
        # Property chips: the granted item's recorded "attuned" state first, then the catalog's
        # real per-item properties (weapon simple/improvised, attunement clause). De-duped, no fab.
        properties = ["Attuned"] if item.get("attuned") else []
        for chip in (_catalog_property_chips(meta) if meta else []):
            if chip not in properties:
                properties.append(chip)
        out.append({
            "id": f"{cid}:{idx}:{name}",
            "owner": cid,
            "name": name,
            "qty": int(qty) if qty is not None else 1,
            "type": _infer_item_type(name, item),
            "kind": kind,
            "glyph": _text(item.get("glyph"), name.lower()),
            "equipped": bool(item.get("equipped")),
            "weight": f"{weight:g} lb" if weight is not None and weight > 0 else "—",
            "value": f"{cost:g} gp" if cost is not None and cost > 0 else "—",
            "rarity": rarity or "common",
            "desc": _text(item.get("description"), "No description recorded."),
            # Combat stat block (empty string / None when the catalog has no such datum —
            # the screen hides each row that is blank). damage e.g. "1d8", damageType "slashing",
            # ac e.g. 18 (base AC for armor / shields).
            "damage": damage,
            "damageType": damage_type,
            "ac": ac,
            "attunement": attunement,
            "attuned": bool(item.get("attuned")),
            "properties": properties,
        })
    return out


def _currency_for(ch: dict) -> dict:
    cur = ch.get("currency") if isinstance(ch.get("currency"), dict) else {}
    return {k: int(_num(cur.get(k)) or 0) for k in ("cp", "sp", "ep", "gp", "pp")}


def _inventory_party(snapshot: dict) -> list[dict]:
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
        items = _inventory_items(cid, ch)
        out.append({
            "id": cid,
            "name": _text(ch.get("name"), cid),
            "short": "portrait",
            "class": klass,
            "level": level or 1,
            "alignment": _text(ch.get("alignment"), "Unaligned"),
            "currency": _currency_for(ch),
            "equipped": _equipped_items(ch),
            "items": items,
        })
    return out


def build_inventory_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project each party character's inventory (name/qty/type/glyph/equipped) + currency
    from engine-owned state for the stash screen."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    party = _inventory_party(snapshot)
    # Flat shared-stash view (all party items) for the center grid, plus per-hero packs.
    stash: list[dict] = []
    for member in party:
        stash.extend(member.get("items", []))
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "dayLabel": _openworlds_day_label(snapshot),
        "party": party,
        "stash": stash,
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


# ── Relations surface (factions + companions + met NPCs) ──────────────────────

_FACTION_COLORS = ["#22305E", "#6E1D1D", "#7a6644", "#2f5a3a", "#7a3d6e", "#3a4a5a"]


def _reputation_to_bar(rep: int) -> int:
    """Map a -100..100 reputation onto the 0..100 bar the RepBar component renders."""
    return max(0, min(100, int(round((rep + 100) / 2))))


def _standing_label(rep: int) -> str:
    if rep <= -40:
        return "Hostile"
    if rep < 0:
        return "Wary"
    if rep < 30:
        return "Civil"
    if rep < 70:
        return "Cordial"
    return "Welcome"


# Leading articles/qualifiers stripped before deriving a faction sigil so
# "The Flaming Fist" reads as "FF" (not "T") and "Order of the Gauntlet" as "G".
_FACTION_SIGIL_PREFIXES = ("the ", "order of the ", "order of ", "cult of the ",
                           "cult of ", "house of ", "house ", "clan ", "guild of ",
                           "guild ", "company of the ", "company of ")


def _faction_sigil(name: str) -> str:
    """Derive a 1-2 letter sigil for a faction medallion. Strips a leading article/
    qualifier ("The ", "Order of ", "Cult of ", ...) then takes the initials of the
    first one or two remaining words, so "The Flaming Fist" -> "FF" and a single-word
    faction -> its first letter."""
    base = _text(name).strip()
    low = base.lower()
    for pre in _FACTION_SIGIL_PREFIXES:
        if low.startswith(pre) and len(low) > len(pre):
            base = base[len(pre):].strip()
            break
    words = [w for w in base.replace("-", " ").split() if w]
    if not words:
        return "✦"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[1][:1]).upper()


def _relations_factions(snapshot: dict) -> list[dict]:
    factions = snapshot.get("factions")
    out: list[dict] = []
    if not isinstance(factions, dict):
        return out
    for i, (fid, row) in enumerate(factions.items()):
        if not isinstance(row, dict):
            continue
        rep = _num(row.get("reputation"))
        rep = int(rep) if rep is not None else 0
        tags = [str(t) for t in row.get("tags", []) if str(t)] if isinstance(row.get("tags"), list) else []
        name = _text(row.get("name"), _text(fid, "Faction"))
        out.append({
            "id": _text(fid),
            "name": name,
            "short": _text(row.get("description"))[:48] or "a standing power",
            "kind": "Faction",
            "color": _FACTION_COLORS[i % len(_FACTION_COLORS)],
            "sigil": _faction_sigil(name),
            "motto": tags[0].title() if tags else "",
            "seat": "",
            "rep": _reputation_to_bar(rep),
            "reputation": rep,
            "tags": tags,
            "threshold": {"hostile": 25, "neutral": 50, "friendly": 75},
            "standing": _standing_label(rep),
            # Faction-growth membership (engine Faction.rank/joined/questline_arc_id) — the
            # Skyrim/PFK join->grow->lead loop the read-model was dropping. rank 0 == not a
            # ranked member; joined == the join_faction latch; questlineArcId names the arc.
            "rank": int(_num(row.get("rank")) or 0),
            "joined": bool(row.get("joined")),
            "questlineArcId": _text(row.get("questline_arc_id")),
            "lastContact": "",
            "body": _text(row.get("description"), "Little is recorded of this faction's dealings with the party."),
            "events": [],
            "offers": tags,
        })
    return out


def _attitude_disposition(ch: dict) -> str:
    """Map an NPC's attitude_value (-100..100) / free-text attitude onto the screen's
    disposition buckets (friend / ally / neutral / cool / enemy)."""
    val = _num(ch.get("attitude_value"))
    if val is not None and val != 0:
        if val >= 60:
            return "friend"
        if val >= 20:
            return "ally"
        if val <= -40:
            return "enemy"
        if val < 0:
            return "cool"
        return "neutral"
    attitude = _text(ch.get("attitude")).lower()
    if any(w in attitude for w in ("ally", "friend", "warm", "devoted", "loyal")):
        return "friend"
    if any(w in attitude for w in ("hostile", "enemy", "foe")):
        return "enemy"
    if any(w in attitude for w in ("guarded", "wary", "cold", "cool", "suspicious")):
        return "cool"
    return "neutral"


# Mirror of companion_arc.ATTITUDE_WARN_{LOW,HIGH} (the engine's danger band). A LIVE
# (unfired) `attitude_below` agenda whose companion sits in [-40, -20] AND below the
# agenda's breaking point is "approaching a fracture" — the engine emits an advisory
# `betrayal_warning` from `evaluate()`; here we recompute the SAME band read-only from the
# snapshot so the relations screen can telegraph it. Display-only: never mutates, never
# fires anything, reads only the approval gauge + the (engine-set) decision_flag presence.
_ATTITUDE_WARN_HIGH = -20  # upper edge: the bond has clearly soured
_ATTITUDE_WARN_LOW = -40   # lower edge: below this it's already deep-red / near-snap


def _betrayal_warning(ch: dict, snapshot: dict) -> dict | None:
    """Advisory "approaching a breaking point" telegraph for a companion, recomputed
    read-only from the snapshot (mirrors companion_arc._betrayal_warning).

    Returns a small advisory dict ONLY when the companion carries a LIVE (unfired)
    ``attitude_below`` agenda AND its ``attitude_value`` sits in the danger band
    [_ATTITUDE_WARN_LOW, _ATTITUDE_WARN_HIGH] AND has crossed below the agenda's
    breaking point (``value``). None otherwise. Reads the sealed agenda's TRIGGER/VALUE/
    FIRED only to decide *whether* a warning is live — it NEVER surfaces the agenda's
    private intent (`note`/`decision_flag` name), so no DM-only fiction leaks. The
    ``decision_active`` flag is a plain bool: True when the agenda names a content flag
    that is present+True in ``Campaign.flags`` (a recorded choice has already spiked the
    odds), so the screen can foreshadow harder."""
    arc = ch.get("arc")
    if not isinstance(arc, dict):
        return None
    agenda = arc.get("agenda")
    if not isinstance(agenda, dict):
        return None
    if agenda.get("trigger") != "attitude_below" or bool(agenda.get("fired")):
        return None
    threshold = _num(agenda.get("value"))
    av = _num(ch.get("attitude_value"))
    if threshold is None or av is None:
        return None
    threshold = int(threshold)
    av = int(av)
    # Only warn while in the band AND actually below the agenda's breaking point (an
    # agenda whose threshold is even lower isn't live yet).
    if not (_ATTITUDE_WARN_LOW <= av <= _ATTITUDE_WARN_HIGH):
        return None
    if av >= threshold:
        return None
    flags = snapshot.get("flags") if isinstance(snapshot.get("flags"), dict) else {}
    decision_flag = _text(agenda.get("decision_flag"))
    decision_active = bool(flags.get(decision_flag)) if decision_flag else False
    return {
        "attitude_value": av,
        "threshold": threshold,
        "band": [_ATTITUDE_WARN_LOW, _ATTITUDE_WARN_HIGH],
        "decision_active": decision_active,
        "label": "Bond fracturing",
        "note": (
            "This companion is approaching a breaking point — their bond has soured into "
            "the danger band."
            + (" A choice you made has deepened the rift." if decision_active else "")
        ),
    }


def _relations_npcs(snapshot: dict) -> list[dict]:
    """Project NPCs the party has actually met (kind=='npc') + companions, with attitude
    and (for companions) the dossier's banter/relationship facts + arc state."""
    chars = snapshot.get("characters")
    locs = snapshot.get("locations")
    party = snapshot.get("party") if isinstance(snapshot.get("party"), list) else []
    out: list[dict] = []
    if not isinstance(chars, dict):
        return out
    for cid, ch in chars.items():
        if not isinstance(ch, dict):
            continue
        kind = _text(ch.get("kind"))
        is_companion = kind == "companion" or cid in party and kind != "player"
        if kind == "player":
            continue
        if kind == "npc" and not bool(ch.get("met")) and _num(ch.get("attitude_value")) in (None, 0):
            # A roster stranger the party hasn't met — don't list. Gate ONLY on met / a real
            # attitude_value: the seeded post-BG3 canon roster (Jaheira, Astarion, the Emperor, …)
            # carries a DESCRIPTIVE attitude blurb (a role/bio, e.g. "High Harper, veteran of a
            # hundred years") in `attitude`, NOT a relationship stance — keying the filter off that
            # text made every un-met legend show as "known" on a fresh game (the met-everyone bug).
            continue
        if kind == "monster" and not is_companion:
            continue
        loc_id = _text(ch.get("location_id"))
        location = loc_id
        if isinstance(locs, dict) and loc_id:
            loc = locs.get(loc_id)
            if isinstance(loc, dict):
                location = _text(loc.get("name"), loc_id)
        dossier = ch.get("companion_dossier") if isinstance(ch.get("companion_dossier"), dict) else {}
        approval = _num(ch.get("attitude_value"))
        row = {
            "id": _text(cid),
            "name": _text(ch.get("name"), cid),
            "short": "portrait",
            "role": ("Companion" if is_companion else "NPC") + (f" · {_text(ch.get('attitude'))}" if _text(ch.get("attitude")) else ""),
            "kind": kind,
            "companion": bool(is_companion),
            "location": location or "Unknown",
            "faction": "",
            "disposition": _attitude_disposition(ch),
            "approval": int(approval) if approval is not None else None,
            # Advisory telegraph (#118): present (a dict) only for a companion whose live
            # attitude_below agenda sits in the danger band; None otherwise. Display-only.
            "betrayalWarning": _betrayal_warning(ch, snapshot) if is_companion else None,
            "attitude": _text(ch.get("attitude")),
            "body": _text(ch.get("backstory")) or _text(ch.get("personality")) or _text(ch.get("notes")) or "Little is known of them yet.",
            "banter_tags": [str(t) for t in dossier.get("banter_tags", []) if str(t)] if isinstance(dossier.get("banter_tags"), list) else [],
            "relationships": {str(k): str(v) for k, v in dossier.get("relationships", {}).items()} if isinstance(dossier.get("relationships"), dict) else {},
            "values": [str(v) for v in dossier.get("values", []) if str(v)] if isinstance(dossier.get("values"), list) else [],
            "dues": [{"text": str(m), "fulfilled": False} for m in (ch.get("memory") or [])[:4] if str(m)],
            "lastSpoken": (ch.get("memory") or [""])[-1] if isinstance(ch.get("memory"), list) and ch.get("memory") else "",
            "lastSpokenAt": location or "",
        }
        out.append(row)
    return out


def _relations_companion_arcs(snapshot: dict) -> list[dict]:
    """Project companion personal-quest arcs (campaign.companion_quest_arcs) — the
    character-owned arc lifecycle, with each stage's status."""
    arcs = snapshot.get("companion_quest_arcs")
    chars = snapshot.get("characters") if isinstance(snapshot.get("characters"), dict) else {}
    out: list[dict] = []
    if not isinstance(arcs, dict):
        return out
    for aid, arc in arcs.items():
        if not isinstance(arc, dict):
            continue
        comp_id = _text(arc.get("companion_id"))
        comp = chars.get(comp_id) if isinstance(chars.get(comp_id), dict) else {}
        stages = [
            {"title": _text(s.get("title")), "status": _text(s.get("status"), "locked"), "note": _text(s.get("note"))}
            for s in (arc.get("stages") or []) if isinstance(s, dict) and _text(s.get("title"))
        ]
        out.append({
            "id": _text(aid),
            "companion_id": comp_id,
            "companion": _text(comp.get("name"), comp_id),
            "title": _text(arc.get("title"), "Personal arc"),
            "status": _text(arc.get("status"), "locked"),
            "note": _text(arc.get("note")),
            "stages": stages,
        })
    return out


def _relations_camp_beats(snapshot: dict) -> dict:
    """Project camp-beat history/cooldowns without asking the viewer to schedule or
    record anything. `record_camp_beat` remains the only engine write lane."""
    state = snapshot.get("camp_beats") if isinstance(snapshot.get("camp_beats"), dict) else {}
    records = state.get("records") if isinstance(state.get("records"), list) else []
    chars = snapshot.get("characters") if isinstance(snapshot.get("characters"), dict) else {}
    day = int(_num(snapshot.get("day")) or 0)
    solo_days = int(_num(state.get("solo_cooldown_days")) or 2)
    pair_days = int(_num(state.get("pair_cooldown_days")) or 3)
    max_records = int(_num(state.get("max_records")) or 200)
    recent: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        companion_ids = [str(cid) for cid in (record.get("companion_ids") or []) if str(cid)]
        participants = []
        for cid in companion_ids:
            ch = chars.get(cid) if isinstance(chars.get(cid), dict) else {}
            participants.append({"id": cid, "name": _text(ch.get("name"), cid)})
        kind = _text(record.get("kind"), "solo")
        cooldown_days = pair_days if kind == "pair_banter" else solo_days
        record_day = int(_num(record.get("day")) or 0)
        ready_day = record_day + cooldown_days if record_day else 0
        recent.append({
            "id": _text(record.get("id")),
            "day": record_day,
            "kind": kind,
            "participants": participants,
            "tags": [str(t) for t in (record.get("tags") or []) if str(t)] if isinstance(record.get("tags"), list) else [],
            "resolved": bool(record.get("resolved")),
            "note": _text(record.get("note")),
            "cooldown": {
                "days": cooldown_days,
                "ready_day": ready_day,
                "remaining_days": max(0, ready_day - day) if day and ready_day else 0,
            },
        })
    recent.sort(key=lambda row: (row.get("day") or 0, row.get("id") or ""), reverse=True)
    return {
        "summary": {
            "records": len(records),
            "solo_cooldown_days": solo_days,
            "pair_cooldown_days": pair_days,
            "max_records": max_records,
        },
        "recent": recent[:8],
    }


def build_relations_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
) -> dict:
    """Project the relations web from engine-owned state: factions (name/reputation/tags),
    met NPCs + companions (attitude, dossier banter/relationships), and companion arcs."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "dayLabel": _openworlds_day_label(snapshot),
        "factions": _relations_factions(snapshot),
        "npcs": _relations_npcs(snapshot),
        "companionArcs": _relations_companion_arcs(snapshot),
        "campBeats": _relations_camp_beats(snapshot),
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }


# ── Parley surface (sheet-correct social options for the lead PC) — UI of #141 ─

_PARLEY_CORE_SKILLS = ("persuasion", "deception", "intimidation", "insight")
_PARLEY_DC_BAND = {"easy": 10, "medium": 14, "hard": 18}


def _lead_pc(snapshot: dict) -> tuple[str, dict]:
    """The default parley actor: the first PLAYER in the party, else the first party
    member, else any character. Mirrors server._lead_pc_id."""
    chars = snapshot.get("characters") if isinstance(snapshot.get("characters"), dict) else {}
    party = snapshot.get("party") if isinstance(snapshot.get("party"), list) else []
    for pid in party:
        ch = chars.get(pid) if isinstance(pid, str) else None
        if isinstance(ch, dict) and _text(ch.get("kind")) == "player":
            return pid, ch
    for pid in party:
        ch = chars.get(pid) if isinstance(pid, str) else None
        if isinstance(ch, dict):
            return pid, ch
    for cid, ch in chars.items():
        if isinstance(ch, dict):
            return cid, ch
    return "", {}


def _suggested_parley_dc(difficulty: str, house_difficulty: str) -> int:
    base = _PARLEY_DC_BAND.get(difficulty.strip().lower(), _PARLEY_DC_BAND["medium"])
    shift = {"hard": 2, "easy": -2}.get(house_difficulty, 0)
    return base + shift


def _parley_event_trigger_holds(event: dict, snapshot: dict) -> bool:
    """Snapshot-side mirror of engine `events.trigger_holds` (Quest & Arc engine, Layer 3) —
    reads ONLY the engine-mutated flags / faction reputation / day already in the snapshot
    (contract-safe; no fiction). Kept in lock-step with servers/engine/events.py so the viewer
    surfaces exactly the Events the engine's `present_events` would. An unknown trigger or a
    malformed operand degrades to "not available" (never raises)."""
    trig = event.get("trigger") or "manual"
    if trig == "manual":
        return True
    flags = snapshot.get("flags") if isinstance(snapshot.get("flags"), dict) else {}
    if trig == "flag_set":
        name = str(event.get("trigger_value") or "")
        return bool(name) and bool(flags.get(name))
    if trig == "day_reached":
        try:
            day = int(snapshot.get("day", 1))
            return day >= int(event.get("trigger_threshold", 0))
        except (TypeError, ValueError):
            return False
    if trig == "reputation_at":
        factions = snapshot.get("factions") if isinstance(snapshot.get("factions"), dict) else {}
        fac = factions.get(str(event.get("trigger_faction_id") or ""))
        if not isinstance(fac, dict):
            return False
        try:
            rep = int(fac.get("reputation", 0))
            target = int(event.get("trigger_threshold", 0))
        except (TypeError, ValueError):
            return False
        return rep <= target if target < 0 else rep >= target
    return False


def _live_parley_event(snapshot: dict) -> dict | None:
    """The first UNRESOLVED, trigger-met Event in the snapshot (by id, stable), projected to the
    surface shape ``{id, prompt, anchor_npc_id, options:[{label, tag, skill, dc}], resolve_with}``
    — or None when no Event is live. Mirrors engine `events.present` (deterministic id order,
    skips resolved). Read-only; the viewer never resolves — a picked option relays via /move and
    the DM agent calls `resolve_event`."""
    raw_events = snapshot.get("events")
    if not isinstance(raw_events, dict):
        return None
    for _eid, ev in sorted(raw_events.items(), key=lambda kv: kv[0]):
        if not isinstance(ev, dict) or ev.get("resolved"):
            continue
        if not _parley_event_trigger_holds(ev, snapshot):
            continue
        raw_opts = ev.get("options") if isinstance(ev.get("options"), list) else []
        options = [
            {
                "label": _text(o.get("label")),
                "tag": _text(o.get("tag")),
                "skill": _text(o.get("skill")),
                "dc": int(o.get("dc", 0)) if isinstance(o.get("dc"), (int, float)) else 0,
            }
            for o in raw_opts
            if isinstance(o, dict)
        ]
        return {
            "id": _text(ev.get("id")),
            "prompt": _text(ev.get("prompt")),
            "anchor_npc_id": _text(ev.get("anchor_npc_id")),
            "options": options,
            "resolve_with": "resolve_event",
        }
    return None


def build_parley_surface(
    snapshot: dict,
    *,
    campaign_id: str,
    live: bool,
    is_live_view: bool,
    difficulty: str = "medium",
) -> dict:
    """Project a parley menu for the lead PC: actor + per-skill {skill, modifier,
    suggested_dc} + alignment + free_form. Prefers the engine's own
    generate_parley_options (loaded via models.Campaign) for sheet-correct modifiers;
    degrades to a snapshot-only computation mirroring it. Closes the UI side of #141.

    Quest & Arc engine, Layer 3: when a first-class stumble-into Event is live (unresolved +
    its contract-safe trigger holds in the snapshot), an optional ``event`` block is added —
    ``{id, prompt, anchor_npc_id, options:[{label, tag, skill, dc}], resolve_with}`` — so the
    authored Event options surface as the menu slots. The free-form path STAYS (never a closed
    set, #141 guard); a picked option still relays via /move and the DM agent calls
    `resolve_event`. No live Event -> no block (today's freeform parley, byte-for-byte)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    actor_id, actor = _lead_pc(snapshot)
    # Parley backdrop scope (P2 fix): the old behavior reused the SAME `location:<id>` scope
    # the Table scene plate and the Map sidebar already render, so every conversation at a
    # location showed the identical skyline — reading as "the art is broken / there's only one
    # picture." Differentiate per NPC/event: when a live stumble-into Event is anchored on an
    # NPC, prefer that NPC's portrait-derived scope (so each interlocutor gets a distinct
    # backdrop); else an explicit per-event scene scope; else fall back to the location plate.
    # Computed below once `live_event` is resolved; seeded here with the location fallback.
    loc_id = _text(snapshot.get("current_location_id"))
    parley_scope = f"location:{loc_id}" if loc_id else ""
    base = {
        "campaign_id": campaign_id,
        "title": _text(snapshot.get("title"), campaign_id or "Open Worlds"),
        "dayLabel": _openworlds_day_label(snapshot),
        "actor": _text(actor.get("name"), actor_id),
        "actor_id": actor_id,
        "location_id": loc_id,
        "imageScope": parley_scope,
        "alignment": _text(actor.get("alignment")),
        "free_form": True,
        "difficulty": difficulty,
        "guidance": (
            "Author 2-4 SHORT options tagged by alignment + skill+DC + a "
            "reputation/consequence hint, then ALWAYS leave a free-form path. These are "
            "slots, not lines — voice the prose yourself."
        ),
        "skills": [],
        "live": bool(live),
        "is_live_view": bool(is_live_view),
        "can_act": bool(live and is_live_view),
        "state_authority": "engine",
        "write_lane": "/move",
    }
    # Quest & Arc engine, Layer 3: surface a live stumble-into Event's authored options as the
    # menu slots when one is available. Independent of the actor's sheet (a stumble-into is about
    # the situation), so it attaches even on the empty/no-actor path. The free-form path stays.
    live_event = _live_parley_event(snapshot)
    if live_event is not None:
        base["event"] = live_event
        # Differentiate the parley backdrop by the live Event (P2): prefer the anchor NPC's
        # portrait-derived scope (so each interlocutor gets a distinct plate instead of the
        # shared location skyline), else the event id; the location fallback stays underneath.
        # This is what stops consecutive parleys from all rendering the same location image.
        anchor = _text(live_event.get("anchor_npc_id"))
        ev_id = _text(live_event.get("id"))
        if anchor:
            base["imageScope"] = f"portrait-{anchor}"
        elif ev_id:
            base["imageScope"] = f"event:{ev_id}"
    if not actor_id or not actor:
        base["source"] = "empty"
        return base

    # Default skill set: the actor's proficient/expertise skills UNION the four core
    # social skills (mirror generate_parley_options' default), stable order.
    proficiencies = actor.get("skill_proficiencies") if isinstance(actor.get("skill_proficiencies"), list) else []
    expertise = actor.get("skill_expertise") if isinstance(actor.get("skill_expertise"), list) else []
    chosen = list(dict.fromkeys([*proficiencies, *expertise]))
    for s in _PARLEY_CORE_SKILLS:
        if s not in chosen:
            chosen.append(s)
    house = ""
    hr = snapshot.get("house_rules")
    if isinstance(hr, dict):
        house = _text(hr.get("difficulty"))
    dc = _suggested_parley_dc(difficulty, house)

    skill_rows: list[dict] = []
    for sk in chosen:
        if sk not in _SKILL_ABILITIES:
            continue
        skill_rows.append({
            "skill": sk,
            "label": sk.replace("_", " ").title(),
            "modifier": _skill_bonus_from_sheet(actor, sk),
            "suggested_dc": dc,
            "proficient": sk in proficiencies or sk in expertise,
            "expertise": sk in expertise,
            "core": sk in _PARLEY_CORE_SKILLS,
        })
    base["skills"] = skill_rows
    base["source"] = "viewer.snapshot"
    return base


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
    move_sink_live: bool = False,
    now: float,
) -> dict:
    """Browser-safe OpenWorlds launcher row for one campaign.

    This deliberately projects only player-facing fields. It never includes local
    absolute paths, scene `dm_notes`, lore recall input, sealed agendas, or raw
    session transcripts; follow-on surfaces can add explicit read models when they
    need more data.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    live = bool(current and can_resume and move_sink_live) or (now - last_played) < 90
    location = _display_location(snapshot)
    loc_id = _text(snapshot.get("current_location_id"))
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

    resume_url = f"/openworlds/?campaign={quote(campaign_id)}" if can_resume else ""
    legacy_dashboard_url = f"/dashboard?campaign={quote(campaign_id)}" if can_resume else ""
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
        "locationId": loc_id,
        "imageScope": (f"location:{loc_id}" if loc_id else ""),
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
        "legacyDashboardUrl": legacy_dashboard_url,
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


def _refresh_openworlds_campaign_times(
    cards: list[dict],
    *,
    now: float,
    move_sink_live: bool = False,
) -> list[dict]:
    refreshed: list[dict] = []
    for card in cards:
        c = dict(card)
        last_played = c.get("last_played")
        last_played = (
            last_played
            if isinstance(last_played, (int, float)) and not isinstance(last_played, bool)
            else 0
        )
        live = bool(move_sink_live and c.get("current") and c.get("canResume")) or (now - last_played) < 90
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


def _openworlds_campaigns(attached_campaign: str = "", *, move_sink_live: bool = False) -> dict:
    global _openworlds_catalog_cache
    now = time.time()
    roots = _campaign_catalog_roots()
    current_campaigns_dir = _resolved(_campaigns_dir())
    signature, snapshots = _openworlds_catalog_index(roots, attached_campaign)
    if _openworlds_catalog_cache and _openworlds_catalog_cache[0] == signature:
        out = _refresh_openworlds_campaign_times(
            _openworlds_catalog_cache[1],
            now=now,
            move_sink_live=move_sink_live,
        )
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
                    move_sink_live=move_sink_live,
                    now=now,
                )
            except (OSError, TypeError, ValueError):
                continue
            built.append(summary)
        _openworlds_catalog_cache = (signature, built)
        out = _refresh_openworlds_campaign_times(
            built,
            now=now,
            move_sink_live=move_sink_live,
        )
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
    detail: str | None = None,
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
    if detail:
        item["detail"] = detail
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
                    _action_item("continue", "Continue", kind="do", text="continue", detail="Press onward", disabled_reason=base_reason),
                    _action_item("look", "Look", kind="do", text="look around", detail="Survey scene", disabled_reason=base_reason),
                    _action_item("say", "Say", detail="Speak aloud", disabled_reason=base_reason, ui="focus-say"),
                    _action_item("do", "Do", detail="Act in world", disabled_reason=base_reason, ui="focus-do"),
                    _action_item("check", "Check", detail="Roll a skill", disabled_reason=base_reason, ui="palette-skills"),
                    _action_item("save", "Save", detail="Resist danger", disabled_reason=base_reason, ui="palette-saves"),
                ],
            },
            {
                "id": "combat",
                "label": "Combat",
                "actions": [
                    _action_item("attack", "Attack", kind="attack", name="Attack", detail="Strike a foe", disabled_reason=turn_action_reason("action")),
                    _action_item("bonus-action", "Bonus", kind="combat", name="Bonus Action", detail="Quick extra move", disabled_reason=turn_action_reason("bonus")),
                    _action_item("reaction", "Reaction", kind="combat", name="Reaction", detail="Respond fast", disabled_reason=reaction_reason()),
                ],
            },
        ],
    }
    actions = _session_available_actions(model)
    enabled_actions, blocked_actions = _session_action_buckets(actions)
    model["writeLane"] = _session_write_lane_metadata()
    model["enabledActions"] = enabled_actions
    model["blockedActions"] = blocked_actions
    return model


def _viewer_config() -> dict:
    """Read-only runtime facts for the quick-settings modal — voice backend + whether
    the voice server is present, and whether a live move sink is configured. Pure
    reader: no writes, no engine import, just env + filesystem the viewer already
    knows. Campaign settings (pacing_mode, leveling_mode) come from /state instead."""
    backend = (env_var("TTS_BACKEND", "kokoro") or "kokoro").strip().lower() or "kokoro"
    voice_ready = _VOICE_DIR.is_dir() and backend != "null"
    return {
        "voice": {"backend": backend, "ready": voice_ready},
        # The engine's image provider runs server-side; the viewer can only say whether
        # any cached art exists for this state dir (a non-empty images/ tree).
        "image": {"cache_present": (_state_dir() / "images").is_dir()},
        "moves_enabled": _moves_path() is not None,
    }


def _file_line_count(path: str) -> int:
    if not path:
        return 0
    try:
        return sum(1 for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _chat_file_summary(path: str) -> dict:
    """Summarize the two-sided chat tail without mutating live campaign state."""
    summary = {"line_count": 0, "last_role": "", "pending_player_turn": False}
    if not path:
        return summary
    last: dict | None = None
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                summary["line_count"] += 1
                last = payload
    except OSError:
        return summary
    role = str((last or {}).get("role") or "").strip().lower()
    summary["last_role"] = role
    summary["pending_player_turn"] = role == "player"
    return summary


def _provider_status_summary() -> dict:
    """Read the provider lifecycle sidecar without mutating campaign state."""
    path = _state_dir() / "provider_status.json"
    fallback = {
        "schema": "worldos.provider-status.v1",
        "status": "unknown",
        "reason": "",
        "detail": "",
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    return {
        "schema": str(payload.get("schema") or "worldos.provider-status.v1"),
        "provider": str(payload.get("provider") or ""),
        "status": str(payload.get("status") or "unknown"),
        "reason": str(payload.get("reason") or ""),
        "detail": str(payload.get("detail") or ""),
        "max_turns": payload.get("max_turns"),
        "dm_turns": payload.get("dm_turns"),
        "updated_at": payload.get("updated_at"),
    }


def _repo_build_sha() -> str:
    env = env_var("BUILD_SHA", "")
    if env and env.strip():
        return env.strip()
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except Exception:
        return "unknown"
    return (proc.stdout or "").strip() or "unknown"


def _repo_version() -> str:
    env = env_var("APP_VERSION", "")
    if env and env.strip():
        return env.strip()
    version_file = _REPO_ROOT / "VERSION"
    try:
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        pass
    return "unknown"


def _move_run_id(dest: Path | None) -> str:
    if dest is not None and dest.name == "player_moves.jsonl":
        return dest.parent.name
    return ""


def _app_status_image_probe(surface: dict) -> bool:
    scene = surface.get("scene") if isinstance(surface.get("scene"), dict) else {}
    scope = scene.get("imageScope") if isinstance(scene, dict) else ""
    return bool(scope and _latest_descriptor(str(scope)))


def _browser_health_counts(console_log: str | None, network_log: str | None) -> tuple[int, int]:
    def iter_ndjson(path_value: str | None):
        if not path_value:
            return
        try:
            path = Path(path_value).expanduser().resolve(strict=True)
        except (OSError, FileNotFoundError):
            return
        try:
            with path.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except OSError:
            return

    console_errors = 0
    for item in iter_ndjson(console_log) or ():
        kind = str(item.get("type") or item.get("level") or "").lower()
        text = str(item.get("text") or item.get("message") or item.get("error") or "").lower()
        if kind in {"error", "pageerror", "fatal"} or "uncaught" in text or "exception" in text:
            console_errors += 1

    network_failures = 0
    for item in iter_ndjson(network_log) or ():
        status = item.get("status") or item.get("status_code")
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            status_int = 0
        if item.get("error") or item.get("failed") or status_int >= 400:
            network_failures += 1
    return console_errors, network_failures


def _app_status_readiness(*, live: dict | None, moves: Path | None, is_live_view: bool,
                          actor: dict, enabled_actions: list[str],
                          art_root: Path, chat_lines: int, surface: dict,
                          provider: str, pending_player_turn: bool = False,
                          provider_status: dict | None = None,
                          console_errors: int = 0,
                          network_failures: int = 0) -> tuple[dict, dict]:
    same_port_alive = True  # This payload was generated by the same port that answered /app-status.
    route_loaded = True
    moves_writable = bool(live and moves is not None)
    surface_can_act = bool(surface.get("can_act"))
    provider_ready = bool(live and provider.strip())
    provider_lifecycle = str((provider_status or {}).get("status") or "").strip().lower()
    provider_stopped = provider_lifecycle in {"stopped", "failed", "exhausted"}
    actor_present = bool(actor.get("id") or actor.get("name"))
    actions_present = bool(enabled_actions)
    recent = surface.get("recentEvents") if isinstance(surface.get("recentEvents"), list) else []
    recent_narration = sum(
        1 for item in recent
        if isinstance(item, dict) and item.get("kind") in {"narration", "dialogue"}
    )
    narration_present = chat_lines > 0 or recent_narration > 0
    art_present = art_root.is_dir()
    image_probe_ok = _app_status_image_probe(surface)
    console_errors = max(0, int(console_errors or 0))
    network_failures = max(0, int(network_failures or 0))

    failure_bucket = "none"
    failure_detail = ""
    if not same_port_alive or not route_loaded:
        failure_bucket = "no_launcher"
        failure_detail = "viewer route is not live on the same localhost port as app-status"
    elif not art_present or not image_probe_ok:
        failure_bucket = "no_art"
        failure_detail = "private art root or representative image probe is missing"
    elif provider_stopped:
        failure_bucket = "no_provider"
        failure_detail = str((provider_status or {}).get("detail") or "DM provider is no longer running")
    elif not provider_ready or not moves_writable or not is_live_view or not surface_can_act:
        failure_bucket = "no_provider"
        failure_detail = "live provider move sink is not ready"
    elif not actor_present:
        failure_bucket = "no_actor"
        failure_detail = "no active player actor is seated"
    elif not actions_present:
        failure_bucket = "no_actions"
        failure_detail = "no enabled player actions are exposed"
    elif not narration_present:
        failure_bucket = "no_narration"
        failure_detail = "no visible narration/chat has been observed"
    elif console_errors:
        failure_bucket = "console_error"
        failure_detail = "browser console errors were reported"
    elif network_failures:
        failure_bucket = "console_error"
        failure_detail = "browser network failures were reported"

    ready_for_smoke = failure_bucket == "none"
    ready_for_play = (
        ready_for_smoke
        and not pending_player_turn
        and provider.strip().lower() in {"codex", "claude", "openclaw", "scripted"}
    )
    status = "busy" if ready_for_smoke and pending_player_turn else ("ready" if ready_for_smoke else "degraded")
    health = {
        "same_port_alive": same_port_alive,
        "route_loaded": route_loaded,
        "console_errors": console_errors,
        "network_failures": network_failures,
        "provider_ready": provider_ready,
        "provider_status": provider_status or {},
        "image_probe_ok": image_probe_ok,
        "pending_player_turn": bool(pending_player_turn),
        "failure_bucket": failure_bucket,
        "failure_detail": failure_detail,
    }
    readiness = {
        "status": status,
        "ready_for_smoke": ready_for_smoke,
        "ready_for_play": ready_for_play,
        "pending_player_turn": bool(pending_player_turn),
        "failure_bucket": failure_bucket,
        "failure_detail": failure_detail,
    }
    return readiness, health


def _app_status_payload(*, port: int, attached_campaign_id: str, viewed_campaign_id: str,
                        transcript_path: str, chat_path: str) -> dict:
    """Machine-readable app/test harness truth for agents.

    This is a read-only probe over the same viewer facts the UI already uses. It is intentionally
    small and stable: harnesses can ask which campaign/run/provider/art root they are actually
    driving before spending model time or trusting a screenshot.
    """
    live = _live_play()
    moves = _moves_path()
    raw_snap = _read_snapshot(viewed_campaign_id) if viewed_campaign_id else {}
    if not isinstance(raw_snap, dict):
        raw_snap = {}
    is_live_view = bool(live and viewed_campaign_id and viewed_campaign_id == attached_campaign_id)
    surface = build_session_surface(
        raw_snap,
        campaign_id=viewed_campaign_id,
        live=live,
        is_live_view=is_live_view,
        recent_events=_session_event_tail(viewed_campaign_id) if viewed_campaign_id else [],
    )
    enabled_actions = [
        str(action.get("id") or "")
        for action in surface.get("enabledActions", [])
        if isinstance(action, dict) and action.get("id")
    ]
    actor = (surface.get("actionModel") or {}).get("actor") or {}
    art_root = _ingested_images_root()
    state_root = _state_dir()
    provider = env_var("PROVIDER", "") or ""
    chat_summary = _chat_file_summary(chat_path)
    chat_lines = int(chat_summary.get("line_count") or 0)
    pending_player_turn = bool(chat_summary.get("pending_player_turn"))
    provider_status = _provider_status_summary()
    console_errors, network_failures = _browser_health_counts(
        env_var("BROWSER_CONSOLE_LOG", ""),
        env_var("BROWSER_NETWORK_LOG", ""),
    )
    readiness, health = _app_status_readiness(
        live=live,
        moves=moves,
        is_live_view=is_live_view,
        actor=actor,
        enabled_actions=enabled_actions,
        art_root=art_root,
        chat_lines=chat_lines,
        surface=surface,
        provider=provider,
        pending_player_turn=pending_player_turn,
        provider_status=provider_status,
        console_errors=console_errors,
        network_failures=network_failures,
    )
    provider_lifecycle = str(provider_status.get("status") or "").strip().lower()
    provider_stopped = provider_lifecycle in {"stopped", "failed", "exhausted"}
    effective_can_act = bool(surface.get("can_act")) and not pending_player_turn and not provider_stopped
    effective_enabled_actions = enabled_actions if effective_can_act else []
    return {
        "ok": True,
        "schema": "worldos.app-status.v1",
        "surface": "openworlds",
        "state_authority": "engine",
        "write_lane": "/move",
        "build": {
            "sha": _repo_build_sha(),
            "version": _repo_version(),
        },
        "viewer": {
            "port": int(port),
            "repo_root": _resolved(_REPO_ROOT),
            "state_root": _resolved(state_root),
            "provider": provider,
            "transcript_path": transcript_path,
            "chat_path": chat_path,
            "chat_lines": chat_lines,
            "last_chat_role": str(chat_summary.get("last_role") or ""),
            "provider_status": provider_status,
        },
        "art": {
            "repo_root": _resolved(_art_repo_root()),
            "private_root": _resolved(art_root),
            "private_root_present": art_root.is_dir(),
        },
        "live": {
            "attached_campaign_id": attached_campaign_id,
            "campaign_id": viewed_campaign_id,
            "active_session_id": str(raw_snap.get("active_session_id") or ""),
            "run_id": _move_run_id(moves),
            "moves_path": _resolved(moves) if moves is not None else "",
            "moves_writable": bool(live),
            "is_live_view": is_live_view,
            "surface_can_act": bool(surface.get("can_act")),
            "pending_player_turn": pending_player_turn,
            "can_act": effective_can_act,
            "actor": {
                "id": str(actor.get("id") or ""),
                "name": str(actor.get("name") or ""),
                "kind": str(actor.get("kind") or ""),
            },
            "surface_enabled_action_ids": enabled_actions,
            "enabled_action_ids": effective_enabled_actions,
            "enabled_action_count": len(effective_enabled_actions),
        },
        "readiness": readiness,
        "health": health,
        "endpoints": {
            "app_status": "/app-status",
            "session_surface": "/session-surface",
            "campaign_catalog": "/openworlds/campaigns.json",
            "move": "/move",
            "chat": "/chat",
            "activity": "/activity",
            "image": "/image?scope=<scope>",
        },
    }


def _active_session_id(campaign_id: str) -> str:
    """Resolve the campaign's current session id (the session-log basename the /events feed tails).
    BUG2: this is the namespace the viewer composes onto each `seq` (`${sid}:${seq}`) so the dedup/
    order key is globally unique across a session ROTATION — without it a fresh session's narration
    (which restarts at line 0,1,2) collides with the prior session's seq 0,1,2 and is suppressed."""
    snap = _read_snapshot(campaign_id)
    sid = snap.get("active_session_id") or (snap.get("session_ids") or [None])[-1]
    return sid if isinstance(sid, str) else ""


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
            row = json.loads(stripped)
        except json.JSONDecodeError:
            break  # half-written trailing line — DON'T advance past it; re-read next poll
        # Stamp the row with its ABSOLUTE line index in the session log as a STABLE id (`seq`).
        # The viewer keys narration dedup + chronological order off this — NOT off the prose —
        # so a beat the DM rewords between its streamed copy and its turn-END /chat reply can't
        # show twice, and a re-ingest (windowing / cursor-rewind on session rotation) of the same
        # line collapses to one row. The session log is the engine's sole-writer narration record,
        # so a line index is a true monotonic per-beat identity. `consumed` is this line's index
        # (it was incremented for every prior line, blank or not), and the same absolute indexing is
        # mirrored by _session_recent_events so the leading history band shares the key space.
        if isinstance(row, dict):
            row.setdefault("seq", consumed)
        out.append(row)
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
    backend = (env_var("TTS_BACKEND", "kokoro") or "kokoro").strip().lower() or "kokoro"
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


# --- POST /portrait-gen — opt-in "Generate a unique face" for a player-created PC (#265).
# The Create wizard offers BOTH the shipped 12-face gallery (the default) AND this opt-in
# generated portrait. We SHELL the engine's imagegen layer (uv run --directory servers/engine)
# exactly as /speak shells the voice server and play.sh shells the engine — keeping THIS reader
# a pure reader of engine *modules* (it imports nothing from the engine). imagegen.generate
# writes ONLY the derived cache (<state>/images/<scope>/…), never snapshot.json, so the engine's
# sole-writer invariant holds even though it's "a write".
#
# A player-created PC has no engine id during the wizard, so we generate to a PROVISIONAL
# content-scope (portrait-pc-<stableHash of name|race|class|seed>) that the wizard can render
# immediately via <Img scope=…>; play.sh re-keys it onto portrait-<char_id> at PC-mint time.
#
# CRITICAL — QA/tests never hit the gateway: this route does NOT set CLAWDND_IMAGE_PROVIDER.
# It inherits the process env, so on a normal dev/QA box (provider unset → null) the call
# returns a placeholder with NO network, and the UI keeps the gallery selection. The gateway
# path engages ONLY when the host already has CLAWDND_IMAGE_PROVIDER=openclaw + a token wired.

# Runs in the engine's environment (cwd = servers/engine; `import imagegen`/`store` resolve).
# Reads one JSON request from stdin {race,class,name?,appearance?,seed?,scope}, builds the
# painterly brief, generates (provider selected by inherited env), and prints ONE compact JSON
# verdict line. NEVER raises — any failure maps to a placeholder verdict so the caller can fall
# back to the gallery face.
_PORTRAIT_GEN_SNIPPET = r"""
import json, sys
import imagegen
def main():
    raw = sys.stdin.read()
    req = json.loads(raw) if raw.strip() else {}
    race = (req.get("race") or "").strip()
    class_ = (req.get("class") or req.get("class_") or "").strip()
    scope = (req.get("scope") or "").strip()
    seed = req.get("seed")
    try:
        seed = int(seed) if seed is not None and str(seed) != "" else None
    except (TypeError, ValueError):
        seed = None
    prompt = imagegen.portrait_prompt(
        race, class_,
        name=req.get("name"), appearance=req.get("appearance"), alignment=req.get("alignment"),
    )
    desc = imagegen.generate("portrait", prompt, seed=seed, scope=scope)
    # A real, servable portrait carries path/url/bytes_b64 and is NOT a placeholder/degraded.
    servable = bool(desc.get("path") or desc.get("url") or desc.get("bytes_b64"))
    generated = servable and not desc.get("placeholder") and not desc.get("degraded_from")
    print(json.dumps({
        "ok": True, "scope": scope, "generated": bool(generated),
        "placeholder": bool(desc.get("placeholder")) or not servable,
        "provider": desc.get("provider", "null"),
        "degraded_from": desc.get("degraded_from"),
        "prompt": prompt,
    }))
try:
    main()
except Exception as exc:  # never let an exception escape — caller maps to ok:false
    print(json.dumps({"ok": False, "reason": "portrait-gen error: " + str(exc)[:200]}))
"""

# Interactive budget: the OpenClaw client's default 180s poll is far too long for a wizard
# button. Bound the wait via CLAWDND_OPENCLAW_POLL_TIMEOUT (the engine client honors it) so a
# slow/remote gateway can't hang the call, and cap the whole subprocess just above it.
_PORTRAIT_GEN_POLL_TIMEOUT = "60"
_PORTRAIT_GEN_TIMEOUT = 75  # seconds — above the poll budget; never unbounded.


def _portrait_gen_scope(race: str, class_: str, name: str, seed: object) -> str:
    """Deterministic provisional scope for a wizard-generated PC portrait (#265).

    The PC has no engine id yet, so key the generated face by a stable content hash of
    (name|race|class|seed) — the SAME inputs the wizard knows — as portrait-pc-<hash>.
    The wizard renders it immediately via <Img scope=…>; play.sh re-keys it onto the real
    portrait-<char_id> once the PC is minted. Length-capped + sanitized (it's a path seg)."""
    basis = "|".join([
        str(name or "").strip().lower(),
        str(race or "").strip().lower(),
        str(class_ or "").strip().lower(),
        "" if seed is None else str(seed),
    ])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"portrait-pc-{digest}"


def _portrait_gen(payload: dict) -> dict:
    """Generate (or recall) a unique PC portrait via the engine; return a UI-ready verdict.

    Contract (never hangs, never errors the page) — mirrors _speak:
    - a real face was produced/cached -> {"ok": True, "generated": True, "scope": …}
    - null/offline (no provider)      -> {"ok": True, "generated": False, "placeholder": True, …}
    - bad inputs / uv missing / crash -> {"ok": False, "reason": …}
    The heavy lifting runs in a bounded engine subprocess so a wedged gateway can't tie up the
    viewer thread (and the server is threaded, so other requests stay responsive regardless)."""
    race = str(payload.get("race") or "").strip()
    class_ = str(payload.get("class") or payload.get("class_") or "").strip()
    if not race or not class_:
        return {"ok": False, "reason": "portrait-gen needs 'race' and 'class'"}
    if not _ENGINE_DIR.is_dir():
        return {"ok": False, "reason": "engine not found"}
    seed = payload.get("seed")
    scope = _portrait_gen_scope(race, class_, str(payload.get("name") or ""), seed)
    req = json.dumps({
        "race": race, "class": class_,
        "name": payload.get("name"), "appearance": payload.get("appearance"),
        "alignment": payload.get("alignment"), "seed": seed, "scope": scope,
    })
    # Inherit env so the provider stays whatever the HOST configured (null on a normal box —
    # no network, no gateway). Add ONLY the interactive poll bound; never set the provider here.
    env = dict(os.environ)
    # Set BOTH names so the engine child resolves the bound regardless of which
    # convention it reads (the engine prefers WORLDOS_*; CLAWDND_* is the v1.x
    # warn-only fallback). See issue #295 (W0-E).
    if "WORLDOS_OPENCLAW_POLL_TIMEOUT" not in env and "CLAWDND_OPENCLAW_POLL_TIMEOUT" not in env:
        env["WORLDOS_OPENCLAW_POLL_TIMEOUT"] = _PORTRAIT_GEN_POLL_TIMEOUT
        env["CLAWDND_OPENCLAW_POLL_TIMEOUT"] = _PORTRAIT_GEN_POLL_TIMEOUT
    cmd = [
        "uv", "run", "--directory", str(_ENGINE_DIR), "--no-project",
        "python", "-c", _PORTRAIT_GEN_SNIPPET,
    ]
    try:
        proc = subprocess.run(
            cmd, input=req, capture_output=True, text=True,
            timeout=_PORTRAIT_GEN_TIMEOUT, env=env,
        )
    except FileNotFoundError:
        return {"ok": False, "reason": "uv not installed", "scope": scope}
    except subprocess.TimeoutExpired:
        # The gateway was too slow — keep the gallery face; the cache write (if it lands
        # later) is harmless and simply ignored.
        return {"ok": False, "reason": "portrait generation timed out", "scope": scope}
    except Exception as exc:  # noqa: BLE001 — last-resort guard; /portrait-gen must never 500
        return {"ok": False, "reason": f"portrait-gen error: {exc}", "scope": scope}
    line = (proc.stdout or "").strip().splitlines()
    res: dict = {}
    if line:
        try:
            res = json.loads(line[-1])  # the snippet's final line is its JSON verdict
        except json.JSONDecodeError:
            res = {}
    if not res:
        reason = (proc.stderr or "").strip().splitlines()
        return {"ok": False, "reason": (reason[-1][:160] if reason else "portrait engine unavailable"), "scope": scope}
    res.setdefault("scope", scope)
    return res


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
        "app_status": "/app-status",
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
    if route in (_OPENWORLDS_ROUTE, f"{_OPENWORLDS_ROUTE}/"):
        index = _OPENWORLDS_DIR / "index.html"
        return index if index.is_file() else None
    if not route.startswith(f"{_OPENWORLDS_ROUTE}/"):
        return None
    rel = unquote(route[len(_OPENWORLDS_ROUTE) + 1:])
    if not rel or rel.endswith("/"):
        rel = f"{rel}index.html"
    try:
        root = _OPENWORLDS_DIR.resolve()
        target = (root / rel).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


def _openworlds_asset_version() -> str:
    """A cache-busting token that changes whenever any local script source changes.
    The OpenWorlds SPA loads its modules via plain <script src="X.jsx"> tags with no
    version, and a long-lived WebView (the native app's WKWebView) or browser profile
    can hold a STALE copy of those modules across launches — so a player relaunches and
    still sees retired UI (e.g. old honesty badges). Stamping the src with ?v=<mtime>
    forces every client to re-fetch the moment a .jsx/.js changes."""
    latest = 0.0
    try:
        for p in _OPENWORLDS_DIR.iterdir():
            if p.suffix.lower() in (".jsx", ".js") and p.is_file():
                latest = max(latest, p.stat().st_mtime)
    except OSError:
        return "0"
    return str(int(latest))


def _openworlds_index_bytes(asset: Path) -> bytes:
    """Serve the OpenWorlds index.html with each local script src version-stamped, so a
    cached WebView/browser never renders against stale modules (see
    _openworlds_asset_version)."""
    html = asset.read_text(encoding="utf-8")
    ver = _openworlds_asset_version()
    # Stamp the more specific .jsx" first so the .js" pass can't touch an already-stamped
    # .jsx?v=... (which contains no .js" substring). Vendor .js are stamped too — harmless.
    html = html.replace('.jsx"', f'.jsx?v={ver}"').replace('.js"', f'.js?v={ver}"')
    return html.encode("utf-8")


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

    def _live_play_view_campaign(self, query: dict) -> str:
        """The campaign the LIVE-PLAY gates (/app-status, /session-surface) should project.

        Same as `_view_campaign`, but with a NARROW self-healing step that fixes the
        permanent action-lock wedge: the play surface gates every action on
        `can_act = live AND viewed == attached`. The attached campaign is recency-resolved
        and re-evaluated each request (the viewer launches UNPINNED for `scripts/play.sh` and
        the native app), while the browser sends a STICKY `?campaign=` derived from its catalog
        pick. If those drift — the catalog poll briefly drops `current`, the provider process
        exits so the client's native auto-follow stops re-syncing, or a second save out-ranks
        the live run on recency — the client keeps posting a STALE `viewed`, so `is_live_view`
        latches False with no recovery and the table reads "live provider move sink is not
        ready" / "viewing non-live campaign" forever (a release blocker).

        Recovery rule (matches the move sink's own truth, so it can't misroute a write):
        FOLLOW the attached campaign when the move sink is live AND we are in AUTO-FOLLOW
        (unpinned) mode AND the attached campaign is a real, resumable PLAY-store campaign AND
        the client's view is stale — empty, or pointing at a campaign that is NOT the current
        live run. We do NOT snap when launched PINNED (an explicit director's view stays
        gated), nor when the viewed campaign IS the live one (already correct), nor when there
        is no resolvable live campaign (nothing to follow — keep the honest read-only state).
        The POST /move cross-campaign refusal (engine = sole writer) is unchanged; this only
        re-aligns the READ-model gate to the sink the writes already go to."""
        attached = self._resolve_campaign()
        if self.pinned or not attached or not _live_play():
            # Pinned view, no live game, or no attached campaign → leave the switcher exactly
            # as-is (a genuine read-only/non-live director's view MUST stay gated).
            return self._view_campaign(query)
        override = _safe_campaign_id((query.get("campaign") or [""])[0])
        if override and override == attached:
            return override  # already viewing the live run — nothing to heal
        # The attached campaign is only the recovery target when it is the SINGLE current,
        # resumable play-store campaign (so we never follow into a parallel/stale store). Pass
        # the just-resolved `attached` so the `current` flag is correct regardless of class state.
        live_current = [
            c.get("id") for c in _list_campaigns(attached)
            if isinstance(c, dict) and c.get("current") and c.get("live")
        ]
        if attached in live_current and (not override or override not in live_current):
            # Client view is stale (empty, or a non-live save) while exactly the attached run
            # is live → follow it so the action layer recovers the moment the sink is healthy.
            return attached
        return override or attached

    def _serve_simple_surface(self, qs: dict, builder, *, heal: bool = False) -> None:
        """Dispatch a snapshot-only read-model surface (journal/character/inventory/
        relations/parley). Mirrors the /atlas-surface handler exactly: a catalog ?source/
        ?run ref wins (a QA/parallel run), else the per-request ?campaign view override,
        else the lazily-attached campaign; an absent/empty snapshot degrades to a graceful
        empty surface. `builder(snapshot, campaign_id=, live=, is_live_view=) -> dict`."""
        live = _live_play()
        catalog_ref = _session_surface_catalog_ref(qs)
        if catalog_ref is not None:
            cid, raw_snap, _campaign_dir_path, root_is_current = catalog_ref
            self._json(builder(
                raw_snap,
                campaign_id=cid,
                live=live,
                is_live_view=bool(live and root_is_current and cid == self.campaign_id),
            ))
            return
        # Symmetric heal (#640): a play surface (parley/character) opts into the SAME self-healing
        # the /session-surface gate uses, so a drifted client ?campaign re-aligns to the live run
        # instead of latching is_live_view=False ("viewing non-live campaign" read-only). Pure
        # read-only views (journal/seed/acts/inventory/relations) keep the literal ?campaign view.
        cid = self._live_play_view_campaign(qs) if heal else self._view_campaign(qs)
        if not cid:
            self._json(builder({}, campaign_id="", live=live, is_live_view=False))
            return
        raw_snap = _read_snapshot(cid)
        if not isinstance(raw_snap, dict):
            raw_snap = {}
        self._json(builder(
            raw_snap,
            campaign_id=cid,
            live=live,
            is_live_view=live and cid == self.campaign_id,
        ))

    def _build_render_surfaces(self, qs: dict) -> dict:
        """GRAPHICS #455 — build the {atlas, combat, character} bundle a graphical client needs,
        using the EXACT same builders + campaign-resolution the GET routes use (catalog ?source/
        ?run ref wins, else ?campaign view, else attached campaign; empty snapshot -> graceful
        empty surfaces). This guarantees the SSE payloads are byte-identical to the polled ones.
        Pure reader; never writes."""
        live = _live_play()
        catalog_ref = _session_surface_catalog_ref(qs)
        if catalog_ref is not None:
            cid, raw_snap, campaign_dir, root_is_current = catalog_ref
            is_live = bool(live and root_is_current and cid == self.campaign_id)
            recent = _session_event_tail_from_dir(campaign_dir, raw_snap)
        else:
            # Symmetric heal (#640): match the GET combat/atlas surfaces (and /session-surface) so
            # the SSE render mirror re-aligns to the live run too — keeps SSE consistent with polled.
            cid = self._live_play_view_campaign(qs)
            raw_snap = _read_snapshot(cid) if cid else {}
            if not isinstance(raw_snap, dict):
                raw_snap = {}
            is_live = bool(live and cid and cid == self.campaign_id)
            recent = _session_event_tail(cid) if cid else None
        cid = cid or ""
        combat_kwargs = {"recent_events": recent} if recent is not None else {}
        return {
            "type": "surfaces",
            "campaign_id": cid,
            "atlas": build_atlas_surface(raw_snap, campaign_id=cid, live=live, is_live_view=is_live),
            "combat": build_combat_surface(raw_snap, campaign_id=cid, live=live,
                                           is_live_view=is_live, **combat_kwargs),
            "character": build_character_surface(raw_snap, campaign_id=cid, live=live,
                                                 is_live_view=is_live),
        }

    def _serve_surface_stream(self, qs: dict) -> None:
        """GRAPHICS #455 — Server-Sent-Events stream of the render surfaces. Emits a `surfaces`
        event whenever the bundle changes (+ a heartbeat comment between), so a client gets push
        latency instead of a 3s poll. BOUNDED so it never hangs a worker or test: `?once=1`
        sends one event and closes; `?max_seconds=N` caps the lifetime (default 30, hard-capped
        300); the loop also exits on client disconnect. Additive — the polled GET surfaces are
        unchanged and remain the canonical fallback."""
        once = (qs.get("once") or ["0"])[0] in ("1", "true", "yes")
        try:
            max_seconds = float((qs.get("max_seconds") or ["30"])[0])
        except (TypeError, ValueError):
            max_seconds = 30.0
        max_seconds = max(1.0, min(max_seconds, 300.0))
        try:
            interval = float((qs.get("interval") or ["1"])[0])
        except (TypeError, ValueError):
            interval = 1.0
        interval = max(0.25, min(interval, 10.0))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")  # don't let a proxy buffer the stream
        self.end_headers()

        deadline = time.monotonic() + max_seconds
        last_sig: str | None = None
        try:
            # tell the client to reconnect ~1s after we cap/close the stream (low combat latency).
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while True:
                bundle = self._build_render_surfaces(qs)
                data = json.dumps(bundle)
                sig = str(hash(data))
                if sig != last_sig:
                    self.wfile.write(f"event: surfaces\ndata: {data}\n\n".encode("utf-8"))
                    last_sig = sig
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                if once or time.monotonic() >= deadline:
                    break
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # client disconnected — normal SSE teardown, not an error.
            return

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
            # A PC pulled from canon is minted with an opaque engine id (portrait-char_…);
            # retry by the character's name slug so their real ingested face still resolves.
            desc = _portrait_by_name(scope, self.campaign_id or _pick_campaign(None) or "")
        if not desc:
            # A character with no real face 404s here and the UI shows a portrait-shaped
            # silhouette placeholder. We deliberately do NOT substitute a class/race heraldic
            # crest — a coat of arms is not a person's face, and dressing a faceless PC in one
            # reads as a bug, not art. Canon NPCs resolve to real ingested portraits above.
            self._send(404, b"no image", "text/plain")
            return
        ctype = desc.get("mime_type")
        ctype = ctype if isinstance(ctype, str) and ctype.strip() else "image/png"
        # 1) a real file on disk — ONLY if it's contained under an expected image root
        # (the derived cache, the OpenClaw gateway media dir, or the gitignored _private
        # ingested-art tree). The viewer is the documented "pure reader": a descriptor's
        # `path` must never let /image serve an arbitrary file (e.g. /etc/passwd) even
        # if tampered. W2b adds the _private ingested root so wiki_images.py output is
        # served transparently alongside generated images.
        path = desc.get("path")
        if isinstance(path, str) and path:
            _oh = os.environ.get("OPENCLAW_HOME")
            roots = [
                _state_dir() / "images",
                Path(env_var_legacy("CLAWDND_OPENCLAW_MEDIA_DIR")
                     or ((Path(_oh) if _oh else Path.home() / ".openclaw") / "media" / "tool-image-generation")),
                # W2b: ingested wiki art (gitignored _private; never committed).
                _ingested_images_root(),
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

    def _serve_export(self, campaign_id: str) -> None:
        """GET /export?campaign=<id> — stream a campaign's snapshot.json as a download.

        The export IS the engine-owned campaign aggregate, served verbatim from disk so the
        downloaded chronicle byte-for-byte matches what the engine wrote (we deliberately do
        NOT re-serialize a parsed dict, which would lose the engine's exact formatting). Pure
        reader: never writes, never imports the engine. 404s cleanly when the campaign id is
        empty/unsafe/unknown or the snapshot is missing, so the UI can surface a clear message.
        Content-Disposition names the file <campaign_id>-chronicle.json for a friendly save."""
        safe = _safe_campaign_id(campaign_id)
        if not safe:
            self._send(404, b"no such campaign", "text/plain; charset=utf-8")
            return
        snap = _campaign_dir(safe) / "snapshot.json"
        try:
            data = snap.read_bytes()
        except OSError:
            self._send(404, b"no snapshot for campaign", "text/plain; charset=utf-8")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe}-chronicle.json"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._resolve_campaign()  # lazily attach if we launched before a game existed
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            # The root used to serve the pre-OpenWorlds raw-DOM dashboard (viewer/index.html).
            # OpenWorlds (/openworlds/) is the real, current UI the desktop app loads, so the
            # root now redirects there — hitting 127.0.0.1:<port>/ in a browser shows the same
            # app the native shell does, never the retired raw-DOM MVP.
            self._redirect(f"{_OPENWORLDS_ROUTE}/")
        elif route in ("/legacy", "/legacy.html"):
            self._redirect(f"{_OPENWORLDS_ROUTE}/")
        elif route in ("/dashboard", "/dashboard.html"):
            html = (_HERE / "dashboard.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/openworlds/config.json":
            self._json(_openworlds_config())
        elif route in ("/app-status", "/__worldos/app-status.json"):
            qs = parse_qs(parsed.query)
            viewed = self._live_play_view_campaign(qs)
            self._json(_app_status_payload(
                port=int(self.server.server_address[1]),
                attached_campaign_id=self.campaign_id,
                viewed_campaign_id=viewed,
                transcript_path=self.transcript_path,
                chat_path=self.chat_path,
            ))
        elif route == "/openworlds/campaigns.json":
            self._json(_openworlds_campaigns(self.campaign_id, move_sink_live=_live_play()))
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
            cid = self._live_play_view_campaign(qs)
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
            # Symmetric heal (#640): mirror /session-surface — re-align to the live run instead of
            # latching is_live_view=False (the read-only lockout) when the client's ?campaign drifts.
            cid = self._live_play_view_campaign(qs)
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
            # Symmetric heal (#640): mirror /session-surface — re-align to the live run instead of
            # latching is_live_view=False (the read-only lockout) when the client's ?campaign drifts.
            cid = self._live_play_view_campaign(qs)
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
        elif route == "/surface-stream":
            # GRAPHICS #455 — SSE push transport. An ADDITIVE Server-Sent-Events mirror of the
            # render surfaces (atlas + combat + character) behind IDENTICAL payload shapes, so a
            # graphical client can react to engine changes without 3s polling. Polling stays the
            # canonical fallback (the GET surfaces are untouched); this only ADDS a channel. The
            # engine remains sole writer — this reader pushes the same build_*_surface output.
            self._serve_surface_stream(parse_qs(parsed.query))
        elif route == "/ugc/profiles":
            # GRAPHICS #453/#442 — list stored UGC games (read-only). Presentation artifacts,
            # not game state; the engine remains sole writer of state.
            self._json({"profiles": ugc_store.list_profiles(_ugc_root())})
        elif route == "/ugc/profile":
            # GRAPHICS #453/#442 — fetch a stored UGC render-profile (latest, or ?version=N).
            qs = parse_qs(parsed.query)
            game_id = _text((qs.get("game_id") or [""])[0], "")
            owner = _text((qs.get("owner") or ["local"])[0], "local")
            ver_raw = (qs.get("version") or [""])[0]
            try:
                version = int(ver_raw) if ver_raw else None
            except (TypeError, ValueError):
                version = None
            if not game_id:
                self._json({"ok": False, "reason": "game_id required"})
                return
            prof = ugc_store.load_profile(_ugc_root(), game_id, owner=owner, version=version)
            if prof is None:
                self._send(404, b'{"ok":false,"reason":"profile not found"}', "application/json")
                return
            self._json(prof)
        elif route == "/journal-surface":
            # The quest journal read model: tracked quests + unresolved hooks (as rumors)
            # + the Campaign Director's top structural debts (#72) as a GM advisory.
            self._serve_simple_surface(parse_qs(parsed.query), build_journal_surface)
        elif route == "/seed-surface":
            # The World-Seed read model (#266): live seed identity (de-faking the hardcoded
            # StatLine), the params each control binds to, the free/gated/locked mutability
            # matrix, and session_started. Empty snapshot → honest empty-state (present:false).
            self._serve_simple_surface(parse_qs(parsed.query), build_seed_surface)
        elif route == "/acts-surface":
            # Read-only act/chronicle payoff surface. It shows compiled path state when the
            # engine has one and otherwise says the act tracker is not wired for this save yet.
            self._serve_simple_surface(parse_qs(parsed.query), build_acts_surface)
        elif route == "/character-surface":
            # The party's full character sheets (classes/skills/spells/resources/AC/death
            # saves) projected from the engine snapshot into the heroes screen shape.
            self._serve_simple_surface(parse_qs(parsed.query), build_character_surface, heal=True)
        elif route == "/inventory-surface":
            # Each party member's pack (name/qty/type/glyph/equipped) + currency.
            self._serve_simple_surface(parse_qs(parsed.query), build_inventory_surface)
        elif route == "/relations-surface":
            # Factions (reputation/tags) + met NPCs/companions (attitude, dossier facts)
            # + companion personal-quest arcs.
            self._serve_simple_surface(parse_qs(parsed.query), build_relations_surface)
        elif route == "/parley-surface":
            # Sheet-correct social options for the lead PC (per-skill modifier + suggested
            # DC + alignment + free_form) — the UI side of #141. ?difficulty tunes the DC band.
            qs = parse_qs(parsed.query)
            difficulty = _text((qs.get("difficulty") or [""])[0], "medium")
            self._serve_simple_surface(
                qs,
                lambda snap, **kw: build_parley_surface(snap, difficulty=difficulty, **kw),
                heal=True,
            )
        elif route == _OPENWORLDS_ROUTE:
            suffix = f"?{parsed.query}" if parsed.query else ""
            self._redirect(f"{_OPENWORLDS_ROUTE}/{suffix}")
        elif route.startswith(f"{_OPENWORLDS_ROUTE}/"):
            asset = _openworlds_asset(route)
            if asset is None:
                self._send(404, b"not found", "text/plain")
            elif asset.name == "index.html" and asset.parent == _OPENWORLDS_DIR:
                self._send(200, _openworlds_index_bytes(asset), _openworlds_mime(asset))
            else:
                self._send(200, asset.read_bytes(), _openworlds_mime(asset))
        elif route == "/campaigns":
            # Read-only list for the topbar switcher (#H3): every projectable campaign,
            # newest-active first, with the attached one marked `current`. Lets the
            # dashboard offer a picker instead of silently auto-following recency.
            self._json({"campaigns": _list_campaigns()})
        elif route == "/export":
            # ST-03 export-chronicle: serve a campaign's snapshot.json verbatim as a
            # download. Pure reader — the snapshot is the engine-owned campaign aggregate;
            # we stream its on-disk bytes (preserving the engine's exact serialization) and
            # never parse/rewrite it. A PRESENT-but-invalid ?campaign 404s (so a bad id is an
            # honest miss, not a silent fall-through to a different chronicle); an ABSENT
            # ?campaign falls back to the per-request view override / attached campaign.
            qs = parse_qs(parsed.query)
            requested = (qs.get("campaign") or [""])[0]
            cid = _safe_campaign_id(requested) if requested else self._view_campaign(qs)
            self._serve_export(cid or "")
        elif route == "/build-options":
            # Read-only progression planner bridge: path-safe campaign scope +
            # character id, then engine.build_options. It returns disabled/error
            # data for the dashboard to render and never exposes level_up.
            qs = parse_qs(parsed.query)
            cid = (qs.get("campaign") or [""])[0] or self._view_campaign(qs)
            character_id = (qs.get("character") or [""])[0]
            self._json(build_options_response(cid, character_id))
        elif route == "/bestiary-surface":
            # Read-only player-safe bestiary/codex projection. No campaign or combat
            # mutation route is exposed here; the engine returns only public preview fields.
            # The campaign scope (resolved the same way /build-options does) lets the engine
            # gate the stat reveal by the party's earned intel tier (#263); no campaign keeps
            # it the honest global preview.
            qs = parse_qs(parsed.query)
            query = (qs.get("q") or qs.get("query") or [""])[0]
            cid = (qs.get("campaign") or [""])[0] or self._view_campaign(qs)
            raw_limit = (qs.get("limit") or ["20"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 20
            # ?reference=1 -> "Browse all": bypass campaign intel and return the global SRD
            # preview so the codex is useful before the party has slain anything (the #263
            # intel codex is perpetually fog-of-war in real play). Truthy: 1/true/yes/on.
            reference = (qs.get("reference") or [""])[0].strip().lower() in ("1", "true", "yes", "on")
            self._json(build_bestiary_response(query, limit, cid, reference=reference))
        elif route == "/roster-surface":
            # Read-only canon-NPC PICKER projection (the "reverse character creator"): the
            # PLAYABLE roster (origins excluded by the record `playable` flag), filtered by
            # ?race / ?class / ?level, plus the distinct race/class/level facets for the filter
            # chips. The campaign scope (resolved like /bestiary-surface) only picks which world's
            # roster to browse — no campaign falls back to the shipped default world (the new-game
            # path). Exposes NO write/seat route; the bind is the native startProviderSession
            # bridge / load_canon_character, never here.
            qs = parse_qs(parsed.query)
            cid = (qs.get("campaign") or [""])[0] or self._view_campaign(qs)
            catalog_ref = _session_surface_catalog_ref(qs)
            race = (qs.get("race") or [""])[0]
            char_class = (qs.get("class") or qs.get("char_class") or [""])[0]
            level = (qs.get("level") or [""])[0]
            # Cap the returned cards so the picker grid stays renderable (the unfiltered playable
            # roster is ~2,000). ?limit tunes it; bounded to [1, 500] (a bad value -> the default).
            try:
                limit = int((qs.get("limit") or ["120"])[0])
            except (TypeError, ValueError):
                limit = 120
            limit = max(1, min(500, limit))
            if catalog_ref is not None:
                cid, raw_snap, _campaign_dir, _root_is_current = catalog_ref
                raw_world_id = raw_snap.get("world_id") if isinstance(raw_snap, dict) else None
                world_id = (
                    raw_world_id.strip()
                    if isinstance(raw_world_id, str) and raw_world_id.strip()
                    else None
                )
                self._json(build_roster_response(cid, race, char_class, level, limit, world_id=world_id))
                return
            self._json(build_roster_response(cid, race, char_class, level, limit))
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
            view_cid = self._view_campaign(qs)
            entries, nxt = _read_events(view_cid, since)
            # BUG2: include the resolved session id so the client composes a globally-unique
            # `${sid}:${seq}` dedup/order key — a bare per-session line index collides across a
            # session rotation (cold-open + DM-turn-retry re-mint), suppressing the new session's
            # post-move narration (seq 0,1,2 already claimed by the prior session's cold-open).
            self._json({"entries": entries, "next": nxt, "sid": _active_session_id(view_cid)})
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
        if route in ("/save-slot", "/load-slot"):
            self._do_slot("save" if route == "/save-slot" else "load")
            return
        if route == "/seed-param":
            self._do_seed_param()
            return
        if route == "/portrait-gen":
            self._do_portrait_gen()
            return
        if route == "/ugc/profile":
            self._do_ugc_save()
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

    def _do_ugc_save(self) -> None:
        """POST /ugc/profile — the UGC save-INTENT (#453/#442). Mirrors the /move discipline: a
        client never writes the store directly; it submits a render-profile and the SERVER decides.
        The submission is VALIDATED + GATED against the FROZEN contract (schema + required gates);
        only an accepted profile is persisted, as a new append-only version (ownership/history
        intact). A render-profile is presentation, not game state, so this does not touch the
        engine's sole-writership. Body: {profile: <render-profile>, owner?: <str>}."""
        payload = self._read_post_json()
        if payload is ... or not isinstance(payload, dict):
            self._json({"ok": False, "reason": "bad UGC payload"})
            return
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            self._json({"ok": False, "reason": "missing 'profile' object"})
            return
        owner = _text(payload.get("owner") or "local", "local")
        try:
            result = ugc_store.save_profile(_ugc_root(), profile, owner=owner)
        except ValueError as exc:
            self._json({"ok": False, "reason": f"rejected: {exc}"})
            return
        except OSError as exc:
            self._json({"ok": False, "reason": f"store write failed: {exc}"})
            return
        if not result["accepted"]:
            # rejected by the gate -> nothing persisted; surface why + the human-gate queue.
            failed = {k: v["detail"] for k, v in result["report"]["gates"].items()
                      if v.get("required") and v.get("passed") is False}
            self._json({"ok": False, "reason": "rejected by contract gate", "failed_gates": failed,
                        "human_gate_queue": result["report"]["human_gate_queue"]})
            return
        self._json({"ok": True, "owner": result["owner"], "game_id": result["game_id"],
                    "version": result["version"],
                    "human_gate_queue": result["report"]["human_gate_queue"]})

    def _do_seed_param(self) -> None:
        """POST /seed-param — the World-Seed write lane (#266). Mirrors /move's intent bridge
        EXACTLY: the viewer NEVER writes campaign state; it appends a single validated
        ``{kind:"set_seed_param", param, value[, force]}`` intent line to $CLAWDND_PLAYER_MOVES,
        which the live DM/engine session drains and applies via the engine's set_seed_param
        tool (the engine stays the SOLE WRITER). Read-only (refuses) when there is no live
        game; refuses a write tagged for a non-live (merely viewed) campaign (#49)."""
        dest = _moves_path()
        if dest is None:
            self._json({"ok": False, "reason": "read-only (no live game)"})
            return
        payload = self._read_post_json()
        if payload is ...:
            self._json({"ok": False, "reason": "bad seed-param payload"})
            return
        if isinstance(payload, dict):
            viewed = payload.get("campaign")
            if viewed and self.campaign_id and viewed != self.campaign_id:
                self._json({"ok": False, "reason": "viewing a non-live campaign — switch to the live run to change the seed"})
                return
        intent, why = sanitize_seed_param(payload)
        if intent is None:
            self._json({"ok": False, "reason": why})
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(intent, separators=(",", ":")) + "\n")
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

    def _do_portrait_gen(self) -> None:
        """POST /portrait-gen — opt-in "Generate a unique face" for a player-created PC (#265).

        Reads {race, class, name?, appearance?, alignment?, seed?} and shells the engine's
        imagegen layer to generate a portrait into a provisional content-scope (returned as
        ``scope``). Always 200 with a JSON verdict (face-or-placeholder); never hangs (bounded
        subprocess on a threaded server) and never errors the page. On a normal box with no
        image provider configured the verdict is a placeholder with NO network — the wizard
        then keeps the player's selected gallery face. This is a DERIVED-cache write only (the
        engine remains the sole writer of campaign state; snapshot.json is never touched)."""
        payload = self._read_post_json()
        if payload is ... or not isinstance(payload, dict):
            self._json({"ok": False, "reason": "bad portrait-gen payload"})
            return
        self._json(_portrait_gen(payload))

    def _do_slot(self, action: str) -> None:
        """POST /save-slot | /load-slot — quicksave / quickload of the live campaign.

        Reads {"campaign", "slot"?} and delegates to the engine-owned save_slot/load_slot tool
        via the in-process bridge (the engine is the sole writer). Always 200 with a JSON verdict
        ({"ok": ...}); never writes campaign state in the viewer. A load OVERWRITES live state, so
        the UI gates it behind a confirm before reaching here. The id is path-validated downstream."""
        payload = self._read_post_json()
        if payload is ... or not isinstance(payload, dict):
            self._json({"ok": False, "reason": "bad save/load payload"})
            return
        campaign_id = payload.get("campaign")
        # Bind the write to the LIVE campaign exactly as /move does: a save/load tagged for a
        # campaign other than the attached live run is refused, never misrouted (#49).
        if campaign_id and self.campaign_id and campaign_id != self.campaign_id:
            self._json({"ok": False, "reason": "viewing a non-live campaign — switch to the live run to save/load"})
            return
        self._json(_save_load_slot_response(action, campaign_id, payload.get("slot")))

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
    _Handler.transcript_path = env_var("VIEWER_TRANSCRIPT") or (
        sys.argv[3] if len(sys.argv) > 3 else ""
    )
    # Optional two-sided conversation log to show the player+DM exchange in the chat.
    _Handler.chat_path = env_var("VIEWER_CHAT") or ""
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    if campaign_id:
        print(f"WorldOS play-view: http://127.0.0.1:{port}  (campaign: {campaign_id})")
    else:
        print(f"WorldOS play-view: http://127.0.0.1:{port}  (no game yet — the view attaches automatically once one starts)")
    if _Handler.transcript_path:
        print(f"Watching agent transcript: {_Handler.transcript_path}")
    moves = _moves_path()
    print(
        f"Player moves → appending to: {moves}"
        if moves
        else "Player moves: DISABLED (set CLAWDND_PLAYER_MOVES to enable POST /move)."
    )
    tts = env_var("TTS_BACKEND", "kokoro")
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
