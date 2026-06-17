# The Physician of Hollow Mile

*A WorldOS gothic-horror campaign — SRD 5.2, levels 3 → 7, ~3–4 sessions.*
*A deliberate tonal departure from the engine's heroic/intrigue defaults: run it slow, cold, and grieving — dread over spectacle, mercy over monster-mash.*
*Original fan-content prose on SRD primitives. Released under the same terms as the bundled world seeds (SRD 5.2 / CC-BY-4.0 rules). NOT official, never sold.*

---

## The Pitch

**Hollow Mile** is a fen-locked hamlet of forty souls strung along one sunken road, and for three
winters it has been dying of a wasting plague the locals call the **Grey Quiet** — a sickness that does
not kill so much as *still*. The afflicted go cold and slow and finally lie down in the reed-beds and do
not get up. And on the **third night** after they lie down, they *rise*, grey and patient and wrong, and
walk back toward the houses they loved.

Between the hamlet and that horror stands one woman: **Doctor Eline Mourn**, a plague-doctor in a long
oiled coat and a beaked leather mask, who came three winters ago and has held the Grey Quiet to a slow
burn with a tincture of her own devising — the **Stillwater Draught**. The village worships her. What no
one knows is that the Grey Quiet is *hers*: the failed first version of the very cure she now doses them
with, a draught she invented years ago to save one dying child — her ward, **Wren** — that unmade the
girl into the first grey-walker instead, and seeped from that one body into the water and the fen. She is
not the plague's villain by malice. She is its **mother**, dosing a wound she opened, certain that one
more refinement will close it. And beneath the bog, in a drowned chapel-crypt, the **Stillwater Spring**
still runs with the original cursed reagent — and the grey-walking remains of Wren still wait at its
heart, the proof and the source and the thing Eline cannot bring herself to burn.

## The Hidden Antagonist

**Corliss Vane, "the Reed-Factor of Hollow Mile."** Not a robed cultist — an affable fen-smuggler with a
barge-pole's reach and a poacher's patience, kin to the dying Vanes and seemingly the most *helpful* man
in the Mile. Two winters ago he discovered the bog's grey water fetches a fortune upriver as a "stilling
draught" among the dying rich, and he has been quietly grave-robbing the walkers and skimming the Spring
ever since — shipping the curse out by the bottle, the reason the Grey Quiet has begun to spread *beyond*
Hollow Mile. He means to seize the Spring **whole** and sell it forever, and he'll use the party (or
Eline) to break the chapel's last wards and open the source he can't reach himself.

- **Public face:** the put-upon kinsman who "just moves reeds," lends a cart, knows the safe paths — and
  warns everyone off the bog *"for your own good"* (the first note that sounds less like care than a man
  guarding a claim).
- **The reveal** lands across Act 2: the helpful factor is the one *spreading* the Grey Quiet for profit,
  the grave-robbed reagent and the low-riding night barges are his, and three towns upriver are already
  going grey from his bottles.
- **Stats (Acts 2–3):** reflavored **Bandit Captain** (AC 15, HP 65, CR 2) — twin gutting-knives, a
  thrown net, a grey-water flask he smashes to raise a walker as cover. A cornered profiteer protecting a
  claim, who *bargains before blades* and **flees a lost Spring rather than die for it.**

## The Companion — and the Fork

**Doctor Eline Mourn** (Cleric/Life Domain 3, `companion-default`) — the masked plague-doctor holding
the Grey Quiet at bay, and the **secret mother of the plague itself**. She believes in the body the way a
faithless person believes in arithmetic: that flesh is a problem with a solution, that suffering is data,
that there is no soul-sized wound she cannot, given time and a clean enough table, close. She is gentle
with the suffering and merciless with sentiment — she will end a hopeless case cleanly rather than let it
linger. She joined the party as the only hands in the hamlet that don't already worship or fear her, and
because some buried part of her hopes they will make her finally do the thing she cannot: go down to the
Spring and burn it, Wren and all.

She is the campaign's moral core **and a real fork** — but note the fork is *cold*, not hot:

| Path | What it takes | What she becomes |
|------|---------------|------------------|
| **Loyalty** (gates 20 → 40) | Trust her with the truth; treat the grey-walkers as patients; ease hopeless cases cleanly; help her **unmake** the Spring rather than hide it | She confesses, leads the party to the Spring, and performs — at last — the hard mercy she came three winters to do. She walks out free, grieving but whole. |
| **Betrayal** (gate 15 + agenda) | Torch the walkers wholesale; let the sick burn to be safe; side with Corliss's profit; and — the seal of it — **seize the cursed reagent** instead of destroying it | She does **not** snap. She *decides*, clinically, that the party has become the disease — carriers who will spread her failure across the river — and turns to **excise the contaminant**, grieving and certain. |

