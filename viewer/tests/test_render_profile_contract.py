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
