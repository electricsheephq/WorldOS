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

import difflib
from collections import deque

from models import Campaign, Location
from scene_grid import ensure_scene_grid  # A1 SceneGrid emitter (engine sole-writer)

# The in-world day, in order. Travel advances along this cycle; passing the end
# rolls over into the next day.
PHASES: tuple[str, ...] = ("morning", "afternoon", "evening", "night")


def _bfs_first_step(campaign: Campaign, start_id: str, dest_id: str) -> Location | None:
    """The FIRST hop a shortest path from `start_id` to `dest_id` takes, or None when no
    path exists (F14-5 / #812). A pure breadth-first walk of the location graph the engine
    already holds — so a rejection of a known-but-not-adjacent dest can name the next step
    ("go via the Cellar") instead of leaving the DM to step blindly. Read-only: no mutation."""
    if start_id == dest_id:
        return None
    # parent map: child_id -> the immediate-neighbour-of-start it was first reached through
    first_hop: dict[str, str] = {}
    seen: set[str] = {start_id}
    q: deque[str] = deque()
    start = campaign.locations.get(start_id)
    for nxt in (start.connections if start is not None else []):
        if nxt in campaign.locations and nxt not in seen:
            seen.add(nxt)
            first_hop[nxt] = nxt  # a direct neighbour's first hop is itself
            q.append(nxt)
    while q:
        cur_id = q.popleft()
        if cur_id == dest_id:
            return campaign.locations.get(first_hop[cur_id])
        cur = campaign.locations.get(cur_id)
        for nxt in (cur.connections if cur is not None else []):
            if nxt in campaign.locations and nxt not in seen:
                seen.add(nxt)
                first_hop[nxt] = first_hop[cur_id]  # inherit the originating first hop
                q.append(nxt)
    return None


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
        # F14-5 (#812): a bare "unknown location id" on a typo sends the DM guessing. Surface a
        # did-you-mean of the nearest known location id/name so the next call recovers. The
        # "unknown location id {id!r}" key/prefix is preserved (additive tail).
        corpus = {loc.id: loc.name for loc in campaign.locations.values()}
        candidates = list(corpus) + [n for n in corpus.values() if n]
        near = difflib.get_close_matches(destination_id, candidates, n=3, cutoff=0.5)
        # map any matched display-name back to "id (name)" so the DM gets the id to pass
        name_to_id = {n: i for i, n in corpus.items() if n}
        shown = []
        for m in near:
            lid = m if m in corpus else name_to_id.get(m, m)
            shown.append(f"{lid} ({corpus.get(lid, m)})")
        hint = f" — did you mean {', '.join(dict.fromkeys(shown))}?" if shown else ""
        raise ValueError(f"unknown location id {destination_id!r}{hint}")

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
            # F14-5 (#812): the engine holds the WHOLE graph — a known-but-not-adjacent dest
            # gets a BFS first-step route hint ("travel via <id> (<name>)") instead of only the
            # direct exits, so a multi-hop journey doesn't bounce 18% of the time. A genuinely
            # disconnected dest says so plainly. Pure read; no mutation on a rejected travel.
            step = _bfs_first_step(campaign, cur_id, destination_id)
            if step is not None:
                route = (f" To reach {dest.name!r} ({dest.id}), travel via "
                         f"{step.id} ({step.name}).")
            else:
                route = f" There is no known route from here to {dest.name!r}."
            raise ValueError(
                f"cannot travel to {destination_id!r}: not connected to the current "
                f"location. Reachable from here: {exits}.{route}"
            )

    first_visit = not dest.visited
    dest.visited = True
    campaign.current_location_id = destination_id

    # A1 — the SceneGrid emitter (engine sole-writer): the party is arriving at `dest`, so
    # make sure it has a Tier-1 spatial layout to render. GUARDED (no-op if dest already
    # carries a scene_grid — a re-visit never re-rolls) + deterministic (seeded off
    # (world_id, location_id), isolated from the combat dice stream). Additive: a world
    # with no world_id still emits a deterministic grid; an old snapshot round-trips. The
    # MCP wrapper in server.py save_campaigns the mutated campaign after this returns.
    ensure_scene_grid(campaign.world_id, dest)

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


def _cast_at(campaign: Campaign, loc_id: str) -> list[dict]:
    """The local cast anchored to a location — NPCs/monsters/companions — for the
    "who's here / who's nearby" spatial context (fables-style). Players aren't
    listed (they ARE the party)."""
    return [
        {"id": ch.id, "name": ch.name, "kind": ch.kind, "attitude": ch.attitude}
        for ch in campaign.characters.values()
        if ch.location_id == loc_id and ch.kind in ("npc", "monster", "companion")
    ]


def look_around(campaign: Campaign) -> dict:
    """Describe where the party stands, who's around, and the exits they can take.

    Returns ``{location, here, exits, day, time_of_day}``. ``location`` is None when
    the party hasn't been placed yet and now also carries its ``region`` (the parent
    zone). ``here`` is the local cast in speaking distance (same location). Each exit
    carries ``visited`` plus ``walk_minutes`` (from the location's `travel_times`, or
    None when unset → the DM uses a sensible default) and ``characters`` (who's over
    there, out of speaking distance) — mirroring fables' nearby-locations context.
    Dangling connection ids are dropped.
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
            "region": cur.region,
        }
        if cur is not None
        else None
    )
    here = _cast_at(campaign, cur.id) if cur is not None else []
    cur_times = cur.travel_times if cur is not None else {}
    exits = [
        {
            "id": loc.id,
            "name": loc.name,
            "visited": loc.visited,
            "walk_minutes": cur_times.get(loc.id),
            "characters": [ch["name"] for ch in _cast_at(campaign, loc.id)],
        }
        for loc in reachable(campaign)
    ]
    return {
        "location": location,
        "here": here,
        "exits": exits,
        "day": campaign.day,
        "time_of_day": campaign.time_of_day,
    }
