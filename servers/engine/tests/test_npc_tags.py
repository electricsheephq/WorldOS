"""Structured NPC tagging — the DM's "pull exactly the right canon character" surface.

The ~2,076 canon character JSONs were 99% empty for categorical fields, so the engine couldn't
filter them structurally. This adds ADDITIVE Character fields (tags / faction_id / is_merchant /
canon_location_id / arc_role / ending_role / quest_ties), a read-only `find_npcs` query tool, a
derivation that populates a high-confidence slice, and an ending_role projection. These tests
guard:

  * the new fields are ADDITIVE — an old snapshot (and a Character built with no tagging) loads
    with empty/False defaults and round-trips unchanged (back-compat);
  * strict validation still holds (a typo'd field is rejected);
  * `find_npcs` filters the canon roster correctly — by tag / faction / is_merchant /
    arc_role / name_contains — and AND-combines them;
  * the derivation populated the shipped BG canon (Talli is a merchant Harper; the 7 origin
    heroes are arc_role="origin-hero" + tag "companion");
  * `_apply_ending_overlay` projects a fate's `status` onto the matching Character's
    `ending_role` (survived/died/ambiguous), and `ending_role_from_status` maps prose correctly;
  * `load_canon_character` carries the canon record's tags onto the live Character.
"""

import pytest
from pydantic import ValidationError

import content
import server
from models import Campaign, Character


# --- additive default + back-compat ----------------------------------------

def test_tagging_fields_default_empty():
    """A fresh Character carries empty/False tagging fields == today's behavior."""
    ch = Character(name="Nobody")
    assert ch.tags == []
    assert ch.faction_id == ""
    assert ch.is_merchant is False
    assert ch.canon_location_id == ""
    assert ch.arc_role == ""
    assert ch.ending_role == ""
    assert ch.quest_ties == []


def test_old_snapshot_without_tagging_fields_deserializes_unchanged():
    """A snapshot predating these fields must load with empty defaults and round-trip — the
    additive-default contract that keeps every existing campaign loadable."""
    ch = Character(name="Hero", kind="npc", attitude_value=5)
    data = ch.model_dump(mode="json")
    old = {
        k: v
        for k, v in data.items()
        if k not in {"tags", "faction_id", "is_merchant", "canon_location_id", "arc_role", "ending_role", "quest_ties"}
    }
    reloaded = Character.model_validate(old)
    assert reloaded.tags == [] and reloaded.faction_id == "" and reloaded.is_merchant is False
    assert reloaded.arc_role == "" and reloaded.ending_role == "" and reloaded.quest_ties == []
    # full round-trip stays stable
    assert Character.model_validate(reloaded.model_dump(mode="json")).faction_id == ""


def test_old_campaign_snapshot_with_untagged_characters_loads():
    """A whole Campaign whose characters carry no tagging fields deserializes unchanged."""
    c = Campaign(title="Pre-tagging")
    ch = Character(name="Ally", kind="npc")
    c.characters[ch.id] = ch
    raw = c.model_dump(mode="json")
    for cd in raw["characters"].values():
        for f in ("tags", "faction_id", "is_merchant", "canon_location_id", "arc_role", "ending_role", "quest_ties"):
            cd.pop(f, None)
    reloaded = Campaign.model_validate(raw)
    assert reloaded.characters[ch.id].tags == []
    assert reloaded.characters[ch.id].is_merchant is False


def test_tagging_fields_round_trip_when_set():
    ch = Character(
        name="Quartermaster",
        kind="npc",
        tags=["merchant", "harper"],
        faction_id="harpers",
        is_merchant=True,
        canon_location_id="last-light-inn",
        arc_role="minor",
        ending_role="survived",
        quest_ties=["save-the-inn"],
    )
    again = Character.model_validate(ch.model_dump(mode="json"))
    assert again.tags == ["merchant", "harper"]
    assert again.faction_id == "harpers"
    assert again.is_merchant is True
    assert again.canon_location_id == "last-light-inn"
    assert again.arc_role == "minor"
    assert again.ending_role == "survived"
    assert again.quest_ties == ["save-the-inn"]


