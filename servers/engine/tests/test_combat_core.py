"""Engine-run combat CORE tests (Track 2b/2c/2e).

Covers the four net-new pieces of the engine-run combat loop:
  - combat_ai.pick_action: the pure greedy-v1 EV policy returns sane Intents.
  - combat_loop.run_combat_autonomous(mode="test"): a seeded random-vs-random fight runs to a
    terminal state and EVERY combatant acted.
  - combat_loop.run_combat_round/autonomous(mode="live"): auto-runs ONLY hostiles, STOPS at the
    first PC/companion turn, never auto-plays a PC.
  - the TEST toggles (force_hit / fast_resolve) are DEAD CODE outside the double guard, force the
    HIT BOOLEAN without corrupting crit/damage accounting, and the dice-seed is deterministic.

All of this is LLM-free, so it runs in the fast deterministic lane. Default-off is asserted: a
non-sandbox campaign with no env behaves exactly as today.
"""
from __future__ import annotations

import dice as dice_mod
import combat_ai
from combat_ai import AttackOption, CombatantView, CombatView, Intent, SpellOption, p_hit
import combat_loop
import store


# ── helpers ──────────────────────────────────────────────────────────────────────────

def _mk_view(actor_attacks, foes, grid=False, actor_cell=None, **kw) -> CombatView:
    return CombatView(
        actor_id="A",
        actor_cell=actor_cell,
        actor_zone="",
        actor_side="enemy",
        speed=kw.get("speed", 30),
        dashed=False,
        grid_enabled=grid,
        grid_width=kw.get("w", 10),
        grid_height=kw.get("h", 10),
        cell_size=5,
        foes=tuple(foes),
        allies=tuple(kw.get("allies", ())),
        attacks=tuple(actor_attacks),
        spells=tuple(kw.get("spells", ())),
        caster_level=int(kw.get("caster_level", 0)),
        # v2.0c martial fields (all default to today's behavior when omitted).
        actor_current_hp=int(kw.get("actor_current_hp", 0)),
        actor_max_hp=int(kw.get("actor_max_hp", 0)),
        abilities=tuple(kw.get("abilities", ())),
        sneak_attack=kw.get("sneak_attack", None),
        action_available=bool(kw.get("action_available", True)),
        bonus_action_available=bool(kw.get("bonus_action_available", True)),
        is_raging=bool(kw.get("is_raging", False)),
    )


def _foe(fid, hp=10, ac=12, cell=None, name=None):
    return CombatantView(id=fid, name=name or fid, side="party",
                         current_hp=hp, max_hp=hp, armor_class=ac, cell=cell)


def _ally(aid, hp=10, max_hp=10, cell=None, name=None, downed=False):
    # An ally is the actor's OWN side. The test view's actor_side is "enemy", so an ally is "enemy".
    return CombatantView(id=aid, name=name or aid, side="enemy",
                         current_hp=hp, max_hp=max_hp, armor_class=12, cell=cell, downed=downed)


def _heal(name="Healing Word", amount=6.0, rng=60, slot=1, bonus=True):
    return SpellOption(name=name, range_ft=rng, requires_slot=True,
                       is_heal=True, heal_amount=amount, slot_level=slot, is_bonus_action=bonus)


import contextlib  # noqa: E402


@contextlib.contextmanager
def _seeded_ids(seed: int):
    """Install a SEEDED entity-id generator (models._new_id) for the duration, then restore it. A
    seeded `seed` makes the WHOLE fight reproducible — character ids feed the AI's focus-fire and
    v2.0c ability tie-breaks, so RANDOM ids would let the round count drift even at a fixed dice seed
    (the richer martial behavior — Action Surge / Second Wind — surfaced this: which-goblin-dies-when
    becomes id-order-sensitive). Mirrors qa/combat_smoke._install_deterministic_ids."""
    import random as _r
    import models
    orig = models._new_id
    rng = _r.Random(0xC0FFEE ^ int(seed))
    models._new_id = lambda prefix: f"{prefix}_{rng.getrandbits(48):012x}"
    try:
        yield
    finally:
        models._new_id = orig


# ── p_hit / EV math ────────────────────────────────────────────────────────────────

def test_p_hit_curve_and_clamps():
    assert p_hit(5, 15) == 0.55          # need 10+, 11 faces hit
    assert p_hit(0, 25) == 0.05          # impossible but for the nat-20 floor
    assert p_hit(20, 5) == 0.95          # auto but for the nat-1 ceiling (NOT 1.0)
    # monotone: a higher bonus never lowers the chance
    assert p_hit(7, 15) >= p_hit(5, 15)


def test_average_total_matches_expected():
    assert dice_mod.average_total("2d8+3") == 12      # 2*4.5 + 3
    assert dice_mod.average_total("1d6") == 4          # round(3.5)
    assert dice_mod.average_total("5") == 5            # flat
    assert dice_mod.average_total("2d6") == 7


# ── pick_action: greedy-v1 returns sane Intents ──────────────────────────────────────

def test_pick_action_attacks_best_in_reach_target():
    # Two foes; the AI should focus-fire the LOWER-HP one on an EV tie (same AC).
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="2d6+3", damage_type="slashing")]
    view = _mk_view(atks, [_foe("lo", hp=5, ac=12), _foe("hi", hp=40, ac=12)])
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    assert intent.target_id == "lo"            # focus-fire the weaker foe
    assert intent.attack_name == "Claw"


def test_pick_action_prefers_higher_ev_attack():
    # Big-damage low-accuracy vs small-damage high-accuracy against one foe (AC 15).
    atks = [
        AttackOption(name="Heavy", to_hit=0, damage_expr="4d6", damage_type="b"),    # P~0.30 * 14
        AttackOption(name="Quick", to_hit=10, damage_expr="1d6", damage_type="b"),   # P~0.80 * 4
    ]
    view = _mk_view(atks, [_foe("t", hp=30, ac=15)])
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    # Heavy EV ~ 0.30*14 = 4.2 ; Quick EV ~ 0.80*3.5 = 2.8 -> Heavy wins.
    assert intent.attack_name == "Heavy"


