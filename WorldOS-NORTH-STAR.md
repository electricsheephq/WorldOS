# WorldOS — NORTH STAR (long-game ceiling, not release-state authority)

> **This is the optimization target.** The RUNBOOK tells you *how to work* (invariants, dev/QA
> loops, the file map). This doc tells you *what "great" means* — the thing the whole loop serves.
> When a decision is ambiguous, when the score and your gut disagree, when you're about to add a
> feature: come back here. The North Star is **more than a score.** The score is a proxy for it.
>
> Read order on resume during takeover: `WorldOS-OPERATING-GOAL.md` → `WorldOS-GUI-RUNBOOK.md` →
> `WorldOS-RUNBOOK.md` → `qa/SCORECARD.md` → this file. The North Star tells you what "great"
> means; it does not decide whether the current build is releasable.
>
> Voice: confident, opinionated, builder's. Last north-star content update 2026-05-28;
> takeover freshness note added 2026-05-31.
>
> **Historical state as of v1.0.1:** engine 1385/1385 ✓, viewer 90/90 ✓, all 14 OpenWorlds screens render polished + data-bound + honest, Phase-4 action lanes wired (Merchant + Forge + Create), the seven BG3 origin heroes carry companion dossiers, build script prefers stable Developer ID signing. This is historical context; for the current SHA, gate source, and release state, use the Operating Goal and Scorecard.

---

## PART 1 — THE TRUE GOAL (beyond the score)

WorldOS has **two deliverables, and they are inseparable:**

**(A) The felt prestige-CRPG session.** A player should finish a session feeling what they feel
after a great night of Baldur's Gate 3 — *that mattered; that was mine; I want to know what happens
next.* Concretely, the experience is: you **drop into a persistent world** that already has a
history; you **stumble into quests** that then **evolve** as you act on them; you **build (or burn)
relationships with factions** and earn your way up their ranks; you **travel with companions** who
have their own sheets, voices, and agendas — companions who can **turn on you** when your choices
betray what they stand for; and the world **moves on its own** while you're elsewhere. Every
playthrough **diverges**, and every divergence is **canon-grounded** — never random noise, always
"this world, plausibly, this time."

**(B) The engine that makes new universes from a seed — GENERATIVITY.** The deeper goal is not one
great world; it's a **universe-system**. Reverse-engineer how BG3 / Skyrim / Kingmaker structure
story into a **seed + engine** that can spin up a *new*, internally-coherent, lore-grounded world.
The fitness bar is brutal and clarifying: **a second world should be near-free once the first is
perfected.** If adding a world means re-building systems, the engine isn't done. If it means writing
a seed (regions, factions, cast, history, standing threads, variant matrices, endings) and the
living-story machine just *runs*, the engine is done. **BG is the proving ground; the engine is the
product.**

The two are inseparable because **B is what makes A repeatable and ownable.** Anyone can hand-author
one good adventure. The prize is a machine that generates the *felt* prestige session on demand, in
a fresh universe, every time — canon-grounded, divergent, alive.

> **The one-line North Star:** *a deterministic engine that generates Baldur's-Gate-caliber STORY on
> demand inside persistent, canon-grounded worlds you can spin up from a seed — where threads evolve,
> companions are real and can turn, the world moves on its own, and every playthrough diverges.*

What this is explicitly **NOT:** a rules calculator with flavor text; a railroaded module; a
roguelike that randomizes for novelty's sake; a chatbot that hallucinates dice. The mechanics are
table stakes and must be *flawless and invisible*. The mechanics are the floor. **The story is the
ceiling, and the ceiling is the point.**

---

## PART 2 — FIRST-PRINCIPLES DESIGN GUIDELINES (what makes it great)

These are distilled from the build — every one was paid for in a real defect, a real decision doc,
or a real owner steer. Each is a **principle + why it matters.** They are load-bearing; violating one
doesn't just lose points, it breaks the thing.

### THE STORY PRINCIPLES (what the world must *feel* like)

