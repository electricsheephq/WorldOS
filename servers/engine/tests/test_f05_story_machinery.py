"""F05 cluster — Story machinery reach (issue #800).

Covers the audited sub-findings that #851 left on the table:
  F05-2  — set_quest_status & complete_objective auto-resolve never scheduled evolution.
  F05-4  — resolve_scene_debt did not suppress re-detection (resolved debts re-surfaced
           forever and could never be re-resolved).
  F05-5  — choice_without_outcome was unresolvable; director nudge named a nonexistent
           update_decision tool.
  F05-6  — faction-arc progression was a dark loop (locked-but-earned stages invisible
           on every per-beat surface).
  F05-7  — quest_stalled ignored the engine's own progress verbs (a late-added quest was
           flaggable immediately).
  F05-10 — prelude "meeting" beat bound a uniformly-random roster NPC (Raphael / Withers /
           The Emperor 3-in-9).

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (unit 05, Part C).
"""

from __future__ import annotations

import random

import pytest

import faction_arc as faction_arc_mod
import questgen
import scene_debt
import server
import store
from models import (
    Campaign,
    Character,
    Consequence,
    Decision,
    Faction,
    FactionArc,
    FactionArcStage,
    Quest,
    QuestHook,
    SceneDebt,
)


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("F05 Test")["id"]


def _add_active_quest(cid: str, title: str, objectives: list[str]) -> str:
    return server.add_quest(cid, title=title, objectives=objectives)["id"]


# ── F05-2 — evolution routes through ALL completion verbs ──────────────────────


class TestF05_2_EvolutionAllVerbs:
    def test_set_quest_status_schedules_evolution(self, cid):
        qid = _add_active_quest(cid, "Save the Grove", ["find the druid"])
        # Pre-set evolves_to on the quest, then resolve via set_quest_status.
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.quests[qid].evolves_to = "the grove's gratitude curdles into a debt"
            store.save_campaign(c)
        out = server.set_quest_status(cid, qid, "completed")
        assert out["status"] == "completed"
        # The evolution Consequence must now exist (the helper ran).
        c2 = store.load_campaign(cid)
        note = server._evolution_note(qid)
        assert any(con.note == note for con in c2.consequences), \
            "set_quest_status must schedule the rule-of-three evolution"
        # And it's surfaced in the return so the DM is TOLD.
        assert "evolution_scheduled" in out

    def test_set_quest_status_evolution_idempotent(self, cid):
        # set_quest_status keeps its frozen 3-arg wire contract; evolution comes from a
        # pre-authored evolves_to. Re-resolving must never double-schedule (note guard).
        qid = _add_active_quest(cid, "Slay the Wyrm", ["track the wyrm"])
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.quests[qid].evolves_to = "echo"
            store.save_campaign(c)
        server.set_quest_status(cid, qid, "completed")
        server.set_quest_status(cid, qid, "completed")  # re-resolve
        c2 = store.load_campaign(cid)
        note = server._evolution_note(qid)
        assert sum(1 for con in c2.consequences if con.note == note) == 1, \
            "re-resolving must never double-schedule (note guard)"

    def test_complete_objective_all_done_schedules_evolution(self, cid):
        qid = _add_active_quest(cid, "Clear the Crypt", ["enter", "defeat the lich"])
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.quests[qid].evolves_to = "the lich's phylactery resurfaces"
            store.save_campaign(c)
        server.complete_objective(cid, qid, "enter")
        out = server.complete_objective(cid, qid, "defeat the lich")
        assert out["status"] == "completed"
        c2 = store.load_campaign(cid)
        note = server._evolution_note(qid)
        assert any(con.note == note for con in c2.consequences), \
            "complete_objective's all-done auto-resolve must schedule evolution"
        assert "evolution_scheduled" in out

    def test_complete_objective_no_evolves_to_is_noop(self, cid):
        # ADDITIVE: a quest with no evolves_to schedules nothing (today's behavior).
        qid = _add_active_quest(cid, "Plain Errand", ["deliver the letter"])
        server.complete_objective(cid, qid, "deliver the letter")
        c2 = store.load_campaign(cid)
        assert not any(con.note == server._evolution_note(qid) for con in c2.consequences)

    def test_three_verbs_mutually_idempotent(self, cid):
        qid = _add_active_quest(cid, "The Bargain", ["seal the pact"])
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.quests[qid].evolves_to = "the pact comes due"
            store.save_campaign(c)
        server.complete_objective(cid, qid, "seal the pact")  # auto-resolves + schedules
        server.set_quest_status(cid, qid, "completed")        # re-resolve via verb 2
        server.complete_quest(cid, qid, "completed")          # re-resolve via verb 3
        c2 = store.load_campaign(cid)
        note = server._evolution_note(qid)
        assert sum(1 for con in c2.consequences if con.note == note) == 1, \
            "all three completion verbs share the same evolution; no double-schedule"


