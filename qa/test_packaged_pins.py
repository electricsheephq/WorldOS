"""Red-first tests for the packaged plate/boxes/camera pin parity check."""

import json
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
    assert pins.main([str(app.with_name("WorldOSPlayer")), "--repo", str(repo)]) == 2


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
