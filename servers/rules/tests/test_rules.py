import pytest

import server


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Never hit the network in tests; exercise only the bundled SRD data.
    monkeypatch.setenv("WORLDOS_RULES_OFFLINE", "1")
    yield


def test_bundled_loaded():
    # Full SRD 5.2.1 set is merged in, so totals are far larger than the
    # starter slice. All 14 starter conditions + the curated starter spells /
    # monsters / rules must survive the merge.
    assert len(server._CONDITIONS) >= 14
    assert "goblin" in server._MONSTERS  # starter-only entry preserved
    assert "fire bolt" in server._SPELLS  # starter entry preserved
    assert len(server._RULES) >= 4
    # The full set contributes a large long tail offline.
    assert len(server._SPELLS) > 100
    assert len(server._MONSTERS) > 100


def test_starter_curated_fields_preserved_on_collision():
    # "fire bolt" exists in both layers; the full-set entry wins but the curated
    # starter record (with engine-facing `mechanics`) is kept under `_starter`.
    fb = server._SPELLS["fire bolt"]
    assert "_open5e" in fb  # came from / was overlaid by the full set
    assert "mechanics" in fb["_starter"]
    assert fb["_starter"]["mechanics"]["damage"] == "1d10"


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


def test_full_set_spell_found_offline():
    # Fireball is NOT in the starter slice; it must now resolve from the full
    # SRD 5.2.1 set with the network disabled.
    r = server.find_spell("Fireball")
    assert r is not None
    assert r["name"] == "Fireball"
    assert r["level"] == 3
    assert r["school"] == "Evocation"
    assert r["_source"] == "srd-bundled"


def test_full_set_monster_found_offline():
    # Tarrasque was previously the canonical "not bundled" example; it is now
    # vendored in the full set and must resolve offline with a real stat block.
    r = server.find_monster("Tarrasque")
    assert r is not None
    assert r["name"] == "Tarrasque"
    assert r["cr"] == "30"
    assert r["abilities"]["str"] == 30
    assert r["actions"]  # actions joined from CreatureAction fixtures
    assert r["_source"] == "srd-bundled"


def test_full_set_spell_fuzzy_offline():
    # Fuzzy matching also works across the full set.
    assert server.find_spell("firebal")["name"] == "Fireball"


def test_full_set_new_categories_offline():
    # Categories that only exist in the full set: items, feats, classes,
    # backgrounds, species.
    longsword = server.find_item("Longsword")
    assert longsword is not None and longsword["damage"] == "1d8"
    bag = server.find_item("Bag of Holding")
    assert bag is not None and bag["rarity"] == "uncommon"
    assert server.find_feat("Alert") is not None
    wizard = server.find_class("Wizard")
    assert wizard is not None and wizard["hit_dice"].lower() == "d6"
    assert server.find_background("Acolyte") is not None
    assert server.find_species("Elf") is not None


def test_still_unknown_offline_returns_none():
    # Something genuinely absent from both layers still returns None offline.
    assert server.find_monster("definitely-not-a-real-monster-xyz") is None
    assert server.find_spell("not-a-spell-zzz") is None


def test_search_substring():
    names = {h["name"] for h in server.search("fire")}
    assert "Fire Bolt" in names
    assert "Fireball" in names  # full-set entry is searchable


def test_search_category_filter():
    hits = server.search("go", category="monster")
    assert any(h["name"] == "Goblin" for h in hits)
    assert all(h["category"] == "monster" for h in hits)


def test_wrap_helper():
    assert server._wrap({"name": "Prone"}, "prone")["found"] is True
    miss = server._wrap(None, "tarrasque")
    assert miss["found"] is False and miss["query"] == "tarrasque"