# ── F05-4 — resolve_scene_debt suppresses re-detection ─────────────────────────


class TestF05_4_ResolvedDebtSuppression:
    def _plant_due(self, cid: str) -> str:
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.consequences.append(Consequence(trigger_day=1, text="The ritual completes.", fired=False))
            c.day = 5
            store.save_campaign(c)
        raw = server.get_scene_debts(cid)
        due = [d for d in raw["live_debts"] if d["kind"] == "due_consequence"]
        assert due
        return due[0]["id"]

    def test_resolved_debt_no_longer_live(self, cid):
        debt_id = self._plant_due(cid)
        server.resolve_scene_debt(cid, debt_id, "Surfaced it.")
        raw = server.get_scene_debts(cid)
        live_ids = [d["id"] for d in raw["live_debts"]]
        assert debt_id not in live_ids, \
            "a resolved debt must be SUPPRESSED from the live list (F05-4)"

    def test_resolved_debt_off_director_advisory(self, cid):
        debt_id = self._plant_due(cid)
        server.resolve_scene_debt(cid, debt_id, "Surfaced it.")
        adv = server.get_campaign_director(cid)
        assert all(d["id"] != debt_id for d in adv["debts"]), \
            "a resolved debt must not crowd out a director top-3 slot"

    def test_resolved_day_stamped(self, cid):
        debt_id = self._plant_due(cid)
        server.resolve_scene_debt(cid, debt_id, "Surfaced it.")
        c = store.load_campaign(cid)
        rec = next(d for d in c.scene_debts if d.id == debt_id)
        assert rec.resolved is True
        assert rec.resolved_day == 5, "resolved_day must stamp the campaign day"

    def test_suppressed_during_snooze(self, cid):
        # Within the snooze window the resolved debt stays suppressed AND a re-resolve is
        # gracefully refused (no churn) — the resolution actually holds for a while.
        debt_id = self._plant_due(cid)  # day 5
        server.resolve_scene_debt(cid, debt_id, "First time.")
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 8  # still within RESOLVED_SNOOZE_DAYS (7) of day 5
            store.save_campaign(c)
        raw = server.get_scene_debts(cid)
        assert debt_id not in [d["id"] for d in raw["live_debts"]], "snoozed: not live"
        assert debt_id in [d["id"] for d in raw["resolved_debts"]], "stays in audit trail"
        res = server.resolve_scene_debt(cid, debt_id, "Again — should refuse.")
        assert res["message"] == "already resolved"

    def test_re_resolve_after_snooze_lapses(self, cid):
        # After the snooze lapses, an UN-addressed structural fact (same id) re-detects and is
        # re-resolvable — no eternal silence, no eternal nag. The audit record is UPDATED in
        # place (re-stamped), not duplicated.
        debt_id = self._plant_due(cid)  # day 5
        server.resolve_scene_debt(cid, debt_id, "First time.")
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 20  # well past the 7-day snooze
            store.save_campaign(c)
        raw = server.get_scene_debts(cid)
        # The fact still genuinely owes (the consequence is still un-fired) -> re-surfaces live.
        assert debt_id in [d["id"] for d in raw["live_debts"]], "snooze lapsed: re-surfaces"
        out = server.resolve_scene_debt(cid, debt_id, "Second time.")
        assert out["message"] == "resolved"
        assert out["debt"]["resolved_day"] == 20
        # Exactly ONE audit record for this id (updated in place, not appended twice).
        c2 = store.load_campaign(cid)
        assert sum(1 for d in c2.scene_debts if d.id == debt_id) == 1


