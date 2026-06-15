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
