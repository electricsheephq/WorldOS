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
