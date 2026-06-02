# WorldOS render/ — Phaser thin-client renderers (M0 zone · M1 GT1 tilemap · M2 GT2 backdrop)

The graphical renderers for WorldOS, served from this subtree (`/openworlds/render/`). Each is
a *thin client* — it owns no game state; it reads the engine surfaces and writes only
constrained intents to `/move`.

- **`index.html` + `renderer.js`** — M0 zone thin-client (the architecture proof).
- **`tilemap.html` + `renderer-tilemap.js`** — **M1 GT1 SNES-pixel tilemap** (top-down
  16-bit exploration + zone-mode turn combat). Open `/openworlds/render/tilemap.html`.
- **`backdrop.html` + `renderer-backdrop.js`** — **M2 GT2 Pillars/BG backdrop-isometric**
  (painted full-screen backdrop + renderer-owned walkmask + depth-sorted token actors +
  click-to-move + backdrop-mode combat replay). Open `/openworlds/render/backdrop.html`.

> **Roadmap:** `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md` · **Contract:**
> `docs/roadmap/contracts/render-profile.md` + `render-profile.schema.json` +
> `move-intents.md`. Implements M0 (#425–#433) + M1 GT1 (#434–#440) + M2 GT2 (#443–#448).

## GT2 (M2) — what's built vs deferred
- **Built (#443–#446):** `scene_kind:"backdrop"` render-profile (#443); a **renderer-owned
  walkmask** floor polygon whose clicks resolve to engine **zones** via `{kind:"move_to_zone"}`
  — the engine owns the destination, the renderer owns the path, no engine change (#444);
  full-screen backdrop swap per location + **depth-sorted token actors** (feet-anchored, nearer
  occludes farther) + **click-to-move**/click-to-travel (#445); **backdrop-mode combat** as a
  pure replay of `/combat-surface` + `/events` — BG/PoE paused-turn presentation, engine decides
  every outcome, derived positions never authoritative (#446). Flat-lit MVP (normal-map lighting
  is M5/Branch B).
- **#447 painted-backdrop asset pipeline — WIRED to reuse the existing imagegen + BG catalog**
  (owner decision 2026-06-02, first-party/internal): location backdrops + actor tokens resolve
  their render-profile `art.scope_key` through the existing `GET /image?scope=…` bridge and draw
  the real art when present; a miss (404) falls back to the procedural painted-ish backdrop /
  token. Lazy + cached per scope; redraws when art loads. The painted-backdrop **gen +
  walkmask-authoring + vision-critic coherence gate** (and the UGC rights-clean asset model)
  defer to M3.
- **#448 QA gates:** the render gate (`qa/render_gate_probe.js`) now covers `backdrop.html`
  (boots clean, canvas mounts, no React/VTT chrome leak); `test_render_backdrop.py` gates
  serve/MIME/contract in CI. The backdrop/occlusion screenshot-critic + blind-playtester
  traversal build on the M3 gated AI build-loop harness (same as M1 #441).

## GT1 (M1) — what's built vs deferred
- **Built (#434–#438):** `scene_kind:"tilemap"` render-profile; a procedural top-down tilemap
  per location; **click-to-travel** on green exit tiles (`{kind:"travel"}`); party/status HUD
  from `/character-surface` (zero client rules); **zone-mode turn combat** as a pure replay of
  `/combat-surface` + `/events` (zone bands + tokens, NO VTT cells/rulers, derived positions).
- **#439 asset pipeline — WIRED (owner decision 2026-06-02: reuse existing imagegen + BG
  catalog, first-party/internal):** actor tokens resolve their render-profile `art.scope_key`
  through the existing `GET /image?scope=…` bridge and draw the real sprite when present; a
  miss (404) falls back to the procedural placeholder token. Lazy + cached per scope; redraws
  when art loads. (The UGC-clean asset-model decision — self-hosted vs paid API for
  user-shippable games — defers to M3.) Tiles remain procedural for now.
- **Deferred — #440/#441 QA gates + blind-playtester:** wire when `qa/playwright/node_modules`
  is installed (the render-gate + persona harness need it). The static serve/contract gating
  (`test_render_tilemap.py`) is in CI now.
- **Deferred — #442 UGC scene authoring v0:** an M3-foundation piece (engine-owned scene
  persistence); lands with the M3 UGC work.

## Run it

```
# from the repo root
python3 viewer/server.py "" 8765
# then open:
#   http://127.0.0.1:8765/openworlds/render/             (standalone, bundled fixtures)
#   http://127.0.0.1:8765/openworlds/render/?campaign=<id>   (drive a live game)
```

No build step, no CDN: Phaser is vendored at `../vendor/phaser-3.80.1.min.js`, and
`viewer/server.py`'s OpenWorlds asset handler serves this dir with **zero server change**.

## What it proves (the architecture, made visible)

1. **Renderer owns no state.** `surface-client.js` only GETs the read-model surfaces and
   POSTs an intent. It re-fetches every poll; the engine is the sole writer.
2. **Positions are presentation derived from engine zones.** `renderer.js` draws zone
   **bands** and groups tokens by `zone`, re-deriving x,y itself — it ignores any surface
   `position.x/y` (an ephemeral render-hint). Mirrors `viewer/server.py:_combat_row_positions`.
3. **Zone-mode renders bands, NOT a VTT grid** — no cells, no rulers, no measurement. The
   honest rendering the contract requires (the engine's combat is gridless/named-zone).
4. **Consumes the render-profile contract.** `render-profile.example.json` is a clean,
   schema-valid instance of `docs/roadmap/contracts/render-profile.schema.json`.

## Files

- `index.html` — entry (vendored Phaser; served at `/openworlds/render/`)
- `surface-client.js` — the swappable thin-client transport (poll now; websocket later, #455)
- `renderer.js` — the Phaser zone-mode scene
- `render-profile.example.json` — a contract-valid render-profile instance
- `fixtures/*.json` — surface payloads mirroring the real builder shapes (standalone mode)

## Not yet (later milestones, intentionally)

- React-screen integration (a `screen-render.jsx` + `index.html` script tag) — deferred to a
  coordinated milestone merge, since the shared `index.html`/`app.jsx` are the implementation
  agent's hot files. The self-contained sub-app at `/openworlds/render/` needs none of that.
- The two `viewer/server.py` edits (#429 move-vocab, #432 position-hint) — Lane B, sequenced
  last per the plan; until #429 lands, a live engine rejects `{kind:"travel"}` (the spike's
  documented contract-gap demo).
- Tilemap (GT1) + backdrop-isometric (GT2) render profiles, websocket transport, the AI
  build-loop — M1/M2/M3.
