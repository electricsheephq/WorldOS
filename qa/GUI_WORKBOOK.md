# WorldOS GUI Workbook — the living punch-list (verified on the REAL surface)

> Single source of truth for the GUI test→fix→look loop. Each row: defect, VERIFIED root cause
> (file:line), fix, status, proof. "Verified" = observed on the live playable surface
> (8799 from CANONICAL — has _private art) or read from canonical source. NOT a proxy guess.
> Iteration surface: `http://127.0.0.1:8799/openworlds/` (`scripts/play.sh` from canonical).
> Gate surface: built `dist/WorldOS.app` via `qa/ui_playtest_app.sh`. See `WorldOS-GUI-RUNBOOK.md`.

## ★ HEADLINE (verified 2026-05-31, 3 clean reads on live canonical 8799)
**Most of what the owner saw broken was a STALE-BUILD / WORKTREE-WITHOUT-ART artifact, not broken canonical code.** Served correctly from canonical:
- `can_act:true`; palette **5 exploration actions ENABLED** (continue/say/do/check/save); attack/bonus/reaction correctly disabled ("not in combat").
- **ALL images 200**: `location:loc-lower-city`, `portrait-rolan`, `portrait-minsc`, `map_lower-city`, `region:baldurs-gate`, `scene_baldurs-gate`.
- DM cold-open narration = 4842 chars with **27 paragraph breaks** (the prose IS well-formed).
⇒ The fix for "no images / no map / no palette" is **serve/build WITH `_private` art present** (infra), not 3 code PRs. The real *code* bugs are layout prominence + render formatting + the silent companion.

## Phase 0 — infra (DONE)
- ✅ `launch.json` repointed off the deprecated LEXAR copy → canonical/8799/`/openworlds/`/live state.
- ✅ 8799 playable from canonical: `can_act:true`, PC Rolan + Minsc, images + map render.

## REAL bugs (verified; the actual punch-list)
| # | Defect | VERIFIED root cause | Fix | Status | Proof |
|---|---|---|---|---|---|
| G3 | Palette buried in 280px right-rail; `slice(0,6)` drops bonus-action+reaction; no center palette | screen-table.jsx:751 | Drop slice; promote palette to main action column near Declare | Builder B in flight | live: palette enabled but only in right rail |
| G4 | Chronicle renders as ONE block despite well-formed prose | `LogEntry` (screen-table.jsx:870) renders `{text}` with default `white-space` → collapses the `\n\n` the DM DOES emit (27 breaks verified) | `white-space: pre-line` or split to `<p>` | Builder B in flight | chat.jsonl dm msg: 27 double-newlines; render collapses |
| G6 | Companion (Minsc/Alfira, kind='companion') silently in solo party at cold-open, no narrated meeting | solo play.sh + questgen prelude seat a companion into `party` pre-narration | Solo: no silent companion OR gate add_to_party behind a narrated meeting | Builder C in flight | live solo party=['Rolan','Minsc and Boo'] |
| G5 | "No streaming visible" — VERIFY (may be infra: owner watched a stale/wrong surface) | /events+useLiveSession+log_event exist; DM emits paragraph-rich prose | Confirm mid-turn log_event on live 8799; fix skill only if batched | Builder C verifying | DM prose well-formed; streaming path exists |
| G7 | Worktree/.app builds 404 images (no _private) | `_ingested_images_root()` (server.py:203) hardcoded to server.py's repo → worktree has no _private | Honor WORLDOS_REPO_ROOT so .app/worktree builds point at canonical art | TODO (small, infra-grade) | du _private: 2.9G canonical, 0 in worktree |

## EVAPORATED on clean re-verification (were CORRUPTED READS — do NOT chase)
- **G1 "palette all-disabled / PC kind=pc / no active character"** — FALSE. Live PC Rolan is `kind='player'`; palette enabled. (A corrupted snapshot read invented `kind='pc'`/`npc-alfira`. Builder A killed — pushed no PR.)
- **G2 "scene plate 404"** — FALSE. `location:loc-lower-city` → 200 from canonical.
- Earlier "zero images / no map" — environment (served from worktree/stale build), not code.

## NOT bugs (proxy artifacts; confirmed absent in canonical)
- Doubled labels; dead-hero roster (fixed engine-side 4a1d6e8b); "Alfira→Rolan" (play_party pre-seed).

## Loop discipline (corruption-hardened)
The tool channel intermittently FABRICATES file/snapshot reads (it invented G1/G2). **Before tasking a fix, verify the symptom with ≥2 clean reads against the LIVE surface (curl /session-surface, /image HTTP codes) — never a single Read.** Per fix: builder → PR → CI(viewer-tests) → merge → rebuild 8799 → LOOK → tick + proof. Gate = RRI on rebuilt `.app`.
