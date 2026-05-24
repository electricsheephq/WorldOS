"""Load a bundled adventure module into a fresh Campaign.

An adventure module is content/campaigns/<id>/adventure.json (authored, CC-BY).
seed_campaign() turns its declarative data (locations, NPCs, hook) into live
engine state: NPCs become voiced Characters, locations populate the map, and the
hook becomes the opening quest. The DM skill then reads the scenes and runs play.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import worldsim
from models import Campaign, Character, Faction, Location, Quest


def _content_dir() -> Path:
    raw = os.environ.get("CLAWDND_CONTENT_DIR")
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "content"


def _characters_dirs(world_id: str) -> list[Path]:
    """Where ingested canon characters live: content/worlds/<id>/characters/ and its
    gitignored _private/ mirror (for locally-cached records)."""
    base = _content_dir() / "worlds"
    return [base / world_id / "characters", base / "_private" / world_id / "characters"]


def is_playable(rec: dict) -> bool:
    """Whether a canon record may be picked up as the PLAYER. Top heroes (the BG3
    origin companions) are marked `"playable": false` so they stay legends/quest-givers,
    never a hero the player embodies. Absent flag = playable (a minor figure)."""
    return bool(rec.get("playable", True))


def list_canon_characters(world_id: str, playable_only: bool = False) -> list[dict]:
    """The ingested canon characters available for a world — {name, race, class,
    playable, role} each — from content/worlds/<id>/characters/*.json. De-duplicated by
    name (a figure on two wikis collapses to one). `playable_only` keeps just the minor
    figures the player may pick up as their PC (top heroes are filtered out)."""
    out: list[dict] = []
    seen: set[str] = set()
    for cdir in _characters_dirs(world_id):
        if not cdir.is_dir():
            continue
        for p in sorted(cdir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            nm = (rec.get("name") or p.stem).strip()
            if nm.lower() in seen:
                continue
            playable = is_playable(rec)
            if playable_only and not playable:
                continue
            seen.add(nm.lower())
            out.append({
                "name": nm,
                "race": rec.get("race", ""),
                "class": rec.get("class", ""),
                "playable": playable,
                "role": rec.get("role", ""),
            })
    return out


def load_canon_character(world_id: str, name: str) -> "dict | None":
    """Load one ingested canon character record by name (or file slug), or None."""
    want = name.strip().lower()
    for cdir in _characters_dirs(world_id):
        if not cdir.is_dir():
            continue
        for p in sorted(cdir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if (rec.get("name", "").strip().lower() == want) or (p.stem.lower() == want):
                return rec
    return None


def load_adventure_data(adventure_id: str) -> dict:
    path = _content_dir() / "campaigns" / adventure_id / "adventure.json"
    if not path.exists():
        raise ValueError(f"no adventure named {adventure_id!r} (looked at {path})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"adventure {adventure_id!r} has malformed JSON: {exc}") from exc


def _as_list(adv: dict, key: str) -> list:
    val = adv.get(key, [])
    if not isinstance(val, list):
        raise ValueError(f"malformed adventure: '{key}' must be a list, got {type(val).__name__}")
    return val


def _dedupe_strs(xs) -> list[str]:
    """Order-preserving de-duplication of a string iterable (case-sensitive on the id/
    name value as stored). Used when area connection-name resolution can collapse two
    distinct names onto the same location id."""
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _as_list_lenient(rec: dict, key: str) -> list:
    """Tolerant list-getter for OPTIONAL, externally-authored overlay fields (B-LOW-2).

    Unlike `_as_list` (which is strict so a malformed *adventure* fails loudly at seed),
    an ending overlay is a small, hand-edited add-on: a field that's present-but-not-a-
    list should DEGRADE, not crash the whole start_world. Missing -> []; a list -> as-is;
    any other scalar -> a single-element list (matching the tolerant `.get()`/`fates`-
    non-dict handling elsewhere in `_apply_ending_overlay`)."""
    val = rec.get(key, [])
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def seed_campaign(adv: dict) -> Campaign:
    """Build a Campaign from an adventure dict. Tolerant of optional fields, but
    rejects malformed shapes and duplicate ids rather than silently dropping data."""
    if not isinstance(adv, dict):
        raise ValueError("adventure data must be a JSON object")
    c = Campaign(title=adv.get("title", "Untitled Adventure"), summary=adv.get("premise", ""))
    # An authored adventure may declare the WORLD it's set in, so the DM can lookup_lore
    # that world's corpus + honor its era while running the module.
    c.world_id = str(adv.get("world_id", ""))
    c.era = str(adv.get("era", ""))

    # Persist the authored scenes verbatim so the DM can read them at play time via
    # get_scene (read_aloud prose, dm_notes staging beats, check DCs). Without this
    # the rich per-scene authoring is dropped at seed and the DM plays blind.
    scenes = adv.get("scenes", [])
    if isinstance(scenes, list):
        c.scenes = [s for s in scenes if isinstance(s, dict)]

    first_loc = None
    for loc in _as_list(adv, "locations"):
        location = Location(
            name=loc.get("name", "?"),
            description=loc.get("description", ""),
            connections=loc.get("connections", []),
            hex=loc.get("hex"),  # optional axial coords (presentation only)
        )
        if loc.get("id"):
            if loc["id"] in c.locations:
                raise ValueError(f"duplicate location id {loc['id']!r} in adventure")
            location.id = loc["id"]
        c.locations[location.id] = location
        if first_loc is None:
            first_loc = location.id
    c.current_location_id = first_loc
    if first_loc is not None:
        c.locations[first_loc].visited = True  # the party starts here
    # Render as a hex map if the adventure declares it or any location has coords.
    c.map_kind = adv.get("map_kind") or (
        "hex" if any(l.hex for l in c.locations.values()) else "none"
    )

    for npc in _as_list(adv, "npcs"):
        data = {
            "name": npc.get("name", "NPC"),
            "kind": "npc",
            "voice_id": npc.get("voice_id", "npc-male-1"),
            "personality": npc.get("personality", ""),
            "attitude": npc.get("attitude", ""),
        }
        # Optional combat stats: a fightable NPC (a villain, a guard) is seeded
        # battle-ready so the DM uses THIS record in combat rather than spawning a
        # duplicate monster — which is what left two records of the same character.
        for k in (
            "max_hp", "armor_class", "hit_dice", "proficiency_bonus", "abilities",
            "damage_resistances", "damage_immunities", "damage_vulnerabilities",
            "condition_immunities",
        ):
            if k in npc:
                data[k] = npc[k]
        ch = Character(**data)
        if "max_hp" in npc:
            ch.current_hp = ch.max_hp  # a stat-blocked NPC starts at full health
        if npc.get("id"):
            if npc["id"] in c.characters:
                raise ValueError(f"duplicate npc id {npc['id']!r} in adventure")
            ch.id = npc["id"]
        c.characters[ch.id] = ch

    # Companions are full party members (their own sheet + voice), seeded into
    # the party so the player starts the adventure WITH a companion at their side.
    # The companion dict mirrors Character's fields (Pydantic coerces the nested
    # abilities / classes / spell_slots); unknown keys are rejected.
    for comp in _as_list(adv, "companions"):
        data = dict(comp)
        comp_id = data.pop("id", None)
        data["kind"] = "companion"
        data.setdefault("voice_id", "companion-default")
        ch = Character(**data)
        if comp_id:
            if comp_id in c.characters:
                raise ValueError(f"duplicate character id {comp_id!r} in adventure")
            ch.id = comp_id
        ch.current_hp = ch.max_hp  # a fresh companion joins at full health
        if not ch.hit_dice_remaining:
            ch.hit_dice_remaining = ch.total_level
        c.characters[ch.id] = ch
        c.party.append(ch.id)

    for fac in _as_list(adv, "factions"):
        faction = Faction(
            name=fac.get("name", "Faction"),
            description=fac.get("description", ""),
            reputation=int(fac.get("reputation", 0)),
        )
        if fac.get("id"):
            faction.id = fac["id"]
        c.factions[faction.id] = faction

    if adv.get("hook"):
        quest = Quest(title=adv.get("title", "Adventure"), description=adv["hook"])
        c.quests[quest.id] = quest

    return c


def load_world_data(world_id: str) -> dict:
    """Load a world-seed bible: content/worlds/<id>/world.json, falling back to the
    gitignored content/worlds/_private/<id>/ for personal/internal seeds (e.g. a
    Forgotten-Realms/post-BG3 world the owner uses privately). Same loader either way."""
    base = _content_dir() / "worlds"
    path = base / world_id / "world.json"
    if not path.exists():
        private = base / "_private" / world_id / "world.json"
        if not private.exists():
            raise ValueError(f"no world named {world_id!r} (looked at {path} and {private})")
        path = private
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"world {world_id!r} has malformed JSON: {exc}") from exc


def _areas_dirs(world_id: str) -> list[Path]:
    """Where ingested navigable AREAS live: content/worlds/<id>/areas/ and its gitignored
    _private/ mirror (for locally-cached records). Each *.json is a Location-shaped record
    produced by tools/ingest/wiki_to_areas.py."""
    base = _content_dir() / "worlds"
    return [base / world_id / "areas", base / "_private" / world_id / "areas"]


def load_world_areas(world_id: str) -> list[dict]:
    """The ingested navigable areas a world ships — content/worlds/<id>/areas/*.json — as
    a list of Location-shaped dicts (name, description, region, connections, tags, +
    source_url/license/attribution). De-duplicated by name (a place on two wikis collapses
    to one). Returns an EMPTY list if no areas/ dir exists, so a world without ingested
    areas reproduces today's seed behavior exactly. Malformed/unreadable files are skipped."""
    out: list[dict] = []
    seen: set[str] = set()
    for adir in _areas_dirs(world_id):
        if not adir.is_dir():
            continue
        for p in sorted(adir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(rec, dict):
                continue
            nm = (rec.get("name") or p.stem).strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            out.append(rec)
    return out


def list_worlds() -> list[dict]:
    """Available world seeds — every content/worlds/<id>/world.json (including the
    gitignored _private/ ones), as {id, name, premise, era, tone, lore_pages}. Powers
    /world-list and the start_world discovery flow."""
    base = _content_dir() / "worlds"
    out: list[dict] = []
    if not base.is_dir():
        return out
    seen: set[str] = set()
    for wj in sorted(base.rglob("world.json")):
        wid = wj.parent.name
        if wid in seen:
            continue
        try:
            w = json.loads(wj.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        seen.add(wid)
        lore = wj.parent / "lore"
        out.append({
            "id": w.get("id", wid),
            "name": w.get("name", wid),
            "premise": (w.get("premise", "") or "")[:240],
            "era": w.get("era", ""),
            "tone": (w.get("tone", "") or "")[:140],
            "lore_pages": len(list(lore.rglob("*.md"))) if lore.is_dir() else 0,
        })
    return out


def _origins_dirs(world_id: str) -> list[Path]:
    """Where premade PC origin TEMPLATES live: content/worlds/<id>/origins/ and its
    gitignored _private/ mirror. A template is a ready-to-play character build the
    player can pick as their PC at world start (start_character origin='template:<id>')."""
    base = _content_dir() / "worlds"
    return [base / world_id / "origins", base / "_private" / world_id / "origins"]


def list_origin_templates(world_id: str) -> list[dict]:
    """The premade PC origin templates a world ships (content/worlds/<id>/origins/*.json),
    as {id, name, class, level, blurb} each. De-duplicated by id."""
    out: list[dict] = []
    seen: set[str] = set()
    for odir in _origins_dirs(world_id):
        if not odir.is_dir():
            continue
        for p in sorted(odir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            oid = (rec.get("id") or p.stem).strip()
            if oid.lower() in seen:
                continue
            seen.add(oid.lower())
            out.append({
                "id": oid,
                "name": rec.get("name", oid),
                "class": rec.get("class_name", "") or rec.get("class", ""),
                "level": rec.get("level", 1),
                "blurb": rec.get("blurb", ""),
            })
    return out


def load_origin_template(world_id: str, template_id: str) -> "dict | None":
    """Load one premade PC origin template by id (or file slug) for a world, or None."""
    want = template_id.strip().lower()
    for odir in _origins_dirs(world_id):
        if not odir.is_dir():
            continue
        for p in sorted(odir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if (str(rec.get("id", "")).strip().lower() == want) or (p.stem.lower() == want):
                return rec
    return None


def _endings_dirs(world_id: str) -> list[Path]:
    """Where post-state ending OVERLAYS live: content/worlds/<id>/endings/ and its
    gitignored _private/ mirror. An overlay rewrites a base world into a specific
    post-campaign state (e.g. post-BG3 branch outcomes)."""
    base = _content_dir() / "worlds"
    return [base / world_id / "endings", base / "_private" / world_id / "endings"]


def list_endings(world_id: str) -> list[dict]:
    """The post-state ending overlays a world ships (content/worlds/<id>/endings/*.json),
    as {id, name} each. These let start_world seed the setting in a chosen aftermath
    rather than its default state. De-duplicated by id."""
    out: list[dict] = []
    seen: set[str] = set()
    for edir in _endings_dirs(world_id):
        if not edir.is_dir():
            continue
        for p in sorted(edir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            eid = (rec.get("id") or p.stem).strip()
            if eid.lower() in seen:
                continue
            seen.add(eid.lower())
            out.append({"id": eid, "name": rec.get("name", eid)})
    return out


def load_ending_data(world_id: str, ending_id: str) -> "dict | None":
    """Load one ending overlay by id (or file slug) for a world, or None if not found."""
    want = ending_id.strip().lower()
    for edir in _endings_dirs(world_id):
        if not edir.is_dir():
            continue
        for p in sorted(edir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if (str(rec.get("id", "")).strip().lower() == want) or (p.stem.lower() == want):
                return rec
    return None


def _apply_ending_overlay(c: Campaign, overlay: dict) -> None:
    """Fold a post-state ending overlay onto an already-base-seeded Campaign (mutates).

    OVERWRITES `era` (the chronology guardrail moves to the post-state). The post-state
    is mutually exclusive with the base facts it changes, so the overlay also RETRACTS
    the base facts it supersedes (B-HIGH-1): an optional ``supersedes`` list of
    case-insensitive substrings drops any base `history`/`standing_thread` line carrying
    a match BEFORE the overlay's own `history_append` + `standing_threads` are folded
    into recallable `lore`. The world-sim is then re-seeded ONCE from the MERGED set
    (surviving base threads + overlay threads) — never base-then-overlay — so no retired
    thread keeps ticking and there are no duplicate `thread_id`s (B-LOW-1). Each `fates`
    entry lands as a memory fact on the matching npc_roster Character — plus a lore line
    so a hero who isn't in the roster is still covered. Premise gets the suffix appended.
    (The overlay's story_seeds_append are surfaced by start_world, not here.)"""
    # The post-state chronology REPLACES the base era — who's alive / what happened changed.
    new_era = str(overlay.get("era") or "").strip()
    if new_era:
        c.era = new_era

    suffix = str(overlay.get("premise_suffix") or "").strip()
    if suffix:
        c.summary = (c.summary + " " + suffix).strip() if c.summary else suffix

    # Which base facts this post-state RETRACTS: case-insensitive substrings. A base
    # lore line (history or standing thread) carrying any of these is mutually exclusive
    # with the overlay and is dropped so `recall` never returns both (B-HIGH-1).
    supersedes = [s.lower() for s in (_as_list_lenient(overlay, "supersedes")) if str(s).strip()]

    def _superseded(text: str) -> bool:
        low = str(text).lower()
        return any(sub in low for sub in supersedes)

    # The base standing-thread texts are exactly the world-beats seed_threads already
    # scheduled (text == the thread). Capture the SURVIVING base threads (not retracted),
    # then clear ALL base thread-beats — we reseed once from the merged set below so no
    # retired thread keeps ticking and ids don't collide with the overlay's (B-LOW-1).
    surviving_base_threads = [
        cq.text for cq in c.consequences
        if cq.thread_id and str(cq.text).strip() and not _superseded(cq.text)
    ]
    c.consequences = [cq for cq in c.consequences if not cq.thread_id]

    # Drop superseded base facts from recallable lore, then fold the overlay's own
    # history + standing threads in. (Base lore was history + standing_threads; the
    # superseded base threads are dropped here too, in lockstep with the beats above.)
    if supersedes:
        c.lore = [l for l in c.lore if not _superseded(l)]
    extra_lore = [
        str(x) for x in (
            _as_list_lenient(overlay, "history_append") + _as_list_lenient(overlay, "standing_threads")
        ) if str(x).strip()
    ]
    if extra_lore:
        c.lore = list(c.lore) + extra_lore

    # Re-seed the world-sim ONCE from the MERGED set: the base threads that SURVIVED the
    # retraction, followed by the overlay's post-state threads. This is the only call
    # that schedules thread-beats when an ending is active, so each thread has exactly
    # one record with a unique id and the world never ticks a thread the post-state retired.
    overlay_threads = [str(t) for t in _as_list_lenient(overlay, "standing_threads") if str(t).strip()]
    merged_threads = surviving_base_threads + overlay_threads
    if merged_threads:
        worldsim.seed_threads(c, merged_threads)

    # Each fate lands on the matching roster NPC as a memory fact (so the DM voices the
    # right post-state when they appear). Roster characters are keyed by id or name; a
    # hero who ISN'T in the roster (e.g. Gale, only in lore) is covered by a lore line.
    fates = overlay.get("fates") or {}
    if isinstance(fates, dict):
        for key, fate in fates.items():
            if not isinstance(fate, dict):
                continue
            who = str(key)
            parts = [p for p in (
                fate.get("status"), fate.get("where"), fate.get("note"),
            ) if str(p or "").strip()]
            detail = " — ".join(str(p).strip() for p in parts)
            # Resolve the roster character by id first, then by case-insensitive name.
            ch = c.characters.get(who)
            if ch is None:
                kl = who.strip().lower()
                ch = next(
                    (x for x in c.characters.values() if x.name.strip().lower() == kl),
                    None,
                )
            label = ch.name if ch is not None else who
            fact = f"[{overlay.get('name', overlay.get('id', 'ending'))}] {label}: {detail}".strip(" :—")
            if ch is not None:
                ch.memory.append(fact)
            # Always add a lore line too, so non-roster heroes are recallable as well.
            c.lore.append(fact)


def seed_world(world: dict, start_at: str = "", ending: str = "") -> Campaign:
    """Seed a Campaign from a WORLD bible (a persistent setting the DM generates
    *within*, not a fixed plot). Unlike an adventure, a world ships its regions,
    factions, a roster of pullable NPCs, and its history/standing-threads as `lore`
    — which the ledger indexes so `recall` keeps the generated story consistent. The
    DM then drops the party at a starting region and generates + persists the actual
    adventure as the player explores.

    `ending` selects a post-state OVERLAY (content/worlds/<id>/endings/<ending>.json):
    after the base seed, the overlay OVERWRITES the era, appends its history + standing
    threads into recallable lore (and ticks them in the world-sim), appends story_seeds,
    and lands each `fates` entry on the matching roster NPC. `ending="random"` picks one
    of the world's overlays at random; an unknown/empty `ending` leaves the BASE world
    state untouched (today's behavior). The resolved id is stored on `Campaign.ending_id`."""
    if not isinstance(world, dict):
        raise ValueError("world data must be a JSON object")
    c = Campaign(title=world.get("name", "Untitled World"), summary=world.get("premise", ""))
    c.world_id = str(world.get("id", ""))  # enables lookup_lore over this world's corpus
    c.era = str(world.get("era") or world.get("current_year") or "")  # chronology guardrail

    first_loc = None
    for reg in _as_list(world, "regions"):
        location = Location(
            name=reg.get("name", "?"),
            description=reg.get("description", ""),
            connections=reg.get("connections", []),
            notes=" ".join(reg.get("tags", [])),
            hex=reg.get("hex"),
        )
        if reg.get("id"):
            if reg["id"] in c.locations:
                raise ValueError(f"duplicate region id {reg['id']!r} in world")
            location.id = reg["id"]
        c.locations[location.id] = location
        if first_loc is None:
            first_loc = location.id

    # ADDITIVE: also seed any ingested areas/ records as navigable Locations (S4). A
    # world with no areas/ dir gets an empty list, so this is a no-op and the default
    # path reproduces today's behavior EXACTLY. Areas are deduped against the regions
    # already seeded above (by id AND by case-insensitive name) so an ingested page that
    # overlaps an authored region never double-seeds; connection NAMES are resolved to
    # location ids by name where possible, and left as hints (the Location model accepts
    # free-form strings in `connections`) where they don't match a seeded place.
    seeded_names = {loc.name.strip().lower() for loc in c.locations.values()}
    new_area_ids: list[str] = []
    for area in load_world_areas(c.world_id):
        name = str(area.get("name", "")).strip()
        if not name or name.lower() in seeded_names:
            continue  # never double-seed a region the world already declares
        aid = str(area.get("id", "")).strip()
        if aid and aid in c.locations:
            continue  # id collision with a seeded region — skip rather than clobber
        location = Location(
            name=name,
            description=str(area.get("description", "")),
            region=str(area.get("region", "")),
            notes=" ".join(str(t) for t in (area.get("tags") or []) if str(t).strip()),
            connections=[str(x) for x in (area.get("connections") or []) if str(x).strip()],
        )
        if aid:
            location.id = aid
        c.locations[location.id] = location
        seeded_names.add(name.lower())
        new_area_ids.append(location.id)

    # Resolve the freshly-seeded areas' connection NAMES to location ids where a seeded
    # place matches by name (case-insensitive); unmatched names stay verbatim as hints.
    # Only the new areas are rewritten — regions already carry id-based connections.
    if new_area_ids:
        name_to_id = {loc.name.strip().lower(): lid for lid, loc in c.locations.items()}
        for aid in new_area_ids:
            loc = c.locations[aid]
            loc.connections = _dedupe_strs(
                name_to_id.get(conn.strip().lower(), conn) for conn in loc.connections
            )

    # Drop the party at the requested start, else the world's first starting_option,
    # else the first region.
    starts = [s.get("location_id") for s in _as_list(world, "starting_options") if s.get("location_id")]
    start_id = start_at or (starts[0] if starts else first_loc)
    if start_id and start_id not in c.locations:
        raise ValueError(f"start location {start_id!r} is not a region of this world")
    c.current_location_id = start_id
    if start_id:
        c.locations[start_id].visited = True
    c.map_kind = world.get("map_kind") or ("hex" if any(l.hex for l in c.locations.values()) else "none")

    for fac in _as_list(world, "factions"):
        faction = Faction(
            name=fac.get("name", "Faction"),
            description=fac.get("description", ""),
            reputation=int(fac.get("reputation", 0)),
        )
        if fac.get("id"):
            faction.id = fac["id"]
        c.factions[faction.id] = faction

    # Roster NPCs exist in state (recallable, voiced) but are not party members — the
    # DM pulls them in or invents freely. Each NPC's hook is stored as a memory fact.
    for npc in _as_list(world, "npc_roster"):
        ch = Character(
            name=npc.get("name", "NPC"),
            kind="npc",
            voice_id=npc.get("voice_id", "npc-male-1"),
            personality=npc.get("personality", ""),
            attitude=npc.get("role", ""),
        )
        if npc.get("id"):
            if npc["id"] in c.characters:
                raise ValueError(f"duplicate npc id {npc['id']!r} in world")
            ch.id = npc["id"]
        if npc.get("hook"):
            ch.memory.append(npc["hook"])
        c.characters[ch.id] = ch

    # World facts the DM recalls to stay consistent (indexed into the ledger as lore).
    c.lore = [str(x) for x in (_as_list(world, "history") + _as_list(world, "standing_threads")) if str(x).strip()]

    # Background world-sim: schedule the standing threads as recurring "world beats"
    # so they advance on the clock even when the party isn't pursuing them.
    worldsim.seed_threads(c, [str(t) for t in _as_list(world, "standing_threads")])

    # Optional post-state overlay: fold a chosen (or random) ending onto the base seed.
    # Unknown/empty `ending` is a no-op, so the default path reproduces the base world
    # state exactly.
    want_ending = (ending or "").strip()
    if want_ending:
        wid = str(world.get("id", ""))
        if want_ending.lower() == "random":
            choices = [e["id"] for e in list_endings(wid)]
            want_ending = random.choice(choices) if choices else ""
        overlay = load_ending_data(wid, want_ending) if want_ending else None
        if overlay is not None:
            _apply_ending_overlay(c, overlay)
            # Persist the resolved id so a re-grounding / resume knows which post-state
            # this world is in (and "random" resolves to a concrete id, not re-rolled).
            c.ending_id = str(overlay.get("id", want_ending))

    return c
