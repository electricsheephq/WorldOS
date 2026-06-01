# WorldOS — Architecture

> Architecture map only. For current agent routing, GUI/native app proof, and QA commands, start with
> `WorldOS-OPERATING-GOAL.md`, `WorldOS-RUNBOOK.md`, and `qa/QA_TOOLS.md`.

WorldOS is a Claude Code plugin: an **AI Dungeon Master + voiced AI companion** that
plays D&D 5e. Its center of gravity is a **living-world engine** — the DM *generates*
an epic, mature story live inside a persistent, canon-anchored world, with deterministic
rules and a memory that keeps it consistent. Story quality is the north star (scored on
a "Tolkien" story-craft lens).

## The shape

```
Player ⇄ DM skill (+ companion) ── orchestrates ──▶ 3 MCP servers (uv) ──▶ on-disk truth
                                          │
   engine  (SOLE WRITER, ~58 tools) ──────┤   start_world / create_campaign / start_adventure ─┐
     get_state · look_around · get_scene  │   add_location · travel_to · create_character       ├─▶ snapshot.json  (atomic, campaign_lock)
     combat · spells · inventory · rests   │   remember · record_decision · add_consequence ─────┘     └ sessions/*.jsonl (append-only)
     recall*          ◀── ledger.py    (FTS5 over snapshot+jsonl, rebuild-if-stale)  ┐ derived,
     lookup_lore      ◀── lorebook.py  (FTS5 over content/worlds/<id>/lore/**)        │ read-only,
     world_tick       ◀── worldsim.py  (standing threads tick on the day clock)       │ rebuildable,
     companion_advise ◀── companion.py (CompanionProvider seam → Tier-2 fork)         ┘ deletable
   rules (SRD 5.2 lookup, offline + dnd5eapi fallback)      voice (Kokoro / null TTS + STT)

   content/worlds/<id>/  world.json (regions·factions·roster·era·threads) + lore/*.md (authored) + lore/wiki/*.md (ingested CC-BY-SA)
                          ▲ tools/ingest/ (offline MediaWiki → lore corpus)
   QA: qa/run_parallel.sh → claude -p --plugin-dir (plays the REAL plugin) → distill → score ×2 (mechanical + Tolkien lens)
```

## Load-bearing invariant

**The engine (`servers/engine/`) is the sole writer and sole source of truth.** Campaign
state is one atomic `snapshot.json` per campaign, written temp+`os.replace` under a
`campaign_lock` (flock), with an append-only `sessions/*.jsonl` log. Everything else —
the `ledger` (memory index), the `lorebook` (lore index), the `viewer` — is a **derived,
rebuildable, read-only consumer**. Each is deletable without touching the engine. This is
what lets a campaign survive context compaction and span many sessions.

## How a living world is played

1. **`start_world(world_id)`** (`content.seed_world`) → seeds a campaign from a world
   bible: regions → a navigable `Location` map, factions, an NPC roster, and
   history/standing-threads → `Campaign.lore` (+ the world's `era` chronology). It also
   seeds the standing threads as recurring **world-sim** beats. `resume=<id>` continues an
   existing world instead of orphaning it.
2. **The DM generates live** (the `dungeon-master` skill's "Generating a world live"
   mode): on arrival it `lookup_lore`s the canon (authored pages outrank ingested wiki),
   narrates, and **persists** what it builds — `add_location` (the world grows + stays
   travelable across sessions), `create_character`, `remember`, `record_decision`,
   `add_consequence`.
3. **Canon stays consistent** via two FTS layers: `recall*` (campaign memory: events,
   dialogue, decisions, npc-facts, lore) and `lookup_lore` (the world's lore corpus). The
   `era` is surfaced inline so the DM keeps chronology straight.
4. **The world moves on its own**: `world_tick` (auto-fired by `travel_to`/`downtime`)
   surfaces a background `world_beat` from a standing thread, one at a time.
5. **The companion** acts through the `CompanionProvider` boundary (`companion_advise`,
   `take_turn`) — Tier-1 in-process today; Tier-2 will fork it to an isolated OpenClaw
   sub-session with no DM-loop change.

Authored adventures (`start_adventure`, `content/campaigns/<id>/adventure.json`) coexist:
they seed locations/NPCs/quests, expose authored beats via `get_scene`, and can declare a
`world_id` to use a world's lore corpus.

## Engine modules (`servers/engine/`)

- `models.py` — Pydantic v2 state (`Campaign`, `Character`, `Combat`, `Location`,
  `Faction`, `Quest`, `Consequence`, `Decision`); `extra="forbid"`.
- `store.py` — atomic single-writer persistence + `campaign_lock`, `load/save`, listing.
- `content.py` — `seed_campaign` (modules) + `seed_world` / `load_world_data` / `list_worlds`.
- `lorebook.py` — `lookup_lore` FTS over a world's lore corpus (authored-tier precedence, era parse).
- `ledger.py` — `recall*` FTS over snapshot+jsonl, rebuild-on-signature (a derived index).
- `worldsim.py` — background standing-thread beats on the day clock.
- `consequences.py` — authored time-deferred events (skips world-sim thread-beats).
- `combat.py`, `dice.py`, `spells.py`, `inventory.py`, `rests.py`, `travel.py`,
  `encounter.py`, `bestiary.py`, `npc.py`, `srd_tables.py`, `recap.py`, `companion.py`.
- `server.py` — the FastMCP tool surface over all of the above.

## QA — the fitness function

`qa/run_qa.sh` plays the **real plugin** headless (`claude -p --plugin-dir`, null voice,
sandboxed state) and scores the transcript on **two lenses**: the mechanical rubric
(tool-sourced, rules, state, robustness) and the **Tolkien story-craft lens** (grandeur,
character, prose, momentum, theme, memorability). `run_parallel.sh` runs several at once.
Targets: story-craft ≥ 4.3, mechanical ≥ 4.5, zero critical/high defects. Living-world
play currently scores ~4.1–4.2 (prestige fantasy).
