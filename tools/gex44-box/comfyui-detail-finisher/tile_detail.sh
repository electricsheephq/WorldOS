#!/usr/bin/env bash
# tile_detail.sh <in.png> <out.png> <denoise>
# Runs a tiled, structure-locked SDXL + Tile-ControlNet img2img detail pass
# through the local ComfyUI HTTP API (127.0.0.1:8188) and writes the result to <out.png>.
set -euo pipefail
if [ "${WORLDOS_ALLOW_RETIRED_HOST:-0}" != "1" ]; then
  echo "GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md" >&2
  exit 2
fi

IN="${1:?usage: tile_detail.sh <in.png> <out.png> <denoise>}"
OUT="${2:?usage: tile_detail.sh <in.png> <out.png> <denoise>}"
DENOISE="${3:-0.35}"

COMFY_DIR="/root/comfyui"
COMFY_URL="http://127.0.0.1:8188"
WORKFLOW="${COMFY_DIR}/workflows/tile_detail.json"
PY="${COMFY_DIR}/venv/bin/python"

[ -f "$IN" ] || { echo "ERROR: input not found: $IN" >&2; exit 1; }
[ -f "$WORKFLOW" ] || { echo "ERROR: workflow not found: $WORKFLOW" >&2; exit 1; }

"$PY" - "$IN" "$OUT" "$DENOISE" "$WORKFLOW" "$COMFY_URL" "$COMFY_DIR" <<'PYEOF'
import sys, os, json, time, uuid, shutil, urllib.request, urllib.parse, mimetypes

in_path, out_path, denoise, workflow_path, base, comfy_dir = sys.argv[1:8]
denoise = float(denoise)

def http_json(path, data=None, headers=None):
    url = base + path
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())

# 1. Upload input image to ComfyUI's input dir via /upload/image (multipart)
def upload_image(path):
    fn = os.path.basename(path)
    boundary = "----comfyboundary" + uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    ctype = mimetypes.guess_type(path)[0] or "image/png"
    parts = []
    def add(name, value, filename=None, is_file=False):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            head += f'; filename="{filename}"'
            head += f'\r\nContent-Type: {ctype}'
        head += "\r\n\r\n"
        parts.append(head.encode() + (value if is_file else value.encode()) + b"\r\n")
    add("image", content, filename=fn, is_file=True)
    add("overwrite", "true")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    # returns {"name":..., "subfolder":..., "type":"input"}
    return resp["name"] if not resp.get("subfolder") else f'{resp["subfolder"]}/{resp["name"]}'

uploaded = upload_image(in_path)
print(f"[tile_detail] uploaded input as: {uploaded}")

# 2. Load workflow, patch input filename + denoise
with open(workflow_path) as f:
    wf = json.load(f)
patched = False
for nid, node in wf.items():
    if node.get("class_type") == "LoadImage":
        node["inputs"]["image"] = uploaded
        patched = True
    if node.get("class_type") in ("UltimateSDUpscaleNoUpscale", "UltimateSDUpscale", "KSampler"):
        node["inputs"]["denoise"] = denoise
        # keep seam-fix denoise in step with the main pass when present
        if "seam_fix_denoise" in node["inputs"]:
            node["inputs"]["seam_fix_denoise"] = denoise
assert patched, "no LoadImage node found in workflow"

client_id = uuid.uuid4().hex
payload = json.dumps({"prompt": wf, "client_id": client_id}).encode()
resp = http_json("/prompt", data=payload, headers={"Content-Type": "application/json"})
prompt_id = resp["prompt_id"]
print(f"[tile_detail] queued prompt_id={prompt_id} denoise={denoise}")

# 3. Poll /history/<id> until complete
t0 = time.time()
outputs = None
while True:
    time.sleep(2)
    hist = http_json(f"/history/{prompt_id}")
    if prompt_id in hist:
        entry = hist[prompt_id]
        status = entry.get("status", {})
        if status.get("completed") or status.get("status_str") == "success":
            outputs = entry.get("outputs", {})
            break
        if status.get("status_str") == "error":
            raise SystemExit(f"[tile_detail] ComfyUI reported error: {json.dumps(status)}")
    if time.time() - t0 > 1800:
        raise SystemExit("[tile_detail] timed out after 1800s")

# 4. Locate the SaveImage output and copy to out_path
saved = None
for nid, out in (outputs or {}).items():
    for img in out.get("images", []):
        if img.get("type") == "output":
            saved = img
            break
    if saved:
        break
assert saved, f"no output image in history: {json.dumps(outputs)[:500]}"

src = os.path.join(comfy_dir, "output", saved.get("subfolder", ""), saved["filename"])
shutil.copyfile(src, out_path)
print(f"[tile_detail] DONE in {time.time()-t0:.1f}s -> {out_path} (from {src})")
PYEOF
