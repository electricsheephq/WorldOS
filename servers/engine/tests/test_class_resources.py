"""Class-resource pools (parity gap 1.3) — depletable per-rest pools like fables'
Lay on Hands 15/15 and Channel Divinity 2/2 bars. Additive: a character with no
pools behaves exactly as before."""

import pytest

import rests
import server
import srd_tables
from dice import DiceRoll
from models import Character


def mk(**kw) -> Character:
    return Character(name="T", **kw)


def fixed_roll(total: int):
    def _roll(expr, *a, **k):
        return DiceRoll(expression=expr, total=total, rolls=[total])

    return _roll


# --- pure srd_tables derivation ---
def test_paladin_resource_table():
    # Lay on Hands = 5 x level; Channel Divinity from L3.
    res = srd_tables.class_resources_through("paladin", 5)
    assert res["lay_on_hands"]["max"] == 25 and res["lay_on_hands"]["recharge"] == "long"
    assert res["channel_divinity"]["max"] == 2
    # No Channel Divinity at L1/L2 (additive — pool simply absent).
    assert "channel_divinity" not in srd_tables.class_resources_through("paladin", 2)


def test_resource_formulas_across_classes():
    assert srd_tables.class_resources_through("barbarian", 1)["rage"]["max"] == 2
    assert srd_tables.class_resources_through("barbarian", 6)["rage"]["max"] == 4
    assert srd_tables.class_resources_through("monk", 5)["ki"]["max"] == 5  # = level
    assert srd_tables.class_resources_through("sorcerer", 4)["sorcery_points"]["max"] == 4
    # Bardic Inspiration = CHA mod; short-rest recharge once Font of Inspiration (L5).
    assert srd_tables.class_resources_through("bard", 1, cha_mod=3)["bardic_inspiration"] == {
        "max": 3,
        "recharge": "long",
    }
    assert srd_tables.class_resources_through("bard", 5, cha_mod=3)["bardic_inspiration"]["recharge"] == "short"
    f = srd_tables.class_resources_through("fighter", 2)
    assert f["second_wind"]["max"] == 1 and f["second_wind"]["recharge"] == "short"
    assert f["action_surge"]["max"] == 1
    assert "ki" not in srd_tables.class_resources_through("monk", 1)  # no Ki before L2
    assert srd_tables.class_resources_through("wizard", 5) == {}  # no pools


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Resources")["id"]


# --- a Paladin gets Lay on Hands (5 x level) + Channel Divinity ---
def test_paladin_creation_populates_pools(cid):
    pid = server.create_character(
        cid, "Aelar", kind="player", class_name="Paladin", level=5,
        apply_srd_defaults=True, abilities={"charisma": 16, "constitution": 14},
    )["id"]
    sheet = server.get_character(cid, pid)
    pools = sheet["class_resources"]
    assert pools["lay_on_hands"]["max"] == 25 and pools["lay_on_hands"]["used"] == 0
    assert pools["channel_divinity"]["max"] == 2
    # fables-style bar view.
    assert sheet["class_resources_view"]["lay_on_hands"]["label"] == "25/25"
    assert sheet["class_resources_view"]["channel_divinity"]["remaining"] == 2


# --- use_resource depletes + refuses when empty ---
def test_use_resource_depletes_and_refuses_when_empty(cid):
    pid = server.create_character(
        cid, "Aelar", kind="player", class_name="Paladin", level=3,
        apply_srd_defaults=True, abilities={"charisma": 14},
    )["id"]
    # Channel Divinity: 2 uses at L3.
    out = server.use_resource(cid, pid, "channel_divinity")
    assert out["ok"] is True and out["remaining"] == 1
    out = server.use_resource(cid, pid, "channel_divinity")
    assert out["ok"] is True and out["remaining"] == 0
    # Now empty — refuses without changing state, no exception.
    out = server.use_resource(cid, pid, "channel_divinity")
    assert out["ok"] is False and "not enough" in out["error"]
    assert server.get_character(cid, pid)["class_resources"]["channel_divinity"]["used"] == 2

    # Lay on Hands spends hit points (amount > 1); over-spend is refused.
    out = server.use_resource(cid, pid, "lay_on_hands", amount=10)  # pool is 15 at L3
    assert out["ok"] is True and out["remaining"] == 5
    out = server.use_resource(cid, pid, "lay_on_hands", amount=10)
    assert out["ok"] is False and out["remaining"] == 5  # unchanged


def test_use_resource_unknown_pool(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", apply_srd_defaults=True
    )["id"]
    out = server.use_resource(cid, fid, "rage")  # fighter has no rage
    assert out["ok"] is False and "no 'rage' pool" in out["error"]
    assert "second_wind" in out["available"]


# --- short_rest restores short-recharge pools ---
def test_short_rest_restores_short_pools(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=2,
        apply_srd_defaults=True, abilities={"constitution": 14},
    )["id"]
    server.use_resource(cid, fid, "second_wind")
    server.use_resource(cid, fid, "action_surge")
    out = server.short_rest(cid, fid)
    assert set(out["resources_restored"]) == {"second_wind", "action_surge"}
    sheet = server.get_character(cid, fid)
    assert sheet["class_resources"]["second_wind"]["used"] == 0
    assert sheet["class_resources"]["action_surge"]["used"] == 0


def test_short_rest_leaves_long_pools_depleted(cid):
    pid = server.create_character(
        cid, "Aelar", kind="player", class_name="Paladin", level=3,
        apply_srd_defaults=True, abilities={"charisma": 14},
    )["id"]
    server.use_resource(cid, pid, "lay_on_hands", amount=5)  # long-recharge
    out = server.short_rest(cid, pid)
    assert "lay_on_hands" not in out["resources_restored"]
    assert server.get_character(cid, pid)["class_resources"]["lay_on_hands"]["used"] == 5


