"""v1.0.4 wrapper-reliability set (audit F12-1, F12-3, F12-4, F12-5, F12-8 — issues #777 #787 #790 #791).

The beat-reliability cluster from docs/audits/ENGINE-AUDIT-2026-06-11.md unit 12:

* F12-1 (#753-adjacent): the flat 200s routine-beat deadline killed ~18% of HEALTHY beats
  (measured routine p90=224s, max=360s), and the ONE retry re-used the SAME deadline verbatim.
  Fix: routine default 200 -> 360 in ``clawdnd_dm_timeout`` + a ``clawdnd_dm_retry_timeout``
  escalation (attempt 2 gets the model-aware cold-open tier, never less than attempt 1's).
* F12-8 (#787): ``timeout(1)`` is a coreutils binary ABSENT on stock macOS — every beat died
  rc=127 in <1s, masked. Fix: the ``worldos_timeout`` shim (timeout(1) when present, else a
  python3 subprocess fallback preserving rc=124 deadline semantics) + a non-fatal preflight
  warning with the brew hint.
* F12-3 (#777): play.sh's cold open recorded an EMPTY opening unconditionally and entered the
  move loop anyway (the indefinitely-"running" dead session, 401-class proven 2026-06-02).
  Fix: the empty-DMSG abort + the ``clawdnd_pc_seated`` seating guard (factored to the lib,
  REUSED by play_party.sh) with one reseat retry then a loud abort.
* F12-4 (#790): play_party.sh never emitted the model-independent progress heartbeat #623
  factored into the lib for it (post-#763 the viewer flips progress at heartbeat INGEST).
* F12-5 (#791): play_party.sh was the ONLY beat loop without the soft clock-tick backstop.

Pattern follows test_dm_session_remint.py: gateway-free, no live claude/network, the REAL bash
helpers exercised under /bin/bash (macOS system bash 3.2 — also guards 3.2-cleanliness), plus
static anti-drift asserts on the wrapper call sites. Discovered + run via servers/engine pytest.
"""

import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "qa" / "lib_beat_driver.sh"
COMMON = ROOT / "scripts" / "launch_common.sh"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# A lib preamble that neutralizes ambient WORLDOS_/CLAWDND_ knobs so defaults are what we test.
_CLEAN = (
    'set -u\n'
    'unset WORLDOS_BEAT_TIMEOUT CLAWDND_BEAT_TIMEOUT WORLDOS_COLDOPEN_TIMEOUT '
    'CLAWDND_COLDOPEN_TIMEOUT CLAWDND_DM_MODEL WORLDOS_DM_MODEL 2>/dev/null || true\n'
    f'. "{LIB}"\n'
)


# ============================== F12-8: the worldos_timeout shim ==============================

def test_worldos_timeout_is_transparent_for_a_fast_command():
    """rc + stdout of the wrapped command pass through untouched (no deadline hit)."""
    r = _bash(_CLEAN + 'worldos_timeout 5 /bin/sh -c "echo HELLO; exit 7"; echo "rc=$?"')
    assert r.returncode == 0, r.stderr
    assert "HELLO" in r.stdout, (r.stdout, r.stderr)
    assert "rc=7" in r.stdout, "the child's exit code must pass through verbatim"


def test_worldos_timeout_kills_at_the_deadline_with_rc_124():
    """A wedged command is killed at the deadline and the caller sees timeout(1)'s rc=124."""
    t0 = time.monotonic()
    r = _bash(_CLEAN + 'worldos_timeout 1 sleep 30; echo "rc=$?"')
    wall = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    assert "rc=124" in r.stdout, (r.stdout, r.stderr)
    assert wall < 15, f"the deadline must actually kill the child (took {wall:.1f}s)"


