"""F04-1: region danger/creature tables must MATCH shipped content.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F04-1, issue #822).

The bug: every Baldur's Gate area/region ships region="Baldur's Gate" (or '' for the
authored regions), which matches NO wander keyword, so the resolver falls back to the
BASE_RATE (0.30) wilderness model — a city street rolls a 30% chance of a *wolf/ogre*
ambush. The signal that says "this is a city" lives in the location's NAME + tags
(joined into `notes`), surfaces the matcher never saw.

The fix: (1) the three staging seams build a COMPOSITE match string
"<region> <name> <notes>" and pass it to the resolver (the wire `region` value stays
loc.region); (2) wander.py gains urban keywords (market/tavern/harbor/.../quarter)
in the civilized band + sewer/undercity in the non-civilized band; (3) content.py
seeds the authored regions' `region` field (was '').
"""

import random

import pytest

import content
import server
import store
import wander


# --- (1) CONTENT: the authored BG regions now seed a non-empty `region` -------------


def test_authored_regions_seed_region_field():
    """content.seed_world must stamp `region` on authored regions (was '' — the F04-1
    seed-side half). Old snapshots round-trip (the field already existed); this only
    changes what a FRESH seed writes."""
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    # the authored Lower City hub now carries a region (its own name) — not ''
    lower = c.locations.get("loc-lower-city")
    assert lower is not None
    assert lower.region, "authored region seeded with empty region (F04-1 seed half not applied)"


# --- (2) RESOLVER coverage over the real shipped BG world ----------------------------


def _bg_campaign():
    w = content.load_world_data("baldurs-gate")
    return content.seed_world(w)


def _composite(loc) -> str:
    """The match string the engine seams build (mirrors server-side _stage seam)."""
    return f"{loc.region} {loc.name} {loc.notes}".strip()


def test_bg_locations_resolve_non_base_rate_keyword():
    """Spec: ≥90% of BG locations must resolve a non-BASE_RATE keyword via the composite
    (today: 0% — every one falls to BASE_RATE)."""
    c = _bg_campaign()
    locs = list(c.locations.values())
    assert len(locs) >= 20, f"expected the full BG graph, got {len(locs)} locations"
    resolved = 0
    for loc in locs:
        comp = _composite(loc)
        if wander._match_keyword(comp, wander.REGION_RATES) is not None:
            resolved += 1
    frac = resolved / len(locs)
    assert frac >= 0.90, f"only {resolved}/{len(locs)} ({frac:.0%}) BG locations resolve a keyword"


def test_bg_city_scenes_read_civilized_via_composite():
    """A BG *city* location (tagged 'city') must resolve to the civilized tier + low rate
    through the composite — NOT the wilderness BASE_RATE."""
    c = _bg_campaign()
    city_locs = [
        loc for loc in c.locations.values()
        if "city" in (loc.notes or "").lower() and "sewer" not in (loc.notes or "").lower()
        and "dungeon" not in (loc.notes or "").lower() and "ruins" not in (loc.notes or "").lower()
    ]
    assert city_locs, "no plain-city BG locations found — content drifted"
    for loc in city_locs:
        comp = _composite(loc)
        chance = wander.encounter_chance(comp)
        assert chance <= 0.12, f"{loc.name!r} composite {comp!r} chance {chance} > 0.12 (wilderness leak)"
        assert wander._region_tier(comp) == "civilized", f"{loc.name!r} not civilized via composite"


def test_bg_undercity_sewers_read_non_civilized_via_composite():
    """The Undercity / sewers areas (urban-underground) must read NON-civilized via the
    composite even though their names/tags also contain 'city'."""
    c = _bg_campaign()
    underground = [
        loc for loc in c.locations.values()
        if "sewer" in (loc.notes or "").lower() or "undercity" in (loc.name or "").lower().replace(" ", "")
        or "undercity" in f"{loc.region}{loc.name}".lower().replace(" ", "")
    ]
    assert underground, "no undercity/sewer BG locations found — content drifted"
    for loc in underground:
        comp = _composite(loc)
        assert wander._region_tier(comp) != "civilized", (
            f"{loc.name!r} composite {comp!r} read civilized; expected non-civilized"
        )


