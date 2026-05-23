# Changelog

All notable changes to ClawDnD. ClawDnD's code is MIT; world seeds are licensed
separately (see `content/worlds/README.md`).

## 0.2.0 — The Living-World Generative Engine

The pivot: the AI DM now **generates an epic story live inside a persistent,
canon-anchored world**, rather than only running pre-authored modules. Story quality is
scored on a "Tolkien" story-craft lens (living-world play ≈ 4.1–4.2 / 5, prestige fantasy).

- **World seeds** — `start_world(world_id)` seeds a campaign from a world bible
  (`content/worlds/<id>/world.json`: regions, factions, NPC roster, history, `era`,
  standing threads). `resume=` continues a world instead of orphaning it.
- **On-demand lore** — `lookup_lore` (FTS over a per-world `lore/` corpus); authored
  canon outranks ingested wiki pages; per-page `era`/chronology surfaced.
- **Campaign memory** — `recall` / `recall_npc` / `recall_decisions` (FTS over
  snapshot + session logs), a derived, rebuildable index (the anti-mush guardrail).
- **Background world-sim** — `world_tick` ticks standing threads on the day clock
  (auto-fired by `travel_to`/`downtime`), one beat at a time.
- **Wiki-ingestion pipeline** — `tools/ingest/` (offline, stdlib) builds lore corpora
  from a MediaWiki/Fandom source (CC-BY-SA); ships a 248-page Baldur's Gate corpus.
- **Authored-scene craft** — `get_scene` surfaces read_aloud/dm_notes; `add_location`
  (live world-building with orphan/dup warnings); `record_decision`; `adjust_reputation`.
- **Companion** — `companion_advise` + an explicit party-deliberation loop; depth
  guidance (a wound, lingering disagreement).
- **Read-only play-view** (`viewer/`) — map/party/quests/event feed. Hex + fallback maps.
- **Front door** — `/world-list`, `/world-play`, `/world-new` commands + a `world-author`
  skill + `content/worlds/README.md` schema doc.
- **QA** — the "Tolkien" story-craft lens + a parallel, retry-hardened QA harness.
- **Worlds shipped** — *The Sundered Reach* (original, CC-BY-4.0) and the *Unofficial
  Baldur's Gate 3+ Universe Seed* (free, unofficial Fan Content).
- **DM skill** — the beat cycle, a "Generating a world live" mode, and a storytelling
  craft bar (stage the antagonist warmth-first, felt menace, the unforgettable beat).

## 0.1.0 — Tier-1 feature-complete

- Three MCP servers (engine, rules, voice). Dice (advantage/crit, roll ledger),
  characters & leveling, combat + action economy, all-339-spell casting, rests,
  inventory/economy, NPC memory + check-gated social, exploration/travel, encounters,
  multi-session persistence + "Previously on…" recaps, time-deferred consequences, a
  multi-act arc generator.
- Bundled SRD 5.2.1 (CC-BY-4.0) + bestiary. Original adventures ("The Cellar Rats",
  "The Embergloom Pact"). Voice (Kokoro/ElevenLabs/null) + STT seam.
- Player slash-commands, README, MIT license + third-party notices.
