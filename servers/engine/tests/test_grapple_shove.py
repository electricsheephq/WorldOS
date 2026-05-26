"""Tests for SRD 5.2 (2024) Grapple / Shove / Escape Grapple.

Rules grounded on:
  - ClassFeature srd-2024_monk_martial-arts: "the Grapple or Shove option of your
    Unarmed Strike … save DC" (Dex modifier may replace STR for Monks).
  - ConditionDescription srd-2024_grappled (Speed 0; Disadvantage vs non-grappler).
  - ConditionDescription srd-2024_prone (restricted movement; adv/disadv on attacks).
  - Creature escape-DC pattern (escape DC = same formula as the grapple DC).
  DC = 8 + attacker's Strength modifier + attacker's proficiency bonus.
  Target rolls STR or DEX save (best of the two by default).
  Escape: Athletics (STR) or Acrobatics (DEX) — best of the two.

Dice are forced by monkeypatching dice.roll to return a controlled DiceRoll so
the outcome is deterministic without relying on seeds (the seed approach works at
the dice.py level but not when the server resolves the *full* save expression with
a variable bonus). We patch at the `server` import boundary so the server code's
`dice_mod.roll(...)` call is intercepted.
"""

from __future__ import annotations

import pytest
from dice import DiceRoll

import combat
import server
from models import Ability, Character, Condition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_char(**kw) -> Character:
    return Character(name="T", **kw)


def _fake_roll(natural: int, bonus: int = 0) -> DiceRoll:
    """Build a DiceRoll that looks like `1d20+bonus` with a fixed natural result."""
    total = natural + bonus
    return DiceRoll(
        expression=f"1d20+{bonus}",
        total=total,
        rolls=[natural],
        is_d20=True,
        natural=natural,
        crit=(natural == 20),
        fumble=(natural == 1),
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


# ---------------------------------------------------------------------------
# Pure combat helpers
# ---------------------------------------------------------------------------

def test_grapple_save_dc_formula():
    """DC = 8 + STR mod + proficiency bonus (sourced from attacker sheet)."""
    # STR 16 -> mod +3; proficiency 3 -> DC = 8+3+3 = 14
    attacker = _make_char(abilities={"strength": 16}, proficiency_bonus=3)
    assert combat.grapple_save_dc(attacker) == 14


def test_grapple_save_dc_min():
    """STR 10 (mod 0) + prof 2 -> DC 10."""
    attacker = _make_char(abilities={"strength": 10}, proficiency_bonus=2)
    assert combat.grapple_save_dc(attacker) == 10


def test_best_save_ability_prefers_dex_when_higher():
    target = _make_char(
        abilities={"strength": 10, "dexterity": 16},
        proficiency_bonus=2,
        saving_throw_proficiencies=[Ability.DEX],
    )
    # DEX bonus = +3 + 2 = +5; STR = 0; DEX wins
    assert combat.best_save_ability(target) == Ability.DEX


def test_best_save_ability_falls_back_to_str_on_tie():
    target = _make_char(abilities={"strength": 10, "dexterity": 10}, proficiency_bonus=2)
    # Both bonuses equal — tie -> STR
    assert combat.best_save_ability(target) == Ability.STR


# ---------------------------------------------------------------------------
# Server-level test helpers
# ---------------------------------------------------------------------------

def _make_pair(str_score: int = 16, prof: int = 2,
               target_str: int = 8, target_dex: int = 8) -> tuple[str, str, str]:
    """Create campaign + attacker + target; return (cid, attacker_id, target_id).
    proficiency_bonus is set via update_character since create_character derives it
    from class level — we force it explicitly for controlled DC calculations."""
    cid = server.create_campaign("GrappleTest")["id"]
    att_id = server.create_character(
        cid, "Attacker", kind="player",
        max_hp=10,
        abilities={"strength": str_score, "dexterity": 10},
    )["id"]
    server.update_character(cid, att_id, {"proficiency_bonus": prof})
    tgt_id = server.create_character(
        cid, "Target", kind="monster",
        max_hp=10,
        abilities={"strength": target_str, "dexterity": target_dex},
    )["id"]
    return cid, att_id, tgt_id


# ---------------------------------------------------------------------------
# Grapple MCP tool — via server
# ---------------------------------------------------------------------------

def test_grapple_fail_applies_condition(monkeypatch):
    """A failed save (roll below DC) must apply the Grappled condition."""
    cid, att_id, tgt_id = _make_pair(str_score=16, prof=2)  # DC = 8+3+2 = 13
    # Force a low roll (natural 1, total 1+bonus) — the target has STR 8 (mod -1), so
    # save total = 1 + (-1) = 0, well below DC 13.
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    result = server.grapple(cid, att_id, tgt_id)
    assert result["success"] is False
    assert result["applied"] is True
    ch = server.get_character(cid, tgt_id)
    assert "grappled" in ch["conditions"]


def test_grapple_success_no_condition(monkeypatch):
    """A successful save must NOT apply the Grappled condition."""
    cid, att_id, tgt_id = _make_pair(str_score=10, prof=2)  # DC = 8+0+2 = 10
    # Force a high roll: natural 20 -> total 19 after mod; beats DC 10
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, -1))
    result = server.grapple(cid, att_id, tgt_id)
    assert result["success"] is True
    assert result["applied"] is False
    ch = server.get_character(cid, tgt_id)
    assert "grappled" not in ch["conditions"]


