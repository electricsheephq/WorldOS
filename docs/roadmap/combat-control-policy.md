# Combat-Control Policy — who drives each combatant, by purpose

> Owner direction (2026-06-21). Companion to `engine-combat-loop-design.md` (the engine-run combat
> mechanism) and epic #1100. This doc is the **policy layer**: given the engine *can* drive any
> combatant, it specifies *who actually drives* each one in each context, and how that wires into QA.

## The principle
The engine must be **able to drive combat ALWAYS — any combatant (player, companion, enemy), any
scenario, competently**. That capability is universal. What changes by **purpose** is *who we let
drive*. Combat mechanics and the story system are two separable halves of the engine; this policy is
purely about the combat half's control surface.

## The driver, per combatant
Each combatant's turn is driven by exactly one of:
- **`engine`** — the deterministic engine "AI" (`combat_ai.pick_action` + `run_combat_*`). Zero LLM
  tokens. (In CRPG parlance "AI", though it is rules/heuristics, not a model.)
- **`dm`** — the token-powered LLM Dungeon Master decides + narrates the turn (today's path).
- **`player`** — a human controls the turn (future live-play; not exercised in headless QA).

## Context presets (the "hybrid Option 1")
The same engine, different driver assignment per context. **Enemies are `engine` in every preset** —
that alone is the standing token win (the DM never computes monster tactics; it narrates the felt
result).

| Context | Player PC | Companions | Enemies | Why |
|---|---|---|---|---|
| **Live play** (default) | `player` (manual), opt-in auto-resolve | `engine` auto, per-companion manual/`dm` toggle | `engine` | BG3/Pathfinder model — the player controls their hero, can delegate or seize companion control for tactical fights. |
| **QA-fast** (NOT testing combat) | `engine` | `engine` | `engine` | The engine resolves the whole fight with no DM tokens, so a story/UX run isn't taxed by combat it isn't measuring. Faster, cheaper QA. |
| **QA-release / full sweep** | `dm` | `dm` | `engine` | Measures the **real player-facing** path — the DM drives the party's combat (we proved the DM is good at it), enemies stay engine. This is the path a GA RRI scores. |
| **Story-first accessibility** | `engine` (auto-resolve) | `engine` | `engine` | An option some players want (automate combat, enjoy the story). It exists; it is **not our lean** — our combat should be **fun, engaging, D&D-style**. |

Live players will eventually want to *control* their combat; the DM may also want to drive a PC turn.
The policy supports all of it because the **driver is per-combatant and reassignable**, not hardwired.

## How it wires into the engine (additive, on the merged spine)
`run_combat_round`/`run_combat_autonomous` already encode two of these via `mode`:
- `mode="test"` = **engine drives everyone** = the **QA-fast** preset (already proven by `qa/combat_smoke.py`).
- `mode="live"` = **engine drives only hostiles, stops at PC/companion** = the **QA-release / live**
  spine (the DM/player resolves the party turn).

The policy layer is a thin generalization: replace the binary `_LIVE_AUTORUN_KINDS` membership test
with a **per-combatant driver map** resolved from a `CombatControlPolicy` (a context preset +
per-combatant overrides), so a companion can be flipped `engine`↔`dm` mid-fight. Additive, default
== today's `live`/`test` behavior. No change to the sole-writer or guard invariants.

## How it wires into testing (the owner's explicit ask)
- A QA knob — `WORLDOS_COMBAT_DRIVER=engine-all | enemies-engine | dm-all` (or a harness flag) —
  selects the preset for a run. **Default by lane:** story/UX/persona runs that aren't measuring
  combat use `engine-all` (fast, cheap); the **release RRI sweep** uses `enemies-engine` (the real
  measured path); a combat-focused run can force `dm-all` to score DM combat narration.
- `qa/combat_smoke.py` (#1104) is the **engine-all path, proven + CI-gated**, and doubles as the
  **engine-AI competence regression gauge** (the maintenance answer: it catches "did the cleric heal?
  did the fighter use abilities?" as the AI grows).
- Because the engine can resolve fights cheaply and correctly, we can afford **more combat, faster**
  (level-ups, encounter density) in non-combat-focused runs without a token blow-up.

## Engine-AI competence ladder + the gap map (from the smoke #1104)
The smoke mapped the honest state — the engine **resolves** combat correctly but doesn't yet **play**
it well:
- **v1 (DONE):** greedy attacker — EV target selection (P(hit)·E[dmg]), focus-fire, weapon/natural
  attacks, move/dodge, correct hit/miss/crit/damage/death + downing. Runs a fight to a clean finish,
  no LLM. *Demonstrated 2026-06-21: 3 L4 PCs vs 5 goblins, 7 rounds, deterministic.*
- **THE GAP:** the AI's decision *view* contains only weapon attacks. A cleric never heals a dying
  ally; casters never cast; fighters never use Action Surge / Second Wind / maneuvers. The smoke's
  scripted assist proves the *verbs* (`cast_spell` etc.) resolve, but the *AI does not choose them*.
- **v2 (NEXT — planned, not yet built; gated on this gap-map being read):** populate `CombatView`
  with spells + class abilities so `pick_action` can choose them — heal a dying ally, cast the
  highest-EV spell, spend Action Surge when the party is hurt, use maneuvers/rage/sneak positioning.
  This is what makes the engine *competent*, the prerequisite for "engine drives party combat well."
- **v3:** positioning / action-economy depth (kiting, cover, OA discipline), then **difficulty tiers**
  (dumb / normal / smart) for enemy challenge tuning + the auto-vs-manual experience.
- **Maintenance:** every rung is regression-gauged by `qa/combat_smoke.py` so competence can't silently
  rot.

## Non-goals / guardrails
Combat mechanics stay separable from the story system; the engine remains sole writer; presets are
additive and default to today's behavior; the story-first auto-combat option is supported but is not
the product's lean. **Our combat should be fun, engaging, and D&D-style.**
