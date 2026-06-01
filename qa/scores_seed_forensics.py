#!/usr/bin/env python3
"""One-shot forensic seed for the canonical scores ledger (DELIVERABLE 2).

Populates ``qa/scores.db`` with EVERY historical scored WorldOS run reconstructible to ~95%
confidence, mined from:
  * ``qa/SCORECARD.md``                          (story/mech/angry duo + sprint rows)
  * ``qa/RRI.json`` + ``qa/ui_playtest_runs/*/{score,run}.json``  (GUI sweeps + headless personas)
  * LEXAR ``session-notes/2026-05-*/**/implementation-notes.html`` (prose score citations + context)
  * ``git log`` (date <-> build_sha cross-check)

Each row's SURFACE is classified precisely — the crux of REGRESSION-FORENSICS.md. Anything
uncertain is flagged in ``notes`` (look for "[UNCERTAIN]" / "[SUSPECT]" / "~" on a SHA/date).
Re-runnable: every row uses INSERT OR REPLACE keyed on run_id, so re-seeding is idempotent.

Run:  python3 qa/scores_seed_forensics.py && python3 qa/scores_db.py --render
"""

from __future__ import annotations

from scores_db import add_run, connect, render_markdown

# Build-date lookup for the SHAs cited below (from `git show -s --format=%ci`), so the
# date<->SHA cross-check is explicit in every row rather than implied.
SHA_DATE = {
    "d2f65f1": "2026-05-29", "e5d651f": "2026-05-29", "6e7c3b0": "2026-05-29",
    "227ff32": "2026-05-29", "583b8a5": "2026-05-29", "eb02d1c": "2026-05-30",
    "4267247": "2026-05-30", "71616cd": "2026-05-30", "c6480a3": "2026-05-31",
    "96c0401": "2026-05-31", "f5500ac": "2026-05-31", "35128e2": "2026-05-27",
    "35662ec": "2026-05-29", "8555ca7": "2026-05-29", "d9f1d44": "2026-05-29",
}