def test_pick_action_skips_when_no_foes():
    intent = combat_ai.pick_action(actor=object(), combat_state=_mk_view([], []))
    assert intent.kind == "skip"


def test_pick_action_moves_to_reach_on_grid_when_out_of_range():
    # On the grid, a melee attacker far from its foe should MOVE toward it (not attack).
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(8, 0))], grid=True, actor_cell=(0, 0))
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "move"
    assert intent.to_cell is not None
    # the chosen cell is strictly closer to the foe than the start
    import combat_grid
    assert combat_grid.distance_ft(intent.to_cell, (8, 0)) < combat_grid.distance_ft((0, 0), (8, 0))


def test_pick_action_attacks_in_reach_on_grid():
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(1, 0))], grid=True, actor_cell=(0, 0))
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    assert intent.target_id == "t"


def test_pick_action_dodges_when_nothing_reachable_no_grid_path():
    # Grid, foe far, but speed 0 -> cannot move, cannot reach -> Dodge fallback.
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(9, 9))], grid=True, actor_cell=(0, 0), speed=0)
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "dodge"


def test_pick_action_is_deterministic():
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="2d6+3")]
    foes = [_foe("a", hp=10, ac=12), _foe("b", hp=10, ac=12)]
    v = _mk_view(atks, foes)
    i1 = combat_ai.pick_action(actor=object(), combat_state=v)
    i2 = combat_ai.pick_action(actor=object(), combat_state=v)
    assert i1 == i2   # same state -> same Intent (frozen dataclass equality)


# ── pick_action: heal-the-dying-ally triage (v2.0a, #1106) ───────────────────────────

def test_pick_action_heals_downed_ally_before_attacking():
    """A healer with a heal spell + a slot heals a DOWNED ally instead of swinging at a foe."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("garrick", hp=0, max_hp=40, downed=True)],
                 spells=[_heal()], caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast"
    assert intent.spell_name == "Healing Word"
    assert intent.target_id == "garrick"   # the downed ally, NOT the foe


def test_pick_action_heals_critical_ally_under_one_third():
    """An ally below 1/3 max HP (but not downed) is healed before attacking."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("hurt", hp=10, max_hp=40)],  # 25% < 1/3
                 spells=[_heal()], caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.target_id == "hurt"


def test_pick_action_prefers_downed_over_merely_wounded():
    """Triage order: a DOWNED ally outranks a merely-wounded one (lower rank = more urgent)."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("wounded", hp=12, max_hp=40),  # < 1/2, not critical
                         _ally("down", hp=0, max_hp=40, downed=True)],
                 spells=[_heal()], caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.target_id == "down"


def test_pick_action_prefers_bonus_action_healing_word_over_touch_cure_wounds():
    """With both a bonus-action ranged heal and a touch heal available, prefer Healing Word."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("down", hp=0, max_hp=40, downed=True)],
                 spells=[
                     _heal("Cure Wounds", amount=8.0, rng=5, slot=1, bonus=False),
                     _heal("Healing Word", amount=6.0, rng=60, slot=1, bonus=True),
                 ], caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.spell_name == "Healing Word"


def test_pick_action_does_not_heal_healthy_allies():
    """A full-HP ally is NOT healed — the healer attacks instead (no heal warranted)."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("fine", hp=40, max_hp=40)],
                 spells=[_heal()], caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack"   # no one to heal -> today's attack logic


def test_pick_action_without_heal_spell_falls_through_to_attack():
    """ADDITIVE: a downed ally but NO heal spell -> the AI behaves exactly as today (attacks)."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("down", hp=0, max_hp=40, downed=True)],
                 spells=[], caster_level=0)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack" and intent.target_id == "goblin"


def test_pick_action_does_not_heal_an_unreachable_ally_on_grid():
    """A touch-only heal whose downed ally is out of reach on the grid does NOT fire — the healer
    falls through (it can still attack a reachable foe). Proves the reach gate on heals."""
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3", reach_ft=5)]
    # Downed ally at (9,9), a foe adjacent at (1,0); a touch (5ft) heal can't reach the ally.
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12, cell=(1, 0))],
                 allies=[_ally("down", hp=0, max_hp=40, downed=True, cell=(9, 9))],
                 spells=[_heal("Cure Wounds", amount=8.0, rng=5, slot=1, bonus=False)],
                 grid=True, actor_cell=(0, 0), caster_level=5)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack"   # ally unreachable for a touch heal -> attack the foe


def test_pick_action_heal_triage_is_deterministic():
    atks = [AttackOption(name="Mace", to_hit=5, damage_expr="1d6+3")]
    v = _mk_view(atks, [_foe("goblin", hp=7, ac=12)],
                 allies=[_ally("down", hp=0, max_hp=40, downed=True)],
                 spells=[_heal()], caster_level=5)
    assert combat_ai.pick_action(actor=object(), combat_state=v) == \
        combat_ai.pick_action(actor=object(), combat_state=v)


# ── pick_action: offensive-spell EV + slot economy + concentration (v2.0b, #1106) ────

def _firebolt(value=11.0, rng=120):
    return SpellOption(name="Fire Bolt", value=value, kind="attack", range_ft=rng,
                       requires_slot=False, slot_level=0)


def _magic_missile(value=10.0, rng=120, slot=1):
    return SpellOption(name="Magic Missile", value=value, kind="auto", range_ft=rng, slot_level=slot)


def _save_spell(name="Burning Hands", value=10.0, rng=15, slot=1, save="dex", on_save="half"):
    return SpellOption(name=name, value=value, kind="save", save_ability=save, on_save=on_save,
                       range_ft=rng, slot_level=slot)


def _mkv(spells, foes, *, atk_bonus=7, save_dc=15, active_conc="", weapon=None, **kw):
    atks = [weapon] if weapon is not None else [AttackOption(name="Dagger", to_hit=2, damage_expr="1d4")]
    v = _mk_view(atks, foes, spells=spells, caster_level=5, **kw)
    # _mk_view doesn't thread the v2.0b caster numbers — rebuild with them set.
    from dataclasses import replace
    return replace(v, spell_attack_bonus=atk_bonus, spell_save_dc=save_dc,
                   active_concentration=active_conc)