# ── F05-5 — choice_without_outcome resolvable + nudge fixed ────────────────────


class TestF05_5_UpdateDecision:
    def test_update_decision_tool_exists(self):
        assert hasattr(server, "update_decision"), \
            "the director nudge names update_decision; the tool must exist"

    def test_update_decision_sets_chosen(self, cid):
        d = server.record_decision(cid, summary="Trust the duke?", options=["trust", "refuse"])
        did = d["id"]
        # The pending decision is a high-sev debt.
        c = store.load_campaign(cid)
        debts = scene_debt.detect(c)
        assert any(db.kind == "choice_without_outcome" and db.subject == did for db in debts)
        # Resolve it via the new tool.
        out = server.update_decision(cid, did, chosen="trust", rationale="he kept his word before")
        assert out["chosen"] == "trust"
        # The debt is gone (chosen is now set).
        c2 = store.load_campaign(cid)
        debts2 = scene_debt.detect(c2)
        assert not any(db.kind == "choice_without_outcome" and db.subject == did for db in debts2)

    def test_update_decision_unknown_raises(self, cid):
        with pytest.raises((ValueError, Exception)):
            server.update_decision(cid, "decision_nope", chosen="x")

    def test_nudge_names_real_tool(self):
        import director
        debt = SceneDebt(
            id="d1", kind="choice_without_outcome", subject="dec1",
            detail="x", severity="high",
            evidence={"decision_id": "dec1", "summary": "Trust?", "options": ["a", "b"]},
        )
        nudge = director._nudge(debt)
        assert "update_decision" in nudge
        assert hasattr(server, "update_decision")


# ── F05-6 — faction earned-but-locked stage is visible ─────────────────────────


class TestF05_6_FactionDarkLoop:
    def _campaign_with_locked_earned_stage(self) -> Campaign:
        c = Campaign(title="T", day=3)
        fac = Faction(id="fac-x", name="The Guild", joined=True, reputation=50)
        arc = FactionArc(
            id="arc-x", faction_id="fac-x", title="Rise in the Guild",
            requires_joined=True, status="available",
            stages=[
                FactionArcStage(id="st1", title="Made Member", status="resolved", unlock_at=0),
                # EARNED (rep 50 >= unlock 40) but still locked — the dark stage.
                FactionArcStage(id="st2", title="Lieutenant", status="locked", unlock_at=40),
                # Not yet earned.
                FactionArcStage(id="st3", title="Guildmaster", status="locked", unlock_at=100),
            ],
        )
        fac.questline_arc_id = "arc-x"
        c.factions["fac-x"] = fac
        c.faction_arcs["arc-x"] = arc
        return c

    def test_earned_locked_stage_detected(self):
        c = self._campaign_with_locked_earned_stage()
        debts = scene_debt.detect(c)
        fr = [d for d in debts if d.kind == "faction_rank_available"]
        assert fr, "an earned-but-locked stage must surface as a debt (read-only detection)"
        ev = fr[0].evidence
        # CORRECTION from spec: label earned-but-locked DISTINCTLY from available.
        assert "earned_locked_stage_ids" in ev
        assert "st2" in ev["earned_locked_stage_ids"]
        # st2 is NOT yet available — never claim it as available_stage_ids.
        assert "st2" not in ev.get("available_stage_ids", [])
        assert "st3" not in ev["earned_locked_stage_ids"]  # not earned yet

    def test_detect_pure_does_not_flip_stage(self):
        c = self._campaign_with_locked_earned_stage()
        scene_debt.detect(c)
        # detect() must be PURE — it never flips locked->available (that's evaluate's job).
        assert c.faction_arcs["arc-x"].stages[1].status == "locked"

    def test_nudge_says_check_faction_arcs(self):
        import director
        c = self._campaign_with_locked_earned_stage()
        debts = [d for d in scene_debt.detect(c) if d.kind == "faction_rank_available"]
        nudge = director._nudge(debts[0])
        # The earned-but-locked nudge must point at check_faction_arcs (the flipper).
        assert "check_faction_arcs" in nudge

    def test_already_available_unchanged(self):
        # An already-available stage keeps its existing behavior (not in earned_locked).
        c = self._campaign_with_locked_earned_stage()
        c.faction_arcs["arc-x"].stages[1].status = "available"
        debts = [d for d in scene_debt.detect(c) if d.kind == "faction_rank_available"]
        assert debts
        ev = debts[0].evidence
        assert "st2" in ev["available_stage_ids"]
        assert "st2" not in ev.get("earned_locked_stage_ids", [])

    def test_not_joined_not_flagged(self):
        c = self._campaign_with_locked_earned_stage()
        c.factions["fac-x"].joined = False
        debts = [d for d in scene_debt.detect(c) if d.kind == "faction_rank_available"]
        assert not debts, "a non-member earns no rank-up nudge"


