"""Tests for Campaign Director scene-debt detection (issue #72).

Per-debt: a campaign that triggers the debt → detect returns it;
a clean campaign → none. Plus: resolve_scene_debt works; additive
round-trip (old snapshot with no scene_debts loads cleanly).

ADVISORY contract under test: detect() is pure (no I/O, no mutation);
resolve is explicit (requires evidence); empty debts == today's behavior.
"""

from __future__ import annotations

import json

import pytest

import scene_debt
import server
import store
from models import Campaign, Character, Consequence, Decision, Quest, QuestHook, SceneDebt


# ── Helpers ───────────────────────────────────────────────────────────────────

def _camp(day: int = 1) -> Campaign:
    return Campaign(title="T", day=day)


def _hook(title: str = "The Lost Heir", status: str = "open", giver_id: str = "") -> QuestHook:
    return QuestHook(title=title, status=status, giver_id=giver_id or "")  # type: ignore[arg-type]


def _npc(name: str, location_id: str = "loc1", met: bool = True) -> Character:
    return Character(name=name, kind="npc", location_id=location_id, met=met)


def _decision(summary: str, options: list[str] = None, chosen: str = "we agreed", day: int = 1) -> Decision:
    return Decision(
        summary=summary,
        options=options or [],
        chosen=chosen,
        day=day,
    )


# ── hook_untracked ────────────────────────────────────────────────────────────

class TestHookUntracked:
    def test_active_hook_no_quest_detected(self):
        c = _camp()
        h = _hook(title="Rescue the Baron", status="active")
        c.quest_hooks.append(h)
        debts = scene_debt.detect(c)
        kinds = [d.kind for d in debts]
        assert "hook_untracked" in kinds

    def test_decision_references_hook_no_quest_detected(self):
        c = _camp()
        h = _hook(title="Cult Rising", status="open")
        c.quest_hooks.append(h)
        c.decisions.append(_decision(summary=f"investigate {h.id}", options=["yes", "no"], chosen="yes"))
        debts = scene_debt.detect(c)
        assert any(d.kind == "hook_untracked" and d.subject == h.id for d in debts)

    def test_hook_with_matching_quest_clean(self):
        c = _camp()
        h = _hook(title="Baron Quest", status="active")
        c.quest_hooks.append(h)
        q = Quest(title="Baron Quest", description="")
        c.quests[q.id] = q
        debts = [d for d in scene_debt.detect(c) if d.kind == "hook_untracked"]
        assert debts == []

    def test_resolved_hook_not_flagged(self):
        c = _camp()
        h = _hook(title="Old Hook", status="resolved")
        c.quest_hooks.append(h)
        debts = [d for d in scene_debt.detect(c) if d.kind == "hook_untracked"]
        assert debts == []

    def test_open_hook_player_not_engaged_clean(self):
        c = _camp()
        h = _hook(title="Dormant Hook", status="open")
        c.quest_hooks.append(h)
        debts = [d for d in scene_debt.detect(c) if d.kind == "hook_untracked"]
        assert debts == []

    def test_hook_giver_id_matches_quest_giver_clean(self):
        c = _camp()
        h = _hook(title="Giver Quest", status="active", giver_id="npc_abc")
        c.quest_hooks.append(h)
        q = Quest(title="Some Quest", giver_id="npc_abc")
        c.quests[q.id] = q
        debts = [d for d in scene_debt.detect(c) if d.kind == "hook_untracked"]
        assert debts == []


# ── quest_stalled ─────────────────────────────────────────────────────────────

