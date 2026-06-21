"""Engine-run combat: the auto-sequencing loop (Track 2c/2e).

See docs/roadmap/engine-combat-loop-design.md §3. This is the orchestrator that drives a
fight by repeatedly asking the pure monster-AI (combat_ai.pick_action) what an actor wants,
then applying that Intent through the EXISTING engine write verbs (server.attack /
cast_spell / move_to_* / use_action / next_turn). It is NOT exposed as an MCP tool (to stay
inside the 120 KB tool-schema budget — see qa/.../test_tool_schema_budget.py); it is a
Python entry-point the engine-only combat smoke and tests call directly.

SOLE-WRITER INVARIANT (load-bearing): this module introduces NO new write path. Every state
change goes through one of server.py's existing verbs, each of which takes its own
campaign_lock + save_campaign. The loop holds no lock and never mutates the Campaign — it
only READS a snapshot to build the CombatView, and the verbs are the single mutating path.

TWO MODES (owner-decided):
  - mode="live": auto-run ONLY hostile (opposite-team) monster/NPC turns; STOP at the first
    PC/companion turn and hand a per-round digest back to the DM. Companions + PCs stay
    DM/agent-driven live.
  - mode="test": run EVERYONE (random PCs + monsters) with no LLM, to a terminal state — the
    trustworthy mechanical signal independent of the LLM scorer.
"""

from __future__ import annotations

from typing import Optional

import combat_ai
from combat_ai import AttackOption, CombatantView, CombatView, Intent

# The two "sides". Party = the player-aligned team (PCs + companions); Enemy = monsters/NPCs.
_PARTY_KINDS = ("player", "companion")
_ENEMY_KINDS = ("monster", "npc")
# In LIVE mode the loop auto-runs ONLY these kinds and STOPS at any other turn.
_LIVE_AUTORUN_KINDS = _ENEMY_KINDS


def _side_of(kind: str) -> str:
    return "party" if kind in _PARTY_KINDS else "enemy"


def _alive(ch) -> bool:
    """A combatant still IN the fight: not dead and above 0 HP. A downed-but-not-dead PC at 0
    HP is not 'alive' for victory detection (it can't act), matching the ADR's 'no living
    combatants' terminal condition."""
    return (not getattr(ch, "dead", False)) and int(getattr(ch, "current_hp", 0)) > 0


def _living_sides(c) -> set:
    """The set of sides ({'party','enemy'}) that still have a living combatant in the order."""
    sides = set()
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        if ch is not None and _alive(ch):
            sides.add(_side_of(ch.kind))
    return sides


# ── Build the read-only CombatView the AI decides over ───────────────────────────────

def _pc_attack_options(server, ch) -> tuple[AttackOption, ...]:
    """Synthesize a generic weapon strike for a PC/companion (TEST mode runs everyone). PCs
    have no stat-block attack line, so we use the sheet-derived melee numbers (proficiency +
    STR/finesse) and a longsword-equivalent 1d8 — enough to drive a TEST fight to a terminal
    state and exercise the real attack() resolution path. (LIVE mode never auto-runs PCs.)"""
    nums = server._combat_numbers(ch)
    atk_bonus = int(nums.get("melee_attack_bonus", 0))
    dmg_mod = int(nums.get("melee_damage_mod", 0))
    expr = f"1d8{'+' + str(dmg_mod) if dmg_mod >= 0 else str(dmg_mod)}" if dmg_mod else "1d8"
    return (AttackOption(name="Weapon", to_hit=atk_bonus, damage_expr=expr,
                         damage_type="slashing", reach_ft=5),)


def _monster_attack_options(server, ch, c) -> tuple[AttackOption, ...]:
    """The actor's AUTHORITATIVE attack lines from its bestiary stat block (the same data the
    DM sees), via server._monster_combat_entry. Falls back to the PC-style synthesis if no
    stat block resolves, so a custom monster still acts."""
    entry = server._monster_combat_entry(ch, c)
    if entry is None or not entry.get("attacks"):
        return _pc_attack_options(server, ch)
    opts: list[AttackOption] = []
    for a in entry["attacks"]:
        rolls = tuple(a.get("damage_rolls") or ())
        opts.append(AttackOption(
            name=str(a.get("name", "Attack")),
            to_hit=int(a.get("to_hit", 0)),
            damage_expr=str(a.get("damage", "") or ""),
            damage_type=str(a.get("damage_type", "") or ""),
            damage_rolls=rolls,
            reach_ft=5,  # PR-1 reach model: 5ft tokens (ranged gating is a later PR)
        ))
    return tuple(opts) if opts else _pc_attack_options(server, ch)


