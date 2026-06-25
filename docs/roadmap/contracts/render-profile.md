# Render-profile contract (M0)

> **Status:** M0 freeze artifact. Implements issues **#425** (core schema), **#426**
> (per-renderer blocks), **#427** (zones-not-xy / positioning modes). Companion to the
> canonical roadmap (`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`) and the JSON Schema
> (`render-profile.schema.json` in this dir). The throwaway reference instance is
> `spikes/m0-phaser-thin-client/render-profile.example.json`.

## What it is

The render-profile is the **single, versioned, layered contract** that renderers and the
AI build-loop consume to draw a WorldOS game. The current renderer lane consumes the
engine-agnostic `core`/SceneGrid contracts; the Phaser, Godot, and RPG Maker blocks are
optional reference/extension surfaces unless a follow-up renderer issue explicitly promotes
one of them. It is **presentation that joins to engine state by id** — it never holds game state.

## The one invariant

**The Python engine is the sole writer of game state.** A render-profile describes *how to
draw* a game; it never decides *what is true*. It references engine state by foreign key
(`engine_location_id`, `engine_actor_id`) and resolves art by scope key (the existing
`Img-scope → /image` bridge). A renderer that consumes this profile owns pixels and input
gestures — nothing else.

## Layered: CORE + per-renderer PROFILES

```
render-profile
├── schema_version            (contract major version; v1)
├── game_id / title
├── core                      ← renderer-AGNOSTIC; EVERY renderer honors it
│   ├── scene_kind            tilemap | backdrop   (a capability, not an impl)
│   ├── positioning           theater | zone       (v1 only; grid is future, #461)
│   ├── locations[]           { engine_location_id (FK), art.scope_key, zones[] }
│   ├── actors[]              { engine_actor_id (FK), art.scope_key }
│   └── ai_disclosure         { generated_by, model, date }
└── renderer_profiles         ← OPTIONAL reference/extension blocks
    ├── phaser                { tileset/tile_size/ui_skin | backdrop_layout/walkmask/… }
    ├── godot                 { projection | backdrop_layout(zone_anchors) | actor_sheets | default_facing }
    └── rpgmaker              (reserved / spec-only; deferred)
```

**The seam rule (the load-bearing line):** CORE carries only what *all* renderers honor —
FK ids, the `scene_kind` capability, named `zones`, scope-key art, and disclosure. Anything
coordinate-, walkmask-, or sprite-sheet-layout-shaped lives in an optional per-renderer
block. The **core-only conformance test (#428)** enforces this: a renderer using `core` +
its own block must render every M0 scene. (The repo's existing SVG/React viewer is a free
third "renderer" to validate that `core` stays renderer-agnostic.)

### Godot reference/extension block (GT2 painterly-isometric)

The `godot` block (added 2026-06-21; sibling of `phaser`/`rpgmaker`) is retained as a
checked-in reference/extension contract. It is **not** the current required renderer lane.
It carries the GT2 Godot client's presentation — and **only** presentation:

- `projection` — the LOCKED dimetric 2:1 (~26.57°) isometric (see `godot/ISO-PROJECTION.md`,
  the single source of truth both the renderer and the Blender bake cite). Irreversible once
  finals bake.
- `backdrop_layout[scope]` — renderer-owned `walk_polygon_ref` + `depth_baseline_y` +
  `zone_anchors{<zone>:[x,y]}` (data-driven zone→screen placement + the Y-sort baseline) +
  optional `normal_map_ref` (Branch B). Absent ⇒ procedural trapezoid fallback.
- `actor_sheets[engine_actor_id]` — the directional sprite-sheet **layout** the renderer
  slices (`facings`, `facing_order`, grid, foot `anchor`, `sheet_scope_key`). The atlas PNG
  is served by the existing `/image` bridge unchanged; CC0 → AI-paintover → Blender-render
  art is interchangeable behind one layout.
- `default_facing` — the 8-facing fallback for a static/teleported token.

**Facing is 100% renderer-DERIVED.** There is **no engine facing field, and one must never be
added** — the engine is the sole writer of game *state*; facing is pure presentation (cf. the
`positionAuthority:'derived'` rule below and the v1 `grid` exclusion). Out of combat the
renderer snaps facing from the zone→zone screen-vector on a `move_to_zone` (reset to
`default_facing` on a `travel`); in combat from actor-zone→target-zone (the Action-Replay
envelope `target_fk`). Example instance:
`viewer/openworlds/render/render-profile.godot.example.json`.

## Zones, not x,y (#427)

The engine adjudicates combat on **named zones + adjacency**, never coordinates —
`Combatant.zone: str` and `Zone{name, description, adjacent[]}` (`servers/engine/models.py`),
whose docstring states it outright: *"NOT a coordinate grid: LLM agents reason about named
regions and their adjacency far more reliably than (x, y)."* The contract therefore carries
**named zones**; each renderer derives its own coordinate space from them. This is not a new
idea — `viewer/server.py:_combat_row_positions` already lays engine zones onto a coarse lane
grid to produce display positions. Any x,y a surface emits is an **ephemeral render-hint,
never authoritative state** (see #432).

### Positioning modes (v1)

| Mode | Meaning | Render rule |
|------|---------|-------------|
| `theater` | No positional model | Party left, foes right (mirrors the engine's empty-zone fallback) |
| `zone` | Engine named regions | Draw zone **bands** with tokens grouped inside; **no VTT cells, no rulers, no measurement**; AoE = zone highlight + affected-token list |

`grid` (authoritative coordinates + measured range / line-of-sight / geometric AoE / flanking)
is **deliberately excluded from v1**. It would require the engine to gain coordinate authority
— a sole-writer **state** change — and is tracked as the **evidence-gated Future milestone**
(#461, "decide after a playtest"). RTwP (real-time + party-AI) is rejected permanently.

## Versioning

`schema_version` is the contract major version (v1 today). Adding optional fields is
backward-compatible; a breaking change bumps the major and requires migrating authored UGC
profiles. Per-renderer blocks evolve without forcing a core bump. The reversal signal: the
core-only conformance test can't render a scene from core + a renderer block, OR a tier needs
a `/move` kind not in the M0 move-intent freeze (`move-intents.md`).

## How a renderer consumes it (the contract in one paragraph)

Boot: `GET /atlas-surface` to learn locations + the current location. Look up the matching
`core.locations[]` entry by `engine_location_id`; draw its `art.scope_key` (tilemap or
backdrop per `scene_kind`). Place each `core.actors[]` entry by its engine `zone` (positions
derived client-side). Poll `/combat-surface` + `/events` and **replay** engine-decided combat
(zero client rules). The only write is a constrained **intent** to `POST /move` (see
`move-intents.md`). The engine snapshot always overrides optimistic UI.

## Companion contracts (the M0 freeze, layered)

This profile is the **frame-state** leg of the M0 contract freeze. Its siblings in this dir:
the **write lane** ([`move-intents.md`](./move-intents.md) — the intents a renderer sends) and the
**time axis** ([`action-replay-envelope.md`](./action-replay-envelope.md) — the ordered, replayable
`/events` beats a renderer animates; its `actor_fk`/`target_fk` join to this profile's
`core.actors[].engine_actor_id` / `core.locations[].engine_location_id`).
