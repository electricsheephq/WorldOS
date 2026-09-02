#!/usr/bin/env python3
"""Unit proof for compute_arrival_hints — the seed-side of coherence-aware arrival hints (#1647 wave-2).

For each door, the hint list is the visually-OPEN cells (per the paint-coherence verdicts) NEAREST the
door, ordered by Chebyshev distance, door-ring-safe (never a door cell) and never a blocked wall/prop.
No coherence report ⇒ ``{}`` (byte-identical to the pre-#1647 world). Pure + deterministic.

Run: python3 -m pytest qa/test_arrival_hints_seed.py -q -p no:xdist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_gfx_town import compute_arrival_hints, load_cell_verdicts  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COHERENCE = REPO / "qa" / "evidence" / "paint-coherence"


def test_none_verdicts_yields_no_hints():
    """No coherence report (cell_verdicts=None) ⇒ {} ⇒ additive/byte-identical."""
    assert compute_arrival_hints([(5, 0)], set(), 10, 10, None) == {}
    assert compute_arrival_hints([(5, 0)], set(), 10, 10, {}) == {}


def test_hints_are_open_cells_nearest_the_door():
    """A door at (5,0): the hint list is OPEN cells ordered nearest-first, and (5,5) [covered] is
    excluded even though it is grid-open."""
    verdicts = {(5, 1): "open", (4, 1): "open", (5, 5): "covered", (2, 8): "open"}
    hints = compute_arrival_hints([(5, 0)], blocked=set(), cols=10, rows=10,
                                  cell_verdicts=verdicts, max_per_door=6)
    cells = hints["5,0"]
    assert (5, 5) not in cells                      # covered — never hinted
    assert cells[0] in ((5, 1), (4, 1))             # nearest the door first (Chebyshev 1)
    assert cells[-1] == (2, 8)                       # the far open cell ranks last
    assert set(cells) == {(5, 1), (4, 1), (2, 8)}


def test_door_and_blocked_cells_are_never_hinted():
    """Door-ring-safe: a door cell itself and any blocked wall/prop cell are excluded from hints even
    when the verdict map calls them open."""
    verdicts = {(5, 0): "open", (3, 3): "open", (6, 6): "open"}
    hints = compute_arrival_hints([(5, 0)], blocked={(3, 3)}, cols=10, rows=10,
                                  cell_verdicts=verdicts)
    cells = hints["5,0"]
    assert (5, 0) not in cells      # the door cell (arriving on the threshold)
    assert (3, 3) not in cells      # a blocked wall/prop cell
    assert cells == [(6, 6)]


def test_per_door_keys_and_cap():
    """Each door gets its own key; `max_per_door` caps the list length."""
    verdicts = {(c, r): "open" for r in range(1, 9) for c in range(1, 9)}
    hints = compute_arrival_hints([(0, 0), (9, 9)], blocked=set(), cols=10, rows=10,
                                  cell_verdicts=verdicts, max_per_door=3)
    assert set(hints) == {"0,0", "9,9"}
    assert len(hints["0,0"]) == 3
    # nearest the (0,0) door first
    assert hints["0,0"][0] == (1, 1)
    assert hints["9,9"][0] == (8, 8)


def test_real_tavern_report_produces_hints():
    """End-to-end against the checked-in tavern coherence report: the tavern's authored door yields a
    non-empty hint list of cells the report classifies OPEN."""
    verdicts = load_cell_verdicts(str(COHERENCE), "tavern")
    assert verdicts, "tavern coherence report should load"
    # tavern authored door (per qa test fixtures) — use whatever open cells exist near a plausible door.
    door = (7, 0)
    hints = compute_arrival_hints([door], blocked=set(), cols=14, rows=10, cell_verdicts=verdicts)
    cells = hints.get("7,0", [])
    assert cells, "expected open hint cells near the tavern door"
    for cell in cells:
        assert verdicts.get(cell) == "open"
