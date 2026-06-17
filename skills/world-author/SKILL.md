---
name: world-author
description: Author a new WorldOS WORLD SEED — a persistent living setting the DM generates within (regions, factions, a cast, history, standing threads, lore). Use when the player wants to create a new world to adventure in (e.g. via /world-new), expand a world's lore, or design a setting at the Baldur's-Gate caliber. Distinct from campaign-author, which builds fixed adventure MODULES.
---

# World Author

You design **world seeds** — persistent settings the DM *generates within*, not fixed plots. A good seed is a dense bible the live DM can build an infinite, consistent, epic campaign on. The bar is **Baldur's Gate**: mature (18+), morally grey, deep history, a larger force stirring beneath a human-scale crisis.

**The schema is documented in `content/worlds/README.md` — read it for every field.** Your job is the *craft* of filling it well.

## Build a world

1. **Find the seed idea.** A place + a wound + a stirring power. Not "a fantasy kingdom" — "a coast of free cities a generation after the war that nearly unmade them, where the seal beneath the waves is thinning." Deep time + a larger force + a human-scale crisis.
2. **Write `content/worlds/<id>/world.json`** (see the schema doc). Make each part earn its place:
   - **`premise` + `tone` + `era`** — the situation, the register, and the chronology (what's already happened; who's alive). The era is a *guardrail* the DM honors.
   - **`history[]`** — dense, specific canonical facts (these are indexed into `recall`, so they keep generated play consistent). Give the world a past that scarred it.
   - **`standing_threads[]`** — live tensions that will *move on their own* (a contested succession, a cult recruiting, factions maneuvering). The world-sim ticks these in the background.
   - **`regions[]`** — ~6 places wired into a navigable map (`connections`), each a short evocative description. A hub + spokes.
   - **`factions[]`** — ~4 powers, each with a real point of view (a "villain" faction should be sympathetic enough to almost agree with).
   - **`npc_roster[]`** — ~6 pullable figures, each with a personality and a `hook`; at least one **morally-complex antagonist** (warmth-first) and one **companion candidate with a wound**.
   - **`story_seeds[]` + `starting_options[]` + `dm_guidance`** — emergent hooks, where to drop the party, and how to run it as a living sandbox.
3. **Seed a starter lore corpus** — a few `content/worlds/<id>/lore/*.md` pages (a `# Title`, prose, an optional `*Era: ...*` line) so `lookup_lore` has canon from day one. More can be ingested later (`tools/ingest/`).
4. **Add `content/worlds/<id>/LICENSE.md`** — required (license_check enforces it). Original world = CC-BY-4.0; a setting-based one = free, unofficial Fan Content (see the schema doc + existing seeds). Put anything you don't want public under `content/worlds/_private/<id>/`.
5. **Validate** — confirm it loads with `start_world(<id>)` (regions/factions/roster/lore seed, a starting option resolves), then offer `/world-play <id>`.

## Quality bar
Hold the same craft as the dungeon-master skill: a world where the local trouble is a thread in something vast; characters with wounds; moral grey over good-vs-evil; prose that evokes. Build the seed so the DM *can't help* but tell a great story in it.
