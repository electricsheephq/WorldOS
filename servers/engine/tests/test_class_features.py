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
