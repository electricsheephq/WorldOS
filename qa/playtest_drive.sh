#!/usr/bin/env bash
# THE WALKABILITY GATE (owner directive 2026-07-15: "walk the character through and screenshot").
# Beauty (the blind panel) and WALKABILITY are DIFFERENT gates. A plate can score 8.3 and still be
# unwalkable: actor projection off the plate, no occlusion behind pillars, door cells that don't sit
# on the painted doorway, props you walk through. This drives the live player over the QA channel
# (:8971 /click + /shot) and captures each state so a human (or a vision agent) SEES the breaks the
# beauty panel is blind to. Run this BEFORE declaring any room shippable — it is the missing gate.
#
# Usage: qa/playtest_drive.sh <out_dir> "c1,r1" "c2,r2" ...   (cells to walk through, in order)
# Requires: the owner player running with WORLDOS_QA_INPUT=1 (port 8971). Reads the shot from the
# app persistentDataPath (com.worldos.WorldOSPlayer/wos_shot.png).
set -uo pipefail
OUT="${1:?usage: playtest_drive.sh <out_dir> <cell> [cell...]}"; shift
PORT="${WORLDOS_QA_INPUT_PORT:-8971}"
SHOT_SRC="$HOME/Library/Application Support/com.worldos.WorldOSPlayer/wos_shot.png"
mkdir -p "$OUT"
_click(){ curl -s -X POST -H 'Content-Type: application/json' -d "{\"c\":$1,\"r\":$2}" "http://127.0.0.1:$PORT/click" >/dev/null; }
_shot(){ curl -s -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PORT/shot" >/dev/null; sleep 2; cp "$SHOT_SRC" "$1" 2>/dev/null; }
_dbg(){ curl -s -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PORT/debug" 2>/dev/null; }
i=0
for cell in "$@"; do
  c="${cell%,*}"; r="${cell#*,}"
  _click "$c" "$r"; sleep 3
  last=$(_dbg | python3 -c 'import sys,json; print(json.load(sys.stdin).get("last",""))' 2>/dev/null)
  _shot "$OUT/step$(printf %02d $i)_c${c}r${r}.png"
  echo "step $i -> click($c,$r) | debug: $last"
  i=$((i+1))
done
echo "captured $i frames to $OUT — READ each: is the character AT the clicked cell, on the FLOOR (not on a prop/wall), and OCCLUDED behind foreground pillars? imp=Y cells must be REJECTED (char does not stand on them)."
