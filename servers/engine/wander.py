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
    # overtly hostile — danger-adjective keywords FIRST so compound names like
    # "The Haunted Wood" or "Cursed Mountain Pass" resolve to the right tier before
    # any terrain noun (e.g. "wood", "mountain") can match first.
    "dungeon": 0.50,
    "ruin": 0.45,
    "haunted": 0.50,
    "cursed": 0.55,
    "shadow": 0.50,
    "blight": 0.50,
    "dread": 0.55,
    "deathly": 0.55,
    "underdark": 0.55,
    # urban UNDERGROUND (F04-1) — sewers / the Undercity. These are city-adjacent but
    # genuinely dangerous, NOT tame, so they sit in the danger band. They MUST precede
    # "city" below: "undercity" contains the substring "city", so without this ordering
    # an authored "The Undercity" region would mis-resolve to the tame civilized tier.
    "undercity": 0.45,
    "sewer": 0.42,
    # tame / civilized — patrolled, low risk. URBAN keywords (F04-1) join the original
    # town/city set so a market/tavern/harbor/temple/quarter scene reads as a city
    # street, not a wilderness trail: a Baldur's Gate area ships region="Baldur's Gate"
    # with these tags joined into its notes, and the staging seam matches off the
    # composite "<region> <name> <notes>".
    "town": 0.08,
    "city": 0.08,
    "market": 0.08,
    "tavern": 0.07,
    "harbor": 0.10,
    "harbour": 0.10,
    "dock": 0.10,
    "port": 0.10,
    "wharf": 0.10,
    "slum": 0.12,
    "warren": 0.12,
    "quarter": 0.08,
    "district": 0.08,
    "temple": 0.10,
    "palace": 0.08,
    "village": 0.10,
    "keep": 0.10,
    "road": 0.15,
    "safe": 0.05,
    "haven": 0.05,
    "sanctuary": 0.05,
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
}

# A coarse danger TIER for each region keyword, used only to bias *which* creatures
# get drawn (see REGION_CREATURES / _region_pool). Independent of REGION_RATES so a
# region's frequency and its flavor can be tuned separately.
_REGION_TIER: dict[str, str] = {
    # danger-adjective keywords FIRST (same ordering discipline as REGION_RATES) so
    # compound names like "The Haunted Wood" → undead, not forest.
    "ruin": "undead", "haunted": "undead", "cursed": "undead",
    "shadow": "undead", "blight": "undead", "dread": "undead",
    "deathly": "undead", "dungeon": "undead",
    "underdark": "underdark",
    # urban UNDERGROUND (F04-1) — sewers / the Undercity read as the urban-underground
    # "underdark" creature tier (kobolds/giant spiders/ghouls/ogres — vermin & lurkers),
    # NOT civilized. MUST precede "city" (the substring hazard — "undercity" ⊃ "city").
    "undercity": "underdark", "sewer": "underdark",
    # tame / civilized — patrolled streets. The urban keywords (F04-1) draw from the
    # civilized pool (Bandit/Guard/Scout/Tough/Cultist/Bandit Captain) — a city threat
    # is a cutpurse or a patrol, never a wolf pack.
    "town": "civilized", "city": "civilized", "village": "civilized",
    "market": "civilized", "tavern": "civilized", "harbor": "civilized",
    "harbour": "civilized", "dock": "civilized", "port": "civilized",
    "wharf": "civilized", "slum": "civilized", "warren": "civilized",
    "quarter": "civilized", "district": "civilized", "temple": "civilized",
    "palace": "civilized",
    "keep": "civilized", "road": "civilized", "safe": "civilized",
    "haven": "civilized", "sanctuary": "civilized",
    "marsh": "swamp", "swamp": "swamp", "bog": "swamp", "river": "swamp",
    "coast": "coast",
    "mountain": "mountain", "hill": "mountain", "waste": "mountain",
    "badland": "mountain",
    "frontier": "mountain",  # frontier → mountain tier (rough country, elevated risk)
    "forest": "forest", "wood": "forest", "fell": "forest", "moor": "forest",
    "wild": "forest",  # wild → forest tier (untamed country)
    "border": "civilized",  # border → civilized tier (contested but still a boundary region)
    # plain/field intentionally absent: they fall through to BASE_RATE / "wilderness" tier
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


# =========================================================================
# TYPED encounters — a wandering encounter is no longer ALWAYS a fight.
#
# A QA arc was combat-heavy (10 fights / 14 beats) because `pick_encounter` only
# ever staged combat, and a reach-for test showed the gpt-5.4 DM won't reach for a
# new high-level tool (encounter_outlook / generate_parley_options) — it falls back
# to habitual skill_check/social_check/spawn_monster (the Wave-12 lesson). The fix
# is to fold VARIETY + the balance signal into the trigger that DOES reliably fire:
# `wander`. `pick_typed_encounter` chooses a TYPE first (most of which are NOT
# fights), so travel/camp feels like the open road — a washed-out ford to cross, a
# wary patrol to talk past, a friendly hunter — not a relentless gauntlet.
# =========================================================================

# The encounter TYPES, and the DEFAULT weight table the picker samples. Tuned so
# combat is the PLURALITY (0.40) but the MINORITY — most wandering encounters
# (0.60) are a skill/social/hazard/boon the DM resolves WITHOUT a fight. These are
# the "civilized" baseline; region weighting (below) skews them per locale.
ENCOUNTER_TYPES = ("combat", "skill", "social", "hazard", "boon")

DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    "combat": 0.40,
    "skill": 0.20,
    "social": 0.20,
    "hazard": 0.12,
    "boon": 0.08,
}

