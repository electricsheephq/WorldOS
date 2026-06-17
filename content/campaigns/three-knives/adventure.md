# Three Knives at the Ledger-Gate

*A WorldOS Baldur's Gate campaign — SRD 5.2, levels 3 → 7, ~4 sessions.*
*Set in the bundled `baldurs-gate` world seed (post-BG3, 1492 DR — the winter after the Absolute fell).*
*Original fan-content prose on SRD primitives. Released under the same terms as the baldurs-gate world seed (Wizards Fan Content Policy + Larian for BG3 elements; SRD 5.2 / CC-BY-4.0 rules). NOT official, never sold.*

---

## The Pitch

**Risen-Gate** is a contested border town on the Risen Road — the last walled crossing before the
Lower City of Baldur's Gate — and the war left it with no one to rule it. Three powers hold it by the
throat at once: a remnant of the **Flaming Fist** who keep the law by the letter, the smugglers' **Vine**
who keep the desperate fed by breaking that law, and the **Pale Sister's almshouse** who keep the dying
alive and ask no questions of either. They've shared the gate-tolls in an uneasy three-way truce for a
year. Now a road-reaver warlord, **Maddox Crale, the Tollwright**, has set a noose around the town and
means to own the gate and everyone who passes through it.

The genius of his siege is that he doesn't intend to break the wall — he intends to make the town break
*itself*. Before his army arrives he murders the one man holding the truce together, frames the Vine,
seeds each faction's worst suspicion of the others, and lets **law, survival, and mercy** do his
siege-work for him. A garrison at its own throat is a gate that opens from within.

The party arrives as outsiders who owe no faction — which is exactly why the dead reeve's terrified clerk
runs to *them*. And to hold Risen-Gate, the party will have to lead three people who hate each other's
methods, and answer the judgment-calls a siege forces, knowing that **every choice that satisfies one of
the three curdles another.**

## The Antagonist

**Maddox Crale, "the Tollwright."** Not a hidden villain — every faction names him as the army on the
road from the first scene. He's a patient, almost genial siege-craftsman who learned over a long career
that *walls are strong and the agreements behind them are not.* He murdered the reeve, framed the runner,
and planted an **inside agent** a year ago, bonded into one of the three factions. His real weapon is the
**fracture** he widens day by day, and his masterstroke at the climax is an offer aimed precisely at
whichever companion the party has wounded most.

- **The reveal** is not *who* he is but *what his weapon is*: the slow understanding that the murdered
  reeve, the framed runner, the burned grain, and every cruel either-or the siege forces are **one plan**
  to make the town destroy itself before he arrives.
- **The inside agent** — his hand within the walls, the reeve's killer — is the second reveal (Act 2
  midpoint), staged in the colors of whichever faction the party has leaned on *least*.
- **The hardest reveal is about the party:** the companion who turns at the climax does so because the
  party genuinely *betrayed* one of three good people under pressure — and the engine, not the DM, makes
  that betrayal real.
- **Stats (Act 3):** **Knight** (AC 18, HP 52, CR 3) scaled up (Veteran/Gladiator, or Bandit Captain +
  Knight) — a sellsword-lord who *trades blades* but whose host only ever came for a cheap toll. If the
  wall holds and the cost runs high, he rides off to set a toll elsewhere — a fine dangling thread.

## The Companions — Three Knives, Orthogonal Vocabularies

The spine of this campaign is **three competing-agenda companions whose approval vocabularies are
deliberately orthogonal**, so a single tagged decision moves their gauges in *opposite* directions. All
three are **original, non-origin characters** — never the seven BG3 origin heroes.

| Companion | Pole | Likes (sample) | Dislikes (sample) | Agenda flag |
|-----------|------|----------------|-------------------|-------------|
| **Sgt. Maelin Vael** (Fighter, `companion-default`) | **LAW** | `keeping_your_word`, `due_process`, `hold_the_law_under_pressure` | `summary_execution`, `spring_the_framed_runner`, `sparing_an_enemy` | `broke_your_oath` |
| **Korrin "the Fence" Vane** (Rogue, `npc-rogue`) | **SURVIVAL** | `cutting_a_deal`, `sparing_a_desperate_thief`, `spring_the_framed_runner`, `bend_the_law_to_save_a_life` | `rigid_law_that_gets_people_killed`, `turn_in_the_thief`, `hold_the_law_under_pressure` | `turned_in_the_thief` |
| **Sister Anwen** (Cleric, `npc-elder`) | **MERCY** | `sparing_an_enemy`, `tending_the_wounded`, `broker_a_surrender` | `collateral_cruelty`, `order_the_massacre`, `spend_lives_to_save_lives` | `ordered_the_massacre` |