Her `CompanionArc` has **three gates** — `loyalty` @20, a **quest-LINKED** `personal_quest` @40 (linked
via `quest_arc_id` → `cqarc-eline-stillwater`, the cure-that-became-a-curse arc), and a `betrayal` @15 —
and a `CompanionAgenda` on the **`prize_seized`** trigger (not `attitude_below`). When the party *seizes*
the reagent (the engine flag `prize_seized` is set), her turn fires **deterministically** and she comes
for the party as a physician removing a contaminant. Honored, she's the hands that finally close the
wound; grasping, she's the clinical verdict the party earns.

> **Engine note.** The `prize_seized` trigger fires on `campaign.flags['prize_seized']` alone — the
> agenda's `decision_flag` (`let_the_ward_burn`) is *not* read by this trigger (the engine reads
> `decision_flag` only for `attitude_below` agendas). It's carried to record the campaign's central choice
> and to mark the path that *avoids* the turn: **let the ward burn = unmake the Spring = don't seize =
> she never turns.**

## Structure — Hub & Three Spokes

The party returns to **Mile-End & Doctor Mourn's Surgery** between acts to rest, level, dose the dying,
and stand the third-night watch — and slowly learn that the kindest hands in Hollow Mile made the wound
they're healing. One site per act radiates from the hub down the chapel-path into the bog.

| Act | Levels | Site | The Beat |
|----|--------|------|----------|
| **1 — The Third-Night Watch** | 3–4 | **The Reedfen & the Sexton's Graves** | Walk the dying boy to the surgery; read the fen — tampered graves, night-barge tracks, a grey-water bottle that shouldn't exist; then stand the **third-night watch** as the grey-walkers wade up homeward, and one of them is someone you've met. Eline treats them as *patients*, not monsters. |
| **2 — The Drowned Chapel** | 4–6 | **The half-submerged bog-chapel & warded crypt** | Descend the chapel-path to Sister Ovick's failing wards. The **midpoint reversal**: the cure is the curse, the source is the Spring below, the first walker is a *child* — and Eline **confesses** (her quest-linked personal-quest gate). Corliss's hand is forced and the wards break. |
| **3 — The Stillwater Spring** | 6–7 | **The drowned crypt's cold pool** | Down to the source, consecrating fire in hand. Wren asks Eline to "save her again." **Unmake the Spring** (cure the fen, release the child) or **seize the reagent** (and fire Eline's clinical turn) — with Corliss racing to take the Spring whole. |

Each act exercises **exploration + social + combat**. Merciful and investigative resolutions earn full XP
— easing the walkers to rest, re-laying the wards, or **unmaking the Spring** counts fully as overcoming
each act.

## Monsters per Act (all bundled SRD stat blocks, CR-tuned)

- **Act 1 (lv 3–4):** grey-walkers = **Zombie** ×3 (CR 1/4), reflavored grey and homeward; one is Halda's
  dead husband. A *grief* beat, not a slasher.
- **Act 2 (lv 4–6):** **Corliss = Bandit Captain** (CR 2) *or* **Spy** for a light table + **Tough** ×3
  (bog-runners) + **Zombie** ×3 (rising walkers). Optional **Ogre Zombie / Ghoul** first-risen horror.
- **Act 3 (lv 6–7):** **Corliss = Bandit Captain** (CR 2) + **Tough** ×3 + **Zombie** ×4 (a *rising-walker
  clock*), with the **unmake-vs-seize** choice and — only if her agenda fires — **Doctor Eline Mourn**
  (Cleric/Life Domain, leveled with the party) as the heartbreak adversary.

Every name resolves through `spawn_monster` / the bundled bestiary. Per-scene scaling notes tune each
fight to party size.

## The Real Boss — the Source, Not the Smuggler's HP

Corliss is the human predator who'd *sell* the horror, but **the real antagonist of the climax is the
Stillwater Spring and the choice at it.** Corliss prefers to bargain and flees a lost Spring rather than
die for it — a Corliss loose upriver with a few bottles is a fine dangling thread. The **win** is
**unmaking the Spring**: consecrating fire to the cursed reagent so every grey-walker lies down for good
and the Grey Quiet ends at its source. The **dark state** is the reagent **seized** — the curse kept
alive as a prize, which fires Eline's clinical turn and makes the party the disease she diagnosed. Don't
make this a fight to a smuggler's death; make it a choice about **what saving someone is worth**.

## How It Ends

