"""WS0 forcing meta-test — the engagement-system MANIFEST guard (mirrors
test_tool_schema_budget.py's "enumerate the live set, assert it == a reviewed constant" pattern,
so any addition/removal of a tracked story system is a deliberate, VISIBLE diff in review).

The keystone discipline: qa/feature_engagement.SYSTEMS is the live manifest of authored story
systems the engagement scorer covers; REVIEWED_SYSTEM_IDS is the frozenset a human signed off on.
If someone adds a system without updating the reviewed set (or vice-versa), this test fails — the
manifest can never silently drift out of coverage (the exact failure WS0 exists to prevent).

Lives in servers/engine/tests (run with the engine suite) but imports the qa-side module via the
sys.path.insert(qa) pattern the sibling qa tests use.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Import the qa-side module (it has no engine dependency — pure stdlib + story_readout).
_QA = Path(__file__).resolve().parents[3] / "qa"
sys.path.insert(0, str(_QA))

import feature_engagement as fe  # noqa: E402


def test_manifest_matches_reviewed_ids():
    """The live SYSTEMS set must equal the human-reviewed REVIEWED_SYSTEM_IDS — adding or
    removing a system without updating the reviewed constant is a failing, visible diff."""
    live = {s.id for s in fe.SYSTEMS}
    assert live == set(fe.REVIEWED_SYSTEM_IDS), (
        "engagement-system manifest drifted from the reviewed set. "
        f"only-in-SYSTEMS={sorted(live - set(fe.REVIEWED_SYSTEM_IDS))}; "
        f"only-in-REVIEWED={sorted(set(fe.REVIEWED_SYSTEM_IDS) - live)}. "
        "Update REVIEWED_SYSTEM_IDS deliberately when changing the manifest."
    )


def test_reviewed_ids_are_exactly_eleven():
    """WS0 shipped 10 reviewed systems; HV4 (#1326) adds `library_reuse` -> 11. The count is pinned
    so a careless drop/add is loud (raise this only with a justification, like the schema-budget
    guard)."""
    assert len(fe.REVIEWED_SYSTEM_IDS) == 11, sorted(fe.REVIEWED_SYSTEM_IDS)
    assert len(fe.SYSTEMS) == 11


def test_every_severity_is_warn_or_fatal():
    """Severity is a closed vocabulary; a typo'd severity must fail (the gate reads it to decide
    fatal-ness)."""
    bad = [(s.id, s.severity) for s in fe.SYSTEMS if s.severity not in ("fatal", "warn")]
    assert not bad, f"systems with an out-of-vocabulary severity: {bad}"


def test_ws0_ships_all_warn():
    """WS0 invariant: EVERY system ships severity='warn' this PR (FATAL graduation is a future
    post-sweep PR). An accidental 'fatal' here would add a new RED to a currently-green pipeline."""
    fatal = [s.id for s in fe.SYSTEMS if s.severity == "fatal"]
    assert not fatal, (
        f"these systems are FATAL but WS0 ships all-WARN (graduation is a future PR): {fatal}"
    )


def test_blocked_systems_stay_warn():
    """faction_arc + companion_quest_arc carry a known snapshot-only ambiguity (seeded-but-locked
    vs never-seeded) and must NEVER be FATAL until that spike is resolved."""
    by_id = {s.id: s for s in fe.SYSTEMS}
    for sid in fe.BLOCKED_SYSTEM_IDS:
        assert sid in by_id, f"blocked id {sid!r} is not in the manifest"
        assert by_id[sid].severity == "warn", f"blocked system {sid!r} must stay WARN"


def test_ids_unique_and_snake_case():
    ids = [s.id for s in fe.SYSTEMS]
    assert len(ids) == len(set(ids)), f"duplicate system ids: {ids}"
    bad = [i for i in ids if not fe._is_snake(i)]
    assert not bad, f"non snake_case system ids: {bad}"


def test_precondition_and_detector_are_callable():
    for s in fe.SYSTEMS:
        assert callable(s.precondition), f"{s.id}.precondition is not callable"
        assert callable(s.detector), f"{s.id}.detector is not callable"
