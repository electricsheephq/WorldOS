# M0 spike — Phaser thin-client (renderer-as-client proof)

> **The pixels are not the point.** This spike exists to validate the renderer-integration
> **contract** and surface the gaps the engine team must close in M0. The Phaser scene is
> throwaway. Decision record: `/tmp/decision-worldos-render-contract.md`; roadmap:
> `roadmap-v2-FINAL.md` (LEXAR session-notes 2026-05-31).

## Run it
```
cd spikes/m0-phaser-thin-client
python3 -m http.server 8088
# open http://127.0.0.1:8088/  (renders standalone on bundled fixtures)
# live:  http://127.0.0.1:8088/?campaign=<id>&base=http://127.0.0.1:8765
```

## What it PROVES (the architecture decision, made visible)
1. **Renderer owns no state.** `surface-client.js` only GETs the read-models and POSTs an intent. Re-fetch every poll; the engine is sole writer. The websocket/SSE upgrade (M3) slots behind the same `SurfaceClient` interface — callers unchanged.
2. **Positions are presentation derived from engine zones (D1).** The scene draws **zone bands** and groups tokens by `zone`, re-deriving x,y itself — it deliberately **ignores** the surface's `position.x/y` to prove that field is an ephemeral hint, not state. This mirrors what `viewer/server.py:_combat_row_positions` already does server-side.
3. **Zone-mode renders bands, NOT a VTT grid.** No cells, no rulers, no measurement — the honest rendering the contract requires (so the engine's gridless zone combat is never misrepresented as coordinate-tactical).
4. **One contract, layered.** `render-profile.example.json` is the concrete core + per-renderer-block schema. The `rpgmaker` block is reserved/spec-only (BYOL exploration export, deferred post-M2).
5. **Turn/initiative HUD + party panel** come straight from the already-shipped `/combat-surface` (#412) and `/character-surface` — zero client rules.

## CONTRACT GAPS this spike SURFACES (the M0 freeze — engine-team priority)

These are the three contracts to freeze in M0, *before* T1/T2 build. The spike hits each concretely:

### GAP 1 — graphical move-intent vocabulary (BLOCKING, engine-side)
The spike POSTs `{kind:"travel", target:"loc-..."}` on a travel click. The current engine **rejects** it: `_MOVE_KINDS` (viewer/server.py:84) is a closed allowlist `{say,do,check,save,combat,attack,cast,use_item,clarify}` with no `travel`. `sanitize_move` returns `"unknown move kind 'travel'"`.
- **Why blocking:** if T1 ships click-to-travel as `do "go to the harbor"` (free text) and M2 later adds `{kind:travel}`, every M1 game + AI-loop glue emits the old shape → breaking change.
- **Freeze now:** add structured graphical intents — `travel`(target=location_id), `inspect`/`examine`(target), `move_to_zone`(zone) — to `_MOVE_KINDS` + `_MOVE_FIELDS` + `sanitize_move`, designed once. Additive at the facade, but semantic for every consumer.

### GAP 2 — surface-read guarantees (engine-side)
The spike tweens/keys tokens by actor `id` and treats `zone` as authoritative, `position.x/y` as a hint. Three guarantees must be documented + tested:
- **Stable actor id across snapshots** (BLOCKING test): tweening pops/respawns tokens if ids churn on re-seed/regeneration. Assert ids persist for the same logical entity.
- **Authoritative-vs-derived fields:** mark `_combat_row_positions` x,y as ephemeral (suggest renaming to `_render_hint_position`) so no renderer/AI-loop persists it as state (it would become a second source of truth the engine silently overwrites).
- **`/events` ordering/replay semantics:** the renderer replays engine-decided combat as animation; needs ordered, replayable events.

### GAP 3 — render-profile contract shape (cross-cutting)
`render-profile.example.json` here is the proposed freeze: layered **core** (scene_kind, named zones, engine FK ids, scope-key art, ai_disclosure — all defaultable for the AI generator) + optional per-renderer blocks. Needs: a JSON Schema, a **core-only conformance test** (a renderer using core + its block renders every M0 scene — extends the viewer-tests CI lane #403), and `schema_version` + capability negotiation so authored UGC profiles survive evolution.

### Also confirmed (NOT gaps — additive/renderer-owned)
- **Walkmask (T2)** is renderer-owned presentation (engine knows location adjacency, not walkable pixels) — no engine change; pathfinding *destinations* still resolve to engine zones/locations.
- **Turn/initiative surface** already exists (#412) — not a new surface.
- **`grid` mode** is explicitly OUT of v1 (a future engine-state epic), so no coordinate/movement/LoS engine work is in M0–M3.

## Files
- `render-profile.example.json` — the contract, concretized (core + per-renderer blocks)
- `surface-client.js` — the swappable thin-client transport (poll now; ws later)
- `spike.js` — the Phaser zone-mode scene
- `index.html` — entry (Phaser 3 from CDN)
- `fixtures/*.json` — surface payloads mirroring the real builder shapes (run standalone)
