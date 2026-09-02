"""#1645 — the COMBAT-CLOSURE nudge (the inverse of end_combat's live-hostile guard).

The proven failure (the FIRST live arc-duo adventure eval, adv_live1): the DM narrated a
crypt goblin fight, `start_combat` fired, but `end_combat` was never called — the fight was
left OPEN, so no XP landed and the arc's beat budget was eaten before the boss was reached.

The engine assist (secondary lever): when a fight is ACTIVE but NO living hostile remains, the
DM-visible combat surface (`_combat_view`, returned by every combat tool) surfaces a LOUD
`pending_resolution` nudge suggesting `end_combat` — and ESCALATES to URGENT once the no-hostile
state has persisted across `server._COMBAT_RESOLUTION_NUDGE_TURNS` consecutive `next_turn` advances.
The engine NEVER auto-ends combat (questgen.py:7 invariant — the DM owns the end_combat
predicate); this only makes an un-closed fight progressively louder.

These guard: the flag APPEARS when hostiles are dead, is ABSENT while any hostile lives (or
combat is inactive), escalates on a persisting streak, CLEARS on end_combat, and — the additive
contract — a combat with no streak (no_hostile_turns == 0) serializes BYTE-IDENTICALLY to a
pre-#1645 snapshot (no new key), so the store's dirty-skip never bumps updated_at.
"""

import pytest

import server
import store
from models import Campaign, Character, Combat, Combatant


# --- helpers ----------------------------------------------------------------


def _monster(name: str = "Goblin", hp: int = 7, dead: bool = False, xp_value: int = 50) -> Character:
    return Character(
        name=name,
        kind="monster",
        current_hp=0 if dead else hp,
        max_hp=hp,
        dead=dead,
        xp_value=xp_value,
    )


def _pc(name: str = "Hero", hp: int = 12) -> Character:
    return Character(name=name, kind="player", current_hp=hp, max_hp=hp)


def _campaign_in_combat(*combatants: Character, active: bool = True,
                        no_hostile_turns: int = 0, turn_index: int = 0) -> Campaign:
    """Register a campaign whose combat order is exactly `combatants`, in initiative
    order. PCs/companions are added to the party."""
    c = Campaign(title="The Crypt Below")
    order = []
    for i, ch in enumerate(combatants):
        c.characters[ch.id] = ch
        if ch.kind in ("player", "companion"):
            c.party.append(ch.id)
        order.append(Combatant(character_id=ch.id, initiative=20 - i))
    c.combat = Combat(active=active, order=order, no_hostile_turns=no_hostile_turns,
                      turn_index=turn_index, round=1)
    return c


# --- the pure _combat_view surface ------------------------------------------


def test_pending_resolution_flag_appears_when_all_hostiles_dead():
    """Hostiles-dead + combat active -> the LOUD advisory flag + nudge text appear."""
    c = _campaign_in_combat(_pc(), _monster(dead=True))
    view = server._combat_view(c)
    assert view["pending_resolution"] is True
    assert view["living_hostiles"] == 0
    assert "end_combat" in view["resolution_nudge"]
    # Below the escalation threshold (streak 0): advisory, NOT urgent, no streak key.
    assert not view["resolution_nudge"].startswith("URGENT")
    assert "pending_resolution_turns" not in view


def test_flag_absent_while_a_hostile_still_lives():
    """A single living hostile in the order suppresses the whole nudge — the fight is on."""
    c = _campaign_in_combat(_pc(), _monster(dead=True), _monster(name="Goblin Boss", dead=False))
    view = server._combat_view(c)
    assert "pending_resolution" not in view
    assert "resolution_nudge" not in view
    assert "living_hostiles" not in view


def test_flag_absent_when_combat_inactive():
    """No active fight -> no nudge, even if a dead monster lingers in a stale order."""
    c = _campaign_in_combat(_pc(), _monster(dead=True), active=False)
    view = server._combat_view(c)
    assert "pending_resolution" not in view
    assert "resolution_nudge" not in view


def test_nudge_escalates_to_urgent_at_the_streak_threshold():
    """Once the no-hostile state persists across server._COMBAT_RESOLUTION_NUDGE_TURNS advances,
    the nudge escalates to URGENT and reports the streak."""
    c = _campaign_in_combat(_pc(), _monster(dead=True),
                            no_hostile_turns=server._COMBAT_RESOLUTION_NUDGE_TURNS)
    view = server._combat_view(c)
    assert view["pending_resolution"] is True
    assert view["pending_resolution_turns"] == server._COMBAT_RESOLUTION_NUDGE_TURNS
    assert view["resolution_nudge"].startswith("URGENT")


def test_nudge_is_advisory_one_below_the_threshold():
    c = _campaign_in_combat(_pc(), _monster(dead=True),
                            no_hostile_turns=server._COMBAT_RESOLUTION_NUDGE_TURNS - 1)
    view = server._combat_view(c)
    assert view["pending_resolution_turns"] == server._COMBAT_RESOLUTION_NUDGE_TURNS - 1
    assert not view["resolution_nudge"].startswith("URGENT")


