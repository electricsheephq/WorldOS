#!/usr/bin/env python3
"""M3 build-loop — STEP 1+2: synthesize a render-profile from a game seed (#449, #450).

The AI build-loop's first stage. Given a small "game seed" (a partial, human- or LLM-authored
description of a game: its scene tier + locations + actors), this emits a COMPLETE, schema-valid
WorldOS render-profile — the contract the generic Phaser renderers consume. It is the codegen
backbone of "the AI can stand up a graphical game from lore" without any human writing the
profile by hand.

DESIGN INVARIANTS (these are why the loop is safe):
  - CONTRACT IS FROZEN, READ-ONLY (#452). This generator only ever EMITS instances of the M0
    schema; it never proposes a schema change. Any seed field that has no home in the contract
    is collected into `unmapped` and surfaced to the human-gate queue (see gate.py) — NEVER
    silently added to the profile. The loop cannot mutate the contract.
  - DEFAULTABLE => PARTIAL SEEDS ARE VALID. Every core field has a sane default, so a sparse
    seed still produces a valid (if minimal) profile — matching the schema's "all core fields
    defaultable for partial AI generation" intent.
  - ART IS RESOLVED BY SCOPE-KEY, NOT BAKED (#450). Each location/actor gets an `art.scope_key`
    derived deterministically from its name (slugified, prefixed). At render time the existing
    Img-scope -> /image bridge resolves it (first-party imagegen + BG catalog, owner decision
    2026-06-02); a miss falls back to the procedural placeholder the renderers already draw. So
    generation never needs the image model to be online.
  - AI-DISCLOSURE IS STAMPED (#450, #454). Generated profiles carry `core.ai_disclosure`
    (generated_by/model/date) so the downstream EU + Steam disclosure obligations have a source.
  - ENGINE STAYS SOLE WRITER. This emits PRESENTATION JSON only. It never writes game state.
    FK ids (engine_location_id / engine_actor_id) are carried through from the seed verbatim —
    the profile JOINS to engine state by id; it does not own it.

Usage:
    python3 generate_profile.py <seed.json> [--out profile.json] [--model <name>] [--date <iso>]
    python3 generate_profile.py example-seed.json            # prints to stdout

Stdlib only (no network, no third-party) so it runs anywhere the engine tests run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
VALID_SCENE_KINDS = ("tilemap", "backdrop")
VALID_POSITIONING = ("theater", "zone")

# Fields the generator KNOWS how to place into the frozen contract. Anything else in a seed
# entry is "unmapped" -> routed to the human-gate queue, never invented into the profile.
_KNOWN_TOP = {"game_id", "title", "scene_kind", "positioning", "locations", "actors"}
_KNOWN_LOC = {"engine_location_id", "name", "zones", "art_scope_key"}
_KNOWN_ACTOR = {"engine_actor_id", "name", "kind", "art_scope_key"}


def slugify(text: str) -> str:
    """Deterministic, url/scope-safe slug from a human name."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "unnamed"


def _scope_for_location(loc: dict) -> str:
    if loc.get("art_scope_key"):
        return str(loc["art_scope_key"])
    base = loc.get("name") or loc.get("engine_location_id") or "location"
    return "scene-" + slugify(base)


def _scope_for_actor(actor: dict) -> str:
    if actor.get("art_scope_key"):
        return str(actor["art_scope_key"])
    base = actor.get("name") or actor.get("engine_actor_id") or "actor"
    # creatures/monsters get a different scope family than PCs so the Img bridge + catalog can
    # route them; the engine id prefix or an explicit `kind` drives it.
    kind = (actor.get("kind") or "").lower()
    aid = str(actor.get("engine_actor_id") or "")
    is_creature = kind in ("creature", "monster", "npc-foe", "foe") or aid.startswith("mon")
    return ("creature-" if is_creature else "portrait-") + slugify(base)


