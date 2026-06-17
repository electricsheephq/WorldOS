---
description: Play in the dashboard — the local viewer becomes your play surface, with a live AI DM beside it.
argument-hint: "[world id] (e.g. sundered-reach) — optional; defaults to baldurs-gate"
---
The player wants to **play in the dashboard** (the browser viewer is the play surface) rather than by typing turns in Claude Code. This is the same living-world generative mode as `/world-play`, just driven through the dashboard's action palette while a separate live DM (the shipped `dungeon-master` skill) responds beside it.

Target world (optional id): $ARGUMENTS

Tell the player how to launch it, then let them play in the browser:

1. **Launch it** — the simplest path is to **double-click `worldos-play.command`** in the repo (a Desktop shortcut is installed by `scripts/install-desktop-shortcut.sh`). Or, from a terminal:
   ```bash
   ./worldos-play.command            # default world (baldurs-gate)
   scripts/play.sh sundered-reach    # a specific world; see /world-list
   ```
   It opens `http://127.0.0.1:8765/dashboard`, flips the viewer into interactive (live) mode, and starts the DM, who opens the world live and hands the player a character + an open moment.
2. **Play in the browser** — the player acts through the action palette: **Say** (speak in-scene), **Do** (attempt something), **Continue**, the dice / skill / save / combat buttons, and click-to-travel. Each action is sent to the DM, who resolves it through the engine, voices the NPCs and companion, and renders the next beat in the chat — turn by turn, live.
3. **It self-stops** — the DM loop is capped (per-turn and whole-session USD budgets, and a hard turn cap), so a runaway loop ends on its own. Raise the caps with `CLAWDND_PLAY_BUDGET`, `CLAWDND_PLAY_SESSION_BUDGET`, `CLAWDND_PLAY_MAX_TURNS`. **Ctrl-C (or close the window) to stop.**

**Want AI companions in the party? (opt-in)** By default you play solo (just you + the DM). You can instead bring a party of **AI companions** who adventure alongside you — each is its OWN agent acting through the same constrained move palette you do (it can disagree, take the lead in its lane, even betray you), NOT the DM voicing it. The dashboard then shows you + your companions + the DM, beat by beat. Companions multiply the live AI cost (each is a separate `claude -p`), so they're **off unless you ask for them**:
```bash
# Name companions with a 4th arg (or $WORLDOS_PLAY_COMPANIONS), COMMA-separated tokens
#   Name:class:persona_file[:spell1|spell2|…]   (the 4th field names a caster's spells)
scripts/play_party.sh baldurs-gate '' 8765 \
  "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Guiding Bolt,Brogan:fighter:qa/play_companion.txt"
# or via env (then double-click / run the .command as usual):
WORLDOS_PLAY_COMPANIONS="Brogan:fighter:qa/play_companion.txt" ./worldos-play.command
```
`scripts/play_party.sh` (and the `.command`, which routes through it) is **identical to solo play when you give no companion spec** — so nothing changes for solo. With companions named, each is pre-seeded into the party with a real sheet, the DM creates *your* character live and opens the scene around the existing party, and every beat your move plus each living companion's moves are resolved together. The same budget / turn caps apply (companions count toward the session ceiling).

Prefer to type your turns in chat instead? Use `/world-play [id]` — same world, same DM, played here in Claude Code. The read-only `worldos-dashboard.command` (the "director's view") just watches a game without an action palette; this command is the one that lets you *play* in the dashboard.
