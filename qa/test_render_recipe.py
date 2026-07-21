"""Red-first tests for render_recipe(geometry) — issue #1619.

THE BUG CLASS THIS KILLS: two of six dwing cycle failures were recipe-AUTHORING bugs (a shared
"ALTAR where present" clause invited invented altars into altar-less rooms; a rewritten entry
diluted the proven shape lock) — ~190 CU + a day lost. Every structural clause was hand-composed
prose describing facts that already live machine-readable in the room geometry JSON. The rule
(the paint slot-swap postmortem, applied to prompts): NOBODY FREEHANDS WHAT A PROGRAM CAN PIN.

render_recipe(geometry) emits DETERMINISTICALLY from the geometry JSON:
  FEATURE COUNTS (strict)  — pillars/braziers/altar/doors (doors with their wall sides),
  ROOM SHAPE (strict)      — from cols x rows aspect,
  DOOR WALLS (strict)      — from door_cells -> perimeter side,
  FOCAL PLACEMENT (strict) — brazier/altar placement from focal prop cells,
  NEGATIVE clauses         — no altar unless an altar prop exists; SINGLE-FLAT-LEVEL always.

These tests pin all of that against the REAL shipped geometry JSONs, plus the VISION additive
fallback: paint_room with no --geometry resolves the recipe class BYTE-IDENTICALLY to today.

Deterministic, no LLM, no Scenario calls — the zero-CU lane.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))
# paint_room's own import seam (scenario_gen helpers live beside the renderer tools).
sys.path.insert(0, str(_QA_DIR.parent / "extensions" / "renderers" / "godot" / "tools"))

import paint_room  # noqa: E402
import render_recipe  # noqa: E402  (the module under test — RED until implemented)

_GEOMS = _QA_DIR / "room_geometries"
_RECIPES = json.loads((_QA_DIR / "unified_paint_recipes.json").read_text())

# Structural markers the generator (qa/render_recipe.py) SOLELY owns. If any of these creep back
# into a hand-authored flavor key, the flavor is duplicating — and will eventually contradict —
# the generated structural block (the exact P1 the review caught on dwing_room_0/1). Compared
# case-insensitively: the generated block writes them lower-case, the old prose SHOUTED them.
_FLAVOR_STRUCTURAL_KEYWORDS = (
    "FEATURE COUNTS", "doorway", "brazier", "BRAZIER", "pillar", "PILLAR",
    "flank", "as wide", "as deep", "wall", "door",
)


def _geom(name: str) -> dict:
    return json.loads((_GEOMS / name).read_text())


def _block(geometry: dict) -> str:
    return render_recipe.render_recipe(geometry)["structural_block"]


# ── FEATURE COUNTS (strict) — from prop kinds + door_cells ──────────────────────

def test_feature_counts_dwing_room_0():
    block = _block(_geom("dwing_room_0_geometry.json"))
    assert "FEATURE COUNTS (strict)" in block
    assert "EXACTLY TWO stone pillars" in block          # anchor_a + anchor_b (kind "pillar")
    assert "EXACTLY ONE altar" in block                  # focal_altar
    assert "EXACTLY TWO braziers" in block               # focal_brazier_w/e
    assert "EXACTLY ONE arched doorway" in block         # door_cells [[11, 6]]


def test_feature_counts_dwing_room_1():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "EXACTLY ONE stone pillar" in block           # anchor_a
    assert "EXACTLY TWO braziers" in block               # focal_brazier_w/e
    assert "EXACTLY TWO arched doorways" in block        # door_cells [[0,3],[11,3]]
    assert "altar" not in block.split("NEGATIVE")[0].lower().replace("no altar", ""), \
        "the counts block must not claim an altar that the geometry does not have"


def test_feature_counts_crypt_stone_pillar_kind():
    # The crypt geometry uses kind "stone_pillar" (not "pillar") — both count as pillars.
    block = _block(_geom("crypt_v36_geometry.json"))
    assert "EXACTLY FOUR stone pillars" in block
    assert "EXACTLY FOUR braziers" in block
    assert "EXACTLY TWO arched doorways" in block


def test_counts_close_with_no_other_openings_clause():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "NO other doorways" in block
    assert "NO other pillars or columns" in block


# ── DOOR WALLS (strict) — door_cells -> perimeter side ──────────────────────────

def test_door_walls_dwing_room_0_single_east():
    block = _block(_geom("dwing_room_0_geometry.json"))
    assert "DOOR WALLS (strict)" in block
    assert "RIGHT (east)" in block                       # x == cols-1
    assert "LEFT (west)" not in block


def test_door_walls_dwing_room_1_west_and_east():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "LEFT (west)" in block                        # x == 0
    assert "RIGHT (east)" in block                       # x == cols-1


def test_door_walls_crypt_back_and_right():
    block = _block(_geom("crypt_v36_geometry.json"))
    assert "BACK (north)" in block                       # y == 0
    assert "RIGHT (east)" in block                       # x == cols-1


def test_door_walls_declares_remaining_walls_unbroken():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "unbroken solid" in block
    assert "NO openings" in block


# ── ROOM SHAPE (strict) — cols x rows aspect ────────────────────────────────────

def test_room_shape_wide_vault_dwing_room_1():
    # 12 cols x 7 rows — the shape-lock clause a hand rewrite diluted (the second failure).
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "ROOM SHAPE (strict)" in block
    assert "WIDER than it is deep" in block
    assert "overlay the input" in block


def test_room_shape_roughly_square_dwing_room_0():
    # 12 cols x 13 rows — near-square must NOT be called a wide vault.
    block = _block(_geom("dwing_room_0_geometry.json"))
    assert "ROOM SHAPE (strict)" in block
    assert "SQUARE" in block
    assert "WIDER than it is deep" not in block


# ── FOCAL PLACEMENT (strict) — brazier/altar cells ──────────────────────────────

def test_brazier_placement_dwing_room_1_flanks_central_lane():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "BRAZIER PLACEMENT (strict)" in block
    assert "flanking the central walking lane" in block  # one west-of-centre + one east-of-centre
    assert "west-of-centre" in block
    assert "east-of-centre" in block
    assert "do NOT move them" in block


def test_focal_placement_dwing_room_0_altar_back_centre():
    block = _block(_geom("dwing_room_0_geometry.json"))
    assert "altar" in block.lower()
    # altar cells [[5,4],[6,4]] in a 12x13 room: centre-x, back half.
    assert "back" in block.lower()


# ── NEGATIVE clauses — derived, never freehanded ────────────────────────────────

def test_negative_no_altar_when_geometry_has_none():
    block = _block(_geom("dwing_room_1_geometry.json"))
    assert "NEGATIVE (strict)" in block
    assert "NO altar" in block                           # the clause that kills invented altars


def test_no_altar_negative_when_geometry_has_altar():
    block = _block(_geom("dwing_room_0_geometry.json"))
    assert "NEGATIVE (strict): there is NO altar" not in block
    assert "EXACTLY ONE altar" in block                  # positive pin instead


def test_single_flat_level_always_emitted():
    for name in ("dwing_room_0_geometry.json", "dwing_room_1_geometry.json",
                 "crypt_v36_geometry.json"):
        block = _block(_geom(name))
        assert "SINGLE FLAT LEVEL" in block, name


# ── Contract: return shape, flavor composition, determinism ─────────────────────

def test_return_contract_keys():
    out = render_recipe.render_recipe(_geom("dwing_room_1_geometry.json"))
    assert set(out) >= {"base_prompt", "gemini_grounding", "structural_block"}
    assert out["structural_block"] in out["base_prompt"]
    assert out["structural_block"] in out["gemini_grounding"]


def test_flavor_is_composed_around_the_generated_block():
    out = render_recipe.render_recipe(
        _geom("dwing_room_1_geometry.json"), flavor="FLAVOR.", gemini_flavor="GFLAVOR.")
    assert out["base_prompt"].startswith("FLAVOR.")
    assert out["gemini_grounding"].startswith("GFLAVOR.")
    assert "FEATURE COUNTS (strict)" in out["base_prompt"]
    assert "FEATURE COUNTS (strict)" in out["gemini_grounding"]


def test_render_is_deterministic_byte_identical():
    geom = _geom("dwing_room_1_geometry.json")
    a = render_recipe.render_recipe(geom, flavor="F", gemini_flavor="G")
    b = render_recipe.render_recipe(geom, flavor="F", gemini_flavor="G")
    assert a == b


# ── paint_room wiring — the VISION additive fallback ────────────────────────────

def test_no_geometry_fallback_is_byte_identical_to_today():
    # No --geometry => the recipe class is used VERBATIM (today's behavior, additive invariant).
    cls = _RECIPES["classes"]["dwing_room_1"]
    resolved = paint_room.resolve_recipe(cls, None)
    assert resolved is cls
    assert resolved["base_prompt"] == cls["base_prompt"]
    assert resolved["gemini_grounding"] == cls["gemini_grounding"]


def test_every_recipe_class_keeps_its_static_prompt_keys():
    for name, cls in _RECIPES["classes"].items():
        assert cls["base_prompt"] and cls["gemini_grounding"], name


def test_dwing_recipes_carry_hand_authored_flavor_keys():
    # The ONLY prose left to hand-author: the per-class flavor sentence (#1619) — style/mood/
    # atmosphere ONLY. Counts, shape, door walls and placement belong to the generator.
    for name in ("dwing_room_0", "dwing_room_1"):
        cls = _RECIPES["classes"][name]
        assert cls.get("flavor"), name
        assert cls.get("gemini_flavor"), name
        assert "FEATURE COUNTS" not in cls["flavor"], "structure belongs to the generator"
        assert "FEATURE COUNTS" not in cls["gemini_flavor"], "structure belongs to the generator"
        lowered = cls["flavor"].lower()
        for keyword in _FLAVOR_STRUCTURAL_KEYWORDS:
            assert keyword.lower() not in lowered, (
                f"{name}.flavor carries generator-owned structure ({keyword.lower()!r}) — "
                "flavor is style/mood only; render_recipe.py owns counts/shape/doors/placement"
            )


def test_paint_room_composes_from_geometry_without_mutating_the_recipe():
    cls = copy.deepcopy(_RECIPES["classes"]["dwing_room_1"])
    snapshot = copy.deepcopy(cls)
    composed = paint_room.resolve_recipe(cls, str(_GEOMS / "dwing_room_1_geometry.json"))
    assert cls == snapshot, "composition must not mutate the loaded recipes"
    assert composed["base_prompt"].startswith(cls["flavor"])
    assert "EXACTLY ONE stone pillar" in composed["base_prompt"]
    assert "LEFT (west)" in composed["base_prompt"]
    assert "NO altar" in composed["gemini_grounding"]
    assert composed["base_prompt"] != snapshot["base_prompt"]


def test_geometry_path_requiring_missing_flavor_fails_loud():
    cls = {"base_prompt": "x", "gemini_grounding": "y"}  # no flavor keys
    try:
        paint_room.resolve_recipe(cls, str(_GEOMS / "dwing_room_1_geometry.json"))
    except SystemExit as e:
        assert "flavor" in str(e)
    else:
        raise AssertionError("missing flavor keys must fail loud, not silently freehand")
