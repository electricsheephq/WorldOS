---
name: asset-gen
description: Generate game art for WorldOS via Meshy / Tripo3D / Scenario / PixelLab — 3D characters, auto-rigging, animation, painterly backgrounds, sprites, tilesets. Use when creating or regenerating visual assets for the active Unity renderer lane, the archived Godot GT2 reference extension, the future GT1 tile engine, or when wiring a non-Eva image-gen provider. Routes the right service to each job and gives the keys, the CLI wrappers, the MCPs, and the sprite-sheet bake contract.
---

# WorldOS AI asset-generation toolkit

Four services cover every visual asset need. Keys are stored OUTSIDE the repo; two services
are wired as MCPs; all four have CLI wrappers (or a stub). Renderer lanes read only the
**sprite-sheet manifest**; the archived Godot reference notes live at
`extensions/renderers/godot/HANDOFF.md` §4.6, so any source drops in at the same `scope_key`
with zero renderer change.

## ★ WorldOS Painterly LoRA — the PERSISTENT consistent style (2026-06-27)
The locked WorldOS visual style is a TRAINED Scenario LoRA: **`model_MB22WaRCBLtfhi5R2CRpHoEL` "WorldOS Painterly"**
(zimage-lora, trained on 6 original moody-painterly assets; Scenario project `proj_sd4w3ozsJaBHYnHS4mP1S2Pw`). Generate ALL
game art THROUGH it for one cohesive BG/PoE/Disco-caliber painted look — characters, enemies, props, scenes. Proven: a
skeleton (NOT in the dataset) rendered in the exact style; ~6 CU/gen (8× cheaper than GPT Image 2).

**On-demand asset recipe (the repeatable workflow):**
1. **Generate** (scenario MCP, MAIN session — the LoRA CANNOT be invoked by its own id; run the base + loras):
   `model_run model_id=model_z-image parameters={ prompt:"<subject>, moody hand-painted painterly, dramatic chiaroscuro,
   dark earthy palette, warm firelight rim vs cool shadow, isolated and centered on a plain flat dark neutral background,
   full body, feet visible", loras:["model_MB22WaRCBLtfhi5R2CRpHoEL"], lorasScale:[0.9], width:1024,
   height:1536 (chars) | 1024 (props), numOutputs:1-2, guidance:5 }`  — use `wait:false` (z-image LoRA cold-start >180s) + poll `job_check`.
2. **Cut out:** `model_run model_id=model_bria-remove-background parameters={ image:<asset_id>, preserveAlpha:true }` → clean RGBA (~3 CU).
3. **Download:** `asset_download` → `curl -L` → box `Assets/painterly/sprites/<id>_idle.png`.
4. **Integrate:** ARM-1A `WorldOS/SpriteBillboard` (quad localEuler (0,180,0), scale ACTOR_TARGET_H×sprite-aspect, feet grounded) —
   see `scratchpad/swap_render.cs`. Engine = SOLE WRITER; sprites are renderer-local view only.
**★ COPYRIGHT GUARD:** NEVER train on / feed the BG/PoE/Disco reference frames (calibration-only). Bootstrap original datasets via text-gen.
**Retrain/extend the LoRA:** `model_create(data.type="zimage-lora")` → `train(action=upload_images, images=[asset_ids])` → `train(action=start)`. ~576 CU / ~40 min for 6 imgs, 5–15 recommended.

## ★ The router — which tool for which job (decide in this order)

Pick the FIRST rule that matches. Full step-by-step recipes: **[TRIPO_PIPELINE.md](TRIPO_PIPELINE.md)**
and **[MESHY_PIPELINE.md](MESHY_PIPELINE.md)** (both live-verified 2026-06-28).

1. **Painterly 2D backdrop / diorama / engine image** → **Scenario** (through the WorldOS Painterly
   LoRA above). `scenario_gen.py` or the `scenario` MCP, or the engine provider `WORLDOS_IMAGE_PROVIDER=scenario`.
2. **Pixel sprite / tileset** → **PixelLab** (GT1-future; `pixellab` MCP).
3. **3D rigged + animated, HUMANOID (biped)** → **Meshy** (cheapest: ~5 cr rig **includes free
   walk/run**, +3 cr/clip) — `meshy_gen.py --rig --animate ...`. **Fallback: Tripo** (richer preset
   set, same code path as creatures) — `tripo_gen.py text --rig`.
4. **3D rigged + animated, CREATURE (quadruped/avian/serpentine/…)** → **Tripo ONLY** — Meshy 422s on
   non-humanoids. `tripo_gen.py text --rig` (rig-check auto-detects the rig_type). **No fallback.**
