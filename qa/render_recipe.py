#!/usr/bin/env python3
"""render_recipe(geometry) — paint-recipe STRUCTURAL clauses generated FROM the geometry JSON.

Issue #1619. Two of six dwing cycle failures were recipe-AUTHORING bugs: a shared "ALTAR where
present" clause invited invented altars into altar-less rooms, and a rewritten entry diluted the
proven shape lock — ~190 CU + a day of confusion. Every one of those clauses described facts that
already live machine-readable in qa/room_geometries/*.json. The rule (the paint slot-swap
postmortem, applied to the prompts themselves): NOBODY FREEHANDS WHAT A PROGRAM CAN PIN.

This module pins, deterministically, from the geometry JSON alone:
  FEATURE COUNTS (strict)  — pillars/braziers/altar/door counts (doors carry their wall sides),
  ROOM SHAPE (strict)      — from the cols x rows aspect,
  DOOR WALLS (strict)      — from door_cells -> perimeter side,
  FOCAL PLACEMENT (strict) — brazier/altar placement from focal prop cells,
  NEGATIVE clauses         — NO altar unless an altar prop exists; SINGLE-FLAT-LEVEL always.

Hand-authoring shrinks to ONE flavor sentence per room class (qa/unified_paint_recipes.json
"flavor"/"gemini_flavor"); qa/paint_room.py --geometry composes flavor + this structural block
at paint time. Without --geometry the static recipes are used verbatim (additive, VISION).

Pure stdlib, no Scenario/LLM calls — the zero-CU lane. Wire contracts untouched.
"""
from __future__ import annotations

# Prop kinds that read as structural pillars/columns on the painted plate. The crypt/throne
# geometries author them as "stone_pillar", the dwing/tavern ones as "pillar" — both pin.
_PILLAR_KINDS = {"pillar", "stone_pillar"}
_FOCAL_KINDS = ("altar", "brazier")  # clause order is deterministic: altar first, then braziers

_NUM_WORDS = {
    1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX",
    7: "SEVEN", 8: "EIGHT", 9: "NINE", 10: "TEN", 11: "ELEVEN", 12: "TWELVE",
}

# Aspect thresholds for the ROOM SHAPE lock. 12x7 (=1.71) is the proven "wide vault" dwing case;
# 12x13 (=0.92) must stay "roughly square". Symmetric margins keep the band deterministic.
_WIDE_ASPECT = 1.3
_DEEP_ASPECT = 1.0 / _WIDE_ASPECT


def _num(n: int) -> str:
    return _NUM_WORDS.get(n, str(n))


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


def _count_kinds(geometry: dict) -> dict:
    counts: dict = {"pillar": 0, "altar": 0, "brazier": 0}
    for prop in geometry.get("props", []):
        kind = prop.get("kind", "")
        if kind in _PILLAR_KINDS:
            counts["pillar"] += 1
        elif kind in counts:
            counts[kind] += 1
    return counts


def _door_walls(geometry: dict) -> list:
    """door_cells -> perimeter side, in input order. y==0 is the BACK (north) wall,
    y==rows-1 the FRONT (south), x==0 the LEFT (west), x==cols-1 the RIGHT (east)."""
    cols, rows = int(geometry["cols"]), int(geometry["rows"])
    walls = []
    for x, y in geometry.get("door_cells", []):
        if y == 0:
            walls.append("BACK (north)")
        elif y == rows - 1:
            walls.append("FRONT (south)")
        elif x == 0:
            walls.append("LEFT (west)")
        elif x == cols - 1:
            walls.append("RIGHT (east)")
        else:  # a door not on the perimeter is a geometry bug — pin it loud, never guess
            raise ValueError(f"door cell [{x}, {y}] is not on the {cols}x{rows} perimeter")
    return walls


def _centroid(cells: list) -> tuple:
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _position_words(geometry: dict, cells: list) -> tuple:
    """Deterministic zone words for a prop's centroid: (horizontal, vertical)."""
    cx = (int(geometry["cols"]) - 1) / 2.0
    cy = (int(geometry["rows"]) - 1) / 2.0
    fx, fy = _centroid(cells)
    horizontal = ("west-of-centre" if fx < cx - 0.5 else
                  "east-of-centre" if fx > cx + 0.5 else "centre")
    vertical = ("back half" if fy < cy - 0.5 else
                "front half" if fy > cy + 0.5 else "mid-depth")
    return horizontal, vertical


def _feature_counts(counts: dict, door_walls: list) -> str:
    parts = []
    if counts["pillar"]:
        parts.append(f"EXACTLY {_num(counts['pillar'])} "
                     f"{_plural(counts['pillar'], 'stone pillar')}")
    if counts["altar"]:
        parts.append(f"EXACTLY {_num(counts['altar'])} "
                     f"{_plural(counts['altar'], 'altar')}")
    if counts["brazier"]:
        # "BLAZING" is load-bearing, not flavor: the flux base must DRAW the fire lit for the
        # brazier beacons to be detectable — a base with unlit bowls forces the styled pass to
        # INVENT fire with count/position liberties (measured b1-b3: 6/6 unlit bases on two rooms
        # until the fire attribute was restored; the hand statics always said "glowing coals").
        parts.append(f"EXACTLY {_num(counts['brazier'])} "
                     f"{_plural(counts['brazier'], 'brazier')}, each holding a BLAZING FIRE "
                     "with bright flames and glowing coals")
    n_doors = len(door_walls)
    if n_doors:
        sides = " + ".join(f"{w} wall" for w in door_walls)
        parts.append(f"EXACTLY {_num(n_doors)} "
                     f"{_plural(n_doors, 'arched doorway')} ({sides})")
    counts_str = ", ".join(parts) if parts else "no structural features"
    return (f"FEATURE COUNTS (strict): {counts_str}; every other wall surface is unbroken "
            "solid — NO other doorways, arches, gates or openings, NO other pillars or columns.")


