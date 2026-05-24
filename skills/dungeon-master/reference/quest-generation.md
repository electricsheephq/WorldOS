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
4. **Threshold** — the party *commits*; the first thread goes live. Only now does open play begin.

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

## A note on givers

The roster includes **Claudan the Chronicler** — a wandering archivist who keeps a ledger of the
city's open griefs and trades threads to anyone willing to pull on them. He's a natural, low-friction
quest-giver when you need one (especially for investigation/knowledge hooks): he knows which wrongs
are ripe and asks only that someone remember it happened.

## The contract (why this is safe to lean on)
The engine assembles seeds + the cold open from the seeded world (world_state, factions, roster,
resolved quest-outcomes) — deterministic, lore-connected, setting-agnostic. It does **not** track
quest completion or branch logic — that lives in your narration. So: weave freely, advance with
`set_quest_status`, and never wait for the engine to "complete" a quest. You are the author; the
engine is your prepared table.
