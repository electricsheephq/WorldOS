"""`_char` resolve-then-suggest (audit F14-8, issue #786).

~60 id-taking tools route every character_id through `server._char`, which used to be a bare
dict-get-and-raise: the observed gate-duo2 double failure passed 'maddala-deadeye' while the
campaign held char_… "Maddala Deadeye", and the error named neither the valid ids nor the
obvious match — a wasted ~100s beat per slip, or a silent freehand.

The resolution ladder (deterministic, read-only):
  1. exact dict-key hit (ids stay canonical);
  2. unique case-insensitive match on id / display name / slugified name (spaces -> hyphens);
  3. unique substring match on name or id;
  4. otherwise the SAME-SHAPED ValueError ("no character …"), now with a did-you-mean of
     the <=5 nearest `id (name, kind)` candidates.
Ambiguity NEVER resolves (two NPCs named "Guard" -> raise listing both); a failed call
mutates nothing.
"""

import pytest

import server


@pytest.fixture()
def camp(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Resolver")["id"]
    mid = server.create_character(cid, "Maddala Deadeye", kind="npc")["id"]
    server.create_character(cid, "Rolan", kind="player")
    return cid, mid


def test_exact_id_still_wins(camp):
    cid, mid = camp
    c = server._require(cid)
    assert server._char(c, mid).id == mid


def test_slugified_name_resolves(camp):
    # THE observed failure class: 'maddala-deadeye' for "Maddala Deadeye".
    cid, mid = camp
    c = server._require(cid)
    assert server._char(c, "maddala-deadeye").id == mid


def test_case_insensitive_name_resolves(camp):
    cid, mid = camp
    c = server._require(cid)
    assert server._char(c, "maddala deadeye").id == mid
    assert server._char(c, "MADDALA DEADEYE").id == mid


def test_unique_substring_resolves(camp):
    cid, mid = camp
    c = server._require(cid)
    assert server._char(c, "maddala").id == mid


def test_typo_raises_with_did_you_mean(camp):
    # 'madala' (missing d) matches nothing exactly — the error must keep its key shape
    # ("no character … in campaign") AND carry the nearest `id (name, kind)` candidates.
    cid, mid = camp
    c = server._require(cid)
    with pytest.raises(ValueError, match="no character") as ei:
        server._char(c, "madala")
    msg = str(ei.value)
    assert mid in msg, f"did-you-mean must name the real id: {msg}"
    assert "Maddala Deadeye" in msg
    assert "Did you mean" in msg


def test_ambiguous_name_never_resolves(camp):
    cid, _mid = camp
    g1 = server.create_character(cid, "Guard", kind="npc")["id"]
    g2 = server.create_character(cid, "Guard", kind="npc")["id"]
    c = server._require(cid)
    with pytest.raises(ValueError, match="no character") as ei:
        server._char(c, "guard")
    msg = str(ei.value)
    assert g1 in msg and g2 in msg, f"ambiguity must list BOTH candidates: {msg}"


def test_no_match_keeps_plain_error_shape(camp):
    cid, _mid = camp
    c = server._require(cid)
    with pytest.raises(ValueError, match="no character 'zzz-qqqqq' in campaign"):
        server._char(c, "zzz-qqqqq")


def test_tools_inherit_resolution_award_xp_slug(camp):
    # The ~60-tool inheritance, on the tool that DID fail in gate-duo2: award_xp resolves
    # the slug and lands the XP on the canonical record.
    cid, mid = camp
    before = server.get_character(cid, mid)["xp"]
    out = server.award_xp(cid, "maddala-deadeye", 50, "resolver test")
    assert "error" not in out
    assert server.get_character(cid, mid)["xp"] >= before + 50


def test_failed_resolution_mutates_nothing(camp):
    # The no-mutation invariant on the failure path (award_xp narrated-not-executed class).
    cid, mid = camp
    before = server.get_character(cid, mid)["xp"]
    with pytest.raises(ValueError, match="no character"):
        server.award_xp(cid, "madala", 50, "must not land")
    assert server.get_character(cid, mid)["xp"] == before


def test_get_character_routes_through_resolver(camp):
    # get_character had its own inline dict-get raise — same shape, now the same ONE site.
    cid, mid = camp
    assert server.get_character(cid, "maddala-deadeye")["id"] == mid