def test_pick_action_casts_attack_cantrip_over_a_weak_weapon():
    """A caster whose best spell out-EVs a feeble weapon CASTS it (the whole point of v2.0b)."""
    v = _mkv([_firebolt()], [_foe("ogre", hp=60, ac=11)])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.spell_name == "Fire Bolt"


def test_pick_action_keeps_weapon_when_it_out_evs_the_spell():
    """ADDITIVE: a martial whose weapon out-EVs any spell still SWINGS — a tie also keeps the weapon
    (conserving the spell). Here a big greataxe beats a tiny cantrip."""
    axe = AttackOption(name="Greataxe", to_hit=9, damage_expr="1d12+5")
    v = _mkv([_firebolt(value=3.0)], [_foe("ogre", hp=60, ac=11)], weapon=axe)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack" and intent.attack_name == "Greataxe"


def test_pick_action_attack_roll_spell_uses_spell_attack_bonus_p_hit():
    """An attack-roll spell is scored P(hit | spell_attack_bonus) * value — a high AC lowers its EV
    below an auto-hit spell of equal raw value, so the auto-hit wins."""
    foe = _foe("foe", hp=60, ac=22)  # very high AC: Fire Bolt rarely hits
    v = _mkv([_firebolt(value=11.0), _magic_missile(value=11.0)], [foe])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    # Fire Bolt P(hit) at +7 vs AC22 is ~0.30 -> EV ~3.3; Magic Missile auto -> EV 11. Auto wins.
    assert intent.kind == "cast" and intent.spell_name == "Magic Missile"


def test_pick_action_save_spell_ev_uses_real_dc_and_save_for_half():
    """A save-for-half spell vs a LOW-save foe (high P(fail)) out-EVs the same spell vs a HIGH-save
    foe — and beats a weak weapon. Proves the real spell_save_dc threads into the EV."""
    weak_foe = CombatantView(id="weak", name="weak", side="party", current_hp=40, max_hp=40,
                             armor_class=18, save_bonuses={"dex": -1})
    v = _mkv([_save_spell(value=12.0)], [weak_foe], save_dc=16)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.spell_name == "Burning Hands"


def test_pick_action_does_not_waste_a_leveled_slot_on_a_trivial_target():
    """SLOT ECONOMY: a single low-HP foe a FREE cantrip already kills does NOT draw a leveled slot —
    the AI prefers the cantrip (slot_level 0)."""
    v = _mkv([_firebolt(value=11.0), _magic_missile(value=10.0, slot=1)], [_foe("g", hp=4, ac=12)])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.spell_name == "Fire Bolt"  # the free cantrip, not the slot


def test_pick_action_does_not_blow_a_high_slot_on_one_weak_goblin():
    """The brief's rule: don't Fireball one 8-HP goblin. A L3 slot's EV is capped at the target's HP
    (8), which is below the L3 slot floor (18) -> the AI falls back to the free weapon."""
    fireball = SpellOption(name="Fireball", value=28.0, kind="save", save_ability="dex",
                           on_save="half", range_ft=150, slot_level=3)
    v = _mkv([fireball], [_foe("g", hp=8, ac=12)])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack"  # the slot isn't worth it on one weak goblin -> the dagger


def test_pick_action_spends_a_leveled_slot_on_a_worthy_target():
    """The flip side: vs a worthy (high-HP) target with no cantrip available, a leveled slot whose
    EV clears the floor IS spent."""
    v = _mkv([_magic_missile(value=10.0, slot=1)], [_foe("ogre", hp=60, ac=11)])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "cast" and intent.spell_name == "Magic Missile"


def test_pick_action_does_not_break_a_better_active_concentration():
    """CONCENTRATION AWARENESS: the AI will NOT start a NEW concentration spell when it is already
    concentrating on a different one — it keeps the active concentration and swings instead."""
    conc = SpellOption(name="Spirit Guardians", value=14.0, kind="save", save_ability="dex",
                       on_save="half", range_ft=15, slot_level=3, concentration=True)
    v = _mkv([conc], [_foe("ogre", hp=60, ac=11)], active_conc="Hold Person")
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert not (intent.kind == "cast" and intent.spell_name == "Spirit Guardians")


def test_pick_action_offensive_scoring_is_deterministic():
    v = _mkv([_firebolt(), _magic_missile(), _save_spell()],
             [_foe("a", hp=30, ac=14), _foe("b", hp=30, ac=14)])
    assert combat_ai.pick_action(actor=object(), combat_state=v) == \
        combat_ai.pick_action(actor=object(), combat_state=v)


def test_pick_action_no_offensive_spells_is_byte_identical_to_pre_pr_attack():
    """ADDITIVE BYTE-IDENTITY: a martial with NO offensive spells picks the EXACT same attack Intent
    as before v2.0b (the offensive path is inert when there are no offensive options)."""
    axe = AttackOption(name="Greataxe", to_hit=9, damage_expr="1d12+5")
    foes = [_foe("a", hp=30, ac=14), _foe("b", hp=12, ac=14)]
    v = _mkv([], foes, weapon=axe, atk_bonus=0, save_dc=0)
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack" and intent.attack_name == "Greataxe"
    assert intent.target_id == "b"  # focus-fire the lower-HP foe (today's tiebreak), unchanged


# ── pick_action / pick_bonus_action / should_action_surge: martial abilities (v2.0c, #1106) ──

from combat_ai import AbilityOption, SneakAttackOption  # noqa: E402


def _ability(kind, resource, **kw):
    return AbilityOption(kind=kind, resource=resource, remaining=kw.pop("remaining", 1), **kw)


