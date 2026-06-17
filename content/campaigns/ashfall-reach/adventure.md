# The Lantern-Keeper of Ashfall Reach

*A WorldOS coastal romance campaign — SRD 5.2, levels 3 → 7, ~4 sessions.*
*Set in the original `saltmere-coast` world (a post-war Reaving coast — NOT a published setting).*
*Original fan-content prose on SRD primitives. Released under the project's terms (SRD 5.2 / CC-BY-4.0 rules; original world, characters, and prose). NOT official, never sold.*

---

## The Pitch

Ashfall Reach is the last lit headland on the **Saltmere Coast** — a black volcanic spur where the old
**Reaving** war left more wrecks than the sea has names for, and where one lighthouse still burns the
only safe channel through the drowning rocks called the **Teeth**. Its keeper is **Wren Calder**, who
climbs the long stair every dusk to light the great lantern and keeps it burning till dawn, alone, the
way they have for six years.

Now a wrecking ring has come back to the Reach — a "salvage company" run by a charming, ruined
merchant-captain called **Halloran Vane, the Tidereeve**, who buys keepers, quenches true lights, and
hangs false amber lanterns on the killing rocks to harvest the ships that break. Vane means to own
Ashfall's lantern the way he's bought every other light on the coast. The only thing in his way is a
keeper who cannot be bought.

This is **a love story with a wrecking-light at the center of it.** Earn Wren's trust by saving the
helpless from the sea; earn their heart at the camp-fire on the cliff; learn the drowned sibling they
never lit the lantern for in time. Or spend a value too cheaply — let a ship founder for the salvage —
and watch the lantern-keeper quietly put out the light between you and walk back up the stair alone.

## The Antagonist (Not Hidden)

**Halloran Vane, "the Tidereeve."** Named from the first scene; the salvage company is on every
warehouse door. The reveal is not *who* he is but *how he works* — the wrecking laundered as lawful
salvage, the bought keepers, and the awful symmetry: Vane was an honest shipmaster until his own fleet
wrecked on a light a keeper wouldn't burn without a bribe, and he came out of the water having learned
it **backward** — that the sea belongs to whoever holds the lights, and that mercy is the luxury that
drowned his crew. He is never cruel for sport. He is worse: reasonable, sorry, and certain, and he
will offer you a fair price for your conscience and **mean it kindly.**

- **Public weapon:** the false amber light on the south rocks, the convoy clock, and the offer — coin
  before blades, always.
- **Lieutenant:** **Sable**, the silent wrecker who hangs the false lights — herself a keeper once, on
  a light Vane bought and quenched, who chose the oilskins over drowning in the debt. The mirror of
  the choice Vane wants Wren to make; turnable at the climax.
- **Stats (Act 3 only):** reflavored **Bandit Captain** (AC 15, HP 65, CR 2, scaled up) — a captain who
  *deals and escapes* before he duels. A Tidereeve who rows off to wreck another coast is a fine
  dangling thread.

## The Companion — and the Fork (a Romance)

**Wren Calder** (Ranger 3, `companion-default`) — the lantern-keeper of Ashfall Reach: lean,
salt-scarred, gentle with the helpless and merciless with anyone who'd trade a life for salvage. As a
child Wren watched a ship founder on the Teeth that the Reach's old keeper let founder *for the
salvage* — and their younger sibling **Edda** drowned within sight of the unlit tower while Wren
screamed from the cliff. They took the keeping to make sure no light ever went dark for coin again, and
they have **not missed a single night in six years**, lighting the tower as a penance for the one they
couldn't light in time.

Wren is the campaign's deliberate **romance arc** — and a real fork, a *love story's* fork, not a
saboteur's:

| Path | What it takes | What it becomes |
|------|---------------|------------------|
| **Loyalty → Romance → Devotion** (gates 25 → 50 → 65 → 75) | Protect the helpless from the sea, refuse salvage-coin for a life, honor Edda's memory, keep a post and a promise | Trust cracks the keeper's solitude; the **romance gate (50)** is the confession at the cliff camp-fire; the **personal_quest gate (65)** is the drowned sibling Edda; the **devotion gate (75)** is the keeper keeping the light *with* the party at the storm — a hand held in the lantern-glow. |
| **Withdrawal** (agenda: `attitude_below -30`, flag `let_the_ship_founder`) | Let a ship founder for salvage, treat Wren as a tool, spend a life as coin | Wren does **not** turn violent and does **not** join Vane. They **quit** — put out the light between you, hand over the keeping, and climb the stair alone into a grief they've decided you can't be trusted with. A **non-combat fallout**: the romance closes, the companion withdraws. |

