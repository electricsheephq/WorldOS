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


# --- weapon RANGE (B / RRI-25e55fa optimizer: "Heavy Crossbow has no 100/400 ft") ----
# A ranged or thrown weapon carries a normal/long range from the SRD Weapon record; the
# catalog dropped it so the inspector showed no range. Additive `range` field ("100/400")
# from the SRD `range`/`long_range`. A pure-melee weapon (range 0) carries no range string.

def test_flattened_record_shape():
    rec = itemcatalog.resolve("Bag of Holding")
    # Every record carries the common keys; weapons/armor add damage/ac.
    for key in ("name", "kind", "rarity", "requires_attunement", "weight",
                "cost", "description", "properties"):
        assert key in rec, key
    assert isinstance(rec["properties"], list)
    assert isinstance(rec["requires_attunement"], bool)
    assert isinstance(rec["weight"], float)
    # cost is TRI-STATE (F09-3): None = no listed price (every magic item);
    # a priced item carries a float.
    assert rec["cost"] is None
    assert isinstance(itemcatalog.resolve("Longsword")["cost"], float)


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
    # F09-1: a TRUE substring (not collapsing to the exact index key) + surrounding
    # whitespace resolves via the fuzzy branch — this used to raise AttributeError
    # ('dict' object has no attribute 'lower') on every unique-substring match.
    assert itemcatalog.resolve("  bag of hold  ")["name"] == "Bag of Holding"


def test_resolve_unique_substring_returns_the_record():
    # F09-1: the fuzzy branch returns the full catalog record, same shape as exact.
    rec = itemcatalog.resolve("bag of hold")
    assert rec is not None and rec["name"] == "Bag of Holding"
    assert rec["kind"] == "wondrous"


def test_resolve_ambiguous_substring_returns_none():
    # 2+ matches stay ambiguous -> None (the caller offers find()/suggest()).
    assert itemcatalog.resolve("potion of") is None


# --- #756: base-name disambiguation + weapon property/versatile enrichment ---


def test_resolve_base_armor_name_disambiguates_to_canonical_record():
    """#756 (the CRITICAL gate-flipper from the RRI-5e98e6f optimizer sweep): the
    market/inventory show 'Studded Leather' but the catalog keys it 'Studded Leather
    Armor', and a bare-substring resolve is AMBIGUOUS (Armor of Resistance (Studded
    Leather), Glamoured Studded Leather, …) -> None -> no AC value -> 'impossible to
    evaluate the upgrade'. The base name + an armor/weapon suffix must resolve to the
    canonical record so the inspector shows the real AC."""
    rec = itemcatalog.resolve("Studded Leather")
    assert rec is not None, "'Studded Leather' must resolve (it did not before #756)"
    assert rec["name"] == "Studded Leather Armor"
    assert rec["kind"] == "armor"
    assert rec["ac"] == 12  # the real base AC the optimizer could not see
    # the short forms a merchant/inventory actually carry all resolve
    for short in ("studded leather", "Leather", "Plate", "Hide", "Splint"):
        assert itemcatalog.resolve(short) is not None, short


def test_base_name_disambiguation_does_not_override_an_exact_or_ambiguous_match():
    # An exact key still wins (no regression): "Leather Armor" stays itself.
    assert itemcatalog.resolve("Leather Armor")["name"] == "Leather Armor"
    # A genuinely ambiguous prefix that is NOT a base+suffix name stays None.
    assert itemcatalog.resolve("potion of") is None


def test_resolve_weapon_exposes_versatile_two_handed_damage():
    """#756: a Versatile weapon hides its two-handed die. The SRD stores it in
    WeaponPropertyAssignment (versatile-wp detail '1d8' for the Quarterstaff); the
    flattened record must surface it as `versatile` so the inspector can read
    '1d6 (1d8 two-handed)' — the optimizer's 'Examine is missing the 1d8 two-handed
    damage' finding."""
    rec = itemcatalog.resolve("Quarterstaff")
    assert rec is not None
    assert rec["damage"] == "1d6"
    assert rec["versatile"] == "1d8"