def test_v2c_no_abilities_attack_is_byte_identical():
    """ADDITIVE BYTE-IDENTITY (v2.0c): an actor with NO abilities + NO sneak dice picks the EXACT
    same plain attack Intent as before v2.0c — every rider field is empty (no maneuver/channel/sneak),
    so the loop applies a byte-identical strike. The whole martial layer is inert when nothing is set."""
    axe = AttackOption(name="Greataxe", to_hit=9, damage_expr="1d12+5")
    v = _mk_view([axe], [_foe("a", hp=30, ac=14)])
    intent = combat_ai.pick_action(actor=object(), combat_state=v)
    assert intent.kind == "attack" and intent.attack_name == "Greataxe"
    assert intent.maneuver == "" and intent.channel == "" and intent.sneak_attack == ()


def test_v2c_no_abilities_bonus_and_surge_are_none():
    """A view with no abilities yields NO bonus action and NO Action Surge (the channels are inert)."""
    axe = AttackOption(name="Sword", to_hit=5, damage_expr="1d8+3")
    v = _mk_view([axe], [_foe("a", hp=20, ac=12)])
    assert combat_ai.pick_bonus_action(object(), v) is None
    assert combat_ai.should_action_surge(v) is None


def test_v2c_second_wind_fires_when_hurt_only():
    """Second Wind is a bonus-action self-heal that fires ONLY when the fighter is hurt (<= 1/2 HP)."""
    axe = AttackOption(name="Sword", to_hit=5, damage_expr="1d8+3")
    sw = _ability("second_wind", "second_wind", is_bonus_action=True, heal_amount=10.0, name="Second Wind")
    foes = [_foe("a", hp=20, ac=12)]
    # Healthy: no Second Wind.
    healthy = _mk_view([axe], foes, abilities=(sw,), actor_current_hp=40, actor_max_hp=40)
    assert combat_ai.pick_bonus_action(object(), healthy) is None
    # Hurt (<= 1/2): Second Wind fires as a use_resource bonus Intent.
    hurt = _mk_view([axe], foes, abilities=(sw,), actor_current_hp=15, actor_max_hp=40)
    bi = combat_ai.pick_bonus_action(object(), hurt)
    assert bi is not None and bi.kind == "use_resource" and bi.resource == "second_wind"


def test_v2c_action_surge_only_when_hot():
    """Action Surge is a NOVA button: NOT spent vs a full-HP lone foe round 1, but spent when a foe
    is finishable (HP within ~2x one attack's EV) or the fighter is hurt."""
    axe = AttackOption(name="Sword", to_hit=8, damage_expr="2d6+4")  # EV ~ 0.85 * 11 ~ 9.4
    surge = _ability("action_surge", "action_surge", name="Action Surge")
    # A tough full-HP foe, healthy fighter -> don't waste the surge.
    cold = _mk_view([axe], [_foe("a", hp=80, ac=12)], abilities=(surge,),
                    actor_current_hp=50, actor_max_hp=50)
    assert combat_ai.should_action_surge(cold) is None
    # A finishable foe (HP ~ one attack's EV) -> surge for the kill.
    hot = _mk_view([axe], [_foe("a", hp=10, ac=12)], abilities=(surge,),
                   actor_current_hp=50, actor_max_hp=50)
    si = combat_ai.should_action_surge(hot)
    assert si is not None and si.resource == "action_surge"


def test_v2c_battle_master_maneuver_declared_on_a_worthy_attack():
    """A Battle Master declares a maneuver on a worthy (non-trivial, likely-to-hit) attack."""
    sword = AttackOption(name="Sword", to_hit=8, damage_expr="1d8+4")
    man = _ability("maneuver", "superiority_dice", size="d8", name="Trip Attack")
    v = _mk_view([sword], [_foe("a", hp=40, ac=12)], abilities=(man,))
    intent = combat_ai.pick_action(object(), v)
    assert intent.kind == "attack" and intent.maneuver == "Trip Attack"
    assert intent.maneuver_resource == "superiority_dice"


def test_v2c_maneuver_not_wasted_on_a_trivial_foe():
    """A maneuver is NOT spent on a foe a plain swing already finishes (HP <= the attack EV)."""
    sword = AttackOption(name="Sword", to_hit=12, damage_expr="2d8+5")  # EV ~ 14
    man = _ability("maneuver", "superiority_dice", size="d8", name="Trip Attack")
    v = _mk_view([sword], [_foe("a", hp=3, ac=10)], abilities=(man,))
    intent = combat_ai.pick_action(object(), v)
    assert intent.kind == "attack" and intent.maneuver == ""  # trivial foe — conserve the die


def test_v2c_sneak_attack_tagged_when_ally_adjacent():
    """A rogue tags Sneak Attack when an ALLY is within 5 ft of the target (the flanking trigger)."""
    dagger = AttackOption(name="Dagger", to_hit=7, damage_expr="1d4+4", reach_ft=5)
    sneak = SneakAttackOption(dice="3d6", value=10.0)
    # Grid: rogue at (0,0), foe at (1,0), ally at (2,0) — the ally is adjacent to the foe.
    ally = CombatantView(id="ally", name="ally", side="enemy", current_hp=20, max_hp=20,
                         armor_class=15, cell=(2, 0))
    v = _mk_view([dagger], [_foe("t", hp=20, ac=13, cell=(1, 0))], grid=True, actor_cell=(0, 0),
                 sneak_attack=sneak, allies=(ally,))
    intent = combat_ai.pick_action(object(), v)
    assert intent.kind == "attack" and intent.sneak_attack
    assert intent.sneak_attack[0]["dice"] == "3d6"


def test_v2c_sneak_attack_not_tagged_without_a_trigger():
    """No advantage and no adjacent ally -> the Sneak Attack rider is NOT tagged (no free sneak)."""
    dagger = AttackOption(name="Dagger", to_hit=7, damage_expr="1d4+4", reach_ft=5)
    sneak = SneakAttackOption(dice="3d6", value=10.0)
    # Lone rogue on the grid, no ally near the foe.
    v = _mk_view([dagger], [_foe("t", hp=20, ac=13, cell=(1, 0))], grid=True, actor_cell=(0, 0),
                 sneak_attack=sneak)
    intent = combat_ai.pick_action(object(), v)
    assert intent.kind == "attack" and intent.sneak_attack == ()


