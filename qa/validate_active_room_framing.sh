#!/usr/bin/env bash
# validate_active_room_framing.sh — #1281 box-side validation for paint_combat_v1.cs `frameActiveRoom`.
#
# C# for the Unity renderer can only run on the GEX44 Unity box (no local Unity). This script documents +
# executes the two box checks the PR needs. Run it FROM THE BOX (or over the SSH drive loop) where the
# unity-mcp editor + reverse tunnel (box:8765 -> Mac viewer) are live and a multi-room campaign is loaded.
#
#   Prereqs (see gex44-unity-host / CANONICAL.md "Live machinery"):
#     - Unity 6 editor open on worldos-unity, unity-mcp bridge on :8080 reachable.
#     - reverse tunnel up: /combat-surface?campaign=<cid> returns tokens + a `grid` block for the ACTIVE room.
#     - a MULTI-ROOM campaign deployed (e.g. seed_gfx_church_crypt.py) so the plate spans >1 room-unit.
#
set -euo pipefail
BOX_ASSETS="/home/unity/worldos-unity/Assets/painterly/backdrops"
FLAG="$BOX_ASSETS/_frame_active_room.txt"
CAP="/home/unity/worldos-unity/Captures-Durable/m1_combat_v1.png"
SCRIPT="paint_combat_v1.cs"   # run via: unity-mcp code execute --no-safety-checks -f $SCRIPT

# MANUAL RUNBOOK, not a CI gate (not referenced by any .github/workflows/*.yml): the two
# `unity-mcp code execute` calls below are commented out because this environment has no live
# Unity/GEX44 session to drive. Without them this script asserts NOTHING — it only re-hashes
# whatever PNG is already sitting at $CAP from some earlier render. Refuse to print misleading
# PASS/inspect lines unless a human running this FROM THE BOX (live unity-mcp session) opts in.
if [ "${WORLDOS_LIVE_UNITY_SESSION:-0}" != "1" ]; then
  echo "== #1281 active-room framing validation: SKIPPED (manual-only) =="
  echo "This script requires a live Unity/GEX44 session (unity-mcp code execute) and cannot"
  echo "run unattended here. Uncomment the three '# unity-mcp code execute' calls below,"
  echo "then re-run FROM THE BOX with WORLDOS_LIVE_UNITY_SESSION=1 to actually execute the checks."
  exit 0
fi

echo "== #1281 active-room framing validation =="

# --- CHECK 1: flag OFF == byte-identical to current render (no _frame_active_room.txt, or it says 0) ---
echo "[1] flag OFF -> byte-identical baseline"
if grep -q '^# unity-mcp code execute' "$0"; then
  echo "    FAIL: the 'unity-mcp code execute' calls below are still commented out — uncomment"
  echo "    them (this file, CHECK 1/CHECK 2) before running with WORLDOS_LIVE_UNITY_SESSION=1."
  exit 1
fi
rm -f "$FLAG"                                   # absent flag == OFF (default)
# render (baseline), hash it
# unity-mcp code execute --no-safety-checks -f "$SCRIPT"
sha_off_a=$(shasum -a256 "$CAP" | awk '{print $1}')
echo "    baseline hash (flag absent): $sha_off_a"
printf '0\n' > "$FLAG"                          # explicit OFF
# unity-mcp code execute --no-safety-checks -f "$SCRIPT"
sha_off_b=$(shasum -a256 "$CAP" | awk '{print $1}')
echo "    hash (flag=0):               $sha_off_b"
[ "$sha_off_a" = "$sha_off_b" ] && echo "    PASS: flag=0 == flag-absent (default path unchanged)" \
  || { echo "    FAIL: flag=0 diverged from flag-absent"; exit 1; }
# NOTE: a bit-exact hash vs the PRE-PR script is the real byte-identity proof — capture it on the merged-base
#   commit first (git stash the change, render, hash), then compare. Non-determinism in Unity capture is
#   possible; if hashes differ, fall back to a pixel-diff (compare -metric AE) and require 0 differing pixels.

# --- CHECK 2: flag ON -> church multi-room plate framed to the ACTIVE room ---
echo "[2] flag ON -> active-room crop"
printf '1\n' > "$FLAG"                           # ON
# unity-mcp code execute --no-safety-checks -f "$SCRIPT"
# The execute() return string logs: "frameActiveRoom ON: grid CxR reqOrtho=.. -> ortho=.. panR=.. panU=.."
#   Assert (visual + log):
#     - ortho < 13  (a sub-full-frame room actually zoomed in), and >= 6.0 (floor honored).
#     - the ACTIVE room + ~1.5-cell margin fills the frame; inactive chambers are OUT of frame (or at the edge),
#       NOT the whole diorama floating in void (the FELT gap).
#     - actors/rings still sit ON their painted cells (registration preserved — the crop is camera-only).
#     - NO raw plate-void at the frame margins (the pan-clamp keeps the framed rect inside the plate rect).
echo "    -> inspect $CAP + the execute() log line; compare against the flag-OFF baseline (should be zoomed)."
echo "    -> optional: run qa/visual-critic on both to confirm the felt-overall lifts vs the 3.5 diorama score."

# cleanup: restore default OFF so live play is unaffected by the validation toggle.
rm -f "$FLAG"
echo "== done (flag restored to default OFF) =="