def test_strict_validation_still_rejects_typos():
    """extra='forbid' is intact — a typo'd tagging field is rejected, not silently dropped."""
    with pytest.raises(ValidationError):
        Character.model_validate({"name": "X", "facton_id": "harpers"})  # typo


# --- find_canon_characters (content-level structural filter) ----------------

WORLD = "baldurs-gate"


def test_find_by_tag_merchant_returns_merchants():
    res = content.find_canon_characters(WORLD, tag="merchant", limit=500)
    assert res, "expected derived merchants in the shipped canon"
    # every match actually carries the tag + the boolean
    assert all("merchant" in [t.lower() for t in r["tags"]] for r in res)
    assert all(r["is_merchant"] for r in res)
    # Talli (the Last Light Inn Harper quartermaster) is among them
    assert any(r["name"] == "Talli" for r in res)


def test_find_by_is_merchant_true_matches_tag_slice():
    by_bool = content.find_canon_characters(WORLD, is_merchant=True, limit=500)
    by_tag = content.find_canon_characters(WORLD, tag="merchant", limit=500)
    assert {r["name"] for r in by_bool} == {r["name"] for r in by_tag}
    assert len(by_bool) >= 50  # the derivation found ~61


def test_find_by_faction_harpers():
    res = content.find_canon_characters(WORLD, faction_id="harpers", limit=500)
    assert res
    assert all(r["faction_id"] == "harpers" for r in res)
    names = {r["name"] for r in res}
    assert "Jaheira" in names  # a known Harper in the corpus


def test_find_by_arc_role_origin_hero_returns_seven():
    res = content.find_canon_characters(WORLD, arc_role="origin-hero", limit=50)
    names = {r["name"] for r in res}
    # the 7 BG3 origin companions
    for hero in ("Astarion", "Gale", "Karlach", "Shadowheart", "Wyll", "Halsin"):
        assert hero in names, f"{hero} should be tagged origin-hero"
    assert len(res) == 7
    assert all("companion" in [t.lower() for t in r["tags"]] for r in res)


def test_filters_and_combine():
    """Filters AND-combine: a merchant in the harpers faction is the intersection."""
    merch_harpers = content.find_canon_characters(WORLD, tag="merchant", faction_id="harpers", limit=500)
    # Talli is both a merchant AND a Harper -> in the intersection
    assert any(r["name"] == "Talli" for r in merch_harpers)
    assert all(r["is_merchant"] and r["faction_id"] == "harpers" for r in merch_harpers)
    # the intersection is no larger than either side
    just_merch = content.find_canon_characters(WORLD, tag="merchant", limit=500)
    assert len(merch_harpers) <= len(just_merch)


def test_find_by_name_contains_is_case_insensitive():
    res = content.find_canon_characters(WORLD, name_contains="jahei", limit=10)
    assert any(r["name"] == "Jaheira" for r in res)


def test_find_no_filters_lists_everyone_bounded_by_limit():
    res = content.find_canon_characters(WORLD, limit=5)
    assert len(res) == 5  # limit is honored


def test_find_empty_filter_is_ignored_not_match_empty():
    """An empty string filter must be IGNORED (list all), not interpreted as 'faction_id == \"\"'."""
    a = content.find_canon_characters(WORLD, faction_id="", limit=3)
    b = content.find_canon_characters(WORLD, limit=3)
    assert [r["name"] for r in a] == [r["name"] for r in b]


# --- find_npcs MCP tool (server surface) ------------------------------------

def test_find_npcs_tool_filters_and_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    w = content.load_world_data(WORLD)
    c = content.seed_world(w)
    server.save_campaign(c)
    out = server.find_npcs(c.id, tag="merchant", limit=500)
    assert out["world_id"] == WORLD
    assert out["count"] == len(out["matches"]) >= 1
    assert all("merchant" in [t.lower() for t in m["tags"]] for m in out["matches"])
    # is_merchant=False default must NOT exclude merchants (it means "unset")
    unfiltered = server.find_npcs(c.id, limit=5)
    assert unfiltered["count"] == 5


