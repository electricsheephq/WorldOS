"""The EVERY-BEAT obligations digest (relationship-cues).

The proven failure (an 18-beat authored playtest): the DM narrates the companion +
quest story in prose but never engages the engine — a companion stays at attitude 0
all run, a quest stays active with empty evolves_to, camp never happens. The lesson:
*surfacing info != the DM using it — fold the obligation into a tool the DM hits EVERY
beat.* persist_beat is that tool; scene_context.durable is the lean-on twin.

These guard `_compute_beat_obligations` (the pure read-only digest), its presence in
persist_beat's and scene_context.durable's return ONLY when actionable, and that a
healthy / minimal campaign yields NO `obligations` key (the additive contract).
"""

import pytest

import server
import store
from models import (
    ArcGate,
    Campaign,
    CampBeatRecord,
    Character,
    CompanionAgenda,
    CompanionArc,
    CompanionDossier,
    Consequence,
    Quest,
)


# --- helpers ----------------------------------------------------------------


def _companion(
    name: str = "Brother Toll",
    attitude: int = 0,
    likes=None,
    gates=None,
    last_long_rest_day: int = -1,
) -> Character:
    dossier = None
    if likes is not None:
        dossier = CompanionDossier(approval_likes=list(likes))
    arc = CompanionArc(arc_gates=list(gates)) if gates is not None else None
    return Character(
        name=name,
        kind="companion",
        attitude_value=attitude,
        companion_dossier=dossier,
        arc=arc,
        last_long_rest_day=last_long_rest_day,
    )


def _campaign_with(*members: Character, day: int = 1) -> Campaign:
    c = Campaign(title="Obligations")
    for m in members:
        c.characters[m.id] = m
        c.party.append(m.id)
    c.day = day
    return c


def _kinds(obligations) -> set:
    return {o["kind"] for o in obligations}


# --- the run-mirroring case (the proven failure) ----------------------------


def test_frozen_companion_and_resolvable_quest_surface():
    """Mirror the playtest: a kind=companion at attitude 0 on day 5, an active quest
    with all objectives done. The digest must name BOTH the frozen-approval and the
    resolvable-quest obligations."""
    comp = _companion(likes=["mercy", "protecting the weak"])
    c = _campaign_with(comp, day=5)
    q = Quest(
        title="The Embergloom Pact",
        objectives=["find the relic", "free the prisoners"],
        completed_objectives=["find the relic", "free the prisoners"],
    )
    c.quests[q.id] = q

    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "companion_approval_frozen" in kinds
    assert "quest_resolvable" in kinds

    frozen = next(o for o in obligations if o["kind"] == "companion_approval_frozen")
    assert frozen["name"] == "Brother Toll"
    assert frozen["approval_likes"] == ["mercy", "protecting the weak"]
    assert "record_decision" in frozen["detail"]

    resolvable = next(o for o in obligations if o["kind"] == "quest_resolvable")
    assert resolvable["title"] == "The Embergloom Pact"
    assert resolvable["quest_id"] == q.id
    assert "complete_quest" in resolvable["detail"]


def test_camp_overdue_when_no_companion_has_rested():
    """A party with a companion that never rested, a few days in -> camp is overdue."""
    comp = _companion(likes=["mercy"], last_long_rest_day=-1)
    c = _campaign_with(comp, day=5)
    obligations = server._compute_beat_obligations(c)
    assert "camp_overdue" in _kinds(obligations)


def test_camp_overdue_when_last_rest_is_three_days_old():
    comp = _companion(likes=["mercy"], attitude=10, last_long_rest_day=2)
    c = _campaign_with(comp, day=5)  # 5 - 2 == 3 days since rest
    assert "camp_overdue" in _kinds(server._compute_beat_obligations(c))


# --- camp_scene_skipped: rested TODAY but no camp scene landed --------------


def test_camp_scene_skipped_fires_when_party_rested_today_with_no_camp_record():
    """The live-run bug: the DM long_rest'd (companion's last_long_rest_day == today) but never
    called camp_scene — so there's NO CampBeatRecord for the companion today. The obligation fires,
    names the companion, and cues camp_scene."""
    comp = _companion(name="Karlach", attitude=10, last_long_rest_day=5)
    c = _campaign_with(comp, day=5)  # rested TODAY (day 5), no camp record at all
    obligations = server._compute_beat_obligations(c)
    assert "camp_scene_skipped" in _kinds(obligations)
    skipped = next(o for o in obligations if o["kind"] == "camp_scene_skipped")
    assert skipped["names"] == ["Karlach"]
    assert comp.id in skipped["character_ids"]
    assert "camp_scene" in skipped["detail"]
    # It is NOT also camp_overdue (the rest is FRESH today, not 3+ days stale).
    assert "camp_overdue" not in _kinds(obligations)


