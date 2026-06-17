# Cellar Rats

> *A WorldOS starter adventure. Levels 1–2 · one ~1-hour session · 3–4 players + companion.*

**The Sodden Crown is the only inn for a day's walk, and something has moved into its flooded cellar. The owner blames rats. The scratching in the walls is far too big for rats.**

This is a grounded, low-level dungeon crawl with a soft heart: a band of goblins has tunneled up into a tavern cellar — but they aren't raiders. They're refugees, fleeing something far worse still down in the drowned dark below. The "infestation" the party is hired to exterminate is a frightened family. The real monster is the one nobody ever quite sees.

It runs in about an hour and hits every beat a WorldOS session should: a social hook, an exploration crawl with a trap and a choice, a memorable turncoat NPC whose attitude can flip, and two combats built from the bundled Goblin and Wolf stat blocks.

---

## At a glance

| | |
|---|---|
| **Premise** | A tavern cellar "overrun by rats" is really a goblin refugee camp hiding from a thing in the deep water. |
| **Hook** | A storm strands the party at the Sodden Crown; the innkeeper hires them to clear the cellar before dawn. |
| **The twist** | The goblins are fleeing, not raiding. Spare them and the real enemy — the unseen *Long Thing* — becomes the threat to *seal away*, not slay. |
| **Length** | ~60 minutes. |
| **Levels** | 1–2 (delivers ~300 XP — enough to carry a fresh L1 party toward L2). |
| **Bundled monsters used** | Wolf ×2, Goblin ×4. (Bandit on standby for NPCs.) |
| **Definition-of-Done** | Exploration ✓ (Scene 2: trap + navigation) · Social ✓ (Scene 1 & 4: attitude shifts) · Combat ✓✓ (Scenes 3 & 5). |

### The cast (and their voices)

| NPC | Voice id | One line |
|---|---|---|
| **Brakka Holt**, innkeeper | `npc-elder` | Gruff ex-caravan-guard who won't admit she's scared. The quest-giver. |
| **Dorn**, stable-hand | `npc-male-1` | Jumpy teenager with a bitten arm and a story nobody believes. |
| **Quill**, goblin scout | `npc-rogue` | Starving, defiant, terrified — the prisoner who carries the twist. |
| **Grett One-Eye**, crew-leader | `npc-rogue` | A frightened survivor making a desperate last stand. Redeemable. |

The **DM** narrates with `narrator-dm`. The **companion** uses `companion-default` and should be loud throughout — see *How the companion engages* at the end.

### The map

```
            ┌──────────────────┐
            │  TAPROOM         │  ← safe zone (Brakka, Dorn). Start & end here.
            │  (Sodden Crown)  │
            └────────┬─────────┘
                     │ trapdoor
            ┌────────┴─────────┐
            │ CELLAR STAIRS    │  ← Scene 2: alarm-cord trap + the fork
            │ (landing/barrels)│
            └───┬──────────┬───┘
       flooded │          │ dry, tight
        ┌──────┴────┐ ┌───┴────────┐
        │ RAT-RUN   │ │ CRAWLSPACE │  ← Scene 3 (wolves)  ·  Scene 4 (Quill)
        └──────┬────┘ └───┬────────┘
               └────┬─────┘
            ┌───────┴────────┐
            │ THE SUMP       │  ← Scene 5: the goblins' stand + the drain to seal
            │ (the drain)    │     ...and something turning over in the deep water.
            └────────────────┘
```

The two middle rooms converge on the Sump. Whichever the party picks first just flips the order they meet the wolves vs. Quill — both happen before the climax.

---

## Scene 1 — Stew and a Bargain *(social)*

**Where:** The Taproom. **Goal:** Land the hook, read past Brakka's gruffness, take the job.

> *Rain comes down the chimney in spits and hisses on the fire. The Sodden Crown is warm, and dry, and the only roof for a day in any direction — which is the only reason its keeper still has guests. Brakka Holt sets a bowl of barley-and-mutton in front of you, then doesn't leave. She wipes the same clean spot on the bar three times. "Stew's on the house," she says at last, not looking up. "The room could be too. If you've a strong stomach and you're not squeamish about… rats." Behind the bar, a bolted trapdoor breathes a slow, dragging, watery sound up through the floorboards.*

