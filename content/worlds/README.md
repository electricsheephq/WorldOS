# World seeds — schema & authoring guide

A **world seed** is a persistent setting the DM *generates within* (not a fixed plot).
`start_world(world_id)` seeds a fresh campaign from it; the DM then runs a living
sandbox — generating scenes on arrival, persisting them (`add_location`, `remember`),
pulling canon on demand (`lookup_lore`), and letting the world's threads move on their
own (`world_tick`). Every playthrough diverges.

Layout:

```
content/worlds/<id>/
  world.json          # the bible (required) — schema below
  LICENSE.md          # required (license_check enforces it) — see Licensing
  lore/*.md           # authored canon pages (tier 0 — outrank wiki in lookup_lore)
  lore/wiki/*.md       # ingested pages (tier 1) — see tools/ingest/
content/worlds/_private/<id>/   # gitignored — personal seeds (e.g. third-party IP)
```

## `world.json` schema (schema_version 1)

| field | type | what it does |
|---|---|---|
| `id` | string | stable id; matches the folder name; what `start_world` takes |
| `schema_version` | int | currently `1` |
| `name` | string | display name |
| `setting` | string? | one-line setting descriptor (optional) |
| `ruleset` | string | `"SRD 5.2"` |
| `era` | string | **chronology** — when it's set + what's already happened. The DM respects it (no raising the long-dead). Surfaced by `start_world`/`lookup_lore`. |
| `tone` | string | the storytelling register (aim for the Baldur's-Gate bar: mature, morally grey) |
| `premise` | string | the world's situation — the larger force stirring beneath a human-scale crisis |
| `map_kind` | `"none"`\|`"hex"` | how the viewer renders the map |
| `history` | string[] | canonical lore facts — copied to `Campaign.lore` and **indexed into `recall`** so a generated story stays consistent |
| `standing_threads` | string[] | the world's live threads — **the world-sim seeds each as a recurring background "world beat"** (`worldsim.seed_threads`) that surfaces via `world_tick` |
| `regions` | object[] | `{id, name, description, connections[], tags[]?, hex?}` → seeded as **Locations** (a navigable map; `connections` are bidirectional at play time) |
| `factions` | object[] | `{id, name, description, reputation}` → seeded as **Factions** |
| `npc_roster` | object[] | `{id, name, voice_id, role, personality, hook}` → seeded as **NPC Characters** (the `hook` becomes a memory fact; "pullable" — the DM brings them in or invents freely) |
| `story_seeds` | string[] | emergent hooks the player can stumble into (returned by `start_world`, not auto-created as quests) |
| `starting_options` | object[] | `{location_id, framing}` — where the DM can drop the party |
| `dm_guidance` | string | how to run this world as a living sandbox |
| `license`, `attribution` | string? | for non-original seeds (see Licensing) |

The engine **never computes on** prose fields — they're DM-facing guidance, like map coords. `seed_world` (in `servers/engine/content.py`) consumes this; `start_world` (server.py) returns the bible; `lorebook.py` indexes the `lore/` corpus.

## The lore corpus (`lore/`)

Markdown "wiki pages" the DM searches with `lookup_lore(campaign_id, query)`:
- **Authored** pages live at `lore/*.md` (**tier 0** — they outrank ingested pages, so your post-canon truth wins over a longer stale wiki page).
- **Ingested** pages live at `lore/wiki/*.md` (**tier 1**), produced by `tools/ingest/` from a wiki (CC-BY-SA).
- Each page: a `# Title` heading, body prose, and an optional `*Era: ...*` / `status:` line (parsed and surfaced per hit so the DM keeps chronology straight).

Seed a few authored pages so `lookup_lore` has canon from day one; ingest more later.

## Licensing (each seed needs a `LICENSE.md`)

`scripts/license_check.py` requires a `LICENSE.md` beside every committed `world.json`.
- **Original seeds** (e.g. `sundered-reach`): original ClawDnD content, **CC-BY-4.0**, on SRD primitives. No third-party setting IP.
- **Setting-based seeds** (e.g. `baldurs-gate`): **FREE, unofficial Fan Content** — rules under CC-BY/OGL SRD, setting names/lore under the **Wizards Fan Content Policy** (+ Larian for BG3); each ingested page carries its source + CC-BY-SA. Not official, not endorsed, never sold.
- ClawDnD's MIT license covers **code only** — it does not extend to the world seeds.

See `sundered-reach/` (original) and `baldurs-gate/` (fan content) as worked examples.