def test_camp_scene_skipped_does_not_fire_when_a_camp_record_exists_today():
    """A camp record FOR THIS companion ON THIS DAY means camp happened — do not fire."""
    comp = _companion(name="Karlach", attitude=10, last_long_rest_day=5)
    c = _campaign_with(comp, day=5)
    c.camp_beats.records.append(
        CampBeatRecord(id="banter-1", day=5, companion_ids=[comp.id], kind="solo")
    )
    assert "camp_scene_skipped" not in _kinds(server._compute_beat_obligations(c))


def test_camp_scene_skipped_does_not_fire_when_rest_was_a_prior_day():
    """If the companion rested YESTERDAY (last_long_rest_day != today) the 'rested-today-but-no-
    camp' gap doesn't apply — camp_overdue owns the stale-rest case, not this obligation."""
    comp = _companion(name="Karlach", attitude=10, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)  # rested day 4, today is 5
    assert "camp_scene_skipped" not in _kinds(server._compute_beat_obligations(c))


def test_camp_scene_skipped_fires_only_for_the_companion_without_a_record_today():
    """Two companions rested today; one got a camp beat, the other didn't — only the one WITHOUT a
    record today is flagged."""
    camped = _companion(name="Shadowheart", attitude=10, last_long_rest_day=5)
    skipped_comp = _companion(name="Lae'zel", attitude=10, last_long_rest_day=5)
    c = _campaign_with(camped, skipped_comp, day=5)
    c.camp_beats.records.append(
        CampBeatRecord(id="banter-1", day=5, companion_ids=[camped.id], kind="solo")
    )
    obligations = server._compute_beat_obligations(c)
    assert "camp_scene_skipped" in _kinds(obligations)
    skipped = next(o for o in obligations if o["kind"] == "camp_scene_skipped")
    assert skipped["names"] == ["Lae'zel"]
    assert camped.id not in skipped["character_ids"]
    assert skipped_comp.id in skipped["character_ids"]


def test_camp_scene_skipped_silent_for_companionless_party():
    pc = Character(name="Hero", kind="player", last_long_rest_day=5)
    c = _campaign_with(pc, day=5)
    assert "camp_scene_skipped" not in _kinds(server._compute_beat_obligations(c))


def test_camp_scene_skipped_record_from_a_prior_day_does_not_suppress():
    """A camp record exists, but from a PRIOR day — today's rest still has no camp scene, so it
    fires (the record must match TODAY's day to suppress)."""
    comp = _companion(name="Karlach", attitude=10, last_long_rest_day=5)
    c = _campaign_with(comp, day=5)
    c.camp_beats.records.append(
        CampBeatRecord(id="banter-old", day=2, companion_ids=[comp.id], kind="solo")
    )
    assert "camp_scene_skipped" in _kinds(server._compute_beat_obligations(c))


def test_quest_stalled_surfaces():
    comp = _companion(attitude=10, last_long_rest_day=4)
    c = _campaign_with(comp, day=8)
    q = Quest(title="The Long Road", objectives=["reach the keep"], last_progress_day=4)
    c.quests[q.id] = q
    obligations = server._compute_beat_obligations(c)
    assert "quest_stalled" in _kinds(obligations)
    # A stalled quest is NOT also reported resolvable.
    assert "quest_resolvable" not in _kinds(obligations)