5. **3D static / low-poly prop** → **Tripo `--lowpoly` (P1)** or Meshy `model_type=lowpoly`.

*(A programmatic `asset_router.py` is intentionally deferred — selection is an agent decision today;
this numbered tree IS the router. The 2D-image-gen router that DOES exist is `servers/engine/imagegen.py`,
`WORLDOS_IMAGE_PROVIDER` — Tripo/Meshy/PixelLab are 3D/sprite, not in it.)*

## ★ Unity consumption (the live renderer lane — read before importing a rigged FBX)

The shipping in-repo renderer is **Godot 2D** (it BAKES a GLB to 8-facing sprite PNGs via
`bake_sprites.py` → `pack_sheet.py`; that path is **reference/quarantined** and does NOT use rig
or animation data). The **live lane is Unity-direct 3D actors** on the GEX44 box (PoE2 pivot, M1.0)
— that is what rig+animate feeds. Two load-bearing import facts (verified 2026-06-28):

- **Import the rigged FBX as `animationType = Generic`, NOT Humanoid.** Tripo/Meshy bone names don't
  auto-map to Unity's Humanoid avatar, so a Humanoid import **silently drops the animation clips**.
  Generic preserves them.
- Prefer **FBX output** (Tripo `--out-format fbx`, Meshy returns FBX) — Unity-ready directly, **no
  Blender**. The box has no Blender; if you must convert a GLB, do it Mac-side. FBX imports untextured
  → assign the albedo separately (the M1.0 pale-albedo step).

## Keys (NEVER commit; NEVER print)
Stored mode-600 OUTSIDE the repo in `~/.worldos/`: `meshy.key`, `tripo3d.key`, `scenario.key` +
`scenario.secret`, `pixellab.key`. Env fallbacks (CI): `WORLDOS_{MESHY,TRIPO,SCENARIO,PIXELLAB}_API_KEY`
(+ `WORLDOS_SCENARIO_SECRET`), read via `servers/engine/_env.py:env_var`. Wrappers enforce these
paths and never echo the key. ⚠ **All keys were pasted in a chat transcript (2026-06-21) — rotate them
in each service dashboard when convenient, then update the `~/.worldos/*.key` file.**

**Get the keys (4 steps):**
1. Create an account at **meshy.ai** / **tripo3d.ai** / **scenario.com** / **pixellab.ai** (whichever you need).
2. Copy the **API key** from that service's dashboard (Scenario also gives a **secret**).
3. Store it OUTSIDE the repo: `~/.worldos/{meshy,tripo3d,scenario,pixellab}.key` (+ `scenario.secret`), then `chmod 600 ~/.worldos/*.key ~/.worldos/*.secret`.
4. Verify auth: `python3 extensions/renderers/godot/tools/<svc>_gen.py --test-key` (e.g. `meshy_gen.py --test-key`).

## CLI wrappers (`extensions/renderers/godot/tools/`, urllib-only, mirror `meshy_gen.py`)
- **`tripo_gen.py`** — `text|image|rig` subcommands; `--rig` (rig-check→rig→retarget), `--out-format glb|fbx`, `--animations`, `--lowpoly` (P1), `--test-key`, `--dry-run`. Rigs **humanoids AND creatures** (rig_type auto-detected). Polls `GET /v3/tasks/{id}` (plural) ≥3s; **downloads immediately (URLs expire ~5 min)**. Full recipe + verified API facts in **[TRIPO_PIPELINE.md](TRIPO_PIPELINE.md)**.
- **`scenario_gen.py`** — `generate|list-models|upscale`; `--model-id`; `--test-key`; `--dry-run`. HTTP Basic; async job → asset download. Scenario has TWO interfaces — REST `https://api.cloud.scenario.com/v1` (Basic, used by `scenario_gen.py`) AND MCP `https://mcp.scenario.com/mcp` (for direct agent tool calls). Both work.
- **`meshy_gen.py`** — text→3D (`--prompt --out`, preview→refine PBR) **plus rig+animate** (`--rig` / `--rig-from-task <id>` / `--animate <action_id...>`, HUMANOID only — Meshy 422s on creatures). The 5-cr rig **includes free walk/run**. `--test-key`, `--dry-run`. Full recipe in **[MESHY_PIPELINE.md](MESHY_PIPELINE.md)**.
- **`pixellab_gen.py`** — STUB for GT1; `--test-key` only.
Always run `--test-key` first to confirm auth.

