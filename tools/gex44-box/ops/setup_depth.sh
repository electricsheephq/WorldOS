set -e
if [ "${WORLDOS_ALLOW_RETIRED_HOST:-0}" != "1" ]; then
  echo "GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md" >&2
  exit 2
fi

python3 -m venv /root/depth_env
/root/depth_env/bin/pip install --quiet --upgrade pip
/root/depth_env/bin/pip install --quiet torch --index-url https://download.pytorch.org/whl/cu121
/root/depth_env/bin/pip install --quiet transformers pillow numpy
/root/depth_env/bin/python -c "from transformers import pipeline; pipeline(\"depth-estimation\", model=\"depth-anything/Depth-Anything-V2-Small-hf\"); print(\"DEPTH_ANYTHING_READY\")"
