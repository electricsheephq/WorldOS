# The living-story loop — make threads *evolve*, choices *ripple*, bonds *turn*

**Read this when a thread resolves, when a decisional surfaces, or when a companion's loyalty is in
play.** The story-craft playbook teaches you to make a single scene sing; this one keeps the *story
between scenes alive* — so the world the player leaves behind keeps moving, and the choices they make
come back to find them. Three engine surfaces carry it. None of them narrate for you; they hand you a
fact, and you make it a scene.

> The discipline that makes this trustworthy: the engine only ever reads things *it* set — a flag, a
> faction's reputation, a companion's approval, the day. It never judges your prose. So when you want a
> choice to have teeth later, you tell the engine about it (set a flag, resolve an event, record the
> decision) — and *then* it can ripple deterministically. A choice you only narrate is a choice the
> world forgets.

## The rule of three — nothing is one-and-done

A resolved quest that simply ends is a closed file; a resolved quest that *plants something* is how a
session becomes a saga. So when you call `complete_quest`, give the thread an echo:

```
complete_quest(campaign_id, quest_id, evolves_to="<a follow-on hook or a free seed tag>", callback_in_days=N)
```

The engine schedules the follow-on as a consequence — due `callback_in_days` later (or immediately, if
0) — and surfaces it back to you when its day comes: it fires automatically into `scene_context`'s
`consequences_due` (the beat re-ground you read every turn), and `check_consequences` shows it too. You
weave the return; the engine just makes sure it *comes back*. The grateful family you saved becomes a feud over the reward.
The smuggler you let walk owes you, and one day comes to collect — or to be collected. The cult you broke
left one survivor who remembers your face. Reach for the *consequence* of the resolution, not a sequel
hook bolted on: what did winning *cost*, who did it *make*, what did it leave *unfinished*. (If you forget,
the Campaign Director will nudge you — a resolved thread with no echo shows up as a debt — but the strong
move is to plant it yourself, in the moment of resolution, while the choice is still warm.)

Don't announce it. The player should never hear "this will return in seven days." They should just,
later, feel the road behind them was never as closed as it looked.

## Stumble-into events — a choice the world actually weighs

Some choice points are first-class **Events**: a content-authored decisional whose options each carry a
*deterministic* ripple. Where a freeform parley leaves you to hand-route the outcome, an Event already
knows what each choice does to the world. Each beat — in the same breath as the Director — call
**`present_events`**. It returns only the Events whose moment has genuinely arrived (a flag is set, a
faction's reputation crossed a line, a day was reached); most beats it returns nothing, and that's
correct. When one *does* surface, it's the engine telling you: the stumble-into moment is now.

**Stage it as a scene, never as a menu.** The Event hands you a `prompt` (the situation) and tagged
`options` (the ways through) — lay them out the way the story-craft playbook lays out any parley: voiced,
embodied, two-to-four short moves with the alignment/skill texture in *your* scaffold, and the free-form
path always open (the player can act off the menu, always). Bind the scene to the `anchor_npc_id` if it
names one — a decisional is sharper in the mouth of someone the player knows.

When the player picks one of the Event's options, resolve it through the engine:

```
resolve_event(campaign_id, event_id, option_label)
```

The engine applies that option's outcome deterministically — sets a flag, shifts a faction's reputation,
schedules a follow-on (the rule of three, at the decisional layer), and hands you back exactly what moved
for you to narrate. It's idempotent: resolve it once. If the player invents something *off* the menu,
that's not an Event option — adjudicate it yourself (`skill_check` / `social_check`), then `record_decision`
and `adjust_reputation` as usual. The Event is a sharper tool for the authored forks; the free-form path is
never closed.

A choice resolved this way *ripples on its own*, deterministically, days or scenes later — which is the
whole point. The player learns their choices have weight because the world keeps proving it without being
asked.

## Decision-gated flips — a bond that turns because of what the player *did*

The most devastating turn in this kind of story isn't a stranger's betrayal — it's an ally who breaks
*because of a choice the player made*. The engine supports this honestly: a companion can carry a sealed
**agenda** that turns them when their approval has curdled past a breaking point, and a *recorded decision*
can make that turn far likelier. The seam is a flag — the same flag an Event or a `record_decision` sets,
the same flag the companion's agenda reads.

Concretely, when a choice is the kind that would wound a specific companion's principles — they took the
devil's gold, they let the innocent die, they sided with the thing the companion swore to fight — record
it so the engine knows:

```
record_decision(campaign_id, summary=…, chosen=…, sets_flag="<the content-defined flag>")
# or, when the choice was an Event option, resolve_event already set the flag for you.
```

That flag spikes the matching companion's betrayal odds — it never *forces* the turn (the breaking point
is still gauged on approval, the roll is still a roll), but a curdling bond now leans toward the cliff. The
engine rolls; you're told the result. **Two obligations on you:**

1. **Foreshadow — never spring it.** Each beat, `check_companion_arc` may hand you a `betrayal_warning`:
   this companion's bond is in the danger band, and (if a decision already spiked it) the odds are leaning.
   That is your cue to *telegraph the fracture* — the held silence after the player's choice, the line
   that doesn't quite forgive, the way they stop watching the player's back. A betrayal the player never
   saw coming reads as the DM cheating; a betrayal they felt building and chose to ignore is tragedy. Open
   the wound before it snaps — the way the story-craft playbook opens any companion's specific guilt.

2. **Stage the flip at a moment, in-character — and keep them themselves.** When the agenda fires (the
   engine tells you), the companion turns *now* — and as a real action, never softened to narration (a
   betrayal that's a real `attack`, not a sad paragraph). But the turn must be recognizably *them*: the
   zealot betrays you out of faith, not malice; the straight-dealer who can't stomach what you became does
   it grieving, not gloating. They were always this person. The choice the player made simply led them to
   the one place their principles couldn't follow. Don't make the companion a different character at the
   knife — make them the *same* character, finally cornered by who the player chose to be.

## The shape of it

Director, events, arcs — consult all three at the top of the beat, the way you already consult the
Director. Most beats they're quiet. When they speak, they're the world insisting it's alive: a thread you
closed coming back changed, a choice you made arriving to be paid, a friend you wronged turning at last.
Weave the top one into the scene in front of the player. Never recite the list — let the living world
*press through the room*, the same as the mythic does.
