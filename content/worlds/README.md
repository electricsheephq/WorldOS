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
| `npc_roster` | object[] | `{id, name, voice_id, role, personality, hook, dossier?}` → seeded as **NPC Characters** (the `hook` becomes a memory fact; "pullable" — the DM brings them in or invents freely). The optional `dossier` is a **companion dossier** (see below). |
| `story_seeds` | string[] | emergent hooks the player can stumble into (returned by `start_world`, not auto-created as quests) |
| `quest_variants` | object[]? | the replayability layer — each MAJOR quest's outcome, resolved once at world-gen. `{id, name, outcomes[{id, when?:{facts-subset} OR random:<weight>, lore, hook?}]}`. An outcome with a `when` dict that is a subset of the world-state (the chosen ending's `facts` + the `world_tenor` dial) is ENDING-TIED (first match wins); otherwise a seeded weighted roll picks among the `random` outcomes. The resolved outcome lands on `Campaign.quest_outcomes[id]` and its `lore`/`hook` are appended to recallable lore as `[Outcome] …` / `[Hook] …` lines. Absent -> no resolution (today's behavior). Read via `get_quest_outcomes`. |
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

## Companion dossiers (optional, additive)

A **companion dossier** is the *operational* identity the engine's living-world systems
act on — camp scheduling, banter selection, approval causes, companion quest arcs. It is
**not** a second copy of the long `personality`/`backstory` prose; it holds **terse**,
machine-usable tags so a wound, want, value, or banter hook is a real engine fact instead
of a line buried in prompt prose. It can be seeded from three places (all optional):

- a `dossier` (or `companion_dossier`) block on an **`npc_roster`** entry in `world.json`;
- a `companion_dossier` (or `dossier`) block in a **canon character** JSON (`characters/*.json`);
- a `dossier` block inside an **ending** `companion_seeds[<id>]` entry (beside its `arc`).

`recruit_companion` synthesizes a *minimal* dossier (from the record's existing
personality/backstory/memory) only when none was seeded. Shape (every field optional;
empty == today's behavior, so old snapshots load unchanged):

```json
{
  "wound": "lost someone at the Drowning",
  "wants": ["hold the seal", "spare victims of the Choir"],
  "fears": ["becoming what she hunts"],
  "values": ["mercy", "duty"],
  "approval_likes": ["protecting refugees"],
  "approval_dislikes": ["cruelty to pawns"],
  "banter_tags": ["war_guilt", "mercy_vs_duty"],
  "camp_prompts": ["asks what mercy costs when the enemy was once innocent"],
  "relationships": {"npc-jaheira": "old ally"}
}
```

**Authoring rules.** Keep entries to a tag or one clause — do **not** paste long copied
wiki/proprietary lore here (the licensing guidance below applies; the dossier is for
systems to act on, not a biography). A malformed dossier block **degrades** (the companion
simply gets none) and never aborts world creation, exactly like a malformed `companion_seeds`
arc. Committed content is still strictly validated (a typo'd field name is rejected at
author time via `extra="forbid"`), so a bad dossier in a shipped seed shows up in tests.

## Licensing (each seed needs a `LICENSE.md`)

`scripts/license_check.py` requires a `LICENSE.md` beside every committed `world.json`.
- **Original seeds** (e.g. `sundered-reach`): original ClawDnD content, **CC-BY-4.0**, on SRD primitives. No third-party setting IP.
- **Setting-based seeds** (e.g. `baldurs-gate`): **FREE, unofficial Fan Content** — rules under CC-BY/OGL SRD, setting names/lore under the **Wizards Fan Content Policy** (+ Larian for BG3); each ingested page carries its source + CC-BY-SA. Not official, not endorsed, never sold.
- ClawDnD's MIT license covers **code only** — it does not extend to the world seeds.

See `sundered-reach/` (original) and `baldurs-gate/` (fan content) as worked examples.