# Per-flavor-tier MULTIPLIERS applied to the default weights, then renormalized
# (see `_typed_weights`). Dangerous country pushes combat/hazard UP and social DOWN
# (fewer friendly travelers in a blighted waste); civilized country does the
# reverse (a patrolled road meets people, not monsters). A tier absent here uses
# the default weights unchanged. Keyed by the same flavor tier as the creature pool
# (`_REGION_TIER`), so frequency, creatures, and the type mix all read off region.
_REGION_TYPE_BIAS: dict[str, dict[str, float]] = {
    "civilized": {"combat": 0.5, "social": 2.0, "hazard": 0.6, "boon": 1.5},
    "forest":    {"combat": 1.0, "skill": 1.2, "social": 1.0, "hazard": 1.1},
    "swamp":     {"combat": 1.15, "social": 0.6, "hazard": 1.8, "boon": 0.7},
    "coast":     {"combat": 1.0, "skill": 1.3, "hazard": 1.3},
    "mountain":  {"combat": 1.1, "skill": 1.4, "social": 0.7, "hazard": 1.5, "boon": 0.8},
    "undead":    {"combat": 1.6, "skill": 0.8, "social": 0.3, "hazard": 1.4, "boon": 0.4},
    "underdark": {"combat": 1.6, "social": 0.3, "hazard": 1.6, "boon": 0.4},
    # "wilderness" (the fallback tier) intentionally absent -> default weights.
}

# DC bands by difficulty (mirrors server's `_suggested_dc` / `_PARLEY_DC_BAND` so a
# wandering skill/social/hazard DC matches the engine's other suggested DCs). The
# ±2 house-rules shift is applied by `_banded_dc`. Kept HERE (not imported from the
# server) so `wander` stays a pure, server-free module.
_DC_BAND: dict[str, int] = {"easy": 10, "medium": 14, "hard": 18}
_HOUSE_DC_SHIFT: dict[str, int] = {"hard": 2, "easy": -2}


