"""Audit-regression guards (2026-06-18) for the companion-approval / obligation surface.

Four CONFIRMED regressions, each fixed at its source in server.py:

  1. DUPLICATE approval_tags DOUBLE-COUNT — ``approval_tags=["mercy", "mercy"]`` (the same
     cause named twice in one decision) moved the gauge +20 instead of +10 because
     _normalize_approval_tags emitted one (key, delta) per list item and _apply_approval_tags
     accumulated. The fix collapses duplicate keys to ONE move per distinct cause per decision.

  2. NON-NUMERIC explicit DELTA crash — ``{"key": "mercy", "delta": "lots"}`` raised a bare
     ValueError out of ``int()`` and aborted the whole record_decision. The fix wraps the
     coercion and raises a CLEAR, key-named ValueError instead.

  3. DISLIKES-ONLY companion mis-cued — a companion authored with ONLY approval_dislikes is
     fully gaugeable (the mover matches dislikes too), but companion_gauge_unauthored gated on
     approval_LIKES only, so it nagged "un-gauged forever". The fix reads dislikes too.

  4. ADVENTURE-COMPANION arc=None parity — content.seed_campaign does not run the companion
     operational-state finisher, so a dossier-authored adventure companion (Vesper) entered the
     party with arc=None. The fix runs _seed_companion_operational_state on each party companion
     after seeding in start_adventure; the helper's None-guards preserve authored arcs.

ADDITIVE: every fix is invariant-safe (engine stays sole writer; old snapshots round-trip;
an authored arc/dossier is never overwritten). These tests are the RED→GREEN evidence.
"""

import pytest

import content
import server
import store
from models import Campaign, Character, CompanionArc, CompanionDossier


# --- shared helpers (mirror test_decision_approval / test_beat_obligations) --


def _new_campaign(monkeypatch, tmp_path, title="AuditRegression"):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign(title)["id"]


def _add_companion(cid, name, *, likes=None, dislikes=None, attitude=0):
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


def _kinds(obligations) -> set:
    return {o["kind"] for o in obligations}


# --- Fix 1: duplicate cause-key is counted ONCE per decision -----------------


def test_duplicate_tag_moves_gauge_once_not_twice(tmp_path, monkeypatch):
    """``["mercy", "mercy"]`` — the same cause named twice in one decision — moves +10, not +20."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"], dislikes=["cruelty"])
    out = server.record_decision(cid, summary="spared the wounded foe", approval_tags=["mercy", "mercy"])
    assert _attitude(cid, comp) == 10  # was 20 before the dedup fix
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == 10 and row["new_value"] == 10
    # the matched cause is reported once, not twice
    assert row["matched_keys"] == ["mercy"]


def test_duplicate_tag_dedup_at_the_normalizer():
    """Unit-level: the normalizer collapses duplicate keys to a single (key, delta) pair."""
    pairs = server._normalize_approval_tags(["mercy", "mercy", "Mercy"])  # casing folds too
    assert pairs == [("mercy", None)]


def test_duplicate_dict_tag_keeps_first_explicit_delta():
    """A cause named twice with an explicit delta on the first occurrence keeps that delta once."""
    pairs = server._normalize_approval_tags([{"key": "power", "delta": 25}, {"key": "power", "delta": 99}])
    assert pairs == [("power", 25)]


def test_duplicate_explicit_delta_applies_once(tmp_path, monkeypatch):
    """A doubled explicit-delta tag applies the delta ONCE (not twice)."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Astarion", likes=["power"])
    out = server.record_decision(
        cid, summary="seized the crown",
        approval_tags=[{"key": "power", "delta": 25}, {"key": "power", "delta": 25}],
    )
    assert _attitude(cid, comp) == 25  # was 50 before the fix
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == 25


def test_bare_string_repeat_fills_in_an_earlier_explicit_delta():
    """First occurrence is a bare string (None delta); a later explicit dict for the same key
    fills the delta in — still ONE pair (so a mixed restatement does not double-count)."""
    pairs = server._normalize_approval_tags(["mercy", {"key": "mercy", "delta": 5}])
    assert pairs == [("mercy", 5)]


def test_distinct_keys_still_each_move(tmp_path, monkeypatch):
    """Regression-guard for the fix: dedup is PER-KEY — two DISTINCT causes still both apply."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Wyll", likes=["heroism", "mercy"])
    out = server.record_decision(cid, summary="brave + kind", approval_tags=["heroism", "mercy"])
    assert _attitude(cid, comp) == 20  # +10 each, both distinct
    row = next(r for r in out["approval_results"] if r["id"] == comp)
    assert row["delta"] == 20
    assert set(row["matched_keys"]) == {"heroism", "mercy"}


# --- Fix 2: a non-numeric explicit delta is handled, not a bare int() crash --


def test_non_numeric_delta_raises_clear_value_error():
    """``{"key": "mercy", "delta": "lots"}`` raises a CLEAR ValueError naming the key — never an
    opaque bare ``int('lots')`` crash that aborts the whole record_decision."""
    with pytest.raises(ValueError) as ei:
        server._normalize_approval_tags([{"key": "mercy", "delta": "lots"}])
    msg = str(ei.value)
    assert "mercy" in msg and "integer" in msg


def test_non_numeric_delta_through_record_decision_raises_value_error(tmp_path, monkeypatch):
    """End-to-end: the malformed delta surfaces as a ValueError (a clean tool error), not an
    unhandled crash, and does NOT move the gauge."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Shadowheart", likes=["mercy"])
    with pytest.raises(ValueError):
        server.record_decision(
            cid, summary="a garbled tag", approval_tags=[{"key": "mercy", "delta": "lots"}],
        )
    assert _attitude(cid, comp) == 0  # no partial move


