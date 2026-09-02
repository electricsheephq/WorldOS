#!/usr/bin/env python3
"""Live-save migration: bake coherence-aware ARRIVAL HINTS into an EXISTING campaign (#1647 wave-2).

The owner-facing win: their live campaign must NOT be wiped or reseeded to get the "Aldric on the
tavern bar" fix. This script loads a live campaign through the SAME engine store the seeds use, and
ADDITIVELY writes ``scene_grid.arrival_hints`` into every location that has a paint-coherence report
(the walkslice rooms: crypt / tavern / shop / tavern_snug / throne_hall). It ALSO relocates any party
token currently standing on a coherence-``covered`` cell to the nearest visually-OPEN cell — so the
"standing on the bar" state ends the moment the migration runs, not just on the next door crossing.

SOLE-WRITER discipline (load-bearing): the engine is the only writer of ``snapshot.json``. Run this
with the owner engine STOPPED. It uses the exact same store API the reseed scripts use
(``server.load_campaign`` -> mutate -> ``server.save_campaign``, atomic temp-file + os.replace under
the campaign lock), so there is never a second concurrent writer. It is ADDITIVE: it only ADDS hint
keys the seed would have baked and only MOVES a token off a covered cell — it never drops or rewrites
any other field, and a location with no coherence report is left byte-identical.

Usage:
  python3 qa/migrate_arrival_hints.py <state_dir> <campaign_id> [--dry-run] [--coherence-dir DIR]

  --dry-run        compute + print what WOULD change; write NOTHING (no save).
  --coherence-dir  override the coherence-report directory (default: qa/evidence/paint-coherence).

Prints a per-location summary (hint counts + any relocations). The orchestrator runs the real
invocation against the owner's state — this script never targets it by default.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))


def _room_candidates(loc, scene_grid) -> list[str]:
    """Ordered room-name candidates to look a coherence report up by. The seed pipeline names a
    location by its room (crypt/tavern/...), so the location id is the primary key; the scene_id
    tail (``<cid>:<town>_<room>`` or ``<cid>:<room>``) is the fallback."""
    out: list[str] = []
    lid = getattr(loc, "id", None)
    if isinstance(lid, str) and lid:
        out.append(lid)
    sgl = getattr(scene_grid, "location_id", None)
    if isinstance(sgl, str) and sgl and sgl not in out:
        out.append(sgl)
    sid = getattr(scene_grid, "scene_id", None)
    if isinstance(sid, str) and ":" in sid:
        tail = sid.split(":", 1)[1]
        for cand in (tail, tail.split("_", 1)[-1]):
            if cand and cand not in out:
                out.append(cand)
    return out


def _report_room(coherence_dir: str, loc, scene_grid) -> str | None:
    """The first candidate room name that HAS a ``<room>_coherence_report.json`` here, else None."""
    for room in _room_candidates(loc, scene_grid):
        if (Path(coherence_dir) / f"{room}_coherence_report.json").is_file():
            return room
    return None


def _nearest_open(cell, cols: int, rows: int, blocked: set, verdicts: dict):
    """The nearest visually-OPEN, non-blocked cell to ``cell`` (Chebyshev, stable (r, c) tiebreak),
    or None when the room has no open floor left. ``blocked`` includes wall/prop terrain AND the cells
    already claimed by other party members so two relocated tokens never stack."""
    opens = [
        (c, r)
        for r in range(rows) for c in range(cols)
        if verdicts.get((c, r)) == "open" and (c, r) not in blocked
    ]
    if not opens:
        return None
    cx, cy = int(cell[0]), int(cell[1])
    return min(opens, key=lambda p: (max(abs(p[0] - cx), abs(p[1] - cy)), p[1], p[0]))


def migrate(state_dir: str, campaign_id: str, coherence_dir: str, dry_run: bool) -> dict:
    """Load the campaign, bake arrival hints + relocate covered party tokens, save (unless dry-run).
    Returns a summary dict (also printed). Pure w.r.t. any location without a coherence report."""
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, "..", "servers", "engine"))
    import server  # noqa: PLC0415
    import scene_grid as scene_grid_mod  # noqa: PLC0415
    from seed_gfx_town import compute_arrival_hints, load_cell_verdicts  # noqa: PLC0415

    c = server.load_campaign(campaign_id)
    if c is None:
        raise SystemExit(f"no campaign with id {campaign_id!r} under {state_dir!r}")

    party_ids = set(getattr(c, "party", []) or [])
    current_loc = getattr(c, "current_location_id", None)
    summary: dict = {"campaign_id": campaign_id, "state_dir": state_dir, "dry_run": dry_run,
                     "locations": [], "total_hint_doors": 0, "total_relocations": 0}

    def _at(ch, loc_id: str) -> bool:
        """A character is IN ``loc_id`` if its ``location_id`` says so, OR (mirroring how the engine
        co-locates the party) it carries no explicit location and this is the campaign's current
        location — so a freshly-seated PC standing on the current room is still relocated."""
        lid = getattr(ch, "location_id", None)
        return lid == loc_id or (lid is None and loc_id == current_loc)

    for lid, loc in c.locations.items():
        grid = getattr(loc, "scene_grid", None)
        if grid is None:
            continue
        room = _report_room(coherence_dir, loc, grid)
        if room is None:
            continue
        verdicts = load_cell_verdicts(coherence_dir, room)
        if not verdicts:
            continue
        cols = int(grid.grid.cols)
        rows = int(grid.grid.rows)
        if cols <= 0 or rows <= 0:
            continue
        door_cells = [tuple(d) for d in (getattr(grid, "door_cells", None) or [])]
        blocked = {tuple(x) for x in scene_grid_mod.impassable_cells(grid, cols, rows)}

        # (1) arrival hints — ADDITIVE merge (never clobber a door key already present).
        computed = compute_arrival_hints(door_cells, blocked, cols, rows, verdicts)
        existing = dict(getattr(grid, "arrival_hints", None) or {})
        added = {k: v for k, v in computed.items() if k not in existing}
        merged = {**existing, **added}

        # (2) relocate party tokens off coherence-COVERED cells. Occupancy = every member's cell in
        # this room; move a covered mover to the nearest open cell not already claimed.
        occupied: set = set()
        for ch in c.characters.values():
            if _at(ch, loc.id) and getattr(ch, "stage_cell", None) is not None:
                occupied.add((int(ch.stage_cell[0]), int(ch.stage_cell[1])))
        relocations: list[dict] = []
        for cid, ch in c.characters.items():
            travels = cid in party_ids or getattr(ch, "kind", "") in ("player", "companion")
            if not travels or not _at(ch, loc.id):
                continue
            sc = getattr(ch, "stage_cell", None)
            if sc is None:
                continue
            here = (int(sc[0]), int(sc[1]))
            if verdicts.get(here) != "covered":
                continue
            avoid = blocked | (occupied - {here})
            dest = _nearest_open(here, cols, rows, avoid, verdicts)
            if dest is None or dest == here:
                continue
            relocations.append({"character": cid, "from": list(here), "to": list(dest)})
            occupied.discard(here)
            occupied.add(dest)
            if not dry_run:
                ch.stage_cell = dest

        if not dry_run and added:
            grid.arrival_hints = merged

        summary["locations"].append({
            "location_id": lid, "room": room,
            "doors_with_hints": len(merged), "doors_added": len(added),
            "hint_counts": {k: len(v) for k, v in sorted(merged.items())},
            "relocations": relocations,
        })
        summary["total_hint_doors"] += len(added)
        summary["total_relocations"] += len(relocations)

    if not dry_run:
        server.save_campaign(c)

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Bake coherence-aware arrival hints into a live campaign.")
    ap.add_argument("state_dir")
    ap.add_argument("campaign_id")
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    ap.add_argument("--coherence-dir", default=os.path.join(HERE, "evidence", "paint-coherence"))
    args = ap.parse_args()

    summary = migrate(args.state_dir, args.campaign_id, args.coherence_dir, args.dry_run)

    mode = "DRY-RUN (no save)" if args.dry_run else "WROTE snapshot"
    print(f"[migrate_arrival_hints] {mode}: campaign={summary['campaign_id']} "
          f"state_dir={summary['state_dir']}")
    if not summary["locations"]:
        print("  no coherence-covered locations found — nothing to migrate (byte-identical).")
    for loc in summary["locations"]:
        print(f"  {loc['location_id']} (report={loc['room']}): "
              f"{loc['doors_added']} door(s) newly hinted, {loc['doors_with_hints']} total; "
              f"hint_counts={loc['hint_counts']}")
        for rel in loc["relocations"]:
            print(f"    relocate {rel['character']}: {rel['from']} (covered) -> {rel['to']} (open)")
    print(f"  TOTAL: {summary['total_hint_doors']} door(s) hinted, "
          f"{summary['total_relocations']} token(s) relocated across "
          f"{len(summary['locations'])} location(s).")


if __name__ == "__main__":
    main()
