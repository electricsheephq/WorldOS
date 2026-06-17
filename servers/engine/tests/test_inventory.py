import pytest

import inventory
import server
from models import Character, Currency


def mk(**kw) -> Character:
    return Character(name="T", **kw)


# --- currency ---
def test_total_copper_and_make_change():
    cur = Currency(pp=1, gp=2, ep=1, sp=3, cp=4)
    assert inventory.total_copper(cur) == 1000 + 200 + 50 + 30 + 4  # 1284
    back = inventory._from_copper(1284)
    assert (back.gp, back.sp, back.cp) == (12, 8, 4)  # canonical gp/sp/cp
    assert inventory.total_copper(back) == 1284  # value preserved


def test_pay_makes_change():
    ch = mk(currency={"sp": 50})  # 50 sp == 5 gp
    inventory.pay(ch, 3)  # pay 3 gp -> 2 gp worth remaining
    assert inventory.total_copper(ch.currency) == 200


def test_pay_insufficient_raises():
    ch = mk(currency={"gp": 2})
    with pytest.raises(ValueError):
        inventory.pay(ch, 5)


def test_gain():
    ch = mk()
    inventory.gain(ch, 15)
    assert inventory.total_copper(ch.currency) == 1500


# --- F09-11: pp/ep preservation on pay AND gain ---------------------------------
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F09-11). _from_copper rebuilt the
# WHOLE purse into gp/sp/cp, so pay()/gain()/sell_item silently vaporized a noble's
# platinum and electrum. Both now preserve untouched denominations; value is exact.

def test_pay_preserves_platinum_when_paying_a_copper():
    ch = mk(currency={"pp": 10})  # 10,000 cp of platinum
    before = inventory.total_copper(ch.currency)
    inventory.pay(ch, 0.01)  # pay 1 cp -> break ONE platinum, keep the rest as pp
    assert ch.currency.pp == 9          # nine platinum survive (was 0 on main)
    assert inventory.total_copper(ch.currency) == before - 1  # value exact


def test_gain_preserves_platinum():
    ch = mk(currency={"pp": 10})
    before = inventory.total_copper(ch.currency)
    inventory.gain(ch, 1)  # earn 1 gp -> pp untouched, +1 gp coin
    assert ch.currency.pp == 10         # platinum survives (was 0 on main)
    assert ch.currency.gp == 1
    assert inventory.total_copper(ch.currency) == before + 100


def test_pay_spends_smallest_coins_first_no_unneeded_break():
    # 1 pp + 5 gp; pay 3 gp -> spend gp coins, never touch the platinum.
    ch = mk(currency={"pp": 1, "gp": 5})
    inventory.pay(ch, 3)
    assert ch.currency.pp == 1 and ch.currency.gp == 2


def test_gp_sp_cp_only_purse_pay_is_value_identical_to_before():
    # No pp/ep present -> behavior is byte-identical to the old _from_copper path.
    ch = mk(currency={"sp": 50})  # 50 sp == 5 gp
    inventory.pay(ch, 3)
    assert inventory.total_copper(ch.currency) == 200


def test_pay_breaks_a_silver_for_a_copper_remainder():
    # Only silver on hand; owe 3 cp -> break one sp, get 7 cp change.
    ch = mk(currency={"sp": 5})
    inventory.pay(ch, 0.03)
    assert inventory.total_copper(ch.currency) == 47  # 50 - 3
    assert ch.currency.cp == 7 and ch.currency.sp == 4


def test_gain_cp_and_pay_cp_preserve_platinum():
    ch = mk(currency={"pp": 2})
    inventory.gain_cp(ch, 5)
    assert ch.currency.pp == 2 and ch.currency.cp == 5
    inventory.pay_cp(ch, 5)
    assert ch.currency.pp == 2 and ch.currency.cp == 0


def test_adjust_currency_negative_raises():
    ch = mk(currency={"gp": 1})
    with pytest.raises(ValueError):
        inventory.adjust_currency(ch, gp=-2)


# --- encumbrance ---
def test_encumbrance_thresholds():
    ch = mk(abilities={"strength": 10})  # x5=50, x10=100, x15=150
    inventory.add_item(ch, "Rock", quantity=1, weight=40)
    assert inventory.encumbrance(ch)["status"] == "unencumbered"
    inventory.add_item(ch, "Boulder", quantity=1, weight=20)  # 60
    assert inventory.encumbrance(ch)["status"] == "encumbered"
    inventory.add_item(ch, "Anvil", quantity=1, weight=50)  # 110
    assert inventory.encumbrance(ch)["status"] == "heavily_encumbered"
    inventory.add_item(ch, "Cart", quantity=1, weight=50)  # 160
    assert inventory.encumbrance(ch)["status"] == "overloaded"


# --- items ---
def test_add_stacks_identical():
    ch = mk()
    inventory.add_item(ch, "Torch", quantity=2, weight=1)
    inventory.add_item(ch, "Torch", quantity=3, weight=1)
    assert len(ch.inventory) == 1 and ch.inventory[0].quantity == 5