# ── F05-7 — quest_stalled reads the engine's progress verbs ────────────────────


class TestF05_7_QuestStalled:
    def test_late_quest_not_immediately_stalled(self, cid):
        # Advance the campaign well past the stall window, THEN add a quest. It must NOT be
        # flaggable on the very next beat — add_quest stamps last_progress_day.
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 20
            store.save_campaign(c)
        qid = _add_active_quest(cid, "Fresh Errand", ["go there"])
        c = store.load_campaign(cid)
        assert c.quests[qid].last_progress_day == 20, "add_quest must stamp last_progress_day"
        debts = scene_debt.detect(c)
        stalled = [d for d in debts if d.kind == "quest_stalled" and d.subject == qid]
        assert not stalled, "a just-added quest must not be flagged stalled immediately"

    def test_progress_resets_stall_clock(self, cid):
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 20
            store.save_campaign(c)
        qid = _add_active_quest(cid, "Two-Step", ["a", "b"])
        # Jump forward past the window with NO progress -> stalled.
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 30
            store.save_campaign(c)
        assert any(
            d.kind == "quest_stalled" and d.subject == qid for d in scene_debt.detect(store.load_campaign(cid))
        )
        # Now make progress via complete_objective -> the clock resets.
        server.complete_objective(cid, qid, "a")
        c = store.load_campaign(cid)
        assert c.quests[qid].last_progress_day == 30
        assert not any(
            d.kind == "quest_stalled" and d.subject == qid for d in scene_debt.detect(c)
        ), "progress via an engine verb must reset the stall clock"

    def test_old_snapshot_falls_back_to_decision_proxy(self):
        # last_progress_day == -1 (old snapshot) -> the legacy Decision-text proxy still works.
        c = Campaign(title="T", day=20)
        q = Quest(title="Legacy Quest", status="active", objectives=["x"], last_progress_day=-1)
        c.quests[q.id] = q
        # No decision, past the window -> stalled (today's behavior preserved).
        assert any(d.kind == "quest_stalled" and d.subject == q.id for d in scene_debt.detect(c))
        # A decision naming the quest within the window -> not stalled (legacy grace).
        c.decisions.append(Decision(summary=f"worked on {q.title}", day=18, chosen="did it"))
        assert not any(d.kind == "quest_stalled" and d.subject == q.id for d in scene_debt.detect(c))


# ── F05-10 — prelude never binds a villain/deity NPC ───────────────────────────


