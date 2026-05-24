#!/usr/bin/env bash
# Play ClawDnD in the dashboard — DOUBLE-CLICK this file to start a new adventure you
# play in your browser (no terminal typing needed). It opens the play dashboard and runs
# a live AI Dungeon Master beside it: you act through the action palette (Say / Do /
# Continue, dice & combat, click-to-travel) and the DM narrates, voices the cast, and
# resolves your moves live, turn by turn. This is the dashboard counterpart to
# `/world-play`. Close this window (or Ctrl-C) to stop.
#
# Optional args (passed straight through): [world-id] [run-id] [port] [companion-spec]
#   e.g.  open the file normally for the default world, or run from a terminal:
#         ./clawdnd-play.command sundered-reach
#
# PLAY WITH AI COMPANIONS (opt-in) — add a party of AI companions who adventure ALONGSIDE
# you, each its own agent acting through the same move palette you do (it can disagree,
# take the lead, even betray you). Name them with a 4th arg (or $CLAWDND_PLAY_COMPANIONS),
# COMMA-separated  Name:class:persona_file[:spell1|spell2|…]:
#         ./clawdnd-play.command baldurs-gate '' 8765 \
#           "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Guiding Bolt,Brogan:fighter:qa/play_companion.txt"
#   or:   CLAWDND_PLAY_COMPANIONS="Brogan:fighter:qa/play_companion.txt" ./clawdnd-play.command
# Companions multiply the live AI cost (each is its own `claude -p`), so they're OFF by
# default — double-clicking this file is exactly today's solo play. With NO companion spec
# this delegates straight to scripts/play.sh, byte-for-byte unchanged.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
# play_party.sh == solo play.sh when no companion spec is given, and adds the opt-in party
# when one is (via the 4th arg or $CLAWDND_PLAY_COMPANIONS). Routing through it keeps the
# double-click solo experience identical while enabling companions for those who want them.
exec "$PWD/scripts/play_party.sh" "$@"
