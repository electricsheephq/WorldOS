# Meshy pipeline runbook (text-to-3d → rig → animate)

The full headless recipe for a **rigged + animated HUMANOID** via Meshy. **Live-tested 2026-06-28.**
The CLI wrapper `extensions/renderers/godot/tools/meshy_gen.py` implements this — prefer it.

> **Meshy is HUMANOID ONLY.** Rigging 422s on non-humanoid meshes ("Pose estimation failed").
> For creatures use `tripo_gen.py`. Meshy's edge for humanoids: the 5-credit rig **includes free
> walk + run clips**, so a usable animated PC costs ~5 cr (vs Tripo's ~100).

## Auth + conventions
- Base `https://api.meshy.ai`, header `Authorization: Bearer msy_<key>` (key in `~/.worldos/meshy.key`, env `WORLDOS_MESHY_API_KEY`).
- **Version split (footgun):** text-to-3d = `/openapi/v2`; **rigging + animation = `/openapi/v1`**.
- Every POST returns `{"result":"<task-uuid>"}`. Poll `GET <path>/{id}`. Status:
  `PENDING → IN_PROGRESS → SUCCEEDED` (or `FAILED`; `consumed_credits:0` on FAILED = auto-refund).
- Output URLs are short-lived signed `assets.meshy.ai` links — download promptly.
- Balance: `GET /openapi/v1/balance` → `{"balance": N}`.

## CLI (the easy path)
```bash
# generate a humanoid, rig it (+ free walk/run), add Idle(0) and Attack(4)
meshy_gen.py --prompt "a human elf ranger, leather armor, hooded cloak, game-ready" \
  --out /tmp/ranger --rig --animate 0 4

# rig an EXISTING Meshy model task (skip generation)
meshy_gen.py --rig-from-task <meshy_model_task_id> --out /tmp/x --animate 0
meshy_gen.py --test-key            # auth smoke (no spend)
meshy_gen.py --prompt "..." --out /tmp/x --dry-run
```

## Raw REST steps

### 1. Text-to-3D — `POST /openapi/v2/text-to-3d` (two stages)
- **preview** (geometry): `{"mode":"preview","prompt":"<=600 chars","ai_model":"meshy-5","model_type":"standard","pose_mode":"t-pose","target_formats":["glb"],"should_remesh":true,"topology":"triangle","target_polycount":30000,"auto_size":true,"origin_at":"bottom"}` → `{"result":"<preview_id>"}`.
- **refine** (PBR texture): `{"mode":"refine","preview_task_id":"<preview_id>","enable_pbr":true,"target_formats":["glb"]}` → `<refine_id>`.
- Poll `GET /openapi/v2/text-to-3d/{id}` → `model_urls.{glb,fbx,...}`.

### 2. Rigging — `POST /openapi/v1/rigging` (~5 cr, HUMANOID only)
```json
{ "input_task_id": "<refine_id or model task>", "height_meters": 1.7 }
```
- One source: `input_task_id` (a prior Meshy task; ≤300k faces) **or** `model_url` (public `.glb`).
- Poll `GET /openapi/v1/rigging/{id}` → `result` has:
  - `rigged_character_fbx_url`, `rigged_character_glb_url`
  - **`basic_animations.{walking,running}_{glb,fbx}_url`** — free walk + run, no extra call.
- Non-humanoid → **422** "Pose estimation failed". (Mesh *generation* is unrestricted; only
  rig/animate are humanoid-locked.)

### 3. Animation — `POST /openapi/v1/animations` (~3 cr each)
```json
{ "rig_task_id": "<rigging_id>", "action_id": 0 }
```
- `action_id` is an **integer** from the animation library (no REST list endpoint; scrape/cache the
  id→name map from docs). Examples: `0`=Idle, `4`=Attack, `14`=Run_02, `125`=Charged_Spell_Cast.
- Poll `GET /openapi/v1/animations/{id}` → `result.{animation_fbx_url, animation_glb_url}`.
- All actions are biped-only (inherits the humanoid constraint).

## Credits (measured / published)
- text-to-3d preview 5–20 cr, refine 10 cr; **rigging 5 cr** (incl. free walk/run); **animation 3 cr** each.
- A usable animated humanoid (rig + 1 clip) ≈ **8 cr** + generation.

## Unity consumption (critical)
- Import the rigged FBX as **`animationType = Generic`, NOT Humanoid** — the Meshy bone names don't
  auto-map to Unity's Humanoid avatar, so Humanoid import **silently drops the clips**. Generic
  preserves them (verified: Idle clip 4.00 s, 26-joint skeleton).
- The **Unity-plugin "Bridge to Unity" is GUI-only / not agent-automatable** — bypass it; drive the
  REST API and import the FBX yourself (CoplayDev unity-mcp on the GEX44 box).
