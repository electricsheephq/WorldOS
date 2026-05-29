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

from pydantic import ValidationError

import questgen
import worldsim
from models import (
    BacklogItem,
    Campaign,
    CampaignCalendar,
    CampaignBacklog,
    CompanionArc,
    CompanionDossier,
    Character,
    DowntimeProject,
    Event,
    Faction,
    FactionArc,
    FactionAsset,
    Location,
    Quest,
    RegionControl,
    SettlementPressure,
    StrategicClock,
    WorldGraph,
    WorldGraphEdge,
    WorldGraphNode,
    WorldState,
)
from store import safe_path_segment  # path-containment guard for world/adventure ids


def _content_dir() -> Path:
    raw = os.environ.get("CLAWDND_CONTENT_DIR")
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "content"


def _characters_dirs(world_id: str) -> list[Path]:
    """Where ingested canon characters live: content/worlds/<id>/characters/ and its
    gitignored _private/ mirror (for locally-cached records)."""
    world_id = safe_path_segment(world_id, "world_id")
    base = _content_dir() / "worlds"
    return [base / world_id / "characters", base / "_private" / world_id / "characters"]


def is_playable(rec: dict) -> bool:
    """Whether a canon record may be picked up as the PLAYER. Top heroes (the BG3
    origin companions) are marked `"playable": false` so they stay legends/quest-givers,
    never a hero the player embodies. Absent flag = playable (a minor figure)."""
    return bool(rec.get("playable", True))


def ending_role_from_status(status: str) -> str:
    """Normalize a free-prose ending `fates.<npc>.status` into the bounded `ending_role`
    outcome tag the engine filters on: "died" | "survived" | "ambiguous" | "" (unclassifiable).

    The ending overlays write narrative status lines ("alive — and still himself, not ascended",
    "departed into its own designs", "as the grave is — present"), so this is deliberately
    conservative: a clear death cue -> "died"; a clear life cue -> "survived"; an explicitly
    uncertain/fate-unknown cue -> "ambiguous"; anything we can't read with confidence -> "" (the
    DM/content can set it by hand). Death is checked FIRST so "alive, but only just — not dead"
    style phrasings still resolve to survived only via the life cue, while a plain "dead" wins."""
    low = str(status or "").strip().lower()
    if not low:
        return ""
    # Explicit ambiguity wins over the cruder alive/dead keyword scan.
    if any(w in low for w in ("unknown", "uncertain", "missing", "vanished", "ambiguous", "fate unclear", "presumed")):
        return "ambiguous"
    # Death cues. Guard against "undead"/"deadly"/"deadeye" false hits by checking word-ish
    # boundaries for the bare "dead"; the other cues are unambiguous substrings.
    import re as _re
    if _re.search(r"\bdead\b", low) or any(w in low for w in ("died", "slain", "killed", "perished", "fell", "deceased")):
        return "died"
    if any(w in low for w in ("alive", "surviv", "living", "lives", "present", "free", "whole", "departed", "wandering")):
        return "survived"
    return ""


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


def find_canon_characters(
    world_id: str,
    *,
    tag: str = "",
    faction_id: str = "",
    is_merchant: "bool | None" = None,
    canon_location_id: str = "",
    arc_role: str = "",
    name_contains: str = "",
    limit: int = 50,
) -> list[dict]:
    """Structurally filter the ingested canon roster — the DM's "pull exactly the right
    character" surface. Reads the new tagging fields (`tags`, `faction_id`, `is_merchant`,
    `canon_location_id`, `arc_role`) straight off each content/worlds/<id>/characters/*.json
    record (populated by tools/derive_npc_tags). Any subset of filters may be given; they are
    AND-combined (a record must satisfy ALL provided filters). Returns up to `limit` matches as
    {name, race, class, tags, faction_id, is_merchant, canon_location_id, arc_role, role,
    playable, source_url}, de-duplicated by name. READ-ONLY — never mutates content.

    An empty/None filter is ignored (not "match empty"), so `find_canon_characters(world)` with
    no filters lists everyone (bounded by `limit`). String filters match case-insensitively;
    `tag` matches membership in the record's `tags` list; `name_contains` is a substring of the
    display name; `is_merchant` (when not None) matches the record's boolean exactly."""
    tagl = tag.strip().lower()
    facl = faction_id.strip().lower()
    locl = canon_location_id.strip().lower()
    arcl = arc_role.strip().lower()
    namel = name_contains.strip().lower()

    def _matches(rec: dict) -> bool:
        if tagl:
            rec_tags = [str(t).strip().lower() for t in (rec.get("tags") or [])]
            if tagl not in rec_tags:
                return False
        if facl and str(rec.get("faction_id", "")).strip().lower() != facl:
            return False
        if is_merchant is not None and bool(rec.get("is_merchant", False)) != is_merchant:
            return False
        if locl and str(rec.get("canon_location_id", "")).strip().lower() != locl:
            return False
        if arcl and str(rec.get("arc_role", "")).strip().lower() != arcl:
            return False
        if namel and namel not in str(rec.get("name", "")).strip().lower():
            return False
        return True

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
            if not isinstance(rec, dict):
                continue
            nm = (rec.get("name") or p.stem).strip()
            if not nm or nm.lower() in seen:
                continue
            if not _matches(rec):
                continue
            seen.add(nm.lower())
            out.append({
                "name": nm,
                "race": rec.get("race", ""),
                "class": rec.get("class", ""),
                "tags": list(rec.get("tags") or []),
                "faction_id": rec.get("faction_id", ""),
                "is_merchant": bool(rec.get("is_merchant", False)),
                "canon_location_id": rec.get("canon_location_id", ""),
                "arc_role": rec.get("arc_role", ""),
                "role": rec.get("role", ""),
                "playable": is_playable(rec),
                "source_url": rec.get("source_url", ""),
            })
            if len(out) >= max(1, limit):
                return out
    return out


def _backstory_snippet(text: str, limit: int = 220) -> str:
    """A short, single-paragraph backstory teaser for the roster card. Collapses internal
    whitespace and trims to ~`limit` chars on a word boundary with an ellipsis. Empty in ->
    empty out (the card then shows just the identity line)."""
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0].rstrip(",.;:")
    return (cut or s[:limit]) + "…"