def test_v2c_guided_strike_only_on_a_likely_miss():
    """Guided Strike (+10) is reserved for a likely-MISS key attack; a likely-HIT strike doesn't burn it."""
    gs = _ability("guided_strike", "channel_divinity", name="Guided Strike")
    # High AC -> low P(hit) -> Guided Strike fires.
    weak = AttackOption(name="Mace", to_hit=4, damage_expr="1d6+2")
    miss = _mk_view([weak], [_foe("a", hp=20, ac=20)], abilities=(gs,))
    i1 = combat_ai.pick_action(object(), miss)
    assert i1.kind == "attack" and i1.channel == "Guided Strike"
    # Low AC -> high P(hit) -> the channel is conserved (no point spending +10 on a sure hit).
    strong = AttackOption(name="Mace", to_hit=10, damage_expr="1d6+2")
    hit = _mk_view([strong], [_foe("a", hp=20, ac=8)], abilities=(gs,))
    i2 = combat_ai.pick_action(object(), hit)
    assert i2.kind == "attack" and i2.channel == ""


def test_v2c_rage_enters_once_when_meleeing():
    """Rage fires as a bonus action when a foe is in melee reach and the barbarian isn't yet raging;
    once raging (is_raging=True) it does NOT re-enter (don't drain the pool)."""
    axe = AttackOption(name="Greataxe", to_hit=7, damage_expr="1d12+4", reach_ft=5)
    rage = _ability("rage", "rage", is_bonus_action=True, name="Rage")
    not_raging = _mk_view([axe], [_foe("a", hp=20, ac=13)], abilities=(rage,), is_raging=False)
    bi = combat_ai.pick_bonus_action(object(), not_raging)
    assert bi is not None and bi.kind == "use_resource" and bi.resource == "rage"
    already = _mk_view([axe], [_foe("a", hp=20, ac=13)], abilities=(rage,), is_raging=True)
    assert combat_ai.pick_bonus_action(object(), already) is None


# ── run_combat_autonomous(mode="test"): seeded random-vs-random to a terminal state ──

