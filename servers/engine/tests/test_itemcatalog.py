"""Item catalog + lookup_item/find_items + catalog-augmented add_item/buy_item.

Wires the vendored SRD item dump (MagicItem/Item/Weapon/Armor.json, CC-BY-4.0)
into the engine so the DM can grant REAL gear by name. Mirrors test_bestiary.py.
"""

import pytest

import itemcatalog
import server


# --- catalog module --------------------------------------------------------


def test_catalog_loads_full_item_set():
    # 960 unique names across the 4 SRD sources (MagicItem 757 / Item 203 /
    # Weapon 38 / Armor 13, deduped first-wins on the Item/Weapon/Armor overlap).
    assert itemcatalog.count() == 960


def test_resolve_magic_item():
    rec = itemcatalog.resolve("bag of holding")  # case-insensitive
    assert rec is not None
    assert rec["name"] == "Bag of Holding"
    assert rec["kind"] == "wondrous"
    assert rec["rarity"] == "uncommon"
    assert rec["requires_attunement"] is False
    assert "interior space" in rec["description"].lower()


def test_resolve_magic_item_requiring_attunement():
    rec = itemcatalog.resolve("Cloak of Protection")
    assert rec is not None
    assert rec["kind"] == "wondrous"
    assert rec["rarity"] == "uncommon"
    assert rec["requires_attunement"] is True


def test_resolve_weapon_has_damage():
    rec = itemcatalog.resolve("Longsword")
    assert rec is not None
    assert rec["kind"] == "weapon"
    assert rec["damage"] == "1d8"
    assert rec["damage_type"] == "slashing"
    assert rec["cost"] == 15.0  # gp (from the Item record, which wins over bare Weapon)


def test_resolve_armor_has_ac():
    rec = itemcatalog.resolve("Plate Armor")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert rec["ac"] == 18
    assert rec["cost"] == 1500.0


def test_resolve_magic_weapon_resolves_damage_via_fk():
    # A magic weapon stores damage only via its weapon FK -> Weapon.json join.
    rec = itemcatalog.resolve("Frost Brand (Glaive)")
    assert rec is not None
    assert rec["kind"] == "weapon"
    assert rec["rarity"] == "very-rare"
    assert rec["damage"] == "1d10"  # joined from the glaive weapon record


def test_flattened_record_shape():
    rec = itemcatalog.resolve("Bag of Holding")
    # Every record carries the common keys; weapons/armor add damage/ac.
    for key in ("name", "kind", "rarity", "requires_attunement", "weight",
                "cost", "description", "properties"):
        assert key in rec, key
    assert isinstance(rec["properties"], list)
    assert isinstance(rec["requires_attunement"], bool)
    assert isinstance(rec["weight"], float) and isinstance(rec["cost"], float)


def test_find_potion_returns_matches():
    matches = itemcatalog.find("potion", limit=100)
    assert len(matches) >= 5
    names = [m["name"] for m in matches]
    assert all("potion" in n.lower() for n in names)
    assert any(n.startswith("Potion of") for n in names)


def test_find_respects_limit_and_sorts():
    matches = itemcatalog.find("ring", limit=3)
    assert len(matches) == 3
    assert [m["name"] for m in matches] == sorted(m["name"] for m in matches)


def test_resolve_unknown_returns_none_and_suggests():
    assert itemcatalog.resolve("florble the nonexistent gizmo") is None
    # a near-miss still yields helpful suggestions (token overlap)
    sugg = itemcatalog.suggest("cloak protection")
    assert "Cloak of Protection" in sugg


def test_resolve_loose_substring_when_unambiguous():
    # surrounding whitespace + a unique substring still resolves
    assert itemcatalog.resolve("  bag of holding  ")["name"] == "Bag of Holding"