def test_grapple_dc_sourced_from_attacker_str_and_prof(monkeypatch):
    """DC must equal 8 + attacker STR mod + attacker prof — verify the returned dc field."""
    # STR 20 -> mod +5; prof 4 -> DC = 17
    cid, att_id, tgt_id = _make_pair(str_score=20, prof=4)
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, 0))
    result = server.grapple(cid, att_id, tgt_id)
    assert result["dc"] == 17


def test_grapple_uses_better_save_ability(monkeypatch):
    """Engine picks the target's better save ability (STR vs DEX) by default."""
    cid = server.create_campaign("savechoice")["id"]
    att_id = server.create_character(
        cid, "A", kind="player", max_hp=10,
        abilities={"strength": 10},
    )["id"]
    server.update_character(cid, att_id, {"proficiency_bonus": 2})
    # Target has high DEX save bonus, low STR
    tgt_id = server.create_character(
        cid, "T", kind="monster", max_hp=10,
        abilities={"strength": 8, "dexterity": 16},
    )["id"]
    server.update_character(cid, tgt_id, {
        "proficiency_bonus": 2,
        "saving_throw_proficiencies": ["dex"],
    })
    calls: list[str] = []
    def capture_roll(expr, **kw):
        calls.append(expr)
        return _fake_roll(10, 0)
    monkeypatch.setattr("server.dice_mod.roll", capture_roll)
    result = server.grapple(cid, att_id, tgt_id)
    # Should pick DEX (bonus = +3 dex + 2 prof = +5; STR = -1)
    assert result["save_ability"] == "dex"


def test_grapple_explicit_save_ability(monkeypatch):
    """save_ability override forces the specified ability even if suboptimal."""
    cid, att_id, tgt_id = _make_pair(target_str=8, target_dex=18)
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(10, 0))
    result = server.grapple(cid, att_id, tgt_id, save_ability="str")
    assert result["save_ability"] == "str"


def test_grapple_high_str_attacker_vs_low_save_target(monkeypatch):
    """High-STR attacker reliably grapples a weak target on a middling natural roll."""
    # STR 20 -> DC = 8+5+2 = 15; target STR 6 (mod -2), no save prof -> bonus = -2
    # Natural 8: total = 8 + (-2) = 6 < 15 -> fail
    cid = server.create_campaign("highstr")["id"]
    att_id = server.create_character(
        cid, "Strongarm", kind="player", max_hp=10,
        abilities={"strength": 20},
    )["id"]
    server.update_character(cid, att_id, {"proficiency_bonus": 2})
    tgt_id = server.create_character(
        cid, "Weakling", kind="monster", max_hp=10,
        abilities={"strength": 6, "dexterity": 6},
    )["id"]
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(8, -2))
    result = server.grapple(cid, att_id, tgt_id)
    assert result["dc"] == 15
    assert result["success"] is False
    assert result["applied"] is True


def test_grapple_immune_target(monkeypatch):
    """A condition-immune target is not grappled even on a failed save."""
    cid, att_id, tgt_id = _make_pair()
    server.update_character(cid, tgt_id, {"condition_immunities": ["grappled"]})
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    result = server.grapple(cid, att_id, tgt_id)
    assert result["applied"] is False
    ch = server.get_character(cid, tgt_id)
    assert "grappled" not in ch["conditions"]


def test_grapple_idempotent_condition(monkeypatch):
    """Grappling an already-grappled target doesn't duplicate the condition."""
    cid, att_id, tgt_id = _make_pair()
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    server.grapple(cid, att_id, tgt_id)
    server.grapple(cid, att_id, tgt_id)
    ch = server.get_character(cid, tgt_id)
    assert ch["conditions"].count("grappled") == 1


# ---------------------------------------------------------------------------
# Shove MCP tool
# ---------------------------------------------------------------------------

def test_shove_prone_applies_prone(monkeypatch):
    """Failed save with mode='prone' applies the Prone condition."""
    cid, att_id, tgt_id = _make_pair()
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    result = server.shove(cid, att_id, tgt_id, mode="prone")
    assert result["success"] is False
    assert result["applied"] is True
    assert result["pushed"] == 0
    ch = server.get_character(cid, tgt_id)
    assert "prone" in ch["conditions"]