def test_resolve_ranged_weapon_exposes_range():
    """RRI-25e55fa optimizer #3: a ranged weapon hides its RANGE (Heavy Crossbow had no
    '100/320 ft'). The SRD Weapon.json carries `range` (normal) + `long_range`; the flatten
    must surface them as `range`/`range_long` so the inspector can read the real bracket.
    Re-derived against data/srd/srd524/Weapon.json: Heavy Crossbow is 100/400 ft in SRD 5.2."""
    hc = itemcatalog.resolve("Heavy Crossbow")
    assert hc is not None
    assert hc["range"] == 100
    assert hc["range_long"] == 400
    shortbow = itemcatalog.resolve("Shortbow")
    assert shortbow is not None and shortbow["range"] == 80 and shortbow["range_long"] == 320


def test_resolve_thrown_weapon_exposes_thrown_range():
    """A thrown melee weapon (Dagger/Handaxe) carries a thrown range (20/60 ft). The flatten
    must surface it so the inspector shows the throwing bracket — never fabricated."""
    dagger = itemcatalog.resolve("Dagger")
    assert dagger is not None
    assert dagger["range"] == 20
    assert dagger["range_long"] == 60


def test_resolve_pure_melee_weapon_has_no_range():
    """A pure melee weapon (Longsword/Greatsword) has range 0 in the SRD; the flatten must
    surface 0 (not a fabricated number) so the inspector hides the Range row entirely."""
    ls = itemcatalog.resolve("Longsword")
    assert ls is not None
    assert ls["range"] == 0
    assert ls["range_long"] == 0


def test_resolve_magic_weapon_inherits_range_via_fk():
    """A magic ranged weapon inherits its base weapon's range via the Weapon FK join, the
    same path damage/properties take (#756)."""
    rec = itemcatalog.resolve("Frost Brand (Glaive)")
    # Glaive is a melee weapon -> range 0 (no fabricated thrown range on a reach polearm).
    assert rec is not None
    assert rec["range"] == 0


def test_resolve_weapon_exposes_real_properties():
    """#756: weapon properties (Versatile, Finesse, Light, Two-Handed, …) live in
    WeaponPropertyAssignment, not inline — the flatten dropped them, so the inspector
    had 'no Versatile property'. They must now appear on the record."""
    qs = itemcatalog.resolve("Quarterstaff")
    assert "Versatile" in qs["properties"]
    # a finesse weapon carries Finesse; a longbow carries Two-Handed/Ammunition.
    dagger = itemcatalog.resolve("Dagger")
    assert dagger is not None and "Finesse" in dagger["properties"]
    longbow = itemcatalog.resolve("Longbow")
    assert longbow is not None and "Two-Handed" in longbow["properties"]


def test_bare_weapon_flatten_merges_simple_flag_with_srd_properties():
    # The pre-existing is_simple/is_improvised flags must not be dropped when the SRD
    # WeaponProperty chips are merged in (additive, de-duped). The bare Weapon.json
    # record carries is_simple; the FK join adds Versatile — both must survive on the
    # SAME flattened record. (NB: the public catalog resolves "Quarterstaff" to the
    # Item.json record, whose is_simple is null — so this asserts the weapon-shape
    # flatten directly, the path a non-Item-shadowed simple weapon takes.)
    rec = itemcatalog._flatten(
        "weapon",
        {"name": "Quarterstaff", "is_simple": True, "damage_dice": "1d6",
         "damage_type": "bludgeoning"},
        "srd-2024_quarterstaff",
    )
    assert "simple" in rec["properties"]
    assert "Versatile" in rec["properties"]
    assert rec["versatile"] == "1d8"


def test_non_versatile_weapon_has_no_versatile_key_value():
    # A weapon with no versatile assignment must not fabricate a two-handed die.
    rec = itemcatalog.resolve("Dagger")
    assert rec.get("versatile", "") == ""


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


def _cp_total(cur: dict) -> int:
    return cur["cp"] + cur["sp"] * 10 + cur["ep"] * 50 + cur["gp"] * 100 + cur["pp"] * 1000


# --- F09-1: fuzzy resolve through the tool path (the live-repro surface) -----


def test_lookup_item_tool_fuzzy_substring_resolves():
    # F09-1 live repro: the fuzzy lookup used to surface as a raw MCP tool error.
    rec = server.lookup_item("bag of hold")
    assert rec["name"] == "Bag of Holding" and "error" not in rec


