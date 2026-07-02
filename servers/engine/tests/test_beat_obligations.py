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
    CompanionQuestArc,
    Consequence,
    Faction,
    FactionArc,
    NarrativeArc,
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


# --- WS-D: camp_overdue reachable by BEATS when the in-world clock never advances ---------------

def test_camp_overdue_reachable_by_beats_when_clock_stuck_at_day_one():
    """The DM rarely advances the in-world clock (0 long_rest/downtime across the QA corpus), so the
    old day>=3 gate never fired and camp — the companion-regard pillar — stayed dead (0/203 camp
    beats). A never-rested party with companions that has played >=_CAMP_OVERDUE_BEATS beats now
    surfaces camp_overdue even at day 1."""
    comp = _companion(likes=["mercy"], last_long_rest_day=-1)
    c = _campaign_with(comp, day=1)            # clock stuck at day 1
    c.narrative_arc.beats_in_act = server._CAMP_OVERDUE_BEATS
    assert "camp_overdue" in _kinds(server._compute_beat_obligations(c))


def test_camp_overdue_not_yet_owed_below_beats_threshold():
    """Below the beats threshold (and below day 3) camp is not yet overdue — no false-early cue."""
    comp = _companion(likes=["mercy"], last_long_rest_day=-1)
    c = _campaign_with(comp, day=1)
    c.narrative_arc.beats_in_act = server._CAMP_OVERDUE_BEATS - 1
    assert "camp_overdue" not in _kinds(server._compute_beat_obligations(c))