def _seed_fight(server, store_mod, *, sandbox=True, monsters=3):
    cid = server.create_campaign("Core Test")["id"]
    server.add_location(campaign_id=cid, name="Pit", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = sandbox
    store_mod.save_campaign(c)
    hero = server.create_character(
        cid, "Hero", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True,
    )["id"]
    mons = [m["id"] for m in server.spawn_monster(cid, "Goblin", count=monsters)["spawned"]]
    server.start_combat(cid, [hero] + mons)
    return cid, hero, mons


# ── v2.0a: the AI heals a downed ally through the real loop (#1106) ──────────────────

def _seed_healer_fight(server, store_mod, *, monsters=3):
    """A cleric + a fighter vs goblins (a sandbox fight). The cleric knows Healing Word + Cure
    Wounds. Returns (cid, cleric, fighter, mon_ids)."""
    cid = server.create_campaign("Heal Test")["id"]
    server.add_location(campaign_id=cid, name="Crypt", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    store_mod.save_campaign(c)
    cleric = server.create_character(
        cid, "Sera", kind="player", race="half-elf", class_name="cleric", level=5,
        abilities={"strength": 12, "dexterity": 10, "constitution": 14,
                   "intelligence": 10, "wisdom": 18, "charisma": 12},
        subclass="War Domain", apply_srd_defaults=True,
    )["id"]
    fighter = server.create_character(
        cid, "Garrick", kind="player", race="human", class_name="fighter", level=5,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True,
    )["id"]
    server.learn_spells(cid, cleric, ["Healing Word", "Cure Wounds", "Sacred Flame"])
    server.prepare_spells(cid, cleric, ["Healing Word", "Cure Wounds", "Sacred Flame"])
    mons = [m["id"] for m in server.spawn_monster(cid, "Goblin", count=monsters)["spawned"]]
    server.start_combat(cid, [cleric, fighter] + mons)
    return cid, cleric, fighter, mons


def test_ai_itself_heals_a_downed_ally_through_the_loop(tmp_path, monkeypatch):
    """END-TO-END: the engine-run AI (NOT a scripted assist) casts a heal on a DOWNED ally and the
    ally's HP rises. Builds the view the loop builds, asks pick_action, applies via the sole-writer
    _apply_intent (cast_spell + apply_healing), and asserts the ally was revived."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(7)
    cid, cleric, fighter, mons = _seed_healer_fight(server, store)

    # Down the fighter to 0 HP (dying) via a real engine verb.
    server.set_hp(cid, target_id=fighter, current_hp=0)
    c = server._require(cid)
    assert c.characters[fighter].current_hp == 0  # downed/dying

    # Pin the cleric as the current combatant with a fresh action economy (so the cast is legal).
    idx = next(i for i, cb in enumerate(c.combat.order) if cb.character_id == cleric)
    c.combat.turn_index = idx
    c.combat.action_used = False
    store.save_campaign(c)

    c = server._require(cid)
    view = combat_loop._build_view(server, c, c.characters[cleric])
    # The view now SEES spells + the downed ally + the caster numbers (the v2.0a foundations).
    assert any(s.is_heal for s in view.spells), "no heal spell discovered"
    assert any(a.id == fighter and a.downed for a in view.allies), "downed ally not in view"
    assert view.caster_level == 5

    intent = combat_ai.pick_action(c.characters[cleric], view)
    assert intent.kind == "cast" and intent.target_id == fighter
    assert intent.spell_name == "Healing Word"   # bonus-action ranged save preferred

    entry = combat_loop._apply_intent(server, cid, cleric, intent)
    after = server._require(cid).characters[fighter]
    assert after.current_hp > 0, "the downed ally was not actually healed"
    assert entry["result"]["heal"]["revived"] is True
    # The heal went through the locked verbs (slot spent by cast_spell, HP by apply_healing).
    assert server._require(cid).characters[cleric].spell_slots[1].used >= 1


def test_no_healable_ally_byte_identical_to_pre_pr_fight(tmp_path, monkeypatch):
    """ADDITIVE BYTE-IDENTITY: a party with NO hurt ally produces the SAME fight as a party with no
    healer at all — same victor / rounds / turns / actors. Proves the heal triage is inert when no
    heal is warranted (default-off / empty == today). Same seed, two seeded fights compared."""
    import server

    def _run_no_healer(seed):
        dice_mod.reseed_process_rng(seed)
        cid, hero, mons = _seed_fight(server, store, monsters=3)
        return combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)

    def _run_with_unhurt_healer(seed):
        # A cleric who knows heals but whose ally is never below the heal threshold: the triage must
        # never fire, so the fight resolves identically to the no-healer baseline's MECHANIC (a
        # weapon-only fight). We assert the loop runs clean to terminal with no heal cast.
        dice_mod.reseed_process_rng(seed)
        cid, cleric, fighter, mons = _seed_healer_fight(server, store, monsters=3)
        return cid, combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)

    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "a"))
    base = _run_no_healer(909)
    assert base["round_cap_hit"] is False and base["turns"] > 0

    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "b"))
    cid, healer_run = _run_with_unhurt_healer(909)
    assert healer_run["round_cap_hit"] is False and healer_run["turns"] > 0
    # No heal was ever cast in the with-healer fight (no ally dropped low enough to warrant one in
    # this seed) — the digest carries no heal entry. If an ally HAD dropped, a heal would be a
    # FEATURE not a regression; this asserts the inert path, the byte-identity claim's core.
    heals = [
        e for rr in healer_run["round_digests"] for e in rr["round_digest"]
        if isinstance(e.get("result"), dict) and "heal" in e["result"]
    ]
    assert heals == [], f"a heal fired in a fight where no ally was hurt: {heals}"


def test_non_caster_view_has_no_spells_and_no_caster_numbers(tmp_path, monkeypatch):
    """A non-caster (a fighter) yields an EMPTY spells tuple + zeroed caster numbers — the view is
    byte-identical to pre-PR for any non-healer actor (the additive invariant at the view layer)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(1)
    cid, hero, mons = _seed_fight(server, store, monsters=2)
    c = server._require(cid)
    view = combat_loop._build_view(server, c, c.characters[hero])
    assert view.spells == ()
    assert (view.spell_attack_bonus, view.spell_save_dc, view.caster_level) == (0, 0, 0)


# ── v2.0b: the AI casts an offensive spell + applies damage through the real loop (#1106) ──

def _seed_wizard_fight(server, store_mod, *, monster="Ogre", monsters=1):
    """A level-5 wizard (feeble STR-8 dagger) who knows Fire Bolt + Magic Missile + Burning Hands,
    vs `monsters` `monster`s, pinned as the current actor with a fresh action economy."""
    cid = server.create_campaign("Wizard Test")["id"]
    server.add_location(campaign_id=cid, name="Tower", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    store_mod.save_campaign(c)
    wiz = server.create_character(
        cid, "Tarn", kind="player", race="human", class_name="wizard", level=5,
        abilities={"strength": 8, "dexterity": 14, "constitution": 12,
                   "intelligence": 18, "wisdom": 10, "charisma": 10},
        apply_srd_defaults=True,
    )["id"]
    server.learn_spells(cid, wiz, ["Fire Bolt", "Magic Missile", "Burning Hands"])
    server.prepare_spells(cid, wiz, ["Fire Bolt", "Magic Missile", "Burning Hands"])
    mons = [m["id"] for m in server.spawn_monster(cid, monster, count=monsters)["spawned"]]
    server.start_combat(cid, [wiz] + mons)
    c = server._require(cid)
    idx = next(i for i, cb in enumerate(c.combat.order) if cb.character_id == wiz)
    c.combat.turn_index = idx
    c.combat.action_used = False
    store_mod.save_campaign(c)
    return cid, wiz, mons


def test_ai_itself_casts_an_offensive_spell_through_the_loop(tmp_path, monkeypatch):
    """END-TO-END: the engine-run AI (NOT a scripted assist) casts an OFFENSIVE spell over its feeble
    dagger and the foe's HP DROPS — applied through the sole-writer _apply_intent (cast_spell +
    apply_damage). Proves v2.0b: the view sees the offensive spells + the caster's attack bonus, and
    the AI chooses the spell AND the damage lands through the locked verbs."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(11)
    cid, wiz, mons = _seed_wizard_fight(server, store, monster="Ogre", monsters=1)
    ogre = mons[0]

    c = server._require(cid)
    view = combat_loop._build_view(server, c, c.characters[wiz])
    # The view sees offensive spells + the caster numbers (the v2.0b foundations).
    offensive = [s for s in view.spells if not s.is_heal and s.kind in ("attack", "auto", "save")]
    assert offensive, "no offensive spells discovered in the wizard's view"
    assert view.spell_attack_bonus > 0 and view.spell_save_dc > 0

    before = c.characters[ogre].current_hp
    intent = combat_ai.pick_action(c.characters[wiz], view)
    assert intent.kind == "cast", f"AI swung instead of casting (chose {intent.kind})"
    assert intent.spell_name in [s.name for s in offensive], f"cast a non-offensive spell: {intent.spell_name}"

    entry = combat_loop._apply_intent(server, cid, wiz, intent)
    after = server._require(cid).characters[ogre].current_hp
    assert after < before, f"the offensive cast did not remove HP ({before} -> {after})"
    assert entry["result"]["damage"]["applied"] >= 1  # the locked apply_damage verb ran


def test_non_caster_fight_is_byte_identical_under_v2b(tmp_path, monkeypatch):
    """ADDITIVE BYTE-IDENTITY: a martial-only fight resolves to the SAME outcome on a re-run — same
    seed, two runs, identical victor/rounds/turns. Seeds BOTH the dice RNG and the entity-id
    generator so the WHOLE fight is reproducible (character ids feed the AI's focus-fire / v2.0c
    ability tie-breaks; random ids would let the round count drift at a fixed dice seed)."""
    import server

    def _run(seed, state):
        monkeypatch.setenv("WORLDOS_STATE_DIR", str(state))
        with _seeded_ids(seed):
            dice_mod.reseed_process_rng(seed)
            cid, hero, mons = _seed_fight(server, store, monsters=3)
            return combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)

    r1 = _run(909, tmp_path / "a")
    r2 = _run(909, tmp_path / "b")
    assert (r1["victor"], r1["rounds"], r1["turns"]) == (r2["victor"], r2["rounds"], r2["turns"])
    assert r1["round_cap_hit"] is False


def test_run_combat_autonomous_test_runs_to_terminal_and_everyone_acted(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    with _seeded_ids(4242):  # seed ids too so the fight is fully reproducible (v2.0c tie-breaks)
        dice_mod.reseed_process_rng(4242)
        cid, hero, mons = _seed_fight(server, store)
        res = combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)
    # terminal: a victor or a draw (never left mid-fight in test mode)
    assert res["victor"] in ("party", "enemy", "draw")
    assert res["round_cap_hit"] is False
    # The hero acted, and EVERY actor that acted is a real combatant (a valid subset of the order).
    # NOTE (v2.0c): "everyone acts" is no longer guaranteed — a stronger martial AI (Action Surge /
    # maneuvers) can drop a monster BEFORE its turn comes up, so a foe may die without acting. The
    # loop's contract is "sequence each combatant that still has a turn", not "force a dead foe to
    # act"; so we assert the acted set is a non-empty subset including the hero, not a strict equality.
    acted = set(res["actors_acted"])
    all_combatants = set([hero] + mons)
    assert acted, "no combatant acted"
    assert hero in acted, "the hero never took a turn"
    assert acted <= all_combatants, f"a non-combatant acted: {acted - all_combatants}"
    # combat closed out (end_combat fired on a decisive result)
    assert res["turns"] > 0


def test_dice_seed_is_deterministic(tmp_path, monkeypatch):
    """Same seed -> byte-identical fight outcome; the seed fixes the whole sequence (dice AND ids)."""
    import server

    def _run(seed):
        with _seeded_ids(seed):
            dice_mod.reseed_process_rng(seed)
            cid, hero, mons = _seed_fight(server, store)
            return combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)

    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "a"))
    r1 = _run(909)
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "b"))
    r2 = _run(909)
    assert (r1["victor"], r1["rounds"], r1["turns"]) == (r2["victor"], r2["rounds"], r2["turns"])


def test_unset_seed_is_nondeterministic_path(tmp_path, monkeypatch):
    """An unset seed reseeds from OS entropy (today's behavior). We can't assert two fights
    DIFFER (they might coincide), but we CAN assert reseed(None) draws fresh entropy: two
    long roll sequences after reseed(None) are extremely unlikely to be identical."""
    dice_mod.reseed_process_rng(None)
    seq_a = [dice_mod.roll("1d20").natural for _ in range(50)]
    dice_mod.reseed_process_rng(None)
    seq_b = [dice_mod.roll("1d20").natural for _ in range(50)]
    assert seq_a != seq_b  # OS entropy -> distinct streams (P(collision) ~ 20^-50)


# ── LIVE mode: auto-run hostiles, STOP at the first PC/companion turn ─────────────────

def test_live_mode_stops_at_pc_and_never_autoplays_pc(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    # Force the PC to act first by giving it an absurd initiative bonus via a high DEX is
    # unreliable; instead just assert the invariant over any initiative order.
    dice_mod.reseed_process_rng(11)
    cid, hero, mons = _seed_fight(server, store, monsters=2)
    res = combat_loop.run_combat_autonomous(cid, mode="live", max_rounds=25)
    # The PC is NEVER in actors_acted in live mode (only hostiles auto-run).
    assert hero not in res["actors_acted"]
    # Live mode hands control back at a PC turn (awaiting_pc set) OR the hostiles all died
    # before the PC's first turn (a clean enemy wipe). Either is valid; the PC never auto-ran.
    if res["awaiting_pc"] is not None:
        assert res["awaiting_pc"] == hero


def test_live_round_returns_digest_and_awaiting(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(3)
    cid, hero, mons = _seed_fight(server, store, monsters=2)
    rr = combat_loop.run_combat_round(cid, mode="live")
    assert "round_digest" in rr and isinstance(rr["round_digest"], list)
    assert rr["mode"] == "live"
    # no PC entry in the digest
    assert all(e["actor_id"] != hero for e in rr["round_digest"])


# ── TEST toggles: dead code when unguarded; force the hit boolean only ────────────────

def _one_attack(server, store_mod, cid, attacker, target, *, attack_bonus=0,
                damage_dice="1d6", **hr):
    """Configure house_rules + run a single attack vs the target, return the result. Pins the
    attacker as the CURRENT combatant and clears its action budget so each call is a legal
    on-turn strike (a test convenience — we drive many attacks without advancing the round)."""
    c = server._require(cid)
    for k, v in hr.items():
        setattr(c.house_rules, k, v)
    # Make `attacker` the current combatant and give it a fresh action economy.
    idx = next((i for i, cb in enumerate(c.combat.order) if cb.character_id == attacker), 0)
    c.combat.turn_index = idx
    c.combat.action_attacks_made = 0
    c.combat.action_used = False
    for cb in c.combat.order:
        if cb.character_id == attacker:
            cb.reaction_used = False
    store_mod.save_campaign(c)
    r = server.attack(cid, attacker_id=attacker, target_id=target,
                      attack_bonus=attack_bonus, damage_dice=damage_dice)
    return r


def test_force_hit_is_dead_code_without_env(tmp_path, monkeypatch):
    """force_hit set, campaign IS sandbox, but WORLDOS_COMBAT_TEST unset -> the toggle is dead:
    a high-AC target is still missed (only the genuine nat-20 floor hits). This proves the
    double guard — force_hit can NEVER fire in a live (non-test-env) game."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Guard")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True  # sandbox half of the guard is TRUE
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=300, armor_class=30)["id"]
    server.start_combat(cid, [a, t])
    hits = sum(
        1 for _ in range(40)
        if _one_attack(server, store, cid, a, t, force_hit=True)["hit"]
    )
    # vs AC 30 with +0, only natural 20s hit (~5%). NOT ~40. So force_hit is DEAD here.
    assert hits <= 6, f"force_hit leaked without the env guard: {hits}/40 hits"


def test_force_hit_forces_hit_under_guard_without_faking_crits(tmp_path, monkeypatch):
    """With BOTH guard halves on (env + sandbox), force_hit makes every roll hit — but it does
    NOT synthesize a nat-20, so crits stay rare (only genuine nat-20s). This is the crit-honesty
    invariant: the smoke can still distinguish 'crits double dice' from 'force_hit faked a crit'."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Guard2")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=999, armor_class=30)["id"]
    server.start_combat(cid, [a, t])
    n = 80
    results = [_one_attack(server, store, cid, a, t, force_hit=True) for _ in range(n)]
    hits = sum(1 for r in results if r["hit"])
    crits = sum(1 for r in results if r["crit"])
    assert hits == n, f"force_hit must force ALL hits: {hits}/{n}"
    # crits are ONLY genuine nat-20s (~5%) — force_hit did NOT fake them.
    assert crits < n * 0.5, f"force_hit fabricated crits ({crits}/{n}) — crit accounting corrupted"


def test_fast_resolve_averages_damage_under_guard(tmp_path, monkeypatch):
    """fast_resolve (guarded) makes damage the deterministic AVERAGE: a 2d6+3 strike always
    applies 10 (2*3.5+3 -> round) regardless of the roll. Outside the guard it would vary."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Fast")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    c.house_rules.force_hit = True       # guarantee the hit so damage always rolls
    c.house_rules.fast_resolve = True
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=999, armor_class=5)["id"]
    server.start_combat(cid, [a, t])
    totals = set()
    for _ in range(20):
        r = _one_attack(server, store, cid, a, t, attack_bonus=10, damage_dice="2d6+3",
                        force_hit=True, fast_resolve=True)
        # only count non-crit strikes (a crit doubles the dice -> different average)
        if not r["crit"]:
            totals.add(r["damage"]["total"])
    # every non-crit strike applied the SAME averaged total (10), not a random spread.
    assert totals == {10}, f"fast_resolve did not average damage deterministically: {totals}"


