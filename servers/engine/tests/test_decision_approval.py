"""Companion approval moves on the player's moral choices (the BG "soul").

`CompanionDossier.approval_likes` / `approval_dislikes` were authored but NEVER read — a
companion's regard could only move on a social_check or a manual adjust_attitude. This brings
the dead read to life: `record_decision` (and the batched `persist_beat` decision leg) accept
an `approval_tags` argument; for every PARTY companion whose dossier lists a matching cause the
ENGINE moves the approval gauge (+10 like / -10 dislike, or an explicit per-tag delta), clamps
it to [-100, 100], and reports the move. The DM TAGS the cause; the engine OWNS the number
(gauge-not-fiction). Tests guard:

  * a like applies +10, a dislike -10, multiple tags accumulate;
  * an explicit {key, delta} overrides the +/-10 default;
  * empty / None approval_tags is byte-identical to today (NO move, NO approval_results key);
  * non-companion party members (and companions with no dossier) are untouched;
  * the move is clamped at +/-100;
  * the same mechanism fires through persist_beat's decision leg;
  * scene_context.durable.companions surfaces each companion's likes/dislikes at stake;
  * an approval delta can cross a locked ArcGate threshold so the arc actually turns
    (integration with companion_arc.evaluate via check_companion_arc).

ADDITIVE invariants: approval_tags is keyword-only + defaulted; an old snapshot with no
`approval_tags` on a stored Decision round-trips unchanged; the engine never infers the tag
from prose — the DM supplies it and the delta is fixed/explicit.
"""

import pytest

import content
import server
import store
from models import Campaign, Decision


# --- helpers ----------------------------------------------------------------

def _new_campaign(monkeypatch, tmp_path, title="Approval"):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign(title)["id"]


def _add_companion(cid, name, *, likes=None, dislikes=None, attitude=0):
    """Create a party companion with a dossier whose approval causes are the given keys."""
    res = server.create_character(cid, name, kind="companion", class_name="Fighter")
    comp_id = res["id"]
    server.update_character(cid, comp_id, {
        "attitude_value": attitude,
        "companion_dossier": {
            "approval_likes": list(likes or []),
            "approval_dislikes": list(dislikes or []),
        },
    })
    return comp_id


def _attitude(cid, comp_id):
    return server.get_character(cid, comp_id)["attitude_value"]


# --- the model is additive --------------------------------------------------

def test_decision_model_has_approval_tags_default_empty():
    d = Decision(summary="spared the goblin")
    assert d.approval_tags == []


def test_old_decision_snapshot_without_approval_tags_round_trips():
    """A stored Decision predating approval_tags loads unchanged (additive default)."""
    d = Decision(summary="took the bribe", chosen="yes")
    raw = d.model_dump(mode="json")
    old = {k: v for k, v in raw.items() if k != "approval_tags"}
    assert "approval_tags" not in old
    reloaded = Decision.model_validate(old)
    assert reloaded.approval_tags == []
    assert reloaded.summary == "took the bribe"


def test_old_campaign_snapshot_with_tagless_decisions_loads():
    c = Campaign(title="Pre-approval")
    d = Decision(summary="freed the prisoner")
    c.decisions.append(d)
    raw = c.model_dump(mode="json")
    for dd in raw["decisions"]:
        dd.pop("approval_tags", None)
    reloaded = Campaign.model_validate(raw)
    assert reloaded.decisions[0].approval_tags == []


# --- record_decision: the core mechanic -------------------------------------

