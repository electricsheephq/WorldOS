"""Class/subclass features at level-up (P2.3) — leveling grants real features."""

import pytest

import server
import srd_tables


def test_features_at_and_through_tables():
    assert any(f["name"] == "Extra Attack" for f in srd_tables.features_at("fighter", 5))
    names = {f["name"] for f in srd_tables.features_through("fighter", 5)}
    assert {"Second Wind", "Action Surge", "Extra Attack"} <= names


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Levels")["id"]


def test_level1_fighter_gets_creation_features(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", apply_srd_defaults=True
    )["id"]
    sheet = server.get_character(cid, fid)
    assert "Second Wind" in sheet["features"]
    assert sheet["extra_attacks"] == 0  # Extra Attack not until level 5


def test_level1_rogue_gets_sneak_attack(cid):
    rid = server.create_character(
        cid, "Sly", kind="player", class_name="Rogue", apply_srd_defaults=True
    )["id"]
    sheet = server.get_character(cid, rid)
    assert sheet["sneak_attack_dice"] == "1d6" and "Sneak Attack" in sheet["features"]


def test_level_up_grants_extra_attack(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=4, apply_srd_defaults=True
    )["id"]
    assert server.get_character(cid, fid)["extra_attacks"] == 0
    out = server.level_up(cid, fid, "Fighter")  # -> level 5
    assert any(f["name"] == "Extra Attack" for f in out["_features_gained"])
    sheet = server.get_character(cid, fid)
    assert sheet["extra_attacks"] == 1 and "Extra Attack" in sheet["features"]


def test_rogue_sneak_attack_scales_on_level_up(cid):
    rid = server.create_character(
        cid, "Sly", kind="player", class_name="Rogue", level=2, apply_srd_defaults=True
    )["id"]
    assert server.get_character(cid, rid)["sneak_attack_dice"] == "1d6"
    server.level_up(cid, rid, "Rogue")  # -> level 3
    assert server.get_character(cid, rid)["sneak_attack_dice"] == "2d6"
