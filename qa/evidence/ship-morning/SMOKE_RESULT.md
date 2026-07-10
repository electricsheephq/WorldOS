# SHIP-MORNING — three-room world (crypt <-> camp <-> tavern) ship + smoke

**Verdict: PASS.** The tavern (PR #1531 NEW-ROOM-TAVERN, epic #1508) is deployed to the player build
alongside crypt + camp and rendered live for the first time via a real player, driven end-to-end
through the #1466 QA `/click` channel (the same validation+POST path a human click takes).

## What shipped
- `extensions/renderers/unity/plates_manifest.json` — 3 plate entries (crypt, camp_clearing_night,
  tavern) deployed to the GEX44 box (`plates/` + `Assets/StreamingAssets/plates/`) + the tavern plate
  PNG (`tavern_truegrey_v1.png`, from `qa/evidence/new-tavern/`).
- Editor verified compile-clean (0 live compile errors; a historical CS0619 log line from an already-
  deleted Hovl demo script is stale, not current — confirmed via `read_console` + file-existence check).
- `Tools/WorldOS/Build/macOS Player (Universal)` rebuilt: **0 errors, 1 pre-existing warning**,
  `StreamingAssets` verified to carry the tavern plate + updated manifest.
- `/home/unity/WorldOSPlayer.app.zip` + `BuildOutput` + `INSTALL.md` refreshed on the box (new top
  section: "three-room world + fire + tuned collision").

## Bug found + fixed (blocking the smoke's own premise)
Live-driving the loop surfaced a real defect: `server.cross_door()` (`servers/engine/server.py`)
resolved the destination as `connections[0]` **unconditionally**, ignoring which door cell was
actually crossed. The walkslice crypt hub has TWO doors (camp @ (6,0), tavern @ (0,5)); before the
fix, crossing the **tavern** door landed the party in `camp_clearing_night` every time — verified live
against a booted engine before touching any UI. Root cause: no per-door destination mapping, only a
"take the first connection" default (self-documented in the old docstring as a known gap for
multi-connection rooms).

**Fix (this PR):** `cross_door` now resolves `door_cells[i] -> connections[i]` by position — the
authoring convention `qa/seed_gfx_walkslice.py` already follows (each door cell and its connection are
appended in the same order) — falling back to `connections[0]` when the index doesn't line up
(byte-identical for every existing single-door room). Mirrored the same fix in the viewer's
`_combat_doors` projection so the in-player door label ("To Firelit Tavern Hall") matches the room the
engine will actually cross into. Added a regression test
(`test_cross_door_resolves_each_door_of_a_multi_door_hub_to_its_own_room`) that reproduces the exact
2-door-hub shape and asserts each door lands in its own room; all existing `cross_door` +
`walkslice_seed_grid` tests still pass (16/16) — the single-door/first-connection fallback behavior is
unchanged.

## Smoke — the #1466 QA `/click` channel, live player build
Driven against the freshly built `WorldOSPlayer.app` (macOS, this build) with
`WORLDOS_QA_INPUT=1` against a locally booted viewer serving the `walkslice_smoke01` 3-room seed
(`qa/seed_gfx_walkslice.py`).

| Step | Channel call | Result |
|------|--------------|--------|
| crypt (initial) | — | PASS — both doors correctly labeled ("To Campfire Clearing", "To Firelit Tavern Hall") |
| crypt -> camp | `POST /click {"c":6,"r":0}` | PASS — `cross_door` landed in `camp_clearing_night`; animated firepit burning |
| camp -> crypt | `server.travel_to` (camp has no authored return door_cell — pre-existing content gap, out of scope for this ship) | PASS |
| crypt -> tavern | `POST /click {"c":0,"r":5}` | **PASS — landed in `tavern` (post-fix); the tavern's first-ever player render** |
| walk in tavern | `POST /click {"c":7,"r":2}` | PASS — hero glided to the new cell |

## Frames (this dir)
- `frame1_crypt.png` — crypt antechamber, both door labels correct.
- `frame2_camp_fire.png` — camp clearing, animated firepit burning, hero present.
- `frame3_tavern.png` — tavern interior (hearth, bar, lanterns, tables) mid-walk — the tavern's
  first-ever player render.

## Known gap (not fixed here, out of scope)
`camp_clearing_night`'s `scene_grid` has no authored `door_cells` back to the crypt
(`qa/seed_gfx_camp.py:_build_camp_grid`), so there is no clickable return door from camp today — the
smoke used the engine's `travel_to` primitive directly for that hop. Content-authoring gap, unrelated
to the tavern ship; flagged for a follow-up seed update.
