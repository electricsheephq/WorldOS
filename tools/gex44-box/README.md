# GEX44 box — preserved tooling & reproducible setup

The GEX44 GPU box (`root@46.4.26.123`, RTX 4000 Ada / 64GB) was WorldOS's primary Unity render
host + image-detail lane. It is being decommissioned. This directory versions the box-only tooling
that previously lived nowhere but the box, so the setup is reproducible and nothing is lost with the
hardware.

**The canonical Unity project itself is NOT here** — it is fully committed and pushed to
`github.com/100yenadmin/worldos-unity-2026-06-30_07-21-47` (incl. Git-LFS). This dir is the
*surrounding* ops/tooling that sat outside that repo.

## Contents

### `ops/`
- **`worldos-unity-save.sh`** — the box's autosave mechanism (cron, runs as `unity`): commit-if-dirty
  locally + best-effort LFS push. `--push` does the daily GitHub push. This is the persistence
  contract referenced across the WorldOS runbooks ("end every box session with this").
- **`launch_editor.sh`** — headless/hidpi Unity editor launcher for the box.
- **`derive_gbuffer.py`** — depth/normal G-buffer derivation for the render pipeline.
- **`setup_depth.sh`** — depth-pass environment setup.
- **`INSTALL.md`** — box bring-up / install notes.

### `comfyui-detail-finisher/`
A headless ComfyUI lane for a **tiled, structure-locked SDXL img2img detail pass** on painterly game
backdrops (denoise ~0.35, SDXL base + Tile ControlNet, tiled via Ultimate SD Upscale so large plates
fit in VRAM; keeps input resolution/aspect). Ran on `127.0.0.1:8188`.
- `README-WORLDOS.md` — full start/stop + usage.
- `tile_detail.sh` — driver script.
- `workflows/tile_detail.json` — the ComfyUI workflow graph (the reusable asset).

### `display-config/`
The 4K-readable remote-desktop (Xorg/openbox/sunshine) profile used to drive the headed editor over
a remote session.

## Not committed here (archived on LEXAR only)
- The vLLM model-serving scripts (`h4-vllm-*.sh`) — a separate, non-WorldOS use of the GPU box.
- ComfyUI `output/` sample renders + `user/comfyui.db`.
- Full box render caches (`Captures*`, `BuildOutput`) — regenerable; the curated renders live in
  the WorldOS session-notes.

Backup location: `LEXAR/Codex/session-notes/2026-07-24/worldos-disk-cleanup-fork/artifacts/`.
