"""Offline unit tests for qa/quest_progress.py — the A-T per-beat quest-stage telemetry.

Drives a SCRIPTED campaign (seed the one-call adventure fixture, then call the engine's
complete_objective / complete_quest / location moves DIRECTLY — no LLM, no MCP) and asserts the
stage stamps land, in ARC order, monotonically. This is the whole point of the objective-based
detectors: every stage is reachable without a `claude -p` in the loop.

Single-process (the engine is not fork-safe under xdist):
    uv run --directory servers/engine python -m pytest qa/test_quest_progress.py -p no:xdist
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

QA = Path(__file__).resolve().parent
REPO = QA.parent
SEEDER = QA / "seed_adventure_demo.py"

sys.path.insert(0, str(QA))
sys.path.insert(0, str(REPO / "servers" / "engine"))

import quest_progress as qp  # noqa: E402


def _seed(state_dir: Path) -> str:
    """Seed the adventure fixture into ``state_dir`` via the real seeder; return the campaign id."""
    os.environ["WORLDOS_STATE_DIR"] = str(state_dir)
    proc = subprocess.run(
        [sys.executable, str(SEEDER), str(state_dir)],
        cwd=str(REPO), env={**os.environ, "WORLDOS_STATE_DIR": str(state_dir)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"seed failed: {proc.stderr}\n{proc.stdout}"
    return proc.stdout.strip().splitlines()[-1]


@pytest.fixture()
def seeded(tmp_path):
    """A freshly-seeded campaign + its engine module, bound to a per-test state dir."""
    state = tmp_path / "state"
    cid = _seed(state)
    os.environ["WORLDOS_STATE_DIR"] = str(state)
    server = qp._import_server(str(state))
    return server, str(state), cid


def _quest(server, cid: str) -> dict:
    return server.get_quests(cid)["quests"][0]


def _obj(server, cid: str, needle: str) -> str:
    """The full objective text matching a substring (so tests need not echo it byte-for-byte)."""
    q = _quest(server, cid)
    hits = [o for o in q["objectives"] if needle.lower() in o.lower()]
    assert len(hits) == 1, f"{needle!r} matched {hits}"
    return hits[0]


def _stages(trace_path: str) -> list[str]:
    import json
    data = json.loads(Path(trace_path).read_text())
    return [s["stage"] for s in data["stamps"]]


def _set_location(server, cid: str, loc_id: str) -> None:
    c = server._require(cid)
    c.current_location_id = loc_id
    server.save_campaign(c)


# ── tests ─────────────────────────────────────────────────────────────────────────────────────

def test_fresh_run_has_no_stamps(seeded):
    server, state, cid = seeded
    res = qp.poll(state, cid, beat=1)
    assert res["quest_status"] == "active"
    assert res["newly_stamped"] == []
    assert _stages(res["trace_path"]) == []


def test_speak_objective_stamps_reached_and_accepted_in_order(seeded):
    server, state, cid = seeded
    q = _quest(server, cid)
    server.complete_objective(cid, q["id"], _obj(server, cid, "Speak with"))
    res = qp.poll(state, cid, beat=2)
    # A single poll that observes both stages stamps them in ARC order.
    assert _stages(res["trace_path"]) == ["reached_giver", "quest_accepted"]
    assert res["quest_status"] == "active"


def test_reached_giver_via_location(seeded):
    server, state, cid = seeded
    q = _quest(server, cid)
    giver_loc = server._require(cid).characters[q["giver_id"]].location_id
    _set_location(server, cid, giver_loc)
    res = qp.poll(state, cid, beat=1)
    assert "reached_giver" in _stages(res["trace_path"])
    stamp = next(s for s in _load(res["trace_path"])["stamps"] if s["stage"] == "reached_giver")
    assert stamp["signal"].startswith("location:")


def test_full_arc_stamps_all_six_in_order(seeded):
    server, state, cid = seeded
    q = _quest(server, cid)
    qid = q["id"]

    qp.poll(state, cid, beat=1)  # nothing yet

    server.complete_objective(cid, qid, _obj(server, cid, "Speak with"))
    qp.poll(state, cid, beat=2)  # reached_giver, quest_accepted

    _set_location(server, cid, q["location_id"])  # the crypt
    qp.poll(state, cid, beat=3)  # entered_dungeon

    server.complete_objective(cid, qid, _obj(server, cid, "Clear the crypt"))  # no stage of its own
    server.complete_objective(cid, qid, _obj(server, cid, "Slay the goblin boss"))
    qp.poll(state, cid, beat=4)  # boss_dead

    # Completing the LAST remaining objective auto-resolves the quest -> status flips.
    server.complete_objective(cid, qid, _obj(server, cid, "Return to Maera"))
    res = qp.poll(state, cid, beat=5)  # reward_received, quest_completed

    assert _stages(res["trace_path"]) == list(qp.STAGES)
    assert res["quest_status"] == "completed"
    # beats are non-decreasing along the arc.
    beats = [s["beat"] for s in _load(res["trace_path"])["stamps"]]
    assert beats == sorted(beats)


def test_terminal_signals_are_never_gated(seeded):
    """A real terminal signal (a slain-boss objective) stamps boss_dead even when the intervening
    entered_dungeon location-beat was never independently caught — telemetry must never DROP a real
    signal over a missed intermediate. Stamps still emerge in STAGES (arc) order within the poll."""
    server, state, cid = seeded
    q = _quest(server, cid)
    server.complete_objective(cid, q["id"], _obj(server, cid, "Speak with"))
    server.complete_objective(cid, q["id"], _obj(server, cid, "Slay the goblin boss"))
    res = qp.poll(state, cid, beat=2)
    stages = _stages(res["trace_path"])
    assert stages == ["reached_giver", "quest_accepted", "boss_dead"]  # arc order, entered_dungeon skipped


def test_boss_dead_via_snapshot_signal(seeded):
    server, state, cid = seeded
    q = _quest(server, cid)
    server.complete_objective(cid, q["id"], _obj(server, cid, "Speak with"))
    _set_location(server, cid, q["location_id"])  # entered_dungeon reachable
    # Kill the boss in the snapshot, combat resolved -> the secondary boss_dead detector fires.
    c = server._require(cid)
    boss = next(ch for ch in c.characters.values() if "boss" in ch.name.lower())
    boss.dead = True
    c.combat.active = False
    server.save_campaign(c)
    res = qp.poll(state, cid, beat=3)
    stages = _stages(res["trace_path"])
    assert "entered_dungeon" in stages and "boss_dead" in stages
    stamp = next(s for s in _load(res["trace_path"])["stamps"] if s["stage"] == "boss_dead")
    assert stamp["signal"].startswith("snapshot:")


def test_stamps_are_idempotent(seeded):
    server, state, cid = seeded
    q = _quest(server, cid)
    server.complete_objective(cid, q["id"], _obj(server, cid, "Speak with"))
    qp.poll(state, cid, beat=2)
    first = _stages_full(state)
    # Re-polling the same state adds no new stamps.
    res = qp.poll(state, cid, beat=3)
    assert res["newly_stamped"] == []
    assert _stages_full(state) == first


def test_quest_completed_via_complete_quest(seeded):
    """A direct complete_quest (status flip) stamps quest_completed even if the interior stages
    were reached out of the objective path (monotonic: all earlier stages detected the same poll)."""
    server, state, cid = seeded
    q = _quest(server, cid)
    qid = q["id"]
    server.complete_objective(cid, qid, _obj(server, cid, "Speak with"))
    _set_location(server, cid, q["location_id"])
    server.complete_objective(cid, qid, _obj(server, cid, "Slay the goblin boss"))
    server.complete_quest(cid, qid, status="completed")
    res = qp.poll(state, cid, beat=9)
    assert res["quest_status"] == "completed"
    # quest_completed is a terminal status flip: it stamps even though reward_received never fired.
    assert _stages(res["trace_path"])[-1] == "quest_completed"
    assert "reward_received" not in _stages(res["trace_path"])
    assert set(_stages(res["trace_path"])) >= {"reached_giver", "quest_accepted",
                                               "entered_dungeon", "boss_dead", "quest_completed"}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────

def _load(trace_path: str) -> dict:
    import json
    return json.loads(Path(trace_path).read_text())


def _stages_full(state: str) -> list[str]:
    return _stages(str(Path(state) / "quest_trace.json"))