# --- F09-3: priceless != free --------------------------------------------------


def test_unpriced_magic_item_cost_is_none():
    # The SRD dump stores 0 / "0.00" for every magic item — that means "no listed
    # price", not "free". Flatten emits None; a real price survives exactly.
    assert itemcatalog.resolve("Bag of Holding")["cost"] is None
    assert itemcatalog.resolve("Candle")["cost"] == 0.01  # sub-gp price preserved


def test_no_catalog_item_flattens_to_zero_cost():
    # Census guard (F09-3): nothing in the index is "free by accident" — every
    # record either carries a positive price or None. The SRD split is exactly
    # 760 unpriced (757 magic items + 3 unpriced mundane index entries) / 200 priced.
    records = list(itemcatalog._index().values())
    zero_or_negative = [r["name"] for r in records if r["cost"] is not None and r["cost"] <= 0]
    assert zero_or_negative == []
    assert sum(1 for r in records if r["cost"] is None) == 760


def test_lookup_item_reports_unpriced_cost_as_null():
    # lookup_item/find_items show cost: null honestly (LLM-read payload).
    assert server.lookup_item("Bag of Holding")["cost"] is None
    out = server.find_items("Bag of Holding", limit=1)
    assert out["items"][0]["cost"] is None


def test_buy_unpriced_item_requires_explicit_cost(cid):
    server.adjust_currency(cid, "grett", gp=50)
    before = server.get_character(cid, "grett")
    with pytest.raises(ValueError, match="cost_gp"):
        server.buy_item(cid, "grett", item_name="Bag of Holding")
    # the error names the gap (the item with no listed price)
    with pytest.raises(ValueError, match="no listed price"):
        server.buy_item(cid, "grett", item_name="Bag of Holding")
    # nothing persisted: purse unchanged, bag NOT granted
    after = server.get_character(cid, "grett")
    assert _cp_total(after["currency"]) == _cp_total(before["currency"])
    assert all(i["name"] != "Bag of Holding" for i in after["inventory"])


def test_buy_unpriced_item_with_explicit_cost_works(cid):
    server.adjust_currency(cid, "grett", gp=500)
    out = server.buy_item(cid, "grett", item_name="Bag of Holding", cost_gp=400)
    assert out["currency"]["gp"] == 100
    assert _item(out["inventory"], "Bag of Holding")


def test_buy_item_explicit_zero_cost_is_a_deliberate_free_grant(cid):
    # cost_gp=0 stays expressible as the DM's explicit choice (a gift/reward).
    before = server.get_character(cid, "grett")
    out = server.buy_item(cid, "grett", item_name="Bag of Holding", cost_gp=0)
    assert _cp_total(out["currency"]) == _cp_total(before["currency"])
    assert _item(out["inventory"], "Bag of Holding")


# --- F09-2: unit price x quantity ---------------------------------------------


def test_buy_item_charges_unit_price_times_quantity(cid):
    server.adjust_currency(cid, "grett", gp=100)
    out = server.buy_item(cid, "grett", item_name="Potion of Healing", quantity=2)
    assert out["currency"]["gp"] == 0  # 100 - 50 x 2
    assert out["unit_cost_gp"] == 50.0
    assert out["total_cost_gp"] == 100.0
    assert _item(out["inventory"], "Potion of Healing")["quantity"] == 2


def test_buy_item_insufficient_for_total_raises_and_persists_nothing(cid):
    server.adjust_currency(cid, "grett", gp=100)  # enough for 1 unit, not 5
    before = server.get_character(cid, "grett")
    with pytest.raises(ValueError, match="insufficient funds"):
        server.buy_item(cid, "grett", item_name="Potion of Healing", quantity=5)
    after = server.get_character(cid, "grett")
    assert _cp_total(after["currency"]) == _cp_total(before["currency"])
    assert after["inventory"] == before["inventory"]


def test_buy_item_sub_cp_exact_for_fractional_prices(cid):
    server.adjust_currency(cid, "grett", gp=1)
    before = server.get_character(cid, "grett")
    out = server.buy_item(cid, "grett", item_name="Candle", quantity=7)  # 0.01 gp each
    assert _cp_total(before["currency"]) - _cp_total(out["currency"]) == 7  # exactly 7 cp
    assert out["unit_cost_gp"] == 0.01
    assert out["total_cost_gp"] == 0.07
    assert _item(out["inventory"], "Candle")["quantity"] == 7


