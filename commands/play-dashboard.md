---
description: Play in the dashboard — the local viewer becomes your play surface, with a live AI DM beside it.
argument-hint: "[world id] (e.g. sundered-reach) — optional; defaults to baldurs-gate"
---
The player wants to **play in the dashboard** (the browser viewer is the play surface) rather than by typing turns in Claude Code. This is the same living-world generative mode as `/world-play`, just driven through the dashboard's action palette while a separate live DM (the shipped `dungeon-master` skill) responds beside it.

Target world (optional id): $ARGUMENTS

Tell the player how to launch it, then let them play in the browser:

1. **Launch it** — the simplest path is to **double-click `clawdnd-play.command`** in the repo (a Desktop shortcut is installed by `scripts/install-desktop-shortcut.sh`). Or, from a terminal:
   ```bash
   ./clawdnd-play.command            # default world (baldurs-gate)
   scripts/play.sh sundered-reach    # a specific world; see /world-list
   ```
   It opens `http://127.0.0.1:8765/dashboard`, flips the viewer into interactive (live) mode, and starts the DM, who opens the world live and hands the player a character + an open moment.
2. **Play in the browser** — the player acts through the action palette: **Say** (speak in-scene), **Do** (attempt something), **Continue**, the dice / skill / save / combat buttons, and click-to-travel. Each action is sent to the DM, who resolves it through the engine, voices the NPCs and companion, and renders the next beat in the chat — turn by turn, live.
3. **It self-stops** — the DM loop is capped (per-turn and whole-session USD budgets, and a hard turn cap), so a runaway loop ends on its own. Raise the caps with `CLAWDND_PLAY_BUDGET`, `CLAWDND_PLAY_SESSION_BUDGET`, `CLAWDND_PLAY_MAX_TURNS`. **Ctrl-C (or close the window) to stop.**

Prefer to type your turns in chat instead? Use `/world-play [id]` — same world, same DM, played here in Claude Code. The read-only `clawdnd-dashboard.command` (the "director's view") just watches a game without an action palette; this command is the one that lets you *play* in the dashboard.