def _build_view(server, c, actor) -> CombatView:
    """Assemble the read-only CombatView for `actor` from the live Combat. READ-ONLY — touches
    no state; the loop never mutates here."""
    actor_side = _side_of(actor.kind)
    actor_cb = next((cb for cb in c.combat.order if cb.character_id == actor.id), None)
    actor_cell = (actor_cb.x, actor_cb.y) if (
        actor_cb is not None and actor_cb.x is not None and actor_cb.y is not None
    ) else None
    actor_zone = actor_cb.zone if actor_cb is not None else ""

    foes: list[CombatantView] = []
    allies: list[CombatantView] = []
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        if ch is None or ch.id == actor.id or not _alive(ch):
            continue
        cell = (cb.x, cb.y) if (cb.x is not None and cb.y is not None) else None
        ac, _ = server._effective_armor_class(ch)
        cv = CombatantView(
            id=ch.id, name=ch.name, side=_side_of(ch.kind),
            current_hp=int(ch.current_hp), max_hp=int(ch.max_hp),
            armor_class=int(ac), cell=cell, zone=cb.zone,
        )
        (foes if cv.side != actor_side else allies).append(cv)

    if actor.kind in _ENEMY_KINDS:
        attacks = _monster_attack_options(server, actor, c)
    else:
        attacks = _pc_attack_options(server, actor)

    return CombatView(
        actor_id=actor.id,
        actor_cell=actor_cell,
        actor_zone=actor_zone,
        actor_side=actor_side,
        speed=int(getattr(actor, "speed", 30)),
        dashed=bool(actor_cb.dashed) if actor_cb is not None else False,
        grid_enabled=bool(c.combat.grid_enabled),
        grid_width=int(c.combat.grid_width),
        grid_height=int(c.combat.grid_height),
        cell_size=int(c.combat.grid_cell_size),
        foes=tuple(foes),
        allies=tuple(allies),
        attacks=tuple(attacks),
        spells=(),  # v1: weapon/natural attacks only; spell EV is a later increment
    )


# ── Apply an Intent via the EXISTING write verbs (the only mutating path) ────────────

def _apply_intent(server, campaign_id: str, actor_id: str, intent: Intent) -> dict:
    """Translate one Intent into existing-verb calls (sole writer). Degrades to a skip on any
    engine refusal (out of reach / no slot / illegal) — the advisory-not-block posture the rest
    of combat uses, so a bad Intent never crashes the loop. Returns a compact digest entry."""
    entry: dict = {"actor_id": actor_id, "kind": intent.kind, "note": intent.note}
    try:
        if intent.kind == "attack":
            opt = next(
                (o for o in _view_cache.get(actor_id, ()) if o.name == intent.attack_name), None
            )
            kwargs = dict(
                campaign_id=campaign_id, attacker_id=actor_id, target_id=intent.target_id,
                attack_name=intent.attack_name,
            )
            if opt is not None and opt.damage_rolls:
                kwargs["damage_rolls"] = [dict(r) for r in opt.damage_rolls]
                kwargs["attack_bonus"] = opt.to_hit
            elif opt is not None:
                kwargs["attack_bonus"] = opt.to_hit
                kwargs["damage_dice"] = opt.damage_expr or "1d4"
                kwargs["damage_type"] = opt.damage_type
            else:
                # No cached option (defensive) — a minimal legal strike so the turn resolves.
                kwargs["attack_bonus"] = 0
                kwargs["damage_dice"] = "1d4"
            res = server.attack(**kwargs)
            dmg = res.get("damage")
            dmg_applied = None
            if isinstance(dmg, dict):
                # multi-component surfaces applied_total; single-type surfaces total.
                dmg_applied = dmg.get("applied_total", dmg.get("total"))
            entry["result"] = {
                "target": res.get("target"), "hit": res.get("hit"), "crit": res.get("crit"),
                "damage": dmg_applied,
                "target_state": res.get("target_state"),
            }
        elif intent.kind == "cast":
            res = server.cast_spell(
                campaign_id=campaign_id, character_id=actor_id,
                spell_name=intent.spell_name, target_id=intent.target_id,
            )
            entry["result"] = {"spell": intent.spell_name, "target_id": intent.target_id}
        elif intent.kind == "move":
            if intent.to_cell is not None:
                server.move_to_coords(campaign_id, actor_id, intent.to_cell[0], intent.to_cell[1])
            elif intent.to_zone:
                server.move_to_zone(campaign_id, combatant_id=actor_id, zone=intent.to_zone)
            entry["result"] = {"to_cell": intent.to_cell, "to_zone": intent.to_zone}
        elif intent.kind == "disengage":
            server.use_action(campaign_id, actor_id, kind="disengage")
            if intent.to_cell is not None:
                server.move_to_coords(campaign_id, actor_id, intent.to_cell[0], intent.to_cell[1])
            elif intent.to_zone:
                server.move_to_zone(campaign_id, combatant_id=actor_id, zone=intent.to_zone)
        elif intent.kind == "dash":
            server.use_action(campaign_id, actor_id, kind="dash")
        elif intent.kind == "dodge":
            # No dedicated Dodge verb; spend the action as a 'skip' so the turn is legally used
            # (the defensive benefit is narrative in v1). Keeps next_turn's PC-skip guard happy.
            server.use_action(campaign_id, actor_id, kind="skip")
        else:  # "skip"
            server.use_action(campaign_id, actor_id, kind="skip")
    except Exception as exc:  # advisory-not-block: degrade to a skip, record why
        entry["error"] = str(exc)
        try:
            server.use_action(campaign_id, actor_id, kind="skip")
        except Exception:
            pass
    return entry