def _shape_clause(geometry: dict) -> str:
    cols, rows = int(geometry["cols"]), int(geometry["rows"])
    aspect = cols / rows
    if aspect >= _WIDE_ASPECT:
        shape = (f"the chamber is markedly WIDER than it is deep — a low broad room roughly "
                 f"{round(aspect, 1)}x as wide as it is deep")
    elif aspect <= _DEEP_ASPECT:
        shape = (f"the chamber is markedly DEEPER than it is wide — roughly "
                 f"{round(rows / cols, 1)}x as deep as it is wide")
    else:
        shape = "the chamber is roughly SQUARE — about as wide as it is deep"
    return (f"ROOM SHAPE (strict): {shape}; preserve the input image's exact room proportions, "
            "floor extent and camera framing (the output must overlay the input).")


def _door_clause(door_walls: list) -> str:
    n = len(door_walls)
    listed = " and ".join(f"the {w} wall" for w in door_walls)
    return (f"DOOR WALLS (strict): the {_num(n)} "
            f"{_plural(n, 'arched doorway')} "
            f"{'is' if n == 1 else 'are'} in {listed}; every other wall surface is unbroken "
            "solid with NO openings.")


def _focal_clause(geometry: dict, counts: dict) -> str | None:
    focus = {k: [p for p in geometry.get("props", []) if p.get("kind") == k]
             for k in _FOCAL_KINDS if counts[k]}
    if not focus:
        return None
    sentences = []
    if focus.get("altar"):
        horizontal, vertical = _position_words(geometry, focus["altar"][0]["cells"])
        sentences.append(f"the altar stands {vertical}, {horizontal}")
    braziers = focus.get("brazier", [])
    if braziers:
        positions = [_position_words(geometry, p["cells"]) for p in braziers]
        n = len(braziers)
        verb = "stands" if n == 1 else "stand"
        if (n == 2 and {p[0] for p in positions} == {"west-of-centre", "east-of-centre"}):
            _side_order = {"west-of-centre": 0, "centre": 1, "east-of-centre": 2}
            ordered = sorted(positions, key=lambda p: _side_order[p[0]])
            detail = " — ".join([""] + [f"one {h} ({v})" for h, v in ordered])
            sentences.append(f"the {_num(n)} braziers stand flanking the central walking "
                             f"lane{detail}")
        else:
            detail = "; ".join(f"{v}, {h}" for h, v in positions)
            sentences.append(f"the {_num(n)} {_plural(n, 'brazier')} {verb}: {detail}")
    title = "FOCAL PLACEMENT (strict)" if focus.get("altar") else "BRAZIER PLACEMENT (strict)"
    return (f"{title}: {'; '.join(sentences)} — exactly where the input image shows them, "
            "every brazier LIT with visible bright flames; do NOT move them and do NOT add more.")


def structural_block(geometry: dict) -> str:
    """The full deterministic structural block for a room geometry JSON."""
    counts = _count_kinds(geometry)
    door_walls = _door_walls(geometry)
    clauses = [_feature_counts(counts, door_walls)]
    if not counts["altar"]:
        clauses.append(
            "NEGATIVE (strict): there is NO altar, NO shrine, NO carved stone chest or table "
            "anywhere in this room — the only stone masses are the pillar(s), the braziers' "
            "iron stands and the wall masonry; do NOT add furniture that is not in the input "
            "image.")
    clauses.append(
        "SINGLE FLAT LEVEL (strict): the floor is ONE SINGLE FLAT LEVEL — NO sunken areas, NO "
        "water channels, cisterns, pools or canals, NO staircases DOWN, NO pits or lower "
        "levels; every floor cell shown in the input stays dry, flat walkable floor.")
    clauses.append(_shape_clause(geometry))
    if door_walls:
        clauses.append(_door_clause(door_walls))
    focal = _focal_clause(geometry, counts)
    if focal:
        clauses.append(focal)
    return " ".join(clauses)


def render_recipe(geometry: dict, flavor: str = "", gemini_flavor: str = "") -> dict:
    """geometry JSON -> {"base_prompt", "gemini_grounding", "structural_block"}.

    The generated structural block is IDENTICAL in both prompts (the structure lock must not
    drift between passes); the per-class hand-authored flavor is the only prose prepended.
    """
    block = structural_block(geometry)
    base = f"{flavor.rstrip()} {block}".strip()
    grounding = f"{gemini_flavor.rstrip()} {block}".strip()
    return {"base_prompt": base, "gemini_grounding": grounding, "structural_block": block}
