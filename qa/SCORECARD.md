# ClawDnD QA Scorecard — running ledger

> The "what did each run score, and what change was under test" log. Updated per QA run.
> System reference: `qa/SCORING.md`. Targets: **story ≥ 4.3, mechanical ≥ 4.5, gate GREEN, 0 critical/high.**
> `*` = RED-capped (gate failed → scores forced ≤ 2.5; not a real quality reading).
> Scorer: claude `score.sh` unless noted `[oc]` (gpt-5.4, grades ~1.5 harsher).

| Run | Date | World | Persona | Model | Beats | Gate | Story | Mech | AngryDM | Change under test / notes |
|---|---|---|---|---|---|---|---|---|---|---|
| newmain-rogue2 | ~2026-05-25 | baldurs-gate | rogue (social) | sonnet | — | GREEN | 4.1 | 3.0 | 2.0 | 85-commit merge validation. Low mech/angry = social run, little combat exercised (coverage artifact, not engine defect). |
| ocwiz-claude | ~2026-05-26 | baldurs-gate | wizard | gpt-5.4 [oc] | — | RED | 2.5* | 2.5* | 2.4* | RED-capped (gate failed). gpt-5.4 OpenClaw-path shakedown. |
| duo-typed1 | 2026-05-26 | baldurs-gate | wayfarer | sonnet | 12 | GREEN | 4.0 | 4.0 | 3.3 | DM reframed travel→urban political thriller (verdict: "prestige-tier"). **add_quest=0 (reach-for gap CONFIRMED)**; 0 wander encounters fired (urban, few travel triggers → variety not exercised in play). angry-dm 3.3 = combat-light coverage artifact. |
| duo-caster1 | 2026-05-26 | baldurs-gate | battlemage | sonnet | 10 | GREEN | 4.0 | 3.8 | 2.6 | **add_quest=3** (contract framing prompts quest-tracking ✓). But player made 11 say/6 do/3 check, **0 cast_spell/0 attack** — DM built an infiltration+moral-dilemma scene; the AI player engaged via roleplay, not combat → no formal fight → angry-dm 2.6 is a **combat-coverage artifact, NOT an engine defect**. |

## Change log (run ↔ engine delta)
- **#137** wandering-encounter system (travel/camp combat risk) — merged.
- **#138** Parley scaffold + encounter_outlook + balancing doctrine — merged.
- **#139** GPT-5.4 / OpenClaw QA path (infra) — merged.
- **#140** typed multi-resolution encounters (combat/skill/social/hazard/boon; 60% non-combat) — merged. Engine variety validated in vivo (pick_typed_encounter distribution: generic 40% combat, road 20%, undead ruin 61%).

## World scope (owner steer 2026-05-26)
- **Baldur's Gate is THE world.** All content + QA target BG. Sundered Reach was a side-option — **deprioritized** (existing SR content stays in place, no further investment). Enrich BG instead (area-detail files, BG-flavored mid-tier monsters, map depth). Perfect the whole system on BG; a 2nd world becomes near-free via the universe seed later.

## Open quality threads (drive the loop)
- **angry-dm / 5e-fidelity stuck ~2.6–3.3 = AI-player NARRATIVE DRIFT, not an engine defect.** duo-caster1 proved it: even a combat-seeking battlemage made 0 cast_spell/0 attack (11 say/6 do/3 check) — both AI player and DM gravitate to roleplay/social/puzzle, so combat (the richest 5e surface) is rarely *formally run*. A human player would fight. FIX = a **forced-combat QA lane** (a scenario that guarantees an engine-run fight) to read real 5e-fidelity — NOT an engine change. (The engine combat is validated by the audit + the combat-torture test.)
- **add_quest reach-for — CONFIRMED + nuanced.** duo-typed1 (emergent thriller) = 0; duo-caster1 (explicit contract) = 3. The DM tracks quests when the goal is explicit, not in emergent play. STRUCTURAL fix = the **Campaign Director** (#72) surfacing "untracked active hook → add_quest" each beat (anchored on #72).
- **Story ~4.0 (vs 4.3 target)** — close. The Campaign Director (#71–73) is the lever (scene-debt: setup-without-payoff, no-reversal, NPC-introduced-but-silent).
- **Companion-agent surface under-tested** — recent QA = player+DM duos only; restore `run_party.sh` (1 AI companion + DM-voiced others) to the cadence (exercises companion clarify/tactics + the betrayal path, #142).
- **must_offer_out (set-piece path)** — DONE: the `start_combat` outlook fold-in (#146) now auto-surfaces it for any over-matched fight, not just wander.