def _banded_dc(difficulty: str, house_difficulty: str = "standard") -> int:
    """A suggested DC for a skill/social/hazard challenge: the situation band (easy
    10 / med 14 / hard 18) shifted by the campaign's house difficulty (+2 'hard',
    -2 'easy'). Same contract as the server's `_suggested_dc`."""
    base = _DC_BAND.get((difficulty or "").strip().lower(), _DC_BAND["medium"])
    return base + _HOUSE_DC_SHIFT.get((house_difficulty or "").strip().lower(), 0)


# --- Region-flavored NON-COMBAT descriptor palettes -------------------------------
# Each entry is (text, suggested_skill, difficulty_band). The picker draws one for
# the region's flavor tier (falling back to "wilderness"), banding the DC off the
# entry's difficulty so an easy ford reads DC 10 and a hard rockfall DC 18. Skills
# are SRD skill keys (snake_case, as in models.SKILL_ABILITIES); social skills are
# the four parley skills. Content is original WorldOS flavor (no SRD text).

# skill obstacles: a region-flavored barrier the party SKILL-CHECKS past.
_SKILL_OBSTACLES: dict[str, list[tuple[str, str, str]]] = {
    "wilderness": [
        ("a washed-out ford where the trail crosses a swollen creek", "athletics", "medium"),
        ("a deadfall of storm-thrown timber blocking the path", "athletics", "easy"),
        ("a faint game-trail forking three ways with no marker", "survival", "medium"),
        ("a steep scree slope that gives underfoot", "acrobatics", "hard"),
    ],
    "civilized": [
        ("a locked relay-gate on the toll road, the keeper nowhere in sight", "sleight_of_hand", "medium"),
        ("a collapsed bridge plank over a wagon-rutted gully", "athletics", "easy"),
        ("a confusing tangle of waystones with worn-off names", "investigation", "medium"),
    ],
    "forest": [
        ("a washed-out ford where the trail crosses a swollen creek", "athletics", "medium"),
        ("a wall of bramble-choked thicket across the deer-path", "survival", "easy"),
        ("a moss-slick log spanning a ravine", "acrobatics", "hard"),
    ],
    "swamp": [
        ("a sucking stretch of bog with no firm footing", "survival", "hard"),
        ("a rotted boardwalk over black water", "acrobatics", "medium"),
        ("a curtain of biting midges hiding the safe channel", "perception", "medium"),
    ],
    "coast": [
        ("tide-flooded rocks you must time to cross", "athletics", "medium"),
        ("a cliff path crumbling above the surf", "acrobatics", "hard"),
        ("a stranded skiff that could ferry you past the headland", "investigation", "easy"),
    ],
    "mountain": [
        ("a sheer rock chimney barring the pass", "athletics", "hard"),
        ("a snow-bridge over a crevasse of unknown depth", "acrobatics", "hard"),
        ("a switchback buried under a recent rockfall", "athletics", "medium"),
    ],
    "undead": [
        ("a barrow-door sealed with a rusted, rune-scratched lock", "sleight_of_hand", "hard"),
        ("a fog of grave-cold that smothers your sense of direction", "survival", "medium"),
        ("a cracked ossuary floor that won't bear weight", "acrobatics", "medium"),
    ],
    "underdark": [
        ("a chasm split by a single salt-crusted ledge", "acrobatics", "hard"),
        ("a fungal forest whose glow hides the true path", "survival", "hard"),
        ("a flooded passage you must feel your way through", "athletics", "medium"),
    ],
}