def test_remove_item_partial_and_full():
    ch = mk()
    inventory.add_item(ch, "Arrow", quantity=20, weight=0.05)
    inventory.remove_item(ch, "Arrow", 5)
    assert ch.inventory[0].quantity == 15
    inventory.remove_item(ch, "Arrow", 15)  # exactly held -> removes the stack
    assert len(ch.inventory) == 0


def test_attunement_limit():
    ch = mk()
    for n in ["A", "B", "C", "D"]:
        inventory.add_item(ch, n, requires_attunement=True)
    inventory.set_attuned(ch, "A", True)
    inventory.set_attuned(ch, "B", True)
    inventory.set_attuned(ch, "C", True)
    with pytest.raises(ValueError):
        inventory.set_attuned(ch, "D", True)  # 4th over the limit


def test_attune_requires_flag():
    ch = mk()
    inventory.add_item(ch, "Mundane Sword", requires_attunement=False)
    with pytest.raises(ValueError):
        inventory.set_attuned(ch, "Mundane Sword", True)


# --- tools ---
def test_buy_and_sell_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shop")["id"]
    h = server.create_character(cid, "Buyer", kind="player")["id"]
    server.adjust_currency(cid, h, gp=50)
    server.buy_item(cid, h, "Longsword", cost_gp=15, weight=3)
    sheet = server.get_character(cid, h)
    assert any(i["name"] == "Longsword" for i in sheet["inventory"])
    assert sheet["currency"]["gp"] == 35  # 50 - 15
    server.sell_item(cid, h, "Longsword", price_gp=7)
    assert not any(i["name"] == "Longsword" for i in server.get_character(cid, h)["inventory"])


def test_buy_insufficient_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shop")["id"]
    h = server.create_character(cid, "Broke", kind="player")["id"]
    with pytest.raises(Exception):
        server.buy_item(cid, h, "Plate Armor", cost_gp=1500)


# --- hardening regressions (from adversarial review) ---
def test_stack_only_identical_items():  # C1
    ch = mk()
    inventory.add_item(ch, "Sword", quantity=1, weight=3, requires_attunement=True)
    inventory.add_item(ch, "Sword", quantity=1, weight=3, requires_attunement=False)
    assert len(ch.inventory) == 2  # different attunement -> not merged


def test_equip_splits_a_stack():  # C2
    ch = mk()
    inventory.add_item(ch, "Torch", quantity=5, weight=1)
    inventory.set_equipped(ch, "Torch", True)
    equipped = [i for i in ch.inventory if i.equipped]
    unequipped = [i for i in ch.inventory if not i.equipped]
    assert len(equipped) == 1 and equipped[0].quantity == 1
    assert unequipped[0].quantity == 4


def test_attune_splits_and_counts_one():  # C2
    ch = mk()
    inventory.add_item(ch, "Ring of X", quantity=2, requires_attunement=True)
    inventory.set_attuned(ch, "Ring of X", True)
    assert sum(1 for i in ch.inventory if i.attuned) == 1  # one slot, not two


def test_gain_negative_raises():  # H1
    with pytest.raises(ValueError):
        inventory.gain(mk(), -5)


def test_remove_prefers_unequipped():  # H2
    ch = mk()
    inventory.add_item(ch, "Dagger", quantity=1, weight=1)
    inventory.set_equipped(ch, "Dagger", True)  # one equipped Dagger
    inventory.add_item(ch, "Dagger", quantity=1, weight=1)  # a spare, unequipped
    inventory.remove_item(ch, "Dagger", 1)
    remaining = [i for i in ch.inventory if i.name == "Dagger"]
    assert len(remaining) == 1 and remaining[0].equipped is True  # the spare was removed


def test_remove_more_than_held_raises():  # H3
    ch = mk()
    inventory.add_item(ch, "Gem", quantity=1)
    with pytest.raises(ValueError):
        inventory.remove_item(ch, "Gem", 50)


def test_item_negative_quantity_rejected():  # H4
    from models import Item

    with pytest.raises(Exception):
        Item(name="Bad", quantity=-1)
    with pytest.raises(ValueError):
        inventory.add_item(mk(), "Bad", quantity=0)


def test_sell_negative_price_raises(tmp_path, monkeypatch):  # H1 via tool
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shop")["id"]
    h = server.create_character(cid, "Seller", kind="player")["id"]
    server.add_item(cid, h, "Trinket")
    with pytest.raises(Exception):
        server.sell_item(cid, h, "Trinket", price_gp=-10)


# --- F09-9: sell price sanity (TELL by default, optional hard cap) -------------


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shop")["id"]
    h = server.create_character(cid, "Seller", kind="player")["id"]
    return cid, h


