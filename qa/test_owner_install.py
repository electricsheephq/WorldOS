import json, plistlib, subprocess, sys
from pathlib import Path
import pytest

QA = Path(__file__).parent
sys.path.insert(0, str(QA))
import owner_install_plists as OIP
import owner_install_verify as OIV

SH = str(QA / "owner_install.sh")
MANIFEST = json.loads((QA.parent / "extensions/renderers/unity/plates_manifest.json").read_text())


def test_plists_render_exact_owner_contract(tmp_path):
    session, player, dm = OIP.render_plists(Path("/Users/m1/worldos-owner"), Path("/Users/m1/Applications/WorldOSPlayer.app"), tmp_path / "state", Path("/opt/homebrew/bin/uv"))
    assert session["ProgramArguments"][-2:] == ["adventure_demo_v1", "8776"]
    assert session["EnvironmentVariables"]["WORLDOS_PLAYER_MOVES"].endswith("/player_moves.json")
    assert player["EnvironmentVariables"]["WORLDOS_QA_INPUT_PORT"] == "8981"
    assert player["ProgramArguments"] == ["/Users/m1/Applications/WorldOSPlayer.app/Contents/MacOS/WorldOSPlayer"]
    assert session["KeepAlive"] is player["KeepAlive"] is dm["KeepAlive"] is False
    # The canonical checkout owns the gitignored _private art; without this the owner
    # worktree becomes the art root and every /image + the app-status probe report it missing.
    assert session["EnvironmentVariables"]["WORLDOS_ART_REPO_ROOT"] == "/Users/m1/WorldOS"
    # Only the session runs at load: a player launched alongside it self-exits (#1612).
    assert session["RunAtLoad"] is True
    assert player["RunAtLoad"] is False and dm["RunAtLoad"] is False


def test_a_dm_consumer_agent_is_rendered(tmp_path):
    _s, _p, dm = OIP.render_plists(Path("/Users/m1/worldos-owner"), tmp_path / "a.app", tmp_path / "state", Path("/opt/homebrew/bin/uv"))
    assert dm["Label"] == "org.worldos.owner-dm"
    assert dm["ProgramArguments"][1].endswith("/qa/agent_play.sh")
    assert dm["ProgramArguments"][2] == "serve"
    assert "--engine" in dm["ProgramArguments"] and "http://127.0.0.1:8776" in dm["ProgramArguments"]
    assert dm["ProgramArguments"][-2:] == ["--campaign", "adventure_demo_v1"]
    # It answers the same move sink the viewer appends say/do/check to.
    assert dm["EnvironmentVariables"]["WORLDOS_PLAYER_MOVES"] == str(tmp_path / "state/player_moves.json")
    # The run dir holds the durable chat cursor: keep it beside the backed-up state, never
    # inside the pinned checkout that `refresh --sha` moves out from under it.
    assert dm["EnvironmentVariables"]["WORLDOS_AGENT_PLAY_ROOT"] == str(tmp_path / "state/agent_play_runs")
    assert dm["EnvironmentVariables"]["WORLDOS_DM_MODEL"] == "opus"


def test_the_viewer_writes_the_exact_chat_file_the_dm_tails(tmp_path):
    # agent_play.sh serve derives chat_path as "<state_dir>/chat.jsonl". A viewer writing
    # chat.json would leave the DM tailing a file nobody writes: every owner line unanswered.
    session, _p, dm = OIP.render_plists(Path("/Users/m1/worldos-owner"), tmp_path / "a.app", tmp_path / "state", Path("/opt/homebrew/bin/uv"))
    state_dir = Path(dm["ProgramArguments"][dm["ProgramArguments"].index("--state") + 1])
    assert session["EnvironmentVariables"]["WORLDOS_VIEWER_CHAT"] == str(state_dir / "chat.jsonl")


@pytest.mark.parametrize("engine,qa", [(8766, 8981), (8776, 8971)])
def test_reserved_owner_ports_are_refused(engine, qa):
    with pytest.raises(ValueError): OIP.validate_ports(engine, qa)


@pytest.mark.parametrize("report", ["result=Failed\ntotalErrors=3\n", "totalWarnings=0\n", ""])
def test_failed_or_unstamped_build_reports_are_refused(report):
    with pytest.raises(ValueError): OIV.check_build_report(report)


def test_successful_build_report_is_accepted():
    fields = OIV.check_build_report("result=Succeeded\ntotalErrors=0\nbuildEndedAt=9/2/2026 10:38:41 AM\n")
    assert fields["result"] == "Succeeded" and fields["buildEndedAt"].startswith("9/2/2026")


