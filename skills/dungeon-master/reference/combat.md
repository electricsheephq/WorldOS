# Combat playbook — running a fight through the engine

The story rules still apply in a fight (NPCs/foes SPEAK, the danger is felt) — but the mechanics are
unforgiving, so source every one from the engine:

- **Put monsters on the field with `spawn_monster(name)`** — it builds a full, combat-ready stat block (HP, AC, abilities, resistances/immunities, attacks) from the SRD bestiary, so you never hand-transcribe stats or guess. Use `count` for a pack (`spawn_monster("Goblin Warrior", count=3)`). Named adventure villains and any NPC with a stat block (e.g. Grett, Quill) are **already** combat-ready — fight their *existing* record; never create a second one for the same character.
- Pass a damage type to `attack`/`apply_damage` (e.g. `damage_type="fire"`) so the engine applies the target's resistance/immunity/vulnerability automatically.
- `start_combat` rolls initiative and sets the turn to the **first** combatant — that combatant acts *immediately*. Do **not** call `next_turn` before the first turn.
- After a combatant finishes its action, call `next_turn` once to advance. The engine skips dead/removed combatants for you — never double-advance to "skip" someone.
- Track the **action economy** with `use_action(kind=action|bonus|reaction)`: each creature gets one action + one bonus action on its turn and one reaction per round (it returns `ok:false` if something tries to act twice or off-turn). Multiattack / Extra Attack is **one** action — declare a single `action`, then make several `attack` calls under it.
- Each `attack` / `cast_spell` / `saving_throw` is for the **current** turn-holder (see `get_state.current_turn`). Acting for someone else mid-combat is a reaction; the engine returns an `off_turn_warning` — heed it so the initiative order doesn't desync.
- `attack` **already applies its own damage** on a hit (and reports the target's new state) — do **not** call `apply_damage` again afterward, or you'll hit twice. Use `apply_damage` only for damage that isn't an attack (a failed save, a trap, environmental).
- For a **save spell**, get the DC from `spell_save_dc` (never compute it by hand — items/proficiency vary), then `saving_throw` the target, then `apply_damage(half=<the save succeeded>)`.
- On EVERY companion turn call `companion_suggest_action` fresh and play it in the companion's voice (deviate only with reason). If it returns `aid_downed`/`heal`, cast the suggested `spell` that turn (`cast_spell` → `apply_healing`) — never let an ally bleed out across rounds with a heal in hand. If `aid_downed` comes back with `spell: null` (no slot left), `stabilize(actor_id, target_id)` the downed ally (a DC 10 Medicine check) instead of hand-waving it.
