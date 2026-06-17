"""The engine-owned 3-act cursor (NarrativeArc) — model + the engine write points.

WorldOS folds the 3-act-shape mandate (setup -> midpoint reversal -> climax) into a
single integer cursor the ENGINE owns and advances under campaign_lock. This is the
canonical contract `Campaign.narrative_arc: NarrativeArc` with field `.act`.

ADDITIVE: an old snapshot lacking the key deserializes to the all-defaulted arc
(act=1, day_act_entered=1, beats_in_act=0, nothing landed) — empty == today, and it
round-trips byte-identically under `_StrictModel` (extra=forbid). The engine is the
SOLE WRITER: persist_beat bumps the act-local beat tally, and advance_act /
mark_reversal / mark_climax stamp the cursor — each under campaign_lock + save_campaign.

Mirrors test_campaign_backlog.py (the additive-model idiom) and the locked-tool tests.
"""

import pytest
from pydantic import ValidationError

import server
import store
from models import Campaign, NarrativeArc


def _camp(day: int = 1) -> Campaign:
    return Campaign(title="T", day=day)


# --- INCREMENT 1: the model is additive (an empty arc == today's behavior) --------------------


def test_default_narrative_arc_is_present_and_all_defaulted():
    # Present-by-default (not Optional), every field defaulted, so the cursor is a valid
    # default_factory and EMPTY == today: act 1, day 1, no beats, nothing landed.
    c = _camp()
    assert isinstance(c.narrative_arc, NarrativeArc)
    arc = c.narrative_arc
    assert arc.act == 1
    assert arc.day_act_entered == 1
    assert arc.beats_in_act == 0
    assert arc.midpoint_reversal_landed is False
    assert arc.climax_landed is False
    assert arc.reversal_day == 0
    assert arc.climax_day == 0


def test_old_snapshot_without_narrative_arc_roundtrips_unchanged():
    # The additive contract: an OLD snapshot dict lacking the key deserializes to the
    # all-defaulted arc (act=1), and the full dump -> validate -> dump is byte-identical.
    old = {"title": "Old Hold", "day": 7, "time_of_day": "evening"}
    c = Campaign.model_validate(old)
    assert c.narrative_arc.act == 1
    assert c.narrative_arc.day_act_entered == 1
    assert c.narrative_arc.beats_in_act == 0

    dumped = c.model_dump(mode="json")
    assert dumped["narrative_arc"] == {
        "act": 1,
        "day_act_entered": 1,
        "beats_in_act": 0,
        "midpoint_reversal_landed": False,
        "climax_landed": False,
        "reversal_day": 0,
        "climax_day": 0,
    }
    again = Campaign.model_validate(dumped)
    assert again.model_dump(mode="json") == dumped


def test_narrative_arc_rejects_typoed_field():
    # _StrictModel (extra=forbid) still rejects a typo'd field on the new model.
    with pytest.raises(ValidationError):
        NarrativeArc(actt=2)


# --- INCREMENT 3: the engine writes (persist_beat tick + the three boundary tools) ------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    c = _camp()
    store.save_campaign(c)
    return c.id


def test_persist_beat_increments_beats_in_act(cid):
    # Every persist_beat that carries a write ticks the act-local beat tally by one.
    before = store.load_campaign(cid).narrative_arc.beats_in_act
    server.persist_beat(campaign_id=cid, events=[{"kind": "narration", "text": "A beat lands."}])
    after = store.load_campaign(cid).narrative_arc.beats_in_act
    assert after == before + 1
    server.persist_beat(campaign_id=cid, events=[{"kind": "narration", "text": "Another beat."}])
    assert store.load_campaign(cid).narrative_arc.beats_in_act == before + 2


def test_advance_act_sets_act_and_resets_beats(cid):
    # Bump some beats first so we can prove the reset.
    server.persist_beat(campaign_id=cid, events=[{"kind": "narration", "text": "x"}])
    c = store.load_campaign(cid)
    c.day = 5
    store.save_campaign(c)
    out = server.advance_act(cid, 2)
    arc = store.load_campaign(cid).narrative_arc
    assert arc.act == 2
    assert arc.day_act_entered == 5
    assert arc.beats_in_act == 0
    assert out["act"] == 2
    assert out["day_act_entered"] == 5


def test_advance_act_rejects_non_contiguous_jump(cid):
    # Only +1 is legal (1 -> 2 -> 3); a 1 -> 3 jump is rejected, naming the legal next act.
    with pytest.raises(ValueError):
        server.advance_act(cid, 3)
    # The cursor is unchanged after the rejected jump.
    assert store.load_campaign(cid).narrative_arc.act == 1


def test_advance_act_full_chain(cid):
    server.advance_act(cid, 2)
    assert store.load_campaign(cid).narrative_arc.act == 2
    server.advance_act(cid, 3)
    assert store.load_campaign(cid).narrative_arc.act == 3
    # No act past 3.
    with pytest.raises(ValueError):
        server.advance_act(cid, 4)


def test_mark_reversal_stamps_landed_and_day_idempotently(cid):
    c = store.load_campaign(cid)
    c.day = 9
    store.save_campaign(c)
    server.mark_reversal(cid)
    arc = store.load_campaign(cid).narrative_arc
    assert arc.midpoint_reversal_landed is True
    assert arc.reversal_day == 9
    # Idempotent: a re-call on a later day keeps the ORIGINAL landed day.
    c = store.load_campaign(cid)
    c.day = 12
    store.save_campaign(c)
    server.mark_reversal(cid)
    arc = store.load_campaign(cid).narrative_arc
    assert arc.midpoint_reversal_landed is True
    assert arc.reversal_day == 9


def test_mark_climax_stamps_landed_and_day_idempotently(cid):
    c = store.load_campaign(cid)
    c.day = 14
    store.save_campaign(c)
    server.mark_climax(cid)
    arc = store.load_campaign(cid).narrative_arc
    assert arc.climax_landed is True
    assert arc.climax_day == 14
    c = store.load_campaign(cid)
    c.day = 20
    store.save_campaign(c)
    server.mark_climax(cid)
    arc = store.load_campaign(cid).narrative_arc
    assert arc.climax_landed is True
    assert arc.climax_day == 14


# --- INCREMENT 3 end-to-end: the cues now fire on real runs -------------------


def test_act_midpoint_owed_fires_end_to_end_via_persist_beat(cid):
    """With the engine writing the cursor, the Act-2 midpoint cue rides the EVERY-BEAT
    persist_beat return on a real run (advance to act 2, day moves, beats accumulate)."""
    server.advance_act(cid, 2)
    c = store.load_campaign(cid)
    c.day = 4  # day_in_act >= 2 since day_act_entered was stamped at day 1
    store.save_campaign(c)
    out = server.persist_beat(
        cid, events=[{"kind": "narration", "text": "The middle of the story turns."}]
    )
    kinds = {o["kind"] for o in out.get("obligations", [])}
    assert "act_midpoint_owed" in kinds
    # Once the reversal is recorded, the cue clears on the next beat.
    server.mark_reversal(cid)
    out2 = server.persist_beat(
        cid, events=[{"kind": "narration", "text": "The party reels from the turn."}]
    )
    kinds2 = {o["kind"] for o in out2.get("obligations", [])}
    assert "act_midpoint_owed" not in kinds2
