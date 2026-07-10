#!/bin/bash
# Primary A/B evidence set at controlStrength 0.5 (painterly freedom). Run from repo root.
set -e
cd "$(dirname "$0")/../../.."
SG="extensions/renderers/godot/tools/scenario_gen.py"
FLUX="model_bfl-flux-1-dev"
CS=0.5
CTL="qa/evidence/tiled-space/controls"
PLATES="qa/evidence/tiled-space/plates"
STYLE="moody hand-painted painterly environment art, Pillars of Eternity Deadfire isometric backdrop, dramatic chiaroscuro, warm firelight vs cool teal shadow, dark earthy palette, dimetric bird's-eye view, lush organic painted forms, cohesive scene, no visible geometry blocks, no characters, no text"
mkdir -p "$PLATES"

echo "=== [1/4] ARM A wide 2-room @2048 cs$CS ==="
python3 "$SG" controlnet --model-id "$FLUX" --control-modality depth --control-strength $CS \
  --num-samples 1 --width 2048 --height 768 --seed 1508 --control-image "$CTL/wide2_depth.png" \
  --out "$PLATES" --scope wideA \
  --prompt "a forsaken war camp clearing at dusk opening eastward into a dark pine forest path, $STYLE"

echo "=== [2/4] ARM A escalation 3-room @2048 cs$CS (0.67x density) ==="
python3 "$SG" controlnet --model-id "$FLUX" --control-modality depth --control-strength $CS \
  --num-samples 1 --width 2048 --height 768 --seed 1508 --control-image "$CTL/wide3_depth.png" \
  --out "$PLATES" --scope wide3 \
  --prompt "a war camp clearing opening into a long dark pine forest path receding into deep woods, $STYLE"

echo "=== [3/4] Baseline single-room + ARM B tile1 (tileL control) @1024 cs$CS ==="
python3 "$SG" controlnet --model-id "$FLUX" --control-modality depth --control-strength $CS \
  --num-samples 1 --width 1024 --height 768 --seed 1508 --control-image "$CTL/tileL_depth.png" \
  --out "$PLATES" --scope tile1 \
  --prompt "a forsaken war camp clearing at dusk, campfire embers, bedrolls, supply crates, timber palisade, trampled earth, $STYLE"
cp "$PLATES/tile1.png" "$PLATES/baseline.png"

echo "=== [4/4] ARM B negative control — naive tile2 (no conditioning) @1024 cs$CS ==="
python3 "$SG" controlnet --model-id "$FLUX" --control-modality depth --control-strength $CS \
  --num-samples 1 --width 1024 --height 768 --seed 1508 --control-image "$CTL/tileR_depth.png" \
  --out "$PLATES" --scope tile2_naive \
  --prompt "a dark pine forest path continuing from a camp clearing, mossy boulders, fallen logs, dense conifers flanking a trodden dirt trail, $STYLE"

echo "=== PRIMARY SET DONE ==="; ls -la "$PLATES"/*.png | sed 's|.*/||'