class TestQuestStalled:
    def test_stalled_active_quest_detected(self):
        c = _camp(day=10)  # well past the 5-day threshold
        q = Quest(title="Find the Sword")
        c.quests[q.id] = q
        # No decisions reference this quest
        debts = scene_debt.detect(c)
        assert any(d.kind == "quest_stalled" and d.subject == q.id for d in debts)

    def test_quest_with_recent_decision_clean(self):
        c = _camp(day=10)
        q = Quest(title="The Heist")
        c.quests[q.id] = q
        # A decision on day 8 references the quest
        c.decisions.append(_decision(
            summary=f"planned {q.id} approach",
            options=["stealth", "force"],
            chosen="stealth",
            day=8,
        ))
        debts = [d for d in scene_debt.detect(c) if d.kind == "quest_stalled"]
        assert debts == []

    def test_completed_quest_not_flagged(self):
        c = _camp(day=10)
        q = Quest(title="Done Quest", status="completed")
        c.quests[q.id] = q
        debts = [d for d in scene_debt.detect(c) if d.kind == "quest_stalled"]
        assert debts == []

    def test_early_campaign_not_flagged(self):
        c = _camp(day=3)  # below the 5-day threshold
        q = Quest(title="Fresh Quest")
        c.quests[q.id] = q
        debts = [d for d in scene_debt.detect(c) if d.kind == "quest_stalled"]
        assert debts == []

    def test_quest_referenced_by_title_in_decision_clean(self):
        c = _camp(day=10)
        q = Quest(title="Dragon Lair")
        c.quests[q.id] = q
        c.decisions.append(_decision(
            summary="approached the dragon lair cautiously",
            options=["sneak", "charge"],
            chosen="sneak",
            day=9,
        ))
        debts = [d for d in scene_debt.detect(c) if d.kind == "quest_stalled"]
        assert debts == []


# ── choice_without_outcome ────────────────────────────────────────────────────

class TestChoiceWithoutOutcome:
    def test_offered_decision_no_chosen_detected(self):
        c = _camp()
        d = Decision(summary="Spare or kill the traitor?", options=["spare", "kill"], chosen="", day=1)
        c.decisions.append(d)
        debts = scene_debt.detect(c)
        assert any(d2.kind == "choice_without_outcome" and d2.subject == d.id for d2 in debts)

    def test_decision_with_chosen_clean(self):
        c = _camp()
        d = _decision(summary="Trust the merchant?", options=["yes", "no"], chosen="yes")
        c.decisions.append(d)
        debts = [d2 for d2 in scene_debt.detect(c) if d2.kind == "choice_without_outcome"]
        assert debts == []

    def test_bare_fact_no_options_clean(self):
        c = _camp()
        # A fact record (no options, no chosen) — not a pending choice
        d = Decision(summary="The party rested in the inn", options=[], chosen="", day=1)
        c.decisions.append(d)
        debts = [d2 for d2 in scene_debt.detect(c) if d2.kind == "choice_without_outcome"]
        assert debts == []


# ── due_consequence ───────────────────────────────────────────────────────────

class TestDueConsequence:
    def test_overdue_consequence_detected(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="The siege begins.", fired=False)
        c.consequences.append(con)
        debts = scene_debt.detect(c)
        assert any(d.kind == "due_consequence" and d.subject == con.id for d in debts)

    def test_future_consequence_clean(self):
        c = _camp(day=3)
        con = Consequence(trigger_day=7, text="Ritual completes.", fired=False)
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "due_consequence"]
        assert debts == []

    def test_fired_consequence_clean(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="Already surfaced.", fired=True)
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "due_consequence"]
        assert debts == []

    def test_thread_consequence_not_flagged_as_due(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="Thread beat.", fired=False, thread_id="thread-1")
        c.consequences.append(con)
        # Should NOT appear as due_consequence (it's a thread, handled by thread_pressure)
        debts = [d for d in scene_debt.detect(c) if d.kind == "due_consequence"]
        assert debts == []

    def test_high_severity_for_2plus_days_overdue(self):
        c = _camp(day=7)
        con = Consequence(trigger_day=3, text="Villain strikes.", fired=False)
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "due_consequence"]
        assert debts and debts[0].severity == "high"

    def test_med_severity_for_1_day_overdue(self):
        c = _camp(day=4)
        con = Consequence(trigger_day=3, text="Ally leaves.", fired=False)
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "due_consequence"]
        assert debts and debts[0].severity == "med"


# ── thread_pressure ───────────────────────────────────────────────────────────

