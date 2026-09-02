# WorldOS design memo — 2026-09-02 (Fable, orchestrator; owner-invited)

The owner asked for feedback on the north-star docs "with latest research or findings". This memo makes six proposals. Each names the
doc it changes, what today's measurement says, the smallest concrete change, and — where it exists — a verified external source
(every citation was URL-fetched by a skeptic agent on 2026-09-02; the verified sweep — claim, URL and status per source —
is committed at [`docs/decisions/research/2026-09-02-design-memo-sources.md`](research/2026-09-02-design-memo-sources.md)).

## 0. Premise check — what two days of measurement say
- The engine + gate half is strong: every probe click behaved, every door crossed with the right camera pin, walk gates are exhaustive
  and reproducible, certs are sha-pinned, the arc harness measures completion honestly.
- The FELT half is where the demo fails, and none of it was visible to the instruments until an agent played: actors 2-3 stops darker
  than the plates (#1738), the silhouette pass firing through fat legacy proxies (#1736), a DM model swap silently changing the game
  (G2 0/4 at 15 and 20 beats), per-object misregistration the old gate under-reported (#1735).
- Every doc defines DONE for a run, a room, a rung or a session — never for a world, never for a player.
- Independent evidence says this is the norm, not a WorldOS quirk: on GBQA (30 games, 124 human-verified bugs) the best model finds
  only 48.39 % per pass [arxiv.org/abs/2604.02648]; agents that self-verify their own game runs end 35/35 experiments with self-scores
  ≥ 0.70 while 15/35 score below random play [arxiv.org/html/2607.24300v1]; adding a play-test loop to game generation lifts rubric pass
  rates from ~30 % to 66.8 % [arxiv.org/html/2605.28258v1]. The loop is the product; the author must never be the judge.

## 1. Agent G4 — the agent-as-playtester loop becomes §9's G4 (PRODUCT-ROADMAP §9, VISION resume protocol, ACTIVE-GOAL)
- **Definition.** G4 = an AGENT playthrough of the shipped build with zero P1 user-truth defects in two consecutive builds, then a
  BLIND agent persona (never the author) completing the arc in the rendered surface. The owner's playthrough is the escalation at the
  80/20 wall, not the gate (owner ruling 2026-09-02).
- **Two passes per build.** Pass 1 = walkaround with no DM (navigation/doors · collision-vs-paint · actors/occlusion · legibility ·
  viewer); pass 2 = the story against the live DM through the DM-only loop (`qa/agent_play.sh`, the same loop the installed instance's
  DM agent runs — so a human at the viewer and an agent use one code path).
- **Multi-lens, multi-pass, reproduce-before-file.** One pass finds about half the bugs (GBQA); lenses are blind to each other;
  every finding is reproduced by a different agent on a fresh sandbox before it is filed — VLM judges throw false positives on
  near-contact geometry and partial occlusion [arxiv.org/abs/2607.25921], which is exactly the silhouette/proxy class we hit.
  The production pattern is the same: TITAN's LLM QA agents ship with oracles + reflective self-correction and found four unknown bugs
  in commercial MMORPG pipelines [arxiv.org/abs/2509.22170]; black-box bot QA files reports with screenshot/video evidence and a
  severity score [modl.ai].
- **Defect contract:** room · cells · what a player would say · the frame the reporter looked at · the `/debug` line · repro · layer
  (engine grid / seed / sidecar / client render / plate / viewer) · P1-P3.
- **Score row** (`qa/SCORECARD.md`, surface `agent_g4`): P1/P2 counts, route completion, legibility median per room, actor-luminance
  floor, frames per room. A build advances only at P1 = 0.
- **The 80/20 wall, defined:** a defect whose fix needs a new subsystem (> 1 day) or a taste fork with two good answers; the escalation
  carries the frames, what was tried, and the options. Human testers anchored by AI errors decide worse
  [arxiv.org/abs/2501.11782] — so the owner gets verified defects, never raw lens output.

## 2. DONE for a world — "World Readiness" (new roadmap section; the finish line ACTIVE-GOAL points at)
A world is DONE when, on ONE installed build with recorded identity: every room in its graph is walk-certified (exhaustive) and
per-object-aligned at the calibrated floor · every door crosses both ways with the room's camera pin · actors read as lit figures
(luminance floor) with no silhouette in the open · the text arc completes ≥ 0.67 at the recorded ruler with pinned model ids · the walked
arc covers the FULL route incl. the return-for-reward · Agent G4 = zero P1 in two consecutive builds · a blind persona completes the
arc · the owner's wall, if hit, was resolved (an option chosen and its fix landed). First world = "The Crypt Below"
(5 rooms: camp_clearing, tavern_snug, shop, crypt, throne_hall); second = the town (6-10 rooms, C1).

## 3. A rung above "completes the arc" — DESIRE instruments and the model-swap discipline (new §4e)
- **Desire.** Today's top rung is completion + behavioral GREEN + panel parity. Nothing measures "want to play". Cheapest first:
  (a) voluntary continuation — blind personas told the objective is optional; measure beats played past it and whether they explore.
  Desire runs disable the completion short-circuit: today `qa/run_adventure.sh` ends the beat loop as soon as the quest leaves
  `active`, so a `--no-short-circuit` continuation flag on the runner is part of the `desire_eval` work item — without it there are no
  post-objective beats to measure;
  (b) would-play-again / would-share — two fixed questions, N ≥ 5 personas, two-anchored against a scripted control session;
  (c) the owner's felt score as the rare calibration anchor. Ship as `qa/desire_eval.py` on the scores_db discipline (predeclared
  bars, N, control). RPGBench's split — machine-checkable metrics vs LLM-as-judge subjective metrics — is our design already
  [arxiv.org/abs/2502.00595]; the desire rung is the missing judge half.
- **Model swaps are ruler changes.** The `opus` alias moving from opus-4-8 to Opus 5 turned a 3/3 into a 0/4 with a byte-identical
  harness. Rule: pinned model ids on every measured row (landed, #1727), and a version change counts as an improvement only when its
  delta exceeds the within-model SPREAD of the SAME metric — the standard error over N >= 3 repeated runs per model, in score units,
  not the raw variance (which is in squared score units and rescales with the ruler) — the Reliable Change Index adapted to LLM
  evaluation [arxiv.org/pdf/2604.27405]. Record N with the row. DM drift is well documented: fact-ledger auditors see 40-68 % fact conflicts over long sessions
  [arxiv.org/html/2608.08160]; adversarial TRPG benches measure ~10 % false-pass rates that vary by setting [arxiv.org/html/2607.02802v1].
  WorldOS's engine IS the fact ledger; the new arc-mode FAIL rows (reroll / add_location / non-seeded spawn / false end_combat) are
  its auditor. Keep the referee in code, not prose (our own spec-amendment cycles plateau after ~3 passes).

## 4. Track C — the town has an architecture (docs/roadmap/TOWN-LAYOUT-DESIGN.md) and needs a charter + one research binding
- Charter: entry gate = agent G4 zero-P1 on the crypt demo; ordered issues #1742-#1745 → C1 v1 → C2 outdoor mode → C6 seed → C7 walked
  arc over the town; exit = World Readiness for the town. Rename #1640's W1-W4 to L1-L4 (the roadmap owns W1-W5).
- **Binding from the research:** all three C1 designs grow TREES (dead-end ratio 34.8 % measured, cycle rank ≈ 1). Shipped procedural
  design solved this deliberately — Dormans' cyclic generation (two arcs between start and goal; lock-then-detour-for-key; nested cycles)
  in Unexplored [gamedeveloper.com … cyclic-dungeon-generation], built on graph rewriting [boristhebrave.com/2021/04/02/graph-rewriting].
  The C1 grammar gets cyclic productions and a dead-end-ratio gate. Caves of Qud's hybrid — static hand-authored towns wrapped in
  procedural worlds [gamedeveloper.com … caves-of-qud] — is the licence to hand-author the hub square and generate the districts.

## 5. "Anyone can make a CRPG" — the authoring surface does not exist in the docs (VISION Act III)
The author is a person with an agent. The authoring TOOL is the pipeline we already run, wrapped: `worldos new-world "<prompt>"` →
schematic → district plan → kit-chain rooms → seed → gates → playable. The gates ARE the editor's guardrails (a room that fails the
walk gate never reaches the author's player); the pack format (Act III #644) is the unit of sharing. Multi-agent "studios" that skip
the play loop ship scaffolding, not games (the 48-agent Stray Spark analysis; GameCraft-Bench's best agent at 41 % on complete Godot
games [arxiv.org/abs/2606.17861]) — the moat is the gate ladder, so VISION should say every gate is judged by "would a non-agent author
survive this?".

## 6. Staleness to fix in one docs PR (measured today)
VISION.md:307 pins paint-first (the kit chain is the room pipeline); VISION's resume protocol points at #1581 (charter #1702 is the
head); VISION Tier-2 actor floors vs measured under-lit actors; the 0.99 registration gate is absent from VISION's scorecard;
PRODUCT-ROADMAP §9 G4 says "owner plays", §4b/§7/§9.1/§9.2 assume the GEX44 box (the Owner Gate Register row "GPU-box renewal" gates
a dead machine); OPERATIONS' box claim-queue + support-VM sections; CLAUDE.md carries no product charter (only the GitNexus block) —
a 15-line "what WorldOS is + the five rules" header would let a Claude agent bootstrap without AGENTS.md.

## 7. What I want (the owner asked for desires)
- Keep the agent-plays-first rule; its first pass found three demo-blocking defects the instruments had passed.
- Spend Track B's remaining budget on ONE more editor arm (Gemini 4K + refs + exact-count/identity clause, best-of-N under the
  calibrated per-object gate; ~100 CU) — the only lever that moved structure (22/23), and the tavern needs the re-edit anyway (#1735).
- Treat the DM as an instrument: every measured run pins the model id; a model change gets a control run before it counts.
- The Codex account refill (quota out until 2026-09-08) is the one thing the agent cannot do.

_Companion artifacts: DIGEST.md (charter-doc digest), DM-DEVIATIONS.md (G2 root cause), TOWN-LAYOUT-DESIGN.md (C1), the agent-g4
frames. Sections 1-3 become roadmap text on approval; section 6 is a mechanical docs PR; section 4's charter opens after G4._
