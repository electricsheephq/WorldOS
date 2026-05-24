"""Regressions for the pre-release adversarial audit (GitHub issues #40-#55).

Each test mirrors the issue's repro sketch so a fix is provably tied to a filed finding.
Grouped by area; the issue number is in each test name.
"""
import pytest

import combat
import content
import server
import store
from models import Character


# ── #40/#41 engine-state: path containment + stable character id ──
def test_issue40_path_like_ids_cannot_escape_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path / "state"))
    for bad in ("../../escape", "/tmp/abs", "..", "a/b", ""):
        with pytest.raises(ValueError):
            with store.campaign_lock(bad):
                pass
    assert not (tmp_path / "escape").exists()  # no lock dir leaked outside the root
    with pytest.raises(ValueError):
        content.load_world_data("../../../etc")


def test_issue41_update_character_cannot_change_the_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("ids")["id"]
    old = server.create_character(cid, "Hero")["id"]
    server.update_character(cid, old, {"id": "visible_unusable", "armor_class": 15})
    assert server.get_state(cid)["party"][0]["id"] == old  # id stayed the stable handle
    assert server.get_character(cid, old)["name"] == "Hero"  # still usable under it
    assert server.get_character(cid, old)["armor_class"] == 15  # the rest of the patch applied


def _campaign():
    return server.create_campaign("adv")["id"]


# ── #42-#45 mechanics: conditions enforce action/save/immunity + concentration ──
def test_issue42_incapacitated_cannot_act_or_attack(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    target = server.create_character(cid, "Target", kind="monster")["id"]
    server.start_combat(cid, [actor, target])
    server.add_condition(cid, actor, "unconscious")
    assert server.use_action(cid, actor, "action")["ok"] is False
    with pytest.raises(ValueError):
        server.attack(cid, actor, target, attack_bonus=99, damage_dice="1")


def test_issue43_condition_saves_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    server.add_condition(cid, actor, "unconscious")
    out = server.saving_throw(cid, actor, "dex", 1)  # would auto-succeed on the roll
    assert out["success"] is False and "condition" in out["reason"]
    # a CON save is unaffected by these conditions (only STR/DEX auto-fail).
    assert "reason" not in server.saving_throw(cid, actor, "con", 1)


def test_issue43_restrained_gives_dex_save_disadvantage(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    server.add_condition(cid, actor, "restrained")
    assert server.saving_throw(cid, actor, "dex", 10).get("disadvantage") is True


def test_issue44_condition_immunity_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    mon = server.create_character(cid, "ImpX", kind="monster")["id"]
    server.update_character(cid, mon, {"condition_immunities": ["poisoned"]})
    out = server.add_condition(cid, mon, "poisoned")
    assert out["immune"] is True and "poisoned" not in out["conditions"]


def test_issue45_temp_hp_does_not_suppress_concentration_check():
    ch = Character(name="Caster", max_hp=20, current_hp=20, temp_hp=30, concentration="Bless")
    out = combat.apply_damage(ch, 30)  # all absorbed by temp HP, but damage was still taken
    assert out["concentration_dc"] == 15
