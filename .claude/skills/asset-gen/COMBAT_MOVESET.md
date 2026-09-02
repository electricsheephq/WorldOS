# WorldOS combat moveset — the canonical "agent adds animations to a model" process

The repeatable recipe for giving a humanoid actor its full combat moveset, then wiring + scoring it.
The engine stays the SOLE WRITER — clips + Animator are renderer-local view only; the engine drives
state *selection* over the bridge, it never owns the Animator as game state.

## The 9-clip canonical moveset
`idle · walk · run · attack · cast · block · dodge · hit · death`
(grounded in the engine's combat verbs + `anim_hint`s + the bake-manifest columns.)

## Feeder A — Meshy (DEFAULT: zero-touch, headless, proven)
**One command** gives a humanoid the entire named moveset:
```bash
meshy_gen.py --prompt "an elf ranger ..." --out <dir> --moveset
# or onto an existing Meshy model:  meshy_gen.py --rig-from-task <id> --out <dir> --moveset
```
- Rig (~5 cr) **includes free walk + run**; `--moveset` adds the other 7 by action_id (~21 cr total).
- Clips land as **named** `anim_<name>.fbx` (anim_attack.fbx, anim_cast.fbx, …) — agent-readable.
- The map (all live-verified 2026-06-28 — each id generated a real clip): `idle=0, attack=4, cast=125,
  block=138, dodge=156, hit=178, death=8` (`WORLDOS_MOVESET` in `meshy_gen.py`).
- **Humanoid-only.** Creatures → Tripo (`tripo_gen.py text --rig`, `spec:"mixamo"`).

## Feeder B — Mixamo (UPGRADE: larger/higher-quality human mocap)
The `unity-mcp-mixamo` MCP does **not** fit WorldOS (Windows-only `.exe`, GUI-bound, can't run in the
local lane). Instead use **`mixamo_gen.py`** — a headless urllib wrapper that drives
Mixamo's internal REST API with the owner's OAuth token (no browser, no Unity, no `.exe`):
```bash
# one-time: log in at mixamo.com -> DevTools console -> copy(localStorage.access_token)
#           -> save to ~/.worldos/mixamo.token (chmod 600)
mixamo_gen.py --test-key                      # confirm token + that the (unofficial) API is still live
mixamo_gen.py moveset --out <dir>             # the same 9 named anim_<name>.fbx
mixamo_gen.py search "sword slash"            # explore the library
```
- ⚠ Mixamo's API is **unofficial** and Adobe has signaled a sunset — always `--test-key` first.
  (Live-confirmed working 2026-06-28: token + search + export + download all functional.)
- The token **expires (~hours)** — when `--test-key`/any call returns 401, refresh it (next section).

### Token refresh — the agent procedure (when `--test-key` says unauthorized)
Mixamo is browser-login only, so the token is extracted from a **logged-in mixamo.com browser tab on
the Mac** via Claude-in-Chrome. A Mac-side agent can do this fully autonomously (no human needed) as
long as a Mixamo session is logged in:
1. `list_connected_browsers` → `select_browser` (the local macOS Chrome).
2. `tabs_context_mcp{createIfEmpty:true}` → `navigate` an MCP tab to `https://www.mixamo.com/`
   (localStorage is shared per-origin with the logged-in session).
3. `javascript_tool`: trigger a Blob **download** of `localStorage.getItem('access_token')` (keeps the
   raw token OUT of the transcript) — `new Blob([t]) → a.download='mixamo_token.txt' → a.click()`.
4. `mv ~/Downloads/mixamo_token*.txt ~/.worldos/mixamo.token && chmod 600` it; re-run `--test-key`.
5. Keep `~/.worldos/mixamo.token` on the local Mac; no remote copy is needed.
This is safe to automate: it's the user's own short-lived token for their own tool. If NO logged-in
Mixamo tab exists, that's the one human gate — ask the owner to log in, then refresh.
- Mixamo clips ride Mixamo's skeleton, which matches our `spec:"mixamo"` rigs, so they retarget onto
  Meshy/Tripo actors. Default export is clip-only (no skin) for retargeting; `--skin` for a base.
- **When to use which:** Meshy = the per-actor baseline for the filler-first loop (keep it primary);
  Mixamo = a richer *shared* pack the owner harvests once and we retarget onto many actors.

## The shared tail (identical for both feeders)
1. **Import in the local Unity project** (`/Users/m1/worldos-unity`) as **`animationType = Generic`, NOT Humanoid.** Tripo/Meshy/Mixamo bone
   names don't auto-map to Unity's Humanoid avatar → a Humanoid import **silently drops the clips**.
   Generic preserves them. Strip the redundant mesh, keep the `AnimationClip`. (Load-bearing — see
   `MESHY_PIPELINE.md` / `TRIPO_PIPELINE.md`.)
2. **Assemble the Animator** over the CoplayDev unity-mcp bridge (or `unity-scene-bootstrapper` if
   trialed): states idle/walk/run/attack/cast/block/dodge/hit/death + the locomotion blend; the engine
   selects state via the bridge, never owning the controller's logic.
3. **Camera** — drive a fixed dimetric VCam + combat framing with **Cinemachine** (ADOPTED, see below).
4. **Render** in the local headed Unity editor via `extensions/renderers/unity/tools/mcp_stdio_exec.py`; use
   `manage_camera` for captures and keep the non-black gate.
5. **Score** the motion with the `visual-critic` skill's **L7 MOTION lens** (from a render reel) vs the
   PoE2 painterly bar; feed defects back → re-animate/re-import until it converges.

## Adopted Unity-agent tools (from the 2026-06-28 skills review; most of the ~30 were SKIP)
- **ADOPT — Cinemachine** (Besty0728/Unity-Skills bundle): the only camera tool that fits — fixed
  dimetric pin + combat framing, render-pipeline-agnostic (works on our built-in pipeline), driven
  headlessly. Add `com.unity.cinemachine` to the box project; drive VCam setup via the existing bridge.
- **TRIAL — unity-compile-fixer + unity-test-runner** (Dev-GOM `unity-dev-toolkit` plugin): headless
  compile-error auto-fix loop + batchmode test runner for the C# glue. Repoint compile-fixer's
  diagnostics from OmniSharp to the CoplayDev compile output before relying on it.
- **SKIP** (architecture-mismatch): ECS/DOTS (fights the Python sole-writer fence), URP-specialist
  (URP deferred), rival editor bridges (akiojin/hatayama — fork the CoplayDev bridge), addressables,
  input-system (input routes through the engine). Full scorecard: session 2026-06-28.
