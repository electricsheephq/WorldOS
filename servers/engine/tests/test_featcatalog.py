"""Feat catalog (pure module) + the read-only ``feats`` MCP tool.

The level-up planner's one real gap: 17 SRD feats live in data/srd/srd524/Feat.json (+
FeatBenefit.json) but no enumeration tool surfaced them, so the planner's feat choice was a
BLIND free-text box. ``featcatalog`` exposes the bundled feat list read-only (mirrors
``feature_catalog`` / ``itemcatalog``); the ``feats`` tool projects it for the viewer. Pure module —
it never authors content; a feat the dump doesn't carry simply isn't listed.
"""

import sys
from pathlib import Path

# Ensure the ENGINE dir wins on sys.path before `import server`. A viewer test sharing the same
# pytest process inserts viewer/ at sys.path[0] (viewer/server.py self-registers), which would
# otherwise shadow `import server` with the VIEWER server (no `feats` tool). Engine-only runs are
# unaffected; this just makes the combined collection deterministic.
_ENGINE_DIR = str(Path(__file__).resolve().parents[1])
if sys.path and sys.path[0] != _ENGINE_DIR:
    sys.path.insert(0, _ENGINE_DIR)
sys.modules.pop("server", None)  # drop any viewer-shadowed `server` so the engine one loads

import featcatalog  # noqa: E402
import server  # noqa: E402

assert hasattr(server, "feats"), "expected the ENGINE server (with the feats tool), not the viewer server"


# --- the pure module ------------------------------------------------------


def test_catalog_loads_the_srd_feat_set():
    # The bundled SRD 5.2 dump carries the 17 feats (Origin / General / Fighting Style / Epic Boon).
    assert featcatalog.count() == 17


def test_all_feats_carry_the_planner_fields():
    feats = featcatalog.all_feats()
    assert feats, "the catalog must list feats"
    for f in feats:
        assert f["name"], "every feat has a name"
        # name / desc / prerequisite / type are the fields the planner reads
        for field in ("name", "desc", "prerequisite", "type"):
            assert field in f


def test_alert_carries_its_full_effect_text_from_feat_benefits():
    rec = featcatalog.lookup("Alert")
    assert rec is not None
    assert rec["name"] == "Alert"
    # the FeatBenefit lines are folded into the effect text (not just the one-line intro)
    assert "Initiative" in rec["desc"]
    assert len(rec["desc"]) > 40


def test_lookup_is_case_insensitive_and_misses_are_honest():
    assert featcatalog.lookup("alert") is not None
    assert featcatalog.lookup("ALERT") is not None
    # a feat not in the SRD dump is an honest miss — never a fabricated entry
    assert featcatalog.lookup("Totally Made Up Feat") is None


def test_find_filters_by_name_prerequisite_and_desc():
    # filter by prerequisite text (the Fighting Style feats share "Fighting Style Feature")
    fs = featcatalog.find("fighting style")
    assert fs, "a 'fighting style' query must match the fighting-style feats"
    assert any(f["name"] == "Archery" for f in fs)
    # empty query returns ALL feats
    assert len(featcatalog.find("")) == 17
    # limit caps the result
    assert len(featcatalog.find("", limit=3)) == 3


def test_records_are_copies_not_cache_aliases():
    a = featcatalog.lookup("Alert")
    a["desc"] = "MUTATED"
    b = featcatalog.lookup("Alert")
    assert b["desc"] != "MUTATED", "lookup must return a copy so a caller can't corrupt the cache"


# --- the read-only `feats` MCP tool ---------------------------------------


def test_feats_tool_lists_all_feats_with_effect_text():
    out = server.feats()
    assert out["count"] == 17
    names = {f["name"] for f in out["feats"]}
    assert {"Alert", "Magic Initiate", "Grappler"} <= names
    alert = next(f for f in out["feats"] if f["name"] == "Alert")
    assert alert["desc"], "each feat carries its effect text for the planner"
    assert "prerequisite" in alert and "type" in alert


def test_feats_tool_query_filters():
    out = server.feats("grappler")
    assert out["count"] >= 1
    assert all("grappler" in (f["name"] + f["prerequisite"] + f["desc"]).lower() for f in out["feats"])
    # the prerequisite rides through so the planner can show it
    grappler = next(f for f in out["feats"] if f["name"] == "Grappler")
    assert "13" in grappler["prerequisite"]
