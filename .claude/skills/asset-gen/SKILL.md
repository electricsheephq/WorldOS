---
name: asset-gen
description: Generate game art for WorldOS via Meshy / Tripo3D / Scenario / PixelLab — 3D characters, auto-rigging, animation, painterly backgrounds, sprites, tilesets. Use when creating or regenerating ANY visual asset for the Godot GT2 renderer (character sheets, NPC/companion models, backdrops, props) or the future GT1 tile engine, or when wiring a non-Eva image-gen provider. Routes the right service to each job and gives the keys, the CLI wrappers, the MCPs, and the sprite-sheet bake contract.
---

# WorldOS AI asset-generation toolkit

Four services cover every visual asset need. Keys are stored OUTSIDE the repo; two services
are wired as MCPs; all four have CLI wrappers (or a stub). The renderer reads only the
**sprite-sheet manifest** (see `godot/HANDOFF.md` §4.6), so any source drops in at the same
`scope_key` with zero renderer change.

## Which tool for which job

| Job | Tool | How |
|---|---|---|
| **3D character / NPC / companion** | **Meshy** or **Tripo H3.1** | `meshy_gen.py` (text→3D, preview→refine) or `tripo_gen.py text` |
| **Rigged + animated character** (GT2 #1091) | **Tripo** | `tripo_gen.py rig` → rig-check → rig(`spec=mixamo`) → retarget `[walk,idle,run,attack,cast]` → animated GLB |
| **Low-poly prop / dungeon dressing** | **Tripo P1** | `tripo_gen.py text --lowpoly` (~10s) |
| **Painterly background / diorama** (GT2 #1089) | **Scenario** | `scenario_gen.py generate --model-id <id>` (train a style model once → consistent backdrops) |
| **Engine 2D image-gen, non-Eva** | **Scenario provider** | `WORLDOS_IMAGE_PROVIDER=scenario` (see below) |
| **Pixel sprites / tilesets** | **PixelLab** | **GT1 FUTURE only** — MCP `pixellab`; `pixellab_gen.py` is a stub |

The 3D path always ends at the bake: **`tripo_gen.py`/`meshy_gen.py` → `godot/tools/bake_sprites.py`
(8 facings @ dimetric 2:1, see `godot/ISO-PROJECTION.md`) → `godot/tools/pack_sheet.py`** (manifest v1:
rows=8 facings, cols=24 idle4/walk8/attack6/cast6, 128px, foot-anchor).

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
4. Verify auth: `python3 godot/tools/<svc>_gen.py --test-key` (e.g. `meshy_gen.py --test-key`).

## CLI wrappers (`godot/tools/`, urllib-only, mirror `meshy_gen.py`)
- **`tripo_gen.py`** — `text|image|rig` subcommands; `--lowpoly` (P1); `--test-key`; `--dry-run` (credit est). Polls `GET /v3/task/{id}` ≥2s; **downloads GLB immediately (URLs expire ~5 min)**. Output → gitignored `content/worlds/_private/<world>/images/<scope>/`.
- **`scenario_gen.py`** — `generate|list-models|upscale`; `--model-id`; `--test-key`; `--dry-run`. HTTP Basic; async job → asset download. Scenario has TWO interfaces — REST `https://api.cloud.scenario.com/v1` (Basic, used by `scenario_gen.py`) AND MCP `https://mcp.scenario.com/mcp` (for direct agent tool calls). Both work.
- **`meshy_gen.py`** — text→3D, `--prompt --out`; `--test-key`; `--dry-run`. The wrapper uses `POST /openapi/v2/text-to-3d` (preview→refine PBR) at `https://api.meshy.ai`. (Meshy's API also exposes `/v1/rigging` + `/v1/animations`, but `meshy_gen.py` does **text-to-3d only** — use **Tripo** for rig→animate.)
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

## Verified API facts (live-probed — some differ from public docs)
- **Meshy**: Bearer, `https://api.meshy.ai/openapi`. text/image→3D + `/v1/rigging` + `/v1/animations` + `/v1/text-to-image`.
- **Tripo**: Bearer, `https://openapi.tripo3d.ai/v3`. `/generation/text|image-to-model`; rig chain `/animations/{rig-check,rig,retarget}` (`rig_type=biped`, `spec=mixamo`, presets `preset:walk|idle|run|attack|cast`). Poll `GET /v3/task/{id}` ≥2s; envelope `{"code":0,"data":{...}}`; model URLs expire ~5 min.
- **Scenario**: **HTTP Basic (key:secret)**, base **`https://api.cloud.scenario.com/v1`** (NOT `api.scenario.com`, NOT Bearer). `POST /v1/generate/txt2img` → `{"job":{"jobId"}}` → poll `GET /v1/jobs/{id}` → `GET /v1/assets/{id}` for the URL. `GET /v1/models`. **Account currently has 0 trained models → `generate` needs a `--model-id` (train/import one in the Scenario UI, or use a public modelId).**
- **PixelLab**: MCP-first, Bearer, `https://api.pixellab.ai/mcp` (JSON-RPC, returns **SSE** `text/event-stream`). REST `/balance` is 404.

## Rate limits / cost / gotchas
Meshy ~200/day; Tripo enforces ~1 req/s — the wrapper polls at ≥3s to stay under it (else 429); Scenario 50–100/s + 10–30 min model training; PixelLab async queue. No hard spend caps — use `--dry-run` for a credit estimate + check balance before bulk. Download asset URLs immediately (finite TTL). Pin seeds / prefer Scenario trained models for consistency. Validate bakes (frame dims + foot-anchor) before trusting a sheet.

## What this unblocks
- **#1089 painterly backdrops** → Scenario (train a style model → `scenario_gen.py generate` or the `scenario` MCP), served via the engine's `/image` from `_private` (no Eva).
- **#1091 real rigged animation** → Tripo `rig`→`retarget` → animated GLB → `bake_sprites.py`.
- **GT1 (future)** → PixelLab tilesets + pixel sprites; not wired into GT2.