# ---------------------------------------------------------------------------
# GROUP A — ENGINE-DUO story/mech/angry runs (gateway-free DM+player claude -p, NO GUI).
# Source: qa/SCORECARD.md story/mech table. All sonnet unless noted. These are the "4.x"
# numbers. Dates are the SCORECARD "Date" column; build SHA inferred from the change-under-test
# + git log (flagged ~ where the row names a fix, not a SHA).
# ---------------------------------------------------------------------------
ENGINE_DUO = [
    # run_id, ts(date), sha, sha_uncertain, story, mech, angry, behav, methodology, persona, notes
    dict(run_id="newmain-rogue2", ts="2026-05-25", sha="~85-commit-merge", story=4.1, mech=3.0, angry=2.0,
         behav="GREEN", persona="rogue (social)",
         notes="85-commit merge validation. Low mech/angry = social run, little combat (coverage artifact, not engine defect). [UNCERTAIN] date ~2026-05-25, exact SHA not recorded."),
    dict(run_id="ocwiz-claude", ts="2026-05-26", sha="~pre-v1.0.0", story=2.5, mech=2.5, angry=2.4,
         behav="RED", persona="wizard", scorer="gpt-5.4", dm="gpt-5.4", actor="gpt-5.4",
         notes="*RED-capped (gate failed). gpt-5.4 OpenClaw-path shakedown. [oc] scorer grades ~1.5 harsher. Scores are gate-capped, not a quality read."),
    dict(run_id="duo-typed1", ts="2026-05-26", sha="~post-#140", story=4.0, mech=4.0, angry=3.3,
         behav="GREEN", persona="wayfarer", methodology="3-lens duo 12-beat",
         notes="DM reframed travel->urban political thriller ('prestige-tier'). add_quest=0 (reach-for gap). angry 3.3 = combat-light coverage artifact."),
    dict(run_id="duo-caster1", ts="2026-05-26", sha="~post-#140", story=4.0, mech=3.8, angry=2.6,
         behav="GREEN", persona="battlemage",
         notes="add_quest=3. Player made 0 cast_spell/0 attack (roleplay infiltration) -> angry 2.6 is a combat-coverage artifact, NOT an engine defect."),
    dict(run_id="duo-director1", ts="2026-05-26", sha="~post-#72-director", story=4.1, mech=3.8, angry=3.2,
         behav="GREEN", persona="wayfarer",
         notes="Campaign Director validated: add_quest fired 3x (reach-for gap closed in play)."),
    dict(run_id="duo-enriched1", ts="2026-05-26", sha="~post-#163", story=4.0, mech=3.6, angry=2.9,
         behav="GREEN", persona="wayfarer",
         notes="Integration run (monster-pack #163/Director/betrayal #158/multiattack #159). angry 2.9 = combat-light wayfarer (sampling). WARN world_peopled."),
    dict(run_id="sprint-story1", ts="2026-05-27", sha="~post-wave-2", story=None, mech=None, angry=None,
         behav="GREEN", persona="wayfarer (social)", methodology="duo 5/8-beat (timed out)",
         notes="Timed out at beat 5 -> no scorer pass. Behavioral GREEN. Qualitative prose ~4.3+ (no number). 1 WARN world_peopled."),
    dict(run_id="sprint-story2", ts="2026-05-27", sha="~pre-35128e2", story=3.0, mech=3.5, angry=3.4,
         behav="GREEN", persona="wayfarer (social)", methodology="3-lens duo 6-beat",
         notes="Pre-clarify-fix. scene_craft=2 (DM took player's turn on a [clarify]). Diagnosed -> fixed in 35128e2. NOT a regression (viewer-only)."),
    dict(run_id="sprint-story3", ts="2026-05-27", sha="35128e2", story=4.0, mech=3.6, angry=3.3,
         behav="GREEN", persona="wayfarer (social)", methodology="3-lens duo 6-beat",
         notes="Clarify-fix validation. story 3.0->4.0, scene_craft 2->4, prose_atmosphere 5. SKILL.md clarify-sharpening lifted story a full point."),
    dict(run_id="duo-qarc1", ts="2026-05-27", sha="~pre-#203", story=4.0, mech=3.9, angry=3.2,
         behav="GREEN", persona="wayfarer", methodology="3-lens duo 10-beat",
         notes="PRE-wiring baseline (before #203 wired Quest&Arc into DM). All 17 gates PASS incl world_peopled. mech/angry up vs prior (merged combat-fidelity fixes showing)."),
    dict(run_id="duo-wired1", ts="2026-05-27", sha="~post-#203", story=4.2, mech=3.7, angry=2.9,
         behav="GREEN", persona="wayfarer", methodology="3-lens duo 10-beat",
         notes="POST-wiring: story 4.0->4.2 (#203 living-story wiring). 0.1 off 4.3 target. mech/angry combat-light sampling."),
    # ow-* OpenWorlds-canon-PC duos (still engine-duo: run_duo.sh seats a canon PC, no GUI)
    dict(run_id="ow-duoA-040524", ts="2026-05-29", sha="~pre-d2f65f1", story=4.2, mech=4.0, angry=2.5,
         behav="GREEN", persona="Dal (canon)", methodology="3-lens duo 8-beat",
         notes="Pre-fix baseline. Story 4.2 (best yet), mech 4.0. angry 2.5 social-only sampling. [SUSPECT-#305] Dal canonically dead; cite for trend only."),
    dict(run_id="ow-duoB-040525", ts="2026-05-29", sha="~pre-d2f65f1", story=4.1, mech=3.5, angry=3.2,
         behav="GREEN", persona="Dal (canon)", methodology="3-lens duo 8-beat",
         notes="Pre-fix baseline. Story ~4.1; mech 3.5 (noisy). [SUSPECT-#305] dead-Dal."),
    dict(run_id="ow-fix-011115", ts="2026-05-29", sha="~d9f1d44+8555ca7", story=4.1, mech=3.8, angry=3.4,
         behav="GREEN", persona="Dal (canon Harper wizard)", methodology="3-lens duo 8-beat",
         notes="VERSION-SKEW FIX validated (re-rooted DM engine to live checkout; 2 prior RED runs were stale-tree). All 17 gates PASS. [SUSPECT-#305] dead-Dal."),
    dict(run_id="ow-spell-022355", ts="2026-05-29", sha="~d9f1d44", story=3.9, mech=3.9, angry=3.9,
         behav="GREEN", persona="Dal (canon)", methodology="3-lens duo 8-beat",
         notes="Spellbook fix validated: angry 3.4->3.9. Still social-only. [SUSPECT-#305] dead-Dal."),
    dict(run_id="ow-combat-031717", ts="2026-05-29", sha="~d9f1d44", story=4.1, mech=3.9, angry=3.3,
         behav="GREEN", persona="Dal (canon)", methodology="forced-combat duo 8-beat",
         notes="Forced-combat persona EXPERIMENT FAILED (player ignored 'must fight'); reverted (35662ec). Confirms emergent OW play is social. [SUSPECT-#305]."),
    dict(run_id="ow-cs2-040914", ts="2026-05-29", sha="~pre-e5d651f", story=None, mech=None, angry=3.0,
         behav="GREEN", persona="combat-sprint pre-seeded", methodology="combat-sprint 1 fight",
         notes="Honest angry via combat-sprint. combat_resolution 4; capped by DM adherence (ranged-in-melee, unused Parry). Scored ~04:15, ~8h BEFORE e5d651f cues shipped (12:32)."),
    dict(run_id="ow-swA-123842", ts="2026-05-29", sha="e5d651f+227ff32", story=2.5, mech=2.5, angry=2.5,
         behav="RED", persona="Dal (canon)", methodology="3-lens duo 7-beat (capped)",
         notes="*RED on player_in_party (DM loaded PC as companion) + DM went silent beat 7 (host memory pressure: 2 duos+3 audit agents). Fixed 6e7c3b0. Not a quality read."),
    dict(run_id="ow-swB-123842", ts="2026-05-29", sha="e5d651f+227ff32", story=2.5, mech=2.5, angry=2.5,
         behav="RED", persona="Dal (canon)", methodology="3-lens duo 8-beat (capped)",
         notes="*RED on no_rejected_tool_calls (new FATAL gate working). DM used update_character(skills=) -> extra=forbid. Fixed 6e7c3b0 alias shim. Not a quality read."),
    dict(run_id="ow-rv1-134258", ts="2026-05-29", sha="6e7c3b0", story=2.5, mech=2.5, angry=2.5,
         behav="RED", persona="Dal (canon)", methodology="3-lens duo 9-beat (capped)",
         notes="*RED still on player_in_party (kind=player defaulted add_to_party=False) + dm_voices_characters (flat-DM sampling). update_character fix validated. Not a quality read."),
    dict(run_id="ow-fixC-043416", ts="2026-05-29", sha="d2f65f1", story=4.3, mech=3.6, angry=2.8,
         behav="GREEN", persona="Dal (canon)", methodology="3-lens duo 8-beat",
         notes="POST-d2f65f1. Story HIT 4.3 (target). Milestone-XP fix validated (Dal 0->300 XP). [SUSPECT-#305] dead-Dal."),
    dict(run_id="ow-fixD-043417", ts="2026-05-29", sha="d2f65f1", story=4.0, mech=4.0, angry=3.4,
         behav="GREEN", persona="Dal (canon)", methodology="3-lens duo 8-beat",
         notes="POST-d2f65f1. Milestone XP fired (Dal 0->225). Mech 4.0 (best duo mech). [SUSPECT-#305] dead-Dal."),
    dict(run_id="ow-cs3-133001", ts="2026-05-29", sha="e5d651f", story=None, mech=None, angry=3.7,
         behav="GREEN", persona="combat-sprint pre-seeded", methodology="combat-sprint 1 fight",
         notes="Combat fidelity 3.0->3.7 (e5d651f combat cues validated; ow-cs2's 3.0 was scored pre-cues). Residual = DM adherence."),
    dict(run_id="ow-v103-reval", ts="2026-05-30", sha="~v1.0.3-post-#162", story=4.1, mech=4.1, angry=3.2,
         behav="GREEN", persona="Dal Lightspark (canon Harper evoker)", methodology="3-lens duo 8-beat",
         notes="*[SUSPECT-#305] POST-v1.0.3 reval, ALL 20+ gates GREEN incl player_in_party. BUT Dal is canonically DEAD -> story/mech 4.1/4.1 SUSPECT as a clean canon-PC baseline. Engine-plumbing validations hold; quality numbers owe a re-run on a LIVING PC."),
    dict(run_id="ow-living1", ts="2026-05-30", sha="~post-#305", story=4.0, mech=4.0, angry=3.8,
         behav="GREEN", persona="Latham (LIVING canon Guild wizard)", methodology="3-lens duo 8-beat",
         notes="OWED post-#305 LIVING-PC duo (replaces invalid dead-Dal reval). ALL 23 gates PASS. Verdict 'genuinely excellent'. Social-only 8-beat so angry 3.8 coverage-capped. THE valid recent engine-duo baseline."),
]

