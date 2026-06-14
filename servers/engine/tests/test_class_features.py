"""Class/subclass features at level-up (P2.3) — leveling grants real features."""

import pytest

import server
import srd_tables


def test_features_at_and_through_tables():
    assert any(f["name"] == "Extra Attack" for f in srd_tables.features_at("fighter", 5))
    names = {f["name"] for f in srd_tables.features_through("fighter", 5)}
    assert {"Second Wind", "Action Surge", "Extra Attack"} <= names


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Levels")["id"]


def test_level1_fighter_gets_creation_features(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", apply_srd_defaults=True
    )["id"]
    sheet = server.get_character(cid, fid)
    assert "Second Wind" in sheet["features"]
    assert sheet["extra_attacks"] == 0  # Extra Attack not until level 5


def test_level1_rogue_gets_sneak_attack(cid):
    rid = server.create_character(
        cid, "Sly", kind="player", class_name="Rogue", apply_srd_defaults=True
    )["id"]
    sheet = server.get_character(cid, rid)
    assert sheet["sneak_attack_dice"] == "1d6" and "Sneak Attack" in sheet["features"]


def test_level_up_grants_extra_attack(cid):
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=4, apply_srd_defaults=True
    )["id"]
    assert server.get_character(cid, fid)["extra_attacks"] == 0
    out = server.level_up(cid, fid, "Fighter")  # -> level 5
    assert any(f["name"] == "Extra Attack" for f in out["_features_gained"])
    sheet = server.get_character(cid, fid)
    assert sheet["extra_attacks"] == 1 and "Extra Attack" in sheet["features"]


def test_rogue_sneak_attack_scales_on_level_up(cid):
    rid = server.create_character(
        cid, "Sly", kind="player", class_name="Rogue", level=2, apply_srd_defaults=True
    )["id"]
    assert server.get_character(cid, rid)["sneak_attack_dice"] == "1d6"
    server.level_up(cid, rid, "Rogue")  # -> level 3
    assert server.get_character(cid, rid)["sneak_attack_dice"] == "2d6"


# ── #624: subclass (Arcane Tradition) options are EXPOSED, not free-text ──────────


def test_subclass_level_table():
    # Every SRD class chooses its subclass at a known level (most at 3; warlock at 3
    # too in SRD 5.2). The engine knows WHEN, so the surface can flag the choice.
    assert srd_tables.subclass_level("wizard") == 3
    assert srd_tables.subclass_level("cleric") == 3
    assert srd_tables.subclass_level("fighter") == 3


def test_wizard_subclass_options_exposed_with_preview():
    opts = srd_tables.subclass_options("wizard")
    assert opts, "wizard must expose at least one Arcane Tradition option"
    names = {o["name"] for o in opts}
    assert "Evoker" in names  # the SRD 5.2 Arcane Tradition
    evoker = next(o for o in opts if o["name"] == "Evoker")
    # The option carries a brief feature preview so the picker isn't a blind text box.
    assert evoker.get("desc"), "subclass option must carry a description/preview"
    assert evoker.get("features"), "subclass option must list the features it grants"
    assert any("Evocation Savant" in f["name"] or "Sculpt Spells" in f["name"]
               for f in evoker["features"])


def test_subclass_options_match_by_alias():
    # The player/DM may name the tradition loosely ("Evocation") — the engine
    # resolves it to the canonical SRD subclass ("Evoker").
    resolved = srd_tables.resolve_subclass("wizard", "Evocation")
    assert resolved == "Evoker"
    assert srd_tables.resolve_subclass("wizard", "Evoker") == "Evoker"
    assert srd_tables.resolve_subclass("wizard", "not-a-real-tradition") is None


def test_level_up_to_subclass_level_applies_subclass_features(cid):
    # A Wizard reaching L3 and choosing the Evocation tradition gains its L3
    # features (Evocation Savant, Sculpt Spells) — not just the generic placeholder.
    wid = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard", level=2,
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.level_up(cid, wid, "Wizard", subclass="Evocation")  # -> level 3
    assert out["classes"][0]["subclass"] == "Evoker"  # normalized to canonical SRD name
    gained = {f["name"] for f in out["_features_gained"]}
    assert "Evocation Savant" in gained and "Sculpt Spells" in gained
    sheet = server.get_character(cid, wid)
    assert "Evocation Savant" in sheet["features"]
    assert "Sculpt Spells" in sheet["features"]


