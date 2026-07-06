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
    Combat,
    Combatant,
    CompanionAgenda,
    CompanionArc,
    CompanionDossier,
    CompanionQuestArc,
    Consequence,
    Faction,
    FactionArc,
    Location,
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


# --- #1313 (cue-half iteration 2b): the ENDGAME quest-resolution cue -----------------------------
#
# The measured residual RED after Option 3 (rri-a1-opt3, 25 clean beats): engagement rose broadly
# but a quest stayed ACTIVE at session end (reads as a dropped thread). Iteration 2 (#1317) opened
# the wrap window on `act == 3 AND climax_landed`. Both halves are OPTIONAL DM calls (advance_act /
# mark_climax): the rri-a1-gate run advanced neither, the window never opened, and the cue fired 0
# times (behavioral AMBER — the same engagement-variance defect one level up).
#
# 2b re-derives the window from the ONE skip-proof gauge — `beats_in_act`, which persist_beat bumps
# every mandatory beat — while keeping `climax_landed` as the semantically-precise fast path:
#
#     _in_wrap_window = climax_landed OR (act >= 2 AND beats_in_act >= _WRAP_WINDOW_BEATS)
#
# Threshold _WRAP_WINDOW_BEATS = 8, calibrated from the 4 real snapshots that reached session-wrap
# (engine-authoritative narrative_arc; format act/beats_in_act/climax_landed):
#   rri-a1-gate  2 /  8 / False  — the behavioral RED; MUST open via the counter floor (no climax) → 8 is the ceiling for T
#   rri-a1-opt3  1 / 16 / True   — opens via climax_landed (act 1: the counter floor deliberately does NOT reach it → act>=2)
#   rri-a1-duo3  2 / 10 / False  — opens via the counter floor (harmless: no quests)
#   rri-a1-duo4  2 / 10 / True   — opens via climax_landed (harmless: the quest is completed)
# T=8 is the unique maximal-yet-sufficient floor: the gate run at beats_in_act=8 with no climax pins
# T<=8, and opt3 opens via the climax fast-path so lowering T buys nothing but a wider mid-act false-
# open surface. The `act >= 2` guard keeps opt3's long act-1 slog (beats=16, act_one_stalled
# territory) from being MIS-read as "wrapping" — in act 1 only the explicit climax flag opens it.
# T=8 also sits +4 above act_climax_owed's own beats_in_act>=4 trigger, so in an act-3 climax
# build-up act_climax_owed owns beats 4-7 alone and the endgame floor only joins from beat 8.
#
# In the window an active quest escalates to a single HIGH quest_endgame_unresolved cue that REPLACES
# the generic resolvable/stalled/unresolved_late cues, so next_action names quest CLOSURE as the
# imperative in the final beats. NO teeth (advisory only). Precedence with act_climax_owed (which
# fires in a counter-opened act-3 window while climax is NOT landed): BOTH are legitimately owed —
# land the climax FIRST, then close the thread — so both stay HIGH and neither is suppressed, with an
# explicit intra-tier tiebreak (act_climax_owed sorts before quest_endgame_unresolved).


def _wrap_window_campaign(active_all_objectives_done: bool = True) -> Campaign:
    """The wrap window opened via the climax fast-path (act 3, climax LANDED, past the counter floor)
    plus one still-ACTIVE quest. The objectives are all-done by default (the resolvable shape) so the
    pre-endgame code path would have fired quest_resolvable — proving the endgame cue REPLACES it."""
    c = Campaign(title="Endgame")
    c.day = 6
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=5, beats_in_act=8,
                                   midpoint_reversal_landed=True, climax_landed=True)
    objectives = ["find the relic"]
    completed = ["find the relic"] if active_all_objectives_done else []
    q = Quest(title="The Debt of Bresser Oln", objectives=objectives,
              completed_objectives=completed, last_progress_day=6)
    c.quests[q.id] = q
    return c


