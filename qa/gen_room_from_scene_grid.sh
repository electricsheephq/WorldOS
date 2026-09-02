#!/usr/bin/env bash
# gen_room_from_scene_grid.sh — generate a painterly room FROM an authored scene_grid, with pathing
# aligned by construction (gfx M-E). This is the "how do we generate a room" pipeline, one command:
#
#   scene_grid (authored geometry) --> greybox render --> img2img paint --> deploy as combat backdrop
#
# The painted props land on the SAME cells the combat pathing (impassable_cells) derives from the SAME
# scene_grid, so a fight on the generated room routes around the painted geometry — never decoupled,
# never reverse-engineered from pixels. See docs/roadmap/ROOM-GENERATION-AND-PATHING.md.
#
#   qa/gen_room_from_scene_grid.sh <campaign_id> <room_type> [strength] [state_dir]
#
# Pre-reqs: a seeded campaign with a scene_grid on its current location (e.g. qa/seed_gfx_combat.py);
# the GEX44 ControlMaster at /tmp/gex44-cm.sock (gex44-unity-host skill); ~/.worldos/scenario.key.
set -euo pipefail

if [ "${WORLDOS_ALLOW_RETIRED_HOST:-0}" != "1" ]; then
  echo "GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md" >&2
  exit 2
fi

CID="${1:?usage: gen_room_from_scene_grid.sh <campaign_id> <room_type> [strength] [state_dir]}"
ROOM="${2:?room_type (crypt|tavern|church|...)}"
STRENGTH="${3:-0.55}"   # default: structure-faithful (interior props stay on-cell). ~0.7 = more painterly.
STATE_DIR="${4:-/tmp/gfx_state}"
CM="/tmp/gex44-cm.sock"
BOX="root@46.4.26.123"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/worldos-session-notes/scenario-assets/${ROOM}_authored"
GEO_LOCAL="/tmp/${ROOM}_room_geometry.json"

echo "[1/5] export the authored scene_grid -> geometry json"
WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$REPO/servers/engine" \
  python "$REPO/qa/export_scene_grid.py" "$CID" "$GEO_LOCAL"

echo "[2/5] deploy geometry to the box + render the greybox at the contract camera"
scp -o ControlPath="$CM" "$GEO_LOCAL" "$BOX:/tmp/room_geometry.json"
ssh -o ControlPath="$CM" "$BOX" "sudo -u unity cp /tmp/room_geometry.json /home/unity/worldos-unity/room_geometry.json"
~/.local/bin/unity-mcp code execute --no-safety-checks -f "$REPO/extensions/renderers/unity/scripts/build_room_greybox.cs"
scp -o ControlPath="$CM" "$BOX:/home/unity/worldos-unity/Captures-Durable/room_greybox.png" "/tmp/${ROOM}_greybox.png"

echo "[3/5] img2img the greybox -> painterly room (strength=$STRENGTH)"
python3 "$REPO/extensions/renderers/godot/tools/generate_room.py" \
  --room "$ROOM" --base-plate "/tmp/${ROOM}_greybox.png" --strength "$STRENGTH" --out "$OUT"

echo "[4/5] deploy the best variant as the active combat backdrop"
"$REPO/qa/deploy_room.sh" "$OUT/room_${ROOM}_0.png" "${ROOM}_authored.png"

echo "[5/5] DONE — render paint_combat_v1.cs to play on the authored room."
echo "      painted props == scene_grid props == combat impassable_cells (aligned by construction)."
echo "      greybox=/tmp/${ROOM}_greybox.png  painted=$OUT/room_${ROOM}_0.png"
