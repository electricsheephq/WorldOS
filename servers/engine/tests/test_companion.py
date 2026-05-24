import pytest

import companion as companion_mod
from companion import (
    CompanionProvider,
    InProcessCompanion,
    SubagentCompanion,
    suggest_action,
)
from models import Character, Combat, Combatant


def mk(name: str, kind: str = "player", current_hp: int = 10, max_hp: int = 10, **kw) -> Character:
    return Character(name=name, kind=kind, current_hp=current_hp, max_hp=max_hp, **kw)


def in_combat(*chars: Character, active: bool = True) -> Combat:
    return Combat(
        active=active,
        round=1 if active else 0,
        order=[Combatant(character_id=c.id) for c in chars],
    )


def roster(*chars: Character) -> dict[str, Character]:
    return {c.id: c for c in chars}


# --- branch 1: a downed ally takes priority ---
def test_suggest_aid_downed_ally():
    comp = mk("Companion", kind="companion")
    ally = mk("Hero", kind="player", current_hp=0)
    enemy = mk("Goblin", kind="monster", current_hp=3)
    combat = in_combat(comp, ally, enemy)

    out = suggest_action(comp, combat, roster(comp, ally, enemy))

    assert out["action"] == "aid_downed"
    assert out["target_id"] == ally.id  # the downed ally, not the living enemy
    assert isinstance(out["reason"], str) and out["reason"]


def test_suggest_aid_downed_beats_attack_even_with_weak_enemy():
    # A 0-HP enemy is also present; the downed ally must still win priority.
    comp = mk("Companion", kind="companion")
    ally = mk("Hero", kind="player", current_hp=0)
    downed_enemy = mk("Goblin", kind="monster", current_hp=0)
    combat = in_combat(comp, ally, downed_enemy)

    out = suggest_action(comp, combat, roster(comp, ally, downed_enemy))

    assert out["action"] == "aid_downed" and out["target_id"] == ally.id


def test_suggest_aid_downed_self():
    # "other-or-self": the companion at 0 HP is itself a downed ally.
    comp = mk("Companion", kind="companion", current_hp=0)
    combat = in_combat(comp)

    out = suggest_action(comp, combat, roster(comp))

    assert out["action"] == "aid_downed" and out["target_id"] == comp.id


# --- branch 2: attack the living enemy with the lowest current_hp ---
def test_suggest_attack_lowest_hp_enemy():
    comp = mk("Companion", kind="companion")
    ally = mk("Hero", kind="player", current_hp=10)
    strong = mk("Ogre", kind="monster", current_hp=20)
    weak = mk("Goblin", kind="npc", current_hp=4)
    combat = in_combat(comp, ally, strong, weak)

    out = suggest_action(comp, combat, roster(comp, ally, strong, weak))

    assert out["action"] == "attack"
    assert out["target_id"] == weak.id  # lowest current_hp among living enemies
    assert isinstance(out["reason"], str) and out["reason"]


def test_suggest_attack_protects_dying_ally_targets_biggest_threat():
    # PERIL OVERRIDE (illithid QA): a non-healer companion, an ally near death (<=25% HP) and no
    # heal available -> efficiency yields to protection: go for the most dangerous foe (highest
    # max HP), NOT a 2-HP straggler. (The companion let the PC die mopping the straggler.)
    comp = mk("Minsc", kind="companion")  # no spells -> can't heal -> reaches the attack step
    dying = mk("Hero", kind="player", current_hp=2, max_hp=23)   # ~9% -> in peril
    boss = mk("Priest", kind="npc", current_hp=20, max_hp=38)    # biggest threat by max HP
    straggler = mk("Cultist", kind="monster", current_hp=2, max_hp=11)
    combat = in_combat(comp, dying, boss, straggler)

    out = suggest_action(comp, combat, roster(comp, dying, boss, straggler))
    assert out["action"] == "attack"
    assert out["target_id"] == boss.id  # the big threat, NOT the weakest straggler
    assert "near death" in out["reason"]


def test_suggest_attack_no_peril_still_focuses_weakest():
    # Without an ally in peril the default holds: focus the weakest to drop it fastest.
    comp = mk("Minsc", kind="companion")
    ally = mk("Hero", kind="player", current_hp=20, max_hp=23)   # healthy -> no peril
    boss = mk("Priest", kind="npc", current_hp=20, max_hp=38)
    straggler = mk("Cultist", kind="monster", current_hp=2, max_hp=11)
    combat = in_combat(comp, ally, boss, straggler)

    out = suggest_action(comp, combat, roster(comp, ally, boss, straggler))
    assert out["target_id"] == straggler.id  # weakest -> default efficiency


