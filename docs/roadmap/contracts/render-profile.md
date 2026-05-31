# Render-profile contract (M0)

> **Status:** M0 freeze artifact. Implements issues **#425** (core schema), **#426**
> (per-renderer blocks), **#427** (zones-not-xy / positioning modes). Companion to the
> canonical roadmap (`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`) and the JSON Schema
> (`render-profile.schema.json` in this dir). The throwaway reference instance is
> `spikes/m0-phaser-thin-client/render-profile.example.json`.

## What it is

The render-profile is the **single, versioned, layered contract** that every renderer
(Phaser web today; an optional Godot desktop client and an optional RPG Maker exploration
adapter later) and the AI build-loop consume to draw a WorldOS game. It is **presentation
that joins to engine state by id** — it never holds game state.

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
└── renderer_profiles         ← OPTIONAL; a renderer reads core + its OWN block
    ├── phaser                { tileset/tile_size/ui_skin | backdrop_layout/walkmask/… }
    └── rpgmaker              (reserved / spec-only; deferred)
```

**The seam rule (the load-bearing line):** CORE carries only what *all* renderers honor —
FK ids, the `scene_kind` capability, named `zones`, scope-key art, and disclosure. Anything
coordinate-, walkmask-, or sprite-sheet-layout-shaped lives in an optional per-renderer
block. The **core-only conformance test (#428)** enforces this: a renderer using `core` +
its own block must render every M0 scene. (The repo's existing SVG/React viewer is a free
third "renderer" to validate that `core` stays renderer-agnostic.)

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
