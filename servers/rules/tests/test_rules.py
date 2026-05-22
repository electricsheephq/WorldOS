import pytest

import server


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Never hit the network in tests; exercise only the bundled SRD data.
    monkeypatch.setenv("CLAWDND_RULES_OFFLINE", "1")
    yield


def test_bundled_loaded():
    assert len(server._CONDITIONS) == 14
    assert "goblin" in server._MONSTERS
    assert "fire bolt" in server._SPELLS
    assert len(server._RULES) >= 4


def test_condition_exact():
    r = server.find_condition("prone")
    assert r is not None
    assert r["name"] == "Prone"
    assert r["_source"] == "srd-bundled"


def test_condition_fuzzy_typo():
    r = server.find_condition("prnoe")
    assert r is not None and r["name"] == "Prone"


def test_spell_lookup():
    r = server.find_spell("fire bolt")
    assert r is not None
    assert r["level"] == 0
    assert r["school"] == "Evocation"


def test_spell_fuzzy_spacing():
    r = server.find_spell("magicmissile")
    assert r is not None and r["name"] == "Magic Missile"


def test_monster_lookup():
    r = server.find_monster("goblin")
    assert r is not None
    assert r["ac"] == 15 and r["hp"] == 7
    assert r["abilities"]["dex"] == 14


def test_rule_fuzzy():
    r = server.find_rule("advantage")
    assert r is not None and "Advantage" in r["name"]


def test_unknown_offline_returns_none():
    assert server.find_monster("tarrasque") is None  # not bundled + offline


def test_search_substring():
    names = {h["name"] for h in server.search("fire")}
    assert "Fire Bolt" in names


def test_search_category_filter():
    hits = server.search("go", category="monster")
    assert any(h["name"] == "Goblin" for h in hits)
    assert all(h["category"] == "monster" for h in hits)


def test_wrap_helper():
    assert server._wrap({"name": "Prone"}, "prone")["found"] is True
    miss = server._wrap(None, "tarrasque")
    assert miss["found"] is False and miss["query"] == "tarrasque"
