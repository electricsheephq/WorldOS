# ClawDnD

**Play full D&D 5e campaigns with an AI Dungeon Master *and* a voiced AI companion — fully voice-acted.**

ClawDnD is a Claude Code plugin. You don't play *against* the AI; you go on an adventure *with* it. A Dungeon Master narrates the world and voices every NPC, and a companion party member adventures alongside you — with its own character sheet, personality, voice, and agency. Dice and rules are deterministic (never hallucinated), campaigns persist across sessions, and every line is spoken aloud.

> **Status:** Tier-1 **plus a living-world generative engine** — built and green in CI (~353 engine tests). The full deterministic stack (dice, characters, combat, spellcasting, rests, inventory/economy, NPC memory, encounters, persistence, voice) runs under an AI DM that **generates epic, mature, Baldur's-Gate-caliber stories live inside persistent, canon-anchored worlds** — grounded by on-demand lore lookup, a searchable campaign memory, chronology, and standing threads that move on their own. QA-scored on a story-craft lens (≈4.2/5, prestige-fantasy). Tier 2 (OpenClaw companion fork) is the remaining milestone. See the [issues](https://github.com/100yenadmin/ClawDnD/issues) for the roadmap.

## What makes it different

The closest existing tools each miss something: commercial AI-DMs treat voice as a bolt-on, voice-first tools are screen-watching overlays rather than full games, and the Claude-native D&D projects have no voice at all. ClawDnD is the unoccupied intersection: **Claude-native + plays the whole game + deterministic 5e rules + persistent campaigns + multi-character voice acting.**

## Architecture

Two tiers:

- **Tier 1 (now) — Claude Code plugin.** You play through Claude Code. The DM brain, story, and the companion all run via Claude. Three Python MCP servers do the deterministic work:
  - `servers/engine` — authoritative game state: dice, character sheets, combat/initiative, conditions, encounters, XP/leveling, and single-writer persistence (campaign truth lives on disk, so it survives context compaction and spans many sessions).
  - `servers/rules` — D&D 5e rules lookup over bundled **SRD 5.2.1** data (offline, canonical), with a `dnd5eapi.co` fallback.
  - `servers/voice` — text-to-speech behind a swappable `TtsBackend` interface. **Kokoro** (local, free, multi-voice) is the default; ElevenLabs can drop in later without touching anything else.
- **Tier 2 (later) — OpenClaw integration.** Your own persistent agent plays the companion in an isolated forked sub-session — same identity and memory, separate from your main work session. The Tier-1 design already has the seam for this: the DM only ever talks to the companion through a `CompanionProvider` interface, so swapping the in-Claude persona for an OpenClaw sub-session is an adapter change, not a rewrite.

The loop is **turn-based** (Claude narrates → per-character voice plays → you type your turn → Claude), not realtime speech-to-speech — that keeps Claude as the brain and gives every character a distinct voice. Voice today is **output** (DM narration + per-character voices). Voice **input** (speech-to-text — *you speak* your turn) is a roadmap item; the seam is built but no backend is wired yet, so for now you type.

```
.claude-plugin/   plugin.json + marketplace.json (install metadata)
.mcp.json         registers the 3 MCP servers
skills/           dungeon-master, companion, campaign-author, world-author
agents/           companion-agent (Tier-2 fork seed)
servers/          engine, rules, voice  (Python, run with uv)
data/srd/         SRD 5.2.1 data (CC-BY-4.0) + ATTRIBUTION
commands/         player-facing slash commands (campaign + /world-*)
content/          campaigns, worlds (living-world seeds + lore corpora), voice map
tools/ingest/     offline wiki → lore-corpus ingestion
state/            runtime campaign saves (git-ignored)
```

## What's inside (Tier 1)

The deterministic core runs in three Python MCP servers and is exercised by a full pytest suite (green in CI):