def test_pack_precedence_srd_wins_and_pack_adds(tmp_path, monkeypatch):
    """A content pack never overrides an SRD item of the same name (srd524 is
    first-wins) but DOES contribute its own new items."""
    import json as _json
    pack = tmp_path / "fakepack"
    pack.mkdir()
    (pack / "Item.json").write_text(_json.dumps([
        # COLLISION: a bogus Longsword — must lose to the canonical SRD one
        {"model": "x.item", "pk": "x_ls", "fields": {"name": "Longsword", "cost": "99999.00",
                                                      "category": "weapon", "weight": "1.0"}},
        # a brand-new pack item — must be added
        {"model": "x.item", "pk": "x_fz", "fields": {"name": "Fizzbin Charm", "cost": "7.00",
                                                      "category": "wondrous-item", "weight": "0.0"}},
    ]))
    monkeypatch.setattr(itemcatalog, "_dirs", lambda: [itemcatalog._PRIMARY, pack])
    itemcatalog._index.cache_clear()
    itemcatalog._weapon_armor_join.cache_clear()
    try:
        # srd524 Longsword wins the collision — not the pack's bogus 99999 cost
        assert itemcatalog.resolve("Longsword")["cost"] == 15.0
        # the pack's own new item is available
        charm = itemcatalog.resolve("Fizzbin Charm")
        assert charm is not None and charm["cost"] == 7.0
        # find() lists each name once (deduped against the first-wins index)
        assert [m["name"] for m in itemcatalog.find("longsword", limit=100)].count("Longsword") == 1
        # count includes the pack's net-new item
        assert itemcatalog.count() == 961
    finally:
        itemcatalog._index.cache_clear()
        itemcatalog._weapon_armor_join.cache_clear()


# --- lookup_item / find_items tools ----------------------------------------


def test_lookup_item_tool_returns_record():
    rec = server.lookup_item("Bag of Holding")
    assert rec["name"] == "Bag of Holding" and rec["kind"] == "wondrous"
    assert "error" not in rec


def test_lookup_item_tool_miss_returns_suggestions():
    out = server.lookup_item("definitely not a real item xyzzy")
    assert "error" in out and "suggestions" in out
    assert isinstance(out["suggestions"], list)


def test_find_items_tool():
    out = server.find_items("potion", limit=5)
    assert out["count"] == 5
    assert all("potion" in i["name"].lower() for i in out["items"])


# --- catalog-augmented add_item / buy_item ---------------------------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def _item(inv, name):
    return next(i for i in inv if i["name"] == name)


def test_add_item_fills_from_catalog(cid):
    out = server.add_item(cid, "grett", item_name="Bag of Holding")
    bag = _item(out["inventory"], "Bag of Holding")
    # description enriched from the SRD record (tagged with kind/rarity)
    assert "interior space" in bag["description"].lower()
    assert "wondrous" in bag["description"].lower()


def test_add_item_catalog_carries_attunement_and_combat_tags(cid):
    out = server.add_item(cid, "grett", item_name="Cloak of Protection")
    cloak = _item(out["inventory"], "Cloak of Protection")
    assert cloak["requires_attunement"] is True

    out = server.add_item(cid, "grett", item_name="Longsword")
    sword = _item(out["inventory"], "Longsword")
    assert sword["weight"] == 3.0
    assert "1d8" in sword["description"]  # damage surfaced into the description


def test_add_item_freetext_path_unchanged(cid):
    # No item_name: the original free-text contract is byte-for-byte preserved.
    out = server.add_item(
        cid, "grett", name="custom thing", description="a weird trinket", weight=0.5
    )
    it = _item(out["inventory"], "custom thing")
    assert it["name"] == "custom thing"
    assert it["description"] == "a weird trinket"  # NOT touched by the catalog
    assert it["weight"] == 0.5
    assert it["requires_attunement"] is False


def test_add_item_explicit_values_override_catalog(cid):
    # A caller value that's explicitly set wins over the catalog fill (additive).
    out = server.add_item(
        cid, "grett", item_name="Bag of Holding", description="the party's lucky sack"
    )
    bag = _item(out["inventory"], "Bag of Holding")
    assert bag["description"] == "the party's lucky sack"


def test_add_item_unresolved_item_name_falls_back_to_name(cid):
    # An item_name that doesn't resolve is ignored; the free-text name is used.
    out = server.add_item(cid, "grett", name="Mystery Box", item_name="not a real srd item")
    assert _item(out["inventory"], "Mystery Box")["name"] == "Mystery Box"