def test_find_npcs_tool_is_merchant_true_narrows(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    w = content.load_world_data(WORLD)
    c = content.seed_world(w)
    server.save_campaign(c)
    only_merch = server.find_npcs(c.id, is_merchant=True, limit=500)
    assert only_merch["count"] >= 1
    assert all(m["is_merchant"] for m in only_merch["matches"])


# --- ending_role projection -------------------------------------------------

def test_ending_role_from_status_mapping():
    f = content.ending_role_from_status
    assert f("dead") == "died"
    assert f("slain at the bridge") == "died"
    assert f("alive") == "survived"
    assert f("alive — and still himself, not ascended") == "survived"
    assert f("departed into its own designs") == "survived"
    assert f("fate unknown") == "ambiguous"
    assert f("vanished, presumed lost") == "ambiguous"
    assert f("") == ""
    # an unreadable status stays unclassified rather than guessing
    assert f("inscrutable") == ""
    # "undead" must NOT trip the bare-word death cue
    assert f("now an undead servant, very much alive in its way") == "survived"


def test_apply_ending_overlay_sets_ending_role_on_roster_npc():
    """The shipped 'heroes live' ending gives roster NPCs alive-status fates; the overlay must
    project those onto `ending_role='survived'`, while the base (no-ending) world leaves it ''."""
    w = content.load_world_data(WORLD)
    base = content.seed_world(w)
    jah_base = next(ch for ch in base.characters.values() if ch.name == "Jaheira")
    assert jah_base.ending_role == ""  # no ending -> untouched (today's behavior)

    e = content.seed_world(w, ending="netherbrain-destroyed-heroes-live")
    jah = next(ch for ch in e.characters.values() if ch.name == "Jaheira")
    assert jah.ending_role == "survived"  # fate status "alive" -> survived
    # find_npcs-style structural filter now works on the live post-state roster
    survivors = [ch.name for ch in e.characters.values() if ch.ending_role == "survived"]
    assert "Jaheira" in survivors


def test_apply_ending_overlay_projects_death_status(tmp_path, monkeypatch):
    """A synthetic overlay with a 'dead' fate must set ending_role='died' on the matching
    roster NPC — exercising the death branch without depending on a specific shipped ending."""
    w = content.load_world_data(WORLD)
    overlay = {
        "id": "synthetic-test-ending",
        "name": "Synthetic Test Ending",
        "era": "test era",
        "fates": {
            "npc-jaheira": {"status": "dead", "where": "the bridge", "note": "fell holding the line"},
            "npc-astarion": {"status": "fate unknown", "where": "the Underdark"},
        },
    }
    c = content.seed_world(w)
    content._apply_ending_overlay(c, overlay)
    jah = next(ch for ch in c.characters.values() if ch.name == "Jaheira")
    astarion = next(ch for ch in c.characters.values() if ch.name == "Astarion")
    assert jah.ending_role == "died"
    assert astarion.ending_role == "ambiguous"


# --- load_canon_character carries the tags onto the live Character ----------

def test_load_canon_character_carries_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    w = content.load_world_data(WORLD)
    c = content.seed_world(w)
    server.save_campaign(c)
    # Talli is a derived merchant Harper — pulling her in must preserve that structure.
    res = server.load_canon_character(c.id, "Talli", kind="npc")
    assert "error" not in res
    ch = c.characters.get(res["id"]) or next(
        x for x in server._require(c.id).characters.values() if x.name == "Talli"
    )
    fresh = server._require(c.id)
    talli = next(x for x in fresh.characters.values() if x.name == "Talli")
    assert talli.is_merchant is True
    assert "merchant" in talli.tags
    assert talli.faction_id == "harpers"
