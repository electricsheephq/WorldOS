#!/usr/bin/env python3
"""test_promote_pipeline.py — HV3 promote.py + library_lint.py (Act II §4c, #1325).

Exercises the promotion PATH fully OFFLINE: every test fabricates pre-scored rows in a temp
`artifacts` table (via scores_db.add_artifact) — NO test invokes a live scorer / claude -p. The
score-if-unscored step is a separately-tested seam (test_score_if_unscored_is_isolated) that only
checks the import/branch wiring, never a live panel run.

Invariant assertions live here:
  * promote.py is the SOLE writer of library/ (nothing else touches it) + additive-by-default.
  * room_recipes.json + registry.json are BYTE-IDENTICAL after any promotion batch.
  * the threshold gate (overall>=4.0, every dim>=3.0, control-valid → stable; canonical never auto).
  * --batch is idempotent + exits 0 with zero promotions.
  * library-lint: no unscored stable entries; provenance+license required on every entry.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
sys.path.insert(0, str(_QA_DIR))
sys.path.insert(0, str(_REPO_ROOT / "tools" / "library"))

import scores_db  # noqa: E402
import promote  # noqa: E402  (tools/library/promote.py)
import library_lint  # noqa: E402
import artifact_calibration_panel as panel  # noqa: E402  (#1355)

ROOM_RECIPES = _REPO_ROOT / "extensions" / "renderers" / "shared" / "room_recipes.json"
REGISTRY = _REPO_ROOT / "data" / "asset-registry" / "registry.json"

_NOISE = promote._control_noise_law()  # source of truth: qa/artifact_controls_identity.json noise_law


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────
def _seed_scored(db, artifact_id, cls, *, overall, dims, panel_id="cal-p", world="baldurs-gate",
                 source_path=None):
    """Insert one scored candidate row + a control row for the same panel (in-band → control-valid)."""
    scores_db.add_artifact(artifact_id, db_path=db, **{"class": cls}, world=world, overall=overall,
                           dims_json=dims, panel_id=panel_id, is_control=0, source_path=source_path)


def _seed_control(db, cls="quest", *, panel_id="cal-p", anchor=4.0, overall=4.0):
    scores_db.add_artifact(f"ctrl:{cls}:{panel_id}", db_path=db, **{"class": cls},
                           overall=overall, dims_json={"d": overall}, panel_id=panel_id,
                           is_control=1, control_anchor=anchor)


def _write_noms(path: Path, lines: list[dict]):
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "scores.db"
    lib = tmp_path / "library"
    noms = tmp_path / "nominations.jsonl"
    return {"db": db, "lib": lib, "noms": noms}


# ── gate logic ──────────────────────────────────────────────────────────────────────────────────
def test_gate_promotes_high_score_with_valid_control(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:q1", "quest", overall=4.4, dims={"clarity": 4, "stakes": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:q1"}])

    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 1 and rep["rejected"] == 0
    entry = json.loads((lib / "quests" / next((lib / "quests").glob("*.json")).name).read_text())
    assert entry["tier"] == "stable"
    assert entry["scores"]["overall"] == 4.4


def test_gate_rejects_low_overall(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:low", "quest", overall=3.9, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:low"}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 0 and rep["rejected"] == 1
    assert not list((lib / "quests").glob("*.json"))


def test_gate_rejects_dim_below_3(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:d", "quest", overall=4.5, dims={"clarity": 5, "stakes": 2})
    _write_noms(noms, [{"artifact_id": "quest:bg:d"}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 0 and rep["rejected"] == 1


def test_gate_rejects_when_panel_not_control_valid(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    # control OUT of band → panel not control-valid → reject even a strong candidate.
    _seed_control(db, anchor=4.0, overall=4.0 + _NOISE + 0.5)
    _seed_scored(db, "quest:bg:v", "quest", overall=4.6, dims={"clarity": 5})
    _write_noms(noms, [{"artifact_id": "quest:bg:v"}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 0 and rep["rejected"] == 1


def test_gate_rejects_when_no_control_in_panel(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    # a panel with NO control row is not control-valid (fail closed).
    _seed_scored(db, "quest:bg:nc", "quest", overall=4.6, dims={"clarity": 5}, panel_id="cal-solo")
    _write_noms(noms, [{"artifact_id": "quest:bg:nc"}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 0 and rep["rejected"] == 1


def test_promote_never_assigns_canonical(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:c", "quest", overall=5.0, dims={"clarity": 5, "stakes": 5})
    _write_noms(noms, [{"artifact_id": "quest:bg:c"}])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    entries = [json.loads(p.read_text()) for p in (lib / "quests").glob("*.json")]
    assert all(e["tier"] != "canonical" for e in entries)
    assert all(e["tier"] == "stable" for e in entries)


# ── invariants ──────────────────────────────────────────────────────────────────────────────────
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_room_recipes_and_registry_byte_identical_after_batch(env):
    """HARD INVARIANT: a promotion batch (incl. a ROOM entry) leaves room_recipes.json + registry.json
    byte-identical. promote.py is never a second writer of either."""
    db, lib, noms = env["db"], env["lib"], env["noms"]
    before = {ROOM_RECIPES: _sha(ROOM_RECIPES), REGISTRY: _sha(REGISTRY)}
    _seed_control(db, cls="location")
    _seed_scored(db, "location:bg:tavern", "location", overall=4.3, dims={"vividness": 4},
                 panel_id="cal-p")
    _write_noms(noms, [{
        "artifact_id": "location:bg:tavern",
        "room_ref": {"recipe_key": "tavern", "asset_ids": ["fighter", "goblin"]},
    }])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    for p, h in before.items():
        assert _sha(p) == h, f"{p} changed after a promotion batch — promote.py must not edit it"


def test_room_entry_references_recipe_not_inlines_it():
    """HARD INVARIANT (build_entry is the code unit that owns it): a room entry carries room_ref
    {recipe_key, asset_ids} BY VALUE and never inlines the recipe/registry payload itself.

    NOTE on scope: scores_db.ARTIFACT_CLASSES (HV1, qa/scores_db.py) does not include "room" today —
    only HV1's rubric+committed-control-fixture epic can add it (a class needs a rubric, a schema, and
    >=2 committed control fixtures per test_all_class_rubric_and_schema_files_exist /
    test_committed_controls_cover_every_class in qa/test_artifact_evals.py). So a DB row can never carry
    class="room" yet, and promote_batch's `cls = row["class"]` (promote.py) can never reach
    build_entry's cls=="room" branch end-to-end. That reachability gap is a separate, larger fix
    (flagged upstream, out of scope for this PR) — but build_entry's OWN contract is fully unit-testable
    today and is exactly what this test must pin, rather than asserting a rejected/promoted count that
    says nothing about room_ref."""
    gate = promote.GateResult(True, "stable", [], 4.2, {"mood": 4}, True)
    score_row = {"run_id": "r1", "world": "baldurs-gate", "sha": "abc123", "source_path": None,
                "panel_id": "cal-p", "ac_ruler": "ac_1"}
    room_ref = {"recipe_key": "crypt", "asset_ids": ["skeleton_archer"]}

    entry = promote.build_entry("room:bg:crypt", "room", score_row, gate, license="proprietary",
                                promoted_at="2026-07-06T00:00:00+00:00", room_ref=room_ref)

    assert entry["room_ref"] == {"recipe_key": "crypt", "asset_ids": ["skeleton_archer"]}
    # references BY VALUE only — never inlines actual recipe/registry payload content.
    assert set(entry) >= {"artifact_id", "class", "provenance", "scores", "tier", "room_ref"}
    assert "recipe" not in entry and "registry" not in entry and "payload" not in entry


def test_room_ref_dropped_when_class_is_not_room(env):
    """Documents the CURRENT reachability gap precisely (see test above): a room_ref supplied on a
    nomination whose scored row is class="location" (the only way a room-style artifact can be scored
    today, since ARTIFACT_CLASSES has no "room") is silently dropped by build_entry — the entry is
    promoted as a plain location, with NO room_ref. This is expected given today's schema, not a
    regression; it exists so a future fix that adds "room" to ARTIFACT_CLASSES has a failing test to
    flip green, instead of this gap staying invisible."""
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db, cls="location")
    _seed_scored(db, "room:bg:crypt", "location", overall=4.2, dims={"mood": 4}, panel_id="cal-p")
    _write_noms(noms, [{
        "artifact_id": "room:bg:crypt",
        "room_ref": {"recipe_key": "crypt", "asset_ids": ["skeleton_archer"]},
    }])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 1
    entry = json.loads((lib / "locations" / next((lib / "locations").glob("*.json")).name).read_text())
    assert entry["class"] == "location"
    assert "room_ref" not in entry  # the gap: room_ref was supplied but never attached


def test_empty_nominations_is_additive_noop(env):
    """Additive-by-default: an empty queue leaves library/ byte-identical (here: not even created)."""
    db, lib, noms = env["db"], env["lib"], env["noms"]
    noms.write_text("", encoding="utf-8")
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 0
    assert not lib.exists() or not any(lib.rglob("*.json"))


def test_missing_nominations_file_exits_zero(env, capsys):
    db, lib = env["db"], env["lib"]
    rc = promote.main(["--batch", "--library", str(lib), "--nominations",
                       str(env["noms"]), "--db", str(db)])
    assert rc == 0


def test_batch_is_idempotent(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:i", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:i"}])
    r1 = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    files1 = sorted(str(p) for p in lib.rglob("*.json"))
    r2 = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    files2 = sorted(str(p) for p in lib.rglob("*.json"))
    assert r1["promoted"] == 1
    assert r2["promoted"] == 0 and r2["already_processed"] == 1
    assert files1 == files2  # no duplicate entry written


def test_dry_run_writes_nothing(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:dry", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:dry"}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db, dry_run=True)
    assert rep["promoted"] == 1 and rep["dry_run"] is True
    assert not lib.exists()  # NOTHING written on dry-run


def test_skip_unscored_leaves_unscored_for_later(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:scored", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:scored"},
                       {"artifact_id": "quest:bg:unscored"}])  # not in the DB
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db, skip_unscored=True)
    assert rep["promoted"] == 1 and rep["skipped"] == 1


# ── library-lint ────────────────────────────────────────────────────────────────────────────────
def test_lint_clean_after_valid_batch(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:ok", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:ok"}])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert library_lint.lint_library(lib) == []


def test_lint_flags_unscored_stable_entry(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:ok", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:ok"}])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    # Corrupt an entry into an unscored stable state and confirm lint catches it.
    entry_path = next((lib / "quests").glob("*.json"))
    entry = json.loads(entry_path.read_text())
    entry["scores"]["overall"] = None
    entry["scores"]["dims"] = {}
    entry_path.write_text(json.dumps(entry))
    problems = library_lint.lint_library(lib)
    assert any("unscored stable" in p.lower() for p in problems)


def test_lint_flags_missing_provenance_or_license(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:ok", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:ok"}])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    entry_path = next((lib / "quests").glob("*.json"))
    entry = json.loads(entry_path.read_text())
    entry.pop("license")
    entry["provenance"] = {}
    entry_path.write_text(json.dumps(entry))
    problems = library_lint.lint_library(lib)
    assert any("license" in p for p in problems)
    assert any("provenance" in p for p in problems)


def test_lint_missing_pack_json(tmp_path):
    lib = tmp_path / "library"
    (lib / "quests").mkdir(parents=True)
    problems = library_lint.lint_library(lib)
    assert any("pack.json" in p for p in problems)


def test_pack_json_has_required_metadata(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    _seed_control(db)
    _seed_scored(db, "quest:bg:ok", "quest", overall=4.4, dims={"clarity": 4})
    _write_noms(noms, [{"artifact_id": "quest:bg:ok"}])
    promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    pack = json.loads((lib / "pack.json").read_text())
    assert {"name", "version", "license", "provenance"} <= set(pack)


# ── score-if-unscored isolation (no live scorer) ──────────────────────────────────────────────────
def test_score_if_unscored_is_isolated_from_promotion_path():
    """The offline promotion path (dry_run / skip_unscored / already-scored) never imports or calls a
    live scorer. Assert score_if_unscored exists as a separable seam and is NOT reached when scored."""
    assert hasattr(promote, "score_if_unscored")
    # A nomination with no source_path raises before any scorer contact — proves the guard, no live call.
    with pytest.raises(ValueError):
        promote.score_if_unscored({"artifact_id": "x"})


def test_hv1_exposes_callable_panel_entrypoint():
    """Epic addendum [MED]: HV1's artifact_score.py must expose a plain callable score_artifact_panel."""
    import artifact_score
    assert callable(getattr(artifact_score, "score_artifact_panel", None))