def test_quest_no_echo_surfaces_for_resolved_quest_without_evolution():
    comp = _companion(attitude=10, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    q = Quest(title="The Sealed Crypt", status="completed", evolves_to="")
    c.quests[q.id] = q
    assert "quest_no_echo" in _kinds(server._compute_beat_obligations(c))


def test_quest_no_echo_suppressed_when_a_consequence_names_the_quest():
    comp = _companion(attitude=10, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    q = Quest(title="The Sealed Crypt", status="completed", evolves_to="")
    c.quests[q.id] = q
    c.consequences.append(
        Consequence(trigger_day=8, text="Echoes of the Sealed Crypt stir the cult.")
    )
    assert "quest_no_echo" not in _kinds(server._compute_beat_obligations(c))


def test_quest_no_echo_suppressed_when_evolves_to_is_set():
    comp = _companion(attitude=10, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    q = Quest(title="The Sealed Crypt", status="completed", evolves_to="the cult regroups")
    c.quests[q.id] = q
    assert "quest_no_echo" not in _kinds(server._compute_beat_obligations(c))


def test_companion_arc_gate_near_surfaces_within_twenty_points():
    gate = ArcGate(kind="loyalty", threshold=25, note="a deepened loyalty")
    comp = _companion(attitude=10, gates=[gate], last_long_rest_day=4)  # 15 away
    c = _campaign_with(comp, day=5)
    obligations = server._compute_beat_obligations(c)
    near = [o for o in obligations if o["kind"] == "companion_arc_gate_near"]
    assert near and near[0]["points_away"] == 15


def test_companion_arc_gate_not_near_is_silent():
    gate = ArcGate(kind="loyalty", threshold=80, note="a deepened loyalty")
    comp = _companion(attitude=10, gates=[gate], last_long_rest_day=4)  # 70 away
    c = _campaign_with(comp, day=5)
    assert "companion_arc_gate_near" not in _kinds(server._compute_beat_obligations(c))


# --- contextual silence: the digest must not nag a healthy/minimal campaign ---


def test_healthy_campaign_yields_no_obligations():
    """Approval moved, recently rested, no ripe/stalled quest -> empty digest."""
    comp = _companion(name="Wyll", attitude=20, likes=["mercy"], last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    q = Quest(title="An ongoing thread", objectives=["keep going"], last_progress_day=5)
    c.quests[q.id] = q
    assert server._compute_beat_obligations(c) == []


def test_early_days_do_not_trip_frozen_or_camp():
    """Before day 3 a frozen companion / un-rested party is normal, not an obligation."""
    comp = _companion(likes=["mercy"], attitude=0, last_long_rest_day=-1)
    c = _campaign_with(comp, day=2)
    assert server._compute_beat_obligations(c) == []


def test_companionless_campaign_is_silent():
    pc = Character(name="Hero", kind="player")
    c = _campaign_with(pc, day=8)
    obligations = server._compute_beat_obligations(c)
    assert "companion_approval_frozen" not in _kinds(obligations)
    assert "camp_overdue" not in _kinds(obligations)


def test_frozen_fires_without_likes_suggests_adjust_attitude():
    """A frozen companion is flagged even with NO authored approval_likes — an inert
    companion is the bug regardless. When the vocabulary isn't authored, the cue points at
    adjust_attitude (not approval_tags), so an un-augmented companion still gets engaged."""
    comp = _companion(likes=None, attitude=0, last_long_rest_day=4)  # no dossier
    c = _campaign_with(comp, day=5)
    frozen = [o for o in server._compute_beat_obligations(c)
              if o["kind"] == "companion_approval_frozen"]
    assert frozen, "a frozen companion should be flagged even without authored likes"
    assert frozen[0]["approval_likes"] == []
    assert "adjust_attitude" in frozen[0]["detail"]


# --- persist_beat / scene_context surfaces ----------------------------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def _make_frozen_run(cid: str) -> None:
    """Mutate the live campaign to look like the failure run: a frozen companion w/
    likes on day 5 + an active all-objectives-done quest."""
    c = store.load_campaign(cid)
    comp = Character(
        name="Brother Toll",
        kind="companion",
        attitude_value=0,
        companion_dossier=CompanionDossier(approval_likes=["mercy"]),
        last_long_rest_day=-1,
    )
    c.characters[comp.id] = comp
    c.party.append(comp.id)
    q = Quest(
        title="The Embergloom Pact",
        objectives=["free the prisoners"],
        completed_objectives=["free the prisoners"],
    )
    c.quests[q.id] = q
    c.day = 5
    store.save_campaign(c)


def test_persist_beat_returns_obligations_when_actionable(cid):
    _make_frozen_run(cid)
    out = server.persist_beat(cid, events=[{"kind": "narration", "text": "The beat lands."}])
    assert "obligations" in out
    assert "companion_approval_frozen" in _kinds(out["obligations"])
    assert "quest_resolvable" in _kinds(out["obligations"])


def test_persist_beat_omits_obligations_key_on_healthy_fixture(cid):
    """The unmodified starter fixture is healthy/early -> no obligations key (the four-
    key additive shape is preserved)."""
    out = server.persist_beat(cid, events=[{"kind": "narration", "text": "A quiet beat."}])
    assert "obligations" not in out
    # The old four-key shape (plus optional approval_results) is intact.
    assert set(out) <= {"logged", "remembered", "decision", "time", "approval_results"}


def test_scene_context_durable_mirrors_obligations(cid):
    _make_frozen_run(cid)
    sc = server.scene_context(cid)
    durable = sc["durable"]
    assert "obligations" in durable
    assert "companion_approval_frozen" in _kinds(durable["obligations"])


def test_scene_context_durable_omits_obligations_on_healthy_fixture(cid):
    sc = server.scene_context(cid)
    assert "obligations" not in sc["durable"]


# --- companion_betrayal_approaching (the BETRAYAL-side analog of #961's loyalty cues) ----
#
# The cooperative cues (frozen / camp / gate-near) fire when a THRIVING bond sits inert. The
# betrayal cue is their mirror: a live, unfired attitude_below agenda whose bond has curdled
# past its breaking point (and clearly soured) wants the DM to FORESHADOW the turn every beat.
# The telegraph companion_arc.evaluate() computes only reached the DM via an explicit
# check_companion_arc call, so an approaching betrayal stayed invisible in play — the precise
# gap that left "betrayals never engage" even though the engine machinery (issue #142) works.


def _betrayer(attitude, threshold=18, fired=False, decision_flag="") -> Character:
    """A companion carrying an attitude_below betrayal agenda (Sergeant Ondine Marsh's shape)."""
    return Character(
        name="Sergeant Ondine Marsh",
        kind="companion",
        attitude_value=attitude,
        arc=CompanionArc(
            agenda=CompanionAgenda(
                trigger="attitude_below", value=threshold, fired=fired, decision_flag=decision_flag
            )
        ),
    )


def test_betrayal_approaching_fires_when_bond_curdles_past_breaking_point():
    """A live attitude_below agenda + a bond soured into the warning band (regard -30,
    threshold 18) -> the DM is cued to foreshadow the fracture this beat."""
    comp = _betrayer(attitude=-30, threshold=18)
    c = _campaign_with(comp, day=4)
    appr = [o for o in server._compute_beat_obligations(c)
            if o["kind"] == "companion_betrayal_approaching"]
    assert appr, "a curdled betrayal agenda must surface a cue"
    o = appr[0]
    assert o["name"] == "Sergeant Ondine Marsh"
    assert o["attitude_value"] == -30 and o["threshold"] == 18
    assert o["deep_red"] is False  # -30 has not yet passed the deep-red marker (-40)
    assert "REAL attack" in o["detail"]


def test_betrayal_approaching_silent_when_bond_only_mildly_soured():
    """The agenda is LIVE (regard 10 < threshold 18) but the bond is still above the upper
    warning edge (-20) -> don't telegraph a barely-cooled bond."""
    comp = _betrayer(attitude=10, threshold=18)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_for_warm_companion():
    """The gs-ledger-betray case: even a 'cruel' player ran a liberatory arc, regard rose to
    50 -> the agenda never went live and the cue does NOT false-fire."""
    comp = _betrayer(attitude=50, threshold=18)
    c = _campaign_with(comp, day=6)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_without_agenda():
    """A loyalty-only companion (no betrayal agenda) is never flagged, however soured."""
    comp = _companion(name="Sergeant Ondine Marsh", attitude=-30, last_long_rest_day=4)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_when_agenda_already_fired():
    """A FIRED agenda is the event itself, not a warning -> never re-cued as approaching."""
    comp = _betrayer(attitude=-50, threshold=18, fired=True)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_marks_deep_red_and_decision_flag():
    """Deep red (regard past -40) + a recorded decision_flag spike both surface — severity
    rises to high and the cue tells the DM to foreshadow harder."""
    comp = _betrayer(attitude=-45, threshold=18, decision_flag="took_ferreths_coin")
    c = _campaign_with(comp, day=6)
    c.flags["took_ferreths_coin"] = True
    appr = [o for o in server._compute_beat_obligations(c)
            if o["kind"] == "companion_betrayal_approaching"]
    assert appr
    o = appr[0]
    assert o["deep_red"] is True
    assert o["decision_flag_active"] is True
    assert o["severity"] == "high"
    assert "deep red" in o["detail"].lower()


def _make_betrayer_run(cid: str) -> None:
    """Mutate the live campaign: a party companion whose attitude_below agenda has curdled
    past its breaking point into the warning band (regard -30, threshold 18)."""
    c = store.load_campaign(cid)
    comp = Character(
        name="Sergeant Ondine Marsh",
        kind="companion",
        attitude_value=-30,
        arc=CompanionArc(agenda=CompanionAgenda(trigger="attitude_below", value=18)),
    )
    c.characters[comp.id] = comp
    c.party.append(comp.id)
    c.day = 4
    store.save_campaign(c)


def test_persist_beat_surfaces_approaching_betrayal(cid):
    """End-to-end: the cue rides the EVERY-BEAT persist_beat path (the whole point — the
    telegraph that needed an explicit check_companion_arc now reaches the DM every beat)."""
    _make_betrayer_run(cid)
    out = server.persist_beat(
        cid, events=[{"kind": "narration", "text": "She won't meet your eyes."}]
    )
    assert "obligations" in out
    assert "companion_betrayal_approaching" in _kinds(out["obligations"])


def test_scene_context_durable_surfaces_approaching_betrayal(cid):
    """The cue must also ride the lean-on re-ground twin (scene_context.durable), matching the
    dual-surface coverage every prior obligation kind has."""
    _make_betrayer_run(cid)
    sc = server.scene_context(cid)
    assert "obligations" in sc["durable"]
    assert "companion_betrayal_approaching" in _kinds(sc["durable"]["obligations"])
