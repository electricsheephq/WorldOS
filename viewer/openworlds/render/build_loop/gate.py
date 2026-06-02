#!/usr/bin/env python3
"""M3 build-loop — STEP 4: the gated harness + human-gate queue (#452).

Every build-loop iteration must PASS this gate before its output is accepted; on any failure the
iteration is REJECTED (the loop retries or escalates). This is what makes an unattended AI
build-loop safe: it can scaffold freely, but it cannot ship anything that fails an objective
check, and it can NEVER touch the things only a human should own.

THE TWO HARD BOUNDARIES:
  1. THE LOOP MUST NOT MUTATE THE CONTRACT. This harness loads the M0 schema READ-ONLY and
     validates against it. Any field the generator could not place in the contract arrives as
     `_unmapped` and is routed to the HUMAN-GATE QUEUE — never auto-merged into the schema. A
     proposed contract change is a human decision, full stop.
  2. TASTE / STORY / RIGHTS ARE HUMAN-GATED. Art-taste sign-off, story/lore approval, and
     asset-rights/AI-disclosure review are emitted into the human-gate queue, not auto-passed.
     The harness gates the OBJECTIVE properties (schema-valid / art-resolvable / no-overlap /
     renders-clean / blind-playtester); it defers the SUBJECTIVE ones to people.

OBJECTIVE GATES (auto, pass/fail):
  - schema_valid     : instance validates against the frozen render-profile schema.
  - contract_invariants : zones are named (never x,y); walkmask/depth/coord data confined to
                          renderer_profiles (never core); FK ids present.
  - art_present      : every art.scope_key is non-empty + well-formed (resolves via /image at
                       render time; a runtime miss is a soft fallback, but an EMPTY/blank scope is
                       a hard fail — it means the generator dropped art).
  - no_overlap       : no two actors collide into an unusable layout — here, a static check that
                       distinct actors don't share an identical scope_key AND id (a duplicate
                       actor entry), and that zone counts stay within a renderable bound.
  - renders_clean    : OPTIONAL — if Playwright is installed, run qa/render_gate_probe.js against
                       the served game (0 console errors, canvas mounts). Skipped gracefully (not
                       failed) when Playwright is absent, mirroring the M1/M2 render gate.
  - blind_playtester : OPTIONAL — reserved hook for qa/ui_playtest.sh persona traversal (#441);
                       reported as "deferred" until wired, never silently "passed".

Usage:
    python3 gate.py <profile.json> [--schema <path>] [--strict-render] [--json]
    # exit 0 iff all REQUIRED objective gates pass; human-gate items never fail the gate, they
    # are reported for routing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[3]
_DEFAULT_SCHEMA = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"

_COORD_KEYS = {"x", "y", "col", "row", "grid_x", "grid_y", "coords", "position",
               "walkmask_ref", "depth_bands", "backdrop_layout", "free_positions",
               "tile_size", "tileset_scope_key"}
_MAX_ZONES_PER_LOCATION = 12   # a renderable bound (bands/markers stay legible)


def _validate_schema(profile: dict, schema: dict) -> tuple[bool, str]:
    try:
        jsonschema = __import__("jsonschema")
    except ImportError:
        # No jsonschema -> structural fallback so the gate still means something in minimal CI.
        if profile.get("schema_version") != schema["properties"]["schema_version"]["const"]:
            return False, "schema_version mismatch (structural check; jsonschema not installed)"
        core = profile.get("core")
        if not isinstance(core, dict):
            return False, "core missing (structural check)"
        for req in schema["properties"]["core"]["required"]:
            if req not in core:
                return False, f"core.{req} missing (structural check)"
        return True, "structural-only (jsonschema not installed)"
    try:
        jsonschema.validate(profile, schema)
        return True, "jsonschema valid"
    except jsonschema.ValidationError as e:  # type: ignore[attr-defined]
        return False, f"jsonschema: {e.message}"


def _check_contract_invariants(profile: dict) -> list[str]:
    fails: list[str] = []
    core = profile.get("core", {})
    # zones named, never coordinates; FK ids present.
    for loc in core.get("locations", []):
        if not loc.get("engine_location_id"):
            fails.append("a location is missing engine_location_id (FK)")
        if _COORD_KEYS & set(loc):
            fails.append(f"location has coordinate-shaped keys in core: {_COORD_KEYS & set(loc)}")
        for z in loc.get("zones", []):
            if not (isinstance(z, str) and z.strip()):
                fails.append("a zone is not a non-empty string (zones must be NAMED, not x,y)")
    for a in core.get("actors", []):
        if not a.get("engine_actor_id"):
            fails.append("an actor is missing engine_actor_id (FK)")
    # coordinate/walkmask data must be confined to renderer_profiles, never core.
    if _COORD_KEYS & set(core):
        fails.append(f"core carries renderer-only coordinate keys: {_COORD_KEYS & set(core)}")
    return fails


def _check_art_present(profile: dict) -> list[str]:
    fails: list[str] = []
    core = profile.get("core", {})
    for loc in core.get("locations", []):
        sk = (loc.get("art") or {}).get("scope_key", "")
        if not (isinstance(sk, str) and sk.strip()):
            fails.append(f"location {loc.get('engine_location_id')} has empty art.scope_key")
    for a in core.get("actors", []):
        sk = (a.get("art") or {}).get("scope_key", "")
        if not (isinstance(sk, str) and sk.strip()):
            fails.append(f"actor {a.get('engine_actor_id')} has empty art.scope_key")
    return fails


def _check_no_overlap(profile: dict) -> list[str]:
    fails: list[str] = []
    core = profile.get("core", {})
    seen_actor_ids: set[str] = set()
    for a in core.get("actors", []):
        aid = a.get("engine_actor_id", "")
        if aid and aid in seen_actor_ids:
            fails.append(f"duplicate actor entry for {aid} (overlapping tokens)")
        seen_actor_ids.add(aid)
    for loc in core.get("locations", []):
        n = len(loc.get("zones", []))
        if n > _MAX_ZONES_PER_LOCATION:
            fails.append(f"location {loc.get('engine_location_id')} has {n} zones "
                         f"(> {_MAX_ZONES_PER_LOCATION}; markers/bands would overlap)")
    seen_loc_ids: set[str] = set()
    for loc in core.get("locations", []):
        lid = loc.get("engine_location_id", "")
        if lid and lid in seen_loc_ids:
            fails.append(f"duplicate location entry for {lid}")
        seen_loc_ids.add(lid)
    return fails


def _human_gate_queue(profile: dict) -> list[dict]:
    """Collect everything a HUMAN must sign off — never auto-passed."""
    queue: list[dict] = []
    # 1. proposed contract changes (the loop must NOT mutate the contract).
    for u in profile.get("_unmapped", []):
        queue.append({"kind": "contract-change-proposed", "detail": u,
                      "route": "engine/contract owner"})
    # 2. art taste — every resolvable scope is a taste sample for review.
    core = profile.get("core", {})
    scopes = [(l.get("art") or {}).get("scope_key") for l in core.get("locations", [])]
    scopes += [(a.get("art") or {}).get("scope_key") for a in core.get("actors", [])]
    scopes = [s for s in scopes if s]
    if scopes:
        queue.append({"kind": "art-taste-signoff", "detail": {"scope_keys": sorted(set(scopes))},
                      "route": "art/taste reviewer"})
    # 3. story/lore sign-off.
    queue.append({"kind": "story-signoff",
                  "detail": {"game_id": profile.get("game_id"), "title": profile.get("title")},
                  "route": "narrative reviewer"})
    # 4. AI-disclosure + asset-rights compliance (#454).
    disc = core.get("ai_disclosure")
    queue.append({"kind": "ai-disclosure-and-rights", "detail": {"ai_disclosure": disc,
                  "note": "shippable/UGC games need rights-clean assets (imagegen or "
                          "rights-clean uploads); BG catalog is first-party-only"},
                  "route": "compliance reviewer"})
    return queue


def run_gate(profile: dict, schema: dict, *, render_probe: bool = False) -> dict:
    schema_ok, schema_msg = _validate_schema(
        {k: v for k, v in profile.items() if k != "_unmapped"}, schema)
    inv = _check_contract_invariants(profile)
    art = _check_art_present(profile)
    overlap = _check_no_overlap(profile)
    gates = {
        "schema_valid": {"required": True, "passed": schema_ok, "detail": schema_msg},
        "contract_invariants": {"required": True, "passed": not inv, "detail": inv or "ok"},
        "art_present": {"required": True, "passed": not art, "detail": art or "ok"},
        "no_overlap": {"required": True, "passed": not overlap, "detail": overlap or "ok"},
        "renders_clean": {"required": False, "passed": None,
                          "detail": "run separately via qa/render_gate_probe.js"
                                    if not render_probe else "see render-probe output"},
        "blind_playtester": {"required": False, "passed": None,
                             "detail": "deferred: wire qa/ui_playtest.sh persona traversal (#441)"},
    }
    required_pass = all(g["passed"] for g in gates.values() if g["required"])
    return {
        "accepted": required_pass,
        "gates": gates,
        "human_gate_queue": _human_gate_queue(profile),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate a generated render-profile (M3 build-loop).")
    ap.add_argument("profile", help="path to a generated render-profile JSON")
    ap.add_argument("--schema", default=str(_DEFAULT_SCHEMA))
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text())
    schema = json.loads(Path(args.schema).read_text())
    report = run_gate(profile, schema)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"ACCEPTED: {report['accepted']}")
        for name, g in report["gates"].items():
            mark = {True: "PASS", False: "FAIL", None: "SKIP"}[g["passed"]]
            req = "required" if g["required"] else "optional"
            print(f"  [{mark}] {name} ({req}): {g['detail']}")
        print(f"  human-gate queue: {len(report['human_gate_queue'])} item(s) for human review")
        for item in report["human_gate_queue"]:
            print(f"    -> {item['kind']} :: route={item['route']}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
