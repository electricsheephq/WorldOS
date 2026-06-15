"""Canon-load default + display of a class-appropriate Fighting Style (sweep: veteran/optimizer).

THE COMPLAINT (3582dc2 sweep, veteran + optimizer, MAJOR): a canon figure loaded as a PC
(an L10 Paladin / an L11 Champion Fighter) showed "Fighting Style" only as a blank RULES
STUB — no specific style chosen or displayed ("Fighting Style chosen at L2 is not displayed";
"Fighting Style not selected/displayed — two slots unresolved"). SRD 5.2: a Fighter gets a
Fighting Style at L1, a Paladin and a Ranger at L2.

THE FIX (additive, mirrors the oath fix #895): an ADDITIVE `fighting_style: str = ""` on the
Character, default-set ONLY on the canon-load seat (the `autoset_single_subclass` opt-in flag,
the same gate #895 threads) to a CLASS-APPROPRIATE SRD style when the class grants Fighting
Style by the character's level AND the field is empty: Paladin->"Defense", Fighter->"Defense",
Ranger->"Archery". A class with NO Fighting Style (Wizard), a canon-load BELOW the grant level
(Paladin L1), and the create/level-up planner path (flag OFF) all stay "" — byte-identical, so
old snapshots round-trip and the player-built planner still surfaces the choice.

AC EFFECT (feasibility-gated): the Defense style grants +1 AC while wearing armor. AC is set
ONCE at the seat (inside `_apply_srd_class_defaults`'s `set_base_ac` block, from the class base
AC that already reflects worn armor for the martials); equip_item is ADVISORY and never touches
armor_class; a re-seat passes set_base_ac=False (AC != 10) so the whole block is skipped. That
makes the `set_base_ac` block a CLEAN single insertion point — applied once, provably no
double-count. These tests assert the +1 lands once and survives a re-seat.

DOMAIN-SCOPED (opt-in): the default + AC effect are gated by the `autoset_single_subclass` flag,
which ONLY the canon-load seat (server.load_canon_character) sets True. The create_character +
level-up planner path keeps the flag OFF (default) -> byte-identical to today.
"""

import server
from models import Character, ClassLevel


def _sheet(class_name: str, level: int) -> Character:
    """A bare class sheet at `level`, mirroring how the canon-seat path constructs a Character
    before _apply_srd_class_defaults runs (see server.load_canon_character)."""
    return Character(
        name=f"Canon {class_name}",
        kind="player",
        classes=[ClassLevel(name=class_name, level=level, subclass=None)],
    )


# --- the fix: canon-load opt-in sets a class-appropriate Fighting Style by grant level ----


def test_canon_paladin_l10_gets_defense_fighting_style():
    # THE BUG: an L10 Paladin seated via the canon-load path (autoset_single_subclass=True) must
    # now carry a NAMED Fighting Style ("Defense"), not a blank stub. Paladin grants it at L2.
    ch = _sheet("Paladin", 10)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Defense", "an L10 canon Paladin is owed a class-appropriate style"


