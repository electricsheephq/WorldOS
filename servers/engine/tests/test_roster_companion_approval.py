"""The four ROSTERED Baldur's Gate origins move approval on tagged decisions (sprint5b).

PR #940 made `record_decision(approval_tags=[...])` move a party companion's approval gauge
when a tag matches that companion's `dossier.approval_likes` / `approval_dislikes`. It authored
the matchable lowercase_snake vocabulary into the seven canon character JSONs
(`content/worlds/baldurs-gate/characters/{shadowheart,astarion,karlach,wyll,...}.json`).

BUT the four origins that are actually played most — Shadowheart, Astarion, Karlach, and Wyll
Ravengard — seed into a Baldur's Gate campaign from `world.json`'s `npc_roster`, NOT from the
canon character JSON. `recruit_companion` already flips those roster records kind=npc->companion
and adds them to the party — but it PRESERVES the seeded roster dossier (and synthesizes only a
camp-prompt-only dossier when none was seeded). The roster dossiers carried either STALE PROSE
approval values (Astarion: "defying tyrants"; Karlach: "honest passion") or NONE at all
(Shadowheart, Wyll) — so a snake_case `approval_tags=["mercy"]` matched NOTHING and the whole
feature was INERT for the four most-played companions.

sprint5b syncs each rostered origin's `world.json` dossier approval keys to the same
lowercase_snake vocabulary the canon JSONs use. These tests guard the sync two ways:

  * INTEGRATION (the live path): seed the baldurs-gate world, recruit a rostered origin on its
    seeded `npc-*` id (it flips to a party companion), record a decision tagged with a cause that
    origin LIKES, and assert the gauge moved by +10 and an `approval_results` row was returned.
    This goes RED on the OLD prose/None roster dossiers (the revert-check): with the prior values
    the snake tag matches nothing, so attitude stays 0 and `approval_results` is absent.

  * CONTENT VALIDATION: each rostered origin's `world.json` approval_likes are lowercase_snake
    keys (no spaces, no uppercase) and intersect the SHARED vocabulary the canon character JSONs
    authored — so the roster keys are matchable AND aligned with the rest of the BG soul. This
    guards against a regression back to un-matchable prose or a typo'd key.

ADDITIVE / content-only: no engine change — recruit_companion already promotes kind and the
approval machinery is unchanged. This is purely the roster dossier vocabulary catching up to the
canon character JSONs PR #940 authored.
"""

import pytest

import content
import server


# The four origins that seed from world.json's npc_roster (not the canon character JSON), with
# the rostered `npc-*` id recruit_companion takes, a class to build, and a cause each LIKES.
_ROSTER_ORIGINS = [
    # (npc_id, display_name, class_name, like_tag, abilities)
    ("npc-shadowheart", "Shadowheart", "Cleric", "mercy",
     {"strength": 10, "dexterity": 12, "constitution": 12, "intelligence": 10, "wisdom": 15, "charisma": 10}),
    ("npc-astarion", "Astarion", "Rogue", "freedom",
     {"strength": 10, "dexterity": 16, "constitution": 12, "intelligence": 13, "wisdom": 12, "charisma": 14}),
    ("npc-karlach", "Karlach", "Barbarian", "defiance",
     {"strength": 16, "dexterity": 14, "constitution": 15, "intelligence": 8, "wisdom": 10, "charisma": 12}),
    ("npc-wyll", "Wyll Ravengard", "Warlock", "heroism",
     {"strength": 10, "dexterity": 13, "constitution": 14, "intelligence": 11, "wisdom": 12, "charisma": 16}),
]

_APPROVAL_DELTA = 10  # _APPROVAL_DEFAULT_DELTA (a like applies +10)


def _bg_world() -> dict:
    """The committed baldurs-gate world.json (the seed source for the rostered origins)."""
    return content.load_world_data("baldurs-gate")


def _shared_vocabulary() -> set[str]:
    """The lowercase_snake approval vocabulary the seven canon BG character JSONs authored —
    the matchable keyset every part of the BG 'soul' draws on. Build it from the canon JSONs
    (PR #940's source of truth) via the same loader the engine uses for canon records, not a
    hand-copied constant, so the test tracks the content."""
    vocab: set[str] = set()
    for name in ["Shadowheart", "Astarion", "Karlach", "Wyll", "Gale", "Halsin", "Lae'zel"]:
        rec = content.load_canon_character("baldurs-gate", name)
        if rec is None:
            continue
        d = content._coerce_dossier(rec.get("companion_dossier"), where="vocab")
        if d is None:
            continue
        vocab.update(d.approval_likes)
        vocab.update(d.approval_dislikes)
    assert vocab, "expected the canon BG character JSONs to author a shared approval vocabulary"
    return vocab


def _roster_entry(world: dict, npc_id: str) -> dict:
    entry = next((e for e in world["npc_roster"] if e.get("id") == npc_id), None)
    assert entry is not None, f"{npc_id} missing from baldurs-gate npc_roster"
    return entry


# --- CONTENT VALIDATION: the roster dossier keys are matchable + aligned -----------------------