# social road-meetings: (description, stance, suggested_social_skill, difficulty).
# stance ∈ {wary, desperate, hostile-but-talkable}. The DM voices the NPC + a Parley
# moment, then social_checks the chosen skill vs the DC.
_SOCIAL_MEETINGS: dict[str, list[tuple[str, str, str, str]]] = {
    "wilderness": [
        ("a lone trapper hauling a laden sled, eyeing your weapons", "wary", "persuasion", "medium"),
        ("a footsore pilgrim begging news of the road ahead", "desperate", "insight", "easy"),
        ("a toll-taker's bravo who 'collects' from passers-by", "hostile-but-talkable", "intimidation", "medium"),
    ],
    "civilized": [
        ("a militia patrol that stops you for questioning", "wary", "persuasion", "medium"),
        ("a beggar with a too-sharp eye for your purse", "desperate", "insight", "easy"),
        ("a merchant's outrider who mistakes you for bandits", "hostile-but-talkable", "persuasion", "medium"),
        ("a tax-farmer demanding a road levy you may not owe", "hostile-but-talkable", "deception", "hard"),
    ],
    "forest": [
        ("a ranger who challenges your right to cross her wood", "wary", "persuasion", "medium"),
        ("a charcoal-burner desperate for help finding his lost boy", "desperate", "insight", "easy"),
        ("a poacher who'd rather you didn't see his snares", "hostile-but-talkable", "intimidation", "medium"),
    ],
    "swamp": [
        ("a fen-witch's gaunt servant, wary of strangers", "wary", "persuasion", "medium"),
        ("a half-drowned smuggler begging to be pulled from the mire", "desperate", "insight", "easy"),
        ("a bog-raider sizing up whether you're worth the fight", "hostile-but-talkable", "intimidation", "hard"),
    ],
    "coast": [
        ("a fisherfolk crew suspicious of overland strangers", "wary", "persuasion", "medium"),
        ("a shipwreck survivor pleading for water and aid", "desperate", "insight", "easy"),
        ("a wrecker who'd sooner rob you than guide you", "hostile-but-talkable", "intimidation", "medium"),
    ],
    "mountain": [
        ("a hill-clan scout blocking the pass, hand on axe", "wary", "intimidation", "medium"),
        ("a snow-blind drover who's lost his whole flock", "desperate", "insight", "easy"),
        ("a toll-troll's small kin who'll bargain before brawling", "hostile-but-talkable", "deception", "hard"),
    ],
    "undead": [
        ("a grave-warden who demands to know why you disturb the dead", "wary", "persuasion", "hard"),
        ("a trapped spirit begging release from its barrow", "desperate", "insight", "medium"),
        ("a tomb-robber who'd rather you weren't a witness", "hostile-but-talkable", "intimidation", "medium"),
    ],
    "underdark": [
        ("a deep-gnome trader who trusts no surface-dweller", "wary", "persuasion", "hard"),
        ("a lost surface-scout half-mad for a way back up", "desperate", "insight", "medium"),
        ("a duergar patrol weighing toll against bloodshed", "hostile-but-talkable", "intimidation", "hard"),
    ],
}

# hazards: (peril, save_or_skill, difficulty). save_or_skill is an SRD ability
# (str/dex/con/int/wis/cha — a SAVE) OR a skill key (survival/perception/…), the
# DM's choice of how to avoid it. The DM runs a saving_throw or skill_check vs DC.
_HAZARDS: dict[str, list[tuple[str, str, str]]] = {
    "wilderness": [
        ("a sudden squall drives sleet and chill across the open ground", "con", "medium"),
        ("a concealed pit-burrow waiting to snap a careless ankle", "perception", "easy"),
        ("a flash-flood surge funnels down the dry wash you're crossing", "dex", "hard"),
    ],
    "civilized": [
        ("a runaway draft-team thunders down the road", "dex", "medium"),
        ("a rotten footbridge that gives way mid-span", "dex", "easy"),
    ],
    "forest": [
        ("a wildfire's choking smoke rolls through the canopy", "con", "medium"),
        ("a hornet-nest the size of a barrel, disturbed", "dex", "easy"),
        ("a rope-snare poacher's trap underfoot", "perception", "medium"),
    ],
    "swamp": [
        ("a sinkhole of quicksand opens beneath the leading foot", "dex", "hard"),
        ("a miasma of fever-vapor rises off the standing water", "con", "medium"),
        ("a nest of leeches in the channel you must wade", "con", "easy"),
    ],
    "coast": [
        ("a rogue wave sweeps the tide-flat without warning", "dex", "hard"),
        ("a fog bank rolls in, hiding the cliff edge", "perception", "medium"),
    ],
    "mountain": [
        ("a rockfall sheers loose off the slope above", "dex", "hard"),
        ("a sudden whiteout of driving snow", "con", "medium"),
        ("a hidden cornice cracks at the cliff's lip", "perception", "medium"),
    ],
    "undead": [
        ("a wave of grave-chill saps the warmth from your bones", "con", "hard"),
        ("a sinking crypt-floor drops toward the dark below", "dex", "medium"),
        ("a curse-glyph flares where a careless boot crosses it", "wis", "hard"),
    ],
    "underdark": [
        ("a pocket of cave-damp steals the breath from the passage", "con", "hard"),
        ("a ceiling slab shears free in the dark", "dex", "hard"),
        ("a phosphor-blast of disturbed spore-fungus", "con", "medium"),
    ],
}