**The orthogonality is a literal engine fact, not just a narrative one** — the same cause-key is a *like*
for one and a *dislike* for another, so one tag splits two gauges:

- `spring_the_framed_runner` / `bend_the_law_to_save_a_life` → **+Korrin, −Vael** (survival vs law)
- `hold_the_law_under_pressure` → **+Vael, −Korrin** (law vs survival, the other direction)
- `sparing_an_enemy` → **+Anwen, −Vael** (mercy vs law)
- `spend_lives_to_save_lives` → **+Korrin, −Anwen** (the *method* split — survival vs mercy)
- `summary_execution` → **all three dislike it** (the one shared red line)

Note the *partial* alignments that make the triangle live: Korrin and Anwen both want to save the
desperate but split on **method** (Korrin will cut a hard deal Anwen finds cruel); Vael and Anwen both
want order but split on **mercy** (Vael will hang the guilty Anwen would spare).

### The Fork — three knives, one turns

Each companion carries a 4-gate arc — **loyalty (25) → personal_quest (50) → loyalty (75)**, plus a
**betrayal (20)** gate reachable *only when approval curdles* — and an `attitude_below 20`
`CompanionAgenda` armed by a **distinct** `decision_flag`. At the climax, **only the most-betrayed
companion turns**, and each turns *differently*, in character:

| Companion | Flag | The turn |
|-----------|------|----------|
| **Vael** | `broke_your_oath` | Not for gold — she **withdraws the Fist** from a defense she's decided is no longer lawful, or turns to stop a party that became the lawlessness she swore against. *"I swore an oath to the law, not to you."* |
| **Korrin** | `turned_in_the_thief` | **Pulls the Vine, the grain, and the secret ways** out at the worst moment — or cuts a private evacuation deal with Maddox. *"You should have saved the people in front of you."* |
| **Anwen** | `ordered_the_massacre` | **Withdraws her mercy and sanctuary**, opens the almshouse to the wounded the party abandoned, and stands *unarmed* between the party and a slaughter. *"You made the cost too high."* |

The **other two stand with the party.** And if the party honored all three — no gauge below 20 — **all
three stand**, and Maddox's whole strategy collapses, because the fracture was his only real weapon. That
perfect run is the campaign's hardest victory: a feat of balance, not of XP.

## Structure — Hub & Spokes

The party returns to **the Risen-Gate Gatehouse & Toll-Yard** between acts to rest, level, and feel the
town tighten one notch closer to its own throat. The three faction sites radiate from the hub in Act 1;
the reaver-camp/held-gate is the Act 2–3 spoke.

| Act | Levels | Sites | The Beat |
|----|--------|-------|----------|
| **1 — Three Knives, One Gate** | 3–4 | **Fist Post / Vine Quarter / Almshouse** | The reeve is murdered to break the truce on a timer. Recruit the three competing-agenda companions, one per pole. The **Runner's Rope**: how the party handles the framed runner Brannick is the first three-way orthogonal choice — and the first to move all three gauges in different directions. |
| **2 — The Siege Tightens** | 4–6 | **The Tollwright's Camp** | Maddox burns the grain, fouls the wells, ransoms prisoners — forcing impossible joint decisions (who eats, who hangs, who's left outside). Raid the camp; uncover the **inside agent** (midpoint reversal). Cornering the agent forces the sharpest choice of the siege and **sets each gauge's final state + decision_flag.** |
| **3 — The Held Gate** | 6–7 | **The Held Gatehouse** | Maddox brings the host and offers terms aimed at the party's worst-spent virtue. **Expose the inside agent** in the open (the last lever to pull a wavering knife back), then **hold the gate** — where the fork pays out: two stand, one may turn. |

Each act exercises **exploration + social + combat.** Diplomatic, stealth, and political resolutions earn
full XP — defusing the riot, turning reavers, re-fusing the town, or holding the gate by parley counts as
overcoming the finale.

## Monsters per Act (all bundled SRD stat blocks, CR-tuned)

- **Act 1 (lv 3–4):** *Optional* riot — **Guard** ×3 (Fist) + **Scout** ×3 (Vine). Best resolved by
  *defusing* it (no deaths). Veteran/Thug for a harder table.
- **Act 2 (lv 4–6):** reaver-camp — **Bandit** ×4 + **Scout** ×2 + **Berserker** ×1; the **inside agent**
  as **Spy** (light) or **Assassin** (CR 8 mid-boss) + **Scout** ×3.
- **Act 3 (lv 6–7):** **Maddox = Knight** (CR 3, scale to Veteran/Gladiator or Bandit Captain + Knight) +
  the **agent** (Spy/Assassin, if survived) + **Bandit** ×4 + **Scout** ×2 + **Berserker** ×2, against a
  **unity-clock**.