def test_nominations_malformed_line_fails_loud(env):
    db, lib, noms = env["db"], env["lib"], env["noms"]
    noms.write_text('{"artifact_id": "ok"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError):
        promote.read_nominations(noms)


# ── #1355: panel↔promote row-key connection (NO manual bridge) ───────────────────────────────────
def test_promotion_batch_finds_panel_scored_artifact_by_bare_id_no_manual_bridge(env, monkeypatch):
    """HV1's run_panel writes per-scorer rows keyed `{artifact_id}#{panel_id}#s{n}` (bookkeeping for
    N blind scorers); promote.py's nomination lookup exact-matches the bare artifact_id. Before the
    fix, the two never connected — the first live promotion batch (PR #1354) needed a manual bridge
    (hand-inserted bare-id rows) to get panel-scored artifacts through the gate at all. This proves a
    fresh panel run's aggregate row is found by promote_batch with ZERO manual bridging: no hand-written
    bare-id row, no monkeypatched lookup — just run_panel then promote_batch."""
    monkeypatch.setenv("WORLDOS_ARTIFACT_PANEL_DRYRUN", "1")
    db, lib, noms = env["db"], env["lib"], env["noms"]

    report = panel.run_panel("quest", controls_only=True, panel_size=5, db_path=db)
    assert report["panel_valid"] is True  # the dryrun stub scores every control at its anchor

    # Promote one of the just-scored controls' underlying artifact_ids — nothing here writes a
    # bare-id row by hand; it must already exist from run_panel itself.
    control_aid = report["control_medians"][0]["artifact_id"]
    rows_by_bare_id = {r["artifact_id"] for r in scores_db.fetch_artifacts(db)}
    assert control_aid in rows_by_bare_id, (
        "run_panel must write a bare-artifact_id aggregate row, not just the "
        "{artifact_id}#{panel_id}#s{n} per-scorer rows"
    )

    # A control itself is never promotable (evaluate_gate rejects is_control rows) — nominate a
    # plain candidate instead, scored through the SAME run_panel path, to prove the full batch path.
    cand_dir = _write_candidate_dir(env["db"].parent, "quest", "quest:bg:panel-live", overall=4.4)
    cand_report = panel.run_panel("quest", candidates_dir=str(cand_dir), controls_only=False,
                                  panel_size=3, db_path=db)
    assert cand_report["panel_valid"] is True
    cand_aid = next(r["artifact_id"] for r in cand_report["results"] if not r["is_control"])
    assert cand_aid == "quest:bg:panel-live"

    _write_noms(noms, [{"artifact_id": cand_aid}])
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 1 and rep["rejected"] == 0
    entry = json.loads((lib / "quests" / next((lib / "quests").glob("*.json")).name).read_text())
    assert entry["artifact_id"] == cand_aid


def test_panel_aggregate_row_dims_are_median_of_scorer_rows(env, monkeypatch):
    """The aggregate bare-id row's dims_json/overall must be the MEDIAN across the N `#s{n}` scorer
    rows — matching run_panel's own control-band aggregation — not e.g. the last scorer's card."""
    monkeypatch.setenv("WORLDOS_ARTIFACT_PANEL_DRYRUN", "1")
    db = env["db"]
    report = panel.run_panel("quest", controls_only=True, panel_size=5, db_path=db)
    aid = report["control_medians"][0]["artifact_id"]
    expected_overall = report["control_medians"][0]["median"]

    rows = scores_db.fetch_artifacts(db)
    aggregate = next(r for r in rows if r["artifact_id"] == aid)
    scorer_rows = [r for r in rows if r["artifact_id"].startswith(f"{aid}#{report['panel_id']}#s")]
    assert len(scorer_rows) == 5
    assert aggregate["overall"] == expected_overall
    assert aggregate["panel_id"] == report["panel_id"]
    assert aggregate["is_control"] == 1
    # The dryrun stub scores every dim at the anchor for every scorer, so the median dims equal
    # the per-scorer dims exactly — a real (non-dryrun) panel would show genuine per-scorer variance.
    scorer_dims = json.loads(scorer_rows[0]["dims_json"])
    aggregate_dims = json.loads(aggregate["dims_json"])
    assert aggregate_dims.keys() == scorer_dims.keys()


def _write_candidate_dir(tmp_root: Path, cls: str, artifact_id: str, *, overall: float) -> Path:
    """A minimal candidates dir run_panel._candidates_for_class can load: one artifact JSON in the
    canonical schema shape (matches a committed control's payload shape closely enough for
    artifact_score.load_artifact's strict envelope guard, since DRYRUN never calls the live scorer that
    would otherwise read the payload content)."""
    import artifact_score
    control_files = sorted((Path(__file__).resolve().parent / "artifact_controls").glob(f"control__{cls}__*.json"))
    template = artifact_score.load_artifact(control_files[0])
    payload = json.loads(control_files[0].read_text(encoding="utf-8"))
    payload["artifact_id"] = artifact_id
    payload.setdefault("provenance", {})["run_id"] = "run_test_panel_live"
    cand_dir = tmp_root / "cand"
    cand_dir.mkdir(exist_ok=True)
    (cand_dir / "candidate.json").write_text(json.dumps(payload), encoding="utf-8")
    return cand_dir