# boons: a small POSITIVE find. No resolution — the DM narrates it. (text only.)
_BOONS: dict[str, list[str]] = {
    "wilderness": [
        "a hunter's cache of cured meat and clean water, freely shared",
        "a friendly drover who points out a half-day shortcut",
        "a sheltered hollow, dry firewood already stacked",
    ],
    "civilized": [
        "a waystation with a warm hearth and a generous keeper",
        "a courier headed your way who shares the road and the news",
        "a roadside shrine whose offerings-box holds a few forgotten coins",
    ],
    "forest": [
        "a glade of ripe berries and a clear spring",
        "a woodwise hermit who marks a safe path on your map",
        "a hollow oak stocked with a forager's hidden cache",
    ],
    "swamp": [
        "a dry hummock with a poacher's stashed punt",
        "a fen-guide willing to lead you to firm ground",
        "a stand of healthful marsh-herbs ripe for picking",
    ],
    "coast": [
        "a beached net heavy with the morning's catch, free for the taking",
        "a lighthouse-keeper who waves you toward the safe channel",
        "a tide-pool larder of mussels and a freshwater seep",
    ],
    "mountain": [
        "a shepherd's bothy with a stocked woodpile",
        "a sure-footed goatherd who shows you the easy switchback",
        "a sun-warmed ledge with a clear spring and a wide view",
    ],
    "undead": [
        "a grave-offering of preserved rations, untouched and still good",
        "a warding-stone whose old blessing still keeps a circle of calm",
        "a fallen pilgrim's pack with a flask of holy water inside",
    ],
    "underdark": [
        "a vein of luminous fungus that lights the next stretch of tunnel",
        "a cached water-skin and torches left by an earlier expedition",
        "a deep-gnome trail-sign pointing to a safe rest-cave",
    ],
}


def _region_tier(region: str) -> str:
    """The flavor tier for `region` (forest/swamp/undead/…), or 'wilderness' when no
    keyword matches — the key into the typed descriptor palettes + the type bias."""
    keyword = _match_keyword(region, _REGION_TIER)
    return _REGION_TIER[keyword] if keyword is not None else "wilderness"


def _descriptor_pool(region: str, table: dict[str, list]) -> list:
    """The descriptor list for `region`'s flavor tier from one of the palette tables,
    falling back to the 'wilderness' list for an unrecognized tier."""
    tier = _region_tier(region)
    return table.get(tier) or table["wilderness"]


def _typed_weights(region: str, weights: dict[str, float] | None = None) -> dict[str, float]:
    """The per-type sampling weights for `region`: the base weights (caller-supplied
    `weights`, else `DEFAULT_TYPE_WEIGHTS`) times the region's per-type bias
    multipliers (`_REGION_TYPE_BIAS` for the flavor tier; 1.0 for any type/tier not
    listed). Non-negative and not renormalized to 1 (the caller samples proportional
    to whatever the values sum to), but any type missing from `weights` is treated
    as 0 so a caller can pass a partial table to suppress a type entirely."""
    base = weights if weights is not None else DEFAULT_TYPE_WEIGHTS
    bias = _REGION_TYPE_BIAS.get(_region_tier(region), {})
    out: dict[str, float] = {}
    for t in ENCOUNTER_TYPES:
        w = max(0.0, float(base.get(t, 0.0)))
        out[t] = w * bias.get(t, 1.0)
    return out