def test_quest_endgame_unresolved_fires_in_wrap_window_for_active_quest():
    """Wrap window (act 3, climax landed) + an active quest -> a single HIGH quest_endgame_unresolved
    cue naming the quest, with the resolution imperative."""
    c = _wrap_window_campaign()
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    assert "quest_endgame_unresolved" in kinds
    cue = next(o for o in obligations if o["kind"] == "quest_endgame_unresolved")
    assert cue["severity"] == "high"
    assert cue["title"] == "The Debt of Bresser Oln"
    assert "The Debt of Bresser Oln" in cue["detail"]
    assert "wrapping" in cue["detail"]
    # The resolution imperative names all three sanctioned closures.
    assert "complete_quest" in cue["detail"]
    assert "complete_objective" in cue["detail"]
    assert "add_consequence" in cue["detail"]


def test_quest_endgame_unresolved_becomes_the_next_action_imperative():
    """Because it is HIGH and obligations are severity-sorted, the endgame cue is lifted into
    next_action as THE imperative in the final beats."""
    c = _wrap_window_campaign()
    obligations = server._compute_beat_obligations(c)
    na = server._next_action(obligations)
    assert na is not None
    assert na["kind"] == "quest_endgame_unresolved"
    assert na["severity"] == "high"
    assert na["imperative"] == obligations[0]["detail"]


def test_quest_endgame_replaces_resolvable_no_double_fire():
    """PRECEDENCE: the wrap-window cue REPLACES quest_resolvable/quest_stalled for the same quest —
    one cue, not two/three — so next_action is a single resolution directive."""
    c = _wrap_window_campaign(active_all_objectives_done=True)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" in kinds
    assert "quest_resolvable" not in kinds
    assert "quest_stalled" not in kinds
    assert "quest_unresolved_late" not in kinds


def test_quest_endgame_replaces_unresolved_late_when_nothing_progressed():
    """Even when NO objective was ever recorded done (the quest_unresolved_late shape), the wrap-
    window cue owns the quest — quest_unresolved_late stays silent (no double-fire)."""
    c = _wrap_window_campaign(active_all_objectives_done=False)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" in kinds
    assert "quest_unresolved_late" not in kinds
    assert "quest_stalled" not in kinds


def test_quest_endgame_absent_outside_wrap_window_climax_not_landed_below_counter():
    """No climax landed AND below the counter floor (beats_in_act < _WRAP_WINDOW_BEATS) -> NOT the
    wrap window. The endgame cue is silent; the generic quest cue path still owns the active quest.
    This is a genuine mid-act-3 climax build-up, which act_climax_owed (not endgame) should own."""
    c = _wrap_window_campaign()
    c.narrative_arc.climax_landed = False  # climax still owed
    c.narrative_arc.beats_in_act = server._WRAP_WINDOW_BEATS - 2  # below the counter floor
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" not in kinds
    # The pre-endgame path still surfaces the ripe active quest (all objectives done -> resolvable).
    assert "quest_resolvable" in kinds


def test_quest_endgame_opens_via_counter_without_climax_landed():
    """2b CORE FIX (the rri-a1-gate shape): act 3, climax NOT landed, but beats_in_act has reached
    the counter floor -> the wrap window OPENS on the skip-proof counter, so the endgame cue fires
    even though the DM never called mark_climax."""
    c = _wrap_window_campaign()
    c.narrative_arc.climax_landed = False           # DM never marked the climax (the gate defect)
    c.narrative_arc.beats_in_act = server._WRAP_WINDOW_BEATS  # but the mandatory counter reached the floor
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" in kinds


def test_quest_endgame_counter_floor_calibrates_to_the_gate_run():
    """The threshold is pinned by rri-a1-gate (act 2, beats_in_act=8, no climax, quest active) — the
    behavioral RED that MUST open via the counter. Reproduce that exact snapshot shape."""
    c = Campaign(title="Gate run")
    c.day = 7
    c.narrative_arc = NarrativeArc(act=2, day_act_entered=4, beats_in_act=8,
                                   midpoint_reversal_landed=True, climax_landed=False)
    q = Quest(title="The Price of Silence", objectives=["a", "b"],
              completed_objectives=["a"], last_progress_day=7)
    c.quests[q.id] = q
    assert "quest_endgame_unresolved" in _kinds(server._compute_beat_obligations(c))


