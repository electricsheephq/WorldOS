from campaign_calendar import project_calendar_date
from content import seed_world
from models import CampaignCalendar, CalendarMonth, CalendarMoon


def _fixture_calendar() -> CampaignCalendar:
    return CampaignCalendar(
        name="Dale Reckoning",
        era_suffix="DR",
        epoch_year=1492,
        epoch_month=1,
        epoch_day=1,
        weekdays=["Firstday", "Secondday", "Thirdday", "Fourthday", "Fifthday"],
        months=[
            CalendarMonth(name="Hammer", days=30, season="Deepwinter"),
            CalendarMonth(name="Alturiak", days=30, season="The Claw of Winter"),
            CalendarMonth(name="Ches", days=30, season="Springrise"),
        ],
        moons=[
            CalendarMoon(
                name="Selune",
                cycle_days=8,
                phase_names=["new", "waxing", "full", "waning"],
            )
        ],
    )


def test_project_calendar_date_keeps_day_counter_canonical_and_renders_label():
    projection = project_calendar_date(32, "dusk", _fixture_calendar())

    assert projection["available"] is True
    assert projection["canonical_day"] == 32
    assert projection["year"] == 1492
    assert projection["month"] == "Alturiak"
    assert projection["day_of_month"] == 2
    assert projection["weekday"] == "Secondday"
    assert projection["season"] == "The Claw of Winter"
    assert projection["date_label"] == "Secondday, 2 Alturiak 1492 DR"
    assert projection["label"] == "Secondday, 2 Alturiak 1492 DR · dusk"
    assert projection["moons"] == [
        {
            "name": "Selune",
            "age": 7,
            "cycle_days": 8,
            "phase": "waning",
        }
    ]


def test_project_calendar_date_degrades_empty_calendar_to_legacy_day_label():
    projection = project_calendar_date(5, "night", CampaignCalendar(name="Empty Calendar"))

    assert projection == {
        "available": False,
        "canonical_day": 5,
        "label": "Day 5 · night",
    }


def test_seed_world_preserves_optional_calendar_metadata_without_advancing_time():
    campaign = seed_world(
        {
            "id": "calendar-test",
            "name": "Calendar Test",
            "premise": "A compact calendar fixture.",
            "era": "1492 DR",
            "calendar": _fixture_calendar().model_dump(mode="json"),
            "regions": [
                {"id": "loc-gate", "name": "Gate", "description": "A city gate.", "connections": []}
            ],
            "factions": [],
            "npc_roster": [],
            "history": [],
            "standing_threads": [],
            "starting_options": [{"location_id": "loc-gate", "framing": "Start at the gate."}],
        }
    )

    assert campaign.day == 1
    assert campaign.time_of_day == "morning"
    assert campaign.calendar is not None
    assert campaign.calendar.name == "Dale Reckoning"
    assert project_calendar_date(campaign.day, campaign.time_of_day, campaign.calendar)["label"] == (
        "Firstday, 1 Hammer 1492 DR · morning"
    )


def test_seed_world_ignores_malformed_optional_calendar_metadata():
    campaign = seed_world(
        {
            "id": "calendar-broken",
            "name": "Broken Calendar Test",
            "premise": "A fixture with bad optional calendar metadata.",
            "era": "1492 DR",
            "calendar": {
                "name": "Broken Reckoning",
                "months": [{"name": "Impossible", "days": 0}],
            },
            "regions": [
                {"id": "loc-gate", "name": "Gate", "description": "A city gate.", "connections": []}
            ],
            "factions": [],
            "npc_roster": [],
            "history": [],
            "standing_threads": [],
            "starting_options": [{"location_id": "loc-gate", "framing": "Start at the gate."}],
        }
    )

    assert campaign.day == 1
    assert campaign.calendar is None
