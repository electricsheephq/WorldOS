# ComfyUI "detail-finisher" lane (WorldOS)

A headless ComfyUI install for a **tiled, structure-locked SDXL img2img detail pass** on
painterly game backdrops. Takes an input PNG (~1344x768 to 2760x1504) and runs a
denoise-~0.35 "detail" pass using SDXL base + an SDXL **Tile ControlNet** (structure lock),
tiled via **Ultimate SD Upscale (No Upscale)** so large backdrops fit in VRAM. Output keeps
the input resolution and aspect ratio.

Runs on `127.0.0.1:8188`. **Do NOT touch ports 8080 / 8765 or the `unity` user's editor
(pid on 8080).** This lane only owns the ComfyUI process and files under `/root/comfyui`.

## Start it

```bash
cd /root/comfyui && nohup venv/bin/python main.py --listen 127.0.0.1 --port 8188 > comfy.log 2>&1 & disown
# wait ~10s, then:
curl -s http://127.0.0.1:8188/system_stats
```

## Stop it (only ever this specific process — NEVER a broad pkill)

```bash
PID=$(pgrep -f "main.py --listen 127.0.0.1 --port 8188" | head -1)
kill "$PID"
```

## Driver script

```bash
/root/comfyui/tile_detail.sh <in.png> <out.png> <denoise>
# example (the proven proof pass):
/root/comfyui/tile_detail.sh /tmp/crisp_diff.png /tmp/crisp_diff_tiled.png 0.35
```

The driver uploads the input via the `/upload/image` API, loads
`/root/comfyui/workflows/tile_detail.json`, patches in the input filename + denoise,
POSTs to `/prompt`, polls `/history/<id>` until done, and copies the SaveImage output
(from `/root/comfyui/output/`) to `<out.png>`.

Proven proof pass: input 2760x1504 -> output 2760x1504, ~164s, peak ~12.4 GB VRAM,
output mean pixel 0.348 (valid, non-black).

## Pipeline (workflows/tile_detail.json, API format)

`CheckpointLoaderSimple` -> `LoadImage` -> `CLIPTextEncode` (pos/neg) ->
`ControlNetLoader` (tile) -> `ControlNetApplyAdvanced` (strength 0.75, applied to the
source image = structure lock) -> `UltimateSDUpscaleNoUpscale`
(denoise 0.35, cfg 6.0, dpmpp_2m/karras, 24 steps, 1024x1024 tiles, tiled_decode,
Band-Pass seam fix) -> `SaveImage`.

To change detail strength, pass a different denoise arg (0.25 subtle .. 0.45 aggressive);
higher drifts further from the source structure.

## Models

| File | Path | Size (bytes) | Size (h) | Source |
|------|------|-------------|----------|--------|
| sd_xl_base_1.0.safetensors | /root/comfyui/models/checkpoints/ | 6938078334 | 6.5G | huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 (sd_xl_base_1.0.safetensors) |
| controlnet-tile-sdxl.safetensors | /root/comfyui/models/controlnet/ | 2502139104 | 2.4G | huggingface.co/xinsir/controlnet-tile-sdxl-1.0 (diffusion_pytorch_model.safetensors, renamed) |

## Env

- venv: `/root/comfyui/venv` (torch 2.6.0+cu124, CUDA available on RTX 4000 SFF Ada)
- custom node: `ComfyUI_UltimateSDUpscale` (ssitu) in `/root/comfyui/custom_nodes/`
- log: `/root/comfyui/comfy.log`