# Per-turn cache of the actor's attack options so _apply_intent can recover the damage spec
# for the chosen attack_name without re-deriving. Set by run_combat_round before applying.
_view_cache: dict = {}


# ── The rounds ───────────────────────────────────────────────────────────────────────

def run_combat_round(campaign_id: str, mode: str = "live", max_turns: int = 60) -> dict:
    """Sequence combatants from the current turn to the end of the round.

    mode="test": run EVERYONE via pick_action + the existing verbs.
    mode="live": run ONLY hostile (monster/npc) turns; STOP at the first PC/companion turn and
                 return {round_digest, awaiting_pc: <id>} so the DM resolves it.

    Sole writer: every mutation goes through server.attack/cast_spell/move_to_*/use_action/
    next_turn. The loop holds no lock. max_turns is a safety rail against a non-advancing order.
    """
    import server  # lazy: avoid a server->combat_loop import cycle at module load

    if mode not in ("live", "test"):
        raise ValueError("mode must be 'live' or 'test'")

    digest: list[dict] = []
    awaiting_pc: Optional[str] = None
    start_round = None

    for _ in range(max_turns):
        c = server._require(campaign_id)
        if not c.combat.active or not c.combat.order:
            break
        if start_round is None:
            start_round = c.combat.round
        # Stop once the round has advanced past the one we started (one round per call).
        if c.combat.round > start_round and start_round is not None:
            break

        cur_id = c.combat.current_combatant_id
        if cur_id is None:
            break
        actor = c.characters.get(cur_id)
        if actor is None:
            server.next_turn(campaign_id)
            continue

        # LIVE: stop at the first PC/companion turn — never auto-play them.
        if mode == "live" and actor.kind not in _LIVE_AUTORUN_KINDS:
            awaiting_pc = actor.id
            break

        # Dead/downed current combatant -> just advance.
        if not _alive(actor):
            server.next_turn(campaign_id)
            continue

        # One full turn for `actor`: ask the AI, apply, repeat for a Multiattack budget.
        view = _build_view(server, c, actor)
        _view_cache[actor.id] = view.attacks
        # Multiattack budget: how many attack() strikes this actor's Attack action grants.
        ma = max(1, server._attacker_multiattack_count(actor, c))
        strikes_left = ma
        acted = False
        # Re-ask pick_action per granted strike (move-then-attack, or several strikes).
        for _strike in range(max(1, ma) + 2):  # +2 headroom for a move then attacks
            c = server._require(campaign_id)
            actor = c.characters.get(cur_id)
            if actor is None or not _alive(actor) or not c.combat.active:
                break
            if not _living_sides(c) or len(_living_sides(c)) < 2:
                break  # fight is decided; stop issuing this actor's strikes
            view = _build_view(server, c, actor)
            _view_cache[actor.id] = view.attacks
            intent = combat_ai.pick_action(actor, view)
            entry = _apply_intent(server, campaign_id, actor.id, intent)
            digest.append(entry)
            acted = True
            if intent.kind == "attack":
                strikes_left -= 1
                if strikes_left <= 0:
                    break
            elif intent.kind in ("skip", "dodge", "disengage"):
                break
            elif intent.kind == "move":
                continue  # let the next iteration try to attack from the new cell
            else:  # cast / dash
                break

        # Advance the turn (auto-resolves end-of-turn repeat saves). If the actor never acted,
        # mark a skip first so next_turn's PC-skip guard (companions count) is satisfied.
        c = server._require(campaign_id)
        if c.combat.active and c.combat.current_combatant_id == cur_id:
            if not acted:
                try:
                    server.use_action(campaign_id, cur_id, kind="skip")
                except Exception:
                    pass
            try:
                server.next_turn(campaign_id)
            except Exception:
                break
        _view_cache.pop(cur_id, None)

        # Stop the round if the fight is decided.
        c = server._require(campaign_id)
        if not c.combat.active or len(_living_sides(c)) < 2:
            break

    c = server._require(campaign_id)
    living = _living_sides(c)
    return {
        "mode": mode,
        "round": c.combat.round,
        "round_digest": digest,
        "awaiting_pc": awaiting_pc,
        "combat_active": c.combat.active,
        "living_sides": sorted(living),
    }


