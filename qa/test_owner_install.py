import json, plistlib, re, shlex, subprocess, sys
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
    assert dm["StandardOutPath"] == str(tmp_path / "state/owner-dm.log")
    assert dm["StandardErrorPath"] == str(tmp_path / "state/owner-dm.err.log")


def test_the_viewer_writes_the_exact_chat_file_the_dm_tails(tmp_path):
    """Wire contract at the viewer<->DM seam, read off the REAL qa/agent_play.sh.

    Unit-testing each side against the other's assumption is exactly how this drifts: a
    viewer writing chat.json leaves `serve` tailing a file nobody writes, and every owner
    line goes unanswered while both halves look correct in isolation.
    """
    script = (QA / "agent_play.sh").read_text()
    derived = re.search(r'chat_path "\$state/([\w.]+)"', script)
    assert derived, "agent_play.sh no longer derives chat_path from the state dir"
    session, _p, dm = OIP.render_plists(Path("/Users/m1/worldos-owner"), tmp_path / "a.app", tmp_path / "state", Path("/opt/homebrew/bin/uv"))
    state_dir = Path(dm["ProgramArguments"][dm["ProgramArguments"].index("--state") + 1])
    assert session["EnvironmentVariables"]["WORLDOS_VIEWER_CHAT"] == str(state_dir / derived.group(1))
    # `serve` reads its run dir from WORLDOS_AGENT_PLAY_ROOT, defaulting inside the checkout.
    assert "WORLDOS_AGENT_PLAY_ROOT" in script


def test_the_dm_agent_invokes_flags_the_real_script_accepts():
    script = (QA / "agent_play.sh").read_text()
    _s, _p, dm = OIP.render_plists(Path("/Users/m1/worldos-owner"), Path("/a.app"), Path("/st"), Path("/opt/homebrew/bin/uv"))
    assert "serve)" in script, "agent_play.sh no longer dispatches a `serve` subcommand"
    for flag in [a for a in dm["ProgramArguments"] if a.startswith("--")]:
        assert f"{flag})" in script, f"agent_play.sh does not accept {flag}"


@pytest.mark.parametrize("engine,qa", [(8766, 8981), (8776, 8971)])
def test_reserved_owner_ports_are_refused(engine, qa):
    with pytest.raises(ValueError): OIP.validate_ports(engine, qa)


@pytest.mark.parametrize("report", ["result=Failed\ntotalErrors=3\n", "totalWarnings=0\n", ""])
def test_failed_or_unstamped_build_reports_are_refused(report):
    with pytest.raises(ValueError): OIV.check_build_report(report)


def test_successful_build_report_is_accepted():
    fields = OIV.check_build_report(
        "result=Succeeded\ntotalErrors=0\nbuildEndedAt=9/2/2026 10:38:41 AM\n"
        "alwaysIncludedShaders=WorldOS/OccluderDepth,WorldOS/ActorSilhouette\n")
    assert fields["result"] == "Succeeded" and fields["buildEndedAt"].startswith("9/2/2026")


def test_build_report_missing_required_shader_is_refused_with_the_line():
    line = "alwaysIncludedShaders=WorldOS/OccluderDepth"
    with pytest.raises(ValueError, match=r"alwaysIncludedShaders=.*ActorSilhouette"):
        OIV.check_build_report(f"result=Succeeded\n{line}\n")


def test_build_sha_cannot_bypass_the_required_shader_report(tmp_path):
    app = _fake_app(tmp_path, report=None)
    body = f'''BUILD_SHA=deadbeef
python3() {{ echo "PINS GREEN"; }}
preflight {shlex.quote(str(app))}
'''
    out = _owner_shell(tmp_path, body)
    assert out.returncode == 1
    assert "build-report.txt is required" in out.stderr


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


def _fake_app(tmp_path, report=("result=Succeeded\n"
                                "alwaysIncludedShaders=WorldOS/OccluderDepth,WorldOS/ActorSilhouette\n")):
    app = tmp_path / "Bad.app"; (app / "Contents/MacOS").mkdir(parents=True); (app / "Contents/Resources/Data").mkdir(parents=True)
    binary = app / "Contents/MacOS/WorldOSPlayer"; binary.write_text("bad"); binary.chmod(0o755)
    (app / "Contents/Resources/Data/level0").write_text("clean")
    if report is not None: (tmp_path / "build-report.txt").write_text(report)
    return app


def _owner_shell(tmp_path, body):
    prefix = Path(SH).read_text().split("\nMODE=", 1)[0].replace(
        'ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)', f'ROOT={shlex.quote(str(QA.parent))}')
    return subprocess.run(["/bin/bash", "-c", prefix + "\n" + body], text=True, capture_output=True,
                          env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                               "TMPDIR": str(tmp_path)})