# --- (3) SEAM: the staged payload keeps the DISPLAY region, matches off the composite -


@pytest.fixture
def city_camp(tmp_path, monkeypatch):
    """A campaign whose current location is a CITY scene: region='Baldur's Gate',
    notes carry the 'city' tag (mirroring a real ingested BG area)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("City Test")["id"]
    start = server.add_location(cid, "The Lower City", region="Baldur's Gate")["id"]
    dest = server.add_location(
        cid, "Bloomridge Market", connections=[start], region="Baldur's Gate"
    )["id"]
    pc = server.create_character(cid, "Renn", kind="player")["id"]
    server.update_character(cid, pc, {"classes": [{"name": "fighter", "level": 3}]})
    # the 'city' signal lives in NOTES (tags joined into notes by the ingest path);
    # add_location has no notes kwarg, so set it directly (mirrors content.py:1665).
    c = store.load_campaign(cid)
    c.locations[start].notes = "market city hub"
    c.locations[dest].notes = "market city hub"
    store.save_campaign(c)
    return cid, start, dest


def test_city_travel_leg_does_not_stage_wilderness_pool(city_camp, monkeypatch):
    """A forced city travel-leg combat must draw from the CIVILIZED pool, never the
    wilderness pool — the F04-1 core fix (no wolves in the Lower City)."""
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    monkeypatch.setattr(wander, "_weighted_choice", lambda *a, **k: "combat")
    wilderness = set(wander.REGION_CREATURES["wilderness"]) - set(wander.REGION_CREATURES["civilized"])
    civilized = set(wander.REGION_CREATURES["civilized"])
    cid, _start, dest = city_camp
    drawn = set()
    for _ in range(20):
        out = server.travel_to(cid, dest, advance_time=True)
        we = out.get("wandering_encounter")
        if we and we.get("type") == "combat":
            for foe in we["foes"]:
                drawn.add(foe["name"])
        # reset position for the next leg
        c = store.load_campaign(cid)
        c.current_location_id = _start_of(c, dest)
        # despawn staged monsters so the next leg starts clean
        for mid in [i for i, ch in c.characters.items() if ch.kind == "monster"]:
            del c.characters[mid]
        store.save_campaign(c)
    assert drawn, "no combat staged across 20 forced legs"
    assert drawn.issubset(civilized), f"city leg drew wilderness creatures: {drawn - civilized}"
    assert not (drawn & wilderness), f"city leg drew wilderness-only creatures: {drawn & wilderness}"


def test_city_seam_payload_keeps_display_region(city_camp, monkeypatch):
    """The staged payload's `region` key must stay the DISPLAY value (loc.region =
    'Baldur's Gate'), NOT the composite match string (don't change the wire semantics)."""
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    monkeypatch.setattr(wander, "_weighted_choice", lambda *a, **k: "boon")
    cid, _start, dest = city_camp
    out = server.travel_to(cid, dest, advance_time=True)
    we = out["wandering_encounter"]
    assert we["region"] == "Baldur's Gate", f"payload region leaked the composite: {we['region']!r}"


def test_city_roll_wandering_encounter_keeps_display_region(city_camp, monkeypatch):
    """The explicit roll_wandering_encounter tool: civilized pool + display region."""
    monkeypatch.setattr(wander, "_weighted_choice", lambda *a, **k: "boon")
    cid, _start, _dest = city_camp
    out = server.roll_wandering_encounter(cid)
    assert out["staged"] is True
    assert out["region"] == "Baldur's Gate", f"explicit-roll region is {out['region']!r}"


def _start_of(c, dest):
    """The non-dest location id (the start) — helper for the multi-leg reset."""
    for lid in c.locations:
        if lid != dest:
            return lid
    return c.current_location_id
