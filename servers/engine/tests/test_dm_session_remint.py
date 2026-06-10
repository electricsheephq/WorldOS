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


def test_play_party_drives_the_arc_runbook():
    """G1 (2026-06-03): the .app DM path (play_party.sh) must DRIVE the story arc per beat via the
    shared clawdnd_runbook_for_beat (the same arc-driver run_duo.sh uses, which reaches engine
    combat/travel/rest). Without it the DM was purely reactive and free-play personas finished at
    the intro — the full 8-beat arc never fired (G1 fail). Mirrors play.sh/run_duo arc-driving."""
    party = _src("scripts/play_party.sh")
    assert "clawdnd_runbook_for_beat" in party, "play_party.sh must call the shared arc runbook"
    assert "BEAT_NO=$((BEAT_NO + 1))" in party, "must advance a per-beat counter for the runbook"
    assert 'ARC CUE' in party and '$RUNBOOK' in party, "the runbook must be injected (framed as internal arc cue)"
    # the arc cue is INTERNAL planning the DM must NOT echo, and the reply must be prose not scaffolding
    # (2026-06-05 narrative crit: the DM rendered its scaffolding notes verbatim instead of a lived scene).
    assert "do NOT quote, echo, or render this line" in party.lower() or "Do NOT quote, echo, or render this line" in party
    assert "terse scaffolding" in party, "must forbid scaffolding-note output (prose-only rule)"


# --- #719: the cold-open RETRY must RESUME the minted campaign, not re-seed a second one ------
# Distinct from the session-id re-mint above (a 401 collision) AND from #640's play_party launch
# reuse: here play.sh's DEFAULT cold-open prompt says start_world + "if existing_campaigns, start
# fresh", so a timed-out attempt-1 (which already minted+seeded a campaign) gets a retry that mints
# a SECOND, party-less campaign — the viewer auto-follows the empty orphan ⇒ party-wipe + input-lock.
# The fix swaps the retry $msg to a resume directive (get_state, NO start_world) via a sourceable
# helper, so it's deterministically testable with no LLM/engine/network.

