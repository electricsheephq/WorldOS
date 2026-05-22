"""Exploration & travel: move the party across the location graph and advance
the in-world clock.

Pure module (no MCP, no I/O). It mutates a `Campaign` in place — the same
load -> mutate -> save discipline the engine tools use — and otherwise just
reads the location graph. The graph lives on `Campaign.locations` (a dict of
`Location`, each carrying `connections` = ids of adjacent locations) with the
party's position in `Campaign.current_location_id`.

Travel is gated to edges visible *from the current location*: you can only step
to a location listed in the current one's `connections`. This keeps movement
honest (the DM can't teleport the party past a locked door the map doesn't
connect) and gives the play loop a real, tool-sourced exploration mechanic
rather than free-form narration. Arriving advances the clock by one time-of-day
phase by default (the "travel pace" / calendar primitive), rolling the day over
at night.
"""

from __future__ import annotations

from models import Campaign, Location

# The in-world day, in order. Travel advances along this cycle; passing the end
# rolls over into the next day.
PHASES: tuple[str, ...] = ("morning", "afternoon", "evening", "night")


def reachable(campaign: Campaign) -> list[Location]:
    """The locations directly reachable from the party's current location.

    Empty when there is no current location, or when its `connections` name no
    known locations (dangling ids are skipped, not errored).
    """
    cur_id = campaign.current_location_id
    if cur_id is None:
        return []
    cur = campaign.locations.get(cur_id)
    if cur is None:
        return []
    out: list[Location] = []
    for dest_id in cur.connections:
        dest = campaign.locations.get(dest_id)
        if dest is not None:
            out.append(dest)
    return out


def advance_clock(campaign: Campaign, steps: int = 1) -> tuple[int, str]:
    """Advance the in-world clock by `steps` time-of-day phases (mutates campaign).

    Rolls `day` forward once per full lap of PHASES. A `time_of_day` that isn't
    one of the four canonical phases is normalized to the start of the cycle
    before advancing. `steps <= 0` is a no-op. Returns the new (day, time_of_day).
    """
    if steps <= 0:
        return campaign.day, campaign.time_of_day
    try:
        idx = PHASES.index(campaign.time_of_day)
    except ValueError:
        idx = 0  # normalize an unknown phase to the canonical cycle
    total = idx + steps
    campaign.day += total // len(PHASES)
    campaign.time_of_day = PHASES[total % len(PHASES)]
    return campaign.day, campaign.time_of_day


def travel_to(campaign: Campaign, destination_id: str, advance_time: bool = False) -> dict:
    """Move the party to `destination_id` (mutates campaign).

    Rules:
      * the destination must be a known location;
      * if the party has a current location, the destination must be in its
        `connections` (you travel along edges you can see) — otherwise a
        ValueError lists the reachable exits;
      * traveling to the location you're already in is rejected (no silent
        no-op / spurious time cost);
      * with no current location set, this is initial placement: any known
        location is allowed.

    Marks the destination visited. The clock advances only when `advance_time` is
    True — short moves within a site (room to room in a dungeon) should leave it
    False so a brief crawl doesn't burn a whole day; pass True for a long or
    overland journey. Returns a result dict the DM can narrate from:
    ``{from, to, to_name, first_visit, day, time_of_day, reachable}``.
    """
    dest = campaign.locations.get(destination_id)
    if dest is None:
        raise ValueError(f"unknown location id {destination_id!r}")

    cur_id = campaign.current_location_id
    if cur_id is not None:
        if destination_id == cur_id:
            raise ValueError(f"already at {dest.name!r}")
        cur = campaign.locations.get(cur_id)
        connections = cur.connections if cur is not None else []
        if destination_id not in connections:
            exits = ", ".join(
                f"{loc.id} ({loc.name})" for loc in reachable(campaign)
            ) or "(none)"
            raise ValueError(
                f"cannot travel to {destination_id!r}: not connected to the current "
                f"location. Reachable from here: {exits}"
            )

    first_visit = not dest.visited
    dest.visited = True
    campaign.current_location_id = destination_id

    if advance_time:
        advance_clock(campaign, 1)

    return {
        "from": cur_id,
        "to": dest.id,
        "to_name": dest.name,
        "first_visit": first_visit,
        "day": campaign.day,
        "time_of_day": campaign.time_of_day,
        "reachable": [{"id": loc.id, "name": loc.name} for loc in reachable(campaign)],
    }


def look_around(campaign: Campaign) -> dict:
    """Describe where the party stands and the exits they can take.

    Returns ``{location, exits, day, time_of_day}``. ``location`` is None when
    the party hasn't been placed yet. Each exit carries whether it's been
    visited so the DM knows what's new; dangling connection ids are dropped.
    """
    cur_id = campaign.current_location_id
    cur = campaign.locations.get(cur_id) if cur_id is not None else None
    location = (
        {
            "id": cur.id,
            "name": cur.name,
            "description": cur.description,
            "notes": cur.notes,
            "visited": cur.visited,
        }
        if cur is not None
        else None
    )
    exits = [
        {"id": loc.id, "name": loc.name, "visited": loc.visited}
        for loc in reachable(campaign)
    ]
    return {
        "location": location,
        "exits": exits,
        "day": campaign.day,
        "time_of_day": campaign.time_of_day,
    }
