# ClawDnD

**Play full D&D 5e campaigns with an AI Dungeon Master *and* a voiced AI companion — fully voice-acted.**

ClawDnD is a Claude Code plugin. You don't play *against* the AI; you go on an adventure *with* it. A Dungeon Master narrates the world and voices every NPC, and a companion party member adventures alongside you — with its own character sheet, personality, voice, and agency. Dice and rules are deterministic (never hallucinated), campaigns persist across sessions, and every line is spoken aloud.

> **Status:** early development (v0.0.1). Building Epic 0 — foundation. See the [issues](https://github.com/100yenadmin/ClawDnD/issues) for the roadmap.

## What makes it different

The closest existing tools each miss something: commercial AI-DMs treat voice as a bolt-on, voice-first tools are screen-watching overlays rather than full games, and the Claude-native D&D projects have no voice at all. ClawDnD is the unoccupied intersection: **Claude-native + plays the whole game + deterministic 5e rules + persistent campaigns + multi-character voice acting.**

## Architecture

Two tiers:

- **Tier 1 (now) — Claude Code plugin.** You play through Claude Code. The DM brain, story, and the companion all run via Claude. Three Python MCP servers do the deterministic work:
  - `servers/engine` — authoritative game state: dice, character sheets, combat/initiative, conditions, encounters, XP/leveling, and single-writer persistence (campaign truth lives on disk, so it survives context compaction and spans many sessions).
  - `servers/rules` — D&D 5e rules lookup over bundled **SRD 5.2** data (offline, canonical), with a `dnd5eapi.co` fallback.
  - `servers/voice` — text-to-speech behind a swappable `TtsBackend` interface. **Kokoro** (local, free, multi-voice) is the default; ElevenLabs can drop in later without touching anything else.
- **Tier 2 (later) — OpenClaw integration.** Your own persistent agent plays the companion in an isolated forked sub-session — same identity and memory, separate from your main work session. The Tier-1 design already has the seam for this: the DM only ever talks to the companion through a `CompanionProvider` interface, so swapping the in-Claude persona for an OpenClaw sub-session is an adapter change, not a rewrite.

The loop is **turn-based** (Claude narrates → per-character voice plays → you speak/type → speech-to-text → Claude), not realtime speech-to-speech — that keeps Claude as the brain and gives every character a distinct voice.

```
.claude-plugin/   plugin.json + marketplace.json (install metadata)
.mcp.json         registers the 3 MCP servers
skills/           dungeon-master, companion, campaign-author
agents/           companion-agent (Tier-2 fork seed)
servers/          engine, rules, voice  (Python, run with uv)
data/srd/         SRD 5.2 data (CC-BY-4.0) + ATTRIBUTION
content/          campaigns + voice map
state/            runtime campaign saves (git-ignored)
```

## Requirements

- [Claude Code](https://claude.ai/code)
- [`uv`](https://docs.astral.sh/uv/) (manages the Python servers; auto-provisions Python 3.12)
- macOS / Apple Silicon recommended for the local Kokoro voice (works elsewhere too)

## Install (dev)

```bash
git clone https://github.com/100yenadmin/ClawDnD.git
cd ClawDnD
# In Claude Code:
/plugin marketplace add 100yenadmin/ClawDnD
/plugin install clawdnd@clawdnd
```

## Licensing

ClawDnD's own code is **MIT** (see `LICENSE`). It deliberately reuses only permissively-licensed components and ships the CC-BY-4.0 SRD. It does **not** redistribute copyrighted published adventures — it ships original / CC-licensed content, a campaign generator, and a *private* import path for adventures you legally own. See `THIRD_PARTY_NOTICES.md` and `data/srd/ATTRIBUTION.md`.
