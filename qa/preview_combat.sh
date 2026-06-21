#!/usr/bin/env bash
# qa/preview_combat.sh — drive a real ENGINE-RUN combat into the LIVE PREVIEW so you can WATCH it
# play out in the web #battle viewer (= exactly what the Mac app's battle tab renders).
#
# The web viewer (viewer/server.py -> /openworlds/#battle) already renders combat — tokens, HP,
# initiative, command center, battle log — by polling /combat-surface off the engine snapshot.
# This harness seeds a sandbox combat under play-state/preview and steps the v2.0 competent engine
# AI round-by-round with a delay, so the polling viewer shows the fight UNFOLD (no LLM, no tokens).
#
# Usage:  qa/preview_combat.sh [delay_s] [seed] [rounds]
#   delay_s  seconds between rounds (default 5 — >= the viewer's ~5s combat poll so each round shows)
#   seed     deterministic dice seed (default 11)
#   rounds   safety cap (default 25)
#
# First make sure the viewer is up:  (in the agent) preview_start worldos-viewer   -> port 8799
# Then open:  http://127.0.0.1:8799/openworlds/#battle
set -euo pipefail
cd "$(dirname "$0")/.."
DELAY="${1:-5}"
SEED="${2:-11}"
ROUNDS="${3:-25}"
export WORLDOS_STATE_DIR="$(pwd)/play-state/preview"
mkdir -p "$WORLDOS_STATE_DIR/campaigns"
echo "preview_combat: WORLDOS_STATE_DIR=$WORLDOS_STATE_DIR  delay=${DELAY}s/round  seed=$SEED"
echo "viewer must be running on :8799 (preview_start worldos-viewer); watch http://127.0.0.1:8799/openworlds/#battle"
exec uv run --directory servers/engine python ../../qa/preview_combat_driver.py "$DELAY" "$SEED" "$ROUNDS"
