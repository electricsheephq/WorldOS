"""Deterministic pytest wrapper for the engine-only combat smoke (Track 2d).

Tier-0 / fast-gate home: NO LLM, runs in a few seconds, fixed seed. It asserts — through the
real engine verbs + the engine-run combat loop — that EVERY mechanic class fires in a random-vs-
random auto-combat AND that a representative spell from EVERY category resolves correctly. This
is the trustworthy MECHANICAL signal that's independent of the slow/hangy LLM scorer.

It imports qa/combat_smoke.py and drives run_part1 / run_part2 directly (so it asserts the
structured result, not stdout). Single-process friendly (-p no:xdist). State goes to per-call
temp dirs the smoke creates; nothing touches qa/scores.db.

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_combat_smoke.py -q -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

# The engine root must be importable. combat_smoke bootstraps it on import, but be explicit so
# the wrapper works whether launched from repo root or via `uv run --directory servers/engine`.
_ENGINE_DIR = QA_DIR.parent / "servers" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import combat_smoke as smoke  # noqa: E402

_SEED = 4242


def _server_store():
    import os
    os.environ["WORLDOS_COMBAT_TEST"] = "1"
    smoke._install_deterministic_ids(_SEED)
    import server
    import store as store_mod
    return server, store_mod


# ── PART 1: every mechanic class fires ─────────────────────────────────────────────────

def test_part1_every_mechanic_class_fires():
    """The random-vs-random auto-combat (+ the scripted save/condition/concentration/resource
    assist) fires EVERY mechanic class within the seeded budget. A class that never fires across
    the seeds is a real coverage hole or engine bug — fail loudly."""
    server, store_mod = _server_store()
    checks, summaries = smoke.run_part1(server, store_mod, _SEED, fast=False, max_seeds=4)
    not_fired = [ck.desc for ck in checks.values() if not ck.fired]
    assert not not_fired, f"mechanic class(es) never fired across seeds: {not_fired}"
    # The auto-loop actually ran a fight to a terminal state (not an empty no-op).
    assert summaries, "no fights ran"
    assert any(s["turns"] > 0 for s in summaries), "no turns were taken in any fight"
    assert all(not s["round_cap_hit"] for s in summaries), \
        f"a fight hit the round cap (non-terminating?): {summaries}"


def test_part1_auto_loop_native_classes_fire_without_the_scripted_assist():
    """Guard that the AUTO loop itself (greedy-v1 weapon attacks + next_turn) natively produces
    the hit/miss/crit/XP/death-save classes — independent of the scripted assist — so a future
    regression in the loop (not the assist) is caught here too."""
    import dice as dice_mod
    import combat_loop
    import os
    import tempfile
    server, store_mod = _server_store()

    native = {k: smoke.Check(k, k) for k in ("hit", "miss", "xp", "death_save")}
    fired_all = False
    for off in range(5):
        os.environ["WORLDOS_STATE_DIR"] = tempfile.mkdtemp(prefix=f"cs_native_{off}_")
        dice_mod.reseed_process_rng(_SEED + off)
        cid, pc_ids, mon_ids, caster_id, martial_id = smoke._seed_party_vs_monsters(
            server, store_mod, off, fast=False
        )
        combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=30)
        smoke._scan_event_stream(server, store_mod, cid, native)
        if all(ck.fired for ck in native.values()):
            fired_all = True
            break
    missing = [k for k, ck in native.items() if not ck.fired]
    assert fired_all, f"auto-loop native classes never fired (no scripted assist): missing {missing}"


def test_part1_is_deterministic_under_a_fixed_seed():
    """A fixed seed reproduces the EXACT run — same victor / rounds / turns. The smoke installs a
    seeded id generator + reseeds the dice RNG, so the whole fight is byte-reproducible."""
    server, store_mod = _server_store()
    _, sums_a = smoke.run_part1(server, store_mod, _SEED, fast=False, max_seeds=2)
    # Re-install the seeded ids (run_part1 consumed draws) and re-run from the same seed.
    smoke._install_deterministic_ids(_SEED)
    _, sums_b = smoke.run_part1(server, store_mod, _SEED, fast=False, max_seeds=2)
    sig_a = [(s["seed"], s["victor"], s["rounds"], s["turns"]) for s in sums_a]
    sig_b = [(s["seed"], s["victor"], s["rounds"], s["turns"]) for s in sums_b]
    assert sig_a == sig_b, f"non-deterministic fight: {sig_a} != {sig_b}"


# ── PART 2: every representative spell resolves ────────────────────────────────────────

def test_part2_all_representative_spells_resolve():
    """Every category's representative spell resolves with the right gauge movement — no THROW,
    no WRONG-EFFECT. These are the curated full-automation spells (all 6 owner-named categories)
    + a srd524-only control spell (Hold Person) + an engine AoE (Fireball)."""
    server, store_mod = _server_store()
    results, not_swept, _, total = smoke.run_part2(server, store_mod, _SEED)
    threw = [(r.name, r.detail) for r in results if r.status == "THREW"]
    wrong = [(r.name, r.detail) for r in results if r.status == "WRONG-EFFECT"]
    assert not threw, f"spell(s) THREW during cast: {threw}"
    assert not wrong, f"spell(s) applied the WRONG effect: {wrong}"
    assert len(results) >= 10, f"expected >=10 representative casts, got {len(results)}"
    # The not-swept accounting is honest + non-empty (the ~330 srd524-only records).
    assert not_swept > 0 and total > len(results), \
        "not-swept accounting looks wrong — every castable spell should not be in the swept set"


def test_part2_covers_every_owner_named_category():
    """The sweep covers all six owner-named categories plus control + AoE — so 'all the spells
    work' isn't silently narrowed to one family."""
    server, store_mod = _server_store()
    results, _, _, _ = smoke.run_part2(server, store_mod, _SEED)
    cats = {r.category for r in results}
    required = {
        "attack-cantrip", "auto-hit", "save-for-half", "heal", "buff-conc",
        "utility-buff", "condition-control", "aoe",
    }
    assert required <= cats, f"missing categories: {required - cats}"