# ---------------------------------------------------------------------------
# GROUP B — COMBAT-SPRINT runs (engine-duo, pre-seeded fight, Angry-DM only). These are
# BUG-FINDERS; a single sprint's Angry-DM is coverage-capped ~3 (per SCORING.md). NOT
# regressions when low. Source: SCORECARD.md.
# ---------------------------------------------------------------------------
COMBAT_SPRINT = [
    dict(run_id="sprint-postbrief", ts="2026-05-26", sha="~post-#173", angry=2.8,
         notes="Post-#173 per-turn turn_brief. UNCHANGED 2.8 -> lane found engine REJECTS monster Multiattack -> #180."),
    dict(run_id="sprint-multiattack", ts="2026-05-26", sha="~#180", angry=3.3,
         notes="#180 validated: 2.8->3.3. Multiattack correct, crit doubled. #1 residual = Round-1 turn-skip -> #183."),
    dict(run_id="sprint-noskip", ts="2026-05-26", sha="~#183", angry=2.9,
         notes="#183 turn-skip fix validated (skip GONE). 2.9 = single-sprint variance + coverage-capped ~3. New bug: Guiding Bolt phantom marker -> #186."),
    dict(run_id="sprint-postgbolt", ts="2026-05-26", sha="~#188", angry=3.2,
         notes="Post-#188. Guiding Bolt registration fixed. Next half: advantage marker not auto-consumed -> #194. Coverage-capped ~3."),
    dict(run_id="sprint-enriched1", ts="2026-05-27", sha="~#195", angry=3.4,
         notes="#195 enriched seed validated: 3.4 (new sprint high). conditions 3->4. Found repeat-save HIGH defect -> #209."),
    dict(run_id="sprint-enriched2", ts="2026-05-27", sha="~#209", angry=3.4,
         notes="#209 repeat-save validated. HELD 3.4 (capped by Ghoul Multiattack composition #211 + necrotic #210)."),
    dict(run_id="sprint-postmaneuver", ts="2026-05-27", sha="~#215", angry=3.7,
         notes="NEW SPRINT HIGH 3.4->3.7. #215 maneuver-die validated; #210/#211/#213 GONE. Engine combat core clean; residual = DM adherence + auditability."),
    dict(run_id="sprint-cs3", ts="2026-05-27", sha="~post-#180", angry=4.2,
         notes="Combat fidelity RESOLVED. angry 4.2 (was 2.8-3.3 residual). tool_fidelity 5, action_economy 5. 4 defects all MED/LOW theater-of-mind edge cases. THE engine combat-fidelity high-water mark."),
    dict(run_id="cs-hb1", ts="2026-05-27", sha="~post-#180", angry=3.5,
         notes="Sampling variance NOT a regression (cs3=4.2 holds). This run's rolls meant NO condition landed -> conditions=2 drags overall. Engine fundamentals all 4."),
]