def test_quest_endgame_counter_floor_does_not_open_in_act1_slog():
    """The `act >= 2` guard: a long act-1 slog (the rri-a1-opt3 pre-climax shape — beats_in_act well
    past the floor but STILL in act 1) is act_one_stalled territory, NOT wrapping. Without the climax
    flag, the counter floor must NOT open the endgame cue in act 1."""
    c = _wrap_window_campaign()
    c.narrative_arc.act = 1
    c.narrative_arc.climax_landed = False
    c.narrative_arc.beats_in_act = 16  # opt3's act-1 beats — long, but NOT wrapping
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" not in kinds


def test_quest_endgame_climax_fastpath_opens_in_act2():
    """The climax_landed FAST PATH is act-agnostic (>= act 2): the rri-a1-duo4 shape (act 2, climax
    landed) opens the window via the flag regardless of the counter — climax landed IS "past the
    peak, now closing"."""
    c = _wrap_window_campaign()
    c.narrative_arc.act = 2
    c.narrative_arc.climax_landed = True
    c.narrative_arc.beats_in_act = 3  # below the counter floor, but the climax flag opens it
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" in kinds


def test_quest_endgame_absent_when_all_quests_resolved_in_wrap_window():
    """In the wrap window with NO active quest (the only quest is completed) -> nothing owed. The
    endgame cue is absent (it fires only on an ACTIVE thread left open at wrap)."""
    c = Campaign(title="Endgame clean")
    c.day = 6
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=5, beats_in_act=6,
                                   midpoint_reversal_landed=True, climax_landed=True)
    done = Quest(title="A resolved thread", status="completed", objectives=["x"],
                 completed_objectives=["x"], evolves_to="a lingering echo")
    c.quests[done.id] = done
    assert "quest_endgame_unresolved" not in _kinds(server._compute_beat_obligations(c))


def test_quest_endgame_byte_identical_empty_digest_in_wrap_window_with_no_quests():
    """The ADDITIVE contract holds in the wrap window: a healthy act-3 climax-landed beat with no
    quests and no other owed cue yields the byte-identical empty digest (no obligations key)."""
    c = Campaign(title="Endgame empty")
    c.day = 6
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=5, beats_in_act=6,
                                   midpoint_reversal_landed=True, climax_landed=True)
    assert server._compute_beat_obligations(c) == []


def test_quest_endgame_silent_for_arcless_campaign():
    """Defensive: an arc-less campaign (no narrative_arc attribute path) reads _in_wrap_window False
    and never trips the endgame cue, even with an active all-done quest."""
    c = Campaign(title="No arc")
    c.day = 6
    c.narrative_arc = None
    q = Quest(title="Loose thread", objectives=["x"], completed_objectives=["x"])
    c.quests[q.id] = q
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_endgame_unresolved" not in kinds


def test_quest_endgame_precedence_climax_owed_sorts_first_when_both_owed():
    """PRECEDENCE (2b): in a counter-opened act-3 window where the climax is NOT landed, BOTH
    act_climax_owed AND quest_endgame_unresolved are legitimately owed. Neither is suppressed; both
    are HIGH; but act_climax_owed sorts FIRST (land the payoff, THEN close the thread) — so it, not
    the endgame cue, becomes the next_action imperative."""
    c = _wrap_window_campaign()
    c.narrative_arc.climax_landed = False               # climax still owed
    c.narrative_arc.beats_in_act = server._WRAP_WINDOW_BEATS  # counter floor reached -> window open
    obligations = server._compute_beat_obligations(c)
    kinds = _kinds(obligations)
    # BOTH owed — neither suppressed.
    assert "act_climax_owed" in kinds
    assert "quest_endgame_unresolved" in kinds
    # Both HIGH severity.
    assert next(o for o in obligations if o["kind"] == "act_climax_owed")["severity"] == "high"
    assert next(o for o in obligations if o["kind"] == "quest_endgame_unresolved")["severity"] == "high"
    # act_climax_owed sorts strictly before quest_endgame_unresolved.
    climax_i = next(i for i, o in enumerate(obligations) if o["kind"] == "act_climax_owed")
    endgame_i = next(i for i, o in enumerate(obligations) if o["kind"] == "quest_endgame_unresolved")
    assert climax_i < endgame_i
    # The climax is the payoff -> it, not the closure cue, is THE next_action.
    na = server._next_action(obligations)
    assert na["kind"] == "act_climax_owed"


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


