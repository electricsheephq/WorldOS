# WorldOS GUI Workbook — the living punch-list (verified on the REAL surface)

> Single source of truth for the GUI test→fix→look loop. Each row: defect, VERIFIED root cause
> (file:line), fix, status, proof. "Verified" = observed on the live playable surface
> (8799 from CANONICAL — has _private art) or read from canonical source. NOT a proxy guess.
> Iteration surface: `http://127.0.0.1:8799/openworlds/` (`scripts/play.sh` from canonical).
> Gate surface: built `dist/WorldOS.app` via `qa/ui_playtest_app.sh`. See `WorldOS-GUI-RUNBOOK.md`.

## ★ HEADLINE (historical canonical f5500ac, verified 2026-05-31, 3 clean reads on live canonical 8799)
**Most of what the owner saw broken was a STALE-BUILD / WORKTREE-WITHOUT-ART artifact, not broken canonical code.** Served correctly from canonical:
- `can_act:true`; palette **5 exploration actions ENABLED** (continue/say/do/check/save); attack/bonus/reaction correctly disabled ("not in combat").
- **ALL images 200**: `location:loc-lower-city`, `portrait-rolan`, `portrait-minsc`, `map_lower-city`, `region:baldurs-gate`, `scene_baldurs-gate`.
- DM cold-open narration = 4842 chars with **27 paragraph breaks** (the prose IS well-formed).
⇒ The fix for "no images / no map / no palette" is **serve/build WITH `_private` art present** (infra), not 3 code PRs. The real *code* bugs are layout prominence + render formatting + the silent companion.

Post-#465 note: `origin/main` is now `b15ad3c`, and RRI release scoring requires a
disk-backed `palette_live` proof with **≥6 enabled actions** on a `can_act:true` surface. The 5-action
canonical read above remains useful orientation, but it is not release proof; issue #466 must either prove
the built app now meets the live palette gate or fail with an actionable artifact path.

## Phase 0 — infra (DONE)
- ✅ `launch.json` repointed off the deprecated LEXAR copy → canonical/8799/`/openworlds/`/live state.
- ✅ 8799 playable from canonical: `can_act:true`, PC Rolan + Minsc, images + map render.
- ✅ Lexar worktree `.app` smoke can launch without killing the canonical app and still read canonical
  private art via `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val`.

## REAL bugs (verified; the actual punch-list)

| # | Defect | VERIFIED root cause | Fix | Status | Proof |
|---|---|---|---|---|---|
| G3 | Palette buried in 280px right-rail; `slice(0,6)` drops bonus-action+reaction; no center palette | screen-table.jsx action list used to live outside the main play column and cap rows | Drop slice; promote all exploration actions to main action column near Declare; group combat verbs separately | Merged in #465; needs built-app palette-live proof from #466 | Static proof: `viewer/tests/test_openworlds_static.py::test_openworlds_table_renders_all_actions_without_truncation` and `test_openworlds_table_promotes_palette_to_main_column` |
| G4 | Chronicle renders as ONE block despite well-formed prose | `LogEntry` rendered `{text}` with default `white-space`, collapsing `\n\n` paragraph breaks | Keep sanitization, render narration with `whiteSpace: "pre-line"` | Merged in #465; needs built-app live look + full gate | Static proof: `viewer/tests/test_openworlds_static.py::test_openworlds_table_chronicle_preserves_paragraph_breaks` |
| G6 | Companion (Minsc/Alfira, kind='companion') silently in solo party at cold-open, no narrated meeting | solo play.sh could prompt the DM into seating/recruiting a companion at cold-open | Solo prompt now says the player begins alone; `play_party.sh` with empty companion spec execs solo `play.sh` unchanged | Merged in #465; needs live gate proof | Static proof: `qa/test_release_gate_static.py::test_solo_play_contract_does_not_silently_recruit_companion` |
| G5 | "No streaming visible" — VERIFY (may be infra: owner watched a stale/wrong surface) | /events+useLiveSession+log_event exist; DM emits paragraph-rich prose; PR #394 merged streaming-lite for #393 | Confirm mid-turn log_event on live 8799; fix only if current built-app play still batches blank waits | #394 merged; #393 remains open until built-app Part A+B proves no latency give-ups | DM prose well-formed; streaming path exists; proof still owed on built app |
| G7 | Worktree/.app builds 404 images (no _private) | `_ingested_images_root()` (server.py:203) hardcoded to server.py's repo → worktree has no _private | Split code repo from art repo: viewer honors `WORLDOS_ART_REPO_ROOT`; native app has a Private art repo path and launch-root override for gate builds | Merged in #465; needs #466 full gate proof | 2026-05-31 smoke: worktree `dist/WorldOS.app` pid 69755 launched without killing canonical app; spawned worktree `viewer/server.py` on 8765; `/image?scope=location:loc-lower-city` returned `200 904100`; Info.plist root=`/Volumes/LEXAR/repos/worldos-takeover-stabilization`, art=`/Users/lume/ClawDnD-val` |

## EVAPORATED on clean re-verification (were CORRUPTED READS — do NOT chase)
- **G1 "palette all-disabled / PC kind=pc / no active character"** — FALSE. Live PC Rolan is `kind='player'`; palette enabled. (A corrupted snapshot read invented `kind='pc'`/`npc-alfira`. Builder A killed — pushed no PR.)
- **G2 "scene plate 404"** — FALSE. `location:loc-lower-city` → 200 from canonical.
- Earlier "zero images / no map" — environment (served from worktree/stale build), not code.

## NOT bugs (proxy artifacts; confirmed absent in canonical)
- Doubled labels; dead-hero roster (fixed engine-side 4a1d6e8b); "Alfira→Rolan" (play_party pre-seed).

## Loop discipline (corruption-hardened)
The tool channel intermittently FABRICATES file/snapshot reads (it invented G1/G2). **Before tasking a fix, verify the symptom with ≥2 clean reads against the LIVE surface (curl /session-surface, /image HTTP codes) — never a single Read.** Per fix: builder → PR → CI(viewer-tests) → merge → rebuild 8799 → LOOK → tick + proof. Gate = RRI on rebuilt `.app`.