# --- the next_turn streak tick (the "N consecutive advances") ---------------


@pytest.fixture
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))


def test_next_turn_increments_streak_when_no_hostile_remains(_state):
    # Current combatant is the dead monster (kind==monster => next_turn's PC-skip guard is
    # not engaged), so the advance is legal with no PC action taken.
    c = _campaign_in_combat(_monster(dead=True), _pc(), no_hostile_turns=0, turn_index=0)
    store.save_campaign(c)
    view = server.next_turn(c.id)
    assert view["pending_resolution"] is True
    reloaded = store.load_campaign(c.id)
    assert reloaded.combat.no_hostile_turns == 1


def test_next_turn_resets_streak_when_a_hostile_is_still_up(_state):
    # A living hostile is in the order and a stale streak is carried -> the advance clears it.
    living = _monster(name="Goblin Boss", dead=False)
    c = _campaign_in_combat(living, _pc(), no_hostile_turns=5, turn_index=0)
    store.save_campaign(c)
    view = server.next_turn(c.id)
    assert "pending_resolution" not in view
    reloaded = store.load_campaign(c.id)
    assert reloaded.combat.no_hostile_turns == 0


def test_end_combat_clears_the_nudge(_state):
    c = _campaign_in_combat(_pc(), _monster(dead=True), no_hostile_turns=3)
    store.save_campaign(c)
    server.end_combat(c.id, resolution="the last goblin falls")
    reloaded = store.load_campaign(c.id)
    assert reloaded.combat.active is False
    assert reloaded.combat.no_hostile_turns == 0
    # And the surface is quiet: a fresh Combat() carries no pending_resolution.
    assert "pending_resolution" not in server._combat_view(reloaded)


# --- the additive serialization contract (byte-identical round-trip) --------


def test_no_hostile_turns_omitted_from_dump_when_zero():
    """A combat with no streak dumps WITHOUT the key -> a pre-#1645 snapshot round-trips
    byte-identically and the store's dirty-skip stays a no-op on a pure load->save."""
    assert "no_hostile_turns" not in Combat().model_dump()
    assert "no_hostile_turns" not in Combat().model_dump_json()
    # A campaign with a default (out-of-combat) Combat carries no new key at all.
    assert "no_hostile_turns" not in Campaign(title="x").model_dump_json()


def test_no_hostile_turns_emitted_when_nonzero_and_round_trips():
    dumped = Combat(no_hostile_turns=4).model_dump()
    assert dumped["no_hostile_turns"] == 4
    restored = Combat.model_validate(dumped)
    assert restored.no_hostile_turns == 4


def test_next_turn_streak_ticks_through_the_pc_skip_path(_state):
    # evaOS #1654 round: the prior streak tests put the MONSTER at turn_index=0, bypassing
    # next_turn's PC-skip guard (server.py ~5476). Here the PC is the OUTGOING combatant —
    # the guard path runs — and the no-hostile streak must still tick and surface the nudge.
    c = _campaign_in_combat(_monster(dead=True), _pc(), no_hostile_turns=0, turn_index=1)
    store.save_campaign(c)
    # The guard requires the outgoing PC to have acted — declare a pass, then advance.
    pc_id = c.combat.order[1].character_id
    server.use_action(c.id, pc_id, kind="skip")
    view = server.next_turn(c.id)
    assert view["pending_resolution"] is True
    reloaded = store.load_campaign(c.id)
    assert reloaded.combat.no_hostile_turns == 1


def test_killing_blow_return_carries_the_nudge(_state, monkeypatch):
    # codex #1654 round: attack() returns its own dict (no combat view) — the closure advisory
    # must ride the killing blow's OWN return, not just the next view read.
    living = _monster(name="Last Goblin", dead=False)
    c = _campaign_in_combat(living, _pc(), no_hostile_turns=0, turn_index=1)
    living_id = c.combat.order[0].character_id
    ch = c.characters[living_id]
    ch.current_hp = 1
    pc_id = c.combat.order[1].character_id
    # attack() still rolls the d20 under a +100 bonus and a natural 1 auto-misses (≈1 run in 20
    # left the goblin alive → no nudge → CI flake, 2026-09-02). Pin the HIT through the
    # double-guarded test toggle (WORLDOS_COMBAT_TEST=1 AND sandbox campaign) so the killing
    # blow is deterministic; the nudge logic under test is unchanged.
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    c.is_sandbox = True
    c.house_rules.force_hit = True
    store.save_campaign(c)
    result = server.attack(c.id, attacker_id=pc_id, target_id=living_id,
                           attack_bonus=100, damage_dice="1d1+100", damage_type="slashing")
    assert result.get("pending_resolution") is True
    assert result.get("living_hostiles") == 0