def test_numeric_string_delta_still_coerces():
    """A numeric STRING delta (a common JSON-ish input) still coerces cleanly to int."""
    pairs = server._normalize_approval_tags([{"key": "mercy", "delta": "15"}])
    assert pairs == [("mercy", 15)]


# --- Fix 3: a dislikes-ONLY companion is gaugeable, not nagged as un-gauged ---


def _dislikes_only_companion(name="Astarion", dislikes=("naive_altruism",)) -> Character:
    return Character(
        name=name,
        kind="companion",
        attitude_value=0,
        companion_dossier=CompanionDossier(approval_likes=[], approval_dislikes=list(dislikes)),
    )


def _campaign_with(*members: Character, day: int = 1) -> Campaign:
    c = Campaign(title="Obligations")
    for m in members:
        c.characters[m.id] = m
        c.party.append(m.id)
    c.day = day
    return c


def test_dislikes_only_companion_does_not_trip_gauge_unauthored():
    """A companion authored with ONLY approval_dislikes is gaugeable (the mover matches dislikes
    too), so companion_gauge_unauthored must NOT fire — it was falsely nagging forever."""
    comp = _dislikes_only_companion()
    c = _campaign_with(comp, day=5)
    assert "companion_gauge_unauthored" not in _kinds(server._compute_beat_obligations(c))


def test_dislikes_only_companion_is_actually_moved_by_a_tag(tmp_path, monkeypatch):
    """Proves the cue's premise: a dislikes-only companion really is moved by a matching tag,
    so cueing it as 'un-gauged' would be wrong."""
    cid = _new_campaign(monkeypatch, tmp_path)
    comp = _add_companion(cid, "Astarion", dislikes=["naive_altruism"])
    server.record_decision(cid, summary="a bleeding-heart gesture", approval_tags=["naive_altruism"])
    assert _attitude(cid, comp) == -10


def test_truly_vocabularyless_companion_still_trips_gauge_unauthored():
    """Revert-guard: a companion with NEITHER likes nor dislikes still trips the cue (the real
    un-gauged case the cue is meant to catch)."""
    comp = Character(name="Recruit", kind="companion", companion_dossier=None)
    c = _campaign_with(comp, day=5)
    assert "companion_gauge_unauthored" in _kinds(server._compute_beat_obligations(c))


# --- Fix 4: an adventure companion gets a default arc after start_adventure ---


def test_adventure_companion_gets_default_arc(tmp_path, monkeypatch):
    """Vesper (cellar-rats) is authored with a dossier but arc=None. After start_adventure the
    operational-state finisher seeds a default loyalty arc so a gate can linger — the gauge moves
    AND the arc machine has state to track."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    c = store.load_campaign(cid)
    vesper = next(ch for ch in c.characters.values()
                  if ch.kind == "companion" and ch.name == "Vesper")
    assert vesper.arc is not None, "Vesper should get a default arc seeded at start_adventure"
    assert vesper.arc.arc_gates, "the default arc carries at least one loyalty gate"
    # the authored dossier is PRESERVED (the None-guards never overwrite authored state)
    assert vesper.companion_dossier is not None
    # and Vesper is a real party member
    assert vesper.id in c.party


def test_adventure_companion_default_arc_is_a_loyalty_gate(tmp_path, monkeypatch):
    """The default arc is the light loyalty gate the shared finisher seeds (not an empty arc)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    c = store.load_campaign(cid)
    vesper = next(ch for ch in c.characters.values()
                  if ch.kind == "companion" and ch.name == "Vesper")
    kinds = {g.kind for g in vesper.arc.arc_gates}
    assert "loyalty" in kinds


@pytest.mark.parametrize("adventure_id,companion,authored_threshold", [
    # the spine companions ship an AUTHORED arc — the None-guard must NOT overwrite it
    ("embergloom-pact", "Brother Toll", None),
    ("ashfall-reach", "Wren Calder", None),
])
def test_authored_spine_companion_arc_is_preserved(adventure_id, companion, authored_threshold,
                                                   tmp_path, monkeypatch):
    """A spine companion authored WITH an arc keeps that arc through start_adventure — the
    None-guarded finisher only fills a MISSING arc, never clobbers an authored one."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    # capture the authored arc straight from the adventure module
    adv = content.load_adventure_data(adventure_id)
    authored = next(comp for comp in adv.get("companions", []) if comp.get("name") == companion)
    authored_gate_count = len(authored.get("arc", {}).get("arc_gates", []) or [])
    assert authored_gate_count > 0, "fixture sanity: this spine companion ships an authored arc"

    cid = server.start_adventure(adventure_id)["campaign_id"]
    c = store.load_campaign(cid)
    ch = next(x for x in c.characters.values() if x.kind == "companion" and x.name == companion)
    assert ch.arc is not None
    # the authored gate count is intact — the finisher did not append/replace
    assert len(ch.arc.arc_gates) == authored_gate_count
