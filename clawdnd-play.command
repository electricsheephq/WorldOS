#!/usr/bin/env bash
# Play ClawDnD in the dashboard — DOUBLE-CLICK this file to start a new adventure you
# play in your browser (no terminal typing needed). It opens the play dashboard and runs
# a live AI Dungeon Master beside it: you act through the action palette (Say / Do /
# Continue, dice & combat, click-to-travel) and the DM narrates, voices the cast, and
# resolves your moves live, turn by turn. This is the dashboard counterpart to
# `/world-play`. Close this window (or Ctrl-C) to stop.
#
# Optional args (passed straight through): [world-id] [run-id] [port]
#   e.g.  open the file normally for the default world, or run from a terminal:
#         ./clawdnd-play.command sundered-reach
set -uo pipefail
cd "$(dirname "$0")" || exit 1
exec "$PWD/scripts/play.sh" "$@"