# === WS3a — DM-unavoidable PER-BEAT PROGRESSION / CLOSURE cues =====================================
#
# Five engine-gauge cues that keep the HARD mechanical loop from quietly stalling: a party stuck in
# one scene, a fight left hanging, XP that never landed, a frozen clock, a quest never resolved. Each
# reads only an ENGINE-MUTATED gauge (never a tool-count / beat-history / Decision prose). Each gets a
# FIRE test (owed gauge -> kind present) + a CLEAR test (move the gauge -> kind gone).


def _location(name="Opening Scene", visited=True, connections=None):
    return Location(name=name, visited=visited, connections=list(connections or []))


def _arced_campaign(*members: Character, beats_in_act=8, day=1, act=1) -> Campaign:
    """A campaign whose narrative arc has been DRIVEN beats_in_act beats — the _beats_in_act
    signal the WS3a beats-gated cues (party_stuck, clock_dm_frozen, quest_unresolved_late) read."""
    c = _campaign_with(*members, day=day)
    c.narrative_arc = NarrativeArc(act=act, day_act_entered=1, beats_in_act=beats_in_act)
    return c


# --- WS3a-1. party_stuck_one_location --------------------------------------------------------------


def test_party_stuck_one_location_fires_after_substantial_beats_in_one_scene():
    """8+ act-local beats, only one visited location, no in-place progression -> the cue fires."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    loc = _location(visited=True)
    c.locations[loc.id] = loc
    c.current_location_id = loc.id
    cue = next((o for o in server._compute_beat_obligations(c)
                if o["kind"] == "party_stuck_one_location"), None)
    assert cue is not None
    assert cue["severity"] == "med"
    assert "travel_to" in cue["detail"]


def test_party_stuck_one_location_clears_once_a_second_location_is_visited():
    """Moving the visited gauge to >= 2 clears the cue (the party traveled)."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    a, b = _location("A", visited=True), _location("B", visited=True)
    c.locations[a.id] = a
    c.locations[b.id] = b
    assert "party_stuck_one_location" not in _kinds(server._compute_beat_obligations(c))


def test_party_stuck_one_location_silent_below_the_beats_threshold():
    """A short run (< _PARTY_STUCK_BEATS) in one scene is a legitimate vignette, not a stall."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=7, day=1)
    loc = _location(visited=True)
    c.locations[loc.id] = loc
    assert "party_stuck_one_location" not in _kinds(server._compute_beat_obligations(c))


def test_party_stuck_one_location_silent_under_in_place_progression_exception():
    """The byte-identical assert_behavioral exception: visited>=1 AND clock advanced AND a quest
    actually completed AND beats>=8 -> a complete single-scene drama, NOT a stuck stall."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=9, day=2)  # clock advanced (day>1)
    loc = _location(visited=True)
    c.locations[loc.id] = loc
    q = Quest(title="A single-scene resolution", status="completed",
              objectives=["x"], completed_objectives=["x"])
    c.quests[q.id] = q
    assert "party_stuck_one_location" not in _kinds(server._compute_beat_obligations(c))


# --- WS3a-2. combat_left_hanging -------------------------------------------------------------------


def _combat_with_monster(monster: Character) -> Combat:
    return Combat(active=True, order=[Combatant(character_id=monster.id, initiative=10)])