def test_create_wizard_at_subclass_level_applies_subclass_features(cid):
    # A Wizard CREATED directly at L3 with a subclass gets its subclass features too
    # (features_through, the from-scratch path).
    wid = server.create_character(
        cid, "Tara", kind="player", class_name="Wizard", level=3, subclass="Evoker",
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    sheet = server.get_character(cid, wid)
    assert "Evocation Savant" in sheet["features"]
    assert "Sculpt Spells" in sheet["features"]


def test_build_options_exposes_subclass_choice_at_subclass_level(cid):
    # The build planner the /character surface reads must surface the legal subclass
    # options (with previews) when the next level grants a subclass — so the picker
    # presents a real list instead of a free-text box.
    wid = server.create_character(
        cid, "Nyx", kind="player", class_name="Wizard", level=2,
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    planner = server.build_options(cid, wid)
    wiz_opt = next(o for o in planner["options"] if o["class_name"] == "wizard")
    sub = wiz_opt.get("subclass")
    assert sub and sub["required"] is True
    names = {o["name"] for o in sub["options"]}
    assert "Evoker" in names
    assert all(o.get("desc") for o in sub["options"])


def test_build_options_subclass_options_carry_full_feature_detail(cid):
    # #607 (RRI-25e55fa optimizer): the viewer subclass picker reads build_options (GET
    # /build-options). The optimizer asked to COMPARE archetypes by their features. The
    # SRD 5.2.1 ships exactly ONE subclass per class (Champion for Fighter — Battle Master /
    # Eldritch Knight are licensed PHB content, not shippable), so the real, in-scope fix is
    # that the available archetype carries its FULL feature set (choice-level PLUS higher-
    # level features, each with rules text + level) — not just the lone level-3 entry.
    # The bug: build_options omitted full_features (preview_level_up already passed it).
    wid = server.create_character(
        cid, "Lyra", kind="player", class_name="Fighter", level=2,
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    planner = server.build_options(cid, wid)
    fopt = next(o for o in planner["options"] if o["class_name"] == "fighter")
    sub = fopt.get("subclass")
    assert sub and sub["required"] is True
    champion = next(o for o in sub["options"] if o["name"] == "Champion")
    feats = champion.get("features") or []
    # Full set (>1 = more than the lone choice-level entry), each with rules text + level.
    assert len(feats) >= 2, f"expected full feature list, got {feats}"
    assert all(f.get("desc") and f.get("level") for f in feats), feats


# ── #624 backfill (rc2 audit): a MISSED subclass choice is offered at the NEXT
# level-up — an L5 wizard with no Arcane Tradition (the pendingSubclass case) must
# still get the options block, not the free-text fallback. ──────────────────────


def test_build_options_backfills_missed_subclass_choice(cid):
    # An L5 wizard with NO subclass set (already PAST the choice level) leveling to
    # L6 must still get the subclass options block — a missed choice is offered at
    # the next level-up (5e table-rules common practice).
    wid = server.create_character(
        cid, "Vex", kind="player", class_name="Wizard", level=5,
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    planner = server.build_options(cid, wid)
    wiz_opt = next(o for o in planner["options"] if o["class_name"] == "wizard")
    sub = wiz_opt.get("subclass")
    assert sub and sub["required"] is True
    names = {o["name"] for o in sub["options"]}
    assert "Evoker" in names
    assert all(o.get("desc") for o in sub["options"])


def test_build_options_no_subclass_block_past_level_when_already_chosen(cid):
    # An L5 wizard who ALREADY has a tradition gets NO subclass block past the
    # choice level — the choice is not re-offered (unchanged behavior).
    wid = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard", level=5, subclass="Evoker",
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    planner = server.build_options(cid, wid)
    wiz_opt = next(o for o in planner["options"] if o["class_name"] == "wizard")
    assert wiz_opt.get("subclass") is None


def test_level_up_backfill_applies_choice_level_features(cid):
    # Choosing the subclass on the backfill path (L5 wizard, no subclass, leveling
    # to L6 with subclass named) still grants the CHOICE-LEVEL features — the
    # missed L3 set (Evocation Savant + Sculpt Spells), not nothing.
    wid = server.create_character(
        cid, "Vex", kind="player", class_name="Wizard", level=5,
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.level_up(cid, wid, "Wizard", subclass="Evocation")  # -> level 6
    assert out["classes"][0]["subclass"] == "Evoker"  # normalized to canonical SRD name
    gained = {f["name"] for f in out["_features_gained"]}
    assert "Evocation Savant" in gained and "Sculpt Spells" in gained
    sheet = server.get_character(cid, wid)
    assert "Evocation Savant" in sheet["features"]
    assert "Sculpt Spells" in sheet["features"]


# ── #607 / RRI-25e55fa optimizer: preview_level_up EXPOSES the subclass picker ──
# The optimizer's #1 finding: the subclass picker showed only ONE option and no
# feature text, and an L11 sheet with "Choose your subclass" unfilled didn't flag
# the choice as overdue. preview_level_up — the single-class level-up data path —
# must carry the legal SRD subclass options (WITH feature text) AND a due/overdue
# signal. (SRD 5.2 licenses ONE subclass per class — Champion / Evoker / … — so the
# list is the SRD-correct set; the bug was that it was empty/featureless/unflagged.)


def test_preview_level_up_exposes_subclass_choice_at_choice_level(cid):
    # A Fighter L2 -> L3 (the subclass-choice level) must carry the subclass-choice
    # block: the legal SRD options, each with a description AND its features.
    fid = server.create_character(
        cid, "Aria", kind="player", class_name="Fighter", level=2,
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.preview_level_up(cid, fid, "Fighter")  # -> level 3
    sub = out.get("subclass_choice")
    assert sub is not None, "preview at the subclass-choice level must carry the picker block"
    assert sub["required"] is True
    assert sub["due"] is True and sub["overdue"] is False
    assert sub["group_label"] == "Martial Archetype"
    names = {o["name"] for o in sub["options"]}
    assert "Champion" in names  # the SRD 5.2 Martial Archetype
    champ = next(o for o in sub["options"] if o["name"] == "Champion")
    assert champ.get("desc"), "each subclass option carries a description"
    assert champ.get("features"), "each subclass option lists the features it grants"
    feat_names = {f["name"] for f in champ["features"]}
    assert "Improved Critical" in feat_names  # the level-3 archetype feature
    # each feature carries rules text the picker can show (not a bare name)
    assert all(f.get("desc") for f in champ["features"])


def test_preview_subclass_options_carry_higher_level_features(cid):
    # The task: each option lists "the level-3 (and where known, higher) features".
    # Champion gains Improved Critical + Remarkable Athlete at the choice level (3) AND
    # higher-level archetype features (Additional Fighting Style, Superior Critical,
    # Survivor, …) — surfaced from the licensed SRD ClassFeature text.
    fid = server.create_character(
        cid, "Bren", kind="player", class_name="Fighter", level=2,
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.preview_level_up(cid, fid, "Fighter")
    champ = next(o for o in out["subclass_choice"]["options"] if o["name"] == "Champion")
    feat_names = {f["name"] for f in champ["features"]}
    # the choice-level set is always present…
    assert "Improved Critical" in feat_names and "Remarkable Athlete" in feat_names
    # …and the higher-level archetype features are surfaced too (Champion gains more
    # past 3 in the SRD: Additional Fighting Style, Superior Critical, Survivor).
    assert len(champ["features"]) > 2, "higher-level archetype features must be surfaced"
    assert {"Additional Fighting Style", "Superior Critical", "Survivor"} <= feat_names
    # Every listed feature carries its rules text; the choice-level features carry level 3,
    # additional features carry an int level when SRD-known or None (never a fabricated level).
    assert all(f.get("desc") for f in champ["features"])
    by_name = {f["name"]: f for f in champ["features"]}
    assert by_name["Improved Critical"]["level"] == 3
    assert by_name["Remarkable Athlete"]["level"] == 3
    assert all((f["level"] is None) or (isinstance(f["level"], int) and f["level"] >= 3)
               for f in champ["features"])


def test_preview_no_subclass_block_when_not_due_and_already_chosen(cid):
    # A Fighter who ALREADY has an archetype gets NO subclass block on a later level-up
    # (the choice is not re-offered) — additive/unchanged for the common case.
    fid = server.create_character(
        cid, "Cael", kind="player", class_name="Fighter", level=4, subclass="Champion",
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.preview_level_up(cid, fid, "Fighter")  # -> level 5
    assert out.get("subclass_choice") is None


def test_preview_flags_overdue_subclass_choice_l11_fighter(cid):
    # The optimizer's L11 case: a Fighter at L11 with NO archetype set is OVERDUE for
    # its subclass choice (due at 3). Leveling to L12 the preview must flag the choice
    # as required AND overdue so the viewer can prompt — not silently skip it.
    fid = server.create_character(
        cid, "Dax", kind="player", class_name="Fighter", level=11,
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    out = server.preview_level_up(cid, fid, "Fighter")  # -> level 12
    sub = out.get("subclass_choice")
    assert sub is not None, "an overdue subclass choice must still surface the picker"
    assert sub["required"] is True
    assert sub["due"] is True
    assert sub["overdue"] is True, "a choice due at 3 but unset at 11 is OVERDUE"
    assert {o["name"] for o in sub["options"]} >= {"Champion"}


def test_preview_subclass_block_is_additive_no_mutation(cid):
    # The new block must not mutate state and must not appear for a multiclass step into
    # a class below its subclass level (no false picker).
    wid = server.create_character(
        cid, "Eir", kind="player", class_name="Wizard", level=1,
        abilities={"intelligence": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    before = server.get_character(cid, wid)
    out = server.preview_level_up(cid, wid, "Wizard")  # L1 -> L2, below the choice level
    assert out.get("subclass_choice") is None  # not due yet
    assert server.get_character(cid, wid) == before  # preview never mutates


# ── #888 / optimizer+veteran: subclass features populate THROUGH the levels ──────
# "Level 10 Paladin has NO Sacred Oath subclass — missing 7 levels of subclass
# features." SRD 5.2 ships exactly ONE shippable Paladin oath (Oath of Devotion),
# whose higher features land at 7 (Aura of Devotion) / 15 (Smite of Protection) /
# 20 (Holy Nimbus) — exactly the "Subclass Feature" placeholders. The fix: a
# Paladin seated/leveled AT or PAST those levels with the oath actually RECEIVES
# the oath features it is owed, not just the level-3 pair.


def test_subclass_features_through_paladin_oath_of_devotion():
    # The pure table: through L10 an Oath of Devotion Paladin is owed Sacred Weapon +
    # Oath of Devotion Spells (choice level 3) AND Aura of Devotion (7) — but NOT yet
    # Smite of Protection (15) or Holy Nimbus (20).
    through10 = {f["name"]: f.get("level")
                 for f in srd_tables.subclass_features_through("paladin", "Oath of Devotion", 10)}
    assert "Sacred Weapon" in through10 and through10["Sacred Weapon"] == 3
    assert "Oath of Devotion Spells" in through10
    assert through10.get("Aura of Devotion") == 7, "an L10 oath Paladin is owed Aura of Devotion (7)"
    assert "Smite of Protection" not in through10  # a level-15 feature isn't owed at 10
    assert "Holy Nimbus" not in through10           # a level-20 feature isn't owed at 10
    # …and at L20 it has the FULL oath progression (no fabricated levels).
    through20 = {f["name"]: f.get("level")
                 for f in srd_tables.subclass_features_through("paladin", "Devotion", 20)}
    assert through20.get("Aura of Devotion") == 7
    assert through20.get("Smite of Protection") == 15
    assert through20.get("Holy Nimbus") == 20


def test_subclass_features_through_below_choice_level_is_empty():
    # Below the subclass-choice level nothing is owed (additive — today's behavior).
    assert srd_tables.subclass_features_through("paladin", "Oath of Devotion", 2) == []
    # an unknown subclass is honest-empty too (never a fabrication)
    assert srd_tables.subclass_features_through("paladin", "not-an-oath", 10) == []


def test_create_paladin_at_l10_with_oath_populates_higher_features(cid):
    # A Paladin CREATED directly at L10 with Oath of Devotion (the seed path,
    # _apply_srd_class_defaults) gets the choice-level features AND Aura of Devotion (7) —
    # the optimizer's "missing 7 levels of subclass features" is closed.
    pid = server.create_character(
        cid, "Zevlor", kind="player", class_name="Paladin", level=10, subclass="Oath of Devotion",
        abilities={"strength": 16, "charisma": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    sheet = server.get_character(cid, pid)
    assert sheet["classes"][0]["subclass"] == "Oath of Devotion"
    assert "Sacred Weapon" in sheet["features"]
    assert "Oath of Devotion Spells" in sheet["features"]
    assert "Aura of Devotion" in sheet["features"], "an L10 oath Paladin must have Aura of Devotion (7)"
    # a level-15/20 oath feature is NOT granted early (no fabrication)
    assert "Smite of Protection" not in sheet["features"]
    assert "Holy Nimbus" not in sheet["features"]


def test_create_paladin_loose_oath_name_normalizes_and_populates(cid):
    # The DM/record may name the oath loosely ("Devotion") — it normalizes to the canonical
    # SRD name and still grants the through-features.
    pid = server.create_character(
        cid, "Karlach", kind="player", class_name="Paladin", level=10, subclass="Devotion",
        abilities={"charisma": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    sheet = server.get_character(cid, pid)
    assert sheet["classes"][0]["subclass"] == "Oath of Devotion"
    assert "Aura of Devotion" in sheet["features"]


def test_level_up_late_oath_choice_populates_through_features(cid):
    # The "OR" branch the task names: an L10 Paladin with NO oath (overdue) who finally
    # CHOOSES Oath of Devotion at level-up gets the oath features THROUGH the levels —
    # Sacred Weapon + Oath Spells AND the already-earned Aura of Devotion (7), not just
    # the level-3 pair.
    pid = server.create_character(
        cid, "Wyll", kind="player", class_name="Paladin", level=10,
        abilities={"charisma": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    assert "Aura of Devotion" not in server.get_character(cid, pid)["features"]
    out = server.level_up(cid, pid, "Paladin", subclass="Oath of Devotion")  # -> level 11
    gained = {f["name"] for f in out["_features_gained"]}
    assert {"Sacred Weapon", "Oath of Devotion Spells", "Aura of Devotion"} <= gained
    sheet = server.get_character(cid, pid)
    assert {"Sacred Weapon", "Oath of Devotion Spells", "Aura of Devotion"} <= set(sheet["features"])


def test_update_character_setting_oath_late_populates_through_features(cid):
    # Setting the oath via update_character on an L10 Paladin (the sheet edit path) re-derives
    # the class defaults and grants the owed oath features through the levels.
    pid = server.create_character(
        cid, "Minsc", kind="player", class_name="Paladin", level=10,
        abilities={"charisma": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    server.update_character(cid, pid, patch={"subclass": "Oath of Devotion"})
    sheet = server.get_character(cid, pid)
    assert sheet["classes"][0]["subclass"] == "Oath of Devotion"
    assert "Aura of Devotion" in sheet["features"]


def test_champion_through_features_unchanged_additive(cid):
    # Additivity guard: a Fighter created at L10 with Champion now also receives its owed
    # higher features (Additional Fighting Style 7, Heroic Warrior 10) — the same
    # through-the-levels grant, proving the change generalizes and isn't Paladin-special.
    fid = server.create_character(
        cid, "Lae'zel", kind="player", class_name="Fighter", level=10, subclass="Champion",
        abilities={"strength": 16, "constitution": 14}, apply_srd_defaults=True,
    )["id"]
    sheet = server.get_character(cid, fid)
    assert "Improved Critical" in sheet["features"]
    assert "Additional Fighting Style" in sheet["features"]
    assert "Heroic Warrior" in sheet["features"]
    # Superior Critical (15) / Survivor (18) are NOT owed at 10 (no fabrication)
    assert "Superior Critical" not in sheet["features"]
    assert "Survivor" not in sheet["features"]