def test_suggest_attack_skips_downed_enemies():
    # A 0-HP enemy is not a valid target; the only living enemy is chosen.
    comp = mk("Companion", kind="companion")
    dead_enemy = mk("Corpse", kind="monster", current_hp=0)
    living = mk("Bandit", kind="npc", current_hp=15)
    combat = in_combat(comp, dead_enemy, living)

    out = suggest_action(comp, combat, roster(comp, dead_enemy, living))

    assert out["action"] == "attack" and out["target_id"] == living.id


# --- branch 3: roleplay when not in combat ---
def test_suggest_roleplay_out_of_combat():
    comp = mk("Companion", kind="companion")
    combat = Combat(active=False)  # no combat underway, empty order

    out = suggest_action(comp, combat, roster(comp))

    assert out["action"] == "roleplay"
    assert out["target_id"] is None
    assert isinstance(out["reason"], str) and out["reason"]


# --- branch 4: defend fallback (active combat, no aid target, no living enemy) ---
def test_suggest_defend_when_active_but_nothing_to_do():
    comp = mk("Companion", kind="companion")
    ally = mk("Hero", kind="player", current_hp=10)
    spent_enemy = mk("Goblin", kind="monster", current_hp=0)  # down, not a target
    combat = in_combat(comp, ally, spent_enemy, active=True)

    out = suggest_action(comp, combat, roster(comp, ally, spent_enemy))

    assert out["action"] == "defend" and out["target_id"] is None


def test_suggest_skips_combatants_missing_from_roster():
    # A combatant id with no Character in the roster is ignored, not an error.
    comp = mk("Companion", kind="companion")
    enemy = mk("Goblin", kind="monster", current_hp=5)
    combat = Combat(
        active=True,
        round=1,
        order=[Combatant(character_id="char_missing"), Combatant(character_id=enemy.id)],
    )

    out = suggest_action(comp, combat, roster(enemy))  # comp not even in roster

    assert out["action"] == "attack" and out["target_id"] == enemy.id


# --- InProcessCompanion (Tier-1) wraps suggest_action ---
def test_in_process_companion_is_a_provider():
    comp = mk("Companion", kind="companion", voice_id="companion-1")
    provider = InProcessCompanion(comp)
    assert isinstance(provider, CompanionProvider)
    assert provider.character_id == comp.id
    assert provider.voice_id == "companion-1"


def test_in_process_take_turn_uses_suggest_action_with_models():
    comp = mk("Companion", kind="companion")
    enemy = mk("Goblin", kind="monster", current_hp=2)
    combat = in_combat(comp, enemy)
    provider = InProcessCompanion(comp)

    out = provider.take_turn({"combat": combat, "characters": roster(comp, enemy)})

    assert out["action"] == "attack" and out["target_id"] == enemy.id


def test_in_process_take_turn_accepts_serialized_situation():
    comp = mk("Companion", kind="companion")
    ally = mk("Hero", kind="player", current_hp=0)
    combat = in_combat(comp, ally)
    provider = InProcessCompanion(comp)

    situation = {
        "combat": combat.model_dump(),
        "characters": {cid: ch.model_dump() for cid, ch in roster(comp, ally).items()},
    }
    out = provider.take_turn(situation)

    assert out["action"] == "aid_downed" and out["target_id"] == ally.id


def test_in_process_take_turn_no_combat_falls_back_to_roleplay():
    comp = mk("Companion", kind="companion")
    provider = InProcessCompanion(comp)
    out = provider.take_turn({})
    assert out["action"] == "roleplay" and out["target_id"] is None


def test_in_process_react_is_quiet_by_default():
    comp = mk("Companion", kind="companion")
    provider = InProcessCompanion(comp)
    assert provider.react({"kind": "ally_downed"}) is None


# --- SubagentCompanion (Tier-2) is a documented stub ---
def test_subagent_companion_methods_raise_not_implemented():
    comp = mk("Companion", kind="companion")
    sub = SubagentCompanion(comp)
    with pytest.raises(NotImplementedError):
        sub.take_turn({})
    with pytest.raises(NotImplementedError):
        sub.react({})
    with pytest.raises(NotImplementedError):
        _ = sub.character_id
    with pytest.raises(NotImplementedError):
        _ = sub.voice_id


def test_module_exposes_ally_and_enemy_kind_sets():
    assert companion_mod.ALLY_KINDS == {"player", "companion"}
    assert companion_mod.ENEMY_KINDS == {"npc", "monster"}