- **Dice** — full notation (`2d6+3`, `4d6kh3`, advantage/disadvantage, exploding), crit dice-doubling, and an auditable roll ledger.
- **Characters & progression** — creation, derived stats, XP and leveling, ability/skill checks, saving throws.
- **Combat** — initiative order, the action economy, attack-vs-AC, damage/healing, temp HP, conditions, concentration checks, death saves, and an encounter state machine.
- **Spellcasting** — slots, prepared/known spells, save-vs-attack, save DCs, upcasting, and concentration binding.
- **Rests** — short and long rests that correctly restore HP, slots, and other resources.
- **Inventory & economy** — items, attunement, equipping, currency, buying/selling, and encumbrance.
- **NPCs & social** — an attitude model, persistent NPC memory, and check-gated social outcomes that *mechanically* change how an NPC responds.
- **Encounters** — SRD CR→XP math, party XP budgets, and difficulty classification with the encounter multiplier.
- **Rules lookup** — bundled **SRD 5.2.1** (CC-BY-4.0): hundreds of spells, monsters, items, plus feats, backgrounds, species, classes, conditions, and rules, with fuzzy search and a `dnd5eapi.co` fallback.
- **Voice (output)** — `speak(text, voice_id)` behind a swappable `TtsBackend` (Kokoro / ElevenLabs / null), distinct per-character voices, with a reliable text-only fallback. A speech-to-text *seam* is in place (swappable `SttBackend`) but the backends are stubs — voice **input** is on the roadmap; today you type your turn.
- **Persistence & recaps** — single-writer atomic saves, a session log, and a "Previously on…" recap so a campaign survives quit/reload and context compaction across many sessions.
- **Content** — an original CC-BY starter adventure (**"The Cellar Rats"**), a campaign scaffold/validator, and a *private* import path for adventures you legally own.
- **Living worlds (generative play)** — drop into a persistent **world seed** (regions, factions, a pullable cast, history, and an `era` the DM stays true to) and the DM generates the adventure *live*: grounding in canon on demand (`lookup_lore` over a per-world lore corpus — authored pages outrank ingested ones), *persisting* what it builds (`add_location`, `remember`) so the world is travelable and survives across sessions, and letting the world's **standing threads move on their own** (`world_tick`). Ships an original world (*The Sundered Reach*, CC-BY) plus a wiki-ingestion pipeline (`tools/ingest/`) for building deep lore corpora. See `content/worlds/README.md`.

## How to play

Once installed, drive the game from Claude Code with these commands:

**Living worlds — the generative mode, ClawDnD at its best:** drop into a persistent world and the DM generates an epic, mature story *live* within its canon (real places, factions, history, and an era it stays true to), grounded by on-demand lore lookup, with the world's standing threads moving on their own — different every playthrough.

| Command | What it does |
|---|---|
| `/world-list` | Browse the living worlds you can adventure in. |
| `/world-play [id]` | Drop into a world (e.g. `sundered-reach`) — the DM generates the story live, grounded in the world's canon, and resumes an existing campaign if one exists. |
| `/world-new [concept]` | Author a new original world seed to adventure in. |
| `/campaign-new [name]` | Create a campaign, roll up your character, meet your AI companion, and begin. |
| `/session-start [id]` | Resume (or begin) play — loads state, recaps "Previously on…", hands off to the DM. |
| `/session-recap [id]` | Read a voiced recap of the story so far without advancing it. |
| `/save` | Flush a durable checkpoint you can quit and reload from. |
| `/roll <expr>` | Make a real, auditable engine roll (`/roll d20 advantage`, `/roll 2d6+3`). |
| `/voice-toggle [on\|off]` | Switch spoken voice on or off — text-only fallback when off. |

You don't strictly need the commands: the **dungeon-master** skill activates whenever you ask to start or continue a ClawDnD adventure. The commands are just the front door.

## Play in the dashboard

The local viewer can be more than a window onto the game — it can be the surface you *play on*. There are **two ways to play** the same living-world mode:

- **In Claude Code** — type your turns and the DM responds in chat. Use `/world-play [id]` (or just ask).
- **In the dashboard** — play in your browser: you act through the on-screen **action palette** (**Say** to speak in-scene, **Do** to attempt something, **Continue**, the dice / skill / save / combat buttons, and click-to-travel), and a live AI DM responds beside it, turn by turn.

To play in the dashboard, **double-click `clawdnd-play.command`** (a Desktop shortcut is installed by `scripts/install-desktop-shortcut.sh`), or run it from a terminal:

```bash
./clawdnd-play.command            # default living world
scripts/play.sh sundered-reach    # a specific world — see /world-list
```

What to expect: your browser opens to `http://127.0.0.1:8765/dashboard`, the DM opens the world live and hands you a character and an opening scene, and the action palette goes live. You act through it; the DM resolves each move through the deterministic engine, voices the NPCs and your companion, and renders the next beat in the chat — the same generative living-world play as `/world-play`, just driven from the browser. (The companion `/play-dashboard` command explains the same thing inside Claude Code.)

The DM loop is **safety-capped** so it can't run away: a per-turn budget, a whole-session budget ceiling, and a hard turn cap — adjust them with `CLAWDND_PLAY_BUDGET`, `CLAWDND_PLAY_SESSION_BUDGET`, and `CLAWDND_PLAY_MAX_TURNS`. Press **Ctrl-C** (or close the window) to stop.