The **romance is opt-in and earned** — a party that allies with Wren without courting them still gets a
fierce, faithful companion; the **romance** gate fires only when approval is high *and* the party has
leaned into the tenderness. Honored, Wren is the heart of the coast; curdled, they're the love the
party wrecked themselves.

The agenda is a `CompanionAgenda` (`attitude_below -30`, `decision_flag: let_the_ship_founder`) — the
withdrawal is a **real engine event**, not a line in a prompt. Set the flag with `record_decision` /
`set_flag` when the party lets a ship founder for salvage or trades a life for coin.

## Structure — Hub & Three Spokes

The party returns to **the Saltmere Cove & the Drowned-Rat tavern** (and the cliff above it) between
acts to rest, level, and learn the coast's grief one quiet voice at a time. One site per act radiates
from the hub.

| Act | Levels | Site | The Beat |
|----|--------|------|----------|
| **1 — The False Light** | 3–4 | **The Teeth & the Channel** | Work the wrecking: two lights, the false-light hook, the laundered salvage, the indebted harbormaster. Then **row into the surf beside Wren** to save a ship the false light calls — the keeper's first test of whether a light is safe in your hands. Trust gate (25). |
| **2 — The Keeper's Heart** | 4–6 | **Ashfall Lighthouse & the Cliff** | Vane stops attacking the Teeth and attacks the **keeper** — cut supply, a planted frame, a civil buy-out offer. At the **camp-fire on the cliff**, Wren lets you in: the **romance** confession (50) and the **drowned sibling** Edda (65). Defend the light from Sable's wreckers. |
| **3 — The Longest Night** | 6–7 | **The South Rocks in the Long Grey Storm** | The worst storm of the year; the winter convoy runs the Teeth; Vane moves to quench the true light and wreck a dozen ships at once. **Hold the light** — douse the false one, keep the true one, break Vane against a **convoy-clock**. The **devotion** payoff (75), or the **withdrawal** cost. |

Each act exercises **exploration + social + combat**. Rescues, the dive to lay Edda to rest, social
turns, and the romance all earn full XP — *holding the light* counts as overcoming the finale.

## Monsters per Act (all bundled SRD stat blocks, CR-tuned)

- **Act 1 (lv 3–4):** wreckers in the surf = **Thug → Tough** ×3 (CR 1/2) + optional **Scout** (Sable
  directing). A rescue-under-fire; *lives saved* is the win.
- **Act 2 (lv 4–6):** **Sable = Spy** (CR 1) + **Tough** ×3 + optional **Swarm of Rats**. A
  defend-the-lighthouse skirmish (keep the light, save the supply, clear the frame).
- **Act 3 (lv 6–7):** **Vane = Bandit Captain** (CR 2, scaled up) + **Sable = Assassin/Spy** (if not
  turned) + **Tough** ×3 + **Scout**, against a **convoy-clock**.

Every name resolves through `spawn_monster` / the bundled bestiary. Per-scene scaling notes tune each
fight to party size.

## The Real Boss — the Wrecking, Not Vane's HP

Vane is the human architect the party confronts, but **the real antagonist is the wrecking** — the
false light, the bought keepers, the predation of salvage. Vane *prefers to deal and to flee*, and a
Tidereeve who rows off to "salvage" another coast is a fine dangling thread. The **win** is **holding
the light**: dousing the false amber light, keeping Ashfall's true light burning, and saving the
convoy. The **loss** state is the Act 3 **convoy-clock** running out — the false light calling the
convoy onto the Teeth for the richest wrecking the coast has ever seen. Three win-conditions keep the
finale open: **hold the light**, **turn or break the crew**, or **put Vane down by force**.

## How It Ends

Dawn comes grey and washed-clean; the Long Grey is blown out to sea; the convoy rides at anchor in the
cove — a dozen ships, a hundred souls, brought home through the one honest light on the coast. How the
Saltmere remembers it is up to the party: as the storm they broke the Tidereeve's ring, or — quieter,
and truer — *as the night the lantern-keeper finally stopped keeping the light alone.* If Wren was
honored, they climb the stair beside the party to bank the flame at dawn, **Edda laid to rest** in the
channel that took her, and light the tower from now on as a **hope** instead of a penance — the hand
still in theirs in the lantern-glow. If the party spent the keeper too cheaply, the light still burns
above the Reach — it always will — but it burns for **one person again**, the lonely figure on the
cliff who reached out once across six years and learned the hard way that some lights are not safe with
everyone. *The false lantern lies smashed on the south rocks. But the sea always wants more than the
light can save.*