def test_worldos_timeout_works_without_a_timeout_binary(tmp_path):
    """The F12-8 victim host: NO timeout(1) anywhere on PATH. The shim must still enforce the
    deadline (rc=124) and stay rc-transparent via the python3 subprocess fallback."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # a minimal PATH: python3 (for the fallback) + sleep (the test child). No timeout.
    py = subprocess.run(["/bin/bash", "-lc", "command -v python3"], capture_output=True, text=True).stdout.strip()
    assert py, "test needs a resolvable python3"
    os.symlink(py, bindir / "python3")
    os.symlink("/bin/sleep", bindir / "sleep")
    script = (
        _CLEAN
        + f'PATH="{bindir}"\n'
        + 'command -v timeout >/dev/null 2>&1 && { echo TIMEOUT_STILL_ON_PATH; exit 1; }\n'
        + 'worldos_timeout 1 sleep 30; echo "to-rc=$?"\n'
        + 'worldos_timeout 5 /bin/sh -c "exit 7"; echo "pass-rc=$?"\n'
        + 'worldos_timeout 5 /nonexistent-cmd-f128; echo "nf-rc=$?"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "TIMEOUT_STILL_ON_PATH" not in r.stdout
    assert "to-rc=124" in r.stdout, ("fallback must preserve rc=124 deadline semantics", r.stdout, r.stderr)
    assert "pass-rc=7" in r.stdout, ("fallback must pass the child's rc through", r.stdout, r.stderr)
    assert "nf-rc=127" in r.stdout, ("fallback must preserve rc=127 command-not-found semantics", r.stdout, r.stderr)


def test_worldos_timeout_prefers_the_native_binary_when_present(tmp_path):
    """When timeout(1) IS on PATH the shim must use it (no python interpreter per beat)."""
    stubdir = tmp_path / "stub"
    stubdir.mkdir()
    stub = stubdir / "timeout"
    stub.write_text('#!/bin/sh\necho NATIVE-TIMEOUT-USED\nshift\nexec "$@"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    r = _bash(_CLEAN + f'PATH="{stubdir}:$PATH"\nworldos_timeout 5 /bin/echo hi; echo "rc=$?"')
    assert r.returncode == 0, r.stderr
    assert "NATIVE-TIMEOUT-USED" in r.stdout, (r.stdout, r.stderr)
    assert "hi" in r.stdout and "rc=0" in r.stdout


def test_play_lanes_invoke_the_dm_through_the_shim():
    """Static anti-drift: both product lanes wrap the DM `claude -p` in worldos_timeout — a bare
    `timeout "$beat_timeout"` (the rc=127 dependency) must NOT remain in either."""
    for name in ("scripts/play.sh", "scripts/play_party.sh"):
        src = _src(name)
        assert 'worldos_timeout "$beat_timeout"' in src, f"{name} must use the worldos_timeout shim"
        # a BARE invocation = `timeout` as a standalone command word (helpers like worldos_timeout
        # and clawdnd_dm_retry_timeout end in the same token, hence the word-boundary lookbehind).
        assert not re.search(r'(?<![\w_])timeout "\$beat_timeout"', src), (
            f"{name} still invokes the bare coreutils `timeout` (rc=127 on stock macOS)"
        )


def test_preflight_warns_about_missing_timeout_with_a_brew_hint():
    """launch_common.sh preflight names the coreutils dep (non-fatal: the shim covers absence,
    so this WARNS with the brew hint instead of failing a launch the shim can serve)."""
    common = _src("scripts/launch_common.sh")
    assert "clawdnd_warn_if_no_timeout" in common, "preflight must check for timeout(1)"
    assert "brew install coreutils" in common, "the warning must carry the brew hint"
    # both play lanes run the check at startup
    for name in ("scripts/play.sh", "scripts/play_party.sh"):
        assert "clawdnd_warn_if_no_timeout" in _src(name), f"{name} must run the timeout preflight"
    # behavioral: with timeout absent the check WARNS (brew hint on stderr) and returns 0.
    r = _bash(
        f'set -u; . "{COMMON}"\n'
        'PATH="/nonexistent-only"\n'
        'clawdnd_warn_if_no_timeout; echo "rc=$?"\n'
    )
    assert r.returncode == 0, r.stderr
    assert "rc=0" in r.stdout, "the preflight warning must never fail the launch"
    assert "brew install coreutils" in r.stderr, (r.stdout, r.stderr)


# ====================== F12-1: routine deadline + retry-deadline recompute ======================

def test_routine_beat_timeout_default_is_360():
    """Measured routine p90=224s / max=360s — the flat 200s default killed ~18% of healthy beats."""
    r = _bash(_CLEAN + 'echo "bt=$(clawdnd_dm_timeout 0)"')
    assert r.returncode == 0, r.stderr
    assert "bt=360" in r.stdout, (r.stdout, r.stderr)


def test_routine_beat_timeout_env_override_still_wins():
    """CLAWDND_BEAT_TIMEOUT keeps its name + precedence (frozen wire contract); WORLDOS_ twin wins."""
    r = _bash(
        _CLEAN
        + 'CLAWDND_BEAT_TIMEOUT=222; echo "c=$(clawdnd_dm_timeout 0)"\n'
        + 'WORLDOS_BEAT_TIMEOUT=233; echo "w=$(clawdnd_dm_timeout 0)"\n'
    )
    assert r.returncode == 0, r.stderr
    assert "c=222" in r.stdout, r.stdout
    assert "w=233" in r.stdout, r.stdout


def test_retry_timeout_escalates_to_the_coldopen_tier():
    """Attempt 2 must NOT reuse attempt 1's deadline: it escalates to the model-aware cold-open
    tier (opus 500 / non-opus 550 after F12-2) and never DE-escalates below attempt 1's deadline."""
    r = _bash(
        _CLEAN
        + 'CLAWDND_DM_MODEL=opus\n'
        + 'echo "opus360=$(clawdnd_dm_retry_timeout 360)"\n'
        + 'echo "opus600=$(clawdnd_dm_retry_timeout 600)"\n'
        + 'CLAWDND_DM_MODEL=sonnet\n'
        + 'echo "sonnet360=$(clawdnd_dm_retry_timeout 360)"\n'
    )
    assert r.returncode == 0, r.stderr
    assert "opus360=500" in r.stdout, ("a routine retry escalates to the opus cold-open tier", r.stdout)
    assert "opus600=600" in r.stdout, ("never de-escalate below attempt 1's deadline", r.stdout)
    # F12-2: the non-opus cold-open tier rose 400 -> 550, so a non-opus routine retry escalates to 550.
    assert "sonnet360=550" in r.stdout, ("non-opus escalates to the 550s cold-open tier (F12-2)", r.stdout)