# ---------------------------------------------------------------------------
# GROUP C — GUI-HEADLESS-PROXY persona runs (Playwright palette on real /openworlds/ viewer,
# byte-identical play_party.sh backend). Source: qa/ui_playtest_runs/*/{score,run}.json.
# These carry persona SATISFACTION (0-10), not story/mech lens scores. sonnet DM + sonnet
# player. The "GUI barely works" signal lives HERE — and it is LATENCY + image 404s, not a
# story/mech regression (str2-narrative rated prose 9/10).
# ---------------------------------------------------------------------------
GUI_HEADLESS = [
    # run_id, ts, sha, persona, sat, crit, gaveup(bool), pass(bool), img404, notes
    dict(run_id="nb1", ts="2026-05-30T18:00Z", sha="71616cd", persona="newbie", sat=4, crit=0,
         gaveup=True, passed=False, img404=0,
         notes="AI-playtester v1 first end-to-end. completed_intro_flow=true, loop confirmed (browser->/move->DM->snapshot). GAVE UP: hit budget waiting for DM's first narration (locked input, no progress indicator). Latency, not quality."),
    dict(run_id="str-adversarial", ts="2026-05-31T05:50Z", sha="c6480a3", persona="adversarial", sat=8, crit=0,
         gaveup=False, passed=True, img404=2,
         notes="PASS, sat 8. Reached play in 2 actions, 0 console errors. Only 2 image 404s (auto). Best GUI-headless result; shows the surface IS playable."),
    dict(run_id="str-narrative", ts="2026-05-31T05:50Z", sha="c6480a3", persona="narrative", sat=None, crit=None,
         gaveup=None, passed=None, img404=None,
         notes="[UNCERTAIN] run.json present, no score.json (loop incomplete this attempt). Superseded by str2-narrative."),
    dict(run_id="str-optimizer", ts="2026-05-31T05:50Z", sha="c6480a3", persona="optimizer", sat=None, crit=None,
         gaveup=None, passed=None, img404=None,
         notes="[UNCERTAIN] run.json present, no score.json. Superseded by str3-optimizer."),
    dict(run_id="str2-adversarial", ts="2026-05-31T00:26Z", sha="c6480a3", persona="adversarial", sat=4, crit=0,
         gaveup=False, passed=False, img404=6,
         notes="sat 4 (derived), 0 console errors, 6 image 404s. 3 'bug' + 2 ux reports. 7 in-story turns. Not gave-up but didn't pass scorer thresholds."),
    dict(run_id="str2-narrative", ts="2026-05-31T00:26Z", sha="c6480a3", persona="narrative", sat=9, crit=0,
         gaveup=True, passed=True, img404=0,
         notes="sat 9 SELF-REPORTED: 'prose quality excellent'. GAVE UP on DM LATENCY (3-5+ min/turn locks input, dissolves tension). DECISIVE: high story quality, killed by latency surface issue — NOT a quality regression."),
    dict(run_id="str2-optimizer", ts="2026-05-31T00:26Z", sha="c6480a3", persona="optimizer", sat=None, crit=None,
         gaveup=None, passed=None, img404=None,
         notes="[UNCERTAIN] run.json present, no score.json. Superseded by str3-optimizer."),
    dict(run_id="str3-optimizer", ts="2026-05-31T00:26Z", sha="c6480a3", persona="optimizer", sat=5, crit=2,
         gaveup=False, passed=False, img404=19,
         notes="sat 5, 2 CRITICAL bugs (char-sheet depth: abilities/spells tabs), 19 image 404s. Optimizer probes char sheet hard -> finds asset+depth gaps. 0 console errors."),
    dict(run_id="sweep2-newbie", ts="2026-05-31T06:07Z", sha="c6480a3", persona="newbie", sat=None,
         notes="[UNCERTAIN] run.json present, no score.json (sweep2 produced no scored artifact for this persona)."),
    dict(run_id="sweep3-newbie", ts="2026-05-31T06:17Z", sha="c6480a3", persona="newbie", sat=7, crit=0,
         gaveup=False, passed=True, img404=0,
         notes="sat 7, PASS. 0 console/network/image errors. 4 in-story turns. Clean newbie play on c6480a3 viewer surface."),
    dict(run_id="sweep4-veteran", ts="2026-05-31T06:35Z", sha="c6480a3", persona="veteran", sat=5, crit=0,
         gaveup=False, passed=False, img404=4,
         notes="sat 5, 0 critical, 4 image 404s, 4 major (relations/abilities/battle tab ux). 3 in-story turns. Didn't pass thresholds but no give-up."),
    # gate-96c0401-* : part_a native transition FAILed, backend_not_ready -> persona loop never ran (no score)
    dict(run_id="gate-96c0401-newbie", ts="2026-05-31T05:52Z", sha="96c0401", persona="newbie", surface="smoke-only",
         passed=False,
         notes="part_a native_transition_356 FAIL -> backend_not_ready -> persona loop never ran. No quality read (harness/transition failure). 4 sibling personas same."),
]