---

## Sample Scene Prose

> *(These expand the read-aloud + dm_notes in `adventure.json` into playable prose the DM voices at the
> table. Boxed text is for the players; the rest is staging.)*

### Act 1 · Two Lights on the Teeth — *the hook, at the Drowned-Rat*

> The Drowned-Rat tavern leans into the wind, peat-smoke and salt thick in the low room. In the corner
> a weather-beaten fisherwoman with a bandaged hand says it again to faces that won't meet hers: *"There
> were TWO lights. The true white off the headland — late, dim, like something was smothering it — and a
> false amber down on the south rocks, swinging, calling us in like it was the channel. We'd be drowned
> but for the keeper."*

She nods at the lean, salt-scarred figure by the fire — rope-burned palms wrapped in rag, drying out
after rowing into the surf by hand. **Lean on the dramatic register.** The cove is a town in *debt*;
the talk is careful because Vane's coin reaches everywhere. What marks **Wren** as the one worth
following is the tired, level grey gaze that's stopped expecting help — and the cold, *personal* fury
under it.

> Wren Calder looks at the party with a gaze that's stopped expecting much. *"Someone's hanging false
> lights to break ships on the Teeth. I've said it all season. I light the only honest light left on this
> coast, and I light it alone, and I'm asking — because you're the only souls on the Reach who don't owe
> Halloran Vane a copper — is there anyone left in the world who'll help me keep one light honest?"*

A sharp player who makes the **Insight (DC 13)** catches the loneliness under the calm, and an *old,
deep grief* this wrecking has torn back open — older than this season. Don't reveal Edda yet; the
camp-fire is Act 2's.

### Act 1 · Into the Surf — *the rescue climax, the keeper's first test*

> A low amber light flares on the south rocks, swinging like a beckoning hand, and out past the Teeth a
> merchantman's lanterns answer it and turn — *toward* the killing stones. Ashfall's great white flares
> late and furious as Wren fights whatever's smothering it, but the ship is already committing to the lie.

> Wren shoves a rowboat down the shingle, palms still raw. *"She's going to strike. Crew of six. I can't
> pull six alone."* Out in the breaking water, dark figures in oilskins are not rowing to *save* the
> drowning — they're rowing to **harvest** them, gaffs and cargo-hooks in hand, waiting for the ship to
> break.

**This is the keeper's chief approval surface of Act 1.** Rowing in for strangers
(`row_into_the_surf_for_a_stranger`), refusing to let a soul drown for salvage
(`refuse_salvage_coin_for_a_life`, `protect_the_helpless`) sends approval up hard and can open the
**trust gate (25)**. The agenda seed: a party that hangs back, lets the crew drown for cargo, or takes
the wreckers' salvage-bargain is doing exactly the thing that — set as `let_the_ship_founder` —
**arms Wren's withdrawal agenda.** The win is *lives saved,* not bodies dropped.

> Wren rows into the surf by hand, hauls a half-drowned sailor over the gunwale, and looks at the
> stranger rowing in beside them with a hard, surprised gratitude. *"You came out for them. For people
> you don't even know."* A breath. *"Don't let me get used to that and be wrong."*

### Act 2 · The Camp-Fire on the Cliff — *the romance + the drowned sibling*

The small fire ticks in the lee of the rocks; the great lantern turns above on the schedule Wren's body
has kept for six years without a clock. **Let it breathe — this is the campaign, not a side-quest.**

> *"I had a sibling. Edda. Younger. Laugh like the gulls."* The lantern sweeps over you both and away.
> *"There was a keeper here before me who let a ship founder on the Teeth for the salvage it'd wash up —
> kept the light dark on purpose, for coin. Edda was on a fishing boat that night. I was a child on the
> cliff. I screamed at the dark tower and I could not light it in time, and the sea took my sibling
> within sight of a lantern that stayed black for money."*

That is the **personal_quest gate (65)** — the drowned sibling, the lantern never lit in time, the six
years of penance-keeping. Then the **romance gate (50)**, the confession made the keeper's way — plain,
gentle, braced to cost them:

