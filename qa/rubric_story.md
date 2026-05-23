# ClawDnD STORY-FIRST playtest rubric

You are grading a deliberately **story-first** playtest (exploration / NPCs /
party deliberation, minimal combat). The question this run answers: **does
ClawDnD feel like a guided adventure with a real co-adventurer, or like a combat
sim?** Score the same 7 criteria (1–5) and JSON schema as always, but weight and
interpret them for storytelling:

- **companion_agency** — THE headline criterion here. Did the companion react AND
  advise on essentially *every* beat, in its own distinct voice, with real
  opinions (and at least once push back / disagree)? Was `companion_advise` used
  and its prompt voiced — or did the companion go quiet / get puppeted? A silent
  or yes-man companion is a hard fail (1–2).
- **narrative_pacing** — Does it read like a story a DM is guiding: vivid scenes,
  NPCs that feel alive in their own voices, momentum, the spotlight on the player
  + companion? Or stop-start and mechanical?
- **exploration** — Were investigation/social beats real (a `social_check` that
  matters, `look_around`/`travel_to`, learning + `remember`ing facts)?
- **tool_sourced** — Were the new story tools actually used (`companion_advise`,
  `recall`, `record_decision`) and were ALL mechanics still tool-sourced (no
  invented rolls/DCs)? Party deliberation should produce a `record_decision`.
- **state_integrity** — Does final state match the story (party, location, day,
  the recorded decision, any consequence scheduled)?
- **rules_correctness** / **robustness** — judge whatever little mechanics occur;
  don't penalize the absence of combat (this run is intentionally low-combat).

`overall` = honest weighted average with **companion_agency and narrative_pacing
counting double** for this story-first run. `verdict` = one sentence: does this
feel like an adventure *with* a real companion, and is the storytelling loop
working? `defects` = concrete, fixable gaps (esp. anywhere the companion was
silent, deliberation was skipped, or recall/decisions weren't used).