Dawn over Hollow Mile, and for the first time in three winters the reeds are *only reeds* — no singing,
no grey shapes wading homeward. The Spring is a scorched hollow under the drowned chapel; the walkers lie
where they lay down, dead the ordinary way at last, and **Sexton Brell** can finally dig a grave and
trust it to stay closed. The party decides how the hamlet remembers it — as the winter strangers came
down the sunken road and unmade the cold thing under the bog, or as the winter they learned the kindest
hands in the Mile had *made* the wound they were healing, and chose, at the last, whether the cure was
worth the child at the bottom of the Spring. *The Grey Quiet is ended. Doctor Eline Mourn's mask hangs by
the surgery door — or it does not, depending on what the party let her become.*

---

## Sample Scene Prose

> *(These expand the read-aloud + dm_notes in `adventure.json` into the playable, slow-and-cold prose the
> DM voices at the table. Boxed text is for the players; the rest is staging.)*

### Act 1 · The Boy Going Grey — *the hook, on the sunken road*

> The sunken road into Hollow Mile runs below the level of the fen, a green tunnel of head-high reed that
> whispers on both sides though there is no wind. Where the road meets the first cottages, a broad woman
> with reed-scarred hands steps into your path and does not move, because in her arms is a boy of perhaps
> fourteen, grey at the lips and shivering with a cold that has nothing to do with the season.
>
> *"You're outsiders,"* she says, flat, like it's the only good news she's had in a month. *"You don't
> have the fear yet."*

**Lean on the dread, and keep it physical** — the windless whispering reed, the cold that isn't weather,
the village's refusal to *name* the third-night rising. Halda (`npc-female-1`) doesn't want a cure she
can't buy; she wants strong arms to walk her boy down, and someone unafraid to **stand the third night**
if the worst comes. At the Mile's end, the masked physician watches from her door:

> The figure in the beaked mask takes Tomas from his mother's arms with hands that are clean and exact
> and do not shake. *"Grey Quiet,"* she says — a statement, not a question. *"Third stage, maybe fourth.
> I can slow it."* The mask turns to you. *"I can always slow it. I have never once stopped it. If you've
> come to help, understand that first, so you don't hope at me. Hope is the one thing I can't dose."*

**Eline never removes the mask.** A sharp **Medicine (DC 13)** reveals the Grey Quiet is *unnatural* — a
freezing of the will, a grey shimmer in the blood no sickness should have — and earns her first flicker
of regard (`trusting_evidence_over_superstition`).

### Act 1 · What Comes Back — *the third-night watch*

> Past midnight the singing of the reeds changes pitch, and they come — slow, patient figures wading up
> out of the black water, grey-skinned and unhurried, water streaming off them, their faces calm as
> sleepers. They do not lunge. They *walk*, steady and homeward, toward the lamplit Mile and the chapel
> beyond it, as if they are only late getting home.

The lantern catches the nearest one's face, and **Halda makes a sound you will not forget** — because it
is the man in the portrait over her hearth, two winters in the ground. **The grey-walkers (Zombie ×3) are
victims, not monsters**: they shamble *home*, grappling and dragging toward the water more than savaging.
This is **Eline's moral knife** — she lifts her bag, not a blade:

> *"They are not attacking,"* Doctor Mourn says, very evenly, stepping between you and the nearest grey
> face. *"They are PATIENTS. A body can be saved and still be lost, and that is what you are looking at.
> Ease them. Lay them down. But do not — "* her voice does not rise, which is worse — *"do not burn them
> to feel safe. I will remember if you do."*

Easing them to rest (**Religion DC 13**) and honoring Halda's dead wins Eline hard and opens her
**loyalty gate (20)**; torching them wholesale over her plea is the first push toward her **betrayal gate
(15)**. The walkers all turn the same way — toward the chapel — *the* clue the party carries into Act 2.

### Act 2 · The Ward-Keeper's Line — *the reversal, and the confession*

> On the one dry step of the drowned chapel a single candle burns, and behind it stands a gaunt woman in
> a lay-keeper's grey, a line of salt across the crypt-stair at her back. From somewhere far down it,
> faint and patient and unmistakably a child's, a voice is singing the same tune the reeds sing.

**Sister Ovick** (`npc-female-1`) is the moral counterweight — the woman who has spent her life simply
**holding the line** so the curse spreads no further, where Eline would refine the wound and Corliss
would sell it. She names the unbearable shape of it: the source is the Spring below, and the first walker
is a *child*. And she turns to the doctor:

> *"You know what's down there, doctor. You've known the whole time."* Ovick's voice goes very gentle and
> very hard at once. *"Are you finally going to help me end it — or just kneel by it and weep again?"*

If the party has earned it (**Insight DC 14**), this is where **Eline confesses the whole of it** — Wren,
the first draught, the Spring, the cure she has never been able to perform — her **quest-linked
personal-quest gate (40)**:

> *"Her name was Wren,"* Eline says, and the clinician's voice finally cracks clean through. *"She was
> nine, and she was dying, and I made something to save her. It did not save her. It made... this. All of
> this. Every grey face in that water is a copy of the first mistake I ever refused to bury."* She looks
> at you through the mask. *"I cannot burn her. Three winters, and I cannot. So I am asking you to come
> down there with me — and if I kneel by that cold water and lose my nerve again, you finish it. Whatever
> I say."*

### Act 3 · The Unmaking — *the cold pool, the choice*

> In the center of the still grey water, neither living nor dead, a small grey figure sits with her knees
> drawn up, singing the tune the reeds sing, and when your light falls on her she lifts a child's face,
> patient and cold and glad to see you. *"You came,"* Wren says, in a voice like water under ice. Her grey
> eyes find Eline, frozen on the last step. *"You came BACK. You're going to save me again, aren't you?"*

This is the campaign's last and heaviest word. **Unmake the Spring** — consecrating fire to the reagent,
holy water and a rite of rest to break the binding and *release* Wren (**Religion DC 15**) — and the Grey
Quiet ends at its source, every walker in the fen laid down, at the cost of the child. Or **seize the
reagent** — and `prize_seized` fires, and Doctor Mourn turns:

> *"I'm sorry,"* Eline says, and she means it, and that is the worst part. She does not raise her voice.
> She sets down her bag and takes up the consecrating brand instead, and her eyes are wet and perfectly
> certain. *"You were going to carry it out of here. Sell it, or study it, or just keep it, and it would
> spread, the way it always spreads. I made one mistake. I will not let you make it a thousand. Hold
> still. This is the kindest cut I have left."*

If the party reaches her in time (**Persuasion DC 15**) and gives her back her own words, she kneels,
takes Wren's grey hand, and performs — through her grief, not around it — the cure she came three winters
to do:

> *"You can't save me again,"* Eline tells the child, very softly, the mask off at last. *"I can only let
> you rest. I should have let you rest a long time ago. I'm here. Close your eyes."* And she sets the fire
> to the cold water, and for one clean moment Wren's face is only a child's face, before she goes.

---

## Running Notes

- **Tone first.** This is a tonal *departure* — gothic horror, not heroic adventure or city intrigue. Run
  it slow and cold: the windless singing reed, the cold that isn't weather, the dead who walk *home*. Dread
  over spectacle; mercy over monster-mash. The scariest thing in the campaign is a kind woman in a mask.
- **The grey-walkers are victims, never enemies.** Easing and laying them to rest is the campaign's heart
  and Eline's chief approval surface; a "combat" that ends with the dead laid down gently is a *better*
  win, not a softer one. One of them is Halda's husband — play the grief.
- **Leveling:** milestone recommended — level 4 after the third-night watch, 5–6 across the drowned chapel,
  7 before/during the Stillwater Spring. (~4,000–5,000 XP total if you prefer encounter XP.)
- **The fork is cold, not hot.** Eline does *not* rage. When her `prize_seized` agenda fires she turns
  *clinically* — a physician excising a contaminant, grieving and certain. Run it as heartbreak, never
  villainy. The single best insurance against the turn is a party she trusts to do the hard mercy.
- **Set the flags honestly.** Set the engine flag `prize_seized` when the party *takes* the cursed reagent
  at `scene-a3-spring` — that, alone, fires Eline's turn. Set the content flag `let_the_ward_burn` via
  `record_decision`/`set_flag` when they *choose* to burn the Spring and end Wren (the path that avoids the
  turn), so the chronicle records the choice. The engine does **not** read `decision_flag` for a
  `prize_seized` agenda — it's there to record the choice and mark the safe path, not to gate the fire.
- **The linked personal-quest.** Eline's `personal_quest` gate (`eline-gate-quest`, @40) is **linked** via
  `quest_arc_id` → `cqarc-eline-stillwater` / `stage_id` → `cqstage-eline-confession` (authored in the
  top-level `companion_quest_arcs` block). `seed_campaign()` folds the companion (with arc + dossier), the
  NPCs, locations and scenes, but does **not** itself seed a campaign-level `companion_quest_arcs` block —
  so at runtime register the arc with `set_companion_quest_arc` (or seed it from the world/ending) for the
  link to resolve live. Until then the gate latches a one-shot `link_error` (F06-11) and stays
  *locked-but-recoverable*; the personal-quest still plays from the gate's `note`. See `schema_notes`.
- **Validation:** `validate_adventure()` returns `[]`; `seed_campaign()` loads cleanly with Doctor Eline
  Mourn in the party (Cleric/Life Domain 3), the full dossier, a 3-gate arc (`loyalty` 20, a quest-linked
  `personal_quest` 40, a `betrayal` 15), and a `CompanionAgenda` on the `prize_seized` trigger
  (`decision_flag: let_the_ward_burn`). The companion and antagonist are original, non-origin characters;
  none of the seven banned BG3 origins appears as PC or companion.
