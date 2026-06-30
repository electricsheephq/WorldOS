#!/usr/bin/env bash
# deploy_room.sh — make a generated room the ACTIVE combat backdrop on the GEX44 box (gfx M-E).
#
# The renderer (paint_combat_v1.cs) reads the active room's plate from
# Assets/painterly/backdrops/_active_combat.txt AND the active campaign from _active_campaign.txt, so the
# SAME live-combat loop plays on ANY generated room+campaign by swapping that config — no renderer edit.
# This deploys a local plate PNG to the box and points the renderer at it (+ optionally at a campaign id).
# Combat then plays on the new room (crypt / tavern / church / ...).
#
#   qa/deploy_room.sh <local_plate.png> [box_plate_name.png] [campaign_id]
#
# Pre-req: the GEX44 ControlMaster at /tmp/gex44-cm.sock (gex44-unity-host skill).
set -euo pipefail

LOCAL_PLATE="${1:?usage: deploy_room.sh <local_plate.png> [box_plate_name.png] [campaign_id]}"
BOX_NAME="${2:-$(basename "$LOCAL_PLATE")}"
CAMPAIGN="${3:-}"
CM="/tmp/gex44-cm.sock"
BOX="root@46.4.26.123"
BDIR="/home/unity/worldos-unity/Assets/painterly/backdrops"

[ -f "$LOCAL_PLATE" ] || { echo "no such plate: $LOCAL_PLATE" >&2; exit 1; }

scp -o ControlPath="$CM" "$LOCAL_PLATE" "$BOX:/tmp/$BOX_NAME"
ssh -o ControlPath="$CM" "$BOX" \
  "sudo -u unity cp /tmp/$BOX_NAME $BDIR/$BOX_NAME && printf '%s' '$BOX_NAME' | sudo -u unity tee $BDIR/_active_combat.txt"
# Point the renderer at the room's campaign so the LIVE combat surface (cells/pathing) matches the plate.
if [ -n "$CAMPAIGN" ]; then
  ssh -o ControlPath="$CM" "$BOX" "printf '%s' '$CAMPAIGN' | sudo -u unity tee $BDIR/_active_campaign.txt"
  echo "[deploy_room] active campaign -> $CAMPAIGN"
fi
echo "[deploy_room] active combat room -> $BOX_NAME (render paint_combat_v1.cs to see it)"