def test_retry_timeout_tolerates_a_garbage_base():
    """3.2-clean robustness: a non-numeric base (an env typo) still yields the escalation tier."""
    r = _bash(_CLEAN + 'CLAWDND_DM_MODEL=opus; echo "g=$(clawdnd_dm_retry_timeout banana)"')
    assert r.returncode == 0, r.stderr
    assert "g=500" in r.stdout, r.stdout


def test_play_lanes_recompute_the_retry_deadline():
    """Static anti-drift: both dm_turn paths captured beat_timeout ONCE and reused it verbatim on
    the retry (the F12-1 reuse bug) — they must now recompute via clawdnd_dm_retry_timeout."""
    for name in ("scripts/play.sh", "scripts/play_party.sh"):
        src = _src(name)
        assert 'beat_timeout="$(clawdnd_dm_retry_timeout "$beat_timeout")"' in src, (
            f"{name} retry must recompute its deadline (attempt 2 escalates, never reuses verbatim)"
        )


def test_play_sh_documented_routine_default_matches_the_lib():
    """play.sh's CLAWDND_BEAT_TIMEOUT line both documents AND seeds the routine default — it must
    agree with the lib's 360 (a 200 left here would silently pin the old kill deadline)."""
    play = _src("scripts/play.sh")
    assert 'CLAWDND_BEAT_TIMEOUT="${CLAWDND_BEAT_TIMEOUT:-360}"' in play, (
        "play.sh must seed/document the raised 360s routine default"
    )
    assert ':-200}' not in play, "the stale 200s default must not survive anywhere in play.sh"


# ================= F12-3: cold-open failure abort + the shared seating guard =================

