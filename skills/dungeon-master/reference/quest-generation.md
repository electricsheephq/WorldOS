# Quest generation — the cold open + the lore-derived hooks (S7)

When you `start_world` a campaign, the engine has already **assembled the material for you**: a
guaranteed cold-open and a graph of lore-derived quest seeds. You don't invent quests from nothing
or drop the player mid-scene — you **weave** what the engine handed you. (The engine owns the
structure; you own the prose. It never judges the fiction — *you* advance everything.)

## Open a NEW campaign with the PRELUDE — never start mid-quest

The single most common opening failure is dropping the player into an in-progress action with no
grounding and no "how did we meet?". The engine prevents that: call **`get_prelude`** at the very
start of a new campaign (it's also echoed in the `start_world` result). It returns four guaranteed
beats with bound nouns — **weave them in order, in your own framing and prose. It is a checklist,
NOT a rail and NOT a read-aloud list.**

1. **Arrival** — ground the PC in the world *before* anything is asked of them: a place, a tone, a
   floor to stand on (`ref_id` is the starting location — `look_around` it). Low-stakes, orienting.
2. **Meeting** — *how the party meets the first companion* (`ref_id` is a roster companion). Give the
   meeting a **reason**: a shared stake, a common threat, a chance that puts you on the same side.
   This is the beat that was missing — don't skip it; don't have the companion just "be there."
3. **Inciting Incident** — the **wrong lands in front of the party**, not reported third-hand
   (`ref_id` is the spine hook — its `grievance`). Make it concrete and personal. This is the Call.
4. **Threshold** — the **player commits** (the first thread goes live). This commitment is the
   PLAYER's to make, not yours to narrate — it's the one beat that buys the whole campaign.

> **Hold the silence — the Threshold is the player's, not yours.** The #1 way the cold open
> fails: you build a gorgeous inciting incident, the companion turns and says "Or—?" … and then
> you *narrate the hero's choice for them* ("she presses the last coin into the boy's hand; she
> stays"). That erases the single most important decision in the session. **Stop on the open
> question and do not write another word until the player acts.** Same for the first fork (chase
> the enemy *or* race to the cache): name both, then STOP — the player picks, *then* you
> `travel_to`. Resisting the urge to fill the silence beautifully is the whole job here. (This is
> the SKILL.md non-negotiable "never play their part" at its sharpest — the cold open is where
> it's most tempting to break.)

> **The cold open's FINAL output is the opening SCENE itself — as your reply text, in 2nd
> person.** This is the same non-negotiable as every beat (SKILL.md: "Your turn's FINAL output
> is ALWAYS 2nd-person player-facing narration"), and the cold open is where it's most often
> dropped: you do all the silent setup with tools — `start_world`/`get_state`, seat the PC,
> recruit the companion, `look_around`, `generate_image`, `log_event`, `remember` — and then
> *end the turn on a tool call or a 3rd-person setup note instead of writing the scene*. The
> player reads **only your reply text** as their opening; an empty reply or a 3rd-person brief
> means a first-timer sees no scene at all. So: do the setup FIRST, then **close the turn by
> writing the Arrival as 2nd-person prose addressed to "you"** — where you are, what you see/
> hear/smell, who's present and a real quoted line, ending on the open moment + choice.
> **NEVER** let your reply be a game-system setup brief like *"COLD OPEN — ARRIVAL: Rolan
> (tiefling wizard, PC) walks toward Sorcerous Sundries…"* — that 3rd-person, sheet-tagged
> notation is your private scratchpad, never the player's scene. If you `log_event` a setup
> note, you must STILL write the 2nd-person scene as your reply text.
>
> **STREAM the cold open too — it's the slowest turn of the whole session, so it benefits most.**
> Setting up a brand-new world (`start_world`, seat the PC, recruit the companion, `look_around`,
> art) is minutes of silent tool work; a first-timer staring at a blank dashboard the whole time
> is the canonical give-up. So once the setup tools have run, **`log_event(kind="narration", …)`
> the Arrival scene as 2nd-person prose the moment you've composed it** — it streams onto the
> dashboard while you finish the rest (this is SKILL.md beat-cycle step 2 applied to the cold open).
> The thing you `log_event` here is the **player-facing scene itself** ("You step out of the rain
> into the close, candle-smell dark of Sorcerous Sundries…"), NOT a `COLD OPEN — ARRIVAL:` setup
> brief — the brief is your scratchpad and the dashboard's recovery path rejects it on purpose; a
> real 2nd-person scene streams. Then ALSO speak that **same** Arrival prose as your reply text
> (the dashboard de-dups the two copies by text, so it shows once — see SKILL.md "Your turn's
> FINAL output"). One scene, streamed live and echoed as the reply; never a brief, never two
> different versions.

Spend real scene-time here. A strong cold open is the difference between "a session that started"
and "a session someone wants to keep playing." Then enter the normal beat cycle.

## Weave the hooks — don't hand out a quest list

**`get_quest_hooks`** returns lore-derived seeds — each a dramatic SHAPE (investigation, heist,
rescue, faction-war, …) bound to typed lore nouns (a **giver**, a **target**, a **place**, an
**item**) and a **`grievance`** (a wrong the lore already contains). Use them like this:

- **Let the player STUMBLE into a hook** — surface it as something they *notice* (a giver NPC with a
  worry, a nailed-up notice, a rumor at the bar), never as a menu of quests. "It's about finding the
  quest," not picking one off a board.
- **The `spine` hook is the main arc**; the rest are **ribs** that `arc_back` to it. When the player
  veers, pull an adjacent rib — it still feeds the spine, so the story never collapses. Veering is a
  different thread, not falling off the map.
- **The grievance is the why.** Open on the wrong, not the errand. "Recover the rune" is a fetch;
  "the Gondian smith who smuggled out the rune will hang if the Watch traces it first" is a quest.
- When the party **bites**, promote the hook into a tracked quest with `add_quest`, and call
  **`set_quest_status(hook_id, "active")`**; mark **`"resolved"`** when the fiction concludes it
  (the engine never auto-detects this — you judge it). A resolved rib's `arc_back` may now matter to
  the spine — weave that callback.
- **Generate more on demand.** If the player exhausts or ignores the seeds, invent new ones in the
  same spirit (a SHAPE + a lore grievance + real nouns) and `add_quest` them — the hooks are a
  starting graph, not a ceiling. Chain them: resolving one wrong should expose the next.

## Claudan & the Wild Wasteland — a RARE chaos-engine easter egg

The roster includes **Claudan the Chronicler**, but he is **NOT a normal quest-giver** — the engine
deliberately keeps him out of the default hook pool (he's flagged an easter egg; the real hooks bind
to the world's factions and figures, never to him). Treat him like Fallout's *Wild Wasteland* perk:
a rare, opt-in detour into the gleefully absurd.

- **He's a find, not a fixture.** Don't seed him into the main arc or push his quests. He turns up
  **off the beaten path** — a side alley, a wrong turn, a door that wasn't there yesterday — and
  only for a party that goes looking or stumbles in. Most playthroughs never meet him. That's the point.
- **His errands open as the MOST mundane thing imaginable** — "deliver this sealed jar, don't open
  it"; "ask the miller about his cat"; "return a library book." The kind of quest a player would
  shrug off. Let them take it precisely because it looks like nothing.
- **Then crank the chaos dial to 100.** Escalate beat by beat into the off-the-wall: the jar holds a
  bound archdevil's *grudge*; the miller's cat is a polymorphed petty god mid-divorce; the overdue
  book is the only copy of a ritual three cults are killing for. Planar incursions, indignant deities,
  an explosion no one can explain. **Intense and hilarious by turns** — real stakes (gods and demons
  genuinely throw down) wrapped in farce. It is a deliberate, gleeful tonal break from the main
  grim/hopeful story — so use it sparingly, where a palate-cleanser of pure spectacle lands.
- **Claudan means well and is baffled by his own wake.** Play him sincere, soft-spoken, a half-step
  behind the catastrophe he set in motion. He is never the villain; he's the fuse.
- Mechanically it's still the normal loop — `add_quest` it, run the beats, resolve through the engine
  — you just narrate the escalation. (No special tool; the chaos is yours to dial. If a run wants a
  mechanical chaos knob later, that's a noted follow-up.)

## The contract (why this is safe to lean on)
The engine assembles seeds + the cold open from the seeded world (world_state, factions, roster,
resolved quest-outcomes) — deterministic, lore-connected, setting-agnostic. It does **not** track
quest completion or branch logic — that lives in your narration. So: weave freely, advance with
`set_quest_status`, and never wait for the engine to "complete" a quest. You are the author; the
engine is your prepared table.
