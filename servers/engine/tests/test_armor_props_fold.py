"""Item armor-FK resolve path must fold the SRD armor's stealth/STR props.

`itemcatalog.resolve("Chain Mail")` resolves via the Item.json record, whose
`armor` FK joins to the bare Armor.json row. That FK block folded AC + the
DEX-mod rule but DROPPED the armor's stealth-disadvantage + STR-requirement —
so the viewer item inspector rendered Chain Mail with no STR-req/stealth pill.
The `model == "armor"` path already builds those two props; this mirrors them
onto the Item armor-FK path. Additive: an armor whose SRD source has
stealth=false/str=null gains no pill.
"""

import itemcatalog


def test_chain_mail_folds_stealth_disadvantage_and_str_req():
    # SRD Armor.json: Chain Mail grants_stealth_disadvantage=true,
    # strength_score_required=13 — both must surface as inspector pills.
    rec = itemcatalog.resolve("Chain Mail")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert "stealth-disadvantage" in rec["properties"]
    assert "str-13" in rec["properties"]


def test_breastplate_has_no_stealth_or_str_pill():
    # SRD: Breastplate is stealth=false / str=null — additive fix must NOT
    # fabricate a pill for it.
    rec = itemcatalog.resolve("Breastplate")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert not any(p == "stealth-disadvantage" or p.startswith("str-")
                   for p in rec["properties"])


def test_light_armor_has_no_stealth_or_str_pill():
    # Light armor (Leather) is stealth=false / str=null — clean inspector.
    rec = itemcatalog.resolve("Leather Armor")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert not any(p == "stealth-disadvantage" or p.startswith("str-")
                   for p in rec["properties"])


def test_stealth_str_pills_are_not_duplicated():
    # De-dup guard: each pill appears at most once.
    rec = itemcatalog.resolve("Chain Mail")
    assert rec["properties"].count("stealth-disadvantage") == 1
    assert rec["properties"].count("str-13") == 1


# --- Negation: a few SRD magic armors REMOVE the base armor's stealth/STR -----
# penalties their `armor` FK would otherwise fold in (Mithral / Elven Chain).
# Suppressing these mirrors the item's own rules text — without it the inspector
# rendered a false penalty pill (the gap #899's FK fold made newly visible).

def _no_stealth_or_str(rec):
    return not any(p == "stealth-disadvantage" or p.startswith("str-")
                   for p in rec["properties"])


def test_mithral_chain_mail_suppresses_base_stealth_and_str():
    # SRD desc: "If the armor normally imposes Disadvantage on Dexterity
    # (Stealth) checks or has a Strength requirement, the mithral version ...
    # doesn't." Its `armor` FK is srd-2024_chain-mail (stealth + str-13), but
    # the mithral version negates both.
    rec = itemcatalog.resolve("Mithral Armor (Chain Mail)")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert rec["ac"]  # AC still inherited via the FK; only the penalties drop
    assert _no_stealth_or_str(rec), rec["properties"]


def test_mithral_plate_and_splint_suppress_base_stealth_and_str():
    # Plate (str-15) and Splint (str-15) bases both impose stealth + STR; the
    # mithral versions negate both via the same desc clause.
    for name in ("Mithral Armor (Plate)", "Mithral Armor (Splint)",
                 "Mithral Armor (Half Plate)", "Mithral Armor (Scale Mail)",
                 "Mithral Armor (Ring Mail)"):
        rec = itemcatalog.resolve(name)
        assert rec is not None, name
        assert _no_stealth_or_str(rec), (name, rec["properties"])


def test_elven_chain_mail_suppresses_base_stealth_and_str():
    # 5e Elven Chain has no Stealth disadvantage and no STR requirement, but the
    # SRD dump carries no negation clause in its desc, so it is matched by name
    # prefix. Its `armor` FK is srd-2024_chain-mail (stealth + str-13).
    rec = itemcatalog.resolve("Elven Chain Mail")
    assert rec is not None
    assert rec["kind"] == "armor"
    assert rec["ac"]  # +1 base armor AC still inherited
    assert _no_stealth_or_str(rec), rec["properties"]


def test_elven_chain_shirt_has_no_stealth_or_str_pill():
    # Chain Shirt base carries no penalty, so this was already clean — guard it
    # so the negation set never regresses it into carrying a pill.
    rec = itemcatalog.resolve("Elven Chain Shirt")
    assert rec is not None
    assert _no_stealth_or_str(rec), rec["properties"]


def test_negation_does_not_regress_plain_chain_mail():
    # Non-negating heavy armor MUST keep its pills — the negation set is narrow.
    rec = itemcatalog.resolve("Chain Mail")
    assert "stealth-disadvantage" in rec["properties"]
    assert "str-13" in rec["properties"]