def roster_surface(
    world_id: str,
    *,
    race: str = "",
    char_class: str = "",
    level: str = "",
    playable_only: bool = True,
    limit: int = 120,
) -> dict:
    """Read-only roster projection for the canon-NPC PICKER (the "reverse character creator").

    The player filters the ingested canon roster by race / class / level and picks a pre-made
    canon NPC to play AS — they never invent one. This returns the richer per-record shape the
    picker card needs (the light `list_canon_characters` drops level/backstory/id):

      {id, name, race, class, level, role, playable, backstory (short snippet), portrait_scope}

    `id` is the file slug (content/worlds/<id>/characters/<slug>.json) — there is no `id` field on
    the records, and the slug is what `portrait-<slug>` resolves to (the ingested face). `level` is
    carried through verbatim as the record stores it (a string, e.g. "5").

    `playable_only` defaults True so origins/legends (the 7 BG3 origin companions, marked
    `playable:false`) are EXCLUDED — the player picks a minor figure, never an origin hero. Any
    of race / char_class / level narrows the result (case-insensitive exact match on the record's
    field; an empty filter is ignored). De-duplicated by name (a figure on two wikis collapses).

    Also returns `facets` — the distinct race / class / level values present in the playable
    roster (BEFORE the race/class/level filters narrow it, frequency-ordered so the densest chips
    lead) so the picker can offer real filter chips. The unfiltered playable roster is ~2,000, far
    too many cards to paint at once, so the returned `characters` list is capped to `limit` (a
    `limit <= 0` disables the cap); `total` is the FULL matched count and `returned` is how many
    cards rode along — the picker shows "N of total" and narrows via the chips. READ-ONLY: never
    mutates content, never touches a snapshot. Mirrors `find_canon_characters` structurally."""
    racel = race.strip().lower()
    classl = char_class.strip().lower()
    levell = level.strip().lower()

    out: list[dict] = []
    seen: set[str] = set()
    # Facet tallies (lower-key -> [display, count]) so chips can be ordered by how many of the
    # roster they cover — "Human"/"Fighter" before the long tail of one-off wiki values.
    facet_races: dict[str, list] = {}
    facet_classes: dict[str, list] = {}
    facet_levels: dict[str, list] = {}

    def _tally(store: dict, value: str) -> None:
        key = value.lower()
        if key in store:
            store[key][1] += 1
        else:
            store[key] = [value, 1]

    for cdir in _characters_dirs(world_id):
        if not cdir.is_dir():
            continue
        for p in sorted(cdir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(rec, dict):
                continue
            nm = (rec.get("name") or p.stem).strip()
            if not nm or nm.lower() in seen:
                continue
            if playable_only and not is_playable(rec):
                continue
            seen.add(nm.lower())

            rrace = str(rec.get("race", "") or "").strip()
            rclass = str(rec.get("class", "") or "").strip()
            rlevel = str(rec.get("level", "") or "").strip()
            # Facets reflect the full playable roster, BEFORE the per-request filters narrow it,
            # so the chips always offer every real option (not just what the current filter left).
            if rrace:
                _tally(facet_races, rrace)
            if rclass:
                _tally(facet_classes, rclass)
            if rlevel:
                _tally(facet_levels, rlevel)

            # AND-combined filters (an empty filter is "don't filter on this").
            if racel and rrace.lower() != racel:
                continue
            if classl and rclass.lower() != classl:
                continue
            if levell and rlevel.lower() != levell:
                continue

            slug = p.stem  # the file slug IS the portrait/id key (portrait-<slug> resolves)
            out.append({
                "id": slug,
                "name": nm,
                "race": rrace,
                "class": rclass,
                "level": rlevel,
                "role": str(rec.get("role", "") or ""),
                "playable": is_playable(rec),
                "backstory": _backstory_snippet(rec.get("backstory", "")),
                # The /image scope the picker card renders. Ingested canon faces resolve by the
                # name slug (the viewer's _scope_key folds portrait-<slug> -> <slug>).
                "portrait_scope": "portrait-" + slug,
            })

    # `total` is the FULL matched count; the returned list is capped to `limit` so the picker
    # grid stays renderable (the unfiltered playable roster is ~2,000 — far too many cards/images
    # to paint at once). The UI shows "showing N of total" and narrows via the facet chips. A
    # `limit <= 0` means "no cap" (the test/headless path that wants the whole slice).
    total = len(out)
    if limit and limit > 0:
        out = out[:limit]

    def _by_count(store: dict) -> list[str]:
        # Most-covered value first (ties alphabetical) so the picker's chips lead with the
        # races/classes that actually populate the roster, not a one-off wiki value.
        return [v[0] for v in sorted(store.values(), key=lambda dc: (-dc[1], dc[0].lower()))]

    def _sorted_levels(store: dict) -> list[str]:
        # Numeric-aware sort so "2" < "10" (levels are stored as strings); non-numeric tail last.
        def _key(dc: list):
            try:
                return (0, int(dc[0]))
            except (TypeError, ValueError):
                return (1, dc[0].lower())
        return [v[0] for v in sorted(store.values(), key=_key)]

    return {
        "world_id": world_id,
        "total": total,            # full matched count (before the render cap)
        "returned": len(out),      # how many cards this payload actually carries
        "limit": limit,
        "characters": out,
        "facets": {
            "races": _by_count(facet_races),
            "classes": _by_count(facet_classes),
            "levels": _sorted_levels(facet_levels),
        },
    }


def load_canon_character(world_id: str, name: str) -> "dict | None":
    """Load one ingested canon character record by name (or file slug), or None."""
    want = name.strip().lower()
    want_toks = set(want.split())
    recs: list[tuple[str, dict]] = []
    for cdir in _characters_dirs(world_id):
        if not cdir.is_dir():
            continue
        for p in sorted(cdir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            recs.append((p.stem.lower(), rec))
    # 1) exact match on display name or file slug.
    for stem, rec in recs:
        if (rec.get("name", "").strip().lower() == want) or (stem == want):
            return rec
    # 2) fuzzy fallback (QA): the roster/prelude may use a FULLER display name than the canon
    # file — e.g. "Wyll Ravengard" vs the "Wyll" record. Match a record whose name-tokens are a
    # subset of the query (or the query's are a subset of the name's), but ONLY when that pins a
    # UNIQUE record — never guess between two. So "Wyll Ravengard" -> Wyll; an ambiguous query -> None.
    cands: dict[str, dict] = {}
    for _stem, rec in recs:
        nm = rec.get("name", "").strip().lower()
        nm_toks = set(nm.split())
        if nm_toks and (nm_toks <= want_toks or want_toks <= nm_toks):
            cands[nm] = rec
    return next(iter(cands.values())) if len(cands) == 1 else None


def load_adventure_data(adventure_id: str) -> dict:
    adventure_id = safe_path_segment(adventure_id, "adventure_id")
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


def _coerce_dossier(raw, *, where: str) -> "CompanionDossier | None":
    """Validate an OPTIONAL companion-dossier block into a CompanionDossier, or DEGRADE.

    #68: a dossier is externally-authored content (npc_roster / canon JSON / ending
    companion_seeds). A present-but-malformed block (wrong shape, bad type, a forbidden
    extra key) must SKIP — the companion simply gets no dossier — never abort start_world,
    exactly like the `companion_seeds` arc and `world_state` guards. A missing/None block
    returns None (today's behavior). `where` is a short diagnostic label for the skip log."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        print(f"[content] skipping malformed companion_dossier in {where} (not an object)")
        return None
    try:
        return CompanionDossier.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        print(f"[content] skipping malformed companion_dossier in {where}")
        return None


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
    world_id = safe_path_segment(world_id, "world_id")
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
    world_id = safe_path_segment(world_id, "world_id")
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
    world_id = safe_path_segment(world_id, "world_id")  # defense-in-depth (no current raw-input path)
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
    world_id = safe_path_segment(world_id, "world_id")  # defense-in-depth (no current raw-input path)
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
    so a hero who isn't in the roster is still covered. Premise gets the suffix appended —
    unless the overlay supplies a full `premise` REPLACEMENT (S6), which supersedes the
    base premise before the suffix appends (for endings whose base premise contradicts the
    post-state). (The overlay's story_seeds_append / story_seeds_replace are surfaced by
    start_world, not here.)

    ADDITIVE (S4): an optional `companion_seeds` block PRE-LOADS canon companions'
    relationship arcs + sealed agendas onto the matching roster Character — so the chosen
    ending shapes which companions can turn (and why). A seed for a companion not present
    in this campaign is skipped; no `companion_seeds` key is a no-op (today's behavior)."""
    # The post-state chronology REPLACES the base era — who's alive / what happened changed.
    new_era = str(overlay.get("era") or "").strip()
    if new_era:
        c.era = new_era

    # ADDITIVE (S6 audit): an optional `premise` REPLACES the base rendered premise
    # entirely (paralleling how `era` is overwritten), for endings whose base premise
    # contradicts the post-state (the "Gortash dead AND alive in one paragraph" bleed-
    # through). Absent -> the base premise is kept and only the suffix appends (today's
    # behavior). The replacement runs BEFORE the suffix append, so an ending may use both
    # a clean standalone premise and the "into this world the player steps" suffix closer.
    new_premise = str(overlay.get("premise") or "").strip()
    if new_premise:
        c.summary = new_premise

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

    # NOTE: the retraction predicate is recorded on the campaign (`c.lore_supersedes`)
    # LATER — only if the world_state block validates (see the world_state setter below).
    # The two are deliberately COUPLED: the `.md` de-confliction (lore_supersedes) and the
    # mitigating canon header (world_state) are belt-and-suspenders; recording the redaction
    # predicate while the header degraded to None would let lookup_lore strip authored .md
    # canon WITHOUT the framing header that justifies it (B-LOW). The c.lore retraction just
    # below uses the LOCAL `supersedes` list and is unconditional — base recall stays
    # de-conflicted regardless, exactly as before (this coupling is `.md`-surface-only).

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

    # Resolve a roster character by id first, then by case-insensitive name (the keying
    # used by both `fates` and `companion_seeds`); None if no such roster figure is in
    # this campaign (e.g. a hero who's only in lore, like Gale).
    def _resolve_roster(key: str) -> Character | None:
        ch = c.characters.get(key)
        if ch is None:
            kl = key.strip().lower()
            ch = next(
                (x for x in c.characters.values() if x.name.strip().lower() == kl),
                None,
            )
        return ch

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
            ch = _resolve_roster(who)
            label = ch.name if ch is not None else who
            fact = f"[{overlay.get('name', overlay.get('id', 'ending'))}] {label}: {detail}".strip(" :—")
            if ch is not None:
                ch.memory.append(fact)
                # ADDITIVE: project the fate's STATUS onto the Character's structured
                # `ending_role` (died/survived/ambiguous) so `find_npcs` can filter the post-
                # state roster by outcome, not just by the free-prose memory fact. Only set when
                # we can classify the status with confidence — an unreadable status leaves the
                # field "" (today's behavior). A non-roster hero gets only the lore line (above).
                role = ending_role_from_status(str(fate.get("status") or ""))
                if role:
                    ch.ending_role = role
            # Always add a lore line too, so non-roster heroes are recallable as well.
            c.lore.append(fact)

    # ADDITIVE (S5): the chosen ending sets the campaign's structured, canonical
    # WORLD-STATE — the load-bearing facts the DM narrates within (tenor + setting-
    # specific decisionals), surfaced as a canon header on recall/lookup_lore so both
    # retrieval surfaces share one authority. A malformed block (bad tenor enum, wrong
    # shape, forbidden extra key) must DEGRADE — skip it (the world keeps the base/None
    # state) — not abort start_world, exactly like the companion_seeds guard below. No
    # `world_state` key -> world_state stays None, so the default path is today's behavior.
    #
    # The `.md` de-confliction predicate (`c.lore_supersedes`) is recorded HERE, COUPLED to
    # a VALID world_state (B-LOW): the redaction (which strips authored .md sentences) and
    # its mitigating canon header are belt-and-suspenders. If the world_state block degrades
    # to None, we must NOT drive .md redaction without that header — so we leave
    # lore_supersedes empty and lookup_lore stays byte-identical to the no-ending path. (The
    # base-recall c.lore retraction above already ran on the local `supersedes` list and is
    # unaffected — this all-or-nothing coupling is the `.md` surface only.)
    ws_raw = overlay.get("world_state")
    if isinstance(ws_raw, dict):
        try:
            c.world_state = WorldState.model_validate(ws_raw)
        except (ValidationError, ValueError, TypeError):
            print(
                f"[content] skipping malformed world_state in ending overlay "
                f"{overlay.get('id', overlay.get('name', '?'))!r}"
            )
        else:
            # world_state validated -> safe to also drive the coupled .md de-confliction.
            if supersedes:
                c.lore_supersedes = list(supersedes)

    # ADDITIVE post-state seeding (S4 synthesis): the chosen ending may PRE-LOAD a
    # canon companion's relationship arc + sealed agenda, so "the chosen ending shapes
    # which companions betray you, and why" is a real engine fact at start_world — not
    # something the DM has to author by hand. `companion_seeds` maps a roster companion
    # (by id like "npc-the-emperor" OR display name) -> {"arc": {arc_gates, agenda},
    # "dossier"?: {wound, wants, values, ...}}. Each seed lands on the SAME roster
    # Character `fates` resolves; a seed for a companion not present in this campaign is
    # skipped silently. No `companion_seeds` key -> nothing touched, so the default path
    # is byte-for-byte today's behavior.
    companion_seeds = overlay.get("companion_seeds") or {}
    if isinstance(companion_seeds, dict):
        ov_id = overlay.get("id", overlay.get("name", "?"))
        for key, seed in companion_seeds.items():
            if not isinstance(seed, dict):
                continue
            ch = _resolve_roster(str(key))
            if ch is None:
                continue  # companion isn't in this world's roster/campaign — skip silently
            # The relationship arc + sealed agenda (S4). A dict-but-INVALID arc (e.g. a
            # `day_reached` agenda missing its M2-required `value`, a bad gate kind, a
            # forbidden extra key) raises pydantic at validate time. An ending overlay is a
            # small hand-edited add-on (like `fates` above): a single bad arc must DEGRADE —
            # skip it (the companion gets no arc) — not abort the whole start_world. (The
            # strict adventure-seed path stays loud.) A seed may legitimately carry only a
            # dossier (no `arc`), so a missing/non-dict `arc` just skips the arc, not the seed.
            if isinstance(seed.get("arc"), dict):
                try:
                    ch.arc = CompanionArc.model_validate(seed["arc"])
                except (ValidationError, ValueError, TypeError):
                    # skip the malformed arc; a valid sibling seed in the same overlay still applies
                    print(
                        f"[content] skipping malformed companion_seeds arc for {key!r} "
                        f"in ending overlay {ov_id!r}"
                    )
            # ADDITIVE (#68): the same seed may PRE-LOAD the companion's operational dossier
            # (wound/wants/values/banter/approval causes/relationships) so the chosen ending
            # also shapes who the companion IS to the living-world systems, not just whether
            # they turn. Degrades independently of the arc — a malformed dossier is skipped,
            # a valid arc on the same seed still applies (and vice-versa).
            dossier = _coerce_dossier(
                seed.get("companion_dossier", seed.get("dossier")),
                where=f"companion_seeds {key!r} in ending overlay {ov_id!r}",
            )
            if dossier is not None:
                ch.companion_dossier = dossier

    # ADDITIVE (Quest & Arc engine, Layer 3): the chosen ending may ALSO seed stumble-into
    # Events — so the post-state shapes which decisionals stumble into the party (Raphael's
    # bribe under one ending, the Flaming Fist's offer under another). Folded onto whatever the
    # base world already seeded; a malformed entry degrades (skip-one), like companion_seeds.
    # No `events` key in the overlay is a no-op. Runs here so a `reputation_at` trigger can be
    # ref-checked against the (already-seeded) factions.
    ov_id = overlay.get("id", overlay.get("name", "?"))
    _seed_events_block(c, overlay.get("events"), where=f"ending overlay {ov_id!r}")

    # ADDITIVE (Quest & Arc engine, faction arcs / #127): the chosen ending may ALSO seed faction
    # questlines — so the post-state shapes which factions are joinable + what their arcs become
    # (the Fist's rise looks different under a tyranny ending vs a liberation one). Folded onto
    # whatever the base world already seeded; a malformed entry degrades (skip-one), like events.
    # No `faction_arcs` key in the overlay is a no-op. Runs here so an arc's `faction_id` can be
    # ref-checked against the (already-seeded) factions.
    _seed_faction_arcs_block(c, overlay.get("faction_arcs"), where=f"ending overlay {ov_id!r}")


def _seed_strategic_state(c: Campaign, world: dict) -> None:
    """Seed optional strategic board data from world.json.

    The block is additive and externally-authored, so it mirrors the existing
    world_state/companion_seeds contract: malformed entries or references to
    missing factions/locations are skipped with diagnostics, never partially bound.
    """
    raw = world.get("strategic")
    if raw is None:
        return
    if not isinstance(raw, dict):
        print("[content] skipping malformed strategic block (not an object)")
        return

    def _missing_locations(ids: list[str]) -> list[str]:
        return [x for x in ids if x and x not in c.locations]

    def _missing_factions(ids: list[str]) -> list[str]:
        return [x for x in ids if x and x not in c.factions]

    for entry in _as_list_lenient(raw, "regions"):
        if not isinstance(entry, dict):
            print("[content] skipping strategic region (not an object)")
            continue
        try:
            region = RegionControl.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed strategic region")
            continue
        missing_locs = _missing_locations([region.location_id])
        missing_factions = _missing_factions([region.controller_id, *region.influence.keys()])
        if missing_locs or missing_factions:
            print(
                "[content] skipping strategic region "
                f"{region.location_id!r}: unknown refs "
                f"locations={missing_locs} factions={missing_factions}"
            )
            continue
        c.strategic_state.regions[region.location_id] = region

    for entry in _as_list_lenient(raw, "assets"):
        if not isinstance(entry, dict):
            print("[content] skipping strategic asset (not an object)")
            continue
        try:
            asset = FactionAsset.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed strategic asset")
            continue
        missing_locs = _missing_locations([asset.location_id])
        missing_factions = _missing_factions([asset.faction_id])
        if missing_locs or missing_factions:
            print(
                "[content] skipping strategic asset "
                f"{asset.id!r}: unknown refs "
                f"locations={missing_locs} factions={missing_factions}"
            )
            continue
        c.strategic_state.assets[asset.id] = asset

    for entry in _as_list_lenient(raw, "clocks"):
        if not isinstance(entry, dict):
            print("[content] skipping strategic clock (not an object)")
            continue
        try:
            clock = StrategicClock.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed strategic clock")
            continue
        if clock.scope == "region" and not clock.region_id:
            print(f"[content] skipping strategic clock {clock.id!r}: missing region_id")
            continue
        if clock.scope == "faction" and not clock.faction_id:
            print(f"[content] skipping strategic clock {clock.id!r}: missing faction_id")
            continue
        missing_locs = _missing_locations([clock.region_id])
        missing_factions = _missing_factions([clock.faction_id])
        if missing_locs or missing_factions:
            print(
                "[content] skipping strategic clock "
                f"{clock.id!r}: unknown refs "
                f"locations={missing_locs} factions={missing_factions}"
            )
            continue
        c.strategic_state.clocks[clock.id] = clock

    for entry in _as_list_lenient(raw, "projects"):
        if not isinstance(entry, dict):
            print("[content] skipping strategic project (not an object)")
            continue
        try:
            project = DowntimeProject.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed strategic project")
            continue
        missing_locs = _missing_locations([project.location_id])
        missing_factions = _missing_factions([project.faction_id])
        if missing_locs or missing_factions:
            print(
                "[content] skipping strategic project "
                f"{project.id!r}: unknown refs "
                f"locations={missing_locs} factions={missing_factions}"
            )
            continue
        c.strategic_state.projects[project.id] = project

    c.strategic_state.last_tick_day = c.day


def _seed_events_block(c: Campaign, raw, *, where: str) -> int:
    """Fold an OPTIONAL authored `events` block onto the campaign (Quest & Arc engine, Layer 3).
    Mutates `c.events`; returns the count seeded.

    The block is a list of Event objects OR a dict mapping id -> Event object. Each entry is
    validated into an `Event`; a present-but-MALFORMED entry (wrong shape, a `reputation_at`
    trigger naming a missing faction, a forbidden extra key) is SKIPPED with a diagnostic —
    DEGRADE-not-abort, exactly the companion_seeds / world_state / strategic contract — never
    aborting start_world. A missing/None/non-collection block is a no-op (today's behavior).

    A `reputation_at` trigger whose `trigger_faction_id` isn't a seeded faction is skipped (the
    trigger could never satisfy and signals an authoring typo). Other triggers reference only
    flags/day, which are open by design, so they aren't ref-checked. An explicit id collision
    (two events with the same id) keeps the first and skips the rest, logged."""
    if raw is None:
        return 0
    if isinstance(raw, dict):
        entries = list(raw.values())
    elif isinstance(raw, list):
        entries = raw
    else:
        print(f"[content] skipping malformed events block in {where} (not a list or object)")
        return 0
    seeded = 0
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"[content] skipping events entry in {where} (not an object)")
            continue
        try:
            event = Event.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print(f"[content] skipping malformed event in {where}")
            continue
        # A reputation_at trigger naming a faction not in this world can never fire — almost
        # always an authoring typo. Skip it (degrade) so the board stays clean.
        if event.trigger == "reputation_at" and event.trigger_faction_id not in c.factions:
            print(
                f"[content] skipping event {event.id!r} in {where}: "
                f"reputation_at trigger names unknown faction {event.trigger_faction_id!r}"
            )
            continue
        if event.id in c.events:
            print(f"[content] skipping event {event.id!r} in {where}: duplicate event id (keeping the first)")
            continue
        c.events[event.id] = event
        seeded += 1
    return seeded


def _seed_events(c: Campaign, world: dict) -> None:
    """Seed authored stumble-into Events from `world['events']` (Quest & Arc engine, Layer 3).

    Runs AFTER factions are seeded (so a `reputation_at` trigger can be ref-checked). Additive
    + degrade-not-abort: a world with no `events` key seeds nothing (today's behavior)."""
    _seed_events_block(c, world.get("events"), where="world events block")


def _seed_faction_arcs_block(c: Campaign, raw, *, where: str) -> int:
    """Fold an OPTIONAL authored `faction_arcs` block onto the campaign (Quest & Arc engine,
    faction arcs / #127). Mutates `c.faction_arcs` + links each named faction's `questline_arc_id`;
    returns the count seeded.

    This is the clean answer to "how is a faction arc seeded" given a faction is NOT a Character
    (so the `companion_seeds` path — keyed to a roster companion — doesn't fit): faction arcs get
    their OWN block. It mirrors `_seed_events_block` byte-for-byte — a list of FactionArc objects OR
    a dict id->FactionArc; each validated into a `FactionArc`; a present-but-MALFORMED entry (wrong
    shape, a `faction_id` naming a missing faction, a forbidden extra key, a stage `quest_id` with
    no tracked Quest, a duplicate stage id) is SKIPPED with a diagnostic — DEGRADE-not-abort,
    exactly the companion_seeds / world_state / events contract — never aborting start_world. A
    missing/None/non-collection block is a no-op (today's behavior).

    An arc naming an unknown faction is skipped (the gauge gate could never resolve — an authoring
    typo). An explicit id collision (two arcs with the same id) keeps the first and skips the rest."""
    if raw is None:
        return 0
    if isinstance(raw, dict):
        entries = list(raw.values())
    elif isinstance(raw, list):
        entries = raw
    else:
        print(f"[content] skipping malformed faction_arcs block in {where} (not a list or object)")
        return 0
    seeded = 0
    for entry in entries:
        if not isinstance(entry, dict):
            print(f"[content] skipping faction_arcs entry in {where} (not an object)")
            continue
        try:
            arc = FactionArc.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print(f"[content] skipping malformed faction arc in {where}")
            continue
        # An arc must name a real faction — its gauge gate reads that faction's reputation/standing;
        # an unknown faction can never satisfy it (almost always an authoring typo). Skip (degrade).
        if not arc.faction_id or arc.faction_id not in c.factions:
            print(
                f"[content] skipping faction arc {arc.id!r} in {where}: "
                f"names unknown faction {arc.faction_id!r}"
            )
            continue
        # A stage's optional tracked-Quest projection must point at an existing Quest (seeded
        # quests are rare at world-gen, so this is usually empty). A dangling ref degrades the arc.
        bad_quest = next((s.quest_id for s in arc.stages if s.quest_id and s.quest_id not in c.quests), None)
        if bad_quest is not None:
            print(
                f"[content] skipping faction arc {arc.id!r} in {where}: "
                f"stage references unknown tracked quest {bad_quest!r}"
            )
            continue
        if arc.id in c.faction_arcs:
            print(f"[content] skipping faction arc {arc.id!r} in {where}: duplicate arc id (keeping the first)")
            continue
        c.faction_arcs[arc.id] = arc
        # Link the faction back to its questline (the runtime tools rely on questline_arc_id to
        # find the arc on join_faction). If the faction already links a DIFFERENT arc, keep the
        # first link (one questline per faction); the arc is still seeded + reachable by id.
        fac = c.factions[arc.faction_id]
        if not fac.questline_arc_id:
            fac.questline_arc_id = arc.id
        seeded += 1
    return seeded


def _seed_faction_arcs(c: Campaign, world: dict) -> None:
    """Seed authored faction questlines from `world['faction_arcs']` (Quest & Arc engine, faction
    arcs / #127).

    Runs AFTER factions are seeded (so an arc's `faction_id` can be ref-checked). Additive +
    degrade-not-abort: a world with no `faction_arcs` key seeds nothing (today's behavior)."""
    _seed_faction_arcs_block(c, world.get("faction_arcs"), where="world faction_arcs block")


def _seed_world_graph(c: Campaign, world: dict) -> None:
    """Seed optional WorldGraph metadata from world.json.

    Graph nodes/edges are player-facing metadata only. They are skipped unless
    they refer to existing locations and edges already authorized by
    ``Location.connections``.
    """
    raw = world.get("world_graph")
    if raw is None:
        return
    if not isinstance(raw, dict):
        print("[content] skipping malformed world_graph block (not an object)")
        return

    graph = WorldGraph(
        seed=str(raw.get("seed", "")),
        provenance=str(raw.get("provenance") or "authored"),
    )
    nodes = raw.get("nodes")
    node_values = nodes.values() if isinstance(nodes, dict) else _as_list_lenient(raw, "nodes")
    for entry in node_values:
        if not isinstance(entry, dict):
            print("[content] skipping world_graph node (not an object)")
            continue
        try:
            node = WorldGraphNode.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed world_graph node")
            continue
        if node.location_id not in c.locations:
            print(f"[content] skipping world_graph node {node.location_id!r}: unknown location")
            continue
        graph.nodes[node.location_id] = node

    for entry in _as_list_lenient(raw, "edges"):
        if not isinstance(entry, dict):
            print("[content] skipping world_graph edge (not an object)")
            continue
        try:
            edge = WorldGraphEdge.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed world_graph edge")
            continue
        src = c.locations.get(edge.from_id)
        dst = c.locations.get(edge.to_id)
        if src is None or dst is None:
            print(
                "[content] skipping world_graph edge "
                f"{edge.from_id!r}->{edge.to_id!r}: unknown location"
            )
            continue
        if edge.to_id not in src.connections and edge.from_id not in dst.connections:
            print(
                "[content] skipping world_graph edge "
                f"{edge.from_id!r}->{edge.to_id!r}: not a canonical connection"
            )
            continue
        graph.edges.append(edge)

    c.world_graph = graph


def _seed_settlement_pressure(c: Campaign, world: dict) -> None:
    """Seed optional settlement/NPC/faction pressure from world.json.

    Settlement pressure is additive read-model state anchored to existing locations.
    Unknown references or malformed rows are skipped with diagnostics so an authored
    block cannot partially bind to the wrong civic surface.
    """
    for entry in _as_list_lenient(world, "settlements"):
        if not isinstance(entry, dict):
            print("[content] skipping settlement (not an object)")
            continue
        try:
            settlement = SettlementPressure.model_validate(entry)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed settlement")
            continue

        missing_locations = [settlement.location_id] if settlement.location_id not in c.locations else []
        missing_factions = [fid for fid in settlement.public_faction_ids if fid and fid not in c.factions]
        missing_npcs = [npc.npc_id for npc in settlement.public_npcs if npc.npc_id and npc.npc_id not in c.characters]
        if missing_locations or missing_factions or missing_npcs:
            print(
                "[content] skipping settlement "
                f"{settlement.location_id!r}: unknown refs "
                f"locations={missing_locations} factions={missing_factions} npcs={missing_npcs}"
            )
            continue
        if settlement.location_id in c.strategic_state.settlements:
            print(f"[content] skipping settlement {settlement.location_id!r}: duplicate location_id")
            continue

        settlement.public_faction_ids = _dedupe_strs(fid for fid in settlement.public_faction_ids if fid)
        settlement.establishments = _dedupe_strs(str(x).strip() for x in settlement.establishments if str(x).strip())
        c.strategic_state.settlements[settlement.location_id] = settlement


def _seed_campaign_backlog(c: Campaign, world: dict) -> None:
    """Seed the PROACTIVE living-world backlog (P0) — the world's own off-screen to-do, so the
    campaign advances when in-fiction time passes (P1 tick_backlog) instead of only reacting to
    the player. Mutates `c.campaign_backlog`. Mirrors the additive / degrade-not-abort contract
    (the lenient companion_seeds/world_state path, NOT the loud `_as_list` adventure path): a
    malformed source is SKIPPED with a diagnostic, never aborting start_world.

    Items are DERIVED from the world's EXISTING arc anchors (already seeded above), so every
    item's `goal_ref` traces to a real "why" (Paperclip's goal-ancestry borrow) — no free-
    floating noise:
      * STANDING THREADS (the thread_id-tagged Consequences worldsim.seed_threads scheduled) ->
        one `thread_beat` item each, `needs_llm=True` (escalating a thread into narrated prose
        needs a voice — the engine only ENQUEUES it for the later DM/agent), goal_ref=thread_id,
        recurring so an ignored thread keeps escalating.
      * FACTIONS (c.factions) -> one `faction_move` item each, DETERMINISTIC (`needs_llm=False`):
        a small mechanical reputation drift the engine applies itself (F2 — a number, not prose),
        goal_ref=faction_id, recurring.
      * SPINE QUEST HOOKS (c.quest_hooks where spine) -> one `world_event` item each,
        `needs_llm=True` (the main arc advancing off-screen needs narration), goal_ref=hook.id.

    Trigger days are STAGGERED over the first in-world days (like seed_threads) so developments
    don't all land at once. `last_tick_day` is initialized to `c.day` so a freshly-seeded world
    doesn't immediately owe a backlog of ticks on its first advance.

    A world MAY also author explicit items under a `campaign_backlog` block (a list of item
    dicts); each is validated against BacklogItem and SKIPPED-not-aborted on failure, exactly
    like the derived items. Absent/empty -> only the derived items seed (the default path)."""
    bl = c.campaign_backlog
    base = c.day
    day = base + 3  # stagger the first development a few days out, then space them
    step = 2

    def _add(item: BacklogItem) -> None:
        nonlocal day
        item.trigger_day = day
        bl.items[item.id] = item
        day += step

    # (1) Standing threads -> recurring creative escalations, traced to the thread.
    for cq in c.consequences:
        if not cq.thread_id:
            continue  # plain consequences belong to consequences.due, never the backlog
        text = str(cq.text).strip()
        if not text:
            continue
        try:
            _add(BacklogItem(
                kind="thread_beat",
                title=text[:80],
                goal_ref=cq.thread_id,
                cadence_days=6,        # an ignored thread keeps escalating
                needs_llm=True,        # narrated escalation — enqueue for the DM/agent
                note=text,
            ))
        except (ValidationError, ValueError, TypeError):
            print(f"[content] skipping malformed backlog thread_beat for {cq.thread_id!r}")

    # (2) Factions -> recurring DETERMINISTIC reputation drift (a number the engine applies).
    for fac in c.factions.values():
        try:
            _add(BacklogItem(
                kind="faction_move",
                title=f"{fac.name} maneuvers",
                goal_ref=fac.id,
                cadence_days=8,
                needs_llm=False,                         # mechanical — no prose
                effect={"faction_id": fac.id, "reputation_delta": "-1"},
                note=f"{fac.name} advances its agenda off-screen.",
            ))
        except (ValidationError, ValueError, TypeError):
            print(f"[content] skipping malformed backlog faction_move for {fac.id!r}")

    # (3) Spine quest hooks -> the main arc advances off-screen (creative).
    for hook in c.quest_hooks:
        if not getattr(hook, "spine", False):
            continue
        try:
            _add(BacklogItem(
                kind="world_event",
                title=(hook.title or "The main arc stirs")[:80],
                goal_ref=hook.id,
                cadence_days=0,        # one-shot: the spine moves once, then the DM picks it up
                needs_llm=True,
                note=(hook.arc_back or hook.note or hook.grievance or ""),
            ))
        except (ValidationError, ValueError, TypeError):
            print(f"[content] skipping malformed backlog world_event for {hook.id!r}")

    # (4) Optional authored override items (degrade-not-abort, like companion_seeds).
    for raw in _as_list_lenient(world, "campaign_backlog"):
        if not isinstance(raw, dict):
            print("[content] skipping malformed authored campaign_backlog item (not an object)")
            continue
        try:
            item = BacklogItem.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            print("[content] skipping malformed authored campaign_backlog item")
            continue
        # An authored item may pin its own trigger_day; if it left the default 0, stagger it in.
        if item.trigger_day <= 0:
            item.trigger_day = day
            day += step
        bl.items[item.id] = item

    # The cursor starts at today so the first advance only fires what is genuinely due.
    bl.last_tick_day = base


def _resolve_quest_variants(c: Campaign, world: dict, rng: random.Random) -> None:
    """The replayability layer (S6): resolve each MAJOR world quest's canonical OUTCOME
    once at world-gen (mutates `c`). Mirrors the `world_state` contract — additive, setting-
    agnostic, degrade-not-abort — and reuses the shipped lore/recall plumbing for free.

    Read `world["quest_variants"]` (absent -> a no-op, today's behavior). For each quest,
    pick ONE outcome:
      * ENDING-TIED first — the first outcome carrying a `when` dict that is a SUBSET of the
        world-state (all k=v must hold) wins. The match view is `world_state.facts` PLUS the
        typed `world_tenor` dial as a virtual key (so an outcome may pin to the generic mood —
        `when:{world_tenor:hopeful}` — or to a setting-specific fact — `when:{bhaal:ascendant}`).
        The base/no-ending path has no world_state, so every `when` fails and everything rolls
        random (replayability for the default world too). MUST run AFTER `_apply_ending_overlay`
        so the facts/tenor are populated.
      * RANDOM fallback — a SEEDED weighted roll over the outcomes carrying `random:<weight>`.
        `rng` is derived off the campaign id (belt-and-suspenders; persistence is the real
        reproducibility since seed_world runs once). A separate `random.Random` instance
        (not the global `random`) leaves the ending roll at seed_world untouched.

    The resolved outcome is stored on `c.quest_outcomes[quest_id]` (the structured record a
    tool reads) AND appended to `c.lore` as `[Outcome] <lore>` + (if present) `[Hook] <hook>`
    lines — so recall/lookup_lore surface them under the canon header, exactly like the
    `fates` append in `_apply_ending_overlay`. Every block degrades-not-aborts per the
    world_state guard: a malformed quest/outcome entry is SKIPPED (a valid sibling still
    resolves), never aborting start_world."""
    # The match view for `when`: the setting-specific facts plus the typed `world_tenor`
    # dial as a virtual key, so an outcome can pin to either the generic mood or a fact.
    # No world_state (base/no-ending) -> empty view -> every `when` fails and all rolls.
    facts: dict[str, str] = {}
    if c.world_state is not None:
        facts = dict(c.world_state.facts)
        facts.setdefault("world_tenor", c.world_state.world_tenor)
    for qv in _as_list_lenient(world, "quest_variants"):
        if not isinstance(qv, dict):
            continue
        qid = str(qv.get("id") or "").strip()
        if not qid:
            continue  # an outcome with no quest id can't be stored/recalled — skip it
        # Only well-formed outcomes (a dict carrying a non-empty id) may resolve — an
        # id-less outcome can't be a stored resolution, so it's dropped UP FRONT (never
        # matches as ending-tied, never displaces a valid sibling in the random roll).
        outcomes = [
            o for o in _as_list_lenient(qv, "outcomes")
            if isinstance(o, dict) and str(o.get("id") or "").strip()
        ]
        chosen: dict | None = None
        # ENDING-TIED first: the first outcome whose `when` is a subset of the match view.
        for o in outcomes:
            when = o.get("when")
            if isinstance(when, dict) and when and all(facts.get(k) == v for k, v in when.items()):
                chosen = o
                break
        # RANDOM fallback: a seeded weighted roll over the outcomes carrying `random:<weight>`.
        if chosen is None:
            pool = [o for o in outcomes if o.get("when") is None and o.get("random") is not None]
            weights: list[int] = []
            for o in pool:
                try:
                    weights.append(max(1, int(o.get("random", 1))))
                except (TypeError, ValueError):
                    weights.append(1)  # a non-numeric weight degrades to 1, never aborts
            if pool:
                chosen = rng.choices(pool, weights=weights, k=1)[0]
        if chosen is None:
            continue  # no `when` matched and no random pool — nothing to resolve for this quest
        c.quest_outcomes[qid] = str(chosen["id"]).strip()
        # Append the resolved prose to recallable lore, under the same canon header as
        # `fates`. Stable `[Outcome]`/`[Hook]` prefixes let the DM/tool spot them.
        name = str(qv.get("name") or qid)
        lore_text = str(chosen.get("lore") or "").strip()
        if lore_text:
            c.lore.append(f"[Outcome] {name}: {lore_text}")
        hook_text = str(chosen.get("hook") or "").strip()
        if hook_text:
            c.lore.append(f"[Hook] {name}: {hook_text}")


def seed_world(world: dict, start_at: str = "", ending: str = "") -> Campaign:
    """Seed a Campaign from a WORLD bible (a persistent setting the DM generates
    *within*, not a fixed plot). Unlike an adventure, a world ships its regions,
    factions, a roster of pullable NPCs, and its history/standing-threads as `lore`
    — which the ledger indexes so `recall` keeps the generated story consistent. The
    DM then drops the party at a starting region and generates + persists the actual
    adventure as the player explores.

    `ending` selects a post-state OVERLAY (content/worlds/<id>/endings/<ending>.json):
    after the base seed, the overlay OVERWRITES the era (and, optionally, the rendered
    premise), appends its history + standing threads into recallable lore (and ticks them
    in the world-sim), appends OR replaces story_seeds, lands each `fates` entry on the
    matching roster NPC, and PRE-LOADS any `companion_seeds` arc/agenda onto the matching
    roster companion. `ending="random"` picks one of the world's overlays at random; an
    unknown/empty `ending` leaves the BASE world state untouched (today's behavior). The
    resolved id is stored on `Campaign.ending_id`."""
    if not isinstance(world, dict):
        raise ValueError("world data must be a JSON object")
    c = Campaign(title=world.get("name", "Untitled World"), summary=world.get("premise", ""))
    c.world_id = str(world.get("id", ""))  # enables lookup_lore over this world's corpus
    c.era = str(world.get("era") or world.get("current_year") or "")  # chronology guardrail
    if isinstance(world.get("calendar"), dict):
        try:
            calendar = CampaignCalendar.model_validate(world["calendar"])
            if calendar.months:
                c.calendar = calendar
        except (ValidationError, ValueError, TypeError):
            c.calendar = None

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
    seen_area_ids: set[str] = set()  # intra-area id-collision guard (this seeding pass only)
    new_area_ids: list[str] = []
    for area in load_world_areas(c.world_id):
        name = str(area.get("name", "")).strip()
        if not name or name.lower() in seeded_names:
            continue  # never double-seed a region the world already declares
        aid = str(area.get("id", "")).strip()
        if aid and aid in c.locations:
            # An id already taken — skip rather than clobber. Two cases, both guarded by
            # this single check: (a) the id collides with a previously-seeded AREA (an
            # INTRA-area dup: load_world_areas dedupes by NAME, so two differently-named
            # files can still share an id), or (b) it collides with an authored region.
            # In case (a) the first area MUST survive untouched — log so a dup isn't silent.
            if aid in seen_area_ids:
                print(
                    f"[content] skipping ingested area {name!r}: duplicate area id {aid!r} "
                    f"(already seeded a different area with that id — keeping the first)"
                )
            continue
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
        seen_area_ids.add(location.id)  # remember the id so a later dup can't clobber it
        new_area_ids.append(location.id)

    # Resolve the freshly-seeded areas' connection NAMES to location ids where a seeded
    # place matches by name (case-insensitive); unmatched names stay verbatim as hints.
    # Only the new areas are rewritten — regions already carry id-based connections.
    #
    # Travel/reachable use DIRECTED edges from the CURRENT location (travel.py): an edge
    # area→region does NOT make the area reachable while you're standing in the region.
    # So — exactly like add_location's bidirectional wiring (server.py) — for every
    # resolved area→location edge we ALSO add the REVERSE edge location→area, guarding
    # duplicates. Without this, an ingested area lists its parent region as a connection
    # but is itself unreachable FROM that region (the B2 repro: Bloomridge Market lists
    # loc-lower-city, yet reachable() from loc-lower-city omits Bloomridge).
    if new_area_ids:
        name_to_id = {loc.name.strip().lower(): lid for lid, loc in c.locations.items()}
        for aid in new_area_ids:
            loc = c.locations[aid]
            loc.connections = _dedupe_strs(
                name_to_id.get(conn.strip().lower(), conn) for conn in loc.connections
            )
            # Mirror the forward edges back: any connection that resolved to a real
            # location id gets the area added to ITS connections (bidirectional, deduped).
            for conn_id in loc.connections:
                target = c.locations.get(conn_id)
                if target is not None and target.id != aid and aid not in target.connections:
                    target.connections.append(aid)

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
            # ADDITIVE faction-growth membership (faction arcs / #127). A world MAY seed a
            # starting rank/standing/joined state, but the default leaves a faction un-joined at
            # rank 0 / standing 0 — byte-for-byte today's behavior. `standing` floors at 0 (the
            # monotonic gauge), so a negative seed degrades to 0 rather than aborting the world.
            rank=int(fac.get("rank", 0)),
            standing=max(0, int(fac.get("standing", 0))),
            joined=bool(fac.get("joined", False)),
        )
        if fac.get("id"):
            faction.id = fac["id"]
        c.factions[faction.id] = faction

    _seed_world_graph(c, world)
    _seed_strategic_state(c, world)

    # Faction-growth questlines (Quest & Arc engine, faction arcs / #127). Runs AFTER factions are
    # seeded so an arc's `faction_id` can be ref-checked. Additive: a world with no `faction_arcs`
    # key is a no-op (today's behavior); the ending overlay may add MORE (see _apply_ending_overlay).
    _seed_faction_arcs(c, world)

    # Authored stumble-into Events (Quest & Arc engine, Layer 3). Runs after factions so a
    # `reputation_at` trigger can be ref-checked. Additive: a world with no `events` key is a
    # no-op (today's behavior). The ending overlay may add MORE events (see _apply_ending_overlay).
    _seed_events(c, world)

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
        # ADDITIVE (#68): a roster entry may carry an OPTIONAL companion dossier — the
        # operational identity (wound/wants/values/banter/approval causes/relationships) the
        # living-world systems act on. A malformed block DEGRADES (the NPC gets no dossier),
        # it never aborts the world seed; no `dossier` key -> dossier stays None (today's
        # behavior). Accepts `dossier` or the full `companion_dossier` alias.
        dossier = _coerce_dossier(
            npc.get("companion_dossier", npc.get("dossier")),
            where=f"npc_roster entry {ch.name!r}",
        )
        if dossier is not None:
            ch.companion_dossier = dossier
        # ADDITIVE (#221, the generativity boundary the 2nd-seed spike surfaced): a roster
        # entry may ALSO carry an OPTIONAL companion `arc` — the relationship gauge + arc
        # gates + the sealed `agenda` that flips the companion on a decision/attitude break.
        # In Baldur's Gate this is seeded per-ENDING (companion_seeds in the overlay), so the
        # SAME companion turns differently under different endings; but a world WITHOUT endings
        # (a fresh universe like the Tidal Commonwealth) had NO surface to arm a companion flip
        # at all. Loading `arc` here from the base roster closes that gap — any world can author
        # a companion who turns, right where the companion is defined. A malformed arc DEGRADES
        # (the NPC gets no arc), never aborting the seed; no `arc` key -> arc stays None (today's
        # behavior). An ending overlay's companion_seeds runs LATER (`_apply_ending_overlay`,
        # after this loop) so an ending still OVERRIDES the base arc — endings keep the last word.
        if isinstance(npc.get("arc"), dict):
            try:
                ch.arc = CompanionArc.model_validate(npc["arc"])
            except (ValidationError, ValueError, TypeError):
                print(f"[content] skipping malformed arc in npc_roster entry {ch.name!r}")
        c.characters[ch.id] = ch

    _seed_settlement_pressure(c, world)

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

    # The replayability layer (S6): resolve each major quest's outcome — ending-tied where
    # the chosen ending's world_state.facts match, else a seeded random roll. Runs HERE,
    # after the overlay, so world_state.facts is populated for `when`-matching. A world with
    # no quest_variants is a no-op (quest_outcomes stays {}, c.lore untouched). The rng is
    # seeded off the campaign id (a fresh uuid per campaign) so a given campaign's roll is
    # stable; a separate Random instance leaves the ending roll above untouched.
    _resolve_quest_variants(c, world, random.Random(c.id))

    # S7 — the quest-generation layer: assemble lore-derived quest hooks (from the resolved
    # outcomes + world_state + roster) + a guaranteed 4-beat cold-open PRELUDE the DM weaves.
    # Runs LAST so it can draw on everything seeded above. A SEPARATE Random instance (distinct
    # seed) keeps it from perturbing the quest-variant roll's stream. Additive + degrade-not-
    # abort: a world with no variants/locations yields an empty graph (today's behavior).
    questgen.generate(c, world, random.Random(f"{c.id}:questgen"))

    # The PROACTIVE living-world backlog (P0): derive the world's own off-screen to-do from the
    # arc anchors seeded above (standing threads, factions, spine hooks) so the campaign advances
    # when in-fiction time passes (P1). Runs LAST so factions/threads/spine-hooks all exist for
    # goal_ref binding. Additive + degrade-not-abort: a world with no anchors yields an empty
    # backlog (today's behavior); `last_tick_day` is set to c.day so the first advance owes nothing.
    _seed_campaign_backlog(c, world)

    return c