# ---------------------------------------------------------------------------
# GROUP D — RRI / GUI release sweeps (built .app or its proxy). Source: qa/SCORECARD.md
# Release Sweep Ledger + RRI.json. THESE are the canonical "2.x" GUI numbers — and BOTH are
# explicitly flagged PARTIAL / PRE-HARDENING / HARNESS-CONTAMINATED (not release verdicts).
# ---------------------------------------------------------------------------
RRI_SWEEPS = [
    dict(run_id="sweep1", ts="2026-05-31", sha="c6480a3", surface="GUI-built-app",
         rri=6.0, sat=6.0, crit=2, story=None, mech=None, behav=None, img=None, passed=False,
         per_persona={"arc": "5/5 complete", "narrative": "gave-up (latency)", "optimizer": "2 crit (char-sheet depth)"},
         methodology="5-persona RRI gate (part-A native + part-B persona)",
         notes="*INCOMPLETE / PRE-HARDENING, not release evidence. G3 sat 6.0 + narrative gave-up (LATENCY). G2 optimizer 2 crit (spell DC + class features). G1 arc 5/5, native PASS, palette live. Behavioral/UI/image/console denominators incomplete."),
    dict(run_id="gate-f5500ac-partial", ts="2026-05-31", sha="f5500ac", surface="GUI-built-app",
         rri=2.7, sat=4.0, crit=1, story=4.0, mech=2.7, behav="RED", img=0.0, passed=False,
         per_persona={"newbie": {"sat": 4, "crit": 1, "completed_intro_flow": True, "image_rate": 0.0}},
         methodology="5-persona RRI gate (only newbie scored)",
         notes="*PARTIAL / HARNESS-CONTAMINATED, NOT a release verdict. ONLY newbie wrote score.json; veteran/adversarial/narrative/optimizer lacked artifacts after port/backend harness failures. mech 2.7 = ONE persona's char-sheet ding; behavioral RED + image 0% = harness/clean-checkout (no _private art), not engine. This is THE '2.x GUI' row — and it is not a real quality reading. RRI.json: gates_passed=3/11."),
]

