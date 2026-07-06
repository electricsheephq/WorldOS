#!/usr/bin/env bash
# extensions/renderers/godot/qa/preview_scene.sh — scene-preview harness wrapper.
#
# Runs the Godot --preview-scene entrypoint, which:
#   1. Loads a JSON spec (backdrop path + nav grid + actors + path probe)
#   2. Renders the scene with the dimetric diamond grid overlay drawn by NavOverlay.gd
#   3. Saves a viewport screenshot PNG (real window — NOT --headless, which skips the GPU)
#   4. Writes a nav.json sidecar with A* path results
#
# USAGE:
#   extensions/renderers/godot/qa/preview_scene.sh <spec.json> [shot.png] [overlay]
#
#   spec.json  — path to the scene spec JSON (required). Example format:
#     {
#       "backdrop": "/tmp/art/tavern.png",
#       "nav": {"cols":12,"rows":8,"cell_w_px":72,"origin_px":[512,300],"blocked":[[3,3],[8,2],[6,5]]},
#       "actors": [{"id":"pc","cell":[1,4],"facing":"E"},{"id":"foe","cell":[10,4],"facing":"W"}],
#       "path_probe": {"from":[1,4],"to":[10,4]},
#       "camera": {"zoom":1.0}
#     }
#
#   shot.png   — output PNG path (default: /tmp/scene.png)
#   overlay    — none | grid | full (default: full)
#
# OUTPUT:
#   <shot.png>          — viewport screenshot with grid/path drawn
#   <shot>.nav.json     — {"path_found":bool,"path":[[c,r],...],"blocked_count":int,...}
#
# EXAMPLES:
#   # Simple corridor path (proof A):
#   extensions/renderers/godot/qa/preview_scene.sh /tmp/spec_clearpath.json /tmp/preview_clearpath.png full
#
#   # Blocked destination (proof B):
#   extensions/renderers/godot/qa/preview_scene.sh /tmp/spec_nopath.json /tmp/preview_nopath.png full
#
#   # Grid overlay only (no path or actor overlay):
#   extensions/renderers/godot/qa/preview_scene.sh /tmp/spec.json /tmp/scene.png grid
#
# CI NOTE: on a headless CI box use xvfb:
#   xvfb-run -a -s "-screen 0 1280x720x24" extensions/renderers/godot/qa/preview_scene.sh /tmp/spec.json
#
# GODOT BINARY: looks for godot at /Applications/Godot.app/Contents/MacOS/Godot on
# macOS, then falls back to `godot` on $PATH (Linux CI, xvfb lane).

set -euo pipefail

SPEC="${1:-}"
SHOT="${2:-/tmp/scene.png}"
OVERLAY="${3:-full}"

if [[ -z "$SPEC" ]]; then
    echo "[preview_scene] ERROR: spec path required" >&2
    echo "Usage: $0 <spec.json> [shot.png] [overlay]" >&2
    exit 1
fi

if [[ ! -f "$SPEC" ]]; then
    echo "[preview_scene] ERROR: spec file not found: $SPEC" >&2
    exit 1
fi

# Locate Godot binary.
GODOT_MAC="/Applications/Godot.app/Contents/MacOS/Godot"
if [[ -x "$GODOT_MAC" ]]; then
    GODOT="$GODOT_MAC"
elif command -v godot &>/dev/null; then
    GODOT="godot"
else
    echo "[preview_scene] ERROR: godot binary not found (tried $GODOT_MAC and \$PATH)" >&2
    exit 1
fi

# Locate the archived Godot project directory relative to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GODOT_PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$GODOT_PROJECT/project.godot" ]]; then
    echo "[preview_scene] ERROR: archived Godot project not found at $GODOT_PROJECT" >&2
    exit 1
fi

echo "[preview_scene] spec=$SPEC shot=$SHOT overlay=$OVERLAY"
echo "[preview_scene] godot=$GODOT project=$GODOT_PROJECT"

# Run Godot with a real window (NOT --headless — viewport capture needs the GPU).
# --quit-after 300 is a safety timeout (~5s at 60fps); the harness quits itself after capture.
"$GODOT" --path "$GODOT_PROJECT" --quit-after 300 \
    -- --preview-scene --spec "$SPEC" --shot "$SHOT" --overlay "$OVERLAY" 2>&1 \
    | tee /tmp/preview_scene.log

# Report outcome.
NAV_JSON="${SHOT%.png}.nav.json"
if [[ "${SHOT}" == *.png ]]; then
    NAV_JSON="${SHOT%.png}.nav.json"
else
    NAV_JSON="${SHOT}.nav.json"
fi

if [[ -f "$SHOT" ]]; then
    echo "[preview_scene] RESULT ok=true shot=$SHOT"
else
    echo "[preview_scene] RESULT ok=false shot_missing=$SHOT"
fi

if [[ -f "$NAV_JSON" ]]; then
    PATH_FOUND=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('path_found', False))" "$NAV_JSON" 2>/dev/null || echo "unknown")
    echo "[preview_scene] nav.json=$NAV_JSON path_found=$PATH_FOUND"
else
    echo "[preview_scene] nav.json not written: $NAV_JSON"
fi