def _weighted_choice(weights: dict[str, float], r: random.Random) -> str:
    """Pick a key proportional to its weight. Falls back to 'combat' when every
    weight is zero/empty (so the picker always returns a usable type)."""
    items = [(k, v) for k, v in weights.items() if v > 0]
    if not items:
        return "combat"
    total = sum(v for _, v in items)
    roll = r.random() * total
    upto = 0.0
    for k, v in items:
        upto += v
        if roll < upto:
            return k
    return items[-1][0]  # float-rounding guard


def pick_typed_encounter(
    party_levels: list[int],
    region: str = "",
    rng: random.Random | None = None,
    weights: dict[str, float] | None = None,
    target_difficulty: str = "medium",
    house_difficulty: str = "standard",
) -> dict:
    """Choose a TYPED wandering encounter for `region` — most of which are NOT fights.

    First samples a TYPE from `_typed_weights(region, weights)` (the default mix is
    combat 0.40 / skill 0.20 / social 0.20 / hazard 0.12 / boon 0.08, skewed per
    region: dangerous country -> more combat/hazard, civilized -> more social/boon),
    then fills in the type-specific fields:

      * ``{"type": "combat", "foes": [<pick_encounter specs>]}`` — region foes sized
        to the party (reuses `pick_encounter`; the engine seam folds in the outlook).
      * ``{"type": "skill", "challenge", "skill", "dc"}`` — a region obstacle + a
        suggested SKILL + a DC banded off the obstacle's difficulty.
      * ``{"type": "social", "who", "stance", "skill", "dc"}`` — a road-meeting + a
        stance (wary/desperate/hostile-but-talkable) + a social skill + DC.
      * ``{"type": "hazard", "peril", "save_or_skill", "dc"}`` — an environmental
        danger + an ability SAVE or skill to avoid it + DC.
      * ``{"type": "boon", "find"}`` — a small positive find. No resolution.

    DCs use the same band as the engine's other suggested DCs (easy 10 / med 14 /
    hard 18, shifted +2/-2 by `house_difficulty`). Deterministic under a seeded
    `rng`. A combat pick with an empty party / unresolvable region pool degrades to a
    boon (so the contract always returns a usable typed dict, never `[]`)."""
    r = rng or random.Random()
    etype = _weighted_choice(_typed_weights(region, weights), r)

    if etype == "combat":
        specs = pick_encounter(party_levels, region, target_difficulty=target_difficulty, rng=r)
        if not specs:
            # Nothing to fight (empty party / no resolvable creature) — never stage an
            # empty combat; fall through to a guaranteed-content boon.
            etype = "boon"
        else:
            return {"type": "combat", "foes": specs}

    if etype == "skill":
        text, skill, band = r.choice(_descriptor_pool(region, _SKILL_OBSTACLES))
        return {
            "type": "skill",
            "challenge": text,
            "skill": skill,
            "dc": _banded_dc(band, house_difficulty),
        }

    if etype == "social":
        who, stance, skill, band = r.choice(_descriptor_pool(region, _SOCIAL_MEETINGS))
        return {
            "type": "social",
            "who": who,
            "stance": stance,
            "skill": skill,
            "dc": _banded_dc(band, house_difficulty),
        }

    if etype == "hazard":
        peril, save_or_skill, band = r.choice(_descriptor_pool(region, _HAZARDS))
        return {
            "type": "hazard",
            "peril": peril,
            "save_or_skill": save_or_skill,
            "dc": _banded_dc(band, house_difficulty),
        }

    # boon (the fallback type)
    return {"type": "boon", "find": r.choice(_descriptor_pool(region, _BOONS))}
