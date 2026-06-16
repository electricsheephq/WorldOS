# The Ledger of Mercy

*A WorldOS Baldur's Gate campaign — SRD 5.2, levels 3 → 7, ~4 sessions.*
*Set in the bundled `baldurs-gate` world seed (post-BG3, 1492 DR — the winter after the Absolute fell).*
*Original fan-content prose on SRD primitives. Released under the same terms as the baldurs-gate world seed (Wizards Fan Content Policy + Larian for BG3 elements; SRD 5.2 / CC-BY-4.0 rules). NOT official, never sold.*

---

## The Pitch

In the hungry winter after the Netherbrain fell, the **Lower City** of Baldur's Gate is full of the
ruined — tadpole-survivors no one will hire, war-widows, refugees from the streets the brain
breached. And into that wound steps a mercy: the **Ledger House** on **Gratitude Row**, a debt-relief
almonry where the beloved **Almoner-General Lucan Ferreth** pays off a desperate soul's debts, feeds
their children, and asks only that they sign his **Book of Grace** and *serve the House until the
kindness is repaid.*

The kindness is never repaid. Ferreth has quietly turned charity into bondage — the signers become
the **Gilded Hand**, an off-the-books labour and enforcement network that moves Guild smuggling,
breaks rival almshouses, and disappears anyone who reads too far. He is the warmest man in the Lower
City and he owns more of its poor than the patriars own of its land. And he means, this very winter,
to make it **law**.

## The Hidden Antagonist

**Almoner-General Lucan Ferreth, "the Open Hand of the Lower City."** Not a sneering swindler — a
silver-haired old man who weeps at funerals he paid for and means every kindness. After a lifetime
watching the Gate let its poor freeze and starve and breach under the Netherbrain, Ferreth decided
**freedom is a luxury the desperate cannot afford** — that a person *owned and fed* is better off than
a person *free and dead*. The Book of Grace is, to him, the most loving institution in Baldur's Gate.
He does not think he is a villain. He thinks he is the **only mercy** these streets ever had — and he
will price you, gently, the moment he meets you.

- **Public face:** the city's most beloved benefactor, never thanked with anything but frightened,
  flat-eyed gratitude. His Book has pages no clerk may read; debtors who "complete their service" are
  never seen again.
- **Lieutenant:** **Veil**, his velvet-voiced collections-mistress — the *first* signer of the Book,
  who chose the whip where others chose the collar. She "transfers" readers and never raises her
  voice. Recurs across all three acts.
- **The reveal** lands in two stages: that the *kindness is a cage* (Act 1's vault, Act 2's math), and
  — worse — that **Ferreth knows and means it**, a true believer in benevolent ownership, when the
  party finally meets him without the mask at the Almsmoot.
- **Stats (Act 3 only):** reflavored **Mage** (AC 15, HP 81, CR 6) — a social-control villain who
  *talks and buys* before he fights. *Hold Person* and *Suggestion* are "the weight of a debt";
  *Counterspell* is "the House does not permit that."

## The Companion — and the Fork

**Sergeant Ondine Marsh** (Fighter 3, `companion-default`) — a broad-shouldered ex-**Flaming Fist**
sergeant, cashiered in the lean months when the company couldn't pay its dead's pensions. She buried
her wife **Calla** and her brother out of Ferreth's almonry; he cleared the gravediggers' debt, fed
her, and slid the **Book of Grace** across the table when she had nothing left to refuse with. **She
signed.** Now she is *bonded* — a quiet, decent, dangerous woman wearing the Gilded Hand's mark inked
at her collarbone like a collar she's learned not to feel.

She is the campaign's moral core **and a real fork**:

| Path | What it takes | What she becomes |
|------|---------------|------------------|
| **Loyalty** (gates 25 → 50 → 75) | Free the bonded, refuse Ferreth's coin, honor her dead, treat the signers as people | She breaks her own bond, captains the freed at the Almsmoot, and turns the Gilded Hand against itself — the soldier she was ashamed she'd stopped being. |
| **Betrayal** (gate 18 + agenda) | Treat her as muscle, trade the bonded for expedience, take Ferreth's coin / let him **buy her back** (set the `took_ferreths_coin` flag) | Her bond reasserts. She does the cold soldier's math — her kin over a party that gave her nothing better — and **turns, as a real attack**, at the worst moment. |