def test_combat_left_hanging_fires_when_active_with_no_living_hostile():
    """Combat active but the only hostile in the order is dead -> cue end_combat."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = _combat_with_monster(rat)
    cue = next((o for o in server._compute_beat_obligations(c)
                if o["kind"] == "combat_left_hanging"), None)
    assert cue is not None
    assert cue["severity"] == "med"
    assert "end_combat" in cue["detail"]


def test_combat_left_hanging_clears_while_a_living_hostile_remains():
    """A monster still up at >0 HP keeps the fight legitimately live -> no cue."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=False, current_hp=7, max_hp=7)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = _combat_with_monster(rat)
    assert "combat_left_hanging" not in _kinds(server._compute_beat_obligations(c))


def test_combat_left_hanging_silent_when_combat_inactive():
    """No active combat -> the cue never fires (and xp_unawarded owns the post-fight case)."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = Combat(active=False)
    assert "combat_left_hanging" not in _kinds(server._compute_beat_obligations(c))


def test_combat_left_hanging_owns_the_beat_over_xp_unawarded_while_combat_active():
    """PRECEDENCE: while combat is active, a dead monster carrying XP surfaces combat_left_hanging,
    NOT xp_unawarded (xp_unawarded is gated NON-combat only)."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=25)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = _combat_with_monster(rat)
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "combat_left_hanging" in kinds
    assert "xp_unawarded" not in kinds


# --- WS3a-3. xp_unawarded --------------------------------------------------------------------------


def test_xp_unawarded_fires_for_defeated_monster_carrying_xp_out_of_combat():
    """xp-mode, non-combat, living party member, dead monster with xp_value>0 -> cue."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=25)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = Combat(active=False)
    cue = next((o for o in server._compute_beat_obligations(c)
                if o["kind"] == "xp_unawarded"), None)
    assert cue is not None
    assert cue["severity"] == "med"
    assert "award_xp" in cue["detail"]


def test_xp_unawarded_clears_once_xp_value_is_zeroed():
    """Moving the gauge (xp_value -> 0, as the kill-time / end_combat award does) clears the cue."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=0)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = Combat(active=False)
    assert "xp_unawarded" not in _kinds(server._compute_beat_obligations(c))


def test_xp_unawarded_silent_in_milestone_mode():
    """leveling_mode='milestone' has no auto-XP, so a dead monster's xp_value is irrelevant."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=25)
    c = _campaign_with(pc, day=1)
    c.leveling_mode = "milestone"
    c.characters[rat.id] = rat
    c.combat = Combat(active=False)
    assert "xp_unawarded" not in _kinds(server._compute_beat_obligations(c))


def test_xp_unawarded_silent_after_a_tpk_no_living_party_member():
    """After a total party wipe (no living party member) a dead monster keeping xp is a legitimate
    'awarded to no one' state -> mirror the xp_not_orphaned FATAL's party_alive guard."""
    pc = Character(name="Hero", kind="player", dead=True)
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=25)
    c = _campaign_with(pc, day=1)
    c.characters[rat.id] = rat
    c.combat = Combat(active=False)
    assert "xp_unawarded" not in _kinds(server._compute_beat_obligations(c))


# --- WS3a-4. clock_dm_frozen -----------------------------------------------------------------------


def test_clock_dm_frozen_fires_when_party_moved_but_clock_stuck_at_day_one_morning():
    """Party has visited >= 2 (so party_stuck is silent) but the clock still reads day 1 morning
    after substantial play -> the LOW frozen-clock cue."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    c.time_of_day = "morning"
    a, b = _location("A", visited=True), _location("B", visited=True)
    c.locations[a.id] = a
    c.locations[b.id] = b
    c.combat = Combat(active=False)
    cue = next((o for o in server._compute_beat_obligations(c)
                if o["kind"] == "clock_dm_frozen"), None)
    assert cue is not None
    assert cue["severity"] == "low"
    assert "advance_time" in cue["detail"]


def test_clock_dm_frozen_clears_once_the_clock_advances():
    """Moving the clock gauge (day>1 OR time_of_day past morning) clears the cue."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=2)  # day advanced
    c.time_of_day = "afternoon"
    a, b = _location("A", visited=True), _location("B", visited=True)
    c.locations[a.id] = a
    c.locations[b.id] = b
    assert "clock_dm_frozen" not in _kinds(server._compute_beat_obligations(c))