def _probe_stubs(work, heartbeat=True):
    launch = 'mkdir -p "$DM_RUN"; touch "$DM_HEARTBEAT"' if heartbeat else ":"
    return f'''STATE={shlex.quote(str(work / "state"))}
DM_RUN="$STATE/agent_play_runs/owner"; DM_HEARTBEAT="$DM_RUN/serve.heartbeat"
DM_LOG="$STATE/owner-dm.log"; DM_ERR="$STATE/owner-dm.err.log"
mkdir -p "$STATE"
launchctl() {{ [[ "$*" != *"$DM"* ]] || {{ {launch}; }}; }}
await_code() {{ :; }}
curl() {{ printf '%s\\n' '{{"campaign_id":"adventure_demo_v1","location":{{"id":"camp_clearing"}},"surf":4,"plateLocMatch":true,"camOrtho":13.0}}'; }}
python3() {{ :; }}
sleep() {{ :; }}
mktemp() {{ mkdir -p {shlex.quote(str(work / "probe-tmp"))}; printf '%s\\n' {shlex.quote(str(work / "probe-tmp"))}; }}
'''


def test_start_probe_trap_is_scoped_and_cleans_success_and_failure(tmp_path):
    success = tmp_path / "success"
    ok = _owner_shell(tmp_path, _probe_stubs(success) + "start_and_probe\n")
    assert ok.returncode == 0, ok.stderr
    assert not (success / "probe-tmp").exists()

    failed = tmp_path / "failed"
    body = _probe_stubs(failed) + "python3() { return 1; }\nstart_and_probe\n"
    bad = _owner_shell(tmp_path, body)
    assert bad.returncode == 1
    assert not (failed / "probe-tmp").exists()


def test_reseed_archives_the_dm_run_and_chat_before_starting_fresh(tmp_path):
    state = tmp_path / "state"
    run = state / "agent_play_runs/owner"
    run.mkdir(parents=True)
    (run / "session.json").write_text('{"chat_cursor": 9}')
    (state / "chat.jsonl").write_text('{"role":"player","text":"old"}\n')
    out = _owner_shell(tmp_path, f"reset_dm_run {shlex.quote(str(state))}\n")
    assert out.returncode == 0, out.stderr
    assert not run.exists() and not (state / "chat.jsonl").exists()
    archived = list((state / "agent_play_runs").glob("owner.archived-*"))
    assert len(archived) == 1 and (archived[0] / "session.json").is_file()
    assert len(list(state.glob("chat.archived-*.jsonl"))) == 1


def test_missing_dm_heartbeat_refuses_with_last_log_lines(tmp_path):
    work = tmp_path / "missing-heartbeat"
    state = work / "state"; state.mkdir(parents=True)
    (state / "owner-dm.err.log").write_text("auth/model open failed\n")
    result = _owner_shell(tmp_path, _probe_stubs(work, heartbeat=False) + "start_and_probe\n")
    assert result.returncode == 1
    assert "heartbeat" in result.stderr and "auth/model open failed" in result.stderr
    script = (QA / "agent_play.sh").read_text()
    assert re.search(r'touch .*HEARTBEAT', script), "serve must touch its heartbeat every poll"


def test_consumption_probe_waits_through_initial_zero_and_records_green(tmp_path):
    work = tmp_path / "consumption"; work.mkdir()
    ledger = work / "ledger.json"; ledger.write_text('{"gate_results": {}}')
    count = work / "count"
    body = f'''OWNER_REPO={shlex.quote(str(QA.parent))}
curl() {{
  case "$*" in
    *session-surface*) printf '%s\\n' '{{"campaign_id":"adventure_demo_v1","location":{{"id":"camp_clearing"}}}}' ;;
    *) n=0; [[ ! -f {shlex.quote(str(count))} ]] || n=$(<{shlex.quote(str(count))}); n=$((n+1)); printf '%s' "$n" >{shlex.quote(str(count))};
       if ((n < 3)); then printf '%s\\n' '{{"surf":0,"plateLocMatch":false,"camOrtho":13.0}}';
       else printf '%s\\n' '{{"surf":4,"plateLocMatch":true,"camOrtho":13.0}}'; fi ;;
  esac
}}
sleep() {{ :; }}
await_consumed {shlex.quote(str(work / "surface.json"))} {shlex.quote(str(work / "debug.json"))} 180 {shlex.quote(str(ledger))}
'''
    out = _owner_shell(tmp_path, body)
    assert out.returncode == 0, out.stderr
    assert count.read_text() == "3" and "elapsed=2s" in out.stdout
    assert json.loads(ledger.read_text())["gate_results"]["consumption"]["result"] == "GREEN"
    assert re.search(r"await_consumed .* 180", (QA / "owner_install.sh").read_text())


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