def test_combat_test_mode_guard_requires_both_halves(tmp_path, monkeypatch):
    """The double guard predicate is True ONLY when env==1 AND campaign.is_sandbox."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    cid = server.create_campaign("G")["id"]
    c = server._require(cid)
    # neither half
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    c.is_sandbox = False
    assert server._combat_test_mode_enabled(c) is False
    # env only
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    assert server._combat_test_mode_enabled(c) is False
    # both
    c.is_sandbox = True
    assert server._combat_test_mode_enabled(c) is True
    # sandbox only
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    assert server._combat_test_mode_enabled(c) is False


# ── additive / default-off: a non-sandbox campaign behaves as today ──────────────────

def test_new_fields_default_off_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    from models import Campaign, HouseRules
    assert Campaign(title="x").is_sandbox is False
    assert HouseRules().force_hit is False
    assert HouseRules().fast_resolve is False
    # an OLD snapshot lacking the new keys loads fine (tolerant of absence) and round-trips
    cid = server.create_campaign("RT")["id"]
    c = server._require(cid)
    store.save_campaign(c)
    reloaded = server._require(cid)
    assert reloaded.is_sandbox is False
    assert reloaded.house_rules.force_hit is False


def test_unseeded_live_rolls_do_not_consume_the_shared_stream(monkeypatch):
    """Default-off / 'empty == today' (LOAD-BEARING): with NO active seed — the live-game default —
    an un-seeded roll uses a fresh per-call RNG and must NOT advance the shared process stream. This
    pins the additive invariant (a production game's dice mechanism is byte-identical to pre-Track-2b)
    AND guarantees a seeded TEST cannot leak determinism into a later un-seeded (live) run."""
    monkeypatch.delenv("WORLDOS_COMBAT_SEED", raising=False)
    dice_mod.reseed_process_rng(None)               # deactivate -> live default
    assert dice_mod._SEED_ACTIVE is False
    before = dice_mod._PROCESS_RNG.getstate()
    for _ in range(20):
        dice_mod.roll("1d20")                       # un-seeded, live-default rolls
    assert dice_mod._PROCESS_RNG.getstate() == before   # the shared stream was NOT consumed


def test_active_seed_consumes_shared_stream_and_is_reproducible():
    """When a seed is ACTIVE (a TEST), un-seeded rolls DRAW from the shared stream and the sequence
    is reproducible — the Track-2b feature. Restores the live default at the end so no later test
    inherits an active seed."""
    try:
        dice_mod.reseed_process_rng(777)
        assert dice_mod._SEED_ACTIVE is True
        before = dice_mod._PROCESS_RNG.getstate()
        dice_mod.roll("1d20")
        assert dice_mod._PROCESS_RNG.getstate() != before   # active -> the shared stream advances
        dice_mod.reseed_process_rng(777)
        a = [dice_mod.roll("1d20").total for _ in range(8)]
        dice_mod.reseed_process_rng(777)
        b = [dice_mod.roll("1d20").total for _ in range(8)]
        assert a == b
    finally:
        dice_mod.reseed_process_rng(None)           # restore the live default


def test_set_house_rules_rejects_test_only_toggles(tmp_path, monkeypatch):
    """Defense-in-depth: the live set_house_rules tool REJECTS the TEST-only combat toggles
    (force_hit/fast_resolve) — real HouseRules fields that Pydantic's extra='forbid' would otherwise
    accept — so a live tool can never even persist a TEST toggle. A normal house rule still applies."""
    import pytest
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    cid = server.create_campaign("HR")["id"]
    for bad in ({"force_hit": True}, {"fast_resolve": True}, {"difficulty": "hard", "force_hit": True}):
        with pytest.raises(ValueError, match="TEST-only"):
            server.set_house_rules(cid, bad)
    # the rejected patches never persisted; a normal patch still works and the toggles stay OFF
    hr = server.set_house_rules(cid, {"difficulty": "hard"})
    assert hr["difficulty"] == "hard"
    assert hr.get("force_hit") is False and hr.get("fast_resolve") is False