def test_buy_item_rejects_non_positive_quantity(cid):
    server.adjust_currency(cid, "grett", gp=10)
    before = server.get_character(cid, "grett")
    with pytest.raises(ValueError, match="quantity"):
        server.buy_item(cid, "grett", "Torch", cost_gp=1, quantity=0)
    after = server.get_character(cid, "grett")
    assert _cp_total(after["currency"]) == _cp_total(before["currency"])


def test_sell_item_credits_unit_price_times_quantity(cid):
    server.add_item(cid, "grett", name="Pelt", quantity=3)
    before = server.get_character(cid, "grett")
    out = server.sell_item(cid, "grett", "Pelt", price_gp=0.5, quantity=3)
    assert _cp_total(out["currency"]) - _cp_total(before["currency"]) == 150  # 0.5 gp x 3
    assert out["unit_price_gp"] == 0.5
    assert out["total_price_gp"] == 1.5
    assert all(i["name"] != "Pelt" for i in out["inventory"])


# --- F09-5 stage 1: equip TELLs the mechanical consequences --------------------


def test_equip_catalog_armor_tells_suggested_ac(cid):
    server.add_item(cid, "grett", item_name="Plate Armor")
    out = server.equip_item(cid, "grett", "Plate Armor")
    assert out["equipped"] is True
    mech = out["mechanics"]
    assert mech["applied"] is False  # stage 1 is TELL-only
    assert mech["suggested_ac"] == 18
    assert mech["ac_delta"] == 18 - mech["current_ac"]
    assert "update_character(armor_class=18)" in mech["note"]
    # the engine did NOT silently change AC (stage 2 / #806 owns enforcement)
    assert server.get_character(cid, "grett")["armor_class"] == mech["current_ac"]


def test_equip_shield_tells_plus_two_delta(cid):
    server.add_item(cid, "grett", item_name="Shield")
    out = server.equip_item(cid, "grett", "Shield")
    mech = out["mechanics"]
    assert mech["ac_delta"] == 2  # a shield is +2 ON TOP of current AC, not "AC 2"
    assert mech["suggested_ac"] == mech["current_ac"] + 2
    unequip = server.equip_item(cid, "grett", "Shield", equipped=False)
    assert unequip["mechanics"]["ac_delta"] == -2


def test_unequip_body_armor_suggests_unarmored_baseline(cid):
    server.add_item(cid, "grett", item_name="Plate Armor")
    server.equip_item(cid, "grett", "Plate Armor")
    out = server.equip_item(cid, "grett", "Plate Armor", equipped=False)
    sheet = server.get_character(cid, "grett")
    dex_mod = (sheet["abilities"]["dexterity"] - 10) // 2
    assert out["mechanics"]["suggested_ac"] == 10 + dex_mod


def test_equip_catalog_weapon_tells_attack_implications(cid):
    server.add_item(cid, "grett", item_name="Longsword")
    out = server.equip_item(cid, "grett", "Longsword")
    mech = out["mechanics"]
    assert mech["applied"] is False
    assert mech["damage"] == "1d8" and mech["damage_type"] == "slashing"
    assert "attack" in mech["note"]


def test_equip_freetext_item_payload_unchanged(cid):
    # additive regression: non-catalog items return exactly today's payload.
    server.add_item(cid, "grett", name="lucky pebble")
    out = server.equip_item(cid, "grett", "lucky pebble")
    assert out["equipped"] is True
    assert "mechanics" not in out


# --- F09-6: armor DEX-mod rules recovered from the SRD flatten -----------------


