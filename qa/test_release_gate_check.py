#!/usr/bin/env python3
"""Unit tests for qa/release_gate_check.py — the auto-tag-on-milestone-close gate.

The four load-bearing cases the workflow relies on (and a few more for the edges):
  * marker present + STATUS: RELEASE + clean unused tag + version match ⇒ GO
  * STATUS: DEVELOPMENT (any gate not PASSED)                            ⇒ NO-GO
  * marker absent                                                        ⇒ NO-GO
  * tag already exists                                                   ⇒ NO-GO

Every test builds an ISOLATED fake repo root (tmp_path) with its own VERSION /
servers/engine/__version__.py / git tags, and a TEMP scores.db — NEVER the committed
qa/scores.db (the additive, read-only invariant).

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_release_gate_check.py -q -p no:xdist
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import release_gate_check as rgc  # noqa: E402

ALL_GATES = scores_db.RRI_CANONICAL_GATES


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _make_repo(tmp_path: Path, version: str = "1.0.5", *, with_tags: list[str] | None = None) -> Path:
    """Build a minimal git repo root with VERSION + engine __version__ + optional tags."""
    root = tmp_path / "repo"
    (root / "servers" / "engine").mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "servers" / "engine" / "__version__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)
    for t in (with_tags or []):
        subprocess.run(["git", "-C", str(root), "tag", t], check=True)
    return root


def _seed_release_db(db: Path) -> None:
    """An RRI-bearing ledger row (for ruler provenance in the verdict path)."""
    scores_db.add_run(
        "gate-test", db_path=db,
        surface="GUI-built-app", ts="2026-06-21T00:00:00+00:00", build_sha="abc1234",
        dm_model="opus", scorer_model="claude", rc_label="v1.0.5",
        story_overall=4.4, mech_overall=4.6, behavioral="GREEN", rri=10.0,
        cross_persona_sat=7.5, critical_bugs=0,
        scoring_config_version="sc_test", lens_config_version="lc_test",
    )


def _rri_json(path: Path, *, failed=None, skipped=None, build_sha="abc1234") -> Path:
    """A minimal release_readiness.py-shaped RRI.json (all-PASS unless failed/skipped given)."""
    payload = {
        "rri": 10.0,
        "status": "READY" if not (failed or skipped) else "NOT_READY",
        "release_ready": not (failed or skipped),
        "build_sha": build_sha,
        "gates_total": 11,
        "failed_gates": failed or [],
        "skipped_gates": skipped or [],
        "gate_detail": {g: f"{g} detail" for g in ALL_GATES},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _args(**kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        milestone_title=None, milestone_description="",
        verdict_json=None, rri_json=None, build_sha=None,
        db=None, repo_root=None, allow_prerelease_dev=False, out=None,
    )
    for k, v in kw.items():
        setattr(ns, k, str(v) if isinstance(v, Path) else v)
    return ns


# --------------------------------------------------------------------------- #
# the four load-bearing cases
# --------------------------------------------------------------------------- #
def test_marker_and_release_and_clean_tag_is_go(tmp_path):
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="Ship it. [release-ready]",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "go", v["reasons"]
    assert v["tag"] == "v1.0.5"
    assert v["version"] == "1.0.5"
    assert v["prerelease"] is False
    assert v["rri_status"] == "RELEASE"


def test_development_status_is_no_go(tmp_path):
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json", skipped=["palette_live"])
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "no_go"
    assert v["rri_status"] == "DEVELOPMENT"
    assert "palette_live" in v["gates_not_passed"]
    assert any("DEVELOPMENT" in r for r in v["reasons"])


def test_missing_marker_is_no_go(tmp_path):
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")  # all gates PASS
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="just a normal milestone close",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "no_go"
    assert v["marker_present"] is False
    assert any("marker" in r and "absent" in r for r in v["reasons"])


def test_tag_already_exists_is_no_go(tmp_path):
    root = _make_repo(tmp_path, "1.0.5", with_tags=["v1.0.5"])
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "no_go"
    assert any("already exists" in r for r in v["reasons"])


# --------------------------------------------------------------------------- #
# the other refusals + the pre-release nuance
# --------------------------------------------------------------------------- #
def test_unclean_title_is_no_go(tmp_path):
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    for bad in ("Sprint 12", "v1.0", "v1.0.5 (final)", "release 1.0.5", "1.0.5"):
        v = rgc.evaluate_gate(_args(
            milestone_title=bad, milestone_description="[release-ready]",
            rri_json=str(rri), db=str(db), repo_root=str(root)))
        assert v["decision"] == "no_go", f"{bad!r} should be refused"
        assert v["tag"] is None
        assert any("not a clean" in r for r in v["reasons"])


def test_version_mismatch_is_no_go(tmp_path):
    # repo is at 1.0.5 but the milestone claims v1.0.6
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.6", milestone_description="[release-ready]",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "no_go"
    assert any("version mismatch" in r for r in v["reasons"])


def test_prerelease_can_ship_on_development_with_optin(tmp_path):
    """A -rcN tag MAY proceed on DEVELOPMENT status only with --allow-prerelease-dev."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json", skipped=["native_gate"])  # DEVELOPMENT
    base = dict(milestone_title="v1.0.5-rc5", milestone_description="[release-ready]",
                rri_json=str(rri), db=str(db), repo_root=str(root))
    # without the opt-in: blocked
    assert rgc.evaluate_gate(_args(**base))["decision"] == "no_go"
    # with the opt-in: allowed (still a pre-release)
    v = rgc.evaluate_gate(_args(allow_prerelease_dev=True, **base))
    assert v["decision"] == "go", v["reasons"]
    assert v["prerelease"] is True


