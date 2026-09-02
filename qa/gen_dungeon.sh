#!/usr/bin/env bash
# gen_dungeon.sh — generate a PLAYABLE MULTI-ROOM DUNGEON in ONE command (the repeatable composition
# workflow; the owner's "make it repeatable, one-command"). Authors linked room-units (a seed) -> per unit:
# export --location -> carved greybox -> img2img (recipe chosen by biome) -> deploy with its location_id
# (writes the per-location plate map, #1231). The units link via Location.connections + door_cells; at
# runtime cross_door (the in-app "Cross" button) swaps the engine location and the renderer AUTO-PICKS the
# unit's plate by location (#1231) — hands-off.
#
#   qa/gen_dungeon.sh <seed_script.py> <campaign_id> [strength] [state_dir] [--dry-run]
#   e.g. qa/gen_dungeon.sh qa/seed_gfx_church_crypt.py camp_gfxchurchcrypt01
#
# Scenario img2img paints run SEQUENTIALLY (concurrent paints collide -> silent no-output). Pre-req: the
# GEX44 ControlMaster + the box reverse tunnel (box:8765 -> Mac viewer:8770) so the renderer reads
# /combat-surface; see project_worldos_box_recovery_controlmaster for re-establishing them.
set -euo pipefail

if [ "${WORLDOS_ALLOW_RETIRED_HOST:-0}" != "1" ]; then
  echo "GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md" >&2
  exit 2
fi

SEED="${1:?usage: gen_dungeon.sh <seed_script.py> <campaign_id> [strength] [state_dir] [--dry-run]}"
CID="${2:?campaign_id}"
STRENGTH="${3:-0.55}"
STATE_DIR="${4:-/tmp/gfx_dungeon}"
DRY=""; for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1; done
CM="/tmp/gex44-cm.sock"; BOX="root@46.4.26.123"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
GREYBOX="$REPO/extensions/renderers/unity/scripts/build_room_greybox.cs"
rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"

recipe_for() { case "$1" in
  *hell*|*lava*|*volcanic*) echo hell ;;
  *cathedral*|*nave*|*church*) echo church ;;
  *stair*|*staircase*) echo crypt_stair ;;
  *tavern*|*timber*) echo tavern ;;
  *) echo crypt ;;
esac; }

echo "[gen_dungeon] [1/2] author the linked room-units (seed=$SEED)"
WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$REPO/servers/engine" python "$REPO/$SEED" "$STATE_DIR" >/dev/null

# enumerate the locations carrying a scene_grid: "loc_id<TAB>biome+name" (the units to render)
UNITS=$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$REPO/servers/engine" python - "$CID" <<'PY'
import sys
sys.path.insert(0, "servers/engine")
import server
c = server._require(sys.argv[1])
for lid, loc in c.locations.items():
    sg = getattr(loc, "scene_grid", None)
    if sg is not None:
        biome = ((getattr(sg, "biome", "") or "") + " " + (getattr(loc, "name", "") or "")).lower()
        print(lid + "\t" + biome)
PY
)

echo "[gen_dungeon] [2/2] render each unit (greybox -> img2img -> deploy by location)"
while IFS=$'\t' read -r LID BIOME; do
  [ -z "$LID" ] && continue
  ROOM=$(recipe_for "$BIOME")
  echo "  unit $LID  ->  recipe '$ROOM'  (biome: ${BIOME:0:48})"
  [ -n "$DRY" ] && continue
  GEO="/tmp/dungeon_${LID}.json"
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$REPO/servers/engine" \
    python "$REPO/qa/export_scene_grid.py" "$CID" "$GEO" --location "$LID"
  scp -o ControlPath="$CM" "$GEO" "$BOX:/tmp/room_geometry.json" >/dev/null
  ssh -o ControlPath="$CM" "$BOX" "sudo -u unity cp /tmp/room_geometry.json /home/unity/worldos-unity/room_geometry.json"
  ~/.local/bin/unity-mcp code execute --no-safety-checks -f "$GREYBOX" >/dev/null
  scp -o ControlPath="$CM" "$BOX:/home/unity/worldos-unity/Captures-Durable/room_greybox.png" "/tmp/dungeon_${LID}_greybox.png" >/dev/null
  python3 "$REPO/extensions/renderers/godot/tools/generate_room.py" \
    --room "$ROOM" --base-plate "/tmp/dungeon_${LID}_greybox.png" --strength "$STRENGTH" \
    --num-outputs 1 --out "/tmp/dungeon_${LID}_paint" >/dev/null
  "$REPO/qa/deploy_room.sh" "/tmp/dungeon_${LID}_paint/room_${ROOM}_0.png" \
    "dungeon_${CID}_${LID}.png" "$CID" "$LID" >/dev/null
done <<< "$UNITS"

echo "[gen_dungeon] DONE — playable multi-room dungeon '$CID'${DRY:+ (DRY-RUN: plan only)}."
echo "  units link via Location.connections + door_cells; cross_door (the 'Cross' button) auto-swaps the plate."
echo "  state: $STATE_DIR | point the viewer at $CID + render paint_combat_v1.cs to play."