> Not to be confused with `clawdnd-dashboard.command`, the read-only **director's view** — it *watches* a game (live or a QA run) without an action palette. `clawdnd-play.command` is the one that lets you *play*.

## Quick start — install, create a character, play your first session

### 1. Requirements

- **[Claude Code](https://claude.ai/code)** (the host shell)
- **[`uv`](https://docs.astral.sh/uv/)** — manages the three Python MCP servers; auto-provisions Python 3.12. Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- macOS / Apple Silicon recommended for Kokoro local voice (works on Linux too; Kokoro requires a one-time model download).

### 2. Install the plugin

Clone the repo and register it with Claude Code in a single step:

```bash
git clone https://github.com/100yenadmin/ClawDnD.git
cd ClawDnD
```

Then inside Claude Code, register the local marketplace and install the plugin:

```
/plugin marketplace add .
/plugin install clawdnd@clawdnd
```

Claude Code reads `.claude-plugin/plugin.json` and `.mcp.json`, starts the three MCP
servers (`clawdnd-engine`, `clawdnd-rules`, `clawdnd-voice`) via `uv`, and the plugin
is live for the session.

### 3. Create a character and begin

In Claude Code, start your first living-world session:

```
/world-list          ← see the available worlds
/world-play sundered-reach   ← drop into The Sundered Reach (original, CC-BY)
```

The DM generates your character (level 3, class and concept fitted to the world), opens
a scene grounded in the world's canon, introduces your **voiced AI companion**, and hands
you an open moment to act on. You type your moves; the engine resolves them
deterministically; the DM narrates and voices every character.

Other worlds:
- `baldurs-gate` — the Unofficial BG3+ Universe Seed (free Fan Content)
- `tidal-commonwealth` — The Tidal Commonwealth (original, CC-BY)

Or start a scripted campaign: `/campaign-new [name]` then `/session-start [id]`.

### 4. Play surfaces

**Option A — play in Claude Code** (type your turns in chat):

```
/world-play sundered-reach
```

**Option B — play in the browser dashboard** (action palette + live DM beside it):

```bash
# Double-click the desktop shortcut (after scripts/install-desktop-shortcut.sh), or:
./clawdnd-play.command            # default world (baldurs-gate)
scripts/play.sh sundered-reach    # specific world
```

Your browser opens to `http://127.0.0.1:8765/dashboard`. Act through **Say**, **Do**,
**Continue**, the dice / skill / save / combat buttons, and click-to-travel. The DM
resolves each move through the engine and narrates the next beat live.

**Option C — native macOS app** (OpenWorlds — build from source):

```bash
script/build_and_run.sh           # builds with Swift, ad-hoc signs, opens the app
```

The app (`macos/ClawDnDApp/`) hosts the same read-model screens (atlas, party, quests,
event feed, combat, portrait art) as live engine read-models. Requires macOS 13+ and
`swift` in your PATH (Xcode Command Line Tools).

### 5. Voice

Voice is on by default (Kokoro local TTS, multi-voice, free). Toggle it mid-session:

```
/voice-toggle off    ← text-only fallback
/voice-toggle on     ← voiced narration + per-character voices
```

Kokoro runs on-device; no API key needed. ElevenLabs can drop in later via
`CLAWDND_TTS_BACKEND=elevenlabs` without touching anything else.

---

## Licensing

ClawDnD's own **code is MIT** (see `LICENSE`). It reuses only permissively-licensed components and ships the **CC-BY-4.0 SRD 5.2** for rules. See `THIRD_PARTY_NOTICES.md` and `data/srd/ATTRIBUTION.md`.

**World seeds** (`content/worlds/`) are a separate layer with their own licensing:

- **Original seeds** (e.g. *The Sundered Reach*) are original ClawDnD content, **CC-BY-4.0**, built on SRD primitives.
- **Universe seeds based on existing settings** (e.g. the *Unofficial Baldur's Gate 3+ Universe Seed*) are **unofficial, FREE fan content** — game rules under the **D&D Open Game License / CC-BY SRD**, and setting names/lore/characters under the **Wizards Fan Content Policy** (and, for Baldur's Gate 3 elements, used as unofficial fan content of Larian Studios). They are **not official, not endorsed**, and never sold. Each such seed ships a `LICENSE.md` carrying the required notice. The MIT license covers ClawDnD's code only — **it does not extend to the universe seeds**, which remain the property of their respective rights-holders and are used here strictly as free, unofficial Fan Content.

A `_private/` path under each content area stays **gitignored** for material you don't intend to publish.