# ---------------------------------------------------------------------------
# GROUP E — BUILT-APP & handoff smoke proofs (deterministic / single-move Codex, no LLM
# quality lens). Source: qa/SCORECARD.md built-app + handoff ledgers + run.json files.
# These are wiring/observability PASSes (handoff_score=100), explicitly "not a release
# verdict" and carry NO story/mech score. Recorded for completeness of the surface timeline.
# ---------------------------------------------------------------------------
SMOKE_PROOFS = [
    dict(run_id="worldos-app-baseline", ts="2026-05-30", sha="4267247", surface="GUI-built-app",
         sat=2.0, passed=False, dm="sonnet", actor="manual/CGEvent", scorer="manual",
         methodology="honest built-app baseline (CGEvent + viewer HTTP)",
         source="LEXAR session-notes/2026-05-30/worldos-app-baseline/implementation-notes.html",
         notes="*THE '~2/10' GUI number the owner remembers. Honest: real .app = ~2/10 (CANNOT START — ContinueBanner CTA never calls startProviderSession -> read-only dead-end, P1 WIRING). Via live loop = ~6/10 (great opening, blank-reply beats). Note explicitly: 'the bug is purely the GUI->bridge wiring (P1), NOT the engine/DM/viewer play loop'; 'Engine/dice/state = solid; the surface stayed playable.' SAME build era as engine-duo 4.0/4.0. DECISIVE surface-artifact evidence."),
    dict(run_id="scripted-smoke-20260531T233022Z-080497e", ts="2026-06-01", sha="080497e", surface="smoke-only",
         passed=True, dm="scripted", scorer=None,
         source="LEXAR worldos-agent-grade-app-testability/scripted-smoke-20260531T233022Z-080497e/",
         notes="app-status/session-surface wiring PASS (ready_for_play). Headless screenshot timed out -> observability evidence, no quality read."),
    dict(run_id="codex-app-short-20260601T022114", ts="2026-06-01", sha="c3dfee6", surface="GUI-built-app",
         passed=True, dm="codex", scorer=None, methodology="built-app Codex 2-move playtest",
         source="LEXAR worldos-built-app-playtest/codex-app-short-20260601T022114/",
         notes="Diagnostic short playtest, Arka seated, 2 /move accepted. #476: one recovered log_event(speaker:null). Not an RRI verdict, no quality lens."),
    dict(run_id="post475-main-app-proof-20260601T051230", ts="2026-06-01", sha="32ca561", surface="GUI-built-app",
         passed=True, dm="codex", scorer=None, methodology="built-app Codex 1-move proof",
         source="LEXAR worldos-built-app-playtest/post475-main-app-proof-20260601T051230/",
         notes="Playable diagnostic, provider trace NOT clean (4 cancelled tool calls). Superseded for #479 by f7ab6d7. No quality lens."),
    dict(run_id="codex-current-main-proof-20260531T234242Z", ts="2026-06-01", sha="19c3fd0", surface="GUI-built-app",
         passed=True, dm="codex", scorer=None, methodology="built-app Codex 1-move proof",
         source="LEXAR worldos-built-app-playtest/codex-current-main-proof-20260531T234242Z/",
         notes="Playable diagnostic, provider trace NOT clean (3 cancelled). Superseded by f7ab6d7. No quality lens."),
    dict(run_id="codex-main-f7ab6d7-proof-20260601T010058Z", ts="2026-06-01", sha="f7ab6d7", surface="GUI-built-app",
         passed=True, dm="codex", scorer=None, methodology="built-app Codex 1-move trace-clean proof",
         source="LEXAR worldos-built-app-playtest/codex-main-f7ab6d7-proof-20260601T010058Z/",
         notes="TRACE-CLEAN merged-main diagnostic, closes #479. Alfira active, 5 actions, 1 /move resolved, 0 failed tool calls. Not an RRI verdict; no quality lens (proves the built-app play LOOP works)."),
    dict(run_id="handoff-20260601T081016Z-4a0efe1", ts="2026-06-01", sha="4a0efe1", surface="GUI-built-app",
         rri=None, passed=True, dm="scripted+codex", scorer=None,
         methodology="hybrid handoff gate (web smoke 5 + built smoke 5 + built Codex 1)",
         source="LEXAR worldos-agent-grade-app-testability/handoff-20260601T081016Z-4a0efe1/",
         notes="handoff_score=100/100 (web-scripted + built-scripted + built-Codex all PASS, same SHA). release_verdict=FALSE. Velocity gate, NOT a release verdict; carries no story/mech."),
    dict(run_id="handoff-20260601T085319Z-fd9dba5", ts="2026-06-01", sha="fd9dba5", surface="GUI-built-app",
         passed=True, dm="scripted+codex", scorer=None,
         methodology="hybrid handoff gate (web smoke 5 + built smoke 5 + built Codex 1)",
         source="LEXAR worldos-agent-grade-app-testability/handoff-20260601T085319Z-fd9dba5/",
         notes="handoff_score=100/100, dirty=false, Alfira active, console 0, network 0, image OK. release_verdict implied false. Supersedes 4a0efe1 as GUI velocity proof. No quality lens."),
    dict(run_id="handoff-20260601T100304Z-9545383", ts="2026-06-01", sha="9545383", surface="GUI-built-app",
         passed=True, dm="scripted+codex", scorer=None,
         methodology="hybrid handoff gate (web smoke 5 + built smoke 5 + built Codex 1)",
         source="LEXAR worldos-agent-grade-app-testability/handoff-20260601T100304Z-9545383/",
         notes="handoff_score=100/100 after #508. release_verdict=false. CURRENT GUI velocity proof. Full #466 5-persona RRI still required. No quality lens."),
]


