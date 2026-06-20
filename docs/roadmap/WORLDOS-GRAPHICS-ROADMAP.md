# WorldOS Graphics & Game-Types Roadmap (canonical)

> **Single source of truth.** This document is the canonical roadmap for WorldOS's graphics
> layer and the kinds of games it can produce. It lives in the repo (`docs/roadmap/`),
> ties to the runbook, and is executed via GitHub Milestones + Issues. Any other view
> (Notion, slides) is a **read-only downstream mirror** of this file + GitHub — never a
> second writer. This mirrors WorldOS's core architecture: the engine is the sole
> authority; everything downstream is a projection.
>
> Status: **FILED + PARTIAL M0 CONTRACTS LANDED** — PR #424 filed milestones M0–M6 +
> Future-gated issues #425–#461. PR #464 landed the parallel-safe M0 contract slice:
> #425/#426/#427/#431 closed; #428/#430/#433 remain open for fuller renderer/implementation proof;
> #429/#432 were intentionally held for coordinated `viewer/server.py` edits. Confidence
> on direction ≥95% (first-principles decision 2026-05-31). The full decision records
> (research report, architecture decision, T2-engine addendum) are operator-local
> session-notes and are intentionally not committed; this document is the in-repo canonical
> distillation of them.

---

## 0. The two organizing axes

WorldOS games are described by two axes. The roadmap is the plan for filling in the grid.

- **Axis 1 — GAME TYPES** (`GT*`): the kinds of games WorldOS can produce. A game type is a
  *presentation family* over the one shared engine — NOT a separate engine.
- **Axis 2 — CAPABILITIES** (`C*`): the settings / abilities / configurations a game can use
  (rendering, movement, combat presentation, assets, input, UGC, AI-autonomy…). Each
  capability has **maturity levels**; **Branch A** = the level we ship first, **Branch B** =
  the level we grow into later.

A milestone is then defined as *"these capabilities reach these levels for these game types."*
Capabilities evolve gradually; that gradual evolution **is** the roadmap.

### The one invariant under everything (non-negotiable)
**The Python engine is the sole writer of game state.** Every game type and every capability
is a *renderer/presentation* concern consuming the read-model surfaces (`/atlas-surface`,
`/character-surface`, `/combat-surface`, `/chat`, `/events`) + the single `POST /move` intent
lane. No renderer, no AI loop, no UGC tool ever becomes a second source of truth.

---

## 1. Game-type taxonomy (Axis 1)

