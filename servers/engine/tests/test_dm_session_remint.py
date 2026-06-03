"""Regression: the DM-turn retry must RE-MINT the session id, never reuse a consumed one.

Root cause this guards (forensics 2026-06-02, 3 reproduced runs): the cold-open ``claude -p``
attempt 1 failed (a 401 auth error in all 3 runs) but had ALREADY registered its ``--session-id``
on disk; the ONE retry re-passed that SAME ``--session-id``, which ``claude -p`` rejects
("Session ID <uuid> is already in use.") -> 0-byte output -> empty DM narration -> the cold open
never completes / ``can_act`` never flips. The fix is the shared helper
``clawdnd_dm_remint_session_on_retry`` (qa/lib_beat_driver.sh), wired into the retry of
scripts/play.sh + scripts/play_party.sh and qa/run_duo.sh's ``turn_retry``, plus
``clawdnd_report_attempt_failure`` which stops the masking by surfacing attempt 1's real error.

NOTE: the single-flight launch lock (a DIFFERENT, concurrent-launch concern) is owned by PR #564
(scripts/launch_common.sh) and intentionally NOT duplicated here.

These tests are gateway-free, need no live ``claude``/network/engine, and exercise the REAL bash
helpers under ``/bin/bash`` (macOS system bash is 3.2; the helpers are 3.2-clean, so this also
guards 3.2 compatibility). Discovered + run in CI via ``servers/engine`` pytest.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "qa" / "lib_beat_driver.sh"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- behavioral: the helper itself -----------------------------------------------------------

def test_remint_returns_fresh_id_for_create_mode_and_nothing_for_resume():
    """--session-id (a CREATE) -> a NEW --session-id; --resume -> untouched (empty)."""
    script = (
        f'set -u; . "{LIB}"\n'
        'clawdnd_dm_remint_session_on_retry --session-id OLD-UUID\n'
        'echo "create:${CLAWDND_DM_RETRY_SESSION[*]:-EMPTY}"\n'
        'clawdnd_dm_remint_session_on_retry --resume OLD-UUID\n'
        'echo "resume:${CLAWDND_DM_RETRY_SESSION[*]:-EMPTY}"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    create_line = next(ln for ln in r.stdout.splitlines() if ln.startswith("create:"))
    # create-mode re-mints a --session-id with a FRESH id that is NOT the consumed one.
    assert create_line.startswith("create:--session-id "), r.stdout
    assert "OLD-UUID" not in create_line, "retry must NOT reuse the consumed session id"
    # resume-mode is left untouched (no re-mint -> empty array).
    assert "resume:EMPTY" in r.stdout, r.stdout


def test_remint_two_retries_yield_distinct_ids():
    """Real uuid path (no shim): two re-mints must differ -> proves genuine uniqueness."""
    script = (
        f'set -u; . "{LIB}"\n'
        'clawdnd_dm_remint_session_on_retry --session-id OLD; a="${CLAWDND_DM_RETRY_SESSION[1]}"\n'
        'clawdnd_dm_remint_session_on_retry --session-id OLD; b="${CLAWDND_DM_RETRY_SESSION[1]}"\n'
        'echo "a=$a"; echo "b=$b"; [ -n "$a" ] && [ "$a" != "$b" ] && echo DISTINCT || echo SAME\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "DISTINCT" in r.stdout, r.stdout


def test_report_attempt_failure_names_a_401_auth_error(tmp_path):
    """A failed attempt's 401 must be surfaced (it was masked as a phantom session collision)."""
    fake = tmp_path / "attempt.jsonl"
    fake.write_text(
        '{"type":"system","subtype":"init","apiKeySource":"none"}\n'
        '{"type":"result","subtype":"success","is_error":true,"api_error_status":401,'
        '"result":"Failed to authenticate. API Error: 401 Invalid authentication credentials"}\n'
    )
    r = _bash(f'set -u; . "{LIB}"; clawdnd_report_attempt_failure "{fake}" 1')
    assert r.returncode == 0, r.stderr
    # The message goes to stderr; it must name the 401 and flag it as a non-retryable AUTH failure.
    assert "401" in r.stderr, r.stderr
    assert "AUTH" in r.stderr.upper(), r.stderr


# --- static contract: anti-drift across all three harnesses ----------------------------------

def test_shared_helpers_exist_and_only_remint_create_mode():
    lib = _src("qa/lib_beat_driver.sh")
    assert "clawdnd_dm_remint_session_on_retry()" in lib
    assert "CLAWDND_DM_RETRY_SESSION" in lib
    assert 'if [ "${1:-}" = "--session-id" ]; then' in lib
    assert "clawdnd_report_attempt_failure()" in lib