> *"I've lit it every night since. Six years. As a penance. And somewhere in this terrible season, rowing
> out beside you, I stopped lighting it only against the dark."* Wren turns, grey eyes bright in the
> firelight. *"I started lighting it a little for you. I don't know how to ask anyone to keep a light with
> me. But I find I want to."*

**The romance is opt-in.** A party that answers in kind consummates the turn — the romance is real from
here and pays off at the storm. A party that responds with *respect but not romance* still deepens the
**loyalty** (Wren stays a fierce, faithful ally; the romance gate simply doesn't fire — a valid, gentle
outcome, not a failure). The values-tagged fork lands here or at Vane's offer: honor the grief and
**refuse Vane's coin** (approval up) versus treat the light as a chip to trade
(`take_the_wreckers_coin`, `cruelty_dressed_as_pragmatism` → toward the agenda). If approval has already
curdled (below −30 with `let_the_ship_founder` set), the **withdrawal agenda fires here instead** — the
keeper quietly puts out the light between you and climbs the stair alone. No blade. Just the cost.

### Act 3 · The Light Held, or the Light Between — *the storm-climax*

It all comes at once: the convoy driving toward the Teeth on the false light's lie, Ashfall's true
lantern guttering as wreckers fight to quench it, and Vane drawing his cutlass with a sorrowful shake
of his head. His cruelest card is the **last offer aimed at Wren:**

> *"Keeper. I'll clear your debt and your sibling's grave-stone both, tonight, and you can put down a
> light you've carried alone for six years and finally REST. All you have to do is step aside and let the
> sea take what it's owed."*

**Run the convoy-clock:** each round the false light burns and the true light is dark, the lead convoy
ship runs closer to the Teeth. Three ways to win — **hold the light** (Athletics: douse the false,
keep/relight the true), **turn or break the crew** (Intimidation; turn **Sable**, the keeper-turned-
wrecker, with Insight), or **break Vane** (a very hard **DC 17 Persuasion** that doesn't redeem him but
strips his certainty — *"the merchant the sea drowned would spit on the wrecker you became"*). Vane
fights to *flee,* not to win.

If Wren reached the **devotion gate (75)**, give them the climactic stand — the keeper keeping the
light *with* the party at last:

> Wren plants themselves on the gallery, blade drawn, the great light blazing white behind them, and
> calls down to the wreckers in a voice six years of solitude made iron. *"This light does not go dark.
> Not tonight. Not for his coin. Not for the sea."* They glance back at the party — not the lonely figure
> on the cliff anymore. *"And I'm not keeping it alone."*

If the agenda fired, the keeper withdraws up the stair to keep the light *alone,* and the party finishes
the fight a heart short — *"I hoped, against all my better sense, that this time a light would be safe
with someone. It wasn't."*

---

## Running Notes

- **Leveling:** milestone recommended — level 4 after the Teeth rescue, 5–6 across the lighthouse and the
  camp-fire, 7 before/during the storm-climax. (~8,000 XP total if you prefer encounter XP.)
- **The romance is the spine.** Give `scene-a2-campfire` room to breathe; it *is* the campaign. The
  romance is opt-in — a non-courting party still earns a faithful companion via the loyalty gates.
- **Lives saved is the win, not bodies dropped.** Act 1's rescue and Act 3's convoy are scored on souls
  hauled out of the surf; a "combat" that ends with the whole crew alive is a *better* win.
- **Set the fork flag honestly.** Call `record_decision` / `set_flag` with `let_the_ship_founder` when
  the party lets a ship founder for salvage or trades a life for coin (scene-a1-rescue,
  scene-a2-campfire, scene-a3-climax). That flag *arms* Wren's withdrawal agenda — the engine rolls the
  turn, the content names the cause. Wren never turns violent; the betrayal is the **light going out.**
- **Vane can be *reached*** at the climax (a hard DC 17 Persuasion) — not redeemed, but made to see he is
  now the exact dark-lantern keeper who drowned his own crew. Reserve the full effect for genuine
  brilliance; the default is dousing the false light and saving the convoy.
- **Validation:** `generate_campaign`-shaped (named antagonist, hook/challenge/climax beats, hub + one
  site per act, a full companion dossier + 4-gate arc incl. a **`romance`** gate + a `CompanionAgenda`).
  `validate_adventure()` returns `[]`; `seed_campaign()` loads cleanly with Wren Calder in the party.
