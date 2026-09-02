# QA rig vs the owner's game — window policy and the #1672 badge

Incident, 2026-09-02: `qa/qa_sandbox.py up` launched a second instance of
`/Users/m1/worldos-unity/BuildOutput/WorldOSPlayer.app` with **no Unity window arguments**. It
inherited the SHARED preferences domain's `Screenmanager Fullscreen mode = 1` +
`Screenmanager Resolution Use Native = 1`, came up fullscreen on the developer's only display during
a gate run, and the Mac had to be rebooted.

## Phase 1 — SHIPPED (launch side, current build, no rebuild)

In `qa/qa_sandbox.py` + `qa/lib_qa_window.py`. See the `qa_sandbox.py` module docstring for the
operator-facing summary. In brief:

| Fence | Where |
|---|---|
| `-screen-fullscreen 0 -screen-width 1280 -screen-height 700 -logFile <rundir>/unity_player.log` (a Unity RESOLUTION, i.e. **backing pixels**) | `qa_sandbox._player_windowed_args()` |
| per-run `CFFIXED_USER_HOME=<rundir>/home` (own `Application Support`; `/shot` cannot collide) | `qa_sandbox.up()` |
| owner-active guard (ioreg `HIDIdleTime`, `FORCE_PLAYER_QA=1`, exit `75`) | `qa_sandbox.owner_active_guard()` |
| player is its own `Popen`; `caffeinate -is -w <pid>` is a **sibling** watcher | `qa_sandbox.up()` |
| `sandbox.json` written **before** the readiness wait (anti-orphan fence) | `qa_sandbox.up()` |
| CGWindowList fullscreen/offscreen watchdog on EVERY readiness poll + `qa_sandbox.py watchdog` (permission-free; fullscreen ⇒ kill the rig player, exit `3`) | `qa_sandbox._watchdog()` |
| `/health` coverage bound (polled; < 60 % of the display's backing-pixel area — not an exact size) | `qa_sandbox._assert_windowed()` |
| focus restored **by pid** via System Events (never `open -a`, which would LAUNCH a player) | `lib_qa_window.restore_front()` |
| whole-domain plist snapshot → diff → **attribution-safe** restore, and a two-sensor orphan scan | `qa_sandbox.down()` |
| recorded pgid re-verified before anything is signalled (pid reuse) | `qa_sandbox._kill_group()` |

**Size rationale + UNITS.** `-screen-width/-screen-height` are a Unity RESOLUTION — *backing
pixels* — while the desktop budget and every CGWindowList bound are *points*, so the fit clamp
converts the point budget with the display's backing scale before comparing. Measured 2026-09-02:
**1512x835 points / 3024x1670 pixels, scale 2**; the 1280x700 request produced a **640x382 point**
window and `/health` reported `1280x700`. 1280x700 is aspect 1.829 ≥ the display's, so the ortho crop
(`walk_test.world_to_window_px`) shows *at least as much* horizontal world as the fullscreen baseline
and no sample cell that was in frame falls out.

**Restore rule (what `down` will and will not write).** The domain is SHARED, so a blind restore
would silently revert the owner's own settings. A key is rewritten only when it changed since `up`
AND still holds the exact value this rig deterministically writes — `Screenmanager Fullscreen mode`
`3`, `Screenmanager Resolution Width/Height` = the size actually requested, `Screenmanager Resolution
Use Native` `0` — and only after integer validation. `Screenmanager Window Position X/Y` (cosmetic),
any key the owner wrote last, and the `unity.player_*` churn keys are reported and left alone.

**What Phase 1 does NOT do.** The rig's macOS window title is still `WorldOSPlayer`
(`ProjectSettings.asset:16 productName`, re-asserted in `BuildMacOSPlayer.cs`). Today the rig is
distinguished by window geometry, `lsof -nP -iTCP:8972 -sTCP:LISTEN -t` (owner = 8971), and
`sandbox.json`. #1672 item 4 (the badge) is Phase 2.

## Phase 2 — NOT IN THIS PR (needs an Editor rebuild)

`extensions/renderers/unity/scripts/CombatSurfaceClient.cs`. **Zero `ProjectSettings` /
`PlayerSettings` changes**, so an un-env'd launch stays byte-identical for the owner. The whole
change hangs off the existing `WORLDOS_QA_INPUT == "1"` gate — the one line that already separates
rig from owner.

1. **`:563`** — `if (Environment.GetEnvironmentVariable("WORLDOS_QA_INPUT") == "1") StartQaInput();`
   becomes `{ ApplyQaWindowPolicy(); StartQaInput(); }`.

2. **New `ApplyQaWindowPolicy()`**, beside the QA block at `:2547-2596`:
   - re-assert `Application.runInBackground = true` (already set at `:508`);
   - read `WORLDOS_QA_WIN_W` / `WORLDOS_QA_WIN_H` (defaults 1280/700), clamped against
     `Screen.mainWindowDisplayInfo`;
   - snapshot the four shared `Screenmanager` PlayerPrefs into `_qaPrefsBefore`;
   - `if (Screen.fullScreen || Screen.width != w || Screen.height != h)
      Screen.SetResolution(w, h, FullScreenMode.Windowed);`
   - build a **static** `_qaBadge` string:
     `"QA RIG — NOT YOUR GAME · qa :" + WORLDOS_QA_INPUT_PORT + " · pid " + pid`;
   - start `QaPlaceWindowCo(w, h)`.

3. **`QaPlaceWindowCo`** — wait out the async `SetResolution`, then
   `Screen.MoveMainWindowTo(ref di, pos)` parking bottom-right but **fully on-screen** (clamp so
   ≥ 220x120 points stay on the display), awaiting `op.isDone`. **Never** minimize, hide, push
   offscreen, or use `-batchmode`: `runInBackground` governs the update loop, not presentation, and a
   window with no on-screen surface hands `ScreenCapture` (`:2353-2354`) a black or frozen
   backbuffer **with no HTTP error** — `walk_test` reads that as "the actor never moved". Log
   `"[CSC] QA window WxH windowed @(x,y) fullScreen=False"` so the run dir carries the receipt.

4. **`DrawQaBadge()` must be the FIRST statement of `OnGUI()` (`:2368`).** `:2371` is
   `if (string.IsNullOrEmpty(_advMsg)) return;` — anything placed after it renders only while an
   advisory is up. Fixed `Rect(8, 8, 560, 26)` top-left, red `DrawTexture` + `GUI.Label`, no click
   consumption. **STRICTLY STATIC** content: no clock, counter, fade or pulse. An animated badge is a
   large stable-position diff blob that can win the nearest-neighbour race at
   `walk_test.py:236-254`. Enforced by `qa/test_qa_sandbox_window.py::test_csharp_qa_window_policy`
   (currently `xfail`).

5. **`OnApplicationQuit()` (`:2706`)** → `{ StopQaInput(); QaRestoreScreenPrefs(); }`, restoring
   `_qaPrefsBefore`. This is a **backstop only** — Unity's ScreenManager may persist after
   `OnApplicationQuit`, so the Python snapshot/diff/restore in `qa_sandbox.down()` stays the
   authority and its verdict is what the gate reads.

**Keep BOTH halves, permanently.** `ApplyQaWindowPolicy` runs in `Start()`, i.e. after the window
already exists, and both `SetResolution` and `MoveMainWindowTo` are async — a flag-less launch would
still FLASH fullscreen on the owner's only display for several frames. The CLI flags prevent the
flash; the C# guarantees windowed+badged however the rig is started (double-click, `open`, a future
lane that forgets the flags). **Do not remove the launch flags after the rebuild.**

**Not achievable, do not promise it.** There is no Unity API to set the macOS window **title** — it
is `productName`. A true title badge needs a native plugin, or a separate QA build with its own
`productName` + bundle id (which would also dissolve the shared plist and the shared
`persistentDataPath`, at the cost of a second build to maintain and an ad-hoc re-sign). The IMGUI
banner is the no-plugin answer.

## Still open after Phase 2

- Shared `persistentDataPath` `/shot` collision: both instances write `wos_shot_<id>.png` into
  `~/Library/Application Support/com.worldos.WorldOSPlayer` with per-**process** counters
  (`CombatSurfaceClient.cs:2594, 2650-2653`). #1582 fixed intra-process races only. The owner-active
  guard makes concurrency unlikely; it is **not** an interlock.