| ID | Game type | Status | Engine for it | Notes |
|----|-----------|--------|---------------|-------|
| **GT0** | **Narrative dashboard** (the current OpenWorlds web UI) | **SHIPS TODAY** | React/JSX in WKWebView | The living-world DM + companion RPG; text/portrait-first. The proof that engine-as-authority + thin-client works. |
| **GT1** | **SNES-style pixel turn-based** (JRPG / RPG-Maker-like feel) | Planned (MVP tier 1) | Phaser 3 (MIT), in the existing shell | Tilemap + sprite actors + 16-bit UI; zone-mode turn combat. |
| **GT2** | **Pillars / BG1-2 isometric party cRPG** | In progress (M5, pulled fwd) | **Godot 4 (web + native)** — Phaser GT2 **retired** 2026-06-21 | Painted backdrop + token actors. **Owner direction 2026-06-21:** Godot is the GT2 renderer for both web and native; the Phaser M2 backdrop renderer is retired (didn't deliver the experience). **Branch A = the LOOK; Branch B = measured tactics (evidence-gated).** See epic #1050. |
| GT3+ | (future families — e.g. tactical-grid SRPG, top-down action) | Not scoped | TBD | Added only when a capability set + audience justify it. |

**Rejected engines (recorded so they're not re-litigated):**
- **RPG Maker MZ** — EULA refutes multi-tenant UGC (Authorized-User-only; RTP welded to its
  editor; corporate use excluded). At most a deferred, asset-clean, exploration-only BYOL
  export for GT1. NOT a T2/isometric option (orthogonal-tile-locked).
- **Unity** — no macOS Unity-as-a-Library (can't embed in the WKWebView shell → separate
  binary); cRPG toolkits assume Unity owns the rules (conflicts with engine-authority);
  weakest AI-buildability (35/100); licensing drag for UGC vs MIT.
- **GemRB** — GPL-2.0 (viral), C++ native, IE-data-bound. Reference for the look only.
- **Custom WebGL/WebGPU engine** — reject for the foreseeable roadmap.

---

## 2. Capability model (Axis 2) — Branch A → Branch B maturity

Each capability is a dial. Branch A is the first shippable level; Branch B is the growth level.
"Configs/settings/abilities" the user referenced = these dials, exposed per game.

| ID | Capability | Branch A (ship first) | Branch B (grow into) | Engine impact |
|----|-----------|----------------------|----------------------|---------------|
| **C1** | **Positioning model** | `theater` / `zone` (named regions; engine already has this) | `grid` (authoritative coords + measured range/LoS/AoE, **turn-based**, NO real-time) — **evidence-gated** | A = none; B = additive engine STATE (own first-principles pass) |
| **C2** | **Scene presentation** | tilemap (GT1) · painted backdrop (GT2) | dynamic normal-map lighting (the PoE glow) | renderer-owned |
| **C3** | **Combat presentation** | zone-band tokens, pure replay of engine `/events` | measured-grid tactics view (pairs with C1-B) | A = none; B = with C1-B |
| **C4** | **Movement / travel** | menu travel · click-to-zone · walkmask click-to-move (renderer-owned mask) | measured per-turn movement budget (with C1-B) | A = none; B = with C1-B |
| **C5** | **Asset pipeline** | portraits (exists) · AI pixel sprites · tilesets · painted backdrops · audio | curated per-race/scene sets; user uploads | renderer/content; AI-disclosure metadata mandatory |
| **C6** | **Input intents** (`/move` palette) | `say/do/check/save/combat/attack/cast/use_item/clarify` (today) **+ `travel`, `inspect`, `move_to_zone`** | drag-to-zone gestures; ability-bar verbs (Shove/Dash/Hide) | **contract change — freeze in M0**, roll across viewer+DM+facade together |
| **C7** | **Lighting** | flat-lit backdrops | normal-map dynamic lights (Godot-native; Phaser custom) | renderer-owned |
| **C8** | **Transport** | 3s poll (today) | WebSocket/SSE push (same payload shapes) | additive engine surface (anytime after M0) |
| **C9** | **AI build-loop autonomy** | manual scaffold + human review | gated unattended scaffold (~70-80%) w/ screenshot-critic + blind-playtester gates | tooling; loop never mutates the contract |
| **C10** | **UGC** | none → author scenes | author whole games → ship/sell (MIT-clean stack) | per-user games persist as engine-owned data |

**Branch A vs Branch B in one line:** Branch A = *every capability at its first shippable level
for GT0/GT1/GT2*; Branch B = *the growth levels (measured tactics, dynamic lighting, full
autonomy, ship/sell UGC)* layered on as the long-term plan proves out.

---

## 3. The capability × game-type matrix (target at end of MVP / Branch A)

| Capability | GT0 dashboard | GT1 pixel | GT2 isometric |
|-----------|---------------|-----------|---------------|
| C1 positioning | zone | zone | zone (Branch A) → grid (Branch B, gated) |
| C2 scene | (text/portrait) | tilemap | painted backdrop |
| C3 combat | log/cards | zone-band | zone-band → measured (B) |
| C4 movement | menu | click-to-zone | walkmask click-to-move |
| C5 assets | portraits | sprites+tilesets | backdrops+tokens |
| C6 input | full palette+travel | +target/move_to_zone | +target/move_to_zone |
| C7 lighting | n/a | flat | flat → normal-map (B, Godot) |
| C8 transport | poll → ws | poll → ws | poll → ws |
| C9 autonomy | manual | gated loop | gated loop |
| C10 UGC | none | author→ship (B) | author→ship (B) |

---

## 4. Architecture decisions (locked; confidence ≥95% on direction)

1. **Renderer = thin client; engine = sole writer.** (The invariant.)
2. **Phaser 3 (MIT) + PixiJS v8 (MIT) for the MVP, BOTH tiers**, in the existing React/WKWebView
   shell. One renderer, two render profiles (tilemap / backdrop). Godot 4 reserved for an
   optional *premium native desktop/Steam* GT2 client later (separate binary; MIT). Unity rejected.
   **AMENDMENT 2026-06-21 (owner):** for **GT2 specifically** this is superseded — **Godot 4 is now
   the GT2 renderer for both web and native**, and the **Phaser GT2 backdrop path is retired** (it
   didn't deliver the experience). M5 is pulled forward (epic #1050). GT0 (React dashboard) and GT1
   (Phaser pixel) are unchanged. The thin-client invariant + layered render-profile contract are unchanged.
3. **Contract is LAYERED: core + per-renderer profiles.** Core (renderer-agnostic, all fields
   defaultable for the AI generator): `schema_version`, `scene_kind`, `positioning`, named
   `zones` (NOT x,y), engine FK ids, scope-key art, AI-disclosure. Optional blocks: `phaser{}`,
   `godot{}` (GT2 painterly-iso; added 2026-06-21), `rpgmaker{}` (reserved). Core-only conformance test gates it.
4. **Positions are presentation derived from engine zones** — already the shipped pattern
   (`viewer/server.py:_combat_row_positions`). Token x,y is an ephemeral render-hint, never state.
5. **M0 freezes THREE contracts together:** render-profile · graphical move-intent vocabulary
   (`_MOVE_KINDS` extension) · surface-read guarantees (stable-actor-id test, authoritative-vs-
   derived fields, `/events` ordering). The move-vocabulary is the most likely breaking-change
   source if not frozen first.
6. **RTwP (real-time + party-AI) is REJECTED permanently.** BG3 dropped it; it's incompatible
   with an LLM-DM (an LLM narrates turn-by-turn, can't drive a real-time tick); it would rebuild
   the engine (B1) or break sole-writer (B2). The genre identity lives in the LOOK + turn-based
   positioning, not RTwP.
7. **C1-B (measured-grid turn-based tactics) is the ONLY future engine spatial work worth doing,
   and it's EVIDENCE-GATED** (owner 2026-05-31: "decide after a playtest"). Triggered only if a
   real GT2 playtest shows zone-theater feels too abstract. Own first-principles pass.

---

## 5. Milestones → Epics → Issues

> Each milestone advances specific capabilities to specific levels for specific game types.
> The full, acceptance-criteria issue bodies live in the filed GitHub issues (#425–#461);
> the titles below are the index into them.

### M0 — Contract freeze + thin-client spike  *(advances C6 freeze, C1=zone, C8 groundwork; GT-agnostic)*
- **R0.1 Render-profile contract** (core + per-renderer blocks; zones-not-xy; core-only conformance test in CI)
- **R0.2 Graphical move-intent vocabulary** (extend `_MOVE_KINDS`: travel/inspect/move_to_zone; doc; reject-unknown test; cross-component freeze)
- **R0.3 Surface-read guarantees + spike** (stable-actor-id test [BLOCKING]; namespace the derived position hint; Phaser thin-client spike rendering one location). The `/events` **ordering/replay** half of R0.3 is frozen as the **Action-Replay envelope** contract (`docs/roadmap/contracts/action-replay-envelope.md`; epic **#645** R645.1) — `{seq, actor_fk, verb, target_fk, result, anim_hint}`, the time-axis companion to the render-profile (frame) + move-intents (write) contracts.

### M1 — GT1: SNES pixel turn-based MVP  *(C2=tilemap, C3=zone-band, C4=click-to-zone, C5=sprites, C9=manual)*
- **R1.1 T1 render profile + tilemaps** · **R1.2 T1 combat + character UI (zone-mode)** · **R1.3 T1 QA gates** (reuse screenshot-recorder + UI-gate + blind-playtester; add "no VTT grid chrome in zone mode" check)

### M2 — GT2: Pillars/BG1-2 backdrop-isometric MVP (Branch A = the LOOK)  *(C2=backdrop, C3=zone-band, C4=walkmask, C7=flat)*
- **R2.1 Backdrop render profile + occlusion** (walkmask renderer-owned; destinations resolve to engine zones/locations) · **R2.2 T2 combat + polish** (pure replay; normal-map lighting deferred) · **R2.3 T2 QA gates**
- **⚠ RETIRED 2026-06-21 (the Phaser implementation):** the GT2 *renderer* moved to Godot (M5, pulled forward — epic #1050); the Phaser backdrop renderer (`viewer/openworlds/render/backdrop.html`) is reference-only. The render-profile contract work (R2.1) stays valid — Godot consumes the same `core` + a `godot{}` block.

### M3 — Gated AI build-loop + UGC  *(C9=gated autonomy, C10=author→ship, C5 disclosure)*
- **R3.1 Gated AI build-loop** (generate render-profile core from lore; resolve art; emit Phaser glue; gate each iter; human-gate queue for taste/story/contract) · **R3.2 UGC platform + licensing/compliance** (per-user ownership; AI-disclosure end-to-end [EU 2026-08-02, Steam survey]; MIT redistribution story) · **R3.3 Transport upgrade** (C8: websocket/SSE; swap behind SurfaceClient interface)

### M5 — GT2 Godot painterly-isometric renderer (web + native) — **PULLED FORWARD, IN PROGRESS** (epic #1050)
- The GT2 renderer (Branch A = the LOOK now; Branch B = C7 normal-map lighting later). Godot 4 thin client over the surfaces (HTTPRequest/WebSocketPeer; Light2D; NavigationRegion2D); consumes the `core` contract + a `godot{}` renderer block; GDScript headless-codegen harness (`.tscn` gen + `godot-mcp`). **Owner direction 2026-06-21:** a SINGLE Godot client serves both web (HTML5 export, single-threaded) and native (standalone `.app`); Phaser GT2 retired. Sprint 1 = the vertical slice (#1051–#1056); ISO projection locked in `godot/ISO-PROJECTION.md`.

### M6 — *(optional, post-M1)* GT1 BYOL RPG Maker exploration export
- Asset-clean (no RTP), exploration/dialogue only (battle-system mismatch is fatal), contract-taker; RTP legal guardrails + counsel review.

### Future — *(EVIDENCE-GATED)* C1-B measured-grid turn-based tactics
- Trigger: GT2 playtest shows zone-theater too abstract. Own first-principles pass. Additive engine state (coords + measured range/LoS/AoE), turn-based, NO real-time/party-AI. RTwP stays rejected.

---

## 6. Ties to the runbook + single-source-of-truth mechanics
- **Canonical:** this file (`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`) + GitHub Milestones/Issues + the WorldOS runbook (engine invariants, dev loop, QA gates).
- **Execution:** each epic → a GitHub milestone; each issue → a GitHub issue with the `graphics` + `epic:*` + game-type (`gt0/gt1/gt2`) + capability (`cap:c1…c10`) labels, so the matrix is queryable.
- **Mirror (optional):** a read-only Notion roadmap view generated FROM this file + GitHub — never edited directly. Canonical stays GitHub.
- **Maintenance:** this file is updated as capabilities advance levels; local decision logs can record *why* each level moved.

## 7. Open decisions deferred to owner (genuinely need you)
1. Self-hosted open image model vs commercially-indemnified API for UGC assets (affects M3).
2. GT2 MVP: flat-lit backdrops first (recommended) — confirmed; normal-map lighting = Branch B / Godot.
3. Does the AI build-loop become a user-facing surface ("watch it build") or an internal authoring tool? (shapes M3 UX.)
