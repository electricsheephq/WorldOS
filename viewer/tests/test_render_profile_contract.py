"""M0 #428 (groundwork) — render-profile core-only conformance, dependency-free.

Validates the spike's example render-profile against the formal schema
(docs/roadmap/contracts/render-profile.schema.json) so the contract has CI teeth from day
one. Deliberately uses NO third-party validator (jsonschema may be absent in the viewer-tests
lane): it performs the strict checks that matter for the layered contract —
additionalProperties:false at the load-bearing objects, required fields, enums, and the
zones-not-xy / FK rules. If jsonschema IS installed, it additionally runs a full validate.

This is the seed of the full core-only conformance test (#428): once a real renderer exists,
extend it to assert the renderer can draw every scene from CORE + its own block.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCHEMA = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"
_EXAMPLE = _REPO / "spikes" / "m0-phaser-thin-client" / "render-profile.example.json"


def _load(p: Path) -> dict:
    if not p.exists():
        pytest.skip(f"{p} not present in this checkout")
    return json.loads(p.read_text())


def test_schema_and_example_are_valid_json():
    schema = _load(_SCHEMA)
    ex = _load(_EXAMPLE)
    assert schema.get("$id", "").endswith("render-profile.schema.json")
    assert ex.get("schema_version") == 1


def test_example_has_no_disallowed_keys_at_strict_objects():
    """The layered contract's value comes from strictness: a typo'd or drifted key must be
    caught. Enforce additionalProperties:false manually at top-level + core + the nested
    location/actor objects (the objects the schema marks strict)."""
    schema = _load(_SCHEMA)
    ex = _load(_EXAMPLE)

    top_allowed = set(schema["properties"])
    assert set(ex) - top_allowed == set(), f"disallowed top-level keys: {set(ex) - top_allowed}"

    core_schema = schema["properties"]["core"]
    core_allowed = set(core_schema["properties"])
    assert set(ex["core"]) - core_allowed == set(), f"disallowed core keys: {set(ex['core']) - core_allowed}"

    loc_allowed = set(core_schema["properties"]["locations"]["items"]["properties"])
    for loc in ex["core"]["locations"]:
        assert set(loc) - loc_allowed == set(), f"disallowed location keys: {set(loc) - loc_allowed}"

    act_allowed = set(core_schema["properties"]["actors"]["items"]["properties"])
    for act in ex["core"]["actors"]:
        assert set(act) - act_allowed == set(), f"disallowed actor keys: {set(act) - act_allowed}"


def test_example_honors_core_required_fields_and_enums():
    ex = _load(_EXAMPLE)
    core = ex["core"]
    assert core["scene_kind"] in ("tilemap", "backdrop")
    # v1 positioning is theater|zone ONLY — grid is the evidence-gated future epic (#461).
    assert core["positioning"] in ("theater", "zone"), "v1 must not use grid positioning"
    assert core["locations"] and all("engine_location_id" in l for l in core["locations"])
    assert core["actors"] and all("engine_actor_id" in a for a in core["actors"])


def test_zones_are_named_strings_not_coordinates():
    """The contract carries NAMED zones, never x,y. Lock it: every zone is a non-empty
    string, and no location entry smuggles a coordinate field."""
    ex = _load(_EXAMPLE)
    forbidden = {"x", "y", "col", "row", "grid_x", "grid_y", "coords", "position"}
    for loc in ex["core"]["locations"]:
        assert forbidden.isdisjoint(loc), f"coordinate field leaked into a location: {loc}"
        for z in loc.get("zones", []):
            assert isinstance(z, str) and z.strip(), f"zone is not a named string: {z!r}"


def test_full_jsonschema_validation_when_available():
    """If jsonschema is installed, run a full strict validate as a bonus. Skips cleanly when
    the dep is absent so the lane stays green dependency-free."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = _load(_SCHEMA)
    ex = _load(_EXAMPLE)
    jsonschema.validate(ex, schema)


# --- GT2 Godot reference/extension renderer block (#1051/#1165) -----------------------------
_GODOT_EXAMPLE = _REPO / "docs" / "roadmap" / "contracts" / "examples" / "render-profile.godot.reference.json"
_FACING_KEYS = {"facing", "direction", "orientation", "heading"}


def test_schema_exposes_optional_godot_renderer_block():
    """The Godot client (GT2) is a 4th renderer of the SAME contract. Adding it is a deliberate
    edit to the closed renderer_profiles object (additionalProperties:false) — assert the block
    exists as a sibling of phaser/rpgmaker and is OPTIONAL (renderer_profiles requires no block),
    so Phaser-only profiles stay valid."""
    schema = _load(_SCHEMA)
    rp = schema["properties"]["renderer_profiles"]
    assert {"phaser", "godot", "rpgmaker"} <= set(rp["properties"])
    assert "required" not in rp, "renderer_profiles must not require any renderer block"
    # the existing phaser-only example carries no godot block and must stay valid
    assert "godot" not in _load(_EXAMPLE).get("renderer_profiles", {})


def test_core_has_no_engine_facing_field():
    """Facing is 100% renderer-DERIVED — the engine is the sole writer of game STATE and must
    NEVER gain a facing field. Lock it at the contract: no facing/direction/orientation/heading
    key may appear in core actor or location items (it lives only in the godot renderer block as
    presentation layout)."""
    core = _load(_SCHEMA)["properties"]["core"]["properties"]
    act_props = set(core["actors"]["items"]["properties"])
    loc_props = set(core["locations"]["items"]["properties"])
    assert _FACING_KEYS.isdisjoint(act_props), f"engine facing leaked into core.actors: {act_props & _FACING_KEYS}"
    assert _FACING_KEYS.isdisjoint(loc_props), f"engine facing leaked into core.locations: {loc_props & _FACING_KEYS}"


def test_godot_reference_example_is_strict_and_valid():
    """The Godot reference profile (core + a godot renderer block) satisfies the same strict core
    rules as every instance AND carries a well-formed archived extension block with the LOCKED
    projection + a directional sprite-sheet layout keyed by engine_actor_id (the FK join)."""
    ex = _load(_GODOT_EXAMPLE)
    assert ex.get("schema_version") == 1
    core = ex["core"]
    assert core["scene_kind"] == "backdrop"
    assert core["positioning"] in ("theater", "zone")
    forbidden = {"x", "y", "col", "row", "grid_x", "grid_y", "coords", "position"}
    for loc in core["locations"]:
        assert "engine_location_id" in loc
        assert forbidden.isdisjoint(loc)
        for z in loc.get("zones", []):
            assert isinstance(z, str) and z.strip()
    for act in core["actors"]:
        assert "engine_actor_id" in act
        assert _FACING_KEYS.isdisjoint(act), "no facing on an actor instance (renderer-derived)"

    godot = ex["renderer_profiles"]["godot"]
    assert godot["projection"]["kind"] in ("dimetric", "isometric")
    known_ids = {a["engine_actor_id"] for a in core["actors"]}
    for actor_id, sheet in (godot.get("actor_sheets") or {}).items():
        assert actor_id in known_ids, f"actor_sheets key {actor_id} is not a core engine_actor_id"
        assert sheet["facings"] == len(sheet["facing_order"]), "facings count must match facing_order length"


def test_godot_reference_example_full_jsonschema_validation_when_available():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_load(_GODOT_EXAMPLE), _load(_SCHEMA))