## MCPs (registered at user scope in `~/.claude.json` — do NOT touch the plugin `.mcp.json`)
- **`scenario`** (`https://mcp.scenario.com/mcp`, HTTP Basic) — ✔ connected. Tools: `generate_image`, `generate_custom`, `train_model`, `list_models`, `get_job_status`, `upscale`, etc.
- **`pixellab`** (`https://api.pixellab.ai/mcp`, Bearer) — ✔ connected, 41 tools (pixel chars 4/8-dir, animation, Wang/topdown/sidescroller/isometric tilesets). GT1-future.
Add `@https://api.pixellab.ai/mcp/docs` to a prompt for the full PixelLab tool list.
(Meshy + Tripo have no wired MCP — use the CLI wrappers.)

**MCP not required.** The CLI wrappers (`scenario_gen.py`, `pixellab_gen.py`) are urllib-only and need
NO MCP — they're the portable path. A freshly-spawned subagent may not inherit the user-scope MCP in
its own tool list; the wrappers always work regardless.

## Non-Eva engine image provider
`ScenarioImageProvider` is registered in `servers/engine/imagegen.py` `_HOSTED`. Select with
`WORLDOS_IMAGE_PROVIDER=scenario` (+ `WORLDOS_SCENARIO_MODEL_ID`) to give the engine REAL 2D image-gen
**without** the `openclaw` provider (which rides Eva's gateway — the invariant blocker). It degrades to
null when unconfigured/offline; the default provider stays null.

## Verified API facts (LIVE-TESTED 2026-06-28 — these differ from public docs; trust these)
- **Meshy**: Bearer `msy_*`, `https://api.meshy.ai`. text/image→3D on `/openapi/v2`; **rigging + animation on `/openapi/v1`** (`/openapi/v1/{rigging,animations,balance}`). Rig 5 cr (+free walk/run), anim 3 cr. HUMANOID-only rig/animate. Full spec → [MESHY_PIPELINE.md](MESHY_PIPELINE.md).
- **Tripo**: Bearer, `https://openapi.tripo3d.ai/v3`. Gen `POST /generation/text-to-model {prompt, model}` (no `type`; the old `/generation/text` 404s). **Model ids are DATE-STAMPED** (`P1-20260311`, `v3.1-20260211`; friendly names rejected). Rig chain `/animations/{rig-check,rig,retarget}`: rig `model=v2.5-20260210` (biped+creatures), rig_type from rig-check, presets **namespaced `preset:<rig_type>:<name>`** (`preset:biped:walk`), **ONE per call**. Poll `GET /v3/tasks/{id}` (**plural**) ≥3s; output at `data.output.model_url`, expires ~5 min. Full spec → [TRIPO_PIPELINE.md](TRIPO_PIPELINE.md).
- **Scenario**: **HTTP Basic (key:secret)**, base **`https://api.cloud.scenario.com/v1`** (NOT `api.scenario.com`, NOT Bearer). `POST /v1/generate/txt2img` → `{"job":{"jobId"}}` → poll `GET /v1/jobs/{id}` → `GET /v1/assets/{id}` for the URL. The WorldOS Painterly LoRA `model_MB22WaRCBLtfhi5R2CRpHoEL` is the trained style model (see top); run it via the base `model_z-image` + `loras:[...]`.
- **PixelLab**: MCP-first, Bearer, `https://api.pixellab.ai/mcp` (JSON-RPC, returns **SSE** `text/event-stream`). REST `/balance` is 404. ~50 tools (pixel chars 4/8-dir, animation, tilesets). GT1-future.

## Rate limits / cost / gotchas
Meshy ~200/day; Tripo enforces ~1 req/s — the wrapper polls at ≥3s to stay under it (else 429); Scenario 50–100/s + 10–30 min model training; PixelLab async queue. No hard spend caps — use `--dry-run` for a credit estimate + check balance before bulk. Download asset URLs immediately (finite TTL). Pin seeds / prefer Scenario trained models for consistency. Validate bakes (frame dims + foot-anchor) before trusting a sheet.

## What this unblocks
- **Painterly backdrops** → Scenario (the WorldOS Painterly LoRA → `scenario_gen.py generate` or the `scenario` MCP), served via the engine's `/image` from `_private` (no Eva).
- **Real rigged + animated 3D actors (Unity-direct lane)** → Meshy (humanoid: `--rig --animate`) or Tripo (creatures, or humanoid fallback: `text --rig --out-format fbx`) → FBX → Unity import as `animationType=Generic`. Validated end-to-end 2026-06-28 (walk cycle rendered on GEX44).
- **Godot 2D sprite bake** (`bake_sprites.py`→`pack_sheet.py`) → reference/quarantined; does not consume rig/animation data.
- **GT1 (future)** → PixelLab tilesets + pixel sprites.