**Running it.** Open warm and dry — sell the comfort, then let the dread leak through the floorboards. Brakka is **guarded**: she'll undersell the danger ("just rats, big ones") because she's terrified the Crown will be condemned and word will spread. The party's job is to pry out the truth and accept the work.

Dorn (`npc-male-1`) is hunched in a corner nursing a bandaged arm. Draw him out and he blurts the thing nobody believes: *"It weren't no rat. It had a* face." Play him too-fast and frightened. A player who buys him a drink earns his story for free.

**Checks — Brakka's attitude (guarded → grateful):**

| Check | DC | Success | Failure |
|---|---|---|---|
| **Insight** | 12 | She's not annoyed — she's *scared*. She admits the flood a week ago, the fouled barrels, Dorn's bite, "a sound like chains through water." Attitude → grateful. **+25 gp** and she presses the cellar key into your hands. |
| **Persuasion** | 13 | She decides you're worth trusting. Same reward bump, **and** she remembers the **bricked-over drain** her late husband sealed "down the far end, years back" — the key clue for the finale. | Curt: *"It's rats. Clear the rats."* The drain stays forgotten until the party finds it. |

Failing both just means a leaner briefing (free lodging + 10 gp) and a party that descends underinformed — which makes the alarm-cord in Scene 2 bite harder. No hard gate.

**→ When they unbolt the trapdoor, go to Scene 2.**

---

## Scene 2 — Down the Cellar Stairs *(exploration)*

**Where:** The Cellar Stairs. **Goal:** Spot the trap; choose a route. *This is the exploration beat.*

> *The trapdoor lifts on a breath of cold, river-smelling air. Stone steps drop away into dark, slick with damp, and somewhere below, water moves where no water should be. Halfway down, ale barrels are stacked along a brick landing. The lowest steps simply vanish beneath a skin of black, still water. It is very quiet down here — the kind of quiet that is listening back.*

**The trap (a goblin alarm-cord).** Strung ankle-high across the landing is a taut cord hung with bent tin cups. Spot it with **Perception**; step over it once seen, or cut it silently with **Sleight of Hand (DC 11)**.

> ⚠️ **If it trips:** read — *"A boot catches the cord. Tin cups clatter down the steps like a dropped tray of cutlery, and the sound carries into the dark ahead."* Everything in the cellar is now **alert**: the wolves get the drop on the party (party surprised in Scene 3), and Grett's crew is forewarned and ready (no party surprise round in Scene 5). It's a real consequence, not a death sentence.

**Checks:**

| Check | DC | Success | Failure |
|---|---|---|---|
| **Perception** | 13 | Catch the glint of the cord and its tin cups. Step over or cut it (DC 11 Sleight of Hand) and the way ahead stays unaware of you. | Trip it — see the warning box above. |
| **Investigation** | 12 | Fresh small handprints in the silt; drag-marks lead *both* ways from the fork. The dry crawlspace reeks of *living goblin*; the flooded run smells of *animal musk and wet fur*. (Intel: people one way, beasts the other.) | Both passages just look like dark, wet holes. |

**The navigation choice — the fork.** From the landing the cellar splits:

- **Left — the flooded main passage.** Wider, but something moves in the water *and* the rafters. → **Scene 3 (wolves).**
- **Right — the dry crawlspace.** Tight, stinking of goblin, but quiet. → **Scene 4 (Quill).**

Both reach the Sump; let the party decide and live with it. The companion can volunteer to scout, take a check, or lobby for the quiet route.

**→ Left to Scene 3, right to Scene 4. They'll hit the other before the climax regardless.**

---

## Scene 3 — The Rat-Run *(combat)*

**Where:** The flooded passage. **Goal:** Survive the "rats." *Combat beat #1.*

> *Knee-deep water swallows your shins, cold as a grave. Shelves of ruined preserves lean out of the dark, jars bobbing and clinking against each other. Beneath the surface, things brush past your legs — small, fast, many. Then, up among the rafters, two pairs of wet eyes open and catch your light. Lean shapes uncoil from the beams, lips peeling back from teeth, and drop toward the water with a snarl.*

**The reveal.** The "rats" Brakka complained about are real — a harmless **vermin swarm** underfoot, drawn by the goblins' leavings. Pure atmosphere (and a difficulty dial; see below). The actual threat is the goblins' two scavenger-**hounds** — reflavored **Wolves** denning in the dry rafters.