def test_camp_overdue_beats_path_silent_for_a_party_that_has_rested():
    """The beats reach only surfaces a NEVER-rested (or stale-rested) party; a party that rested
    recently is not overdue even with many beats and a stuck clock — the inner rest check still
    holds (never_rested False, day-latest_rest == 0 < 3)."""
    comp = _companion(likes=["mercy"], attitude=10, last_long_rest_day=1)
    c = _campaign_with(comp, day=1)            # rested on day 1, clock stuck at day 1
    c.narrative_arc.beats_in_act = server._CAMP_OVERDUE_BEATS + 4
    assert "camp_overdue" not in _kinds(server._compute_beat_obligations(c))


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
    """Approval moved, recently rested, no ripe/stalled quest, AND the gauged companion
    owns a personal quest arc (WS-A auto-seeds a vocabulary on every companion, so a
    fully-healthy fixture must also have authored the arc) -> empty digest."""
    comp = _companion(name="Wyll", attitude=20, likes=["mercy"], last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    q = Quest(title="An ongoing thread", objectives=["keep going"], last_progress_day=5)
    c.quests[q.id] = q
    arc = CompanionQuestArc(companion_id=comp.id, title="Wyll's personal thread")
    c.companion_quest_arcs[arc.id] = arc
    assert server._compute_beat_obligations(c) == []


def test_early_days_do_not_trip_frozen_or_camp():
    """Before day 3 a frozen companion / un-rested party is normal, not an obligation.
    The companion is gauged AND owns a personal quest arc so the (day-less) quest cue is
    silent too — isolating the assertion to the day-gated frozen/camp cues under test."""
    comp = _companion(likes=["mercy"], attitude=0, last_long_rest_day=-1)
    c = _campaign_with(comp, day=2)
    arc = CompanionQuestArc(companion_id=comp.id, title="An early personal thread")
    c.companion_quest_arcs[arc.id] = arc
    assert server._compute_beat_obligations(c) == []


def test_companionless_campaign_is_silent():
    pc = Character(name="Hero", kind="player")
    c = _campaign_with(pc, day=8)
    obligations = server._compute_beat_obligations(c)
    assert "companion_approval_frozen" not in _kinds(obligations)
    assert "camp_overdue" not in _kinds(obligations)


def test_no_vocabulary_fires_gauge_unauthored_not_frozen():
    """A companion with NO authored approval vocabulary is the freely-recruited/generated case:
    it must surface companion_gauge_unauthored (author the vocabulary), NOT companion_approval_frozen
    (which is for an AUTHORED-but-unused gauge). The fix is authoring a vocabulary, not nudging a
    number."""
    comp = _companion(likes=None, attitude=0, last_long_rest_day=4)  # no dossier / no vocab
    c = _campaign_with(comp, day=5)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "companion_gauge_unauthored" in kinds
    assert "companion_approval_frozen" not in kinds  # owned by #0 now
    cue = next(o for o in server._compute_beat_obligations(c)
               if o["kind"] == "companion_gauge_unauthored")
    assert cue["name"] == "Brother Toll"
    assert "author_companion_gauges" in cue["detail"]


def test_gauge_unauthored_absent_when_vocabulary_authored():
    """A companion WITH an authored approval vocabulary does not trip the root cue — and a frozen
    such companion trips companion_approval_frozen instead."""
    comp = _companion(likes=["mercy", "protecting the weak"], attitude=0, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "companion_gauge_unauthored" not in kinds
    assert "companion_approval_frozen" in kinds  # vocab exists but regard still frozen


# --- companion_quest_unauthored (the dead companion-quest-arc loop, WS-C) ----
#
# set_companion_quest_arc was the ONLY writer of c.companion_quest_arcs, but nothing ever
# cued the DM to call it -> 0/448 campaigns owned a CompanionQuestArc and the whole
# personal-quest subsystem was narrated-not-engined. This cue mirrors #0
# (companion_gauge_unauthored): a GAUGED companion with no arc gets a per-beat nudge to
# author one. Gauged-ness is the precedence gate, so #0 (un-gauged) and #0b (gauged-but-
# arc-less) never stack on the same companion.


def test_companion_quest_unauthored_fires_for_gauged_companion_without_arc():
    """A gauged party companion (authored approval vocabulary) who owns no CompanionQuestArc
    trips companion_quest_unauthored -> cue authoring the personal thread."""
    comp = _companion(likes=["mercy", "protecting the weak"], attitude=20, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "companion_quest_unauthored" in kinds
    assert "companion_gauge_unauthored" not in kinds  # gauged -> #0 stays silent
    cue = next(o for o in obligations if o["kind"] == "companion_quest_unauthored")
    assert cue["name"] == "Brother Toll"
    assert cue["character_id"] == comp.id
    assert "set_companion_quest_arc" in cue["detail"]


def test_companion_quest_unauthored_absent_when_arc_exists():
    """The same gauged companion, once they own a CompanionQuestArc, no longer trips the cue
    (their personal story is engined)."""
    comp = _companion(likes=["mercy", "protecting the weak"], attitude=20, last_long_rest_day=4)
    c = _campaign_with(comp, day=5)
    arc = CompanionQuestArc(companion_id=comp.id, title="Brother Toll's reckoning")
    c.companion_quest_arcs[arc.id] = arc
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "companion_quest_unauthored" not in kinds


def test_companion_quest_unauthored_absent_for_ungauged_companion():
    """An UN-gauged companion (empty approval vocabulary) is #0's case (author the vocabulary
    first), NOT #0b's: companion_quest_unauthored stays silent and companion_gauge_unauthored
    fires instead. Precedence: vocabulary before the deeper personal-quest layer."""
    comp = _companion(likes=None, attitude=0, last_long_rest_day=4)  # no dossier / no vocab
    c = _campaign_with(comp, day=5)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "companion_quest_unauthored" not in kinds
    assert "companion_gauge_unauthored" in kinds


def test_companion_quest_unauthored_silent_for_companionless_campaign():
    """ADDITIVE/EMPTY contract: a campaign with no companions never trips the cue."""
    pc = Character(name="Hero", kind="player")
    c = _campaign_with(pc, day=8)
    assert "companion_quest_unauthored" not in _kinds(server._compute_beat_obligations(c))


# --- persist_beat / scene_context surfaces ----------------------------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
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


def _author_quest_arcs_for_party_companions(cid: str) -> None:
    """Make the live starter fixture FULLY healthy: WS-A auto-seeds an approval vocabulary
    on every recruited companion, so an otherwise-quiet starter campaign now has gauged
    companions with no personal quest arc -> companion_quest_unauthored. Author one arc per
    party companion so the digest is genuinely empty (NOT a weakened assertion — a truly
    engaged fixture)."""
    c = store.load_campaign(cid)
    for char_id in c.party:
        comp = c.characters.get(char_id)
        if comp is None or getattr(comp, "kind", None) != "companion":
            continue
        arc = CompanionQuestArc(companion_id=comp.id, title=f"{comp.name}'s personal thread")
        c.companion_quest_arcs[arc.id] = arc
    store.save_campaign(c)


def test_persist_beat_omits_obligations_key_on_healthy_fixture(cid):
    """The starter fixture, once its gauged companions are given personal quest arcs, is
    healthy/early -> no obligations key (the four-key additive shape is preserved)."""
    _author_quest_arcs_for_party_companions(cid)
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
    _author_quest_arcs_for_party_companions(cid)
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


def _betrayer(attitude, threshold=-20, fired=False, decision_flag="") -> Character:
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
    threshold -20) -> the DM is cued to foreshadow the fracture this beat."""
    comp = _betrayer(attitude=-30, threshold=-20)
    c = _campaign_with(comp, day=4)
    appr = [o for o in server._compute_beat_obligations(c)
            if o["kind"] == "companion_betrayal_approaching"]
    assert appr, "a curdled betrayal agenda must surface a cue"
    o = appr[0]
    assert o["name"] == "Sergeant Ondine Marsh"
    assert o["attitude_value"] == -30 and o["threshold"] == -20
    assert o["deep_red"] is False  # -30 has not yet passed the deep-red marker (-40)
    assert "REAL attack" in o["detail"]


def test_betrayal_approaching_silent_above_the_breaking_point():
    """The telegraph anchors to the agenda's OWN threshold: a bond that has soured (regard -10)
    but NOT yet crossed its breaking point (-20) is above the threshold -> not live -> no cue.
    (A NEGATIVE threshold is the correct shape — betrayal needs the bond to actually go bad.)"""
    comp = _betrayer(attitude=-10, threshold=-20)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


# The warm (positive) threshold is the DELIBERATE point of this one test — the model validator
# rightly warns that a positive threshold is an authoring footgun, but here we exercise the
# engine's defensive robustness (telegraph even a warm-threshold agenda once live), so suppress it.
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_betrayal_approaching_fires_for_a_warm_threshold_agenda_once_live():
    """Regression for the silent-snap window: a content agenda with a warm (positive) threshold
    that is LIVE (regard below it) NOW telegraphs instead of firing from nowhere — the anchor fix."""
    comp = _betrayer(attitude=10, threshold=18)  # live (10 < 18)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_for_warm_companion():
    """The gs-ledger-betray case: even a 'cruel' player ran a liberatory arc, regard rose to
    50 -> the agenda never went live and the cue does NOT false-fire."""
    comp = _betrayer(attitude=50, threshold=-20)
    c = _campaign_with(comp, day=6)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_without_agenda():
    """A loyalty-only companion (no betrayal agenda) is never flagged, however soured."""
    comp = _companion(name="Sergeant Ondine Marsh", attitude=-30, last_long_rest_day=4)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_silent_when_agenda_already_fired():
    """A FIRED agenda is the event itself, not a warning -> never re-cued as approaching."""
    comp = _betrayer(attitude=-50, threshold=-20, fired=True)
    c = _campaign_with(comp, day=4)
    assert "companion_betrayal_approaching" not in _kinds(server._compute_beat_obligations(c))


def test_betrayal_approaching_marks_deep_red_and_decision_flag():
    """Deep red (regard past -40) + a recorded decision_flag spike both surface — severity
    rises to high and the cue tells the DM to foreshadow harder."""
    comp = _betrayer(attitude=-45, threshold=-20, decision_flag="took_ferreths_coin")
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
    past its breaking point into the warning band (regard -30, threshold -20)."""
    c = store.load_campaign(cid)
    comp = Character(
        name="Sergeant Ondine Marsh",
        kind="companion",
        attitude_value=-30,
        arc=CompanionArc(agenda=CompanionAgenda(trigger="attitude_below", value=-20)),
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


# --- act-transition cues (the engine-owned 3-act cursor) --------------------
#
# Three mutually-exclusive cues read the NarrativeArc cursor (act / day_act_entered /
# beats_in_act / landed flags). At most ONE fires per beat (the cursor is a single integer
# act). An arc-less / default campaign (act=1, day_in_act=0, beats_in_act=0) adds NONE of
# them — the additive/empty contract (byte-identical empty digest).


_ACT_KINDS = {"act_midpoint_owed", "act_climax_owed", "act_one_stalled"}


def test_act_midpoint_owed_fires_in_act2_when_reversal_unlanded():
    """Act 2, reversal not landed, far enough in (day_in_act>=2) -> the midpoint reversal is
    owed (severity med)."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=2, day_act_entered=1)
    c.day = 4  # day_in_act == 3 (>= 2)
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "act_midpoint_owed" in kinds
    owed = next(o for o in obligations if o["kind"] == "act_midpoint_owed")
    assert owed["act"] == 2
    assert owed["severity"] == "med"
    assert "REVERSAL" in owed["detail"] or "reversal" in owed["detail"].lower()
    # mutually exclusive: no other act cue
    assert kinds & _ACT_KINDS == {"act_midpoint_owed"}


def test_act_midpoint_owed_fires_on_beats_threshold():
    """beats_in_act>=4 trips it even on the day it was entered (day_in_act==0)."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=2, day_act_entered=1, beats_in_act=4)
    c.day = 1  # day_in_act == 0, but beats_in_act >= 4
    assert "act_midpoint_owed" in _kinds(server._compute_beat_obligations(c))


def test_act_midpoint_owed_absent_once_reversal_landed():
    """Once the engine stamps midpoint_reversal_landed, the cue clears."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=2, day_act_entered=1, midpoint_reversal_landed=True)
    c.day = 6
    assert "act_midpoint_owed" not in _kinds(server._compute_beat_obligations(c))


def test_act_midpoint_owed_silent_early_in_act2():
    """Just entered Act 2 (day_in_act<2, beats_in_act<4) -> not yet owed."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=2, day_act_entered=3, beats_in_act=1)
    c.day = 3  # day_in_act == 0
    assert "act_midpoint_owed" not in _kinds(server._compute_beat_obligations(c))


def test_act_climax_owed_fires_in_act3_high_severity():
    """Act 3, climax unlanded, day_in_act>=2 -> the climax is owed (severity HIGH)."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=1)
    c.day = 3  # day_in_act == 2 (>= 2)
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "act_climax_owed" in kinds
    owed = next(o for o in obligations if o["kind"] == "act_climax_owed")
    assert owed["act"] == 3
    assert owed["severity"] == "high"
    assert "CLIMAX" in owed["detail"] or "climax" in owed["detail"].lower()
    assert kinds & _ACT_KINDS == {"act_climax_owed"}


def test_act_climax_owed_absent_once_climax_landed():
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=1, climax_landed=True)
    c.day = 8
    assert "act_climax_owed" not in _kinds(server._compute_beat_obligations(c))


def test_act_one_stalled_fires_when_setup_runs_long_by_days():
    """An ENGINE-DRIVEN Act 1 (beats bumped) that has run long on the day-clock
    (day_in_act>=4, below the beats threshold) -> the setup has run long (severity med).
    beats_in_act>0 means the engine engaged the arc, so the day-band counts."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=1, day_act_entered=1, beats_in_act=3)
    c.day = 5  # day_in_act == 4 (>= _ACT1_STALL_DAYS), beats 3 (< _ACT1_STALL_BEATS)
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "act_one_stalled" in kinds
    stalled = next(o for o in obligations if o["kind"] == "act_one_stalled")
    assert stalled["act"] == 1
    assert stalled["severity"] == "med"
    assert "advance_act" in stalled["detail"] or "Act 1" in stalled["detail"]
    assert kinds & _ACT_KINDS == {"act_one_stalled"}


def test_act_one_stalled_fires_on_beats_threshold():
    """beats_in_act>=8 trips it even early in the day-clock."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=1, day_act_entered=1, beats_in_act=8)
    c.day = 1  # day_in_act == 0, but beats_in_act >= _ACT1_STALL_BEATS
    assert "act_one_stalled" in _kinds(server._compute_beat_obligations(c))


def test_act_one_not_stalled_when_setup_is_fresh():
    """A fresh Act 1 (day_in_act<4, beats_in_act<8) does NOT nag."""
    c = Campaign(title="Arc")
    c.narrative_arc = NarrativeArc(act=1, day_act_entered=1, beats_in_act=3)
    c.day = 3  # day_in_act == 2
    assert "act_one_stalled" not in _kinds(server._compute_beat_obligations(c))


def test_arcless_default_campaign_yields_no_act_cues():
    """The ADDITIVE/EMPTY contract: a default-arc campaign (act=1, day_in_act=0,
    beats_in_act=0) trips NONE of the three act cues -> byte-identical empty digest."""
    c = Campaign(title="Arc")  # default narrative_arc
    c.day = 1
    obligations = server._compute_beat_obligations(c)
    assert _kinds(obligations) & _ACT_KINDS == set()
    # A healthy, companion-less, default-arc beat is still fully empty.
    assert obligations == []


# --- quest_stalled BEATS-reach (#1286) --------------------------------------
#
# The measured rri-a1-duo2 defect: a 22-beat run kept an active quest alive while the DM never
# called a progress verb AND never advanced the in-world clock, so the day-only quest_stalled gate
# (day - last_progress_day >= 3) was structurally UNREACHABLE. The beats-reach mirrors camp_overdue
# / act_one_stalled: a quest that got no progress across _QUEST_STALL_BEATS+ beats surfaces even at
# day 1. Quest.last_progress_beat is the engine-mutated baseline (stamped wherever last_progress_day
# is), so the cue reads engine state, never Decision prose (invariant 3).


def _stuck_clock_campaign(beats_now: int, day: int = 1) -> Campaign:
    """A campaign whose in-world clock never advanced (day fixed) but that has PLAYED beats_now
    beats — the exact rri-a1-duo2 shape."""
    c = Campaign(title="Stuck")
    c.day = day
    c.narrative_arc = NarrativeArc(act=1, beats_in_act=beats_now)
    return c


def test_quest_stalled_fires_by_beats_when_clock_is_stuck():
    """22 beats in, an active quest last advanced at beat 0, day never moved -> stalled by beats
    even though day - last_progress_day == 0 (the day gate can't see it)."""
    c = _stuck_clock_campaign(beats_now=22, day=1)
    q = Quest(title="Dresh's Lost Wagon", objectives=["find the wagon"],
              last_progress_day=1, last_progress_beat=0)
    c.quests[q.id] = q
    obligations = server._compute_beat_obligations(c)
    assert "quest_stalled" in _kinds(obligations)
    stalled = next(o for o in obligations if o["kind"] == "quest_stalled")
    assert stalled["title"] == "Dresh's Lost Wagon"
    assert "beats" in stalled["detail"]  # beats phrasing when the day gate didn't drive


def test_quest_stalled_not_fired_by_beats_below_threshold():
    """Just under _QUEST_STALL_BEATS since last progress -> not yet stalled (no false-early cue)."""
    c = _stuck_clock_campaign(beats_now=server._QUEST_STALL_BEATS - 1, day=1)
    q = Quest(title="A fresh thread", objectives=["begin"],
              last_progress_day=1, last_progress_beat=0)
    c.quests[q.id] = q
    assert "quest_stalled" not in _kinds(server._compute_beat_obligations(c))


def test_quest_stalled_beats_silent_when_progress_is_recent():
    """A quest advanced at the CURRENT beat is not stalled by beats even after a long run."""
    c = _stuck_clock_campaign(beats_now=30, day=1)
    q = Quest(title="Actively worked", objectives=["push"],
              last_progress_day=1, last_progress_beat=30)  # advanced this beat
    c.quests[q.id] = q
    assert "quest_stalled" not in _kinds(server._compute_beat_obligations(c))


def test_quest_stalled_day_reach_still_fires_and_reads_days():
    """The original day-reach is preserved: a 3+ day-stale quest still fires with the DAYS phrasing
    (the beats baseline is unset / -1 on an old snapshot, so only the day gate drives)."""
    c = Campaign(title="Clock moves")
    c.day = 8
    q = Quest(title="The Long Road", objectives=["reach the keep"], last_progress_day=4)
    c.quests[q.id] = q
    obligations = server._compute_beat_obligations(c)
    assert "quest_stalled" in _kinds(obligations)
    stalled = next(o for o in obligations if o["kind"] == "quest_stalled")
    assert "days" in stalled["detail"]


def test_quest_stalled_beats_never_fires_on_unstamped_old_snapshot():
    """ADDITIVE: a quest from an old snapshot (last_progress_beat == -1 default) never trips the
    beats path — behavior is byte-identical to today for pre-#1286 state."""
    c = _stuck_clock_campaign(beats_now=50, day=1)
    q = Quest(title="Old snapshot quest", objectives=["x"], last_progress_day=1)  # beat unset (-1)
    c.quests[q.id] = q
    assert "quest_stalled" not in _kinds(server._compute_beat_obligations(c))


def test_add_quest_stamps_last_progress_beat(tmp_path, monkeypatch):
    """add_quest must seed last_progress_beat from the current beat tally so the stall-by-beats
    clock starts at arrival, not at beat 0 (a late-added quest isn't instantly stale)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    c = store.load_campaign(cid)
    c.narrative_arc.beats_in_act = 15
    store.save_campaign(c)
    qid = server.add_quest(cid, title="A late quest", objectives=["go"])["id"]
    c = store.load_campaign(cid)
    assert c.quests[qid].last_progress_beat == 15  # seeded from the live tally, not 0


# --- faction_joinable_unjoined (#1286) --------------------------------------
#
# The rri-a1-duo2 defect also named a "seeded faction never joined". A Faction that carries an
# AUTHORED questline (questline_arc_id set -> a deliberate join->grow->lead FactionArc) but is
# still un-joined a stretch of beats in gets a cue to enlist. questline_arc_id is the false-
# positive-resistant signal: a plain flavour faction (no arc) is NEVER a join obligation.


def _campaign_with_faction_arc(joined: bool, beats: int = server._FACTION_JOIN_BEATS) -> Campaign:
    c = Campaign(title="Factions")
    c.narrative_arc = NarrativeArc(act=1, beats_in_act=beats)
    fac = Faction(name="The Emberwardens", joined=joined)
    arc = FactionArc(faction_id=fac.id, title="Rise of the Emberwardens")
    fac.questline_arc_id = arc.id
    c.factions[fac.id] = fac
    c.faction_arcs[arc.id] = arc
    return c


def test_faction_joinable_unjoined_fires_for_seeded_unjoined_faction():
    c = _campaign_with_faction_arc(joined=False)
    obligations = server._compute_beat_obligations(c)
    assert "faction_joinable_unjoined" in _kinds(obligations)
    cue = next(o for o in obligations if o["kind"] == "faction_joinable_unjoined")
    assert cue["name"] == "The Emberwardens"
    assert "join_faction" in cue["detail"]


def test_faction_joinable_unjoined_silent_once_joined():
    c = _campaign_with_faction_arc(joined=True)
    assert "faction_joinable_unjoined" not in _kinds(server._compute_beat_obligations(c))


def test_faction_joinable_unjoined_silent_for_flavour_faction_without_arc():
    """A faction with NO authored questline is not a join obligation (the party may never enlist)."""
    c = Campaign(title="Factions")
    c.narrative_arc = NarrativeArc(act=1, beats_in_act=20)
    fac = Faction(name="Town Guard")  # no questline_arc_id
    c.factions[fac.id] = fac
    assert "faction_joinable_unjoined" not in _kinds(server._compute_beat_obligations(c))


def test_faction_joinable_unjoined_silent_below_beats_threshold():
    """Below _FACTION_JOIN_BEATS the cue is silent — authoring the arc doesn't nag same-beat."""
    c = _campaign_with_faction_arc(joined=False, beats=server._FACTION_JOIN_BEATS - 1)
    assert "faction_joinable_unjoined" not in _kinds(server._compute_beat_obligations(c))


def test_faction_joinable_unjoined_silent_when_arc_id_dangles():
    """A questline_arc_id pointing at a MISSING FactionArc (partial/older state) degrades to silent,
    never raises — matches the defensive contract of every other cue."""
    c = Campaign(title="Factions")
    c.narrative_arc = NarrativeArc(act=1, beats_in_act=20)
    fac = Faction(name="Ghost Order")
    fac.questline_arc_id = "farc_missing"  # no matching arc in c.faction_arcs
    c.factions[fac.id] = fac
    assert "faction_joinable_unjoined" not in _kinds(server._compute_beat_obligations(c))


def test_faction_cue_absent_on_factionless_campaign():
    """ADDITIVE/EMPTY: a campaign with no factions never trips the faction cue (the block adds
    nothing). (beats_in_act is high here only to clear the beats gate, so act_one_stalled may
    fire — that's the arc cue, not ours; we assert only the faction kind is absent.)"""
    c = Campaign(title="Empty")
    c.narrative_arc = NarrativeArc(act=1, beats_in_act=20)
    assert "faction_joinable_unjoined" not in _kinds(server._compute_beat_obligations(c))