def generate_profile(seed: dict, *, model: str = "worldos-ai-build-loop", date: str = "") -> dict:
    """Pure function: seed dict -> (profile dict). Collects unmapped fields under
    profile['_unmapped'] for the gate to route to humans; that key is stripped before the
    profile is written/validated (it is NOT part of the contract)."""
    unmapped: list[dict] = []

    scene_kind = seed.get("scene_kind", "tilemap")
    if scene_kind not in VALID_SCENE_KINDS:
        unmapped.append({"where": "core.scene_kind", "value": scene_kind,
                         "reason": f"not in {VALID_SCENE_KINDS}; defaulted to 'tilemap'"})
        scene_kind = "tilemap"

    positioning = seed.get("positioning", "zone")
    if positioning not in VALID_POSITIONING:
        unmapped.append({"where": "core.positioning", "value": positioning,
                         "reason": f"not in {VALID_POSITIONING} (v1); 'grid' is the evidence-gated "
                                   "Future epic — routed to human, defaulted to 'zone'"})
        positioning = "zone"

    # top-level unmapped keys
    for k in seed:
        if k not in _KNOWN_TOP:
            unmapped.append({"where": f"seed.{k}", "value": seed[k],
                             "reason": "no home in the frozen contract; human-gate (loop must not "
                                       "mutate the contract)"})

    locations = []
    for loc in seed.get("locations", []) or []:
        for k in loc:
            if k not in _KNOWN_LOC:
                unmapped.append({"where": f"location[{loc.get('name', '?')}].{k}", "value": loc[k],
                                 "reason": "unmapped location field; human-gate"})
        entry: dict[str, Any] = {"engine_location_id": str(loc.get("engine_location_id", ""))}
        entry["art"] = {"scope_key": _scope_for_location(loc)}
        zones = [str(z).strip() for z in (loc.get("zones") or []) if str(z).strip()]
        if zones:
            entry["zones"] = zones
        locations.append(entry)

    actors = []
    for actor in seed.get("actors", []) or []:
        for k in actor:
            if k not in _KNOWN_ACTOR:
                unmapped.append({"where": f"actor[{actor.get('name', '?')}].{k}", "value": actor[k],
                                 "reason": "unmapped actor field; human-gate"})
        actors.append({
            "engine_actor_id": str(actor.get("engine_actor_id", "")),
            "art": {"scope_key": _scope_for_actor(actor)},
        })

    core: dict[str, Any] = {
        "scene_kind": scene_kind,
        "positioning": positioning,
        "locations": locations,
        "actors": actors,
        "ai_disclosure": {
            "generated_by": "worldos-ai-build-loop",
            "model": model,
            "date": date or "",
        },
    }

    # per-renderer block: only the keys that scene_kind needs (keep it honest + minimal).
    phaser: dict[str, Any] = {}
    if scene_kind == "tilemap":
        phaser = {"tileset_scope_key": "tileset-" + slugify(seed.get("title", "default")),
                  "tile_size": 32, "ui_skin": "16bit"}
    elif scene_kind == "backdrop":
        phaser = {"ui_skin": "painted", "walkmask_ref": "procedural", "depth_bands": [0.55, 0.7, 0.85]}

    profile: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "game_id": seed.get("game_id") or slugify(seed.get("title", "untitled-game")),
        "title": seed.get("title", "Untitled WorldOS Game"),
        "core": core,
        "renderer_profiles": {"phaser": phaser},
    }
    if unmapped:
        profile["_unmapped"] = unmapped  # NOT part of the contract; gate strips + routes it.
    return profile


def strip_unmapped(profile: dict) -> dict:
    """Return a copy without the non-contract `_unmapped` annotation (what actually gets written)."""
    return {k: v for k, v in profile.items() if k != "_unmapped"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a WorldOS render-profile from a game seed.")
    ap.add_argument("seed", help="path to a game-seed JSON")
    ap.add_argument("--out", default="", help="write profile here (default: stdout)")
    ap.add_argument("--model", default="worldos-ai-build-loop", help="model name for ai_disclosure")
    ap.add_argument("--date", default="", help="ISO date for ai_disclosure (pass explicitly; the "
                                               "loop is deterministic and does not read the clock)")
    args = ap.parse_args(argv)

    seed = json.loads(Path(args.seed).read_text())
    profile = generate_profile(seed, model=args.model, date=args.date)
    unmapped = profile.get("_unmapped", [])
    out_profile = strip_unmapped(profile)
    text = json.dumps(out_profile, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    if unmapped:
        print(f"\n[human-gate] {len(unmapped)} unmapped field(s) routed to human review "
              f"(NOT added to the profile — the loop does not mutate the contract):", file=sys.stderr)
        for u in unmapped:
            print(f"  - {u['where']}: {u['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