def _debug(**over):
    base = {"ok": True, "surf": 4, "plateLocMatch": True, "camOrtho": 13.0}
    return {**base, **over}


def test_consumed_proof_accepts_the_seeded_campaign_at_its_pinned_ortho():
    surface = {"campaign_id": "adventure_demo_v1", "location": {"id": "camp_clearing"}}
    assert "camp_clearing" in OIV.check_consumed(surface, _debug(), MANIFEST)


@pytest.mark.parametrize("surface,debug", [
    ({"campaign_id": "other", "location": {"id": "camp_clearing"}}, _debug()),      # stale campaign on :8776
    ({"campaign_id": "adventure_demo_v1", "location": {"id": "camp_clearing"}}, _debug(surf=0)),
    ({"campaign_id": "adventure_demo_v1", "location": {"id": "camp_clearing"}}, _debug(plateLocMatch=False)),
    ({"campaign_id": "adventure_demo_v1", "location": {"id": "crypt"}}, _debug()),  # camOrtho != the crypt pin
    ({"campaign_id": "adventure_demo_v1", "location": {"id": ""}}, _debug()),
])
def test_consumed_proof_refuses_a_player_that_never_applied_the_campaign(surface, debug):
    with pytest.raises(ValueError): OIV.check_consumed(surface, debug, MANIFEST)


def _fake_app(tmp_path, report="result=Succeeded\n"):
    app = tmp_path / "Bad.app"; (app / "Contents/MacOS").mkdir(parents=True); (app / "Contents/Resources/Data").mkdir(parents=True)
    binary = app / "Contents/MacOS/WorldOSPlayer"; binary.write_text("bad"); binary.chmod(0o755)
    (app / "Contents/Resources/Data/level0").write_text("clean")
    if report is not None: (tmp_path / "build-report.txt").write_text(report)
    return app


def test_preflight_refuses_red_before_writing(tmp_path):
    result = subprocess.run([SH, "preflight", str(_fake_app(tmp_path))], text=True, capture_output=True)
    assert result.returncode == 1 and "packaged pins RED/ERROR" in result.stderr
    assert list(tmp_path.glob("*.plist")) == []


def test_preflight_refuses_a_failed_build_report_even_though_it_is_nonempty(tmp_path):
    # StampFailedReport writes this beside a possibly-stale .app; a nonempty check takes it.
    # This is the exact gate qa/owner_install.sh shells out to in preflight.
    report = tmp_path / "build-report.txt"; report.write_text("result=Failed\ntotalErrors=2\n")
    out = subprocess.run([sys.executable, str(QA / "owner_install_verify.py"), "build-report", str(report)],
                         text=True, capture_output=True)
    assert out.returncode == 1 and "not Succeeded" in out.stderr
    assert 'owner_install_verify.py" build-report' in (QA / "owner_install.sh").read_text()


def test_no_probe_targets_the_nonexistent_health_route():
    # viewer/server.py do_GET 404s /health, so any /health poll waits out its deadline and aborts.
    probes = [ln for ln in (QA / "owner_install.sh").read_text().splitlines()
              if "curl" in ln and not ln.lstrip().startswith("#")]
    assert probes and not any("/health" in ln for ln in probes)


def test_restore_is_one_line_and_refuses_a_receipt_without_a_manifest(tmp_path):
    out = subprocess.run([SH, "restore", str(tmp_path)], text=True, capture_output=True)
    assert out.returncode == 1 and "restore.json" in out.stderr


def test_rendered_plists_land_under_their_own_labels(tmp_path):
    out = tmp_path / "out"
    subprocess.run([sys.executable, str(QA / "owner_install_plists.py"), "--output", str(out),
                    "--repo", "/Users/m1/worldos-owner", "--app", "/Users/m1/Applications/WorldOSPlayer.app",
                    "--source-app", str(_fake_app(tmp_path)), "--state", str(tmp_path / "state"),
                    "--uv", "/opt/homebrew/bin/uv", "--ledger", str(tmp_path / "ledger.json"),
                    "--mode", "dry-run", "--worktree-sha", "deadbeef",
                    "--build-report", str(tmp_path / "build-report.txt")], check=True)
    labels = sorted(p.name for p in out.glob("*.plist"))
    assert labels == ["org.worldos.owner-dm.plist", "org.worldos.owner-player.plist", "org.worldos.owner-session.plist"]
    assert plistlib.loads((out / "org.worldos.owner-dm.plist").read_bytes())["RunAtLoad"] is False
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["build_identity"]["result"] == "Succeeded"
    assert ledger["labels"] == ["org.worldos.owner-session", "org.worldos.owner-player", "org.worldos.owner-dm"]