def test_clock_dm_frozen_yields_to_party_stuck_when_both_would_fire():
    """PRECEDENCE: a party stuck in ONE scene with a frozen clock surfaces party_stuck_one_location,
    NOT clock_dm_frozen (which requires visited >= 2)."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    c.time_of_day = "morning"
    loc = _location(visited=True)
    c.locations[loc.id] = loc
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "party_stuck_one_location" in kinds
    assert "clock_dm_frozen" not in kinds


# --- WS3a-5. quest_unresolved_late -----------------------------------------------------------------


def test_quest_unresolved_late_fires_when_no_quest_progress_after_substantial_beats():
    """A quest exists, ZERO quests completed, no completed_objectives anywhere, and it isn't already
    flagged resolvable/stalled -> cue recording SOME quest progress."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    q = Quest(title="The untouched thread", objectives=["step one", "step two"])
    c.quests[q.id] = q
    cue = next((o for o in server._compute_beat_obligations(c)
                if o["kind"] == "quest_unresolved_late"), None)
    assert cue is not None
    assert cue["severity"] == "med"
    assert "complete_objective" in cue["detail"]


def test_quest_unresolved_late_clears_once_an_objective_is_recorded_done():
    """Moving the gauge (one completed_objective) clears the late-unresolved cue."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    q = Quest(title="A progressing thread", objectives=["step one", "step two"],
              completed_objectives=["step one"])
    c.quests[q.id] = q
    assert "quest_unresolved_late" not in _kinds(server._compute_beat_obligations(c))


def test_quest_unresolved_late_silent_when_a_quest_is_already_flagged_this_beat():
    """ANTI-SPAM precedence: a quest already surfaced as quest_resolvable (all objectives done)
    suppresses the campaign-level 'nothing moved' cue."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    # A resolvable quest -> quest_resolvable fires AND it has completed_objectives, so the
    # any_objective_completed branch is also false; assert the anti-spam gate explicitly.
    q = Quest(title="Ripe", objectives=["x"], completed_objectives=["x"])
    c.quests[q.id] = q
    kinds = _kinds(server._compute_beat_obligations(c))
    assert "quest_resolvable" in kinds
    assert "quest_unresolved_late" not in kinds