class TestThreadPressure:
    def test_overdue_thread_detected(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="Cult recruits.", fired=False, thread_id="thread-1")
        c.consequences.append(con)
        debts = scene_debt.detect(c)
        assert any(d.kind == "thread_pressure" and d.subject == con.id for d in debts)

    def test_future_thread_clean(self):
        c = _camp(day=3)
        con = Consequence(trigger_day=7, text="Faction moves.", fired=False, thread_id="thread-2")
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "thread_pressure"]
        assert debts == []

    def test_fired_thread_clean(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="Already fired.", fired=True, thread_id="thread-3")
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "thread_pressure"]
        assert debts == []

    def test_authored_consequence_not_flagged_as_thread(self):
        c = _camp(day=5)
        con = Consequence(trigger_day=3, text="Non-thread.", fired=False, thread_id="")
        c.consequences.append(con)
        debts = [d for d in scene_debt.detect(c) if d.kind == "thread_pressure"]
        assert debts == []


# ── npc_introduced_silent ─────────────────────────────────────────────────────

class TestNpcIntroducedSilent:
    def test_met_npc_no_memory_at_current_location_detected(self):
        c = _camp()
        c.current_location_id = "loc1"
        npc = _npc("Verath the Fence", location_id="loc1", met=True)
        c.characters[npc.id] = npc
        debts = scene_debt.detect(c)
        assert any(d.kind == "npc_introduced_silent" and d.subject == npc.id for d in debts)

    def test_npc_with_memory_clean(self):
        c = _camp()
        c.current_location_id = "loc1"
        npc = _npc("Grett", location_id="loc1", met=True)
        npc.memory.append("Grett warned us about the catacombs")
        c.characters[npc.id] = npc
        debts = [d for d in scene_debt.detect(c) if d.kind == "npc_introduced_silent"]
        assert debts == []

    def test_unmet_npc_clean(self):
        c = _camp()
        c.current_location_id = "loc1"
        npc = _npc("Stranger", location_id="loc1", met=False)
        c.characters[npc.id] = npc
        debts = [d for d in scene_debt.detect(c) if d.kind == "npc_introduced_silent"]
        assert debts == []

    def test_npc_at_different_location_clean(self):
        c = _camp()
        c.current_location_id = "loc1"
        npc = _npc("Away NPC", location_id="loc2", met=True)
        c.characters[npc.id] = npc
        debts = [d for d in scene_debt.detect(c) if d.kind == "npc_introduced_silent"]
        assert debts == []

    def test_no_current_location_clean(self):
        c = _camp()
        c.current_location_id = None
        npc = _npc("Floating NPC", location_id="loc1", met=True)
        c.characters[npc.id] = npc
        debts = [d for d in scene_debt.detect(c) if d.kind == "npc_introduced_silent"]
        assert debts == []

    def test_companion_not_flagged(self):
        c = _camp()
        c.current_location_id = "loc1"
        companion = Character(name="Lyra", kind="companion", location_id="loc1", met=True)
        c.characters[companion.id] = companion
        debts = [d for d in scene_debt.detect(c) if d.kind == "npc_introduced_silent"]
        assert debts == []


# ── Clean campaign → no debts ─────────────────────────────────────────────────

class TestCleanCampaign:
    def test_empty_campaign_no_debts(self):
        c = _camp()
        assert scene_debt.detect(c) == []

    def test_campaign_with_everything_resolved_no_debts(self):
        c = _camp(day=3)  # below stall threshold
        # A resolved hook
        h = _hook(title="Done Hook", status="resolved")
        c.quest_hooks.append(h)
        # A completed quest
        q = Quest(title="Finished Quest", status="completed")
        c.quests[q.id] = q
        # A decision with a chosen
        c.decisions.append(_decision("We chose wisely", options=["a", "b"], chosen="a"))
        assert scene_debt.detect(c) == []


# ── resolve_scene_debt tool ───────────────────────────────────────────────────