Her `CompanionAgenda` (`attitude_below 18`, `decision_flag: took_ferreths_coin`) makes the turn a real
engine event, not a line in a prompt. Honored, she's the fiercest ally in the Gate; curdled, she's the
heartbreak the party *earns*.

## Structure — Hub & Three Spokes

The party returns to **the Elfsong Tavern & Gratitude Row** between acts to rest, level, and slowly
learn that the most blessed name in the Lower City is the one to fear. One site per act radiates from
the hub.

| Act | Levels | Site | The Beat |
|----|--------|------|----------|
| **1 — The Book of Grace** | 3–4 | **The Ledger House** | Work the almonry: the un-repayable "service debt" math, the turnable head-clerk, Halsa's pages hidden in the laundry, and the warded lower vault where the first bonded are kept. Veil fronts the House and withdraws — *"the Almoner so dislikes an unpaid debt."* |
| **2 — The Drowned Counting House** | 4–6 | **The flooded Guild vault** | Descend below the Lower City to the bonded-labour underbelly. Free **Halsa** (the witness who carries the full truth *and* Ondine's leverage), recover the duplicate ledgers, and survive the fork's loudest beat: **Veil offers to buy Ondine back.** |
| **3 — The Steward of the Poor** | 6–7 | **The Almsmoot Hall** | Ferreth has gone to the patriars' gala to be chartered **Steward of the Poor** — which makes the Gilded Hand *legal*. Crash the marble, table the evidence, race the **vote-clock**, and break the hold before mercy becomes statute. |

Each act exercises **exploration + social + combat**. Diplomatic, stealth, and political resolutions
earn full XP — swaying the vote or turning the bonded counts as overcoming the finale.

## Monsters per Act (all bundled SRD stat blocks, CR-tuned)

- **Act 1 (lv 3–4):** Gilded Hand enforcers = **Thug → Tough** ×3 (CR 1/2) + optional **Spy** (CR 1).
  Veil withdraws.
- **Act 2 (lv 4–6):** **Veil = Assassin** (CR 8 mid-boss) *or* **Spy** for a light table + **Tough**
  ×3 + **Swarm of Rats** (the drowned vault's vermin). Optional **Wererat / Warrior Veteran** Guild
  muscle.
- **Act 3 (lv 6–7):** **Ferreth = Mage** (CR 6, a *social-control* boss) + **Veil** (Assassin/Spy, if
  she survived) + **Tough** ×3 + gallery **Spy / Warrior Veteran / Flaming Fist Sergeant**, against a
  **vote-clock**.

Every name resolves through `spawn_monster` / the bundled bestiary. Per-scene scaling notes tune each
fight to party size.

## The Real Boss — the Gilded Hand, Not Ferreth's HP

Ferreth is the human architect the party confronts, but **the real antagonist is the Gilded Hand** —
the network of bonded souls and the bondage it represents. Ferreth *prefers to talk and to buy*, and a
Ferreth who slips out a side door to "reform his mercy elsewhere" is a fine dangling thread. The **win**
is breaking the **hold**: killing the Steward-of-the-Poor charter and freeing the bonded so the Hand
collapses into the victims it was made of. The **loss** state is the Act 3 **vote-clock** running out —
the charter passing and the Book of Grace becoming the *law* of the Gate. Three win-conditions keep the
finale open to any party: **sway the vote**, **turn the bonded against the Hand**, or **put the Hand
down by force**.

## How It Ends

Dawn over a changed Gratitude Row. The hungry are still hungry — but the bread is **Brother
Cassian's** now, given the old way, with no book to sign and no hand to ink at the collarbone. The
charter is dead in committee; the Gilded Hand has come apart into the frightened, grateful people it
was always made of. The party decides how the Lower City remembers it — as the night they freed a
hundred bonded souls, or as the night they unmasked the most *loving* man in Baldur's Gate, a true
believer who took the poor *collar and all* because the city never would, and who may, even now, be
somewhere warmer than this, certain he was right. *The Book of Grace is closed. But a book is not a
conscience.*

---

## Sample Scene Prose

> *(These expand the read-aloud + dm_notes in `adventure.json` into the playable, BG3-register prose the
> DM voices at the table. Boxed text is for the players; the rest is staging.)*

### Act 1 · The Sister Who Reads — *the hook, at the Elfsong*

> The Elfsong's dead elf-ghost is singing tonight — a thread of grief in the rafters that the regulars
> have learned not to hear. In the quietest corner, a wiry woman with a grey ceremorphosis-scar running
> down her neck sets three worn silver pieces on the table between you, one at a time, as if each one
> costs her a tooth.
>
> *"They told me you don't owe him,"* she says, low. *"Everyone in the Lower City owes Almoner Ferreth
> something. Not you. That's why I'm here."*

Outside, even at this hour, the dawn queue is forming on Gratitude Row — the ruined and the scarred,
thanking a silver-haired old man's name like a prayer. **Lean on the dramatic irony.** Every NPC blesses
Ferreth; Pell's lonely fear is what marks *her* as strange. The two quiet tells: signers thank him with
**flat, frightened eyes**, and *"transferred to the country chapter"* is a phrase that doesn't survive a
follow-up question.

**Ondine is at the table** — and she's bonded to Ferreth, though the party doesn't know it yet. When his
name comes up she goes still. A sharp player who makes the **Insight (DC 13)** catches the ex-sergeant
*flinch* at "the Book of Grace." Give her a guarded line:

> Ondine doesn't look up from her drink. *"A missing clerk. In Ferreth's House."* A long pull. *"You'll
> want to be careful how loud you say his name in here. Half this room would die for the man who fed
> them. The other half already did."*

**Brother Cassian** (frail, holy, weary — *not* Ferreth's warm register) gives the first lore-crumb:

> *"I gave bread for thirty years, child, and never once asked a name in return. That,"* — he nods at the
> grateful queue — *"asks the name first. A kindness with a signature on it isn't a kindness. It's a
> contract. And contracts have a back page."*

### Act 1 · What's Kept Downstairs — *the vault climax, meeting Veil*

> The stair ends in a low brick vault that the soup-and-bread smell never reaches — here it's tallow,
> damp, and fear. Bonded signers in House grey labour by lantern-light at sorting-tables stacked with
> Guild-marked cargo, none of them meeting your eyes; against the far wall, a row of barred holding-cells
> waits, two of them occupied.

Down the stair behind the party, unhurried, comes **Veil** — widow's grey, gloved hands folded, a
transfer-order half-written in one of them:

> *"The Almoner so dislikes an unpaid debt,"* she says, gently, as though you were expected guests. *"And
> you've run up rather a large one, reading what wasn't yours."*

**Veil withdraws; she does not die here.** She names the party's "debt," promises the House always
collects, and glides back up the stair on round 2–3, leaving her **Gilded Hand enforcers** (Tough ×3) to
cover her. **The bonded signers are not enemies** — they're frightened victims who will *not* fight the
party. This is **Ondine's moral knife**: she puts herself between the party and the cages and will not
raise a hand against a signer.

> Ondine steps in front of the nearest cell, broad back to the bars, blade still sheathed. *"These ones
> don't fight. You hear me? They signed the same book I did."* Her jaw works. *"Cut the muscle if you have
> to. Not them. Never them."*

How the party treats the bonded here is a major approval beat (`free_the_bonded`,
`spare_a_frightened_pawn` vs `treat_the_bonded_as_criminals`). A decent showing can open Ondine's **trust
gate (25)**.

### Act 2 · Collecting the Debt — *the offer, and the fork's loudest beat*

The warm, dry counting-room sits above the black water like a held breath, ledgers ranked floor to
ceiling. **Veil is already there, waiting** — and she opens with *the offer*, not a blade:

> *"You've cost the House a great deal of trouble,"* Veil says, with no anger at all, *"and the Almoner is
> a forgiving man. He's authorized me to settle ALL your debts tonight — the laundress's, the clerk's..."*
> Her gaze slides, gentle as a knife, to your companion. *"...the sergeant's dead. Her wife's grave, paid
> clean. Her people, safe forever. All it costs is that you walk away, and she stays."*

**This is the fork.** Refusing on Ondine's behalf (`refuse_a_bribe`, `free_the_bonded`) sends approval up
hard. Taking the deal — or even *hesitating audibly* — pushes toward the **betrayal gate (18)** and the
`took_ferreths_coin` flag. If the party stands by her:

> Ondine doesn't take her eyes off Veil. *"My wife's name is Calla,"* she says, very quiet. *"You don't
> get to spend her. Not to buy me, not to buy them."* She draws — finally, a soldier again. *"Tell Ferreth
> the debt's paid. In full. Tonight."*

If the party *abandons* her — or has already pocketed Ferreth's coin — her **agenda fires** and you run
her turn at Veil's shoulder as a *real attack*, sick with it:

> *"You should have made me a better offer than freedom I can't afford,"* Ondine says, and she will not
> meet your eyes, and the blade comes up anyway.

### Act 3 · Breaking the Hold — *the mask falls, in marble*

The party tables it in front of the whole bright room — the drowned ledgers, the freed bonded baring the
gilded hands inked on their skin, Halsa's clear clerk's voice naming the dead. And **Ferreth lifts his
silver head with no fear at all:**

> *"You think you've found a crime,"* he says, soft enough that the whole hall leans in to hear the saint
> speak. *"You've found a MERCY. I took them in — collar and all — when this city would have let them
> freeze in the gutter you crawled out of. Fed. Housed. OWNED, yes, and safe in it."* His warm eyes move,
> deliberately, to your companion. *"Sergeant. I cleared your wife's grave. I can clear the rest —
> tonight, in front of these witnesses, a free woman. All you have to do is stand with the hand that fed
> you."*

**The horror is that the mask was real.** Ferreth is not a corrupt steward to be shamed straight; he is a
*true believer*. Run the **vote-clock**: each round the party fails to land the exposure, his patriar
allies edge the charter toward passing. Three ways to win — **sway the vote** (Investigation/Persuasion
before the Council), **turn the bonded** (Intimidation; Ondine leads it on the loyalty path), or **force**
(Ferreth = Mage, a controller who *flees a lost vote rather than die a martyr*). The **DC 17 Persuasion**
doesn't redeem him — it strips the room's *love* off him in real time:

> *"Owned and safe,"* you say to the marble, loud enough for the back benches. *"That's a cage with a
> soup-kitchen on the front. Look at their hands, my lords. He'd ink one on every poor soul in the Gate
> and call it grace."*

If Ondine reached her **loyalty gate (75)**, give her the line that turns the gallery:

> Ondine steps into the candlelight and pulls her collar down so every patriar can see the gilded hand at
> her throat. *"I was the most loyal thing he owned,"* she says. *"And I'm telling you what his mercy is
> worth."* She turns to the liveried enforcers along the wall — her own, once. *"Put them down, lads. The
> book's closed. Come home."*

---

## Running Notes

- **Leveling:** milestone recommended — level 4 after the Ledger House, 5–6 across the Drowned Counting
  House, 7 before/during the Almsmoot. (~8,000 XP total if you prefer encounter XP.)
- **The bonded are victims, never enemies.** Freeing and reassuring them is the campaign's heart and
  Ondine's chief approval surface; a "combat" that ends with cages opened instead of bodies is a *better*
  win, not a softer one.
- **Ferreth can be *reached*** at the climax (a hard DC 17 Persuasion) — not redeemed, but stripped of
  the city's love in front of the Council, collapsing his support and his composure. Reserve the full
  effect for genuine brilliance; the default is breaking the vote and freeing the bonded.
- **Set the fork flag honestly.** Call `record_decision` / `set_flag` with `took_ferreths_coin` when the
  party accepts Ferreth's or Veil's money or stands aside in a buy-back beat (scene-a2-collect,
  scene-a3-climax). That flag *arms* Ondine's betrayal agenda — the engine rolls the turn, the content
  names the cause.
- **Validation:** `generate_campaign`-shaped (hidden antagonist, hook/challenge/climax beats, hub + one
  site per act, a full companion dossier + 4-gate arc incl. a `betrayal` gate + a `CompanionAgenda`).
  `validate_adventure()` returns `[]`; `seed_campaign()` loads cleanly with Sergeant Ondine Marsh in the
  party.
