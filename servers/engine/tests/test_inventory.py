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
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
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
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
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
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shop")["id"]
    h = server.create_character(cid, "Seller", kind="player")["id"]
    server.add_item(cid, h, "Trinket")
    with pytest.raises(Exception):
        server.sell_item(cid, h, "Trinket", price_gp=-10)