def test_quest_unresolved_late_silent_below_the_beats_threshold():
    """A short run with an open quest is normal -> no late-unresolved cue."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=7, day=1)
    q = Quest(title="A young thread", objectives=["step one"])
    c.quests[q.id] = q
    assert "quest_unresolved_late" not in _kinds(server._compute_beat_obligations(c))


def test_quest_unresolved_late_silent_with_no_quests():
    """No quest exists -> the cue cannot fire (nothing to resolve)."""
    pc = Character(name="Hero", kind="player")
    c = _arced_campaign(pc, beats_in_act=8, day=1)
    assert "quest_unresolved_late" not in _kinds(server._compute_beat_obligations(c))


# --- the FULLY-PROGRESSED additive contract -------------------------------------------------------


def test_fully_progressed_snapshot_yields_no_obligations():
    """The ADDITIVE/EMPTY contract extended to WS3a: a snapshot that is fully progressed on EVERY
    WS3a axis — visited >= 2, combat closed, no orphaned XP, day > 1, a completed quest — AND on the
    pre-WS3a axes (gauged companion with a personal arc, recently rested, approval moved) yields the
    byte-identical empty digest (no obligations key)."""
    comp = _companion(name="Wyll", attitude=20, likes=["mercy"], last_long_rest_day=4)
    pc = Character(name="Hero", kind="player")
    # _beats_in_act >= _PARTY_STUCK_BEATS so the WS3a beats-gated cues are ARMED, but the arc is
    # otherwise healthy (Act 3, climax landed, fresh in act) so no act-transition cue fires either.
    c = _campaign_with(comp, pc, day=5)
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=5, beats_in_act=8,
                                   midpoint_reversal_landed=True, climax_landed=True)
    c.time_of_day = "afternoon"  # clock advanced
    # visited >= 2
    a, b = _location("A", visited=True), _location("B", visited=True)
    c.locations[a.id] = a
    c.locations[b.id] = b
    # combat closed; no orphaned XP (dead monster's xp already zeroed by the award)
    c.combat = Combat(active=False)
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=0)
    c.characters[rat.id] = rat
    # Two completed quests with echoes — at WRAP (act 3, climax landed) a fully-progressed snapshot
    # has CLOSED its threads; an ongoing quest left active here would be #1313 quest_endgame_unresolved
    # (correct: a thread left ACTIVE at wrap is owed), so "fully progressed" means resolved-not-dangling.
    done = Quest(title="A resolved thread", status="completed", objectives=["x"],
                 completed_objectives=["x"], evolves_to="a lingering echo")
    done2 = Quest(title="Another resolved thread", status="completed", objectives=["y"],
                  completed_objectives=["y"], evolves_to="another lingering echo")
    c.quests[done.id] = done
    c.quests[done2.id] = done2
    # the gauged companion owns a personal quest arc (WS-A/WS-C healthy fixture)
    arc = CompanionQuestArc(companion_id=comp.id, title="Wyll's personal thread")
    c.companion_quest_arcs[arc.id] = arc
    assert server._compute_beat_obligations(c) == []


# --- #1313 Option 3: severity-sort + `next_action` + persist_beat `owed` -----------------------
#
# The engagement machinery was surfaced every beat (the full `obligations` list) and the DM
# scanned past it (rri-a1-duo3: ZERO engagement tools across 25 clean beats). Option 3 lifts the
# SINGLE top-severity obligation into a named, imperative `next_action` on BOTH seams, and echoes
# the still-owed kinds back on the persist_beat return. Pure read, additive: no obligations ->
# byte-identical to today (no next_action / owed keys).


def _multi_severity_campaign() -> Campaign:
    """A snapshot that fires several obligations spanning ALL severity tiers, so the sort +
    top-lift are observable. Act 3 climax-owed = the lone `high`; a frozen gauged companion +
    an all-objectives-done quest = `med`; a resolved-no-echo quest = `low`."""
    comp = _companion(name="Shadowheart", attitude=0, likes=["mercy"], last_long_rest_day=4)
    pc = Character(name="Hero", kind="player")
    c = _campaign_with(comp, pc, day=5)
    # Act 3 with the climax still owed -> act_climax_owed (severity "high").
    c.narrative_arc = NarrativeArc(act=3, day_act_entered=3, beats_in_act=6,
                                   midpoint_reversal_landed=True, climax_landed=False)
    # med: companion_approval_frozen (gauged companion still at 0 on day 5).
    # med: quest_resolvable (all objectives done).
    resolvable = Quest(title="The ripe thread", objectives=["x"], completed_objectives=["x"])
    c.quests[resolvable.id] = resolvable
    # low: quest_no_echo (a resolved quest with empty evolves_to and no consequence).
    stale = Quest(title="A quiet win", status="completed", objectives=["y"],
                  completed_objectives=["y"], evolves_to="")
    c.quests[stale.id] = stale
    return c


def test_obligations_are_severity_sorted_high_first():
    """The digest is returned high>med>low so the caller can lift the top one."""
    c = _multi_severity_campaign()
    obligations = server._compute_beat_obligations(c)
    severities = [o["severity"] for o in obligations]
    # At least one of each tier is present, and they are non-increasing in urgency.
    assert "high" in severities and "med" in severities and "low" in severities
    rank = {"high": 0, "med": 1, "low": 2}
    ranks = [rank[s] for s in severities]
    assert ranks == sorted(ranks), f"obligations not severity-sorted: {severities}"


def test_next_action_is_the_top_severity_obligation():
    """`_next_action` lifts the single highest-priority obligation, reusing its own detail text
    as the imperative (no invented copy)."""
    c = _multi_severity_campaign()
    obligations = server._compute_beat_obligations(c)
    na = server._next_action(obligations)
    assert na is not None
    top = obligations[0]
    assert na["kind"] == top["kind"] == "act_climax_owed"
    assert na["severity"] == "high"
    # The imperative is the obligation's existing detail verbatim (reused, not invented).
    assert na["imperative"] == top["detail"]


def test_next_action_is_none_when_no_obligations():
    """No obligations -> no next_action (the additive default; empty digest -> no key)."""
    assert server._next_action([]) is None


def test_top_obligation_none_on_empty():
    assert server._top_obligation([]) is None


def test_scene_context_surfaces_next_action_when_actionable(cid):
    _make_frozen_run(cid)
    sc = server.scene_context(cid)
    durable = sc["durable"]
    assert "obligations" in durable
    na = durable["next_action"]
    # The lifted directive is the FIRST (top-severity) obligation, and its imperative is that
    # obligation's own detail.
    top = durable["obligations"][0]
    assert na["kind"] == top["kind"]
    assert na["imperative"] == top["detail"]


def test_scene_context_omits_next_action_on_healthy_fixture(cid):
    """Additive: a healthy fixture has no obligations -> no next_action key (today's shape)."""
    _author_quest_arcs_for_party_companions(cid)
    sc = server.scene_context(cid)
    assert "obligations" not in sc["durable"]
    assert "next_action" not in sc["durable"]


def test_persist_beat_surfaces_next_action_and_owed_when_actionable(cid):
    _make_frozen_run(cid)
    out = server.persist_beat(cid, events=[{"kind": "narration", "text": "The beat lands."}])
    assert "obligations" in out
    # next_action = the top obligation, echoing its own detail as the imperative.
    top = out["obligations"][0]
    assert out["next_action"]["kind"] == top["kind"]
    assert out["next_action"]["imperative"] == top["detail"]
    # `owed` echoes back every still-unmet obligation kind (consequence-of-inaction carried
    # forward), in the same severity-sorted order as the digest.
    assert out["owed"] == [o["kind"] for o in out["obligations"]]
    assert "companion_approval_frozen" in out["owed"]
    assert "quest_resolvable" in out["owed"]


def test_persist_beat_omits_next_action_and_owed_on_healthy_fixture(cid):
    """The ADDITIVE / byte-identical-when-empty invariant on the persist_beat return: a healthy
    beat carries NEITHER the obligations digest NOR the Option-3 keys — the return is exactly
    today's four-key shape (plus optional approval_results)."""
    _author_quest_arcs_for_party_companions(cid)
    out = server.persist_beat(cid, events=[{"kind": "narration", "text": "A quiet beat."}])
    assert "obligations" not in out
    assert "next_action" not in out
    assert "owed" not in out
    assert set(out) <= {"logged", "remembered", "decision", "time", "approval_results"}


def test_quest_endgame_unresolved_fans_out_per_active_quest():
    """MULTI-quest wrap window: each still-ACTIVE quest gets its OWN quest_endgame_unresolved cue
    (the append lives inside the per-quest loop — this pins that fan-out so a refactor that hoists
    the append out of the loop, or dedups by kind, fails loudly). A resolved quest in the same
    window gets none."""
    c = _wrap_window_campaign()
    q2 = Quest(title="The Second Debt", objectives=["pay it"], completed_objectives=[],
               last_progress_day=6)
    c.quests[q2.id] = q2
    q3 = Quest(title="Already Done", objectives=["done"], completed_objectives=["done"],
               last_progress_day=6, status="completed")
    c.quests[q3.id] = q3
    obligations = server._compute_beat_obligations(c)
    endgame = [o for o in obligations if o["kind"] == "quest_endgame_unresolved"]
    assert len(endgame) == 2, f"one cue per ACTIVE quest, got {len(endgame)}"
    titles = {o["title"] for o in endgame}
    assert titles == {"The Debt of Bresser Oln", "The Second Debt"}
    assert all(o["severity"] == "high" for o in endgame)
    # the resolved quest is untouched by the endgame escalation
    assert "Already Done" not in titles
