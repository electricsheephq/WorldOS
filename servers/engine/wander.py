"""Kingmaker-style wandering encounters — the per-region danger roll + foe picker.

A QA + real-play gap: travel and camp carried ZERO combat risk. `travel_to` was
pure graph movement, `long_rest`/`camp_scene` never staged an ambush, and
`encounter.py` was only CR/XP *sizing* math — there was no random-encounter
generator, so six days of overland travel produced no fights and the world felt
inert. This module is the missing generator: it composes the primitives that
already exist (`encounter.py` for XP sizing, `bestiary.py` for creatures) into a
per-region "does something jump the party?" roll plus an appropriately-sized foe
list.

PURE module — no campaign I/O, no MCP, no persistence (unit-testable exactly like
`encounter.py`). The engine seams (`travel_to`, `long_rest`, an explicit MCP tool)
call `encounter_chance`/`roll_encounter` to decide IF a wandering encounter fires
and `pick_encounter` to decide WHAT, then spawn the foes via the existing
`spawn_monster` code path and surface them — they never auto-start combat.

Determinism: every random choice flows through an injected `random.Random` (the
caller passes a seeded one in tests / under a campaign seed), so a given
(seed, region, party) always yields the same roll + foes.
"""

from __future__ import annotations

import random

import bestiary
import encounter

# Per-leg / per-watch base danger when no region keyword matches. ~30% lands in the
# brief's "sensible default 25-35%" band: frequent enough that a multi-day journey
# reliably sees a fight, rare enough that travel isn't wall-to-wall combat.
BASE_RATE = 0.30

# Region danger by KEYWORD (case-insensitive substring of Location.region). The
# region string is free-form / generated content (e.g. "South West Odrun Fell"),
# so we match keywords rather than exact names; an unmatched region falls back to
# BASE_RATE. First matching keyword (in iteration order) wins, so a "haunted
# forest" reads as haunted (the more dangerous tag) before forest. Values are the
# raw per-roll chance BEFORE modifiers.
REGION_RATES: dict[str, float] = {
    # tame / civilized — patrolled, low risk
    "town": 0.08,
    "city": 0.08,
    "village": 0.10,
    "keep": 0.10,
    "road": 0.15,
    "safe": 0.05,
    "haven": 0.05,
    "sanctuary": 0.05,
    # ordinary wilderness — the default-ish overland danger
    "hill": 0.30,
    "forest": 0.32,
    "wood": 0.32,
    "fell": 0.32,
    "moor": 0.32,
    "coast": 0.28,
    "river": 0.28,
    "plain": 0.25,
    "field": 0.25,
    # rough country — more likely to bite
    "marsh": 0.40,
    "swamp": 0.40,
    "bog": 0.40,
    "mountain": 0.38,
    "waste": 0.42,
    "badland": 0.42,
    "frontier": 0.38,
    "wild": 0.40,
    "border": 0.36,
    # overtly hostile — something is almost always out there
    "dungeon": 0.50,
    "ruin": 0.45,
    "haunted": 0.50,
    "cursed": 0.55,
    "shadow": 0.50,
    "underdark": 0.55,
    "blight": 0.50,
    "dread": 0.55,
    "deathly": 0.55,
}

# A coarse danger TIER for each region keyword, used only to bias *which* creatures
# get drawn (see REGION_CREATURES / _region_pool). Independent of REGION_RATES so a
# region's frequency and its flavor can be tuned separately.
_REGION_TIER: dict[str, str] = {
    "town": "civilized", "city": "civilized", "village": "civilized",
    "keep": "civilized", "road": "civilized", "safe": "civilized",
    "haven": "civilized", "sanctuary": "civilized",
    "marsh": "swamp", "swamp": "swamp", "bog": "swamp", "river": "swamp",
    "coast": "coast",
    "mountain": "mountain", "hill": "mountain", "waste": "mountain",
    "badland": "mountain",
    "forest": "forest", "wood": "forest", "fell": "forest", "moor": "forest",
    "ruin": "undead", "haunted": "undead", "cursed": "undead",
    "shadow": "undead", "blight": "undead", "dread": "undead",
    "deathly": "undead", "dungeon": "undead",
    "underdark": "underdark",
}