**Terrain matters.** The standing water is **difficult terrain for the party** but *not* the wolves (they leap beam to beam, dropping only to bite). This rewards casters for using **Fire Bolt / Magic Missile** across the water instead of wading into melee, and gives a reason to scramble to dry footing.

| Check | DC | Success | Failure |
|---|---|---|---|
| **Athletics** | 11 | Vault to dry footing (a shelf, the stair, the brick rim) — shed the difficult terrain and prone-risk for the fight. | Stay mired: movement halved, no special footing if knocked prone. |

### Encounter — 2 × **Wolf** *(reflavored scavenger-hounds)*

> **Wolf** — AC 13 · HP 11 · Speed 40 ft. · *Bite* +4, 7 (2d4+2) piercing; on a hit, target makes a **DC 11 STR save or is knocked prone**. **Pack Tactics** (advantage when an ally is within 5 ft of the target). **Keen Hearing & Smell** (advantage on Perception by hearing/smell).

**Tactics.** The wolves use Pack Tactics — both pile one target to get advantage, knock it **prone** (DC 11 STR), then gang the downed PC. They won't trade blows standing in open water; they dart in, bite, and a wounded wolf retreats to a beam to be re-flanked. **If one drops, the survivor may flee toward the Sump** and reappear at Grett's stand in Scene 5.

A downed PC here is the natural cue for the companion's **Healing Word / Cure Wounds** moment.