class TestResolveSceneDebt:
    @pytest.fixture
    def cid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
        return server.create_campaign("Test World")["id"]

    def test_resolve_requires_evidence(self, cid):
        with pytest.raises((ValueError, Exception)):
            server.resolve_scene_debt(cid, "debt_xxx", "")

    def test_resolve_unknown_debt_raises(self, cid):
        with pytest.raises((ValueError, Exception)):
            server.resolve_scene_debt(cid, "nonexistent_debt_id", "I did it")

    def test_resolve_live_debt_persists(self, cid, tmp_path, monkeypatch):
        # Plant a due consequence so there's a live debt
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            con = Consequence(trigger_day=1, text="The ritual completes.", fired=False)
            c.consequences.append(con)
            c.day = 5
            store.save_campaign(c)

        # Confirm it's live
        raw = server.get_scene_debts(cid)
        due_debts = [d for d in raw["live_debts"] if d["kind"] == "due_consequence"]
        assert due_debts, "Expected a live due_consequence debt"
        debt_id = due_debts[0]["id"]

        # Resolve it
        result = server.resolve_scene_debt(cid, debt_id, "Called check_consequences and surfaced it.")
        assert result["debt"]["resolved"] is True
        assert result["debt"]["resolution_evidence"] == "Called check_consequences and surfaced it."

        # Persisted in snapshot
        c2 = store.load_campaign(cid)
        assert any(d.resolved and d.id == debt_id for d in c2.scene_debts)

    def test_resolve_already_resolved_returns_gracefully(self, cid, tmp_path, monkeypatch):
        # Plant and resolve a due consequence
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            con = Consequence(trigger_day=1, text="Old event.", fired=False)
            c.consequences.append(con)
            c.day = 5
            store.save_campaign(c)

        raw = server.get_scene_debts(cid)
        debt_id = raw["live_debts"][0]["id"]
        server.resolve_scene_debt(cid, debt_id, "Done once.")
        # Re-resolving the same debt should not raise, just return "already resolved"
        result = server.resolve_scene_debt(cid, debt_id, "Trying again.")
        assert result["message"] == "already resolved"


# ── Additive round-trip ───────────────────────────────────────────────────────

class TestAdditiveRoundtrip:
    def test_old_snapshot_no_scene_debts_loads(self, tmp_path):
        """An old snapshot JSON without scene_debts deserialises cleanly."""
        c = Campaign(title="Old Campaign", day=1)
        raw = json.loads(c.model_dump_json())
        assert "scene_debts" in raw  # present with default []
        # Remove the field to simulate an old snapshot
        del raw["scene_debts"]
        # Pydantic should NOT raise (field has a default_factory)
        c2 = Campaign.model_validate(raw)
        assert c2.scene_debts == []

    def test_snapshot_with_scene_debts_round_trips(self):
        """A snapshot with resolved scene_debts serialises and deserialises."""
        c = Campaign(title="Campaign With Debts", day=5)
        debt = SceneDebt(
            kind="due_consequence",
            subject="conseq_abc",
            detail="A consequence was overdue.",
            severity="high",
            resolved=True,
            resolution_evidence="DM surfaced it.",
        )
        c.scene_debts.append(debt)
        raw = c.model_dump_json()
        c2 = Campaign.model_validate_json(raw)
        assert len(c2.scene_debts) == 1
        assert c2.scene_debts[0].resolved is True
        assert c2.scene_debts[0].kind == "due_consequence"


# ── get_campaign_director advisory ───────────────────────────────────────────

class TestGetCampaignDirector:
    @pytest.fixture
    def cid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
        return server.create_campaign("Director Test")["id"]

    def test_clean_campaign_no_advisory(self, cid):
        result = server.get_campaign_director(cid)
        assert result["debts"] == []
        assert result["advisory"] == []
        assert result["total_debts"] == 0

    def test_advisory_returns_nudges_for_debts(self, cid, tmp_path, monkeypatch):
        # Plant an overdue consequence
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 5
            con = Consequence(trigger_day=2, text="The cult acts.", fired=False)
            c.consequences.append(con)
            store.save_campaign(c)

        result = server.get_campaign_director(cid)
        assert result["total_debts"] >= 1
        assert len(result["advisory"]) >= 1
        # Advisory should mention consequence or check_consequences
        assert any("consequence" in a.lower() or "check_consequences" in a for a in result["advisory"])

    def test_advisory_capped_at_three(self, cid, tmp_path, monkeypatch):
        """At most 3 debts returned even when more are present."""
        with store.campaign_lock(cid):
            c = store.load_campaign(cid)
            c.day = 10
            c.current_location_id = "loc1"
            # Plant 5 overdue consequences
            for i in range(5):
                c.consequences.append(Consequence(trigger_day=1, text=f"Event {i}.", fired=False))
            store.save_campaign(c)

        result = server.get_campaign_director(cid)
        assert len(result["debts"]) <= 3