def test_canon_fighter_l1_gets_defense_fighting_style():
    # Fighter grants Fighting Style at L1 (SRD 5.2), so even a fresh L1 canon Fighter is owed one.
    ch = _sheet("Fighter", 1)
    server._apply_srd_class_defaults(ch, "Fighter", 1, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Defense"


def test_canon_fighter_l11_champion_gets_defense_fighting_style():
    # The veteran's second exemplar: an L11 Champion Fighter. Still the L1 Fighting Style slot.
    ch = _sheet("Fighter", 11)
    server._apply_srd_class_defaults(ch, "Fighter", 11, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Defense"


def test_canon_ranger_l2_gets_archery_fighting_style():
    # Ranger grants Fighting Style at L2 and the class-appropriate SRD default is Archery.
    ch = _sheet("Ranger", 2)
    server._apply_srd_class_defaults(ch, "Ranger", 2, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Archery"


# --- NEGATIVE: a class with NO Fighting Style stays "" -------------------------------------


def test_canon_wizard_has_no_fighting_style():
    # A Wizard never gains Fighting Style -> the field stays empty even on the canon-load seat.
    ch = _sheet("Wizard", 10)
    server._apply_srd_class_defaults(ch, "Wizard", 10, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == ""


# --- NEGATIVE: below the grant level stays "" (a real pending choice, not yet due) ---------


def test_canon_paladin_l1_below_grant_level_stays_empty():
    # A Paladin BELOW the L2 grant level keeps "" — the style is not yet owed.
    ch = _sheet("Paladin", 1)
    server._apply_srd_class_defaults(ch, "Paladin", 1, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == ""


def test_canon_ranger_l1_below_grant_level_stays_empty():
    ch = _sheet("Ranger", 1)
    server._apply_srd_class_defaults(ch, "Ranger", 1, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == ""


# --- NEGATIVE: the flag is OFF by default -> create/level-up planner path is byte-identical -


def test_create_path_fighter_l1_stays_empty_without_optin():
    # DOMAIN GATE: without the opt-in (the deliberate create_character + level-up planner path)
    # even a Fighter at the grant level keeps fighting_style="" — the planner surfaces the choice
    # for a player-built char. This guards that the auto-set never leaks onto the planner domain.
    ch = _sheet("Fighter", 1)
    server._apply_srd_class_defaults(ch, "Fighter", 1, set_base_ac=False)  # flag OFF (default)
    assert ch.fighting_style == "", "default (no opt-in) must leave the style a planner choice"


def test_create_path_paladin_l10_stays_empty_without_optin():
    ch = _sheet("Paladin", 10)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False)  # flag OFF (default)
    assert ch.fighting_style == ""


# --- POSITIVE-no-change: an already-chosen style is never clobbered ------------------------


def test_existing_fighting_style_is_respected():
    # Additivity: a sheet that ALREADY carries a chosen style (a hand-authored / DM-set canon
    # record) keeps it — the default-fill only fills an EMPTY field.
    ch = _sheet("Fighter", 6)
    ch.fighting_style = "Great Weapon Fighting"
    server._apply_srd_class_defaults(ch, "Fighter", 6, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Great Weapon Fighting"


# --- additive round-trip: an old snapshot with no field deserializes to "" -----------------


def test_old_snapshot_without_field_round_trips_to_empty():
    # ADDITIVE invariant: a snapshot predating the field has no `fighting_style` key; it must
    # deserialize to "" (the default) and re-serialize without surprises.
    legacy = {
        "name": "Legacy Hero",
        "classes": [{"name": "Fighter", "level": 5, "subclass": None}],
    }
    ch = Character.model_validate(legacy)
    assert ch.fighting_style == ""


# --- AC EFFECT (feasibility-gated): Defense grants +1 AC while wearing armor, no double-count -


def test_canon_paladin_defense_grants_plus_one_ac_once():
    # The Defense style grants +1 AC while wearing armor. A canon Paladin's base AC is 16 (Chain
    # Mail / worn armor) -> with Defense the seat must land at 17. The +1 is applied at the single
    # set_base_ac insertion point (after the base AC), gated on the Defense style.
    ch = _sheet("Paladin", 10)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=True,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Defense"
    # Base Paladin AC is 16 (class_base_ac), +1 Defense = 17. (CON/DEX don't alter a worn-armor AC.)
    assert ch.armor_class == 17, "Defense must add exactly +1 over the worn-armor base AC"


def test_defense_ac_not_double_counted_on_reseat():
    # NO DOUBLE-COUNT: a re-seat (the canon-load path passes set_base_ac=(armor_class==10), so a
    # second seat on an already-armored sheet passes set_base_ac=False) must NOT add a second +1.
    ch = _sheet("Paladin", 10)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=True,
                                     autoset_single_subclass=True)
    assert ch.armor_class == 17
    # Re-seat with set_base_ac=False (AC is now 17, not 10) -> the whole AC block is skipped.
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False,
                                     autoset_single_subclass=True)
    assert ch.armor_class == 17, "a re-seat must not stack a second +1 (stays 17, not 18)"


def test_create_path_no_ac_bonus_without_optin():
    # The AC effect is gated to the canon-load opt-in: the create/level-up planner path (flag OFF)
    # never gets the +1, so the base AC is byte-identical to today (16 for a Paladin).
    ch = _sheet("Paladin", 10)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=True)  # flag OFF (default)
    assert ch.fighting_style == ""
    assert ch.armor_class == 16, "no opt-in -> no Defense +1; base worn-armor AC unchanged"


def test_archery_ranger_no_ac_bonus():
    # Archery is a ranged-attack bonus, NOT an AC bonus — a canon Ranger's AC must be the plain
    # worn-armor base (14), proving the +1 is Defense-specific and never applied for Archery.
    ch = _sheet("Ranger", 2)
    server._apply_srd_class_defaults(ch, "Ranger", 2, set_base_ac=True,
                                     autoset_single_subclass=True)
    assert ch.fighting_style == "Archery"
    assert ch.armor_class == 14, "Archery grants no AC; base worn-armor AC (14) unchanged"