def test_like_applies_plus_ten(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"], dislikes=["cruelty"])
    out = server.record_decision(cid, summary="spared the wounded foe", approval_tags=["mercy"])
    assert _attitude(cid, comp) == 10
    rows = out["approval_results"]
    row = next(r for r in rows if r["id"] == comp)
    assert row["old_value"] == 0 and row["new_value"] == 10 and row["delta"] == 10
    assert row["matched_keys"] == ["mercy"]


def test_dislike_applies_minus_ten(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"], dislikes=["cruelty"])
    out = server.record_decision(cid, summary="tortured the captive", approval_tags=["cruelty"])
    assert _attitude(cid, comp) == -10
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == -10 and row["new_value"] == -10
    assert row["matched_keys"] == ["cruelty"]


def test_multiple_tags_accumulate(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Wyll", likes=["heroism", "mercy"], dislikes=["cruelty"])
    # two likes + one dislike => +10 +10 -10 = +10
    out = server.record_decision(
        cid, summary="charged in to save the villagers but left the bandit to die",
        approval_tags=["heroism", "mercy", "cruelty"],
    )
    assert _attitude(cid, comp) == 10
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == 10
    assert set(row["matched_keys"]) == {"heroism", "mercy", "cruelty"}


def test_explicit_delta_overrides_default(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Astarion", likes=["power"], dislikes=["weakness"])
    out = server.record_decision(
        cid, summary="seized the crown of tyranny",
        approval_tags=[{"key": "power", "delta": 25}],
    )
    assert _attitude(cid, comp) == 25
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == 25


def test_explicit_delta_on_a_dislike_is_used_verbatim(tmp_path, monkeypatch):
    """An explicit delta is applied with its given sign — the like/dislike list still gates
    WHETHER the tag matches, but the explicit delta is authoritative (not re-signed)."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Karlach", likes=["kindness"], dislikes=["cruelty"])
    out = server.record_decision(
        cid, summary="a cold, calculated cruelty",
        approval_tags=[{"key": "cruelty", "delta": -30}],
    )
    assert _attitude(cid, comp) == -30
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == -30


# --- additive: empty / None is byte-identical to today ----------------------

def test_none_approval_tags_is_byte_identical(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Gale", likes=["knowledge"], dislikes=["waste"])
    before = _attitude(cid, comp)
    out = server.record_decision(cid, summary="a quiet choice")
    assert _attitude(cid, comp) == before  # NO move
    assert "approval_results" not in out  # return shape unchanged
    assert set(out) == {"id", "summary", "chosen", "day"}


def test_empty_list_approval_tags_is_byte_identical(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Gale", likes=["knowledge"], dislikes=["waste"])
    out = server.record_decision(cid, summary="a quiet choice", approval_tags=[])
    assert _attitude(cid, comp) == 0
    assert "approval_results" not in out


def test_unmatched_tag_yields_no_results_and_no_move(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Gale", likes=["knowledge"], dislikes=["waste"])
    out = server.record_decision(cid, summary="ate a sandwich", approval_tags=["sandwich"])
    assert _attitude(cid, comp) == 0
    assert "approval_results" not in out  # no companion matched => key absent


def test_sets_flag_still_works_alongside_approval_tags(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"])
    out = server.record_decision(
        cid, summary="spared them", approval_tags=["mercy"], sets_flag="spared_goblin",
    )
    assert out["flag"] == "spared_goblin"
    assert out["approval_results"][0]["delta"] == 10


# --- non-companions / dossier-less party members are untouched --------------

def test_non_companion_party_member_unaffected(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    # the PC is in the party but is not a companion and has no dossier
    pc = server.create_character(cid, "Tav", kind="player", class_name="Rogue")["id"]
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"])
    out = server.record_decision(cid, summary="spared them", approval_tags=["mercy"])
    ids = {r["id"] for r in out["approval_results"]}
    assert pc not in ids and comp in ids
    assert _attitude(cid, pc) == 0


def test_companion_without_dossier_is_skipped(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    res = server.create_character(cid, "Jaheira", kind="companion", class_name="Druid")
    bare = res["id"]
    # ensure no dossier was synthesized by create_character
    server.update_character(cid, bare, {"companion_dossier": None, "attitude_value": 0})
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"])
    out = server.record_decision(cid, summary="spared them", approval_tags=["mercy"])
    ids = {r["id"] for r in out["approval_results"]}
    assert bare not in ids and comp in ids


def test_a_companion_not_in_party_is_skipped(tmp_path, monkeypatch):
    """Only c.party companions move — an NPC-loaded companion not yet in the party stays put."""
    cid = _new_campaign(monkeypatch, tmp_path)
    in_party = _add_companion(cid, "Shadowheart", likes=["mercy"])
    # a companion record that exists but was never added to c.party
    res = server.create_character(cid, "Minsc", kind="companion", class_name="Ranger",
                                  add_to_party=False)
    out_of_party = res["id"]
    server.update_character(cid, out_of_party, {
        "companion_dossier": {"approval_likes": ["mercy"]}, "attitude_value": 0,
    })
    out = server.record_decision(cid, summary="spared them", approval_tags=["mercy"])
    ids = {r["id"] for r in out["approval_results"]}
    assert in_party in ids and out_of_party not in ids
    assert _attitude(cid, out_of_party) == 0


# --- clamp ------------------------------------------------------------------

def test_clamp_at_positive_hundred(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Karlach", likes=["kindness"], attitude=95)
    out = server.record_decision(
        cid, summary="a great kindness", approval_tags=[{"key": "kindness", "delta": 50}],
    )
    assert _attitude(cid, comp) == 100
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["old_value"] == 95 and row["new_value"] == 100
    assert row["delta"] == 5  # the REALIZED move after clamp


def test_clamp_at_negative_hundred(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Astarion", dislikes=["naive_altruism"], attitude=-95)
    out = server.record_decision(
        cid, summary="a foolish, bleeding-heart gesture",
        approval_tags=[{"key": "naive_altruism", "delta": -50}],
    )
    assert _attitude(cid, comp) == -100
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["new_value"] == -100 and row["delta"] == -5


# --- persistence: the move survives a reload --------------------------------

def test_move_is_persisted(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"])
    server.record_decision(cid, summary="spared them", approval_tags=["mercy"])
    # a fresh read goes through load_campaign -> the persisted snapshot
    assert server.get_character(cid, comp)["attitude_value"] == 10
    # and the decision itself stored its tags for recall (reload the on-disk snapshot)
    reloaded = store.load_campaign(cid)
    assert reloaded.characters[comp].attitude_value == 10
    assert any(d.approval_tags == ["mercy"] for d in reloaded.decisions)


# --- persist_beat threads approval_tags through the decision leg ------------

def test_persist_beat_decision_moves_approval(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Wyll", likes=["heroism"], dislikes=["cowardice"])
    out = server.persist_beat(cid, decision={
        "summary": "stood his ground against the devil",
        "approval_tags": ["heroism"],
    })
    assert _attitude(cid, comp) == 10
    assert out["approval_results"][0]["id"] == comp
    assert out["approval_results"][0]["delta"] == 10


def test_persist_beat_decision_without_tags_is_byte_identical(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Wyll", likes=["heroism"])
    out = server.persist_beat(cid, decision={"summary": "a quiet word"})
    assert _attitude(cid, comp) == 0
    assert "approval_results" not in out
    assert set(out) == {"logged", "remembered", "decision", "time"}


def test_persist_beat_explicit_delta_through_decision(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Astarion", likes=["cunning"])
    out = server.persist_beat(cid, decision={
        "summary": "a deft, ruthless con",
        "approval_tags": [{"key": "cunning", "delta": 15}],
    })
    assert _attitude(cid, comp) == 15
    assert out["approval_results"][0]["delta"] == 15


# --- scene_context surfaces the values at stake -----------------------------

def test_scene_context_surfaces_likes_and_dislikes(tmp_path, monkeypatch):
    cid = _new_campaign(monkeypatch, tmp_path)
    _add_companion(cid, "Shadowheart", likes=["mercy", "duty"], dislikes=["cruelty"])
    sc = server.scene_context(cid)
    comp_entries = sc["durable"]["companions"]
    sh = next(e for e in comp_entries if e["name"] == "Shadowheart")
    assert sh["approval_likes"] == ["mercy", "duty"]
    assert sh["approval_dislikes"] == ["cruelty"]


def test_scene_context_companion_without_causes_omits_keys(tmp_path, monkeypatch):
    """A companion whose dossier has no approval causes (or no dossier) keeps today's shape —
    the approval_likes/dislikes keys are ABSENT, not empty lists."""
    cid = _new_campaign(monkeypatch, tmp_path)
    res = server.create_character(cid, "Jaheira", kind="companion", class_name="Druid")
    server.update_character(cid, res["id"], {"companion_dossier": None})
    sc = server.scene_context(cid)
    jah = next(e for e in sc["durable"]["companions"] if e["name"] == "Jaheira")
    assert "approval_likes" not in jah and "approval_dislikes" not in jah


# --- integration: a delta crosses a gate and the arc turns ------------------

def test_approval_delta_unlocks_a_companion_arc_gate(tmp_path, monkeypatch):
    """The whole point: a moral choice moves approval, which crosses a locked ArcGate's
    threshold, and check_companion_arc -> companion_arc.evaluate fires the unlock. Without the
    decision moving the gauge the gate would stay locked forever (the revert check)."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"], attitude=15)
    # arc with a loyalty gate at 20 — one notch above the current 15, so a +10 mercy move crosses it
    server.set_companion_arc(cid, comp, arc={
        "arc_gates": [{"kind": "loyalty", "threshold": 20, "note": "she lets you in"}],
    })
    # nothing has unlocked yet (15 < 20)
    pre = server.check_companion_arc(cid, comp)
    assert pre["results"] == []
    # the moral choice moves approval to 25 — now above the gate
    server.record_decision(cid, summary="spared the wounded foe", approval_tags=["mercy"])
    assert _attitude(cid, comp) == 25
    post = server.check_companion_arc(cid, comp)
    unlocked = [g for r in post["results"] for g in r.get("newly_unlocked", [])]
    assert any(g["kind"] == "loyalty" and g["threshold"] == 20 for g in unlocked)


def test_gate_stays_locked_without_the_decision(tmp_path, monkeypatch):
    """Revert check: same setup, but NO matching approval_tags => no move => gate stays locked.
    This is what proves the decision is what turned the arc (not time / not the evaluate call)."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"], attitude=15)
    server.set_companion_arc(cid, comp, arc={
        "arc_gates": [{"kind": "loyalty", "threshold": 20, "note": "she lets you in"}],
    })
    server.record_decision(cid, summary="a choice she doesn't care about", approval_tags=["greed"])
    assert _attitude(cid, comp) == 15
    post = server.check_companion_arc(cid, comp)
    assert post["results"] == []  # gate never crossed


# --- authored BG companion content uses the shared lowercase_snake vocabulary ----

# The seven Baldur's Gate origin companions whose dossiers were authored with canon causes.
_BG_COMPANIONS = ["Shadowheart", "Astarion", "Gale", "Wyll", "Karlach", "Lae'zel", "Halsin"]


@pytest.mark.parametrize("name", _BG_COMPANIONS)
def test_authored_bg_companion_causes_are_lowercase_snake_keys(name):
    """Every authored BG companion's approval causes are lowercase_snake KEYS (matchable by
    approval_tags) — not prose phrases (which could never match a tag). Guards the content
    against a regression back to un-matchable prose."""
    rec = content.load_canon_character("baldurs-gate", name)
    assert rec is not None, f"{name} canon record missing"
    d = content._coerce_dossier(rec.get("companion_dossier"), where="test")
    assert d is not None, f"{name} has no dossier"
    causes = list(d.approval_likes) + list(d.approval_dislikes)
    assert causes, f"{name} has no authored approval causes"
    for key in causes:
        assert key == key.lower(), f"{name}: {key!r} is not lowercase"
        assert " " not in key, f"{name}: {key!r} is a prose phrase, not a snake_case key"


def test_shared_vocabulary_lets_one_tag_move_several_companions(tmp_path, monkeypatch):
    """The BG "soul": a single moral cause ripples across the party. A `mercy` choice pleases
    Gale, Wyll, AND Halsin in one record_decision — many arcs nudged by one tag. This is the
    cross-cutting authored vocabulary working on real canon content.

    Gale/Halsin take load_canon_character's fresh-load path; Wyll is a rostered origin ("Wyll
    Ravengard") promoted in place by the canon-load dedup. All three carry the canon `mercy`
    like. Each is RECRUITED after load (the documented load -> recruit_companion seating that
    brings a companion into the party); recruit is idempotent for the fresh-loaded two."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    ids = {}
    for name in ["Gale", "Wyll", "Halsin"]:
        res = server.load_canon_character(bg, name, kind="companion", add_to_party=True)
        ids[name] = res["id"]
        # confirm the authored dossier actually lists `mercy` as a like (content sanity)
        assert "mercy" in server.get_character(bg, res["id"])["companion_dossier"]["approval_likes"]
        server.recruit_companion(bg, res["id"])  # seat them in the party (idempotent if already a companion)
    out = server.record_decision(bg, summary="spared the surrendering cultists",
                                 approval_tags=["mercy"])
    moved = {r["id"] for r in out["approval_results"]}
    # ONE tag moved all three — the ripple
    assert all(ids[n] in moved for n in ids), "a shared `mercy` tag should move every mercy-liking companion"
    for n in ids:
        assert server.get_character(bg, ids[n])["attitude_value"] == 10


# --- canon-load dedup: a fuller-display-name roster record is promoted, not duplicated ---

def test_loading_canon_wyll_promotes_roster_record_without_duplicate(tmp_path, monkeypatch):
    """Wyll's roster display name is "Wyll Ravengard" but his canon name is "Wyll", so the
    exact-name dedup MISSED the rostered record and load_canon_character fresh-loaded a SECOND
    "Wyll" (a duplicate beside npc-wyll). Dedup must catch the fuller-display-name roster record
    (via _find_existing_roster_match) and promote it in place: already_present, NO duplicate, and
    after recruit a tagged moral choice moves him (his roster dossier already lists `mercy`)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    res = server.load_canon_character(bg, "Wyll", kind="companion", add_to_party=True)
    assert res.get("already_present") is True and res["id"] == "npc-wyll"
    wylls = [ch for ch in store.load_campaign(bg).characters.values() if "wyll" in ch.name.strip().lower()]
    assert len(wylls) == 1, f"expected one Wyll record, got {[w.name for w in wylls]}"
    likes = server.get_character(bg, "npc-wyll")["companion_dossier"]["approval_likes"]
    assert "mercy" in [k.lower() for k in likes]
    server.recruit_companion(bg, "npc-wyll")
    before = server.get_character(bg, "npc-wyll")["attitude_value"]
    out = server.record_decision(bg, summary="spared the captive", approval_tags=["mercy"])
    assert "npc-wyll" in {r["id"] for r in out["approval_results"]}
    assert server.get_character(bg, "npc-wyll")["attitude_value"] > before


def test_loading_canon_minsc_promotes_roster_record_without_duplicate(tmp_path, monkeypatch):
    """Same fuller-display-name class as Wyll: canon "Minsc" vs rostered "Minsc and Boo"
    (id npc-minsc). Dedup must promote the rostered record in place — already_present, no
    second Minsc minted."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    res = server.load_canon_character(bg, "Minsc", kind="companion", add_to_party=True)
    assert res.get("already_present") is True and res["id"] == "npc-minsc"
    minscs = [ch for ch in store.load_campaign(bg).characters.values() if "minsc" in ch.name.strip().lower()]
    assert len(minscs) == 1, f"expected one Minsc record, got {[m.name for m in minscs]}"
