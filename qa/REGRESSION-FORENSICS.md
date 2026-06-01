# WorldOS Scores — Regression Forensics

> **The question (owner's #1 ask):** scores teeter between *"engine killer 4.5 all-around"* and
> *"GUI barely works, ~2/10"* with no way to see when or how a regression happened. Is the gap a
> **real code regression**, a **surface change** (engine-duo vs GUI), a **model change**
> (Opus→Sonnet), or a **methodology change**?
>
> **Verdict (95% confidence): SURFACE + METHODOLOGY artifact — NOT a code regression, NOT a model
> change.** The "4.x" and "2.x" numbers measure **two different surfaces with two different
> rubrics**, both on **the same model (Sonnet)**, in **the same week**. The story/mechanical engine
> never regressed. The GUI "2/10" is a *playability/wiring* score of a *different* surface, and the
> single worst data point ("RRI 2.7") is explicitly flagged in-repo as **harness-contaminated, not a
> release verdict**.
>
> Evidence: the canonical ledger `qa/scores.db` (57 rows) / `qa/scores_ledger.md`. Sources mined:
> `qa/SCORECARD.md`, `qa/RRI.json`, `qa/ui_playtest_runs/*/{score,run}.json`, LEXAR session-notes,
> and `git log` (date↔SHA cross-check). Methodology spec: `qa/SCORING.md`, `qa/UI_PLAYTEST.md`.

---

## 1. The two scales are not comparable — they measure different things

| | "4.x" scale | "2.x" scale |
|---|---|---|
| **Surface** | `engine-duo` — two gateway-free `claude -p` sessions (DM + AI player) over the engine MCP + snapshot writer. **NO GUI.** (`qa/run_duo.sh`, confirmed: "Gateway-free… no OpenClaw"; its only `server.py` ref is the read-only `player_server.py` MCP facade, not a web viewer.) | `GUI-built-app` / `GUI-headless-proxy` — the shipped `dist/WorldOS.app` (WKWebView) and the Playwright-palette harness driving the real `/openworlds/` viewer. |
| **What's scored** | **Quality**: 3 LLM lenses — Story-craft/Tolkien, Mechanical, Angry-DM/5e-fidelity (each 0–5), + a deterministic behavioral gate. | **Playability**: persona *satisfaction* (0–10), critical bugs, console errors, image render rate, "did they complete the intro flow / give up". RRI = `gates_passed/11 × 10`. |
| **Model** | Sonnet (DM + player). | Sonnet (DM + player). |
| **Typical result** | story 4.0–4.3, mech 3.5–4.1, angry 2.5–4.2 | sat 4–9, RRI 2.7–6.0 |

A story-craft lens score of **4.2** and a GUI **RRI of 2.7** are **not the same axis**. One says "the
DM writes Baldur's-Gate-tier prose and the engine resolves rules cleanly." The other says "a blind
first-timer in the app waited 4 minutes for a reply and the launcher 404'd the portraits." **Both can
be true at once**, and the ledger shows they were — within ~24 hours of each other, on the same SHA
family, same model.

---

## 2. Score timeline (date → SHA → surface → model → story/mech/angry · sat/RRI)

Engine-duo quality (the "4.x" line) and GUI playability (the "2.x" line), interleaved by date. Full
detail + per-run caveats in `qa/scores_ledger.md`.

| Date | Run | SHA | Surface | Model | Story | Mech | AngryDM | Sat / RRI |
|---|---|---|---|---|---|---|---|---|
| 05-25 | newmain-rogue2 | ~85-commit | engine-duo | sonnet | 4.1 | 3.0 | 2.0 | — |
| 05-26 | ocwiz-claude | ~pre-v1.0.0 | engine-duo | **gpt-5.4** | 2.5* | 2.5* | 2.4* | — (RED-capped) |
| 05-26 | duo-typed1 | ~post-#140 | engine-duo | sonnet | 4.0 | 4.0 | 3.3 | — |
| 05-26 | duo-director1 | ~#72 | engine-duo | sonnet | 4.1 | 3.8 | 3.2 | — |
| 05-27 | sprint-story2 | ~pre-35128e2 | engine-duo | sonnet | 3.0 | 3.5 | 3.4 | — |
| 05-27 | sprint-story3 | 35128e2 | engine-duo | sonnet | 4.0 | 3.6 | 3.3 | — |
| 05-27 | duo-wired1 | ~#203 | engine-duo | sonnet | **4.2** | 3.7 | 2.9 | — |
| 05-27 | sprint-cs3 | ~#180 | engine-duo | sonnet | — | — | **4.2** | — |
| 05-29 | ow-duoA-040524 | ~pre-d2f65f1 | engine-duo | sonnet | 4.2 | 4.0 | 2.5 | — |
| 05-29 | ow-fixC-043416 | d2f65f1 | engine-duo | sonnet | **4.3** | 3.6 | 2.8 | — |
| 05-29 | ow-fixD-043417 | d2f65f1 | engine-duo | sonnet | 4.0 | **4.0** | 3.4 | — |
| 05-29 | ow-cs3-133001 | e5d651f | engine-duo | sonnet | — | — | **3.7** | — |
| 05-29 | ow-swA/swB/rv1 | e5d651f+ | engine-duo | sonnet | 2.5* | 2.5* | 2.5* | — (RED, new gates firing) |
| **05-30** | **ow-living1** | **~post-#305** | **engine-duo** | **sonnet** | **4.0** | **4.0** | **3.8** | **—** |
| **05-30** | **worldos-app-baseline** | **4267247** | **GUI-built-app** | **sonnet** | — | — | — | **~2.0** |
| 05-30 | nb1 | 71616cd | GUI-headless-proxy | sonnet | — | — | — | 4 (gave up: latency) |
| 05-31 | str-adversarial | c6480a3 | GUI-headless-proxy | sonnet | — | — | — | **8** (PASS) |
| 05-31 | str2-narrative | c6480a3 | GUI-headless-proxy | sonnet | — | — | — | **9** (gave up: latency) |
| 05-31 | str3-optimizer | c6480a3 | GUI-headless-proxy | sonnet | — | — | — | 5 (2 crit: char-sheet) |
| 05-31 | sweep3-newbie | c6480a3 | GUI-headless-proxy | sonnet | — | — | — | 7 (PASS) |
| **05-31** | **sweep1** | **c6480a3** | **GUI-built-app** | **sonnet** | — | — | — | **RRI ~6.0** |
| **05-31** | **gate-f5500ac-partial** | **f5500ac** | **GUI-built-app** | **sonnet** | 4.0 | **2.7** | — | **RRI 2.7\*** |
| 06-01 | handoff ×3 (4a0efe1→9545383) | — | GUI-built-app | scripted+codex | — | — | — | smoke PASS 100/100 (not a verdict) |

`*` = RED-capped / partial / harness-contaminated — **not a clean quality reading.**

**Read the two columns side by side on 05-30 and 05-31:** engine-duo `ow-living1` scores **story 4.0 /
mech 4.0** while the *same week, same model* GUI surfaces score **~2.0 (cannot-start) and RRI 2.7
(harness-contaminated)**. That is the entire "4.5 vs 2.x" gap, fully explained by surface + rubric.

---

## 3. WHEN did the "4+ → 2.x" shift happen? — It didn't. The 2.x numbers *appeared*, they didn't *fall*.

The "2.x" numbers are **not a later, lower re-measurement of the thing that scored 4.x**. They are the
**first-ever measurements of a brand-new surface**:

- The **GUI surface was never scored before late 05-30.** The AI-playtester harness (`#324`) landed
  `nb1` on **2026-05-30** (SHA `71616cd`) — the *first* GUI playtest in the entire history. The RRI
  scorer (`qa/release_readiness.py`, 11 gates) landed **2026-05-31** (`c6480a3`, #413). Before that,
  **every** score in the project was engine-duo. So there is no "4.x GUI that became 2.x GUI" — the GUI
  simply had no score until the moment it scored low.
- The engine-duo quality line **never dropped to 2.x except when a behavioral gate RED-capped it**
  (ocwiz-claude, ow-swA/swB/rv1) — and those caps are the gate *working as designed* (it forces
  lenses ≤2.5 when e.g. the PC isn't in the party, or a tool call is rejected). Each was diagnosed to a
  specific fix (6e7c3b0 alias shim; #162 player-in-party) and the *next* clean run returned to 4.x
  (ow-v103-reval 4.1/4.1 → ow-living1 4.0/4.0). **These are not regressions; they are gated failures
  with named causes, immediately fixed.**

So the "shift" the owner perceived is an **artifact of the surface coming online**, ~2026-05-30/31,
not a quality cliff.

---

## 4. The four candidate explanations, adjudicated

**(a) Real code regression? — DISPROVEN for story/mech.**
The engine-duo quality line is *flat-to-rising* across the whole window: story 4.0 (05-26) → 4.2/4.3
(05-27/29) → 4.0 (05-30 living-PC). Mech 3.5–4.1, trending up as fixes land (#180 multiattack, #209
repeat-save, #215 maneuver-die, d2f65f1 milestone-XP). Angry-DM rose 2.8 → **4.2** (sprint-cs3) as
combat-fidelity bugs closed. **No engine-duo metric regressed.** Every dip is annotated in SCORECARD
as either (i) a *coverage/sampling artifact* (social run → no combat → low Angry-DM) or (ii) a
*RED-capped gate failure with a named, fixed cause* — never an unexplained quality drop.

**(b) Surface change (engine-duo vs GUI)? — CONFIRMED as the dominant factor.**
This is the crux. The "2/10" originates verbatim from the LEXAR `worldos-app-baseline` note
(2026-05-30, SHA `4267247`): *"Newbie satisfaction (honest): via real .app = ~2/10 (**cannot
start**)… via the live loop = ~6/10."* The note's own root-cause is decisive:
> *"the bug is purely the GUI→bridge **wiring** (P1), NOT the engine/DM/viewer play loop."*
> *"Engine/dice/state = **solid**; the surface stayed playable."*
The `~2/10` is because the prominent "Resume → Play" CTA (`screen-launcher.jsx`) calls
`enterPlayable` which **never invokes `startProviderSession`** → the user lands in a *read-only*
director's view and can't act. That is a front-end wiring dead-end, on top of a **solid** engine. Same
build era as the engine-duo 4.0/4.0. **Surface, not regression.**

**(c) Model change (Opus→Sonnet)? — RULED OUT as the cause of the gap.**
**Both** the 4.x and 2.x numbers are **Sonnet** (DM + player). There is no Opus baseline in the ledger
to have regressed *from* — the only non-Sonnet run is `ocwiz-claude` (**gpt-5.4**, RED-capped, an
OpenClaw-path shakedown), and it scored *lower* (2.4–2.5), not higher. So the model is a **non-factor**
for the 4.x↔2.x gap. (If the owner recalls an "Opus 4.5 era," the ledger has no such row — flag for
confirmation, but the surviving evidence is uniformly Sonnet.)

**(d) Methodology change? — CONFIRMED as the second factor.**
The two scales use **different rubrics with different scales**: engine-duo = three 0–5 *quality*
lenses; GUI = 0–10 *satisfaction* + an 11-gate pass/fail RRI. The single scariest number, **RRI 2.7**
(`gate-f5500ac-partial`), is explicitly flagged in both `qa/SCORECARD.md` and `qa/RRI.json` as
**"PARTIAL / HARNESS-CONTAMINATED, NOT a release verdict"**: *only* the newbie persona wrote a
`score.json`; the other four personas produced nothing after port/backend harness failures, so 8 of
11 gates "failed" for **lack of evidence**, not for measured defects. Its `mech=2.7` is **one
persona's** char-sheet-depth ding; its `behavioral=RED` and `image_render=0%` are harness / clean-
checkout artifacts (the gitignored `_private` BG3 art isn't present in a clean tree, so the UI 404s the
portraits). **Citing 2.7 as "the GUI quality" is a methodology error the repo already warns against.**

---

## 5. What the GUI score is *actually* telling us (so it isn't dismissed)

The GUI surface genuinely needs work — but the ledger pinpoints **what kind**, and it is **not** story
or mechanics:

1. **DM latency** is the #1 play-loop killer. Both give-ups (`nb1`, `str2-narrative`) are latency, not
   quality. `str2-narrative` self-reported **satisfaction 9** ("prose quality excellent") yet quit
   because *"each turn takes 3–5+ minutes… the input has been locked for over 5 minutes."* The
   underlying story is great; the *waiting* is the wall. (Latency-mitigation PRs followed: streaming
   narration #393/#401, non-blocking image-gen #399, fewer round-trips #395.)
2. **Image 404s** (portraits / location art): 0–19 per run, because the licensed `_private` art
   catalog is gitignored and absent in the surfaces under test. A packaging/fallback gap, not a
   render-engine regression.
3. **GUI→bridge wiring** (the `worldos-app-baseline` read-only dead-end) and **char-sheet depth** (the
   optimizer's 2 criticals: spell DC / class features, fixed in #416).

These are real P0/P1 GUI issues — and the empirical-play strategy (STRATEGY.md Option B) is the right
way to burn them down. But they are **playability/wiring/asset/latency** issues layered on top of a
**healthy 4.x engine**, which is exactly why the two numbers diverge.

---

## 6. Confidence & open flags (be honest about the limits)

- **High confidence (≥95%):** surface taxonomy of every row; that engine-duo quality did not regress;
  that the model is Sonnet on both scales; that RRI 2.7 is harness-contaminated (stated in-repo); that
  the "2/10" is a wiring dead-end (stated in the LEXAR note with code line refs).
- **Flagged uncertain (`~` SHA / `[UNCERTAIN]` / `[SUSPECT]` in 33 of 57 rows):**
  - Several engine-duo SHAs are *inferred* from the change-under-test, not recorded verbatim
    (`~post-#203`, `~#180`, etc.). Direction and ordering are right; the exact commit may be ±1–2.
  - **`[SUSPECT-#305]`:** all **Dal Lightspark** rows (ow-duo*/ow-fix*/ow-v103-reval) ran a
    **canonically dead** PC; SCORECARD voids them as a clean canon-PC baseline. Use `ow-living1`
    (Latham, living PC, 4.0/4.0/3.8) as the valid recent engine baseline. Their *plumbing* validations
    still hold.
  - `str-narrative`, `str-optimizer`, `str2-optimizer`, `sweep2-newbie`: `run.json` present but **no
    `score.json`** (incomplete harness attempts) — recorded as rows-without-scores, superseded by sibling
    runs.
  - **No Opus rows exist** in any surviving artifact. If an "Opus 4.5" baseline is being remembered, it
    predates the mined evidence — **flag for the owner to confirm**; on the data, the model is not the
    confound.

## 7. Is there a suspect commit range for a *real* regression?

**For story/mechanics: no — there is no real engine regression to localize.** The engine-duo line is
flat-to-rising; every dip is gate-capped-with-cause or a coverage artifact.

**For GUI playability:** there is no "regression range" because there is no earlier high-GUI baseline
— the GUI was *first scored* at `71616cd` (05-30, harness landing) / `c6480a3` (05-31, RRI landing) and
scored low *from the start*. The relevant "suspect" code is therefore the **pre-existing GUI→bridge
wiring** (`screen-launcher.jsx` `enterPlayable`/`startPlay`, flagged in `worldos-app-baseline`) and
**DM-turn latency** (addressed across #393/#395/#399/#401), not a commit that *broke* a
previously-good GUI.

---

### Bottom line for the owner

You do not have an engine that regressed from 4.5 to 2.x. You have a **4.0–4.3 story / 3.5–4.2
mechanical engine (Sonnet)** that **never regressed**, and a **young GUI surface** whose first scores
(late 05-30/05-31) came in low for **latency + missing art + a launcher wiring dead-end** — measured
on a **different surface with a different rubric**, and whose single worst number (RRI 2.7) is a
**contaminated harness run, not a release verdict**. Fix the GUI's wiring/latency/art; the storytelling
core underneath is already at target.