def test_catalog_armor_carries_dex_mod_rule():
    # Light = full DEX (no cap); medium = capped at +2; heavy = no DEX; shield = +2 bonus.
    light = itemcatalog.resolve("Leather Armor")
    assert light["armor_category"] == "light"
    assert light["ac_dex_mod"] == "full" and light["ac_dex_cap"] is None
    assert light["ac"] == 11

    medium = itemcatalog.resolve("Breastplate")
    assert medium["armor_category"] == "medium"
    assert medium["ac_dex_mod"] == "capped" and medium["ac_dex_cap"] == 2
    assert medium["ac"] == 14  # base AC, NOT the dropped flat value

    heavy = itemcatalog.resolve("Plate Armor")
    assert heavy["armor_category"] == "heavy"
    assert heavy["ac_dex_mod"] == "none" and heavy["ac_dex_cap"] is None
    assert heavy["ac"] == 18


def test_shield_is_a_plus_two_bonus_not_ac_two():
    # The SRD smuggles a shield's +2 BONUS in as ac_base=2 — F09-6 surfaces it as a bonus.
    shield = itemcatalog.resolve("Shield")
    assert shield["armor_category"] == "shield"
    assert shield["ac_bonus"] == 2
    assert shield["ac_dex_mod"] == "none"


def test_catalog_describe_renders_dex_rule_not_flat_ac():
    # The describe string the DM reads must show the DEX rule, not a flat "AC N".
    assert "AC +2 (shield)" in server._catalog_describe(itemcatalog.resolve("Shield"))
    assert "AC 14 + DEX (max +2)" in server._catalog_describe(itemcatalog.resolve("Breastplate"))
    assert "AC 11 + DEX" in server._catalog_describe(itemcatalog.resolve("Leather Armor"))
    assert "AC 18 (no DEX)" in server._catalog_describe(itemcatalog.resolve("Plate Armor"))
    # never the old misleading bare "AC 2" for a shield
    assert "AC 2]" not in server._catalog_describe(itemcatalog.resolve("Shield"))


def test_magic_armor_inherits_dex_rule_via_fk():
    # A MagicItem/Item armor recovers its DEX rule from the FK-joined base Armor record.
    # No SRD magic item in this dump carries an `armor` FK, so prove the join path with a
    # synthetic record pointing at the real breastplate armor pk (medium = +2 cap).
    weapons, armors = itemcatalog._weapon_armor_join()
    bp_pk = next(pk for pk, f in armors.items() if f.get("name") == "Breastplate")
    rec = itemcatalog._flatten("magicitem", {
        "name": "Enchanted Breastplate", "category": "armor", "armor": bp_pk,
        "rarity": "rare", "weight": "20.00",
    })
    assert rec["kind"] == "armor"
    assert rec["ac"] == 14
    assert rec["armor_category"] == "medium"
    assert rec["ac_dex_mod"] == "capped" and rec["ac_dex_cap"] == 2


def test_equip_medium_armor_applies_capped_dex(cid):
    # F09-6 effective-AC path: medium armor adds DEX up to +2, no further.
    server.update_character(cid, "grett", patch={"abilities": {"dexterity": 18}})  # +4 DEX
    server.add_item(cid, "grett", item_name="Breastplate")
    out = server.equip_item(cid, "grett", "Breastplate")
    # base 14 + min(+4, +2 cap) = 16  (NOT 14+4=18, and NOT a flat 14)
    assert out["mechanics"]["suggested_ac"] == 16


def test_equip_light_armor_applies_full_dex(cid):
    server.update_character(cid, "grett", patch={"abilities": {"dexterity": 16}})  # +3 DEX
    server.add_item(cid, "grett", item_name="Leather Armor")
    out = server.equip_item(cid, "grett", "Leather Armor")
    assert out["mechanics"]["suggested_ac"] == 11 + 3  # full DEX, uncapped


def test_equip_heavy_armor_ignores_dex(cid):
    server.update_character(cid, "grett", patch={"abilities": {"dexterity": 18}})  # +4 DEX
    server.add_item(cid, "grett", item_name="Plate Armor")
    out = server.equip_item(cid, "grett", "Plate Armor")
    assert out["mechanics"]["suggested_ac"] == 18  # flat, DEX never applies


# --- F09-7: granted Item persists the catalog's structured stats (#756 root) ---


def test_granted_weapon_persists_structured_stats(cid):
    out = server.add_item(cid, "grett", item_name="Longsword")
    it = _item(out["inventory"], "Longsword")
    assert it["kind"] == "weapon"
    assert it["damage"] == "1d8" and it["damage_type"] == "slashing"
    assert it["cost_gp"] == 15.0
    # the inspector (#756) now has structure, not just prose
    assert it["ac"] is None and it["armor_category"] == ""