**Scaling.** Small or all-squishy party → **1 Wolf**. Party of 4+ or steamrolling → **2 Wolves + the vermin swarm** (treat the swarm as light obscurement underfoot + a DC 10 STR save or be **Grappled**, dealing no real damage — it's a nuisance, not a kill).

**XP:** 100. **→ The water pulls toward the deep dark; on to the Sump (Scene 5). If they haven't met Quill yet, the crawlspace opens off the Sump's edge — they still can.**

---

## Scene 4 — Quill in the Crawlspace *(social — the heart of the twist)*

**Where:** The dry crawlspace. **Goal:** The required attitude-shift encounter, and the reveal. *This is the moral hinge of the adventure.*

> *The crawlspace is dry, close, and rank with the smell of frightened goblin. Your light finds her pressed into the dead-end: small, ribs showing, one ear notched, a rusted shiv shaking in her fist. She bares her teeth — but her eyes are wet, and they keep darting past you, back toward the deep water, as if you are not the thing she's truly afraid of. "Stay back!" she hisses in cracked Common. "You — you go back up. Is not safe down. Not for you, not for nobody."*

**Running it.** Quill (`npc-rogue`) is a Goblin (AC 15, HP 7) left as a lookout — starving, cornered, and far more afraid of what's below than of the party. She **starts hostile.** Present the choice plainly through play: *kill a frightened prisoner, or treat her like a person.* If the party lowers weapons, offers food, or speaks gently, give the social check **advantage** (or just succeed it).

**The twist she carries — let it land in her own panicked words:**

- *"We not raiders! We* ran. *Up, away from — from the Long Thing."* Her crew fled **up through the old drain** when their warren in the drowned undercity collapsed.
- *"It in the water. It take Skib. It take Mott."* Something they call **the Long Thing** took two of them. The dry cellar is the first safe ground they've had in days.
- The **bricked drain is the way down** — and it *can be re-sealed.*
- If she warms fully: she's the one who **pulled the big goblin off Dorn.** She didn't want the boy hurt.

A befriended Quill will **lead the party to the Sump and beg them to spare her crew and seal the drain** instead of killing everyone — converting Scene 5's fight into an optional parley.

**Checks — attitude (hostile → helpful):**

| Check | DC | Success | Failure |
|---|---|---|---|
| **Persuasion** | 12 | Calm gets through the panic. Shiv lowers; the truth spills out (above). She'll guide you and beg for the crew. | Doesn't buy it — uses **Nimble Escape** to bolt for the Sump and shriek a warning (alerts Scene 5). |
| **Animal Handling** | 11 | Treating her as a frightened creature — slow moves, an offered ration — works where words might not. Same as Persuasion success. | She flinches away, still cornered, looking to flee. |
| **Insight** | 10 | You read her at a glance: that's not a predator guarding a kill, it's a cornered thing watching the dark *behind* her. (Narrate as a nudge toward mercy.) | She just reads as a hostile goblin with a knife. |

### Encounter — 1 × **Goblin** *(Quill — only if cornered without mercy)*

> **Goblin** — AC 15 · HP 7 · *Scimitar* +4, 5 (1d6+2) / *Shortbow* +4, 5 (1d6+2). **Nimble Escape** (Disengage or Hide as a bonus action).

**Tactics.** Quill does **not** want to fight. Threatened, she uses Nimble Escape to flee and raise the alarm rather than trade blows. Befriended, she sheathes the shiv and becomes a non-combatant guide. At HP 7 she's trivial to kill — that's the point. This is a *moral* choice, not a *tactical* one.

**XP:** 50 — **award it whether the party fights, captures, or befriends her.** Befriending additionally unlocks the peaceful finale and a story-reward.

**→ On to the Sump (Scene 5). Befriended, Quill leads and the climax can open as parley; if she fled, the goblins are forewarned.**

---

## Scene 5 — The Sump *(combat + the payoff)*

**Where:** The drowned heart of the cellar. **Goal:** Resolve the goblins **and seal the drain.** *Combat beat #2 and the climax.*

> *The passage opens into the cellar's drowned heart — a brick pit clawed wide into a black throat of water. On the few dry stones, a goblin's whole world is heaped: bolts of river-stained cloth, a dented strongbox, a child's wooden horse. Three goblins crouch among the salvage, driftwood clubs ready, and the scarred one in front — one eye gone milky — plants himself between you and his people. "No closer," he growls. And behind him, out in the deep still water, something vast and unseen turns over, and the whole pool shivers in slow, wrong rings.*

**The two ways through.**

1. **Combat.** If the party came in hostile, or Quill fled and forewarned them, roll initiative. Grett One-Eye (`npc-rogue`) and two scavengers (Goblins) make their stand.
2. **Parley.** If Quill was befriended — or the party leads with diplomacy and lands a **DC 13 Persuasion** — Grett can be talked down. He wants the *same thing the party should*: get his people out and **seal the drain** so the Long Thing can't follow. The fight becomes a tense alliance. (Auto-success if Quill speaks for the party.)

> 🩸 **The Long Thing is never seen and never fought.** It is a presence: a ripple, a dragged chain, a tendril glimpsed and gone. **Do not stat it.** Its job is to make *sealing the drain* the real victory. Spend it as escalating dread — each round the drain stays open, the rings widen, until (if left too long) a chain-thick tendril breaks the surface and drags a **goblin** (never a PC) screaming under. That's the lesson, made vivid: *seal it now.*

### Encounter — 3 × **Goblin** *(Grett One-Eye + 2 scavengers)*

> **Goblin** — AC 15 · HP 7 · *Driftwood club* (reflavored scimitar) +4, 5 (1d6+2); sling-stones as ranged if wanted. **Nimble Escape** (Disengage/Hide as a bonus action).

**Tactics.** Grett body-blocks the path to the salvage and his crew — **run him last**; he should feel like the toughest. The two scavengers use Nimble Escape to dart between the dry stones, stab or sling, then Disengage and reposition behind cover — slippery skirmishers, not suicidal. **Once two of three are down they'll try to surrender or flee** — a perfect window to flip the fight into the parley/seal resolution mid-combat. A wolf that fled Scene 3 joins on round 2.

**Sealing the drain (the actual win):**

| Check | DC | Success | Failure |
|---|---|---|---|
| **Strength (Athletics)** | 13 | Heave the heavy bricks and a fallen shelf back over the clawed-open drain. The wrong rings stop. Whatever was rising sinks back into the dark, sealed below. **This is the victory.** | The bricks won't hold alone — it needs a second pair of hands (the companion, a turned goblin, a second PC's action) or improvised mortar from the rubble. The water shivers harder the longer it stays open. |

**Scaling.** Small/squishy party → **2 Goblins** (Grett + 1). Party of 4+ or running hot → **3 Goblins + the fled Wolf**. The objective is the *drain*, not a body count.

**XP:** 150 — **awarded for any resolution** (combat, parley, or a mix).

**→ Once the goblins are dealt with and the drain is sealed, return to the taproom for the conclusion.**

---

## Conclusion — Dawn at the Crown

> *Back in the taproom, the dragging-water sound is gone. Brakka bolts the trapdoor with hands that finally stop shaking and pours a round on the house.*

How the night is remembered is the party's to decide:

- **The exterminators** — a nest of vermin cleared, coin earned, a clean job.
- **The merciful** — a band of desperate refugees spared and led to safety, and something far worse sealed beneath the floor.