# --- regression: adversarial review fixes (H1 catalog robustness, M1 attunement) ---


def test_int_helper_tolerates_non_numeric():
    # H1: a non-numeric AC (e.g. a homebrew pack with ac_base:"plate") must DEGRADE to
    # the default, not raise — the int() sites used to crash the whole catalog index.
    assert itemcatalog._int("plate") == 0
    assert itemcatalog._int(None) == 0
    assert itemcatalog._int("") == 0
    assert itemcatalog._int("18") == 18
    assert itemcatalog._int(18.7) == 18


def test_malformed_pack_record_does_not_sink_catalog(tmp_path, monkeypatch):
    """H1: ONE malformed record (non-numeric AC, a non-dict row, a missing name) in a
    dropped pack must not crash the ENTIRE catalog — the bestiary fault-isolation."""
    import json as _json
    pack = tmp_path / "badpack"
    pack.mkdir()
    (pack / "Item.json").write_text(_json.dumps([
        {"model": "x.item", "pk": "x_bad", "fields": {"name": "Cursed Plate",
                                                       "category": "armor", "armor_class": "plate"}},
        "not-a-dict-row",                                   # a bare string row
        {"model": "x.item", "pk": "x_empty", "fields": {}},  # no name
        {"model": "x.item", "pk": "x_ok", "fields": {"name": "Fizz Trinket",
                                                      "category": "wondrous-item", "weight": "0.0"}},
    ]))
    monkeypatch.setattr(itemcatalog, "_dirs", lambda: [itemcatalog._PRIMARY, pack])
    itemcatalog._index.cache_clear()
    itemcatalog._weapon_armor_join.cache_clear()
    try:
        # No crash: the full SRD set is intact + the good pack item loaded.
        assert itemcatalog.resolve("Longsword")["cost"] == 15.0
        assert itemcatalog.resolve("Fizz Trinket") is not None
        # the non-numeric-AC record degraded gracefully (ac=0) rather than crashing
        cursed = itemcatalog.resolve("Cursed Plate")
        assert cursed is not None and cursed["ac"] == 0
        # the non-dict row + the nameless row were skipped (no exception)
        assert itemcatalog.count() == 962  # 960 SRD + Cursed Plate + Fizz Trinket
    finally:
        itemcatalog._index.cache_clear()
        itemcatalog._weapon_armor_join.cache_clear()


def test_add_item_attunement_override_can_force_off(cid):
    # M1: requires_attunement is tri-state — passing False explicitly forces a catalog
    # attuned item (Cloak of Protection, attunement=True) DOWN to False. The old
    # boolean-`or` couldn't turn attunement off. (The default None → catalog True case
    # is covered by test_add_item_catalog_carries_attunement_and_combat_tags.)
    out = server.add_item(cid, "grett", item_name="Cloak of Protection", requires_attunement=False)
    assert _item(out["inventory"], "Cloak of Protection")["requires_attunement"] is False


def test_add_item_requires_a_name(cid):
    with pytest.raises(ValueError):
        server.add_item(cid, "grett")  # no name and no resolving item_name


def test_buy_item_uses_catalog_price(cid):
    server.adjust_currency(cid, "grett", gp=100)
    out = server.buy_item(cid, "grett", item_name="Potion of Healing")  # SRD price 50 gp
    assert out["currency"]["gp"] == 50  # 100 - 50 charged from the catalog
    potion = _item(out["inventory"], "Potion of Healing")
    assert potion["weight"] == 0.5


def test_buy_item_explicit_cost_overrides_catalog(cid):
    server.adjust_currency(cid, "grett", gp=100)
    out = server.buy_item(cid, "grett", item_name="Potion of Healing", cost_gp=10)  # haggled
    assert out["currency"]["gp"] == 90


def test_buy_item_freetext_path_unchanged(cid):
    server.adjust_currency(cid, "grett", gp=20)
    out = server.buy_item(cid, "grett", "Torch", cost_gp=1)
    assert out["currency"]["gp"] == 19
    assert _item(out["inventory"], "Torch")["name"] == "Torch"
