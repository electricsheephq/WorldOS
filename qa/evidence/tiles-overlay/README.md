# Player Feel — Phases 3+4 evidence (walkability overlay + advisory visibility)

Renderer-only change to `extensions/renderers/unity/scripts/CombatSurfaceClient.cs`.
Validated on the GEX44 box (Unity 6000.5.1f1, worldos-unity), claim under #1386.

## Compile
Deployed the changed script to the box `Assets/CombatSurfaceClient.cs`, `refresh_unity`
domain reload → **0 console errors**, 0 warnings referencing the file. Type reloads and is
instantiable (harness `AddComponent` succeeds).

## Phase 3 — walkability overlay (browser-parity, screen-combat.jsx:721-802)
Built the overlay at runtime via reflection over a real painterly scene (14×11 grid,
CellSize 2): impassable wall column x=3, one occupied cell, one foe cell.

- `overlay_on_hover.png` — faint **gold** inset on walkable cells (thin border, mostly
  transparent), **dark red-brown** tint on the impassable column + occupied/foe cells,
  **red** hover on the foe cell (attack affordance), **brighter gold** hover on a walkable
  cell. `BuildOverlay` produced 154 quads (one pool), colors mutated in place.
- `overlay_off.png` — `ToggleOverlay` → OFF: **zero tiles**, floor byte-identical to the
  no-overlay scene (constraint: OFF = zero visual change).

> **W6.4 (#1463) update — overlay default:** the walkability overlay now defaults **ON** for the
> first turn under **onboarding** (`WORLDOS_PLAYTEST=1` **or** `WORLDOS_ONBOARD=1`), not only under a
> playtest — so a first-timer sees the walkable tiles immediately (the T3 readability gap). Absent
> **both** env vars (beauty captures) the overlay stays **OFF** and the scene is byte-identical, so
> the "OFF = zero visual change" constraint above is unchanged for captures. `G` still toggles. W6.4
> also adds an onboarding hint layer (whose-turn-by-name + affordance, fades after the first action)
> and world-space name plates on the HP-bar root (isCurrent = gold), both `_onboard`-gated. Evidence:
> `qa/evidence/1463/`.

Shader note: cells use `Sprites/Default` (has `_Color`); `Unlit/Transparent` has **no**
`_Color` (property index -1) so a per-cell tint is silently ignored — the first capture
rendered all-white until switched. Transparent queue 2500 so the floor+actors draw first
(tiles blend over floor, are depth-occluded by actors). (The black polygons in the frames
are pre-existing box-scene geometry, not the overlay — the harness ran in the loaded scene.)

## Phase 4 — advisory visibility
Renderer parse path validated deterministically (fed synthetic `/move` JSON through
`HandleAdvisory`), FindDict recursing through the arbiter wrap:
- over-budget `movement_illegal` → `over movement budget — moved anyway`
- Speed-0 `movement_illegal` (conditions) → `can't move (Speed 0) — moved anyway`
- `move_blocked` → its `reason` verbatim
- no advisory → silent (empty)

**DORMANT end-to-end (engine gap, NOT a renderer bug):** the engine computes
`movement_illegal`/`move_blocked` in `move_to_coords` but `_apply_intent`
(servers/engine/combat_loop.py:624-628) discards `move_to_coords`'s return, setting only
`entry["result"]={to_cell,to_zone}`. So the `/move` response never carries these fields
today. The renderer is future-ready: it lights up the instant a 1-line engine change
propagates the view's `movement_illegal`/`move_blocked` into `entry["result"]`. Kept the
renderer a pure consumer per the packet constraint; flagged the enabler rather than
expanding scope into the engine.

## Build
`Tools/WorldOS/Build/macOS Player (Universal)` → result=Succeeded, totalErrors=0,
totalWarnings=4, size≈255 MB. Zip scp'd to
`~/worldos-session-notes/w5a-build/WorldOSPlayer.app.NEW.zip` (existing zip was 29 min old
→ owner may be mid-playtest, so wrote NEW rather than overwriting).
