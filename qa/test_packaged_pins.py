"""Red-first tests for the packaged plate/boxes/camera pin parity check."""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import packaged_pins as pins  # noqa: E402


def _write_manifest(repo: Path, entries: dict) -> Path:
    unity = repo / "extensions" / "renderers" / "unity"
    unity.mkdir(parents=True, exist_ok=True)
    path = unity / "plates_manifest.json"
    path.write_text(json.dumps({"version": 1, "plates": entries}), encoding="utf-8")
    return path


def _fixture(tmp_path: Path, *, second_room: bool = False) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    app = tmp_path / "WorldOSPlayer.app"
    source_root = repo / "extensions" / "renderers" / "unity"
    packaged_root = app / "Contents" / "Resources" / "Data" / "StreamingAssets"
    (source_root / "plates").mkdir(parents=True, exist_ok=True)
    (source_root / "boxes").mkdir(parents=True, exist_ok=True)
    (packaged_root / "plates").mkdir(parents=True, exist_ok=True)
    (packaged_root / "boxes").mkdir(parents=True, exist_ok=True)

    entries = {
        "tavern": {
            "plate": "plates/tavern.png",
            "boxes": "boxes/tavern.json",
            "cameraPin": {"ortho": 10.5224, "pitch": 30, "yaw": 45},
        }
    }
    if second_room:
        entries["crypt"] = {"plate": "plates/crypt.png"}
    _write_manifest(repo, entries)
    (source_root / "plates" / "tavern.png").write_bytes(b"tavern")
    (source_root / "boxes" / "tavern.json").write_bytes(b"boxes")
    if second_room:
        (source_root / "plates" / "crypt.png").write_bytes(b"crypt")

    packaged_manifest = packaged_root / "plates_manifest.json"
    packaged_manifest.write_text(json.dumps({"version": 1, "plates": entries}), encoding="utf-8")
    (packaged_root / "plates" / "tavern.png").write_bytes(b"tavern")
    (packaged_root / "boxes" / "tavern.json").write_bytes(b"boxes")
    if second_room:
        (packaged_root / "plates" / "crypt.png").write_bytes(b"crypt")
    return app, repo


def test_all_equal_is_green(tmp_path):
    app, repo = _fixture(tmp_path)
    report = pins.check(app, repo)
    assert report["verdict"] == "GREEN"
    assert pins.main([str(app), "--repo", str(repo)]) == 0


def test_plate_path_differs_is_red(tmp_path):
    app, repo = _fixture(tmp_path)
    manifest = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    payload = json.loads(manifest.read_text())
    payload["plates"]["tavern"]["plate"] = "plates/other.png"
    manifest.write_text(json.dumps(payload))
    (app / "Contents/Resources/Data/StreamingAssets/plates/other.png").write_bytes(b"tavern")
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any("plate path" in reason for reason in report["rooms"][0]["reasons"])


def test_same_plate_path_different_bytes_is_red(tmp_path):
    app, repo = _fixture(tmp_path)
    (app / "Contents/Resources/Data/StreamingAssets/plates/tavern.png").write_bytes(b"changed")
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any("plate sha256" in reason for reason in report["rooms"][0]["reasons"])


def test_missing_packaged_boxes_is_red_and_prints_missing(tmp_path, capsys):
    app, repo = _fixture(tmp_path)
    (app / "Contents/Resources/Data/StreamingAssets/boxes/tavern.json").unlink()
    assert pins.main([str(app), "--repo", str(repo)]) == 1
    assert "MISSING" in capsys.readouterr().out


def test_room_only_in_repo_manifest_is_red(tmp_path):
    app, repo = _fixture(tmp_path, second_room=True)
    packaged = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    payload = json.loads(packaged.read_text())
    del payload["plates"]["crypt"]
    packaged.write_text(json.dumps(payload))
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any(room["room"] == "crypt" and room["status"] == "RED" for room in report["rooms"])


def test_camera_pin_ortho_differs_is_red(tmp_path):
    app, repo = _fixture(tmp_path)
    manifest = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    payload = json.loads(manifest.read_text())
    payload["plates"]["tavern"]["cameraPin"]["ortho"] = 11.0
    manifest.write_text(json.dumps(payload))
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any("cameraPin.ortho" in reason for reason in report["rooms"][0]["reasons"])


def test_non_app_path_is_error(tmp_path):
    app, repo = _fixture(tmp_path)
    # a REAL directory with a valid StreamingAssets tree but no .app suffix: only the suffix guard can fire
    bare = app.with_name("WorldOSPlayer")
    shutil.copytree(app, bare)
    assert pins.main([str(bare), "--repo", str(repo)]) == 2


def test_unknown_or_empty_rooms_is_error_not_green(tmp_path):
    app, repo = _fixture(tmp_path)
    assert pins.check(app, repo, "no_such_room")["verdict"] == "ERROR"
    assert pins.check(app, repo, [])["verdict"] == "ERROR"
    assert pins.main([str(app), "--repo", str(repo), "--rooms", "typo"]) == 2