def _seed_snapshot(state_dir: Path, camp: str, characters: dict, party: list):
    d = state_dir / "campaigns" / camp
    d.mkdir(parents=True)
    (d / "snapshot.json").write_text(json.dumps({"characters": characters, "party": party}))


def test_pc_seated_helper_matches_the_viewer_contract(tmp_path):
    """clawdnd_pc_seated == viewer _action_actor: seated means a party member whose character
    record is kind=player. Snapshot-read-only; missing/blank inputs -> not seated (rc=1)."""
    sd = tmp_path / "state"
    _seed_snapshot(sd, "c-seated", {"pc1": {"kind": "player"}, "n1": {"kind": "npc"}}, ["pc1"])
    _seed_snapshot(sd, "c-npc-only", {"n1": {"kind": "npc"}, "co1": {"kind": "companion"}}, ["co1"])
    _seed_snapshot(sd, "c-pc-not-in-party", {"pc1": {"kind": "player"}}, [])
    script = (
        f'set -u; . "{LIB}"\n'
        f'clawdnd_pc_seated "{sd}" c-seated; echo "seated=$?"\n'
        f'clawdnd_pc_seated "{sd}" c-npc-only; echo "npc=$?"\n'
        f'clawdnd_pc_seated "{sd}" c-pc-not-in-party; echo "unparty=$?"\n'
        f'clawdnd_pc_seated "{sd}" c-missing; echo "missing=$?"\n'
        f'clawdnd_pc_seated "{sd}" ""; echo "blank=$?"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "seated=0" in r.stdout, (r.stdout, r.stderr)
    assert "npc=1" in r.stdout, "a party of companions/NPCs only is NOT seated"
    assert "unparty=1" in r.stdout, "a player character outside the party is NOT seated"
    assert "missing=1" in r.stdout, "no snapshot -> not seated"
    assert "blank=1" in r.stdout, "a blank campaign id -> not seated (the dead-cold-open mode)"


def test_play_sh_cold_open_aborts_on_an_empty_opening():
    """The F12-3 masking half: a blank cold-open DMSG must abort non-zero BEFORE the move loop
    (today it recorded an unflagged empty row and entered the loop = a running unplayable session)."""
    play = _src("scripts/play.sh")
    assert "DM produced no opening" in play, "play.sh must carry the empty-opening abort"
    abort_at = play.index("DM produced no opening")
    loop_at = play.index("while true; do")
    assert abort_at < loop_at, "the abort must fire BEFORE the move loop is entered"
    # the abort exits non-zero (surfaces via the native bridge instead of masking)
    abort_line = next(ln for ln in play.splitlines() if "DM produced no opening" in ln)
    assert "exit 1" in abort_line, abort_line


def test_play_sh_runs_the_seating_guard_with_one_reseat_then_loud_abort():
    """play.sh must guard seating like play_party: clawdnd_pc_seated, ONE reseat retry on a fresh
    session, then a LOUD non-zero abort on the second miss — in both opener paths (the hero and
    DM-invents openers converge before the guard, so one guard covers both)."""
    play = _src("scripts/play.sh")
    assert play.count("clawdnd_pc_seated") >= 2, "guard must re-check after the reseat retry"
    assert "COLD-OPEN SEATED NO PC" in play, "the second miss must abort loudly"
    guard_at = play.index("clawdnd_pc_seated")
    loop_at = play.index("while true; do")
    assert guard_at < loop_at, "the seating guard must run BEFORE the move loop"
    # the reseat retry runs on a FRESH session id (a consumed cold-open --session-id collides)
    reseat_at = play.index("clawdnd_pc_seated")
    tail = play[reseat_at:loop_at]
    assert "DSID=" in tail, "the reseat retry must mint a fresh session id"


def test_play_party_reuses_the_lib_seating_guard():
    """F12-3 unify: play_party.sh's local pc_seated() is replaced by the SHARED lib helper —
    one implementation, zero drift (the audit's fix shape: extract the helper, stop patching one path)."""
    party = _src("scripts/play_party.sh")
    assert "clawdnd_pc_seated" in party, "play_party.sh must call the shared seating guard"
    assert "pc_seated()" not in party, "the local pc_seated() must be deleted (lib is the one impl)"
    assert party.count('clawdnd_pc_seated "$STATE_DIR" "$CAMPAIGN_ID"') >= 2, (
        "both the initial check and the post-reseat re-check must use the shared helper"
    )


def test_pc_seated_lives_in_the_lib():
    lib = _src("qa/lib_beat_driver.sh")
    assert "clawdnd_pc_seated()" in lib, "the seating guard must be factored into the shared lib"


# ============== F12-4: play_party wrapper heartbeat (the lane #623 never reached) ==============

def test_play_party_emits_the_wrapper_heartbeat_on_the_cold_open():
    """The campaign id is pre-seeded in the party lane, so even the cold open can heartbeat —
    the call must precede the cold-open DM turn."""
    party = _src("scripts/play_party.sh")
    assert "clawdnd_emit_progress_heartbeat" in party, "play_party.sh must emit the wrapper heartbeat"
    hb_at = party.index('clawdnd_emit_progress_heartbeat "$CAMPAIGN_ID" 1 0')
    coldopen_at = party.index('DMSG="$(turn dm "$DSID" 1')
    assert hb_at < coldopen_at, "the cold-open heartbeat must precede the cold-open DM turn"


def test_play_party_emits_the_wrapper_heartbeat_before_companion_moves():
    """Per-beat: the heartbeat lands after the human move is read and BEFORE companion_moves, so
    the player isn't staring at a dead spinner through companion latency."""
    party = _src("scripts/play_party.sh")
    beat_hb_at = party.index('clawdnd_emit_progress_heartbeat "$CAMPAIGN_ID" 0')
    human_move_at = party.index('chatlog player "$PMSG"')
    companions_at = party.index('COMP_BLOCK="$(companion_moves')
    assert human_move_at < beat_hb_at < companions_at, (
        "the per-beat heartbeat must land after the human move is read and BEFORE companion_moves"
    )


def test_play_party_heartbeat_uses_the_shared_helper_not_a_local_bank():
    """#763 decontamination: the heartbeat MUST be the shared lib helper (its exact wrapper lines
    are what app.jsx filters and the #763 fallback recognizes) — never a local text bank."""
    party = _src("scripts/play_party.sh")
    assert "CLAWDND_OPENING_PROGRESS_TEXT=" not in party, "no local heartbeat bank (lib owns the text)"
    assert "CLAWDND_MOVE_PROGRESS_TEXTS=" not in party, "no local heartbeat bank (lib owns the text)"


# ===================== F12-5: play_party soft clock-tick backstop =====================

def test_play_party_soft_ticks_after_each_recorded_beat():
    """Mirror of play.sh:475-478/504: capture PREV_DAY/PREV_TOD pre-beat, clawdnd_soft_tick after
    record_dm_reply — play_party was the ONLY beat loop without the backstop (frozen day-1 morning)."""
    party = _src("scripts/play_party.sh")
    assert 'clawdnd_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"' in party, (
        "play_party.sh must run the soft clock-tick backstop"
    )
    prev_at = party.index('PREV_DAY=')
    dm_beat_at = party.index('DMSG="$(turn dm "$DSID" 0 "[ARC CUE')
    record_at = party.index('record_dm_reply "$CAMPAIGN_ID" "$DMSG" beat')
    tick_at = party.index('clawdnd_soft_tick')
    assert prev_at < dm_beat_at, "PREV_DAY/PREV_TOD must be captured BEFORE the DM beat"
    assert record_at < tick_at, "the tick runs AFTER record_dm_reply (defer to the DM's own clock)"
    assert 'PREV_TOD=' in party


# =============================== syntax: every touched script ===============================

def test_touched_scripts_are_bash_n_clean():
    for rel in (
        "scripts/play.sh",
        "scripts/play_party.sh",
        "scripts/launch_common.sh",
        "qa/lib_beat_driver.sh",
    ):
        r = subprocess.run(["/bin/bash", "-n", str(ROOT / rel)], capture_output=True, text=True)
        assert r.returncode == 0, (rel, r.stderr)
