"""Focused regressions for the read-only QA monitor projection."""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _viewer():
    spec = importlib.util.spec_from_file_location("worldos_viewer_under_test", ROOT / "viewer" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # main() is __name__-guarded, so no server starts
    return mod


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_qa_monitor_surfaces_release_readiness_sidecars_and_unknowns(tmp_path, monkeypatch):
    v = _viewer()
    repo = tmp_path / "repo"
    monkeypatch.setattr(v, "_HERE", repo / "viewer")
    v._monitor_card_cache.clear()

    snap = repo / "qa" / "state" / "caster-run" / "campaigns" / "camp1" / "snapshot.json"
    _write_json(snap, {"title": "QA World", "world_id": "bg3", "characters": {}, "party": []})
    tdir = repo / "qa" / "transcripts"
    _write_json(tdir / "caster-run.score.json", {"overall": 4.7, "gate_status": "GREEN"})
    _write_json(tdir / "caster-run.tolkien.json", {"overall": 4.4})
    _write_json(tdir / "caster-run.fiction.json", {"status": "FAIL", "blockers": ["canon drift"]})
    _write_json(tdir / "caster-run.release.json", {"status": "FAIL", "release_cell": "postbg3-caster"})

    monkeypatch.setattr(v, "_monitor_roots", lambda: [("qa:caster-run", snap.parents[1])])
    first = v._monitor_campaigns()[0]
    assert first["scores"]["mechanical"] == 4.7
    assert first["scores"]["story"] == 4.4
    assert first["scores"]["behavioral"] == "GREEN"
    assert first["scores"]["fiction"] == "FAIL"
    assert first["scores"]["release"] == "FAIL"
    assert first["release_cell"] == "postbg3-caster"
    assert first["blockers"] == ["canon drift"]
    assert first["run_id"] == "caster-run"
    assert first["readiness_updated_at"] >= first["updated_at"]

    _write_json(tdir / "caster-run.release.json", {"status": "PASS", "release_cell": "postbg3-caster"})
    os.utime(tdir / "caster-run.release.json", None)
    second = v._monitor_campaigns()[0]
    assert second["scores"]["release"] == "PASS"

    missing = v._monitor_card("qa:empty-run", snap, {"characters": {}, "party": []})
    assert missing["scores"]["fiction"] == "UNKNOWN"
    assert missing["scores"]["release"] == "UNKNOWN"
    assert missing["scores"]["behavioral"] == "UNKNOWN"
