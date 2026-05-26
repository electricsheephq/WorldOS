"""Quest graph + campaign_dashboard + downtime (P2.8)."""

import pytest

import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def test_add_and_complete_quest(cid):
    q = server.add_quest(
        cid, "Slay the rat-king", giver_id="brakka", location_id="loc-sump",
        objectives=["find the sump"],
    )
    assert q["status"] == "active"
    assert server.complete_quest(cid, q["id"], "completed")["status"] == "completed"


def test_complete_quest_rejects_bad_status(cid):
    q = server.add_quest(cid, "X")
    with pytest.raises(Exception):
        server.complete_quest(cid, q["id"], "ludicrous")


# ── Rule-of-three evolution (Quest & Arc engine, Layer 1) ─────────────────────


def _set_evolution(cid: str, quest_id: str, evolves_to: str, callback_in_days: int) -> None:
    """Plant the rule-of-three fields on a tracked quest (content/questgen would set
    these; add_quest deliberately keeps them DM/content-authored)."""
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        q = c.quests[quest_id]
        q.evolves_to = evolves_to
        q.callback_in_days = callback_in_days
        store.save_campaign(c)


def test_resolving_quest_with_evolution_schedules_consequence(cid):
    """(a) Resolving a quest with evolves_to + callback_in_days=N schedules a
    Consequence at day+N that references the quest (via the evolves_from note)."""
    qid = server.add_quest(cid, "Recover the Sunblade")["id"]
    _set_evolution(cid, qid, evolves_to="hook_shadow_returns", callback_in_days=3)

    before = store.load_campaign(cid)
    start_day = before.day
    assert before.consequences == []  # nothing scheduled yet

    out = server.complete_quest(cid, qid, "completed")
    assert out["status"] == "completed"
    assert out["evolution_scheduled"]["evolves_to"] == "hook_shadow_returns"
    assert out["evolution_scheduled"]["trigger_day"] == start_day + 3

    c = store.load_campaign(cid)
    evo = [con for con in c.consequences if con.note == f"evolves_from:{qid}"]
    assert len(evo) == 1
    assert evo[0].trigger_day == start_day + 3
    assert "hook_shadow_returns" in evo[0].text
    assert "Recover the Sunblade" in evo[0].text
    # Trace-back rides in `note`, NOT thread_id (so check_consequences surfaces it).
    assert evo[0].thread_id == ""


def test_resolving_quest_evolution_surfaces_via_check_consequences(cid):
    """The scheduled evolution surfaces through the existing check_consequences path
    (immediately when callback_in_days == 0)."""
    qid = server.add_quest(cid, "Break the Siege")["id"]
    _set_evolution(cid, qid, evolves_to="seed_warlord_regroups", callback_in_days=0)

    server.complete_quest(cid, qid, "completed")
    res = server.check_consequences(cid)
    due_texts = [d["text"] for d in res["due"]]
    assert any("seed_warlord_regroups" in t and "Break the Siege" in t for t in due_texts)


def test_resolving_quest_without_evolution_schedules_nothing(cid):
    """(b) evolves_to='' / callback=0 -> NO schedule (today's behavior exactly)."""
    qid = server.add_quest(cid, "Plain Errand")["id"]  # no evolves_to set

    out = server.complete_quest(cid, qid, "completed")
    assert "evolution_scheduled" not in out

    c = store.load_campaign(cid)
    assert c.consequences == []


def test_failed_quest_with_evolution_does_not_schedule(cid):
    """A quest that resolves to 'failed' (not 'completed') does NOT schedule the
    evolution — only the completed terminal state grows the rule-of-three follow-on."""
    qid = server.add_quest(cid, "Doomed Errand")["id"]
    _set_evolution(cid, qid, evolves_to="hook_consequences_of_failure", callback_in_days=2)

    out = server.complete_quest(cid, qid, "failed")
    assert "evolution_scheduled" not in out

    c = store.load_campaign(cid)
    assert [con for con in c.consequences if con.note == f"evolves_from:{qid}"] == []


def test_re_resolving_quest_does_not_double_schedule(cid):
    """(c) Re-resolving an already-resolved quest does NOT double-schedule the
    evolution (idempotent on the evolves_from:<quest_id> note)."""
    qid = server.add_quest(cid, "Ring the Old Bell")["id"]
    _set_evolution(cid, qid, evolves_to="hook_bell_echoes", callback_in_days=1)

    first = server.complete_quest(cid, qid, "completed")
    assert "evolution_scheduled" in first
    first_conseq_id = first["evolution_scheduled"]["consequence_id"]

    # Re-resolve (e.g. status toggled back to active by the DM, then completed again).
    server.complete_quest(cid, qid, "active")
    second = server.complete_quest(cid, qid, "completed")
    assert "evolution_scheduled" not in second  # guard held

    c = store.load_campaign(cid)
    evo = [con for con in c.consequences if con.note == f"evolves_from:{qid}"]
    assert len(evo) == 1 and evo[0].id == first_conseq_id  # still exactly one


def test_dashboard_rollup_resolves_links(cid):
    server.create_character(cid, "Hero", kind="player", max_hp=10)
    server.add_quest(cid, "Find the drain", giver_id="brakka", location_id="loc-sump")
    server.add_consequence(cid, 5, "The undercity floods upward.")
    dash = server.campaign_dashboard(cid)
    assert dash["location"] is not None
    assert any(p["name"] == "Vesper" for p in dash["party"])  # seeded companion present
    fd = next(q for q in dash["active_quests"] if q["title"] == "Find the drain")
    assert fd["giver"] is not None and fd["location"] is not None  # names resolved
    assert any("undercity" in pc["text"] for pc in dash["pending_consequences"])


def test_downtime_advances_days_and_fires_consequences(cid):
    server.add_consequence(cid, 3, "Reinforcements arrive at the keep.")
    out = server.downtime(cid, 5, note="travel to the capital")
    assert out["days_elapsed"] == 5 and out["day"] >= 6  # cellar-rats starts on day 1
    assert any("Reinforcements" in d["text"] for d in out["due_consequences"])
