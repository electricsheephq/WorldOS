"""Auto-set the single SRD subclass on seat at/past the choice level (L10 canon Paladin no-oath).

THE BUG (live sweep): a canon figure loaded as a PC at high level kept a NULL subclass. The
seated L10 Paladin "Devella Fountainhead" carried classes=[{name:"Paladin",level:10,
subclass:null}], so the optimizer persona flagged "Level 10 Paladin still showing Choose
Subclass — Sacred Oath not set" and never received the owed subclass features (Aura of
Devotion @7, Sacred Weapon + Oath Spells @3, ...). This is the cross_persona_sat lever.

ROOT CAUSE: `_apply_srd_class_defaults` only granted the subclass features when a subclass
was ALREADY set — a Paladin with subclass=None skipped the whole block.

THE FIX (additive, SRD 5.2.1-correct): SRD 5.2.1 ships EXACTLY ONE subclass per class (all 12
classes return exactly one `subclass_options` entry, all at `subclass_level == 3`). So a
character AT/PAST the subclass-choice level with NO subclass has an UNAMBIGUOUS default — the
sole legal SRD option. We auto-set it there. Below the choice level, or when a class ever ships
>1 SRD subclass, the subclass stays null (a real pending choice). A character that ALREADY has a
subclass set is byte-identical to before.

DOMAIN-SCOPED (opt-in): the auto-set is gated by `_apply_srd_class_defaults`'s
`autoset_single_subclass` flag, which ONLY the canon-load seat (server.load_canon_character) sets
True — a canon figure pulled straight in as a high-level PC has no planner step to pick at. The
deliberate create_character + level-up *planner* path keeps the flag OFF (default), so there the
subclass stays a planner-offered 'overdue choice' (the #607/#888 picker) — byte-identical to
today. These tests exercise both sides of that gate at the `_apply_srd_class_defaults` boundary.
"""

import pytest

import server
from models import Character, ClassLevel


def _paladin(level: int, subclass=None) -> Character:
    """A bare Paladin sheet at `level` (the live 'Devella Fountainhead' fingerprint when
    subclass=None), mirroring how the canon-seat path constructs a Character before
    _apply_srd_class_defaults runs (see server.load_canon_character)."""
    return Character(
        name="Devella Fountainhead",
        kind="player",
        classes=[ClassLevel(name="Paladin", level=level, subclass=subclass)],
    )


# --- the fix: a null-subclass Paladin at/past the choice level gets the sole SRD oath ----


def test_l10_paladin_null_subclass_autosets_oath_and_gets_aura():
    # THE BUG: an L10 Paladin seated via the canon-load path (autoset_single_subclass=True)
    # with subclass=None must now seat WITH the single SRD oath (Oath of Devotion) and the
    # features it is owed THROUGH the levels — closing the optimizer "Level 10 Paladin still
    # showing Choose Subclass — Sacred Oath not set" finding.
    ch = _paladin(10, subclass=None)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False,
                                     autoset_single_subclass=True)
    pal = next(cl for cl in ch.classes if cl.name.lower() == "paladin")
    assert pal.subclass == "Oath of Devotion", "the sole SRD oath must be auto-set at L10"
    assert "Aura of Devotion" in ch.features, "L10 oath Paladin is owed Aura of Devotion (7)"
    # the choice-level features come along too (granted THROUGH the levels)
    assert "Sacred Weapon" in ch.features
    assert "Oath of Devotion Spells" in ch.features


def test_l3_paladin_null_subclass_autosets_at_the_choice_level():
    # Boundary: AT the choice level (3), with the canon-load opt-in, the sole oath is auto-set
    # (choice-level features only; Aura of Devotion (7) is not yet owed).
    ch = _paladin(3, subclass=None)
    server._apply_srd_class_defaults(ch, "Paladin", 3, set_base_ac=False,
                                     autoset_single_subclass=True)
    pal = next(cl for cl in ch.classes if cl.name.lower() == "paladin")
    assert pal.subclass == "Oath of Devotion"
    assert "Sacred Weapon" in ch.features
    assert "Aura of Devotion" not in ch.features  # not owed until level 7


# --- NEGATIVE: below the choice level stays null (a real pending choice) -----------------


def test_l2_paladin_null_subclass_stays_null_below_choice_level():
    # Additive guard: even with the opt-in, a Paladin BELOW the subclass-choice level (3) keeps
    # subclass=None and gains no oath features — the oath is a genuine pending choice, not yet due.
    ch = _paladin(2, subclass=None)
    server._apply_srd_class_defaults(ch, "Paladin", 2, set_base_ac=False,
                                     autoset_single_subclass=True)
    pal = next(cl for cl in ch.classes if cl.name.lower() == "paladin")
    assert pal.subclass is None, "below the choice level the subclass must stay unset"
    assert "Sacred Weapon" not in ch.features
    assert "Aura of Devotion" not in ch.features


# --- NEGATIVE: the flag is OFF by default -> the create/planner path is byte-identical ----


def test_l10_paladin_null_subclass_stays_null_without_optin():
    # DOMAIN GATE: without the opt-in (the default — the deliberate create_character + level-up
    # planner path) an L10 Paladin with subclass=None is BYTE-IDENTICAL to before: the subclass
    # stays null (a planner-offered 'overdue choice', the #607/#888 picker) and no oath features
    # are silently granted. This guards that the auto-set never leaks onto the planner domain.
    ch = _paladin(10, subclass=None)
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False)  # flag OFF (default)
    pal = next(cl for cl in ch.classes if cl.name.lower() == "paladin")
    assert pal.subclass is None, "default (no opt-in) must leave the subclass a pending choice"
    assert "Aura of Devotion" not in ch.features
    assert "Sacred Weapon" not in ch.features


# --- POSITIVE-no-change: an already-set subclass is untouched ----------------------------


def test_l10_paladin_already_set_subclass_is_unchanged():
    # Additivity: a Paladin that ALREADY carries the oath behaves exactly as before — the
    # existing resolve+grant path runs, the canonical name is kept, the features are granted.
    # (Independent of the opt-in: the existing `if sub:` branch fires regardless.)
    ch = _paladin(10, subclass="Oath of Devotion")
    server._apply_srd_class_defaults(ch, "Paladin", 10, set_base_ac=False)
    pal = next(cl for cl in ch.classes if cl.name.lower() == "paladin")
    assert pal.subclass == "Oath of Devotion"
    assert "Aura of Devotion" in ch.features
    assert "Sacred Weapon" in ch.features
