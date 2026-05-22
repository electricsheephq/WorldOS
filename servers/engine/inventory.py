"""Inventory & economy helpers (SRD 5.2). Pure functions over a Character; the
engine tools wrap them with the campaign lock + persistence.

Currency is normalized through copper for spending/earning so paying 5 gp from a
purse of silver "makes change" correctly. Encumbrance uses the SRD variant
thresholds (STR x5 encumbered, x10 heavily encumbered) plus the standard STR x15
carrying capacity.
"""

from __future__ import annotations

from models import Character, Currency, Item

ATTUNEMENT_LIMIT = 3
_COINS_PER_POUND = 50  # SRD variant: 50 coins weigh 1 lb


def total_copper(cur: Currency) -> int:
    return cur.cp + cur.sp * 10 + cur.ep * 50 + cur.gp * 100 + cur.pp * 1000


def _from_copper(total: int) -> Currency:
    # Canonical change in gp/sp/cp (value-preserving); rare pp/ep fold into gp/sp
    # on a transaction, which keeps purses readable for players.
    gp, rem = divmod(total, 100)
    sp, cp = divmod(rem, 10)
    return Currency(cp=cp, sp=sp, gp=gp)


def pay(ch: Character, gp_amount: float) -> Currency:
    """Spend gp_amount (converted via total copper, making change). Raises if the
    character can't afford it."""
    cost = int(round(gp_amount * 100))
    have = total_copper(ch.currency)
    if cost < 0:
        raise ValueError("cannot pay a negative amount")
    if have < cost:
        raise ValueError("insufficient funds")
    ch.currency = _from_copper(have - cost)
    return ch.currency


def gain(ch: Character, gp_amount: float) -> Currency:
    ch.currency = _from_copper(total_copper(ch.currency) + int(round(gp_amount * 100)))
    return ch.currency


def adjust_currency(ch: Character, cp=0, sp=0, ep=0, gp=0, pp=0) -> Currency:
    """Add/subtract specific coin denominations (no auto change-making). Raises if
    any denomination would go negative."""
    new = {
        "cp": ch.currency.cp + cp,
        "sp": ch.currency.sp + sp,
        "ep": ch.currency.ep + ep,
        "gp": ch.currency.gp + gp,
        "pp": ch.currency.pp + pp,
    }
    if any(v < 0 for v in new.values()):
        raise ValueError("a coin denomination would go negative")
    ch.currency = Currency(**new)
    return ch.currency


def carried_weight(ch: Character) -> float:
    w = sum(i.weight * i.quantity for i in ch.inventory)
    coins = ch.currency.cp + ch.currency.sp + ch.currency.ep + ch.currency.gp + ch.currency.pp
    return round(w + coins / _COINS_PER_POUND, 2)


def encumbrance(ch: Character) -> dict:
    s = ch.abilities.strength
    carried = carried_weight(ch)
    if carried > s * 15:
        status = "overloaded"
    elif carried > s * 10:
        status = "heavily_encumbered"
    elif carried > s * 5:
        status = "encumbered"
    else:
        status = "unencumbered"
    return {
        "carried": carried,
        "max_capacity": s * 15,
        "encumbered_at": s * 5,
        "heavily_encumbered_at": s * 10,
        "status": status,
    }


def _find(ch: Character, name: str) -> Item:
    for it in ch.inventory:
        if it.name.lower() == name.lower():
            return it
    raise ValueError(f"no item named {name!r}")


def add_item(ch: Character, name, quantity=1, weight=0.0, requires_attunement=False, description="") -> Item:
    for it in ch.inventory:  # stack identical, unequipped, non-attuned items
        if it.name.lower() == name.lower() and not it.equipped and not it.attuned:
            it.quantity += quantity
            return it
    item = Item(
        name=name, quantity=quantity, weight=weight,
        requires_attunement=requires_attunement, description=description,
    )
    ch.inventory.append(item)
    return item


def remove_item(ch: Character, name, quantity=1) -> None:
    it = _find(ch, name)
    if it.quantity <= quantity:
        ch.inventory.remove(it)
    else:
        it.quantity -= quantity


def set_equipped(ch: Character, name, equipped) -> Item:
    it = _find(ch, name)
    it.equipped = equipped
    return it


def set_attuned(ch: Character, name, attuned) -> Item:
    it = _find(ch, name)
    if attuned:
        if not it.requires_attunement:
            raise ValueError(f"{name} does not require attunement")
        if not it.attuned and sum(1 for i in ch.inventory if i.attuned) >= ATTUNEMENT_LIMIT:
            raise ValueError(f"already attuned to {ATTUNEMENT_LIMIT} items")
    it.attuned = attuned
    return it
