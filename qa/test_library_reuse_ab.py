"""HV4 (#1326) — tests for the library-reuse A/B harness (qa/library_reuse_ab.py).

Exercises the DETERMINISTIC seed-level diff (offline, $0) against a tmp library pack, and asserts
the scored-duo PLAN is well-formed but NEVER executes a scored run in this build.

    uv run --directory servers/engine python -m pytest ../../qa/test_library_reuse_ab.py -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_QA = Path(__file__).resolve().parent
sys.path.insert(0, str(_QA))
sys.path.insert(0, str(_QA.parent / "servers" / "engine"))

import library_reuse_ab as ab  # noqa: E402


def _write_pack(root: Path, pack_name: str = "worldos-harvest") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pack.json").write_text(json.dumps({"name": pack_name, "version": "0.1.0"}), encoding="utf-8")
    qdir = root / "quests"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "quest_lib_relic.json").write_text(json.dumps({
        "artifact_id": "quest:lib:relic", "class": "quest", "tier": "stable",
        "scores": {"overall": 4.2}, "provenance": {"world": "baldurs-gate"},
        "payload": {"name": "The Stolen Relic", "hook": "recover a smuggled relic from the ring"},
    }), encoding="utf-8")


def test_seed_diff_library_first_adds_library_hooks(tmp_path):
    root = tmp_path / "library"
    _write_pack(root)
    rep = ab.seed_diff("baldurs-gate", "worldos-harvest", seed="t", session_beats=12,
                       library_root=str(root))
    pure = rep["arms"]["pure_gen"]["hooks"]
    lib = rep["arms"]["library_first"]["hooks"]
    # pure-gen seeds ZERO library-sourced hooks; library-first seeds >= 1.
    assert pure["library_sourced"] == 0
    assert lib["library_sourced"] >= 1
    assert rep["delta"]["library_sourced_hooks"] >= 1


def test_seed_diff_engagement_signal(tmp_path):
    root = tmp_path / "library"
    _write_pack(root)
    rep = ab.seed_diff("baldurs-gate", "worldos-harvest", seed="t", session_beats=12,
                       library_root=str(root))
    # library_reuse must be N/A (dormant) in pure-gen and NOT engaged there; in the library-first
    # arm it is seeded (present in quest_hooks) — a seed-only diff can't promote it to a Quest, so
    # it reads INERT (owed but not engaged), which is the correct "seeded but decorative" signal.
    pure_cov = rep["arms"]["pure_gen"]["engagement"]
    lib_cov = rep["arms"]["library_first"]["engagement"]
    assert "library_reuse" in pure_cov["na"]
    assert "library_reuse" not in pure_cov["engaged"]
    lib_inert = {x["id"] for x in lib_cov["inert"]}
    assert "library_reuse" in (set(lib_cov["engaged"]) | lib_inert)


def test_duo_ab_plan_is_well_formed_and_not_executed():
    plan = ab.duo_ab("baldurs-gate", persona="skeptic", beats=8)
    arms = {a["arm"] for a in plan["arms"]}
    assert arms == {"pure_gen", "library_first"}
    # the library-first arm opts into the pack; pure-gen leaves it empty.
    lib_arm = next(a for a in plan["arms"] if a["arm"] == "library_first")
    assert lib_arm["env"]["WORLDOS_LIBRARY_PACKS"]
    pure_arm = next(a for a in plan["arms"] if a["arm"] == "pure_gen")
    assert pure_arm["env"]["WORLDOS_LIBRARY_PACKS"] == ""
    # the EVAL gate wires all three sub-gates (parity + cost + engagement).
    gate = plan["gate"]
    assert gate["lens_parity"]["noise_floor"]  # non-empty per-lens floor
    assert gate["cost_reduction"]["target_pct"] > 0
    assert "engagement" in gate


def test_duo_ab_execute_true_refuses():
    # The scored measurement pass is the orchestrator's — execute=True must NOT silently run a duo.
    with pytest.raises(NotImplementedError):
        ab.duo_ab("baldurs-gate", execute=True)
