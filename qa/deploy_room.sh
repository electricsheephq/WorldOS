#!/usr/bin/env bash
# deploy_room.sh — make a generated room the ACTIVE combat backdrop on the GEX44 box (gfx M-E).
#
# The renderer (paint_combat_v1.cs) reads the active room's plate from
# Assets/painterly/backdrops/_active_combat.txt, so the SAME live-combat loop plays on ANY generated
# room by swapping that config — no renderer edit. This deploys a local plate PNG to the box and points
# the renderer at it. Combat then plays on the new room (crypt / tavern / church / ...).
#
#   qa/deploy_room.sh <local_plate.png> [box_plate_name.png]
#
# Pre-req: the GEX44 ControlMaster at /tmp/gex44-cm.sock (gex44-unity-host skill).
set -euo pipefail

LOCAL_PLATE="${1:?usage: deploy_room.sh <local_plate.png> [box_plate_name.png]}"
BOX_NAME="${2:-$(basename "$LOCAL_PLATE")}"
CM="/tmp/gex44-cm.sock"
BOX="root@46.4.26.123"
BDIR="/home/unity/worldos-unity/Assets/painterly/backdrops"

[ -f "$LOCAL_PLATE" ] || { echo "no such plate: $LOCAL_PLATE" >&2; exit 1; }

scp -o ControlPath="$CM" "$LOCAL_PLATE" "$BOX:/tmp/$BOX_NAME"
ssh -o ControlPath="$CM" "$BOX" \
  "sudo -u unity cp /tmp/$BOX_NAME $BDIR/$BOX_NAME && printf '%s' '$BOX_NAME' | sudo -u unity tee $BDIR/_active_combat.txt"
echo "[deploy_room] active combat room -> $BOX_NAME (render paint_combat_v1.cs to see it)"