def test_granted_armor_persists_ac_and_dex_rule(cid):
    out = server.add_item(cid, "grett", item_name="Breastplate")
    it = _item(out["inventory"], "Breastplate")
    assert it["kind"] == "armor"
    assert it["ac"] == 14
    assert it["armor_category"] == "medium"
    assert it["ac_dex_mod"] == "capped" and it["ac_dex_cap"] == 2


def test_granted_shield_persists_bonus_in_ac(cid):
    out = server.add_item(cid, "grett", item_name="Shield")
    it = _item(out["inventory"], "Shield")
    assert it["armor_category"] == "shield"
    assert it["ac"] == 2  # the +2 bonus carried as ac (described as a bonus, not base AC)


def test_freetext_item_has_empty_stat_defaults(cid):
    # additive: a free-text grant carries NO structured stats (all empty defaults).
    out = server.add_item(cid, "grett", name="a weird trinket")
    it = _item(out["inventory"], "a weird trinket")
    assert it["kind"] == "" and it["rarity"] == "" and it["properties"] == []
    assert it["ac"] is None and it["cost_gp"] is None and it["damage"] == ""


def test_old_snapshot_item_round_trips_without_new_fields():
    # F09-7 invariant: an Item dict from BEFORE these fields existed loads + dumps clean
    # (defaults fill), so old saves round-trip under the strict model.
    from models import Item

    old = Item.model_validate({"name": "Old Relic", "quantity": 1, "weight": 2.0,
                               "description": "x", "equipped": False,
                               "requires_attunement": False, "attuned": False})
    assert old.kind == "" and old.cost_gp is None and old.ac is None
    assert old.properties == []
    redumped = Item.model_validate(old.model_dump())  # round-trip is stable
    assert redumped.model_dump() == old.model_dump()


def test_granted_stats_do_not_alias_the_catalog_cache(cid):
    # The catalog rec + its properties list are live lru-cache references; the persisted
    # Item must COPY them (mutating the owned item must not bleed into the catalog).
    rec = itemcatalog.resolve("Studded Leather Armor")
    before = list(rec.get("properties") or [])
    out = server.add_item(cid, "grett", item_name="Studded Leather Armor")
    # mutate the catalog rec's properties; the persisted item must be unaffected (it copied)
    rec.get("properties", []).append("__poison__")
    fresh = _item(server.get_character(cid, "grett")["inventory"], "Studded Leather Armor")
    assert "__poison__" not in fresh["properties"]
    rec["properties"][:] = before  # restore the shared cache list


def test_identical_grants_still_stack_with_stats(cid):
    # Two identical catalog grants produce identical stats -> they still merge to one stack.
    server.add_item(cid, "grett", item_name="Longsword")
    out = server.add_item(cid, "grett", item_name="Longsword")
    swords = [i for i in out["inventory"] if i["name"] == "Longsword"]
    assert len(swords) == 1 and swords[0]["quantity"] == 2


def test_freetext_and_catalog_grants_do_not_merge(cid):
    # A free-text "Longsword" (no stats) must NOT merge with a catalog Longsword (statted).
    server.add_item(cid, "grett", name="Longsword")  # bare, no item_name
    out = server.add_item(cid, "grett", item_name="Longsword")  # statted
    swords = [i for i in out["inventory"] if i["name"] == "Longsword"]
    assert len(swords) == 2  # different stats -> two distinct records


def test_split_stack_carries_structured_stats(cid):
    # F09-7 watch-item: equipping splits a unit off a stack — the split unit must keep the
    # structured stats (the old shallow clone dropped any new Item field).
    server.add_item(cid, "grett", item_name="Longsword")
    server.add_item(cid, "grett", item_name="Longsword")  # stack of 2
    server.equip_item(cid, "grett", "Longsword")
    inv = server.get_character(cid, "grett")["inventory"]
    equipped = next(i for i in inv if i["name"] == "Longsword" and i["equipped"])
    assert equipped["damage"] == "1d8" and equipped["kind"] == "weapon"
    assert equipped["cost_gp"] == 15.0
