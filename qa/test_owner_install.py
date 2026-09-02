import plistlib, subprocess, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import owner_install_plists as OIP


def test_plists_render_exact_owner_contract(tmp_path):
    session, player = OIP.render_plists(Path("/Users/m1/worldos-owner"), Path("/Users/m1/Applications/WorldOSPlayer.app"), tmp_path / "state", Path("/opt/homebrew/bin/uv"))
    assert session["ProgramArguments"][-2:] == ["adventure_demo_v1", "8776"]
    assert session["EnvironmentVariables"]["WORLDOS_PLAYER_MOVES"].endswith("/player_moves.json")
    assert player["EnvironmentVariables"]["WORLDOS_QA_INPUT_PORT"] == "8981"
    assert player["ProgramArguments"] == ["/Users/m1/Applications/WorldOSPlayer.app/Contents/MacOS/WorldOSPlayer"]
    assert session["KeepAlive"] is player["KeepAlive"] is False


@pytest.mark.parametrize("engine,qa", [(8766, 8981), (8776, 8971)])
def test_reserved_owner_ports_are_refused(engine, qa):
    with pytest.raises(ValueError): OIP.validate_ports(engine, qa)


def test_preflight_refuses_red_before_writing(tmp_path):
    app = tmp_path / "Bad.app"; (app / "Contents/MacOS").mkdir(parents=True); (app / "Contents/Resources/Data").mkdir(parents=True)
    binary = app / "Contents/MacOS/WorldOSPlayer"; binary.write_text("bad"); binary.chmod(0o755)
    (app / "Contents/Resources/Data/level0").write_text("clean"); (tmp_path / "build-report.txt").write_text("result=Succeeded\n")
    result = subprocess.run([str(Path(__file__).parent / "owner_install.sh"), "preflight", str(app)], text=True, capture_output=True)
    assert result.returncode == 1 and "packaged pins RED/ERROR" in result.stderr
    assert list(tmp_path.glob("*.plist")) == []