**P1 — The engine is the SOLE WRITER, and it is DETERMINISTIC. The LLM voices; the engine
adjudicates.**
The engine owns every fact of campaign truth — dice, HP, position, conditions, XP, reputation,
flags, the day. It writes them atomically to disk and nothing else mutates them. The LLM's job is to
**narrate and voice** — never to assert an outcome, never to roll in its head, never to decide what
*happened*. *Why it matters:* this is the line between a prestige CRPG and a hallucinating chatbot. A
player can trust the world because the world is real bookkeeping, not improv that contradicts itself
next scene. **Probability proposes; the DM disposes** — the engine rolls the wander/betrayal/variant
dice and *tells* the DM the result; the DM stages and narrates it, but cannot overrule it. Determinism
is not a constraint on the magic — *it is the magic.* It's what lets the world be replayable, shareable,
and compaction-proof.

**P2 — Every thread EVOLVES. Nothing is one-and-done (the rule of three).**
A resolved quest is not a closed quest — it **echoes**: it schedules a follow-on, a consequence, a
return. A favor comes due. A spared villain resurfaces. *Why it matters:* "nothing lingers" is the
single most recurring ding on the story lens, and it's the difference between a *world* and a
*questboard*. BG3 feels alive because Act 1 choices detonate in Act 3. The engine enforces this where
a gauge exists (`Quest.evolves_to` + `callback_in_days` → a scheduled `Consequence`), and advises it
everywhere else (the Director's `thread_no_payoff` / `setup_without_payoff` debts). **If a setup
never pays off, that is a bug — measured as such.**

**P3 — Characters STAY THEMSELVES. Surprises are EARNED and lore-grounded, never out-of-character.**
A companion who betrays you does it because *your choices* pushed a gauge past a telegraphed
threshold along *their* authored agenda — Minsc swings *at* evil, he never bargains with it. A noble
who falls, a friend who turns: it must be foreshadowed (warning bands telegraph), in-character, and
recallable as canon afterward. *Why it matters:* a cheap, unmotivated twist is worse than no twist —
it shatters the trust that makes the world feel real. The engine encodes this as the **breaking-point
guard checked FIRST** (a betrayal roll can *never* fire above the attitude threshold, no matter what
flags are set) and **decision-gated flips** that only raise the weight when the player has actually
earned the turn. Surprise is a *reward for the player's agency*, not a dice-fart.

**P4 — The world MOVES under the scene. It is proactive and off-screen.**
Standing threads advance whether or not the player is watching (`worldsim.tick`). Factions scheme,
clocks tick, consequences mature on their trigger-day. The DM doesn't wait to be prompted — the
Campaign Director tells it what the campaign *owes* this beat (an untracked hook, a stalled quest, an
overdue promise, an NPC who was introduced but never spoke). *Why it matters:* a world that only
exists when looked at is a set, not a world. Proactivity — new faces, advancing schemes, debts coming
due — is what makes a player feel small inside something larger. **A static world is a failure mode,
caught by the world-progression gate** (clock advanced + ≥2 locations + new faces).

**P5 — AGENCY HAS CONSEQUENCE. Choices ripple deterministically.**
A choice at a `ParleyOption` carries a deterministic `Outcome` that the engine applies — a flag set,
reputation shifted, a standing changed, a consequence scheduled. The ripple is *real bookkeeping*,
not a remembered promise the DM might forget. *Why it matters:* agency that doesn't change the state
of the world is theater. The player must be able to point at a later beat and say "that happened
*because of what I did three sessions ago.*" The engine makes the ripple durable and inevitable; the
DM narrates how it lands.

**P6 — GENERATIVITY: a new universe ≈ a new seed.**
Every system must work from **content the engine reads, not code the engine hard-codes.** Worlds are
data (regions, factions, cast, history, standing threads, `quest_variants`, endings). Variance is
authored as **weighted matrices** with documented rarity bands (common ≈55% / uncommon ≈30% / rare
≈10% / very-rare ≈5%), resolved once per seed against `random.Random(c.id)` so two playthroughs of the
same world **diverge reproducibly** while a fresh world spins up coherent and new. *Why it matters:*
this is deliverable B. The test of every feature is: *does this make the next universe cheaper, or
does it bolt one more thing onto this one world?* Build the system, fill it with canon.

### THE ENGINEERING INVARIANTS (what protects all of the above)

These are the disciplines that keep P1–P6 true under change. They are non-negotiable; the RUNBOOK
states them as law, and they exist because the *story* goals demand them.

**E1 — Additive-by-default. Empty == today. Old saves round-trip.**
Every new field defaults to "behaves like today when unset." Models are strict (`extra="forbid"`);
the tolerant load drops only unknown *top-level* keys so a future schema change can't brick a
player's months-old campaign. Each feature is independently removable; blast radius stays low. *Why:*
a persistent, multi-session, compaction-spanning world is worthless if an update eats the save. The
world's permanence is a feature; additivity is how you keep it.

**E2 — Gates and triggers read ONLY engine-mutated values — NEVER fiction.**
A gate may key off `flags`, `reputation`, `attitude_value`, `day`, `standing` — values the engine
itself set. It may **never** try to judge prose. *Why:* the engine cannot reliably parse near-constant
fiction, and the moment it tries, it becomes non-deterministic and breeds edge-case bugs. This is the
hard line from `questgen.py`, and it's *why* quest CONTENT stays DM-advisory while only gauge-backed
things get engine teeth. It's the mechanical expression of P1.

**E3 — REUSE, don't rebuild. The engine is usually ~80–90% there.**
Before building a subsystem, find the existing primitive. The Quest-Arc engine reused the companion
stage-machine; #143 variants reused the shipped `_resolve_quest_variants` resolver; faction arcs will
generalize `CompanionQuestArc` rather than invent a parallel one. *Why:* reuse keeps the surface small,
the tests meaningful, and the system coherent. A second mechanism that does almost-the-same-thing is a
bug farm. Map the running engine before you design (the "read the code as ground truth" step that
every decision doc opens with).

**E4 — VALIDATE before fixing. The scorer mis-attributes root cause.**
When a lens flags a defect, reproduce it against the engine *first*. The "STR-18 → +5 attack" alarm
was false (the engine computes +6 correctly; the real issue was DM adherence). *Why:* "fixing" a
phantom bug at best wastes a cycle and at worst weakens a correct guardrail. The LLM scorer is a
*detector*, not a *diagnostician.* Smoke the engine, *then* act.

**E5 — Surfacing info ISN'T enough. Enforce it in the engine, or fold it into a trigger the DM
already hits.**
Adding a tool the DM *could* call does not mean it *will* (this is the "reach-for" lesson, paid for
repeatedly: the Director, `encounter_outlook`, `add_quest`, monster Multiattack). Two reliable fixes:
**(a) enforce it in the engine** (the Multiattack economy fix, the turn-skip block — the engine
*refuses* the wrong move), or **(b) fold the value into a trigger the DM hits every turn** (per-turn
`turn_brief` on `next_turn`, not just at `start_combat`; the Director consulted at *every* beat start
→ `add_quest` went 0→3). *Why:* a dark surface scores nothing and ships nothing. A feature isn't done
when it's built — it's done when it's *unmissable in play.*

> **The through-line:** the story principles (P1–P6) are *what great is*; the engineering invariants
> (E1–E5) are *how you keep it true while moving fast.* A change that improves a lens score by
> violating an invariant is a regression, not progress.

---

## PART 3 — THE FITNESS FUNCTION AS PROXY

The fitness function is **how we measure progress toward the North Star without a human grading every
run.** It is a **proxy** — genuinely useful, genuinely limited. Treat it as an instrument, not the
destination. Full spec: `qa/SCORING.md`; running ledger: `qa/SCORECARD.md`.

### The shape: 1 hard gate + 3 lenses

| Component | What it measures | The bar |
|---|---|---|
| **Behavioral gate** (`assert_behavioral.py`) | Deterministic pass/fail on structural integrity: turns taken, world progressed (clock + ≥2 locations), player didn't narrate the world (facade over-write), companions spoke, every player move was resolved, combat closed cleanly, no dangling conditions. | **GREEN** (a RED run caps all three lenses to ≤2.5 / INVALID) |
| **Story-craft — "The Loremaster's Eye" (Tolkien lens)** | The felt story on a two-sided play log: scene-craft, grandeur, character depth, prose/atmosphere, dramatic momentum, thematic resonance, memorability. Stingy, BG3-calibrated, **act-relative** (an 8-beat slice is judged as ~Act 1). | **≥ 4.3** |
| **Mechanical** | The DM tool-stream vs correctness: tool-sourced (not hallucinated), rules correctness, state integrity, agency. Hallucinated mechanics are the worst defect. | **≥ 4.5** |
| **5e-fidelity — "The Angry DM"** | Adversarial SRD 5.2.1 checklist on the DM tool-stream: d20 tests, ~15 action types, all 14 conditions, action economy, combat resolution. | (rolls into mech; **0 critical/high** defects) |

**The exit bar:** **story ≥ 4.3, mechanical ≥ 4.5, gate GREEN, 0 critical/high adversarial defects.**

*Honest trajectory note (don't read this doc as describing a solved state):* mechanical ≥ 4.5 is the **aspirational** target — emergent-play mech currently sits ~3.9 and Angry-DM ~3.2, **coverage-gated** (one session can't exercise the whole 5e surface, and the Angry-DM lens is adversarial). The climb path is **coverage** (richer seeds/scenarios) + **content** + engine-fidelity fixes (Multiattack, turn-order, on-hit riders) — *not* prose tuning. A single run's lens number is a floor-check, never a verdict on the ceiling.

### Why the gate exists first
LLM scorers grade *prose* and can't be trusted to flip RED on a structurally-broken run — a dead
scene can read as "atmospheric." The deterministic gate is the **honest floor**: it makes
structural failure *unforgeable*. The lenses grade quality *above* that floor. **GREEN is necessary,
not sufficient.**

### The LIMITS — read these every time you read a score

1. **A single run is COVERAGE-CAPPED.** One playthrough samples a sliver of the surface. A 3-round
   combat sprint structurally tops out around **angry-dm ~3** — not because the engine is broken, but
   because one vanilla fight can't exercise saves + conditions + subclasses + rests + leveling. **The
   score climbs via BROADER play and a richer seed, not by re-running the same short fight.** Don't
   chase a number on a narrow scenario.

2. **Low combat scores on emergent duos are usually SAMPLING, not defects.** Both the AI player and
   the DM drift to roleplay; combat rarely gets formally run (a combat-*seeking* battlemage persona
   once made *zero* attacks because the scene gave no hostile target). The wandering-encounter system
   and combat-seeking personas exist to *force* the coverage. A low angry-dm on a social run is a
   sampling artifact — confirm against the engine (E4) before believing it's a regression.

3. **gpt-5.4 grades ~1.5 points HARSHER than Claude** on the identical transcript. **Claude
   (`score.sh`) is the PRIMARY baseline** (continuity with the historical 4.x numbers); gpt-5.4
   (`score_openclaw.sh`) is a *stricter cross-check / "angry grader"*, not the headline. Never compare
   a gpt-5.4 number against a Claude target — that's a unit mismatch.

4. **The score is a PROXY, not the goal.** "5/5" is an **asymptote** and a **stand-in for the felt
   bar** — it points the right way, it can't fully contain the target. A run can score 4.3 and still
   be forgettable; a run can score 4.0 and contain one unforgettable beat. **Optimize the felt
   experience (Part 1) using the score as your instrument — never optimize the instrument.** The
   day the loop starts gaming the lens (narrow scenarios tuned to the rubric) is the day it stops
   serving the North Star.

> **The combat-sprint exists to FIND BUGS, not to maximize a number.** It isolated Multiattack,
> the Round-1 turn-skip, and the Guiding-Bolt-on-cast bug — all *real engine defects* the surfacing
> work had masked. Use the lenses to *find what's broken* (then fix the engine, E4/E5), and use the
> *trajectory* (the SCORECARD over time) to know if you're winning. A single number is noise; the
> trend is signal.

---

## PART 4 — THE "BEYOND THE SCORE" QUALITATIVE BARS

These are the questions the score **can't fully capture** — the felt-experience bars that *are* the
North Star. Make them **checkable**: ask them of every meaningful run, and if the answer is "no,"
that's a roadmap item even if the lens went GREEN. This is the human-judgment layer that sits *above*
the fitness function.

**The felt-session bars (deliverable A):**

- [ ] **The "years later" test — would a player recount this beat to a friend?** Not "was it
  competent" — *was it memorable?* If nothing in the session is worth retelling, the ceiling wasn't
  reached, whatever the story lens said. *(Check: name the one beat you'd retell. If you can't, the
  run was forgettable.)*

- [ ] **Does every setup get a payoff (the rule-of-three, felt)?** Walk the session: every hook
  raised, every NPC promise, every spared villain — did it echo, evolve, or come due? A dangling
  setup is a broken promise to the player. *(Check: list the setups; confirm each has an echo or a
  scheduled return. Zero orphans.)*

- [ ] **Are the stakes EARNED, not handed over?** No "kill a god on day 1" (barring a deliberate
  Clawdan easter-egg): a high-stakes ask should be GATED behind built trust — a faction questline you
  rose through (rank/standing tiers), a companion bond you deepened, a thread you've been pulling.
  Power and world-changing finales are the *payoff* of an arc, not its opening move. *(Check: did the
  session's biggest stake sit on earned progression — faction rank, a bond, a multi-stage arc — or
  arrive ungrounded?)*

- [ ] **Does a companion feel REAL — and canon?** Two failure modes, both fatal: a companion who's a
  silent stat-block ("log, not a scene"), or a companion who's vivid but *off-character* for who they
  canonically are. The bar is **both**: present, voiced, with their own agenda — *and* unmistakably
  themselves. *(Check: would a BG3 fan recognize this companion's voice and values?)*

- [ ] **Is a betrayal / dark turn EARNED and FORESHADOWED — never cheap?** A turn must be (1) caused
  by the player's choices, (2) telegraphed by warning bands before it fires, (3) in-character for the
  turner, and (4) recallable as canon afterward. A twist that fails any of these is *worse* than no
  twist. *(Check: trace the turn back — what choice caused it, what warning preceded it, why is it
  in-character?)*

- [ ] **Does the world feel ALIVE and INDIFFERENT — does it move without the player?** New faces
  appeared; off-screen schemes advanced; a clock the player wasn't watching still ticked. The world
  should feel like it has somewhere to be. *(Check: name something that changed off-screen this
  session. If nothing did, the world was a backdrop.)*

- [ ] **Did the player's AGENCY visibly change the world's state?** Can you point at a later beat and
  trace it to an earlier choice — a flag, a reputation shift, a door that opened or closed *because of
  what they did*? *(Check: draw one cause→effect arrow across beats. If you can't, agency was
  theater.)*

**The generativity bars (deliverable B):**

- [ ] **Can a FRESH seed generate a coherent NEW universe?** Spin up a world from seed alone — do the
  regions, factions, cast, history, and standing threads cohere into a place that feels *authored*,
  not assembled? A seed that produces incoherent soup means the engine isn't done. *(Check: read a
  fresh-seed world cold — does it read as a real place?)*

- [ ] **Do two playthroughs of the SAME world diverge MEANINGFULLY — and stay canon?** Two seeds of
  BG should produce different `quest_outcomes`, different NPC fates, different recallable
  `[Outcome]/[Hook]` lore — *and* every divergence must be lore-plausible, never random noise. The bar
  is **divergent AND grounded.** *(Check: diff two seeds — are the differences felt, and is each one
  "this world, plausibly"?)*

- [ ] **Is the next world NEAR-FREE?** The ultimate generativity check: could a second world ship by
  *authoring a seed*, with no new engine subsystem? Every time you're tempted to hard-code, ask this.
  *(Check: would this feature need to be rebuilt for world #2, or does it read from content?)*
  **⚠ This is the boldest and least-PROVEN claim in this doc — make it falsifiable.** One world (BG) is
  deeply built; "near-free" is still *vision*, not demonstrated fact. Required milestone before
  declaring the engine "done": spin up a THIN second seed (a small non-BG `world.json` + a few content
  blocks) and confirm it runs a coherent session with **ZERO engine changes**. Treat that second-seed
  spike as a falsifiable gate on deliverable B, not an aspiration.

> **How to use this list:** run it as a post-run reflection on the runs that matter (not every
> sprint). A GREEN run that fails three of these is a *better* signal about the roadmap than the lens
> deltas. **The score tells you the floor held; this list tells you whether you hit the ceiling.** The
> ceiling is the North Star.

---

*The mechanics are the floor. The felt, generative, canon-grounded prestige session is the ceiling.
Build toward the ceiling; use the floor to make sure you never fall.*