def test_sell_surfaces_catalog_cost(shop):
    cid, h = shop
    server.add_item(cid, h, item_name="Longsword")  # list 15
    out = server.sell_item(cid, h, "Longsword", price_gp=10)
    assert out["catalog_cost_gp"] == 15.0
    assert "warning" not in out  # at/under list -> no flag


def test_sell_above_list_warns_but_does_not_block(shop):
    cid, h = shop
    server.add_item(cid, h, item_name="Longsword")  # list 15, cap 2x = 30
    out = server.sell_item(cid, h, "Longsword", price_gp=100)
    assert "warning" in out and "list" in out["warning"].lower()
    # TELL only by default: the sale still goes through and the purse is credited
    assert out["currency"]["gp"] == 100
    assert all(i["name"] != "Longsword" for i in out["inventory"])


def test_sell_freetext_item_has_null_reference(shop):
    cid, h = shop
    server.add_item(cid, h, name="Hand-carved Idol")  # no catalog match
    out = server.sell_item(cid, h, "Hand-carved Idol", price_gp=500)
    assert out["catalog_cost_gp"] is None  # no list price to compare
    assert "warning" not in out  # nothing to warn against


def test_sell_reference_reads_persisted_cost_gp(shop):
    # The reference price comes off the OWNED item's persisted cost_gp (F09-7), which a
    # catalog grant set to the SRD list — proven by mutating the saved record's cost_gp and
    # seeing the reference follow it (rather than a fresh by-name catalog re-resolve).
    cid, h = shop
    server.add_item(cid, h, item_name="Longsword")  # persists cost_gp=15
    ch = server.get_character(cid, h)
    assert next(i for i in ch["inventory"] if i["name"] == "Longsword")["cost_gp"] == 15.0
    out = server.sell_item(cid, h, "Longsword", price_gp=12)
    assert out["catalog_cost_gp"] == 15.0  # the persisted/list reference
    assert "warning" not in out


def test_enforce_sell_cap_blocks_overprice(shop):
    cid, h = shop
    server.set_house_rules(cid, {"enforce_sell_cap": True})
    server.add_item(cid, h, item_name="Longsword")  # list 15, cap 2x = 30
    before = server.get_character(cid, h)
    with pytest.raises(ValueError, match="enforce_sell_cap"):
        server.sell_item(cid, h, "Longsword", price_gp=100)
    # nothing persisted: the item is still held, purse unchanged
    after = server.get_character(cid, h)
    assert any(i["name"] == "Longsword" for i in after["inventory"])
    assert after["currency"] == before["currency"]


def test_enforce_sell_cap_allows_under_cap(shop):
    cid, h = shop
    server.set_house_rules(cid, {"enforce_sell_cap": True})
    server.add_item(cid, h, item_name="Longsword")  # list 15, cap 2x = 30
    out = server.sell_item(cid, h, "Longsword", price_gp=25)  # under cap
    assert out["currency"]["gp"] == 25
    assert "warning" not in out


# --- F09-10: adjust_currency value paths (spend_gp / earn_gp) ------------------


def test_adjust_spend_gp_makes_change(shop):
    cid, h = shop
    server.adjust_currency(cid, h, sp=120)  # 12 gp of value in silver
    out = server.adjust_currency(cid, h, spend_gp=5)  # spend 5 gp value
    assert inventory.total_copper(Currency(**out)) == 700  # 7 gp left, change made


def test_adjust_earn_gp_credits_value(shop):
    cid, h = shop
    out = server.adjust_currency(cid, h, earn_gp=15)
    assert inventory.total_copper(Currency(**out)) == 1500


def test_adjust_spend_gp_insufficient_raises_and_persists_nothing(shop):
    cid, h = shop
    server.adjust_currency(cid, h, gp=2)
    before = server.get_character(cid, h)
    with pytest.raises(ValueError, match="insufficient funds"):
        server.adjust_currency(cid, h, spend_gp=5)
    after = server.get_character(cid, h)
    assert after["currency"] == before["currency"]  # atomic: nothing changed


def test_adjust_denomination_underflow_error_points_at_spend_gp(shop):
    # F09-10: subtracting more gp COINS than held errors with a hint to use spend_gp
    # (which CAN make change) — the original raw ValueError gave no affordance.
    cid, h = shop
    server.adjust_currency(cid, h, sp=120)  # 12 gp of VALUE, but 0 gp coins
    with pytest.raises(ValueError, match="spend_gp"):
        server.adjust_currency(cid, h, gp=-5)  # no gp coins to subtract


def test_adjust_denomination_path_unchanged(shop):
    # additive regression: the plain denomination path behaves exactly as before.
    cid, h = shop
    out = server.adjust_currency(cid, h, gp=10, sp=5)
    assert out["gp"] == 10 and out["sp"] == 5


def test_adjust_negative_value_param_rejected(shop):
    cid, h = shop
    with pytest.raises(ValueError, match="non-negative"):
        server.adjust_currency(cid, h, spend_gp=-1)