@pytest.mark.parametrize("npc_id, name, _cls, _tag, _ab", _ROSTER_ORIGINS)
def test_roster_origin_approval_keys_are_lowercase_snake(npc_id, name, _cls, _tag, _ab):
    """Each rostered origin's world.json approval keys are lowercase_snake (matchable by
    approval_tags), NOT prose phrases (which could never match a tag). This is the content
    guard against a regression back to the un-matchable prose the sync replaced."""
    entry = _roster_entry(_bg_world(), npc_id)
    dossier = entry.get("dossier", entry.get("companion_dossier"))
    assert isinstance(dossier, dict), f"{name} roster entry has no dossier block"
    likes = dossier.get("approval_likes") or []
    dislikes = dossier.get("approval_dislikes") or []
    assert likes, f"{name} roster dossier has no approval_likes"
    assert dislikes, f"{name} roster dossier has no approval_dislikes"
    for key in likes + dislikes:
        assert key == key.lower(), f"{name}: {key!r} is not lowercase"
        assert " " not in key, f"{name}: {key!r} is a prose phrase, not a snake_case key"


@pytest.mark.parametrize("npc_id, name, _cls, _tag, _ab", _ROSTER_ORIGINS)
def test_roster_origin_likes_intersect_shared_vocabulary(npc_id, name, _cls, _tag, _ab):
    """Each rostered origin's approval_likes intersect the SHARED vocabulary the canon BG
    character JSONs authored — so a single tagged decision can ripple across the party (the BG
    soul), and the roster keys aren't a private, un-cross-cutting dialect."""
    vocab = _shared_vocabulary()
    entry = _roster_entry(_bg_world(), npc_id)
    dossier = entry.get("dossier", entry.get("companion_dossier"))
    likes = set(dossier.get("approval_likes") or [])
    assert likes & vocab, (
        f"{name}: approval_likes {sorted(likes)} don't intersect the shared vocabulary "
        f"{sorted(vocab)} — a tag for this origin would never ripple across the party"
    )


def test_the_like_tag_each_test_uses_is_actually_a_roster_like():
    """Sanity: the tag the integration test fires for each origin really IS in that origin's
    world.json approval_likes — so a RED integration test means the sync regressed, not that the
    test picked a tag the content never claimed."""
    world = _bg_world()
    for npc_id, name, _cls, tag, _ab in _ROSTER_ORIGINS:
        dossier = _roster_entry(world, npc_id).get("dossier", {})
        assert tag in (dossier.get("approval_likes") or []), (
            f"{name}: integration test fires {tag!r} but the roster dossier doesn't like it"
        )


# --- INTEGRATION: the live seed -> recruit -> tagged-decision path ------------------------------

@pytest.mark.parametrize("npc_id, name, cls, tag, abilities", _ROSTER_ORIGINS)
def test_rostered_origin_approval_moves_on_a_tagged_decision(
    npc_id, name, cls, tag, abilities, tmp_path, monkeypatch
):
    """The whole point: seed baldurs-gate, recruit a rostered origin on its seeded npc id (it
    flips kind=npc->companion and joins the party), then a decision tagged with a cause that
    origin LIKES moves their approval by +10 and reports an approval_results row.

    REVERT-CHECK: on the OLD roster dossiers this goes RED — Shadowheart/Wyll had NO dossier
    (recruit synthesizes one with EMPTY approval lists) and Astarion/Karlach had PROSE values
    ("defying tyrants", "honest passion") that a snake tag never matches. Either way attitude
    stays 0 and approval_results is absent. This test only passes because the roster dossier
    approval keys were synced to the matchable snake vocabulary.
    """
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]

    # The origin seeds as a plain roster NPC; recruit_companion promotes it in place.
    pre = server.get_character(bg, npc_id)
    assert pre["kind"] == "npc", f"{name} should seed as an npc before recruiting"

    rec = server.recruit_companion(
        bg, npc_id=npc_id, class_name=cls, level=1, abilities=abilities
    )
    assert rec["kind"] == "companion"
    assert npc_id in rec["party"]
    assert rec["dossier_seeded"] is True

    before = server.get_character(bg, npc_id)["attitude_value"]
    out = server.record_decision(bg, summary=f"a choice of {tag}", approval_tags=[tag])

    # The engine moved the gauge and reported the row.
    assert "approval_results" in out, (
        f"{name}: a {tag!r} decision returned no approval_results — the roster dossier's "
        f"approval keys don't match the snake tag (sync regressed)"
    )
    row = next((r for r in out["approval_results"] if r["id"] == npc_id), None)
    assert row is not None, f"{name}: no approval_results row for {npc_id}"
    assert row["matched_keys"] == [tag]
    assert row["old_value"] == before
    assert row["delta"] == _APPROVAL_DELTA
    assert row["new_value"] == before + _APPROVAL_DELTA

    after = server.get_character(bg, npc_id)["attitude_value"]
    assert after == before + _APPROVAL_DELTA, (
        f"{name}: attitude {before} -> {after}, expected +{_APPROVAL_DELTA}"
    )


def test_an_unmatched_tag_still_leaves_a_rostered_origin_unmoved(tmp_path, monkeypatch):
    """Negative control: a tag a rostered origin does NOT like/dislike moves nothing — proving
    the +10 in the positive test came from the matched cause, not from any decision moving any
    companion. (Astarion neither likes nor dislikes 'knowledge'.)"""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    server.recruit_companion(
        bg, npc_id="npc-astarion", class_name="Rogue", level=1,
        abilities={"strength": 10, "dexterity": 16, "constitution": 12,
                   "intelligence": 13, "wisdom": 12, "charisma": 14},
    )
    out = server.record_decision(bg, summary="a scholarly aside", approval_tags=["knowledge"])
    assert "approval_results" not in out
    assert server.get_character(bg, "npc-astarion")["attitude_value"] == 0
