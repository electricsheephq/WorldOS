#!/usr/bin/env python3
"""Red-first units for the room certification artifact (epic #1581, sidecar round-3 adoption).

A certification pins the exact artifact shas the gates certified; ANY drift = STALE = re-gate.
Run: python3 -m pytest qa/test_room_certification.py -q -p no:xdist
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_pipeline as RP  # noqa: E402


def test_repo_certifications_are_fresh():
    """Every committed certification must match the artifacts on disk — CI-enforceable freshness."""
    for cert in sorted(RP.CERT_DIR.glob("*.json")):
        room = cert.stem
        fails = RP.verify_certification(room)
        assert fails == [], f"{room}: " + "; ".join(fails)


def test_missing_certification_is_loud():
    fails = RP.verify_certification("no_such_room_xyz")
    assert fails and "NOT CERTIFIED" in fails[0]


def test_sha_drift_reads_stale(tmp_path, monkeypatch):
    """Red-first: mutate a pinned artifact hash -> the certification must read STALE."""
    cert_src = RP.CERT_DIR / "shop.json"
    if not cert_src.exists():
        return  # no certification in this checkout — the repo test above covers the real ones
    tmp_cert = tmp_path / "certs"
    tmp_cert.mkdir()
    doctored = json.loads(cert_src.read_text())
    doctored["artifacts"]["plate_sha256"] = "0" * 64   # a plate the repo does not contain
    (tmp_cert / "shop.json").write_text(json.dumps(doctored))
    monkeypatch.setattr(RP, "CERT_DIR", tmp_cert)
    fails = RP.verify_certification("shop")
    assert any("drifted" in f for f in fails)


# --- Fix 1: a SKIP (harness/ERROR) walk gate must NEVER certify a room ------------------------------
def test_is_shippable_walk_skip_never_ships():
    """walk=SKIP + coherence=GREEN must NOT ship: a coherent paint says nothing about walkability."""
    results = {"coherence": {"status": "GREEN", "detail": ""}, "walk": {"status": "SKIP", "detail": ""}}
    assert RP._is_shippable(results, ["coherence", "walk"]) is False


def test_is_shippable_requires_green_walk():
    results = {"coherence": {"status": "GREEN"}, "walk": {"status": "GREEN"}}
    assert RP._is_shippable(results, ["coherence", "walk"]) is True


def test_is_shippable_coherence_only_still_ships():
    """When no walk gate ran (walk not in gate_stages), a GREEN coherence still ships — the new gate
    only closes the walk=SKIP hole, it does not change the walk-absent path."""
    assert RP._is_shippable({"coherence": {"status": "GREEN"}}, ["coherence"]) is True


def test_run_full_walk_skip_is_not_certified(tmp_path, monkeypatch):
    """End-to-end: walk=SKIP + coherence=GREEN (MANUAL stages stubbed GREEN) → not shippable, and
    write_certification is NEVER called."""
    monkeypatch.setattr(RP, "stage_coherence", lambda room, out: {"status": "GREEN", "detail": "ok"})
    monkeypatch.setattr(RP, "stage_walk",
                        lambda room, out, engine, qa, stride: {"status": "SKIP", "detail": "harness"})
    monkeypatch.setattr(RP, "stage_manual", lambda name, cmd, needs: {"status": "GREEN", "detail": "stub"})
    cert_calls = []
    monkeypatch.setattr(RP, "write_certification", lambda *a, **k: cert_calls.append(1))
    rep = RP.run("crypt", "full", tmp_path, "e", "q", 3, resume=False)
    assert rep["shippable"] is False
    assert "certification" not in rep and cert_calls == []


def test_resume_reexecutes_cached_skip(tmp_path, monkeypatch):
    """A cached SKIP is NOT terminal on --resume — an ERROR-mapped harness SKIP must be RETRIED."""
    calls = {"n": 0}

    def _walk(room, out, engine, qa, stride):
        calls["n"] += 1
        return {"status": "SKIP", "detail": "harness"}

    monkeypatch.setattr(RP, "stage_walk", _walk)
    RP.run("crypt", "verify", tmp_path, "e", "q", 3, resume=False)
    assert calls["n"] == 1
    RP.run("crypt", "verify", tmp_path, "e", "q", 3, resume=True)   # cached SKIP must re-run
    assert calls["n"] == 2