class TestF05_10_PreludeMeetable:
    def _world_with_villains(self) -> dict:
        return {
            "npc_roster": [
                {"id": "npc-hero", "name": "Ally", "role": "ally"},
                {"id": "npc-raphael", "name": "Raphael", "role": "villain", "prelude_meetable": False},
                {"id": "npc-withers", "name": "Withers", "role": "deity", "prelude_meetable": False},
                {"id": "npc-the-emperor", "name": "The Emperor", "role": "patron", "prelude_meetable": False},
                {"id": "npc-claudan", "name": "Claudan", "role": "chaos", "easter_egg": True},
            ]
        }

    def _campaign_from_roster(self, world: dict) -> Campaign:
        c = Campaign(title="BG", day=1)
        for n in world["npc_roster"]:
            c.characters[n["id"]] = Character(id=n["id"], name=n["name"], kind="npc")
        c.locations["loc1"] = __import__("models").Location(id="loc1", name="The Gate")
        c.current_location_id = "loc1"
        return c

    def test_prelude_never_binds_villains_across_seeds(self):
        world = self._world_with_villains()
        forbidden = {"npc-raphael", "npc-withers", "npc-the-emperor", "npc-claudan"}
        for seed in range(100):
            c = self._campaign_from_roster(world)
            rng = random.Random(seed)
            questgen.generate(c, world, rng)
            meeting = next((b for b in c.prelude if b.kind == "meeting"), None)
            if meeting is not None and meeting.ref_id:
                assert meeting.ref_id not in forbidden, \
                    f"seed {seed}: prelude bound a forbidden NPC {meeting.ref_id}"

    def test_flagless_world_unchanged(self):
        # A world with no prelude_meetable flags keeps today's behavior (all npcs eligible).
        world = {
            "npc_roster": [
                {"id": "npc-a", "name": "A", "role": "ally"},
                {"id": "npc-b", "name": "B", "role": "ally"},
            ]
        }
        bound = set()
        for seed in range(40):
            c = self._campaign_from_roster(world)
            questgen.generate(c, world, random.Random(seed))
            meeting = next((b for b in c.prelude if b.kind == "meeting"), None)
            if meeting and meeting.ref_id:
                bound.add(meeting.ref_id)
        # Both eligible npcs can still be bound (distribution unchanged / not collapsed).
        assert bound == {"npc-a", "npc-b"}


# ── A0.1 — get_quests exposes the FULL quest set for the adventure-eval telemetry ─


class TestA0_1_GetQuestsFullSet:
    def test_returns_all_quests_regardless_of_status(self, cid):
        # Seed two quests, then resolve one fully and advance one objective of the other.
        done_qid = _add_active_quest(cid, "Deliver the Relic", ["reach the temple"])
        active_qid = _add_active_quest(cid, "Two-Step Errand", ["step one", "step two"])
        server.complete_quest(cid, done_qid, "completed")
        server.complete_objective(cid, active_qid, "step one")

        out = server.get_quests(cid)
        assert set(out.keys()) == {"quests"}
        by_id = {q["id"]: q for q in out["quests"]}

        # BOTH quests are present — the completed one does NOT drop out (the get_state gap).
        assert set(by_id) == {done_qid, active_qid}, \
            "get_quests must return every quest regardless of status"

        done = by_id[done_qid]
        assert done["status"] == "completed"
        # The milestone-award idempotency flag is exposed so an eval can read it (its value
        # tracks leveling_mode — only stamped in xp-mode — so we assert presence, not value).
        assert isinstance(done["milestone_awarded"], bool)

        active = by_id[active_qid]
        assert active["status"] == "active"
        # The objective split lets an eval compute progress: one done, one outstanding.
        assert active["objectives"] == ["step one", "step two"]
        assert active["completed_objectives"] == ["step one"]

    def test_get_state_active_quests_unchanged(self, cid):
        # The pinned invariant: get_state still projects ONLY the active quest — get_quests
        # is purely additive and does not alter get_state's active-only slice.
        done_qid = _add_active_quest(cid, "Slay the Beast", ["find the lair"])
        active_qid = _add_active_quest(cid, "Ongoing Watch", ["hold the line"])
        server.complete_quest(cid, done_qid, "completed")

        active_from_state = server.get_state(cid)["active_quests"]
        assert [q["id"] for q in active_from_state] == [active_qid], \
            "get_state.active_quests must still show only the active quest"

    def test_read_only_no_mutation(self, cid):
        # get_quests is pure projection — calling it must not mutate persisted state.
        qid = _add_active_quest(cid, "Untouched", ["a", "b"])
        before = store.load_campaign(cid).model_dump()
        server.get_quests(cid)
        after = store.load_campaign(cid).model_dump()
        assert before == after, "get_quests must not mutate campaign state"
        # Sanity: the projected entry carries the full field set an eval needs.
        entry = next(q for q in server.get_quests(cid)["quests"] if q["id"] == qid)
        assert set(entry) == {
            "id", "title", "status", "objectives", "completed_objectives",
            "giver_id", "location_id", "milestone_awarded",
            "last_progress_day", "last_progress_beat",
        }