def test_coldopen_retry_resumes_existing_campaign_not_reseed():
    """first=1 (cold-open) + no authored hero + attempt-1 ALREADY minted a campaign ⇒ the retry
    message must RESUME that campaign (get_state + DO NOT start_world), NOT re-issue the fresh
    cold-open start_world prompt (which would orphan the seated save)."""
    script = (
        f'set -u; . "{LIB}"\n'
        'printf "%s" "$(clawdnd_coldopen_retry_msg 1 "" camp-EXISTING baldurs-gate '
        '"FRESH_COLDOPEN_SENTINEL call start_world here")"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "camp-EXISTING" in out, out
    assert "DO NOT call start_world" in out, out
    assert "get_state" in out, out
    assert "FRESH_COLDOPEN_SENTINEL" not in out, "the retry must NOT re-issue the fresh cold-open prompt"
    # the default-opener semantics survive: a canon PC is seated ONLY if the party is empty (never invent).
    assert "load_canon_character" in out, out
    assert "NEVER invent" in out or "never invent" in out.lower(), out


def test_coldopen_retry_unchanged_when_no_prior_campaign():
    """first=1 but attempt-1 minted NOTHING (live id empty) ⇒ nothing to resume → run the normal
    cold-open verbatim (byte-unchanged)."""
    script = (
        f'set -u; . "{LIB}"\n'
        'printf "%s" "$(clawdnd_coldopen_retry_msg 1 "" "" baldurs-gate "BASEMSG_SENTINEL")"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "BASEMSG_SENTINEL", r.stdout


def test_coldopen_retry_unchanged_for_continuing_beat():
    """first=0 (a continuing beat) ⇒ never a cold-open re-seed risk → base message unchanged even
    if a campaign exists."""
    script = (
        f'set -u; . "{LIB}"\n'
        'printf "%s" "$(clawdnd_coldopen_retry_msg 0 "" camp-EXISTING baldurs-gate "BASEMSG_SENTINEL")"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "BASEMSG_SENTINEL", r.stdout


def test_coldopen_retry_unchanged_for_authored_hero():
    """An AUTHORED hero (HERO_CAMP set) already uses the clean existing-campaign opener — the helper
    must not interfere (that branch owns its own resume directive)."""
    script = (
        f'set -u; . "{LIB}"\n'
        'printf "%s" "$(clawdnd_coldopen_retry_msg 1 hero-camp-123 camp-EXISTING baldurs-gate "BASEMSG_SENTINEL")"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "BASEMSG_SENTINEL", r.stdout


def test_play_sh_wires_the_coldopen_resume_helper():
    """Anti-drift: the helper must exist in the shared lib AND play.sh's retry must reassign $msg
    through it (so the cold-open retry resumes instead of re-seeding)."""
    lib = _src("qa/lib_beat_driver.sh")
    play = _src("scripts/play.sh")
    assert "clawdnd_coldopen_retry_msg()" in lib, "the resume-directive helper must exist in the lib"
    assert 'msg="$(clawdnd_coldopen_retry_msg "$first"' in play, "play.sh retry must reassign msg via the helper"


# --- #623: the SOLO play.sh path needs the SAME live-progress signal the party/codex paths have --
# The sweep_v8 run that filed #623 used the SOLO scripts/play.sh path. Forensics (Eva, on the run's
# own dm.*.jsonl): all four beats ran cleanly at ttft 2-5s and 85-157s wall — NO real drop/hang. The
# defect is PERCEIVED latency: play.sh had NEITHER the live-progress rule NOR the wrapper-authored
# heartbeat the party (#623) + codex paths already carry, so /events stayed blank for the whole beat
# (the viewer's notePendingProgress streaming-flip never fired) → the player stared at a static
# spinner and called it a dropped/hung beat. The fix is two-layer, both PERCEIVED-latency, neither
# touching wall-clock: a model-INDEPENDENT wrapper heartbeat (a guaranteed early /events row) + the
# model-cooperative live-progress rule. The existing bounded timeout+retry+#357 fallback is unchanged.


def test_live_progress_rule_is_shared_in_the_lib():
    """Anti-drift: factor the live-progress rule into the ONE shared lib so the harnesses can't drift.
    The string must be defined in lib_beat_driver.sh and carry its load-bearing intent."""
    lib = _src("qa/lib_beat_driver.sh")
    assert "CLAWDND_LIVE_PROGRESS_RULE=" in lib, "the live-progress rule must live in the shared lib"
    assert "Live progress rule" in lib and "log_event" in lib and "narration" in lib


def test_play_sh_applies_the_live_progress_rule_to_the_beat_turn():
    """#623: scripts/play.sh (the SOLO claude DM path that filed #623) must apply the live-progress
    rule to its per-beat DM turn — parity with play_party.sh + play_codex_dm.sh. Its ABSENCE is the
    bug: the solo DM emitted nothing to /events until the full 85-157s beat completed."""
    play = _src("scripts/play.sh")
    assert "CLAWDND_LIVE_PROGRESS_RULE" in play, "play.sh must reference the shared live-progress rule"
    assert "$CLAWDND_LIVE_PROGRESS_RULE" in play, "the rule must be spliced into the DM beat prompt"


def test_progress_heartbeat_helpers_exist_in_the_lib():
    """The model-INDEPENDENT heartbeat: a wrapper-authored progress beat the harness logs to /events
    itself, BEFORE the model runs — so the viewer flips off the generic spinner within ~1s no matter
    how long the model thinks (or whether it skips the cooperative early log_event). Mirrors the codex
    DM wrapper's proven OPENING_PROGRESS_TEXT / MOVE_PROGRESS_TEXTS pattern, factored to the shared lib."""
    lib = _src("qa/lib_beat_driver.sh")
    assert "clawdnd_progress_beat_text()" in lib, "the heartbeat-text chooser must exist in the lib"
    assert "clawdnd_emit_progress_heartbeat()" in lib, "the heartbeat emitter must exist in the lib"
    # the emitter routes through the shared engine-log helper (engine stays the sole writer).
    assert "log_engine_narration" in lib


def test_progress_beat_text_is_second_person_and_rotates():
    """The heartbeat text must be a SHORT 2nd-person player-facing teaser (it renders straight into
    the Chronicle), and continuing beats must ROTATE so a multi-beat session doesn't repeat one line.
    The cold open (first=1) gets its own opening teaser; continuing beats (first=0) cycle by index."""
    script = (
        f'set -u; . "{LIB}"\n'
        'echo "open:$(clawdnd_progress_beat_text 1 0)"\n'
        'echo "b0:$(clawdnd_progress_beat_text 0 0)"\n'
        'echo "b1:$(clawdnd_progress_beat_text 0 1)"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    lines = {ln.split(":", 1)[0]: ln.split(":", 1)[1] for ln in r.stdout.splitlines() if ":" in ln}
    # every teaser is non-empty 2nd-person prose addressed to "you"
    for key in ("open", "b0", "b1"):
        assert lines.get(key, "").strip(), f"{key} progress teaser must be non-empty"
        assert "you" in lines[key].lower(), f"{key} teaser must be 2nd-person (address 'you')"
    # continuing beats rotate (index 0 != index 1) so a long session never repeats the same line
    assert lines["b0"] != lines["b1"], "continuing-beat teasers must rotate by index"


def test_emit_progress_heartbeat_logs_a_narration_event(tmp_path, monkeypatch):
    """End-to-end: the emitter must write a real `narration` row to the engine session log for the
    given campaign — the row the viewer's /events poll reads to flip the spinner to 'streaming'. Mints
    a real campaign via the engine, then drives the bash helper against the SAME state dir; a blank
    campaign id must no-op (no crash, no row) since the heartbeat is best-effort, never fatal."""
    import json as _json
    import sys as _sys

    state = tmp_path / "state"
    state.mkdir()
    # Mint a real campaign + a session through the engine (the heartbeat needs a real campaign to log
    # into; an empty dir makes log_event raise — which the helper swallows, but then there's no row).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(state))
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(state))
    _sys.path.insert(0, str(ROOT / "servers" / "engine"))
    import server  # the engine module (servers/engine on sys.path)

    camp = server.create_campaign("Heartbeat")["id"]
    server.start_session(camp)

    script = (
        f'set -u; . "{LIB}"\n'
        f'export ROOT="{ROOT}"; export STATE_DIR="{state}"\n'
        f'export CLAWDND_STATE_DIR="{state}"; export WORLDOS_STATE_DIR="{state}"\n'
        # blank campaign id must no-op (the heartbeat is best-effort, never fatal)
        'clawdnd_emit_progress_heartbeat "" 1 0; echo "blank-rc=$?"\n'
        # a real campaign id must log a narration progress beat
        f'clawdnd_emit_progress_heartbeat "{camp}" 0 0; echo "real-rc=$?"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "blank-rc=0" in r.stdout, ("blank id must no-op cleanly", r.stdout, r.stderr)
    assert "real-rc=0" in r.stdout, ("a real heartbeat must return 0", r.stdout, r.stderr)
    # the engine must have appended at least one narration row for the campaign
    sess_dir = state / "campaigns" / camp / "sessions"
    rows = []
    for f in sess_dir.glob("*.jsonl"):
        for ln in f.read_text().splitlines():
            ln = ln.strip()
            if ln:
                rows.append(_json.loads(ln))
    narr = [x for x in rows if x.get("kind") == "narration" and (x.get("text") or "").strip()]
    assert narr, ("the heartbeat must log a player-facing narration row to the engine", r.stdout, r.stderr)
    assert "you" in narr[-1]["text"].lower(), "the logged heartbeat must be 2nd-person prose"


def test_play_sh_emits_the_heartbeat_before_each_dm_turn():
    """Anti-drift: play.sh must call the heartbeat emitter on BOTH its openers and the per-beat turn,
    so a guaranteed /events row precedes the model's long think on every beat type."""
    play = _src("scripts/play.sh")
    assert play.count("clawdnd_emit_progress_heartbeat") >= 2, (
        "play.sh must emit the heartbeat on the cold-open AND per-beat paths"
    )


# --- #745: the GUI-sweep DM driver must BOUND every beat (the newbie mid-stream-stall give-up) ----
# The lone v1.0.4-rc2 RRI holdout @c92a393 (newbie) hit a DM beat that STREAMED partial prose and then
# FROZE mid-generation. qa/ui_playtest.sh's dm_turn ran `claude -p` with NO `timeout`, NO retry, and NO
# fallback — unlike scripts/play.sh's dm_turn — so the frozen process hung forever: dm_turn never
# returned -> `chatlog dm` (the turn-END /chat line) never fired -> the turn never RESOLVED on the
# client, so the backend offered NO recovery (the client-side stall ceiling, #745 app.jsx, then had to
# carry the whole burden). These guard that the GUI driver now wall-clocks the beat and falls back to
# the engine-logged narration tail so a stalled beat always resolves on /chat.

def test_ui_playtest_dm_turn_wraps_claude_in_a_bounded_timeout():
    """The GUI-sweep dm_turn must wrap `claude -p` in `timeout` (tiered via the shared clawdnd_dm_timeout)
    so a frozen beat is KILLED at a deadline and dm_turn returns — never an indefinite hang."""
    src = _src("qa/ui_playtest.sh")
    # the shared, tiered deadline helper is resolved…
    assert 'clawdnd_dm_timeout "$first"' in src, "ui_playtest.sh must resolve the per-beat deadline via the shared helper"
    # …and the claude invocation is wrapped in `timeout "$beat_timeout"` (the line continuation puts the
    # `claude -p` on the following line, so assert both tokens are present near each other).
    assert 'timeout "$beat_timeout"' in src, "ui_playtest.sh dm_turn must wall-clock `claude -p` with `timeout`"
    # a bare, unbounded `claude -p \\` (no timeout on the same logical line) must NOT remain.
    assert "\n  claude -p " not in src, "ui_playtest.sh must not invoke `claude -p` unbounded (no timeout wrapper)"


def test_ui_playtest_dm_turn_falls_back_to_engine_narration_on_a_stalled_beat():
    """A killed/empty beat must still RESOLVE on /chat: each dm_turn result routes through the shared
    fallback front door (#749c: clawdnd_resolve_dm_reply — a DIRECT call wrapping
    clawdnd_dm_narration_or_fallback, so the recovery flag survives) and the engine-logged narration
    tail becomes the turn-END line, with a recovered reply stamped fallback_recovered on the chat row."""
    src = _src("qa/ui_playtest.sh")
    assert src.count('clawdnd_resolve_dm_reply "$DMSG" "$STATE_DIR"') >= 2, (
        "ui_playtest.sh must recover BOTH the opening and per-beat turns via the shared fallback "
        "front door so a stalled beat still resolves"
    )
    assert "clawdnd_chatlog_dm" in src, (
        "ui_playtest.sh must write dm rows via clawdnd_chatlog_dm so a recovered reply carries "
        "the fallback_recovered honesty stamp (#749c)"
    )


def test_ui_playtest_dm_timeout_is_bash32_clean_and_sourced():
    """The driver sources the shared lib and clawdnd_dm_timeout resolves a positive integer under the
    macOS system bash 3.2 — proving the new wiring is 3.2-clean and actually reachable from this script."""
    lib = ROOT / "qa" / "lib_beat_driver.sh"
    script = (
        f'set -u; . "{lib}"\n'
        # cold-open tier (first=1) and continuing tier (first=0) must both yield a positive integer.
        'CLAWDND_DM_MODEL=opus; co="$(clawdnd_dm_timeout 1)"; bt="$(clawdnd_dm_timeout 0)"\n'
        'echo "co=$co bt=$bt"\n'
        'case "$co" in (*[!0-9]*|"") echo BAD_CO; exit 1;; esac\n'
        'case "$bt" in (*[!0-9]*|"") echo BAD_BT; exit 1;; esac\n'
        '[ "$co" -gt 0 ] && [ "$bt" -gt 0 ] && echo OK\n'
    )
    r = _bash(script)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "OK" in r.stdout, (r.stdout, r.stderr)
