"""Inventory & economy helpers (SRD 5.2). Pure functions over a Character; the
engine tools wrap them with the campaign lock + persistence.

Currency is normalized through copper for spending/earning so paying 5 gp from a
purse of silver "makes change" correctly (Decimal keeps 2-decimal gp exact).
Encumbrance uses the SRD variant thresholds (STR x5 encumbered, x10 heavily
encumbered) plus the standard STR x15 carrying capacity.
"""

from __future__ import annotations

from decimal import Decimal

from models import Character, Currency, Item

ATTUNEMENT_LIMIT = 3
_COINS_PER_POUND = 50  # SRD variant: 50 coins weigh 1 lb

# F09-7: the structured stat fields a catalog grant persists onto an Item (beyond the
# original name/weight/attunement/description). Kept in one place so _split_one's clone,
# add_item's stacking-identity check, and the server-side catalog extractor all agree —
# the audit's watch-item is that _split_one MUST carry these or a split stack loses them.
_STAT_FIELDS = (
    "kind", "rarity", "cost_gp", "damage", "damage_type",
    "ac", "armor_category", "ac_dex_mod", "ac_dex_cap", "properties",
)


def total_copper(cur: Currency) -> int:
    return cur.cp + cur.sp * 10 + cur.ep * 50 + cur.gp * 100 + cur.pp * 1000


def _gp_to_cp(gp: float) -> int:
    # Exact for 2-decimal gp (avoids float/banker's-rounding drift).
    return int((Decimal(str(gp)) * 100).to_integral_value())


def _from_copper(total: int) -> Currency:
    # Canonical change in gp/sp/cp (value-preserving); rare pp/ep fold into gp/sp.
    gp, rem = divmod(total, 100)
    sp, cp = divmod(rem, 10)
    return Currency(cp=cp, sp=sp, gp=gp)


def pay(ch: Character, gp_amount: float) -> Currency:
    """Spend gp_amount (via total copper, making change). Raises on negative or
    insufficient funds."""
    if gp_amount < 0:
        raise ValueError("cannot pay a negative amount")
    cost = _gp_to_cp(gp_amount)
    have = total_copper(ch.currency)
    if have < cost:
        raise ValueError("insufficient funds")
    ch.currency = _from_copper(have - cost)
    return ch.currency


def gain(ch: Character, gp_amount: float) -> Currency:
    if gp_amount < 0:
        raise ValueError("cannot gain a negative amount")
    ch.currency = _from_copper(total_copper(ch.currency) + _gp_to_cp(gp_amount))
    return ch.currency


def gp_to_cp(gp_amount: float) -> int:
    """Exact copper value of a (2-decimal) gp amount — the public face of the
    Decimal conversion so tool-level totals (unit price x quantity, F09-2) stay
    copper-exact instead of accumulating float drift."""
    return _gp_to_cp(gp_amount)


def pay_cp(ch: Character, amount_cp: int) -> Currency:
    """Spend an exact copper amount (making change). Raises on negative or
    insufficient funds. Copper-exact sibling of pay() for unit x quantity totals."""
    if amount_cp < 0:
        raise ValueError("cannot pay a negative amount")
    have = total_copper(ch.currency)
    if have < amount_cp:
        raise ValueError("insufficient funds")
    ch.currency = _from_copper(have - amount_cp)
    return ch.currency


def gain_cp(ch: Character, amount_cp: int) -> Currency:
    """Gain an exact copper amount. Copper-exact sibling of gain()."""
    if amount_cp < 0:
        raise ValueError("cannot gain a negative amount")
    ch.currency = _from_copper(total_copper(ch.currency) + amount_cp)
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


def _find(ch: Character, name: str, predicate=None) -> Item:
    matches = [it for it in ch.inventory if it.name.lower() == name.lower()]
    if not matches:
        raise ValueError(f"no item named {name!r}")
    if predicate:
        for it in matches:
            if predicate(it):
                return it
    return matches[0]


def _split_one(ch: Character, item: Item) -> Item:
    """Split a single unit off a stack so it can be individually equipped/attuned.
    Returns the original if quantity is already 1."""
    if item.quantity <= 1:
        return item
    item.quantity -= 1
    # F09-7: clone via the full model so EVERY Item field (incl. the new structured
    # stats) carries to the split-off unit — equipped/attuned reset to a fresh unit.
    clone = item.model_copy(deep=True)
    clone.quantity = 1
    clone.equipped = False
    clone.attuned = False
    ch.inventory.append(clone)
    return clone


def add_item(
    ch: Character, name, quantity=1, weight=0.0, requires_attunement=False,
    description="", stats: dict | None = None,
) -> Item:
    """Add an item, stacking with a FULLY identical unequipped/non-attuned unit. `stats`
    (F09-7) carries the catalog's structured fields (kind/rarity/cost_gp/damage/ac/…) to
    persist onto a granted Item; None == today's free-text behavior (all stat fields stay
    at their empty defaults). Two grants stack only when their stats are identical too —
    identical catalog grants produce identical stats, so they still merge."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    stats = {k: stats[k] for k in _STAT_FIELDS if stats and k in stats}
    # The Item this grant would CREATE — used both to construct the record and to compare
    # against an existing stack (so we stack only on byte-identical structured stats).
    prospective = Item(
        name=name, quantity=quantity, weight=weight,
        requires_attunement=requires_attunement, description=description, **stats,
    )
    for it in ch.inventory:  # only stack a fully-identical, unequipped, non-attuned item
        if (
            it.name.lower() == name.lower()
            and not it.equipped
            and not it.attuned
            and it.weight == weight
            and it.requires_attunement == requires_attunement
            and it.description == description
            # F09-7: the structured stats must match too, or two differently-statted
            # grants of the same name would silently merge under one record.
            and all(getattr(it, k) == getattr(prospective, k) for k in _STAT_FIELDS)
        ):
            it.quantity += quantity
            return it
    ch.inventory.append(prospective)
    return prospective


def remove_item(ch: Character, name, quantity=1) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    it = _find(ch, name, predicate=lambda i: not i.equipped and not i.attuned)
    if quantity > it.quantity:
        raise ValueError(f"only {it.quantity} of {name!r} held (tried to remove {quantity})")
    if quantity == it.quantity:
        ch.inventory.remove(it)
    else:
        it.quantity -= quantity


def set_equipped(ch: Character, name, equipped) -> Item:
    if equipped:
        it = _split_one(ch, _find(ch, name, predicate=lambda i: not i.equipped))
        it.equipped = True
    else:
        it = _find(ch, name, predicate=lambda i: i.equipped)
        it.equipped = False
    return it


def set_attuned(ch: Character, name, attuned) -> Item:
    if attuned:
        it = _find(ch, name, predicate=lambda i: not i.attuned)
        if not it.requires_attunement:
            raise ValueError(f"{name} does not require attunement")
        if sum(1 for i in ch.inventory if i.attuned) >= ATTUNEMENT_LIMIT:
            raise ValueError(f"already attuned to {ATTUNEMENT_LIMIT} items")
        it = _split_one(ch, it)
        it.attuned = True
    else:
        it = _find(ch, name, predicate=lambda i: i.attuned)
        it.attuned = False
    return it