def test_all_three_harnesses_remint_on_retry():
    """The line whose ABSENCE was the bug must be present in every cold-open retry path."""
    play, party, duo = _src("scripts/play.sh"), _src("scripts/play_party.sh"), _src("qa/run_duo.sh")
    # play.sh + play_party.sh route their non-lean retry through the shared re-mint + error helper.
    for name, src in (("play.sh", play), ("play_party.sh", party)):
        assert "clawdnd_dm_remint_session_on_retry" in src, name
        assert "CLAWDND_DM_RETRY_SESSION" in src, name
        assert "clawdnd_report_attempt_failure" in src, name
    # play_party.sh gained a DM retry it did not have before.
    assert "retrying once with a fresh session" in party
    # run_duo.sh's cold-open ($3=1) retry mints a fresh id rather than reusing $2.
    assert 'if [ "${3:-}" = "1" ]; then' in duo
    assert 'turn "$1" "$_fresh" "$3" "${@:4}"' in duo


def test_play_party_dm_has_live_progress_rule():
    """#623: play_party.sh (the .app + VM-sweep claude DM path) must apply a live-progress rule so
    the DM logs an EARLY /events narration beat — parity with play_codex_dm.sh's
    LIVE_PROGRESS_LOG_RULE. Without it the DM emits nothing to /events until the full 85-157s beat
    completes, so the viewer shows blank and a player/persona perceives a 'dropped'/'hung' beat
    (sweep_v8 forensics: healthy beats, zero streaming refs). Guards against the rule being lost or
    the three harnesses drifting."""
    party = _src("scripts/play_party.sh")
    assert "CLAWDND_LIVE_PROGRESS_RULE=" in party, "play_party.sh lost the live-progress rule definition"
    assert 'msg="$CLAWDND_LIVE_PROGRESS_RULE"' in party, "the rule must be applied to the DM turn message"
    assert "Live progress rule" in party and "log_event" in party and "narration" in party
    # parity: the codex DM path already carried this intent.
    assert "LIVE_PROGRESS_LOG_RULE" in _src("scripts/play_codex_dm.sh")


def test_run_duo_p0_intro_is_a_tagged_say_move():
    """G5/behavioral: the duo's opening player intro (P0) must be a `say()` TAGGED move (via
    player_move), NOT raw prose. The behavioral gate `player_turns_structured` requires every player
    turn to be a facade move; a raw-text intro trips it RED and caps all G5 lenses ≤2.5 (measured on
    the VM 2026-06-03 — the #636 plain-text intro produced exactly that). So P0 goes through
    player_move with a 'SINGLE say()' prompt, keeping the intro a tagged [say] move."""
    duo = _src("qa/run_duo.sh")
    assert 'PMSG="$(player_move 1 ' in duo, "P0 intro must go through player_move (a tagged say move)"
    assert "SINGLE say(" in duo, "P0 prompt must ask for a say(), so the intro is a tagged move"
    assert "ONE OR TWO SENTENCES of plain text" not in duo, "the raw-text intro (behavioral RED) must be reverted"
    # the abort guard stays (an intro is still required).
    assert "player produced no intro — aborting" in duo


def test_run_duo_has_root_is_sandbox_guard():
    """The REAL beat-0 blocker on the root QA VM: claude refuses --dangerously-skip-permissions as
    root unless IS_SANDBOX=1, so every turn returns empty and the run silently aborts. run_duo.sh
    must fail LOUDLY (with the fix) instead of the confusing 'no intro' abort."""
    duo = _src("qa/run_duo.sh")
    assert '[ "$(id -u)" = "0" ] && [ -z "${IS_SANDBOX:-}" ]' in duo, "must detect root + missing IS_SANDBOX"
    assert "IS_SANDBOX=1 bash qa/run_duo.sh" in duo, "must tell the user the exact fix"


def test_play_party_single_flights_the_cold_open_campaign():
    """#640 (the #1 cross-persona G3 blocker): play_party.sh must REUSE a recent seeded campaign in
    its state dir rather than start_world-minting a fresh one on every launch. The .app native RESUME
    and the part-B harness each run the pre-seed; parallel campaigns made the viewer's is_live_view
    (= viewed == attached) latch False → frozen chronicle + 'viewing non-live campaign' read-only
    lockout (newbie/narrative/adversarial, 2026-06-03). Verified on the VM: 3 launches → 1 campaign."""
    party = _src("scripts/play_party.sh")
    assert "Single-flight (#640)" in party, "play_party.sh lost the single-flight reuse"
    assert "_minted = camp is None" in party, "must only mint when no recent campaign was reused"
    assert "time.time() - os.path.getmtime" in party, "reuse must be scoped by a recency window (this run only)"
