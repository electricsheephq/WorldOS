"""Display-only campaign calendar projection.

The canonical engine clock is still ``Campaign.day``. This module maps that
integer into authored calendar labels for viewer/OpenWorlds read models without
introducing a second mutable time authority.
"""

from __future__ import annotations

from models import CampaignCalendar


def _month_cursor(calendar: CampaignCalendar, elapsed_days: int) -> tuple[int, int, int]:
    months = calendar.months
    if not months:
        return calendar.epoch_year, 0, calendar.epoch_day + elapsed_days

    month_index = min(max(calendar.epoch_month - 1, 0), len(months) - 1)
    day_of_month = min(max(calendar.epoch_day, 1), months[month_index].days)
    year = calendar.epoch_year
    remaining = max(elapsed_days, 0)

    while remaining:
        days_left_in_month = months[month_index].days - day_of_month
        if remaining <= days_left_in_month:
            day_of_month += remaining
            remaining = 0
        else:
            remaining -= days_left_in_month + 1
            day_of_month = 1
            month_index += 1
            if month_index >= len(months):
                month_index = 0
                year += 1

    return year, month_index, day_of_month


def _moon_phase(age: int, phase_names: list[str], cycle_days: int) -> str:
    names = [str(name).strip() for name in phase_names if str(name).strip()]
    if not names:
        names = ["new", "waxing", "full", "waning"]
    index = min(len(names) - 1, (age * len(names)) // max(cycle_days, 1))
    return names[index]


def project_calendar_date(day: int, time_of_day: str, calendar: CampaignCalendar) -> dict:
    """Return a JSON-safe calendar projection for a campaign day.

    ``canonical_day`` is echoed so consumers never treat the rendered date as the
    authority for ticks, rests, consequences, or strategic advancement.
    """

    canonical_day = day if isinstance(day, int) and day > 0 else 1
    if not calendar.months:
        phase = str(time_of_day).strip()
        return {
            "available": False,
            "canonical_day": canonical_day,
            "label": f"Day {canonical_day}" + (f" · {phase}" if phase else ""),
        }

    elapsed_days = canonical_day - 1
    year, month_index, day_of_month = _month_cursor(calendar, elapsed_days)
    month = calendar.months[month_index] if calendar.months else None
    month_name = month.name if month else "Day"
    season = month.season if month else ""

    weekday = ""
    weekdays = [str(name).strip() for name in calendar.weekdays if str(name).strip()]
    if weekdays:
        weekday = weekdays[(calendar.week_start_index + elapsed_days) % len(weekdays)]

    era = f" {calendar.era_suffix.strip()}" if calendar.era_suffix.strip() else ""
    date_core = f"{day_of_month} {month_name} {year}{era}"
    date_label = f"{weekday}, {date_core}" if weekday else date_core
    phase = str(time_of_day).strip()
    label = date_label + (f" · {phase}" if phase else "")

    moons = []
    for moon in calendar.moons:
        cycle = max(moon.cycle_days, 1)
        age = (moon.epoch_phase_day + elapsed_days) % cycle
        moons.append(
            {
                "name": moon.name,
                "age": age,
                "cycle_days": cycle,
                "phase": _moon_phase(age, moon.phase_names, cycle),
            }
        )

    return {
        "available": True,
        "calendar": calendar.name,
        "canonical_day": canonical_day,
        "year": year,
        "month": month_name,
        "day_of_month": day_of_month,
        "weekday": weekday,
        "season": season,
        "date_label": date_label,
        "label": label,
        "moons": moons,
    }