def test_shove_push_no_condition(monkeypatch):
    """Failed save with mode='push' sets pushed=5 and applies no condition."""
    cid, att_id, tgt_id = _make_pair()
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    result = server.shove(cid, att_id, tgt_id, mode="push")
    assert result["success"] is False
    assert result["pushed"] == 5
    assert result["applied"] is False
    ch = server.get_character(cid, tgt_id)
    assert "prone" not in ch["conditions"]


def test_shove_success_no_prone(monkeypatch):
    """A successful save results in no condition regardless of mode."""
    cid, att_id, tgt_id = _make_pair(str_score=10, prof=2)  # DC 10
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, 0))
    result = server.shove(cid, att_id, tgt_id, mode="prone")
    assert result["success"] is True
    assert result["applied"] is False
    ch = server.get_character(cid, tgt_id)
    assert "prone" not in ch["conditions"]


def test_shove_invalid_mode():
    """An invalid mode value must raise."""
    cid, att_id, tgt_id = _make_pair()
    with pytest.raises(ValueError, match="mode"):
        server.shove(cid, att_id, tgt_id, mode="flip")


def test_shove_uses_same_dc_as_grapple(monkeypatch):
    """Shove DC must equal grapple DC (same formula — 2024 Unarmed Strike rules)."""
    cid, att_id, tgt_id = _make_pair(str_score=18, prof=3)  # DC = 8+4+3 = 15
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, 0))
    g_result = server.grapple(cid, att_id, tgt_id)
    s_result = server.shove(cid, att_id, tgt_id, mode="prone")
    assert g_result["dc"] == s_result["dc"] == 15


# ---------------------------------------------------------------------------
# Escape Grapple MCP tool
# ---------------------------------------------------------------------------

def test_escape_removes_grappled_on_success(monkeypatch):
    """A successful escape roll removes the Grappled condition."""
    cid, att_id, tgt_id = _make_pair()
    # First grapple the target
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    server.grapple(cid, att_id, tgt_id)
    ch = server.get_character(cid, tgt_id)
    assert "grappled" in ch["conditions"]

    # Now escape with a high roll
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, 2))
    result = server.escape_grapple(cid, tgt_id, att_id)
    assert result["success"] is True
    assert result["escaped"] is True
    ch2 = server.get_character(cid, tgt_id)
    assert "grappled" not in ch2["conditions"]


def test_escape_fails_leaves_grappled(monkeypatch):
    """A failed escape roll leaves the Grappled condition in place."""
    cid, att_id, tgt_id = _make_pair(str_score=16, prof=2)  # DC 13
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    server.grapple(cid, att_id, tgt_id)

    # Low escape roll
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(3, 0))
    result = server.escape_grapple(cid, tgt_id, att_id)
    assert result["success"] is False
    assert result["escaped"] is False
    ch = server.get_character(cid, tgt_id)
    assert "grappled" in ch["conditions"]


def test_escape_dc_matches_grapple_dc(monkeypatch):
    """escape_grapple recomputes the grappler's DC from the sheet — must match grapple's dc."""
    cid, att_id, tgt_id = _make_pair(str_score=14, prof=3)  # DC = 8+2+3 = 13
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    g_result = server.grapple(cid, att_id, tgt_id)

    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(20, 2))
    esc_result = server.escape_grapple(cid, tgt_id, att_id)
    assert esc_result["dc"] == g_result["dc"] == 13


def test_escape_uses_best_athletics_or_acrobatics(monkeypatch):
    """Escape picks Athletics or Acrobatics — whichever gives the higher total."""
    cid = server.create_campaign("esc")["id"]
    att_id = server.create_character(
        cid, "Grappler", kind="player", max_hp=10,
        abilities={"strength": 10},
    )["id"]
    server.update_character(cid, att_id, {"proficiency_bonus": 2})
    # Target: high DEX (Acrobatics better than Athletics when proficient)
    tgt_id = server.create_character(
        cid, "Escapee", kind="player", max_hp=10,
        abilities={"strength": 8, "dexterity": 16},
        skills=["acrobatics"],  # +3 dex + 2 prof = +5 acrobatics; athletics = -1
    )["id"]
    server.update_character(cid, tgt_id, {"proficiency_bonus": 2})
    # Grapple first
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(1, -1))
    server.grapple(cid, att_id, tgt_id)

    calls: list[str] = []
    def capture(expr, **kw):
        calls.append(expr)
        return _fake_roll(15, 5)
    monkeypatch.setattr("server.dice_mod.roll", capture)
    result = server.escape_grapple(cid, tgt_id, att_id)
    # Should have picked acrobatics (the higher skill)
    assert result["skill"] == "acrobatics"
