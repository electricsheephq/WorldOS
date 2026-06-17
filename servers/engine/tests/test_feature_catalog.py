"""Feature catalog + lookup_feature / feature_catalog endpoint (#756-family inspector,
from the RRI-25e55fa optimizer sweep).

The optimizer's #1 min-maxer pain point: every class feature (Extra Attack, Action
Surge, Indomitable…) is static text with NO click-through to the full rules text.
The full SRD 5.2 rules text lives in data/srd/srd524/ClassFeature.json; this exposes
it read-only (mirrors the #872 /item-catalog pattern) so the viewer can show the
complete rules on click. Pure module — never authors content; an unknown feature is an
honest miss, never a fabrication.
"""

import pytest

import feature_catalog
import server


# --- the pure module ------------------------------------------------------


def test_catalog_loads_feature_set():
    # The SRD dump carries hundreds of distinct (owner, feature) rules entries.
    assert feature_catalog.count() > 200


def test_lookup_class_feature_full_text():
    rec = feature_catalog.lookup("fighter", "Action Surge")
    assert rec is not None
    assert rec["name"] == "Action Surge"
    assert rec["owner"] == "fighter"
    # the FULL multi-sentence rules text, not the curated one-liner
    assert "one additional action" in rec["desc"].lower()
    assert len(rec["desc"]) > 80


def test_lookup_is_case_insensitive():
    assert feature_catalog.lookup("Fighter", "action surge") is not None
    assert feature_catalog.lookup("fighter", "EXTRA ATTACK") is not None


def test_lookup_subclass_feature_falls_back_to_parent_class():
    # A Champion has its archetype features AND its base-class features (Action Surge
    # lives on fighter); a lookup on the subclass resolves the parent's feature.
    rec = feature_catalog.lookup("Champion", "Action Surge")
    assert rec is not None and rec["owner"] == "fighter"
    # …and its own archetype feature resolves on the subclass directly.
    own = feature_catalog.lookup("Champion", "Improved Critical")
    assert own is not None and own["owner"] == "champion"


def test_features_for_lists_class_features_with_text():
    feats = feature_catalog.features_for("fighter")
    names = {f["name"] for f in feats}
    assert {"Second Wind", "Action Surge", "Extra Attack", "Indomitable"} <= names
    assert all(f.get("desc") for f in feats)


def test_lookup_unknown_feature_is_honest_none():
    assert feature_catalog.lookup("fighter", "Totally Made Up Feature") is None
    assert feature_catalog.lookup("not-a-class", "Action Surge") is None


def test_lookup_any_prefers_class_hint_for_shared_name():
    # "Spellcasting" is shared across casters with distinct text; the class hint resolves
    # the right one. (Wizard casts with Intelligence; bard with Charisma.)
    wiz = feature_catalog.lookup_any("Spellcasting", class_hints=("wizard",))
    assert wiz is not None and wiz["owner"] == "wizard"
    assert "intelligence" in wiz["desc"].lower()


# --- the MCP endpoint -----------------------------------------------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("Features")["id"]


def test_lookup_feature_tool_returns_rules_text():
    out = server.lookup_feature("Action Surge", class_name="Fighter")
    assert out["name"] == "Action Surge"
    assert "additional action" in out["desc"].lower()


def test_lookup_feature_tool_miss_is_honest():
    out = server.lookup_feature("Made Up Feature", class_name="Fighter")
    assert "error" in out


def test_feature_catalog_tool_lists_a_class_full_text():
    out = server.feature_catalog("Fighter")
    names = {f["name"] for f in out["features"]}
    assert {"Action Surge", "Extra Attack", "Indomitable"} <= names
    asg = next(f for f in out["features"] if f["name"] == "Action Surge")
    assert len(asg["desc"]) > 80  # full rules text


def test_feature_catalog_tool_resolves_a_subclass():
    out = server.feature_catalog("Champion")
    names = {f["name"] for f in out["features"]}
    assert "Improved Critical" in names  # the archetype's own feature with full text


def test_character_features_for_carries_rules_text_for_the_pcs_class(cid):
    # The character read path can resolve a feature's full rules text against the PC's
    # actual class/subclass — the click-through source for the sheet.
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=5, apply_srd_defaults=True
    )["id"]
    out = server.character_feature_rules(cid, fid)
    by_name = {f["name"]: f for f in out["features"]}
    assert "Extra Attack" in by_name
    assert by_name["Extra Attack"]["desc"], "the PC's feature must carry its full rules text"