def test_io_failure_inside_check_is_error_and_report_written(tmp_path, monkeypatch):
    app, repo = _fixture(tmp_path)
    def boom(_path):
        raise PermissionError("simulated unreadable archive")
    monkeypatch.setattr(pins, "_sha256", boom)
    out = tmp_path / "report.json"
    assert pins.main([str(app), "--repo", str(repo), "--json", str(out)]) == 2
    payload = json.loads(out.read_text())
    assert payload["verdict"] == "ERROR" and "PermissionError" in payload["error"]
    assert payload["repo"] == str(repo.resolve())


def test_rooms_filter_is_honoured(tmp_path):
    app, repo = _fixture(tmp_path, second_room=True)
    packaged = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    payload = json.loads(packaged.read_text())
    payload["plates"]["crypt"]["plate"] = "plates/missing.png"
    packaged.write_text(json.dumps(payload))
    report = pins.check(app, repo, rooms=["tavern"])
    assert report["verdict"] == "GREEN"
    assert [room["room"] for room in report["rooms"]] == ["tavern"]


def test_json_report_shape(tmp_path):
    app, repo = _fixture(tmp_path)
    out = tmp_path / "pins.json"
    assert pins.main([str(app), "--repo", str(repo), "--json", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["schema_version"] == 1
    assert isinstance(payload["ts"], str)
    assert payload["app"] == str(app.resolve())
    assert "repo_sha" in payload
    assert isinstance(payload["rooms"], list)
    assert payload["verdict"] == "GREEN"


def test_other_manifest_fields_are_compared(tmp_path):
    app, repo = _fixture(tmp_path)
    manifest = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    payload = json.loads(manifest.read_text())
    payload["plates"]["tavern"]["door_hotspots"] = [{"cell": [9, 9]}]
    manifest.write_text(json.dumps(payload))
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any("manifest field differs: door_hotspots" in r for r in report["rooms"][0]["reasons"])


def test_dirty_repo_is_recorded_not_misattributed(tmp_path, monkeypatch):
    app, repo = _fixture(tmp_path)
    monkeypatch.setattr(pins, "_repo_sha", lambda _r: "abc123")
    monkeypatch.setattr(pins, "_repo_dirty", lambda _r: True)
    report = pins.check(app, repo)
    assert report["verdict"] == "GREEN"
    assert report["repo_dirty"] is True and report["repo_sha"] == "abc123-dirty"


def test_symlink_to_device_is_refused_not_hashed(tmp_path):
    app, repo = _fixture(tmp_path)
    manifest = app / "Contents/Resources/Data/StreamingAssets/plates_manifest.json"
    rel = json.loads(manifest.read_text())["plates"]["tavern"]["plate"]
    target = app / "Contents/Resources/Data/StreamingAssets" / rel
    target.unlink()
    target.symlink_to("/dev/zero")
    out = tmp_path / "report.json"
    # a device is not a regular file: the plate is reported MISSING (RED) without ever being read
    assert pins.main([str(app), "--repo", str(repo), "--json", str(out)]) == 1
    payload = json.loads(out.read_text())
    assert payload["verdict"] == "RED"
    assert any("MISSING" in r for r in payload["rooms"][0]["reasons"])


def test_room_without_plate_entry_is_red(tmp_path):
    app, repo = _fixture(tmp_path)
    for root in (app / "Contents/Resources/Data/StreamingAssets", repo / "extensions/renderers/unity"):
        m = root / "plates_manifest.json"
        payload = json.loads(m.read_text())
        payload["plates"]["ghost"] = {}
        m.write_text(json.dumps(payload))
    report = pins.check(app, repo, "ghost")
    assert report["verdict"] == "RED"
    assert any("no plate entry" in r for r in report["rooms"][0]["reasons"])


def test_effects_registry_parity_is_checked(tmp_path):
    app, repo = _fixture(tmp_path)
    (repo / "extensions/renderers/unity/effects_registry.json").write_text('{"fire": 1}')
    (app / "Contents/Resources/Data/StreamingAssets/effects_registry.json").write_text('{"fire": 2}')
    report = pins.check(app, repo)
    assert report["verdict"] == "RED"
    assert any(r["room"] == "__effects_registry.json" and r["status"] == "RED" for r in report["rooms"])


def test_unknown_dirtiness_is_stamped_unverified(tmp_path, monkeypatch):
    app, repo = _fixture(tmp_path)
    pins._DIRTY_CACHE.clear()
    monkeypatch.setattr(pins, "_repo_sha", lambda _r: "abc123")
    monkeypatch.setattr(pins, "_repo_dirty", lambda _r: None)
    report = pins.check(app, repo)
    assert report["repo_sha"] == "abc123-unverified" and report["repo_dirty"] is None
    pins._DIRTY_CACHE.clear()
