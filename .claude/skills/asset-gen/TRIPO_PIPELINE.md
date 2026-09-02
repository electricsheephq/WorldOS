# Tripo3D v3 pipeline runbook (text/image → model → rig → animate)

The full headless recipe for a **rigged + animated 3D character or creature** via Tripo3D.
**Every fact here was live-tested 2026-06-28** against the real API; where it differs from the
public docs, trust this file. The CLI wrapper `extensions/renderers/godot/tools/tripo_gen.py`
implements exactly this recipe — prefer it; drop to raw curl only when debugging.

> **When to use Tripo (vs Meshy):** Tripo is the **only** provider that rigs **creatures**
> (quadruped/avian/serpentine/aquatic). For humanoids, Tripo and Meshy are interchangeable —
> Meshy is cheaper (see the decision tree in `SKILL.md`).

## Auth + conventions
- Base `https://openapi.tripo3d.ai/v3`. Header `Authorization: Bearer <key>` (key in `~/.worldos/tripo3d.key`, env `WORLDOS_TRIPO_API_KEY`).
- **Task-creating** POSTs (`/generation/*`, `/animations/*`) return `{"code":0,"data":{"task_id":"..."}}`. (The one exception is the upload step `POST /files`, which returns `data.file_token`, not a task.) Poll `GET /tasks/{id}` (**plural** — `/task/{id}` 404s) at **≥3 s** (1 req/s limit → 429).
- Success output URL is `data.output.model_url` and **expires ~5 min** — download immediately.
- Status flow: `queued → running → success` (or `failed`).
- Balance: `GET https://api.tripo3d.ai/v2/openapi/user/balance` → `data.balance`.

## CLI (the easy path)
```bash
# one-shot: generate a HUMANOID, rig it, and emit Unity-ready FBX clips
tripo_gen.py text --prompt "a human elf ranger, leather armor, hooded cloak, A-pose, game-ready" \
  --out /tmp/ranger --rig --out-format fbx --animations walk idle run slash

# CREATURE (only Tripo can): rig_type is auto-detected by rig-check
tripo_gen.py text --prompt "a dire wolf, quadruped, on all fours, game-ready" \
  --out /tmp/wolf --rig --out-format fbx --animations walk

# rig an EXISTING generation task (skip regen)
tripo_gen.py rig --task <generation_task_id> --out /tmp/x --out-format fbx
tripo_gen.py --test-key          # auth smoke (no spend)
tripo_gen.py text ... --dry-run  # plan + est credits, no API call
```

## Raw REST steps (what the wrapper does)

### 1. Generate — `POST /generation/text-to-model`
```json
{ "prompt": "<text>", "model": "P1-20260311" }
```
- **No `type` field** (the old `/generation/text` 404s now).  Field is `model`, **not** `model_version`.
- **Model ids are DATE-STAMPED** — friendly names (`tripo-p1`, `v3.1`) are **rejected**. Current set
  (a 400 error lists the live set when one expires): `P1-20260311` (game/low-poly, best for characters),
  `v3.1-20260211` (default hi-precision), `v3.0-20250812`, `v2.5-20250123`. **Pin the id** in production.
- Image path: upload via `POST /files` (multipart) → `data.file_token`, then
  `POST /generation/image-to-model {"model": "...", "file": {"type":"image","file_token":"..."}}`.
- Poll → `output.model_url` (a `.glb`).

### 2. Rig-check — `POST /animations/rig-check` (FREE, 0 credits)
```json
{ "input": "<generation_task_id>" }
```
- Returns `output.{riggable, rig_type}`. **`rig_type` is recommended by the API** — read it, don't
  hardcode. Enum: `biped, quadruped, hexapod, octopod, avian, serpentine, aquatic`. (Humanoid→`biped`,
  wolf→`quadruped`, both confirmed.) Abort if `riggable:false`.

### 3. Rig — `POST /animations/rig`
```json
{ "input":"<gen_task_id>", "model":"v2.5-20260210", "rig_type":"<from rig-check>",
  "spec":"mixamo", "out_format":"fbx" }
```
- **`model` MUST be `v2.5-20260210`** — it rigs **both** bipeds and creatures. The server default
  (`v2.5-20250123`) is **rejected**; `v1.0-20240301` rigs but its **retarget then fails** — avoid it.
- `spec`: `mixamo` for biped (Unity-friendly bone names), `tripo` for creatures.
- `out_format`: `fbx` → Unity-ready directly (**no Blender**); `glb` otherwise.
- Poll → `output.model_url` = the rigged model.

### 4. Retarget (apply animations) — `POST /animations/retarget`
```json
{ "input":"<rigged_task_id>", "animations":["preset:biped:walk"], "out_format":"fbx" }
```
- **Presets are NAMESPACED by rig_type: `preset:<rig_type>:<name>`.** `preset:biped:walk`,
  `preset:quadruped:walk`. **Bare `preset:walk` FAILS** (the docs showing bare names are wrong).
- **ONE preset per call.** Multi-preset `animations` arrays **fail** (even with individually-valid
  names) — loop, one retarget task per clip.
- Verified biped names: `walk`, `idle`, `run`, `slash`. Quadruped: `walk` (creatures support far fewer).
  An unsupported name fails that one task (the wrapper treats per-clip failures as non-fatal).
- Poll → `output.model_url` = the animated model (one clip).

## Credits / timing (measured)
- Full humanoid gen→rig→1 retarget ≈ **~100 credits, ~3 min**. rig-check is free. The whole
  humanoid+creature validation run cost **215 credits**.

## Unity consumption (critical — see also the Unity section of `SKILL.md`)
- Import the rigged/animated FBX as **`animationType = Generic`, NOT Humanoid.** Tripo's bone names
  (`tripo::Spine_0`, …) don't auto-map to Unity's Humanoid avatar, so a **Humanoid import silently
  drops the animation clips** (`clipAnims=0`). Generic preserves them (verified: walk clip 2.33 s).
- FBX imports **untextured** (flat) — assign the albedo separately (the M1.0 pale-albedo gotcha;
  Tripo embeds texture in the GLB, not always the FBX). Rig + animation are unaffected.
- The local Unity host is `/Users/m1/worldos-unity`; if you ever need GLB→FBX, convert Mac-side. For
  Tripo, prefer `out_format=fbx` and skip Blender entirely.