def run_combat_autonomous(campaign_id: str, mode: str = "test", max_rounds: int = 20) -> dict:
    """Drive run_combat_round repeatedly to a terminal state (victory / defeat / round-cap).

    mode="live": advance the fight up to the NEXT PC decision, then hand control back to the DM
                 (returns the digest + awaiting_pc). Never auto-plays a PC.
    mode="test": run the whole fight to a terminal state with no LLM (reproducible under
                 WORLDOS_COMBAT_SEED).

    Returns a rollup: {rounds, turns, victor, per-round digests, every combatant that acted,
    round_cap_hit}. The round-cap is a safety rail against a non-terminating fight; it ends the
    fight as a draw and flags round_cap_hit. Sole writer (only the round's verbs mutate)."""
    import server  # lazy

    if mode not in ("live", "test"):
        raise ValueError("mode must be 'live' or 'test'")

    rounds: list[dict] = []
    actors_acted: set = set()
    victor: Optional[str] = None
    round_cap_hit = False
    awaiting_pc: Optional[str] = None
    turns = 0

    for _ in range(max_rounds):
        c = server._require(campaign_id)
        if not c.combat.active:
            break
        living = _living_sides(c)
        if len(living) < 2:
            victor = next(iter(living)) if living else "draw"
            break

        rr = run_combat_round(campaign_id, mode)
        rounds.append(rr)
        for e in rr["round_digest"]:
            actors_acted.add(e["actor_id"])
            turns += 1

        if mode == "live" and rr.get("awaiting_pc"):
            awaiting_pc = rr["awaiting_pc"]
            break

        c = server._require(campaign_id)
        living = _living_sides(c)
        if not c.combat.active or len(living) < 2:
            victor = next(iter(living)) if living else "draw"
            break
    else:
        round_cap_hit = True

    # Resolve a final victor read if we exited via a terminal side condition.
    c = server._require(campaign_id)
    living = _living_sides(c)
    if victor is None and not awaiting_pc:
        victor = next(iter(living)) if len(living) == 1 else ("draw" if round_cap_hit else None)

    # TEST mode runs the fight to a terminal state -> close out combat so XP/cleanup fire.
    ended = None
    if mode == "test" and victor is not None and victor != "draw":
        try:
            ended = server.end_combat(campaign_id, resolution=f"{victor} victorious (engine-run test)")
        except Exception as exc:
            ended = {"error": str(exc)}

    return {
        "mode": mode,
        "rounds": len(rounds),
        "turns": turns,
        "victor": victor,
        "awaiting_pc": awaiting_pc,
        "round_cap_hit": round_cap_hit,
        "actors_acted": sorted(actors_acted),
        "round_digests": rounds,
        "end_combat": ended,
    }