def seed(db_path=None, md_path=None) -> int:
    """Seed all historical rows into ``db_path`` (default: the canonical qa/scores.db) and
    re-render ``md_path`` (default: qa/scores_ledger.md). Both args exist so tests can target a
    tmp db; production callers pass nothing."""
    from scores_db import DB_PATH, MD_PATH
    db_path = DB_PATH if db_path is None else db_path
    md_path = MD_PATH if md_path is None else md_path
    conn = connect(db_path)
    n = 0

    for r in ENGINE_DUO:
        sha = r["sha"]
        add_run(
            r["run_id"], db_path=conn, surface="engine-duo",
            ts=r["ts"], build_sha=sha, build_date=SHA_DATE.get(sha.lstrip("~")),
            dm_model=r.get("dm", "sonnet"), actor_model=r.get("actor", "sonnet"),
            scorer_model=r.get("scorer", "claude"),
            methodology=r.get("methodology", "3-lens duo"),
            story_overall=r.get("story"), mech_overall=r.get("mech"), angrydm_overall=r.get("angry"),
            behavioral=r.get("behav"),
            **({"pass": 0} if r.get("behav") == "RED" else {}),
            source_path="qa/SCORECARD.md",
            notes=f"[persona={r.get('persona','?')}] {r['notes']}",
        )
        n += 1

    for r in COMBAT_SPRINT:
        sha = r["sha"]
        add_run(
            r["run_id"], db_path=conn, surface="engine-duo",
            ts=r["ts"], build_sha=sha, build_date=SHA_DATE.get(sha.lstrip("~")),
            dm_model="sonnet", actor_model="sonnet", scorer_model="claude",
            methodology="combat-sprint (pre-seeded fight, Angry-DM only)",
            angrydm_overall=r.get("angry"), behavioral="GREEN",
            source_path="qa/SCORECARD.md",
            notes=f"[combat-sprint bug-finder; single sprint coverage-capped ~3] {r['notes']}",
        )
        n += 1

    for r in GUI_HEADLESS:
        gaveup = r.get("gaveup")
        per = {"persona": r["persona"], "sat": r.get("sat"), "gaveup": gaveup, "crit": r.get("crit"),
               "image_404s": r.get("img404")}
        add_run(
            r["run_id"], db_path=conn, surface=r.get("surface", "GUI-headless-proxy"),
            ts=r["ts"], build_sha=r["sha"], build_date=SHA_DATE.get(r["sha"].lstrip("~")),
            dm_model="sonnet", actor_model="sonnet", scorer_model="derived/self-reported",
            methodology="AI-playtester palette persona (ui_playtest.sh part-B)",
            cross_persona_sat=r.get("sat"), critical_bugs=r.get("crit"),
            per_persona_json=per,
            **({"pass": int(r["passed"])} if r.get("passed") is not None else {}),
            source_path=f"qa/ui_playtest_runs/{r['run_id']}/score.json",
            notes=r["notes"],
        )
        n += 1

    for r in RRI_SWEEPS:
        add_run(
            r["run_id"], db_path=conn, surface=r["surface"],
            ts=r["ts"], build_sha=r["sha"], build_date=SHA_DATE.get(r["sha"].lstrip("~")),
            dm_model="sonnet", actor_model="sonnet", scorer_model="claude/derived",
            methodology=r["methodology"],
            story_overall=r.get("story"), mech_overall=r.get("mech"),
            behavioral=r.get("behav"), cross_persona_sat=r.get("sat"),
            rri=r.get("rri"), critical_bugs=r.get("crit"), image_render_rate=r.get("img"),
            per_persona_json=r.get("per_persona"),
            **({"pass": int(r["passed"])} if r.get("passed") is not None else {}),
            source_path="qa/SCORECARD.md / qa/RRI.json",
            notes=r["notes"],
        )
        n += 1

    for r in SMOKE_PROOFS:
        add_run(
            r["run_id"], db_path=conn, surface=r["surface"],
            ts=r["ts"], build_sha=r["sha"], build_date=SHA_DATE.get(r["sha"].lstrip("~")),
            dm_model=r.get("dm", "scripted"), actor_model=r.get("actor", "scripted"),
            scorer_model=r.get("scorer"),
            methodology=r.get("methodology", "smoke/handoff proof"),
            cross_persona_sat=r.get("sat"), rri=r.get("rri"),
            **({"pass": int(r["passed"])} if r.get("passed") is not None else {}),
            source_path=r.get("source"),
            notes=r["notes"],
        )
        n += 1

    conn.close()
    render_markdown(db_path, md_path)
    return n


if __name__ == "__main__":
    count = seed()
    print(f"seeded {count} historical runs into the canonical ledger; rendered qa/scores_ledger.md")
