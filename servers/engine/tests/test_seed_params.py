"""World-Seed write-lane tests (#266) — the set_seed_param mutability matrix.

set_seed_param is the engine's SOLE writer for the OpenWorlds Seed screen. These tests
pin its contract:
  - additive: an old snapshot lacking `seed_params` loads with defaults and round-trips;
  - FREE params (tone/narration/gm_strictness/chronicle_voice/anachronism/chronicler_notes)
    are always settable, before AND after a session has started;
  - GATED params (difficulty/permadeath/fate_dice/item_destruction) apply freely
    PRE-session, are REFUSED post-session without force (with a warning), and apply
    post-session WITH force (with a warning); difficulty routes to house_rules.difficulty;
  - LOCKED (system) raises;
  - an unknown param raises; a bad value raises;
  - get_state surfaces seed_params so the DM can honor it.
"""

import json

import pytest

import server
import store
from models import Campaign


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def _new_campaign(title="Seed"):
    return server.create_campaign(title)["id"]


def test_seed_params_additive_default_and_roundtrip():
    # A snapshot lacking seed_params loads with the defaults (today's behavior) and
    # round-trips byte-identically through the engine model.
    c = Campaign(title="Additive")
    raw = c.model_dump(mode="json")
    raw.pop("seed_params")
    reloaded = Campaign.model_validate(raw)
    assert reloaded.seed_params.model_dump() == c.seed_params.model_dump()
    assert reloaded.seed_params.tone == "Heroic"
    assert reloaded.seed_params.anachronism is True
    assert reloaded.seed_params.fate_dice is True


def test_get_state_surfaces_seed_params():
    cid = _new_campaign()
    state = server.get_state(cid)
    assert "seed_params" in state
    assert state["seed_params"]["tone"] == "Heroic"  # additive default surfaced


def test_free_param_always_settable_persists_and_surfaces():
    cid = _new_campaign()
    out = server.set_seed_param(cid, "tone", "Grim")
    assert out["applied"] is True
    assert out["mutability"] == "free"
    assert out["warning"] == ""
    assert out["value"] == "Grim"
    # persisted + surfaced for the DM
    assert store.load_campaign(cid).seed_params.tone == "Grim"
    assert server.get_state(cid)["seed_params"]["tone"] == "Grim"


def test_free_param_settable_even_after_session_started():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    # Free params carry NO session gate — a narration register change mid-run is fine.
    out = server.set_seed_param(cid, "narration", "terse")
    assert out["applied"] is True
    assert out["warning"] == ""
    assert store.load_campaign(cid).seed_params.narration == "terse"


def test_free_chronicler_notes_freetext():
    cid = _new_campaign()
    out = server.set_seed_param(cid, "chronicler_notes", "Trust the book.")
    assert out["applied"] is True
    assert store.load_campaign(cid).seed_params.chronicler_notes == "Trust the book."


def test_anachronism_is_free_not_gated():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    # anachronism is reclassified as FREE (language permission, no mechanical effect):
    # settable post-session with no force needed.
    out = server.set_seed_param(cid, "anachronism", False)
    assert out["applied"] is True
    assert out["mutability"] == "free"
    assert store.load_campaign(cid).seed_params.anachronism is False


def test_gated_param_pre_session_applies_silently():
    cid = _new_campaign()
    assert store.load_campaign(cid).session_ids == []  # no session yet
    out = server.set_seed_param(cid, "permadeath", True)
    assert out["applied"] is True
    assert out["mutability"] == "gated"
    assert out["warning"] == ""  # pre-session: no warning
    assert store.load_campaign(cid).seed_params.permadeath is True


def test_gated_param_post_session_refused_without_force():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    out = server.set_seed_param(cid, "fate_dice", False)
    assert out["applied"] is False
    assert out["mutability"] == "gated"
    assert out["warning"]  # explains the refusal + how to force
    assert "force=True" in out["warning"]
    # NOT written — the value is unchanged from its default
    assert store.load_campaign(cid).seed_params.fate_dice is True


def test_gated_param_post_session_applies_with_force_and_warns():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    out = server.set_seed_param(cid, "item_destruction", True, force=True)
    assert out["applied"] is True
    assert out["mutability"] == "gated"
    assert out["warning"]  # retroactive-risk warning surfaced
    assert store.load_campaign(cid).seed_params.item_destruction is True


def test_permadeath_force_warning_says_no_resurrection():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    out = server.set_seed_param(cid, "permadeath", True, force=True)
    assert out["applied"] is True
    assert "does NOT resurrect" in out["warning"]


def test_difficulty_routes_to_house_rules():
    cid = _new_campaign()
    # difficulty is gated and lives on house_rules, NOT seed_params. Pre-session it applies.
    out = server.set_seed_param(cid, "difficulty", "hard")
    assert out["applied"] is True
    assert out["mutability"] == "gated"
    loaded = store.load_campaign(cid)
    assert loaded.house_rules.difficulty == "hard"
    # set_house_rules and the seed lane read the SAME source — no duplicate field.
    assert server.get_state(cid)["seed_params"].get("difficulty") is None  # not mirrored


def test_difficulty_post_session_gated():
    cid = _new_campaign()
    c = store.load_campaign(cid)
    c.session_ids = ["session-1"]
    store.save_campaign(c)
    refused = server.set_seed_param(cid, "difficulty", "easy")
    assert refused["applied"] is False
    assert store.load_campaign(cid).house_rules.difficulty == "standard"  # untouched
    forced = server.set_seed_param(cid, "difficulty", "easy", force=True)
    assert forced["applied"] is True
    assert store.load_campaign(cid).house_rules.difficulty == "easy"


def test_system_is_locked_and_raises():
    cid = _new_campaign()
    with pytest.raises(ValueError, match="LOCKED"):
        server.set_seed_param(cid, "system", "Free Form")


def test_unknown_param_raises():
    cid = _new_campaign()
    with pytest.raises(ValueError, match="unknown seed param"):
        server.set_seed_param(cid, "bogus_param", "x")


def test_bad_value_raises():
    cid = _new_campaign()
    with pytest.raises(Exception):
        server.set_seed_param(cid, "tone", "NotATone")  # invalid Literal


def test_seed_params_persist_in_snapshot_json():
    cid = _new_campaign()
    server.set_seed_param(cid, "tone", "Mythic")
    snapshot = store._campaign_dir(cid) / "snapshot.json"
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert "seed_params" in data
    assert data["seed_params"]["tone"] == "Mythic"