def test_ga_never_ships_on_development_even_with_optin(tmp_path):
    """A clean GA (no -rc) ALWAYS requires STATUS: RELEASE, regardless of the pre-release opt-in."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json", failed=["mechanical"])  # DEVELOPMENT
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        allow_prerelease_dev=True, rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert v["decision"] == "no_go"
    assert v["prerelease"] is False


def test_no_artifact_is_never_release(tmp_path):
    """No RRI/verdict artifact ⇒ inferred status can never certify RELEASE (honesty guard)."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        db=str(db), repo_root=str(root)))  # no rri_json / verdict_json
    assert v["decision"] == "no_go"
    assert v["rri_status"] == "DEVELOPMENT"


def test_verdict_json_path_drives_status(tmp_path):
    """A pre-emitted release_readiness_verdict.json is an accepted per-gate source."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")  # all PASS
    verdict_path = tmp_path / "verdict.json"
    scores_db.release_readiness_verdict(rri, db_path=db, out_path=verdict_path)
    v = rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        verdict_json=str(verdict_path), db=str(db), repo_root=str(root)))
    assert v["decision"] == "go", v["reasons"]
    assert v["rri_status"] == "RELEASE"


def test_evaluate_gate_does_not_mutate_db(tmp_path):
    """The gate check is READ-ONLY on scores.db (never writes the committed ledger)."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    before = db.read_bytes()
    rgc.evaluate_gate(_args(
        milestone_title="v1.0.5", milestone_description="[release-ready]",
        rri_json=str(rri), db=str(db), repo_root=str(root)))
    assert db.read_bytes() == before


# --------------------------------------------------------------------------- #
# the small pure helpers
# --------------------------------------------------------------------------- #
def test_parse_version_tag():
    assert rgc.parse_version_tag("v1.0.5") == {"tag": "v1.0.5", "base": "1.0.5", "prerelease": False}
    assert rgc.parse_version_tag("v1.0.5-rc4") == {"tag": "v1.0.5-rc4", "base": "1.0.5", "prerelease": True}
    assert rgc.parse_version_tag("  v2.3.1-beta.2 ") == {"tag": "v2.3.1-beta.2", "base": "2.3.1", "prerelease": True}
    for bad in (None, "", "v1.0", "1.0.5", "v1.0.5 final", "Sprint", "vX.Y.Z"):
        assert rgc.parse_version_tag(bad) is None, bad


def test_has_release_marker():
    assert rgc.has_release_marker("blah [release-ready] blah")
    assert rgc.has_release_marker("title", "desc with [RELEASE-READY]")  # case-insensitive
    assert not rgc.has_release_marker("release ready", "almost [release ready]")  # no brackets
    assert not rgc.has_release_marker(None, "")


def test_main_exit_codes(tmp_path, capsys):
    """main() returns 0 on GO, 1 on NO-GO, and writes the JSON verdict."""
    root = _make_repo(tmp_path, "1.0.5")
    db = tmp_path / "t.db"; _seed_release_db(db)
    rri = _rri_json(tmp_path / "RRI.json")
    out = tmp_path / "gate.json"
    rc = rgc.main([
        "--milestone-title", "v1.0.5", "--milestone-description", "[release-ready]",
        "--rri-json", str(rri), "--db", str(db), "--repo-root", str(root), "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["decision"] == "go"
    # a no-go path returns 1
    rc2 = rgc.main([
        "--milestone-title", "v1.0.5", "--milestone-description", "no marker",
        "--rri-json", str(rri), "--db", str(db), "--repo-root", str(root)])
    assert rc2 == 1


# --------------------------------------------------------------------------- #
# the workflow YAML — structural lint (valid YAML + the load-bearing gate shape)
# --------------------------------------------------------------------------- #
def test_release_workflow_yaml_is_valid_and_safe():
    """The auto-tag workflow must keep its safety contract: milestone:closed + workflow_dispatch
    trigger, dry_run default TRUE, contents:write only, and the real tag/release step guarded by
    dry_run == 'false'. pyyaml is not an engine dep, so the structural parse is best-effort
    (when available) and the load-bearing safety checks are pyyaml-free string assertions so this
    guard ALWAYS runs in CI."""
    wf = QA_DIR.parent / ".github" / "workflows" / "release-on-milestone-close.yml"
    assert wf.exists(), f"workflow missing at {wf}"
    text = wf.read_text(encoding="utf-8")

    # --- pyyaml-free safety contract (always runs) ---
    assert "types: [closed]" in text, "must trigger on milestone:closed"
    assert "workflow_dispatch:" in text, "must also have a workflow_dispatch (manual test path)"
    assert "permissions:\n  contents: write\n" in text, "permissions must be exactly contents:write"
    # dry_run input defaults to true.
    assert "dry_run:" in text and "default: true" in text, "dry_run must default to true (safety)"
    # The REAL tag/release step is guarded by dry_run == 'false' (never runs by default).
    assert "steps.resolve.outputs.dry_run == 'false'" in text, \
        "the REAL tag/release step must be guarded by dry_run == 'false'"
    assert "gh release create" in text, "workflow must create a gh release in real mode"

    # --- deeper structural parse when pyyaml is present (skipped if not installed) ---
    try:
        import yaml  # type: ignore
    except ImportError:
        return
    doc = yaml.safe_load(text)
    on = doc.get("on", doc.get(True))  # YAML 1.1 parses bare `on:` as boolean True
    assert isinstance(on, dict), "no `on:` trigger block"
    assert on.get("milestone", {}).get("types") == ["closed"]
    inputs = on["workflow_dispatch"].get("inputs", {})
    assert inputs["dry_run"].get("default") is True
    assert doc.get("permissions") == {"contents": "write"}
