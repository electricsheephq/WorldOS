# Death & Re-roll — the D&D answer to "no save states"

Pull this doc the moment a PC drops to 0 HP, and whenever death is on the table. D&D
has **no save states.** When a hero dies you do not rewind the scene — you let it land,
then offer the table's true answer: **re-roll a new character at the same level and
continue the quest.** The world does not reset; the new hero earns their place in it.

## 1. The iron rule: NO rewinds, EVER
When a PC dies you do **not** undo the scene, retcon the killing blow, or "try that fight
again." The dice stand. This is the ultimate form of the non-negotiable you already run —
*a botched check changes the scene, it isn't smoothed over or re-rolled away.* Death is
that rule at its sharpest. Never offer "want to go back?" Never re-roll the killing blow.
Never resurrect by fiat — the engine refuses to heal the dead (`apply_healing` will not
revive a corpse), and you respect that. Resurrection magic that exists in the fiction
(a high-level spell, a divine boon) is a *story* the party earns, never a quiet undo.

## 2. Reading death vs dying (don't jump the gun)
`get_state` now tells you per party member:
- `dying: true` (+ a death-save tally) — **still saveable.** Stabilize, heal, or roll
  death saves. Do NOT offer a re-roll; the hero is bleeding out, not gone.
- `dead: true` — **gone.** This is the re-roll trigger. (Death lands on 3 failed death
  saves, massive damage, or a killing blow while already down.)
- `stable: true` — downed but no longer dying; revive them when you can.

Let the weight of a confirmed death *land* before you offer anything. A beat of silence,
the companion's reaction, the body where it fell. Then offer the way forward.

## 3. When the PC dies → offer "re-roll and continue"
Call:
```
reroll_character(campaign_id, dead_id, name="…", class_name="…", race="…",
                 abilities={...}, level=<defaults to the fallen hero's level>)
```
The engine, atomically: builds the new PC at the **dead hero's level** (same-level is the
rule — they must pull their weight in encounters tuned for the party), makes them the
active `kind=="player"`, and demotes the corpse to a memorial NPC out of the party. The
quest, the day, the locations, the lore, the factions, the surviving companions and their
memories — **all untouched.** You get back `{new_pc, memorial}`.

Roll or assign the new hero's abilities first (`generate_ability_scores` or by hand) and
pass them in. If you leave `class_name` blank you get a blank level-N sheet to flesh out;
pass a known SRD class for a full sheet (HP/saves/AC/features) auto-built at level.

## 4. The new hero EARNS their place — in the fiction
The engine builds the sheet; **you stage the arrival.** Never teleport a stranger into the
party mid-sentence. Give them a *reason* to be here and a *reason* to take up the quest:
- a prisoner the party frees in the very next room,
- a caravan guard who survived the same ambush that killed the fallen one,
- the dead hero's sibling, come to finish their work,
- a local who's been hunting the same villain for their own reasons.

Introduce them in the **next** scene. Let the party meet them. Let the bond start at zero
and be earned over beats — don't hand them instant trust.

## 5. Mid-combat death → finish the fight first
If the death happened mid-combat, the re-roll is a **post-combat beat**, not a mid-fight
swap. The engine already skips the dead in the turn order, so the fight continues cleanly
with whoever's left (companions fight on). When the dust settles, `end_combat`, narrate
the aftermath, *then* offer the re-roll — the new hero arrives in the next scene. Never
inject a fresh PC into an active initiative.

## 6. The fallen hero stays in the world
By default the dead PC becomes a **memorial NPC** — a body, a grave, a name the companions
speak. Honor it. A companion grieves. The party may choose to recover the gear (or leave
it). Don't pretend the dead character never existed; the world remembers its fallen heroes.

## 7. Gear & gold: lost with the body by default
A new character earns their own kit — `reroll_character` does **not** hand the new hero the
dead one's inventory or gold. Auto-gifting a level's worth of magic items cheapens the
death and breaks encounter math. If the fiction lets the party physically loot the fallen
one's corpse, transfer specific items by hand with `add_item` / `remove_item` and gold with
`adjust_currency` — make recovery a *choice with friction* (is the corpse reachable? did
the dragon swallow it?), not an automatic payout.

## 8. Surviving companions remember
A companion's attitude and arc were earned with the *party*, not tied to one PC sheet — so
their standing carries over untouched. Play the truth of it: grief for the one who fell,
wariness toward the newcomer, trust re-forming slowly over beats. Dramatize this; it's some
of the richest material a death gives you. Only reach for `adjust_attitude` (down) if you
want to mechanically reflect "this companion doesn't trust the newcomer yet" — that's your
call, never an automatic engine behavior.

## 9. Party WIPE (TPK) — YOUR call, the players' CHOICE
When `get_state` reports `party_down: true` — every player/companion in the party dead or
bleeding out — you have a true TPK. Make it a **moment**, then offer two honest paths and
let the **players choose.** Never silently pick, and never auto-end the campaign.
- **The tragic end.** The story closes here — a final stand, a last stand the world will
  remember. `end_session` and let it be a real ending.
- **Re-roll and continue.** A *fresh party*, same level, takes up the thread in the same
  living world (quests, factions, the day, lore, and NPC memories all persist). Call
  `reroll_character` once per fallen member to rebuild the party — there's no batch tool;
  N re-rolls compose a new party. The new heroes walk into a world the old ones already
  changed, and they hear of their predecessors' fate.

`party_down` is a read-only signal — it never acts on its own. You recognize the wipe, you
frame the choice, the players decide.

## 10. The one line to remember
Death is **forward motion**: a new hero, the same quest, the world carries on. You never
rewind, never save-scum, never resurrect by fiat. You make the loss *matter*, then you
hand the player a new character and walk them back into the story.