# Creature pools by flavor tier, in ROUGHLY ascending CR within each list. Every
# name here resolves through `bestiary.resolve` against the vendored SRD 5.2.1 dump
# (verified) — they're the picker's palette; the count is what's tuned to hit the
# party's XP budget. A region with no recognized keyword uses "wilderness".
REGION_CREATURES: dict[str, list[str]] = {
    "wilderness": ["Wolf", "Boar", "Giant Spider", "Worg", "Brown Bear", "Dire Wolf", "Ogre"],
    "civilized": ["Bandit", "Guard", "Scout", "Tough", "Cultist", "Bandit Captain"],
    "forest": ["Wolf", "Giant Spider", "Boar", "Worg", "Dire Wolf", "Brown Bear", "Harpy"],
    "mountain": ["Kobold Warrior", "Worg", "Bugbear Warrior", "Brown Bear", "Ogre"],
    "swamp": ["Stirge", "Giant Frog", "Crocodile", "Constrictor Snake", "Giant Spider", "Merrow"],
    "coast": ["Reef Shark", "Sahuagin Warrior", "Crocodile", "Merrow"],
    "undead": ["Skeleton", "Zombie", "Ghoul", "Specter", "Wight", "Wraith"],
    "underdark": ["Kobold Warrior", "Giant Spider", "Ghoul", "Specter", "Ogre"],
}

# Built-in modifier nudges (additive to the chance). A caller may also pass any
# numeric value in `modifiers` to adjust by an arbitrary delta (e.g. the camp-watch
# seam passes {"camouflage": True} to lower the watch ambush chance). Unknown
# string keys with truthy values fall through to a 0 delta (harmless no-op).
_MODIFIER_DELTAS: dict[str, float] = {
    "dangerous": +0.15,   # a tense region / hot pursuit
    "hostile_territory": +0.15,
    "alert": +0.10,       # the party already knows something's hunting them
    "camouflage": -0.15,  # a hidden camp / careful watch (the camp-watch seam)
    "stealth": -0.15,     # moving carefully / off the road
    "well_hidden": -0.20,
    "safe_haven": -0.25,  # a warded / friendly waypoint
    "rested": -0.05,
}


def _region_key(region: str) -> str:
    return (region or "").strip().lower()


def _match_keyword(region: str, table: dict) -> str | None:
    """The first keyword in `table` that appears as a substring of `region`
    (case-insensitive), or None. Dict iteration order is insertion order, so the
    tables above are arranged so a more-specific/dangerous tag wins a tie."""
    key = _region_key(region)
    if not key:
        return None
    for keyword in table:
        if keyword in key:
            return keyword
    return None


def encounter_chance(region: str = "", modifiers: dict | None = None) -> float:
    """The probability [0.0, 1.0] that a wandering encounter fires for `region`.

    A per-region base rate (`REGION_RATES`, keyed by a keyword found in the region
    string; `BASE_RATE` for an unknown/empty region) adjusted by `modifiers`:
      * a known modifier key (e.g. ``dangerous``, ``camouflage``/``stealth``) applies
        its built-in delta when truthy;
      * any numeric value applies as a raw additive delta (so a caller can pass
        ``{"bonus": -0.1}`` or a house-rule scalar directly);
      * unknown truthy string flags are ignored (0 delta) — a forgiving contract so a
        typo'd modifier degrades to a no-op rather than raising.
    The result is clamped to [0.0, 1.0]. Pure: no RNG, no I/O.
    """
    keyword = _match_keyword(region, REGION_RATES)
    chance = REGION_RATES[keyword] if keyword is not None else BASE_RATE
    for name, value in (modifiers or {}).items():
        if isinstance(value, bool):
            if value:
                chance += _MODIFIER_DELTAS.get(name, 0.0)
        elif isinstance(value, (int, float)):
            chance += float(value)
        # non-bool, non-numeric (e.g. a string note) -> ignored
    return max(0.0, min(1.0, chance))


def roll_encounter(
    region: str = "",
    modifiers: dict | None = None,
    rng: random.Random | None = None,
) -> bool:
    """Roll once against `encounter_chance(region, modifiers)`. True == an encounter
    fires. Deterministic under a seeded `rng` (a fresh `random.Random()` is used when
    none is passed). A chance of 0.0 never fires; 1.0 always fires."""
    chance = encounter_chance(region, modifiers)
    if chance <= 0.0:
        return False
    if chance >= 1.0:
        return True
    r = rng or random.Random()
    return r.random() < chance


