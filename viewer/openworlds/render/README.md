# WorldOS render/ — Phaser thin-client (M0)

The first graphical renderer for WorldOS, promoted from `spikes/m0-phaser-thin-client/`
into the **served** viewer subtree. It is the M0 proof that the *renderer-as-thin-client*
architecture works end-to-end against the real engine surfaces.

> **Roadmap:** `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md` · **Contract:**
> `docs/roadmap/contracts/render-profile.md` + `render-profile.schema.json` +
> `move-intents.md`. This dir implements the M0 issues #425–#433 groundwork.

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
