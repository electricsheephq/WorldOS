"""Strategic clocks + downtime projects advance only through engine ticks (#75)."""

import server
import store
import worldsim
from models import Campaign, Consequence, DowntimeProject, Faction, Location, StrategicClock


def _camp(day: int = 1) -> Campaign:
    return Campaign(title="Strategy", day=day)


def test_tick_strategic_advances_due_clocks_and_active_projects_once_per_day():
    c = _camp(day=1)
    c.strategic_state.last_tick_day = 1
    c.strategic_state.clocks["clock-threat"] = StrategicClock(
        id="clock-threat",
        title="Rivals gather leverage",
        kind="threat",
        progress=1,
        target=3,
        tick_every_days=1,
    )
    c.strategic_state.projects["proj-quay"] = DowntimeProject(
        id="proj-quay",
        title="Repair the quay",
        kind="construction",
        status="active",
        progress_days=0,
        duration_days=2,
        effect={"flag": "quay_repaired"},
    )

    c.day = 2
    first = worldsim.tick_strategic(c)
    assert {e["type"] for e in first} == {"clock_advanced", "project_advanced"}
    assert c.strategic_state.clocks["clock-threat"].progress == 2
    assert c.strategic_state.projects["proj-quay"].progress_days == 1

    # Same in-world day: no double-progress and no duplicate event spam.
    assert worldsim.tick_strategic(c) == []
    assert c.strategic_state.clocks["clock-threat"].progress == 2
    assert c.strategic_state.projects["proj-quay"].progress_days == 1

    c.day = 3
    second = worldsim.tick_strategic(c)
    assert any(e["type"] == "clock_due" and e["id"] == "clock-threat" for e in second)
    assert any(e["type"] == "project_complete" and e["id"] == "proj-quay" for e in second)
    assert c.strategic_state.clocks["clock-threat"].progress == 3
    assert c.strategic_state.projects["proj-quay"].status == "complete"
    assert c.flags["quay_repaired"] is True


def test_strategic_clock_cadence_survives_daily_ticks():
    c = _camp(day=1)
    c.strategic_state.last_tick_day = 1
    c.strategic_state.clocks["clock-slow"] = StrategicClock(
        id="clock-slow",
        title="A slow threat",
        tick_every_days=3,
        target=4,
    )

    for day in (2, 3):
        c.day = day
        assert worldsim.tick_strategic(c) == []
        assert c.strategic_state.clocks["clock-slow"].progress == 0

    c.day = 4
    events = worldsim.tick_strategic(c)
    assert events and events[0]["type"] == "clock_advanced"
    assert c.strategic_state.clocks["clock-slow"].progress == 1


def test_world_tick_surfaces_strategic_events_without_firing_narrative_consequences(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = _camp(day=2)
    c.strategic_state.last_tick_day = 1
    c.strategic_state.clocks["clock-threat"] = StrategicClock(
        id="clock-threat",
        title="Rivals gather leverage",
        progress=0,
        target=1,
        tick_every_days=1,
    )
    c.consequences.append(Consequence(trigger_day=99, text="A narrative beat remains pending."))
    store.save_campaign(c)

    out = server.world_tick(c.id)
    assert any(e["type"] == "clock_due" for e in out["strategic_events"])
    reloaded = store.load_campaign(c.id)
    assert len(reloaded.consequences) == 1
    assert reloaded.consequences[0].fired is False


def test_downtime_completes_active_project_and_applies_structured_effect_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = _camp(day=1)
    c.factions["fac-civic"] = Faction(id="fac-civic", name="Civic League", reputation=10)
    c.strategic_state.last_tick_day = 1
    c.strategic_state.projects["proj-envoys"] = DowntimeProject(
        id="proj-envoys",
        title="Host the envoys",
        kind="diplomacy",
        status="active",
        progress_days=0,
        duration_days=2,
        effect={"flag": "envoys_hosted", "faction_id": "fac-civic", "reputation_delta": "5"},
    )
    store.save_campaign(c)

    out = server.downtime(c.id, 2)
    assert any(e["type"] == "project_complete" for e in out["strategic_events"])
    after = store.load_campaign(c.id)
    assert after.strategic_state.projects["proj-envoys"].status == "complete"
    assert after.flags["envoys_hosted"] is True
    assert after.factions["fac-civic"].reputation == 15

    # Repeated explicit ticks on the same day do not re-apply the completion effect.
    assert server.world_tick(c.id)["strategic_events"] == []
    again = store.load_campaign(c.id)
    assert again.factions["fac-civic"].reputation == 15
    assert server.downtime(c.id, 1)["strategic_events"] == []
    later = store.load_campaign(c.id)
    assert later.factions["fac-civic"].reputation == 15


def test_day_rolling_travel_to_advances_strategic_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = _camp(day=1)
    c.time_of_day = "night"
    c.current_location_id = "loc-a"
    c.locations = {
        "loc-a": Location(id="loc-a", name="A", connections=["loc-b"], visited=True),
        "loc-b": Location(id="loc-b", name="B", connections=["loc-a"]),
    }
    c.strategic_state.last_tick_day = 1
    c.strategic_state.projects["proj-watch"] = DowntimeProject(
        id="proj-watch",
        title="Raise the watch",
        status="active",
        duration_days=1,
    )
    store.save_campaign(c)

    out = server.travel_to(c.id, "loc-b", advance_time=True)
    assert out["day"] == 2
    assert any(e["type"] == "project_complete" for e in out["strategic_events"])
    assert store.load_campaign(c.id).strategic_state.projects["proj-watch"].status == "complete"