def _region_pool(region: str) -> list[str]:
    """The creature-name palette for `region`, by its flavor tier (forest/swamp/
    undead/…), falling back to the generic ``wilderness`` pool for an unknown or
    empty region."""
    keyword = _match_keyword(region, _REGION_TIER)
    tier = _REGION_TIER[keyword] if keyword is not None else "wilderness"
    return REGION_CREATURES.get(tier) or REGION_CREATURES["wilderness"]


def _xp_for_name(name: str) -> int | None:
    """The SRD XP value for a resolvable creature name, or None if it doesn't resolve
    / has no derivable XP. Goes through the bestiary so the number matches what
    `spawn_monster` will stamp on the spawned Character (`xp_value`)."""
    canonical = bestiary.resolve(name)
    if canonical is None:
        return None
    sb = bestiary.stat_block(canonical)
    if sb is None:
        return None
    xp = int(sb.get("xp") or 0)
    return xp if xp > 0 else None


def _resolved_pool(region: str) -> list[tuple[str, str, int]]:
    """`(canonical_name, region_keyword_name, xp)` for every creature in the region's
    pool that resolves with positive XP, preserving the pool's ascending-CR order.
    Defends against a pool name that ever fails to resolve (it's just skipped)."""
    out: list[tuple[str, str, int]] = []
    for name in _region_pool(region):
        canonical = bestiary.resolve(name)
        if canonical is None:
            continue
        sb = bestiary.stat_block(canonical)
        xp = int(sb.get("xp") or 0) if sb else 0
        if xp > 0:
            out.append((canonical, name, xp))
    return out


def _count_for_budget(party_levels: list[int], unit_xp: int, target_difficulty: str) -> int:
    """How many copies of a `unit_xp` creature land an encounter in the
    `target_difficulty` band for `party_levels`, using the SRD sizing in
    `encounter.py` (per-monster XP * the group-size multiplier vs the party budget).

    Walks the count up from 1, classifying each group with
    ``encounter.encounter_difficulty``; returns the smallest count whose band is at
    least the target. If even one creature already exceeds the target (a chunky
    solo), returns 1. Caps the search at 12 so a tiny-XP creature against a high
    budget can't loop unboundedly — the cap also matches the SRD multiplier table
    flattening out by then. Always returns >= 1."""
    order = {d: i for i, d in enumerate(("trivial",) + encounter.DIFFICULTIES)}
    want = order.get(target_difficulty, order["medium"])
    best_count = 1
    for n in range(1, 13):
        band = encounter.encounter_difficulty(party_levels, [unit_xp] * n)
        best_count = n
        if order.get(band, 0) >= want:
            return n
    # Never reached the target band even at the cap -> field the cap (the largest
    # group we'll stage); still a real, sized threat.
    return best_count


def pick_encounter(
    party_levels: list[int],
    region: str = "",
    target_difficulty: str = "medium",
    rng: random.Random | None = None,
) -> list[dict]:
    """Choose region-appropriate foe(s) sized to the party's XP budget.

    Picks ONE creature kind from the region's flavor pool (`_region_pool`) at random
    (seeded via `rng`), then sizes the GROUP to hit `target_difficulty`
    (easy/medium/hard/deadly) for `party_levels` using the SRD math in
    `encounter.py` (`encounter_difficulty` over `count` copies). A single-monster pick
    is preferred when one already meets the band; otherwise the count grows until the
    band is met (capped). Returns a list of creature specs the caller spawns and
    surfaces:

        [{"name": <canonical bestiary name>, "count": <int>, "xp_each": <int>,
          "cr": <str>}]

    (A list — same shape across callers — even though we currently field one kind per
    encounter; this leaves room for mixed groups later without a contract change.)

    Deterministic under a seeded `rng`. Returns ``[]`` when `party_levels` is empty or
    the region pool yields no resolvable creature (the caller treats an empty list as
    "no encounter staged" and leaves travel/rest unchanged)."""
    if not party_levels:
        return []
    pool = _resolved_pool(region)
    if not pool:
        return []
    r = rng or random.Random()
    canonical, _pool_name, unit_xp = r.choice(pool)
    count = _count_for_budget(list(party_levels), unit_xp, target_difficulty)
    sb = bestiary.stat_block(canonical)
    cr = sb.get("cr", "") if sb else ""
    return [{"name": canonical, "count": count, "xp_each": unit_xp, "cr": cr}]