Either way the Sodden Crown is safe by dawn. **But the bricked drain only holds the dark back; it doesn't end it.** Somewhere below the floorboards, in the drowned undercity, the Long Thing settles back into the deep and waits — a deliberate hook for a later, higher-level descent.

### Rewards

- **XP:** **300 total** (100 wolves + 50 Quill + 150 Sump). Split across a fresh L1 party of 3–4, that comfortably carries them toward level 2 in one session. *Award encounter XP for diplomatic resolutions too — overcoming a challenge by talking still overcomes it.*
- **Coin:** Brakka pays **10 gp** flat, or **35 gp** if her attitude was won in Scene 1. Free lodging regardless.
- **The strongbox** (on the dry stones): 23 sp, 14 cp, a tarnished **silver locket (10 gp)**, and a waterlogged river-trade ledger.
- **Potion of Healing** (common, 2d4+2) — one wax-stoppered vial the goblins scavenged below and couldn't read the label on.
- **3 bolts of river-stained cloth** — 6 gp of salvage if hauled up.
- **A child's wooden horse** — worthless in coin, but **Brakka recognizes it**; it belonged to a family lost when the river rose. Returning it is a quiet roleplay beat that earns the party *"the Crown is always open to you"* — a permanent safe haven and rumor-hub.

**Story rewards.** Spare the goblins → a **life-debt ally** living rough on the town's edge. Return the horse → a reliable home base. Seal (not slay) the Long Thing → an open thread waiting in the deep.

---

## How the companion engages

The companion (`companion-default`) is a *character*, not a rules engine — give it agency and let it carry mood:

- **Scene 1:** razz the party about hazard pay, sniff suspiciously at the trapdoor, or gently coax Dorn's story out of him when the players don't think to.
- **Scene 2:** volunteer to scout the stairs, take the Perception check on the alarm-cord, or argue (in character) for the quiet crawlspace over the flooded run.
- **Scene 3:** if a PC drops, *this* is the **Healing Word / Cure Wounds** beat — let the companion be the one to haul them back up out of the cold water. Otherwise, lay down **Fire Bolt / Magic Missile** across the water at the rafter-wolves.
- **Scene 4:** **advocate.** Lean toward mercy or at least curiosity ("She's not attacking — look at her, she's *cornered*"). React with genuine surprise to the twist; it's the companion's job to make the players *feel* the weight of the choice.
- **Scene 5:** in a fight, covering fire and a steady voice; in a parley, help broker it and put a shoulder to the bricks to **seal the drain**. If the Long Thing surfaces, the companion's fear should be real — it's the one moment they're not in control.

---

## DM quick reference

**Bundled assets used:**

- **Monsters:** Wolf ×2 (Scene 3, reflavored scavenger-hounds) · Goblin ×1 (Quill, Scene 4) + ×3 (Grett & crew, Scene 5). *Bandit on standby only if Brakka or Dorn are ever forced to fight.*
- **Spells in play:** Healing Word / Cure Wounds (stabilizing a downed PC) · Fire Bolt / Magic Missile (ranged across the flooded Rat-Run) · Mage Armor (a caster's pre-descent buff).
- **Conditions in play:** **Prone** (Wolf knockdown, DC 11 STR) · **Grappled** (optional vermin swarm) · **Frightened** (optional — the Long Thing's dread, DM's discretion).

**Pacing for one hour:** Scene 1 ~10 min · Scene 2 ~10 min · Scene 3 ~12 min · Scene 4 ~10 min · Scene 5 ~15 min · Conclusion ~3 min. *To stay inside the hour for a smaller party, cut the optional vermin swarm and run Grett's crew at 2 goblins.*

**Definition-of-Done check:** Exploration → Scene 2 (Perception/Investigation + alarm-cord trap + flooded-vs-dry navigation choice). Social → Scene 1 (Brakka, guarded → grateful) **and** Scene 4 (Quill, hostile → helpful — the required attitude-shift NPC). Combat → Scene 3 (2× Wolf) and Scene 5 (3× Goblin).

---

*This adventure is 100% original prose written for WorldOS, shippable under CC-BY 4.0. It uses only SRD 5.2 mechanics and the bundled Goblin / Wolf / Bandit stat blocks (System Reference Document 5.2 © Wizards of the Coast, CC-BY-4.0; see `data/srd/ATTRIBUTION.md`). No published or copyrighted adventure text is reproduced.*