# --- long_rest restores ALL (short + long) ---
def test_long_rest_restores_all_pools(cid):
    # Paladin/Fighter multiclass to exercise both a long pool and short pools.
    pid = server.create_character(
        cid, "Aelar", kind="player", class_name="Paladin", level=3,
        apply_srd_defaults=True, abilities={"charisma": 14, "constitution": 14, "strength": 13},
    )["id"]
    server.level_up(cid, pid, "Fighter")  # multiclass: adds Second Wind (short)
    server.use_resource(cid, pid, "lay_on_hands", amount=5)
    server.use_resource(cid, pid, "channel_divinity")
    server.use_resource(cid, pid, "second_wind")
    out = server.long_rest(cid, pid)
    assert set(out["resources_restored"]) >= {"lay_on_hands", "channel_divinity", "second_wind"}
    sheet = server.get_character(cid, pid)
    for rid in ("lay_on_hands", "channel_divinity", "second_wind"):
        assert sheet["class_resources"][rid]["used"] == 0


# --- additive default: a character with no/empty pools still works ---
def test_level1_no_pool_class_unchanged(cid):
    wid = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True,
        abilities={"intelligence": 16},
    )["id"]
    sheet = server.get_character(cid, wid)
    assert sheet["class_resources"] == {}
    assert sheet["class_resources_view"] == {}
    # Rests on an empty-pool character behave exactly as before.
    assert server.long_rest(cid, wid)["resources_restored"] == []
    assert server.short_rest(cid, wid)["resources_restored"] == []


def test_bare_character_deserializes_and_rests(cid):
    # A character created WITHOUT apply_srd_defaults (no pools at all) — the
    # additive default must not break creation, get_character, or rests.
    bid = server.create_character(cid, "Nobody", kind="npc")["id"]
    sheet = server.get_character(cid, bid)
    assert sheet["class_resources"] == {}
    # Pure-helper rests on a hand-built Character with no pools.
    ch = mk(max_hp=10, current_hp=5, hit_dice="1d8", hit_dice_remaining=1)
    assert rests.short_rest(ch, 0, fixed_roll(1))["resources_restored"] == []
    assert rests.long_rest(ch)["resources_restored"] == []


# --- Battle Master DAMAGE maneuver (#213): use_resource rolls the die + stashes a pending bonus ---
def test_use_resource_maneuver_rolls_die_and_stashes_pending(cid, monkeypatch):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=3, apply_srd_defaults=True
    )["id"]
    server.set_class_resource(cid, fid, "superiority_dice", max=4, recharge="short", size="d8")
    # Deterministic die: 1d8 -> 5.
    monkeypatch.setattr(server.dice_mod, "roll", fixed_roll(5))
    out = server.use_resource(cid, fid, "superiority_dice", maneuver="Trip Attack")
    assert out["ok"] is True and out["remaining"] == 3
    assert out["maneuver_damage"]["die"] == "1d8" and out["maneuver_damage"]["rolled"] == 5
    pdb = server.get_character(cid, fid)["pending_damage_bonus"]
    assert pdb["amount"] == 5 and pdb["source"] == "Trip Attack" and pdb["resource"] == "superiority_dice"


def test_use_resource_no_maneuver_sets_no_pending_bonus(cid):
    # ADDITIVE: a plain superiority-die spend (no maneuver) never creates a pending bonus.
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=3, apply_srd_defaults=True
    )["id"]
    server.set_class_resource(cid, fid, "superiority_dice", max=4, recharge="short", size="d8")
    out = server.use_resource(cid, fid, "superiority_dice")  # no maneuver
    assert out["ok"] is True and "maneuver_damage" not in out
    assert server.get_character(cid, fid)["pending_damage_bonus"] is None


def test_use_resource_maneuver_amount_rolls_that_many_dice(cid, monkeypatch):
    # amount=2 rolls 2d8 (the expression passed to the roller carries the count).
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=3, apply_srd_defaults=True
    )["id"]
    server.set_class_resource(cid, fid, "superiority_dice", max=4, recharge="short", size="d8")
    seen = {}
    def _roll(expr, *a, **k):
        seen["expr"] = expr
        return DiceRoll(expression=expr, total=9, rolls=[9])
    monkeypatch.setattr(server.dice_mod, "roll", _roll)
    out = server.use_resource(cid, fid, "superiority_dice", amount=2, maneuver="Trip Attack")
    assert seen["expr"] == "2d8" and out["remaining"] == 2 and out["maneuver_damage"]["rolled"] == 9


def test_use_resource_preserved_across_level_up(cid):
    # Leveling up must NOT silently refill a half-spent pool (preserve `used`).
    pid = server.create_character(
        cid, "Aelar", kind="player", class_name="Paladin", level=3,
        apply_srd_defaults=True, abilities={"charisma": 14, "constitution": 14},
    )["id"]
    server.use_resource(cid, pid, "lay_on_hands", amount=10)  # 15 -> 5 left
    server.level_up(cid, pid, "Paladin")  # L3 -> L4: pool max grows to 20
    sheet = server.get_character(cid, pid)
    # max grew (5*4=20) but the 10 already spent is preserved.
    assert sheet["class_resources"]["lay_on_hands"]["max"] == 20
    assert sheet["class_resources"]["lay_on_hands"]["used"] == 10
