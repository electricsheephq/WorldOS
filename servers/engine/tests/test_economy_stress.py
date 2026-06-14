"""F09-13 — table-driven economy stress suite.

The original economy tests (test_inventory.py / test_itemcatalog.py) are single-path:
one buy, one sell, one armor. This suite is the structural stress matrix the audit asked
for — a value-conservation PROPERTY over mixed buy/sell/spend/earn cycles, a full
armor × DEX effective-AC matrix (F09-6), and xfail rows tagged to the still-open
denomination-preservation finding (F09-11, P3 polish — deferred from this cluster) so the
known gap is RED-documented, not silently green.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (PR #768), unit 09 — F09-6/7/9/10/13.
Engine = sole writer; every tool call here goes through the real campaign_lock + save
path so the matrix also exercises persistence atomicity. Single-process per repo policy.
"""

import pytest

import inventory
import itemcatalog
import server
from models import Character, Currency, Item


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hero(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Stress")["id"]
    h = server.create_character(cid, "Croesus", kind="player")["id"]
    return cid, h


def _value_cp(cur: dict) -> int:
    return inventory.total_copper(Currency(**cur))


# ===========================================================================
# F09-6 — armor effective-AC matrix (light/medium/heavy + shield) × DEX
# ===========================================================================

# (armor name, expected category, base ac). Drawn from the SRD Armor table.
_ARMOR_MATRIX = [
    ("Padded Armor", "light", 11),
    ("Leather Armor", "light", 11),
    ("Studded Leather Armor", "light", 12),
    ("Hide Armor", "medium", 12),
    ("Chain Shirt", "medium", 13),
    ("Scale Mail", "medium", 14),
    ("Breastplate", "medium", 14),
    ("Half Plate Armor", "medium", 15),
    ("Ring Mail", "heavy", 14),
    ("Chain Mail", "heavy", 16),
    ("Splint Armor", "heavy", 17),
    ("Plate Armor", "heavy", 18),
]


@pytest.mark.parametrize("name,category,base_ac", _ARMOR_MATRIX)
def test_armor_catalog_category_and_base(name, category, base_ac):
    rec = itemcatalog.resolve(name)
    assert rec["armor_category"] == category
    assert rec["ac"] == base_ac


def _expected_worn_ac(category: str, base_ac: int, cap, dex_mod: int) -> int:
    if category == "light":
        return base_ac + dex_mod
    if category == "medium":
        return base_ac + min(dex_mod, cap or 2)
    return base_ac  # heavy: no DEX


@pytest.mark.parametrize("name,category,base_ac", _ARMOR_MATRIX)
@pytest.mark.parametrize("dex_score,dex_mod", [(8, -1), (10, 0), (14, 2), (16, 3), (20, 5)])
def test_equip_armor_effective_ac_matrix(hero, name, category, base_ac, dex_score, dex_mod):
    # F09-6: the equip mechanics must compute the worn AC by the armor's DEX-mod rule —
    # light = full DEX, medium = DEX capped at +2, heavy = flat (no DEX).
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": dex_score}})
    server.add_item(cid, h, item_name=name)
    out = server.equip_item(cid, h, name)
    rec = itemcatalog.resolve(name)
    expected = _expected_worn_ac(category, base_ac, rec.get("ac_dex_cap"), dex_mod)
    assert out["mechanics"]["suggested_ac"] == expected, (name, dex_score)


@pytest.mark.parametrize("dex_score,dex_mod", [(10, 0), (16, 3), (20, 5)])
def test_equip_shield_is_flat_plus_two_regardless_of_dex(hero, dex_score, dex_mod):
    # A shield is a +2 BONUS on top of the current AC; DEX never changes that delta.
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": dex_score}})
    server.add_item(cid, h, item_name="Shield")
    out = server.equip_item(cid, h, "Shield")
    assert out["mechanics"]["ac_delta"] == 2
    off = server.equip_item(cid, h, "Shield", equipped=False)
    assert off["mechanics"]["ac_delta"] == -2


# ===========================================================================
# F09-7 — granted items persist structured stats (round-trip + stacking)
# ===========================================================================

_GRANT_MATRIX = [
    # (item_name, kind, has_damage, has_ac)
    ("Longsword", "weapon", True, False),
    ("Dagger", "weapon", True, False),
    ("Plate Armor", "armor", False, True),
    ("Shield", "armor", False, True),
    ("Potion of Healing", "potion", False, False),
    ("Bag of Holding", "wondrous", False, False),
]


@pytest.mark.parametrize("name,kind,has_damage,has_ac", _GRANT_MATRIX)
def test_grant_persists_kind_and_stats(hero, name, kind, has_damage, has_ac):
    cid, h = hero
    out = server.add_item(cid, h, item_name=name)
    it = next(i for i in out["inventory"] if i["name"] == name)
    assert it["kind"] == kind
    assert bool(it["damage"]) == has_damage
    assert (it["ac"] is not None) == has_ac


@pytest.mark.parametrize("name,kind,has_damage,has_ac", _GRANT_MATRIX)
def test_grant_round_trips_through_full_save_load(hero, name, kind, has_damage, has_ac):
    # The persisted stats survive a save + fresh load (real snapshot round-trip).
    cid, h = hero
    server.add_item(cid, h, item_name=name)
    reloaded = next(i for i in server.get_character(cid, h)["inventory"] if i["name"] == name)
    assert reloaded["kind"] == kind
    # the model accepts its own dump back (strict round-trip)
    assert Item.model_validate(reloaded).model_dump() == reloaded


def test_split_then_reload_preserves_stats(hero):
    # Equip-split off a stack, then reload: the split unit keeps its structured stats.
    cid, h = hero
    server.add_item(cid, h, item_name="Dagger")
    server.add_item(cid, h, item_name="Dagger")
    server.equip_item(cid, h, "Dagger")
    inv = server.get_character(cid, h)["inventory"]
    for d in (i for i in inv if i["name"] == "Dagger"):
        assert d["kind"] == "weapon" and d["damage"]  # both the stack and the split unit


# ===========================================================================
# F09-9 / F09-10 — buy/sell/spend/earn value-conservation PROPERTY
# ===========================================================================

# (starting gp, op sequence). Each op is (kind, arg). Value conservation: the purse value
# plus money spent on items equals starting value plus money earned from sales.
_CYCLES = [
    [("earn", 10), ("spend", 4), ("earn", 1), ("spend", 0.5)],
    [("earn", 100), ("spend", 99.99)],
    [("earn", 7), ("spend", 0.07), ("spend", 0.93)],
    [("earn", 50), ("earn", 50), ("spend", 33), ("spend", 17)],
]


@pytest.mark.parametrize("ops", _CYCLES)
def test_spend_earn_value_conservation(hero, ops):
    cid, h = hero
    net = 0  # net cp the purse should hold (earns add, spends subtract)
    for kind, amt in ops:
        if kind == "earn":
            out = server.adjust_currency(cid, h, earn_gp=amt)
            net += inventory.gp_to_cp(amt)
        else:
            out = server.adjust_currency(cid, h, spend_gp=amt)
            net -= inventory.gp_to_cp(amt)
        assert _value_cp(out) == net  # value-exact at every step


def test_buy_then_sell_purse_arithmetic(hero):
    # Buy 3 potions (50 each from the catalog) then sell them at 25 each — exact purse math.
    cid, h = hero
    server.adjust_currency(cid, h, gp=200)
    server.buy_item(cid, h, item_name="Potion of Healing", quantity=3)  # -150
    mid = server.get_character(cid, h)
    assert _value_cp(mid["currency"]) == 5000  # 200 - 150 = 50 gp
    out = server.sell_item(cid, h, "Potion of Healing", price_gp=25, quantity=3)  # +75
    assert _value_cp(out["currency"]) == 12500  # 50 + 75 = 125 gp


def test_buy_insufficient_is_atomic_across_the_matrix(hero):
    cid, h = hero
    server.adjust_currency(cid, h, gp=10)
    before = server.get_character(cid, h)
    with pytest.raises(ValueError):
        server.buy_item(cid, h, item_name="Plate Armor", cost_gp=1500)
    after = server.get_character(cid, h)
    # nothing changed: no coins spent, no armor granted
    assert after["currency"] == before["currency"]
    assert after["inventory"] == before["inventory"]


@pytest.mark.parametrize("frac_gp,units", [(0.01, 7), (0.5, 3), (0.1, 13), (0.99, 11)])
def test_fractional_buy_is_copper_exact(hero, frac_gp, units):
    # F09-2 lineage, stress-fuzzed: unit price × quantity stays copper-exact (no float drift).
    cid, h = hero
    server.adjust_currency(cid, h, gp=100)
    before = _value_cp(server.get_character(cid, h)["currency"])
    out = server.buy_item(cid, h, "Bead", cost_gp=frac_gp, quantity=units)
    spent = before - _value_cp(out["currency"])
    assert spent == inventory.gp_to_cp(frac_gp) * units


# ===========================================================================
# Pure-helper conservation (no I/O) — pay / gain are always value-preserving
# ===========================================================================


@pytest.mark.parametrize("start", [
    {"cp": 999}, {"sp": 50}, {"gp": 4, "sp": 12}, {"gp": 100}, {"cp": 7, "sp": 3, "gp": 1},
])
@pytest.mark.parametrize("spend", [0, 0.01, 1, 3.5, 0.99])
def test_pay_conserves_value(start, spend):
    ch = Character(name="T", currency=start)
    have = inventory.total_copper(ch.currency)
    cost = inventory.gp_to_cp(spend)
    if cost > have:
        with pytest.raises(ValueError):
            inventory.pay(ch, spend)
        assert inventory.total_copper(ch.currency) == have  # unchanged on failure
    else:
        inventory.pay(ch, spend)
        assert inventory.total_copper(ch.currency) == have - cost


@pytest.mark.parametrize("start", [{}, {"gp": 5}, {"sp": 17}, {"cp": 3}])
@pytest.mark.parametrize("earn", [0, 0.05, 2, 13.37])
def test_gain_conserves_value(start, earn):
    ch = Character(name="T", currency=start)
    have = inventory.total_copper(ch.currency)
    inventory.gain(ch, earn)
    assert inventory.total_copper(ch.currency) == have + inventory.gp_to_cp(earn)


# ===========================================================================
# DEFERRED-FINDING RED ROWS — documented gaps, not silent passes
# ===========================================================================
#
# F09-11 (P3 polish, NOT in this P2 cluster #807): pay() AND gain() rebuild the whole
# purse via _from_copper (gp/sp/cp only), so a noble's pp/ep is vaporized into gp even
# though VALUE is conserved. These xfail rows pin the open behavior so the suite goes RED
# the moment F09-11's denomination-preserving fix lands (flip xfail -> xpass).


@pytest.mark.xfail(reason="F09-11 (deferred P3): pay() canonicalizes the whole purse, dropping pp")
def test_pay_preserves_platinum_F09_11():
    ch = Character(name="T", currency={"pp": 10})  # 100 gp of value, all platinum
    inventory.pay(ch, 0.01)  # spend 1 cp
    # EXPECTED once F09-11 lands: only the minimal coin is broken, pp largely intact.
    assert ch.currency.pp >= 9


@pytest.mark.xfail(reason="F09-11 (deferred P3): gain() rebuilds the whole purse, dropping pp/ep")
def test_gain_preserves_platinum_F09_11():
    ch = Character(name="T", currency={"pp": 10})
    inventory.gain(ch, 1)  # earn 1 gp
    # EXPECTED once F09-11 lands: the earned gp is added; existing pp is untouched.
    assert ch.currency.pp == 10


def test_pay_and_gain_conserve_value_even_while_canonicalizing():
    # The companion GREEN assertion: value is ALWAYS exact today, even though F09-11's
    # denomination preservation is still open — so the value-conservation property holds
    # for the whole matrix regardless of the deferred coin-shape gap.
    ch = Character(name="T", currency={"pp": 10})
    inventory.pay(ch, 0.01)
    assert inventory.total_copper(ch.currency) == 10000 - 1
    ch2 = Character(name="T", currency={"pp": 10})
    inventory.gain(ch2, 1)
    assert inventory.total_copper(ch2.currency) == 10000 + 100