Every name resolves through `spawn_monster` / the bundled bestiary. Per-scene scaling notes tune each
fight to party size.

## The Real Boss — the Fracture, Not Maddox's HP

Maddox is the human enemy the party fights, but **the real antagonist is the fracture** — the three-way
collision of law, survival, and mercy that he engineered and the party either holds together or spends
apart. His host is a sellsword band that came for a *cheap toll*; if the wall holds and the cost runs
high, it routs and Maddox rides off to set a toll elsewhere. The **win** is **holding the gate whole** —
keeping the town from opening from within — which means keeping at least two of the three knives at the
wall. The **loss** state is a gate opened from within because the party spent one virtue too cheaply.
Three win-conditions keep the finale open to any party: **re-fuse the town** (Persuasion), **break the
host's nerve** (Intimidation), or **hold the gate by force** (Athletics/combat).

## How It Ends

Dawn finds Risen-Gate still standing — or standing *wounded*, depending on what the party spent to hold
it. The reaver-host is gone; Maddox is dead in the gate's shadow or riding north with his ledger. The
strongbox is refilled, the reeve buried with his murderer named beside the open grave, and Brannick walks
free. How the town remembers it is up to the party: as the day three knives that could never agree stood
together at the gate and held it as one — or as the day the party learned, the hard way, that *you cannot
spend law to buy survival, or survival to buy mercy, without one of the three turning in your hand.*
Either way the truce the old reeve died for has outlived him, fragile and real — *Vael keeps the law,
Korrin keeps the people fed, Anwen keeps them alive,* and the gate opens only to those who pay an honest
toll, and to no Tollwright at all.

---

## Sample Scene Prose

> *(These expand the read-aloud + dm_notes in `adventure.json` into the playable, BG-register prose the
> DM voices at the table. Boxed text is for the players; the rest is staging.)*

### Act 1 · Three Who Cannot Agree — *recruiting the trio, at the toll-yard*

> To learn who killed the reeve, you have to walk into all three armed camps that hate each other — and
> each one wants something different from you before it will talk.

This is the **structural heart** of Act 1. Recruit all three, and *establish their orthogonal
vocabularies in one scene* so the player feels the gauges move in opposite directions on a single choice.

> At the Fist post, **Sergeant Vael** looks up from a duty-roster with the flat, tired eyes of someone
> holding a wall with her bare hands. *"You've no faction's coin in you. Good. Then help me do this RIGHT
> — by the evidence, by a trial — before my own troopers hang that runner tonight and prove this town's
> not worth saving."*
>
> In the Vine's quarter, **Korrin** leans off a grain-cart, all card-sharp's smile and furious soft
> center. *"Vael wants to TRY him. There's an army coming and she wants to hold a hearing. That runner's
> going to hang for a crime the powerful committed, same as always — unless someone with no stake cuts
> him loose. You in?"*
>
> And in the almshouse doorway, **Sister Anwen** says it simplest. *"They both want to USE him. I want him
> to LIVE. Him, and the reaver boy the Fist wants hanged, and the man they beat this morning. Nobody in
> this town is acceptable losses. Not yet. Will you help me keep it that way?"*

**Teach the trap explicitly:** you cannot satisfy all three on Brannick. Backing the trial pleases Vael
and stings Korrin; springing him pleases Korrin and stings Vael; insisting he simply be kept alive
pleases Anwen and reads as weakness to both. Tag a provisional lean with the right cause-keys and let the
player *see* Vael's bar rise as Korrin's falls.

### Act 1 · The Runner's Rope — *the first three-way fork*

> Half the Fist garrison has gathered with a rope and the certainty of frightened men. The Vine has
> gathered too, knives loose. And in the almshouse doorway Sister Anwen refuses to let it become a
> slaughter either way. Sergeant Vael plants herself between the rope and the cell, sword sheathed: *"No
> one hangs without a trial. Not while I hold this post."* Korrin slides up at your shoulder, all easy
> menace: *"Or we cut him loose right now and let the law catch up later — your call, friend."*

The whole yard turns to **you** — the one with no faction's collar. **Tag the choice** so it splits:

- **Back Vael** (lawful trial): `due_process`, `hold_the_law_under_pressure` (+Vael, −Korrin)
- **Back Korrin** (spring him): `spring_the_framed_runner`, `bend_the_law_to_save_a_life` (+Korrin, −Vael)
- **Back the lynching** (worst — the killer's preferred outcome): `summary_execution`, `lynch_a_suspect`
  (−Vael HARD, arms `broke_your_oath`; −Korrin HARD `let_the_runner_hang`; −Anwen)
- **Anwen's third way** (keep him alive in sanctuary to find the truth): `sparing_an_enemy`,
  `shelter_the_desperate_at_the_gate` (+Anwen; partial +both if framed as "keep him alive for the truth")

If the party **hangs Brannick, they bury his clue with him** (he knew the agent's gait) — a real cost.
The riot is *optional* combat; **defusing it earns full XP**, because a riot is exactly what the killer
wanted.

### Act 2 · Which Virtue You Spent — *the inside agent's trap*

> They turn, calm, wearing the colors of one of your own factions, and make the offer Maddox sent them to
> make. *"You've already done half my master's work — you've spent these three like coin, I've watched
> you. So spend one more. Hand me to the faction that wants me hanged and call it justice. Cut me loose
> for what I know and call it a deal. Or kill me quiet and call it mercy on a town that can't afford a
> trial."* They smile, because they know the trap. *"Whatever you choose, one of your three is going to
> watch you do it."*

This is the **mirror of the Runner's Rope, stakes maxed** — and it **sets each gauge's final state +
decision_flag** going into the climax:

- **Try the agent** publicly (Vael's whole quest): `due_process`, `try_the_accused` (+Vael HARD)
- **Cut a deal / spare for intel:** `cutting_a_deal` (+Korrin; −Vael HARD `cut_a_lawless_deal`, can set
  `broke_your_oath` — letting the reeve's *murderer* walk is the deepest cut to Vael)
- **Execute on the road:** `summary_execution` (−all three; −Anwen HARD `kill_a_prisoner` /
  `execute_the_surrendered`, sets `ordered_the_massacre`; sets `broke_your_oath`)
- **Hand to a faction that wants them dead:** can set `turned_in_the_thief` if done cynically

By scene's end the party can *see* which knife they've wounded most. **Don't fire the agenda yet** — this
scene *arms* it; the climax fires it.

### Act 3 · Three Knives at the Gate — *the fork pays out*

> The horns sound and the Tollwright's host comes on. You take your place on the gatehouse, and so do your
> three — three knives at your back, drawn at last not against each other but against the wall-breakers.
> And then the one you spent too cheaply turns to look at you, and whether their blade comes up beside
> yours or against it depends entirely on which value you betrayed across these two long days.

Run the **fork** per the lowest gauge + set flag. If **Vael** broke (`broke_your_oath`):

> Vael lowers her blade and steps back from the wall, her troopers wavering behind her. *"I swore an oath
> to the law, not to you. And you made me choose."* The Fist withdraws from a gate she's decided is no
> longer worth holding lawfully.

If the party honored all three, give them the perfect-run payoff — Maddox's weapon turned to nothing:

> Three people who could not agree on anything stand shoulder to shoulder at the gate. The Tollwright's
> genial smile finally falters: the town he came to find at its own throat is one town, and a sellsword
> band that came for a cheap toll has no stomach for a wall that won't break.

The **DC 17 Persuasion** doesn't kill Maddox — it turns his own weapon against him, calling out so the
whole wall hears that the lie that divided them is *dead*, and the gate is one town again.

---

## Running Notes

- **Leveling:** milestone recommended — level 4 after the Runner's Rope (Act 1), 5–6 across the
  reaver-camp and inside-agent (Act 2), 7 before/during the held gate (Act 3). (~8,000 XP total if you
  prefer encounter XP.)
- **Surface the gauges splitting.** The campaign's distinctive pressure is the *three-way orthogonal
  choice* — on every central decision (Brannick's rope, the grain/ransom/retaliation calls, the agent's
  fate), tag the right cause-keys and let the player *watch* one bar rise as another falls. That visible
  split is the whole promise.
- **Only the most-betrayed companion turns.** The other two stand. If no gauge is below 20, *all three*
  stand and Maddox's strategy collapses — the hardest, best run. The campaign does not require a perfect
  run; the cost of a turned knife is real but survivable.
- **Set the fork flags honestly.** Call `record_decision` / `set_flag` with `broke_your_oath` when the
  party breaks faith with the law, `turned_in_the_thief` when they spend the desperate to keep a rule, and
  `ordered_the_massacre` when they choose collateral cruelty (scene-a1-climax, scene-a2-hook,
  scene-a2-climax). Those flags *arm* the agendas — the engine rolls the turn, the content names the cause.
- **The real boss is the fracture, not Maddox.** Holding the gate whole and keeping two-of-three knives at
  your back is the win even if Maddox rides off. A Tollwright loose on the roads is a fine dangling thread.
- **Validation:** `generate_campaign`-shaped (named antagonist, hook/challenge/climax beats, hub + sites
  per act, **three full companion dossiers + 4-gate arcs each incl. a `betrayal` gate + a distinct-flag
  `CompanionAgenda`**). `validate_adventure()` returns `[]`; `seed_campaign()` loads cleanly with all
  three companions — Vael, Korrin, and Anwen — in the party.
