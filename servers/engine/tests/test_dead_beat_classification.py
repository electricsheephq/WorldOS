"""SYN-01 (#757 / #745) — dead-beat masking & failure classification.

Three interlocking masks made ~10.5% of DM invocations look "resolved" while the player got
nothing (or worse, an auth error rendered as narration):

  (a) a 401-class ``claude -p`` failure carries NON-empty result text (verified verbatim:
      ``subtype:"success", is_error:true, api_error_status:401``) — it bypassed the empty-only
      retry (qa/run_duo.sh turn_retry) AND the empty-only #357 fallback gate and was chatlogged
      AS DM PROSE ("Failed to authenticate…" as narration);
  (b) a fully-dead beat's #357 fallback recycled the PREVIOUS beat's prose (or, post-#763 in the
      heartbeat lanes, ``record_dm_reply`` wrote an unflagged EMPTY dm row) — the beat looked
      resolved while the player saw nothing new;
  (c) three QA runners (qa/run_duo.sh, qa/ui_playtest.sh, qa/run_party.sh) re-defined a 3-arg
      ``chatlog`` AFTER sourcing the lib, silently discarding ``worldos_chatlog_dm``'s
      ``{"fallback_recovered":true}`` honesty stamp — and nothing in qa/assert_behavioral.py
      consumed the stamp even where it worked.

These tests pin the fix: the FINAL result event is classified FIRST (error-class ⇒ the beat
FAILED — never chat the error text, never fallback-recycle, surface the re-auth hint via the
existing worldos_report_attempt_failure pattern); ``record_dm_reply`` refuses blank text and
records a wrapper-authored VISIBLE failure beat stamped ``{"beat_failed":true}``; the pre-beat
log-tail mark preserves the GENUINE #357 win (NEW prose logged this beat, then the turn died);
the 3 chatlog overrides are deleted so the shared lib (incl. the stamp) is the single
implementation; and qa/assert_behavioral.py counts + reports both stamps (gate policy stays
#757's call — the counters never flip RED by themselves).

Bash helpers run under ``/bin/bash`` exactly as the play/QA wrappers invoke them (macOS system
bash 3.2-clean), mirroring tests/test_heartbeat_repair.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_DIR.parents[1]
LIB = REPO_ROOT / "qa" / "lib_beat_driver.sh"

PROSE_1 = "You step into the Heapside warren as lamplight gutters along the brick."
PROSE_2 = "Mirelda lowers her voice; the ledger between you suddenly feels heavier."
ERR_TEXT = "Failed to authenticate: invalid API key provided"

QA_RUNNERS = ("qa/run_duo.sh", "qa/ui_playtest.sh", "qa/run_party.sh")
ALL_DM_WRAPPERS = QA_RUNNERS + ("scripts/play.sh", "scripts/play_party.sh")


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def _hdr(state: Path, chat: Path) -> str:
    return f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{state}"; CHAT="{chat}"; . "{LIB}"\n'


def _chat_rows(chat: Path) -> list[dict]:
    if not chat.exists():
        return []
    return [
        json.loads(line)
        for line in chat.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_result_jsonl(path: Path, *, result: str, is_error: bool = False,
                        status: int | None = None, subtype: str = "success") -> Path:
    """A minimal stream-json transcript ending on a final ``result`` event — the 401 shape is
    the audit's verified-verbatim sample (subtype:"success", is_error:true, api_error_status)."""
    ev: dict = {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    if status is not None:
        ev["api_error_status"] = status
    path.write_text(
        json.dumps({"type": "system", "subtype": "init"}) + "\n" + json.dumps(ev) + "\n",
        encoding="utf-8",
    )
    return path


def _seed_campaign(state: Path, prose_rows: list[str]) -> Path:
    """A minimal snapshot + session log shaped exactly like the engine's on-disk format (the
    same fixture shape test_heartbeat_repair.py uses), so the #357 fallback can recover prose."""
    camp = state / "campaigns" / "c1"
    (camp / "sessions").mkdir(parents=True)
    (camp / "snapshot.json").write_text(
        json.dumps({"id": "c1", "active_session_id": "s1", "day": 1}), encoding="utf-8"
    )
    log = camp / "sessions" / "s1.jsonl"
    log.write_text(
        "".join(
            json.dumps({"t": float(i), "kind": "narration", "text": t}) + "\n"
            for i, t in enumerate(prose_rows)
        ),
        encoding="utf-8",
    )
    return log


# ── leg 1: the FINAL result event is classified FIRST ────────────────────────────────────────


def test_final_text_error_class_echoes_nothing_and_surfaces_reauth_hint(tmp_path):
    """A 401-class result's text is the API's error string, NEVER a reply: the shared extraction
    front door must echo NOTHING (so the empty-only retries now fire on error results too) and
    surface the failure + re-auth hint via the existing worldos_report_attempt_failure pattern."""
    out = _write_result_jsonl(tmp_path / "out.jsonl", result=ERR_TEXT, is_error=True, status=401)
    chat = tmp_path / "chat.jsonl"
    script = (
        _hdr(tmp_path, chat)
        + f'txt="$(worldos_dm_final_text "{out}" "$STATE_DIR" 1)"\n'
        + 'printf "TXT[%s]\\n" "$txt"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "TXT[]" in r.stdout, f"error text leaked as the reply: {r.stdout!r}"
    assert ERR_TEXT not in r.stdout
    assert "401" in r.stderr, f"the HTTP status must be surfaced: {r.stderr!r}"
    assert "AUTH" in r.stderr and "NOT retryable" in r.stderr, (
        "the 401/403 re-auth operator hint (worldos_report_attempt_failure) must fire"
    )
    # The pointer file lets the caller's resolve classify the SAME final result event.
    ptr = tmp_path / ".dm_last_result"
    assert ptr.read_text(encoding="utf-8").strip() == str(out)


def test_final_text_healthy_result_passes_through(tmp_path):
    out = _write_result_jsonl(tmp_path / "out.jsonl", result=PROSE_1)
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + f'txt="$(worldos_dm_final_text "{out}" "$STATE_DIR" 0)"\n'
        + 'printf "TXT[%s]\\n" "$txt"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert f"TXT[{PROSE_1}]" in r.stdout
    assert "[dm-attempt]" not in r.stderr, "a healthy result must not be reported as a failure"


def test_final_text_resultless_stream_is_empty_not_error(tmp_path):
    """A timeout-killed attempt (no result event at all) is NOT error-class here — the callers'
    rc-based reporting + the empty-reply path own that mode (today's behavior, preserved)."""
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps({"type": "system", "subtype": "init"}) + "\n", encoding="utf-8")
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + f'txt="$(worldos_dm_final_text "{out}" "$STATE_DIR" 124)"\n'
        + 'printf "TXT[%s]\\n" "$txt"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "TXT[]" in r.stdout
    assert "[dm-attempt]" not in r.stderr


def test_resolve_classifies_error_result_as_failed_beat_never_recycles(tmp_path):
    """The dead-beat mask, leg (a)+(b) together: an error-class final result fails the beat —
    the reply is EMPTY (no error text, no recycled prose even though the log has prior prose)
    and WORLDOS_DM_BEAT_FAILED=1."""
    _seed_campaign(tmp_path, [PROSE_1])  # recycle bait: the previous beat's prose is recoverable
    out = _write_result_jsonl(tmp_path / "out.jsonl", result=ERR_TEXT, is_error=True, status=401)
    chat = tmp_path / "chat.jsonl"
    script = (
        _hdr(tmp_path, chat)
        + f'_="$(worldos_dm_final_text "{out}" "$STATE_DIR" 1)"\n'
        + 'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        + 'printf "failed=%s recovered=%s reply=[%s]\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "failed=1" in r.stdout, r.stdout
    assert "recovered=0" in r.stdout
    assert "reply=[]" in r.stdout, f"a failed beat must resolve to an EMPTY reply: {r.stdout!r}"
    assert ERR_TEXT not in r.stdout and PROSE_1 not in r.stdout


def test_resolve_healthy_reply_unchanged(tmp_path):
    """A normal beat (healthy result event + non-empty reply) is byte-identical to today."""
    _seed_campaign(tmp_path, [PROSE_1])
    out = _write_result_jsonl(tmp_path / "out.jsonl", result=PROSE_2)
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + f'_="$(worldos_dm_final_text "{out}" "$STATE_DIR" 0)"\n'
        + f'worldos_resolve_dm_reply {PROSE_2!r} "$STATE_DIR"\n'
        + 'printf "failed=%s recovered=%s reply=[%s]\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "failed=0" in r.stdout and "recovered=0" in r.stdout
    assert f"reply=[{PROSE_2}]" in r.stdout


# ── leg 2: recycled-vs-genuine recovery (the pre-beat log-tail mark) ─────────────────────────


def test_resolve_recycled_prose_is_a_failed_beat(tmp_path):
    """Both attempts die with NO new prose logged: the #357 fallback would recover the PREVIOUS
    beat's prose. With a pre-beat mark in place that recovery is RECYCLED ⇒ the beat FAILED
    (reply empty), instead of masking the dead beat as resolved (F12-14)."""
    _seed_campaign(tmp_path, [PROSE_1])
    out = _write_result_jsonl(tmp_path / "out.jsonl", result="")  # died: empty result text
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + 'worldos_dm_prebeat_mark "$STATE_DIR"\n'
        + f'_="$(worldos_dm_final_text "{out}" "$STATE_DIR" 124)"\n'
        + 'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        + 'printf "failed=%s recovered=%s reply=[%s]\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "failed=1" in r.stdout, f"recycled recovery must FAIL the beat: {r.stdout!r}"
    assert "recovered=0" in r.stdout
    assert "reply=[]" in r.stdout
    assert PROSE_1 not in r.stdout, "the previous beat's prose must never be recycled"


def test_resolve_new_prose_after_mark_is_genuine_357_recovery(tmp_path):
    """The genuine #357 win is PRESERVED: the DM logged NEW prose THIS beat (after the mark)
    and then died before its final reply ⇒ the recovery is real (fallback_recovered=1)."""
    log = _seed_campaign(tmp_path, [PROSE_1])
    out = _write_result_jsonl(tmp_path / "out.jsonl", result="")
    mark = _hdr(tmp_path, tmp_path / "chat.jsonl") + 'worldos_dm_prebeat_mark "$STATE_DIR"\n'
    r = _bash(mark)
    assert r.returncode == 0, r.stderr
    # The DM logs NEW prose mid-beat (P2), then the turn dies.
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": 99.0, "kind": "narration", "text": PROSE_2}) + "\n")
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + f'_="$(worldos_dm_final_text "{out}" "$STATE_DIR" 124)"\n'
        + 'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        + 'printf "failed=%s recovered=%s\\nreply=[%s]\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "failed=0" in r.stdout, f"a genuine recovery must NOT fail the beat: {r.stdout!r}"
    assert "recovered=1" in r.stdout
    assert PROSE_2 in r.stdout, "the NEW prose must be the recovered reply"


def test_resolve_without_mark_keeps_legacy_recovery(tmp_path):
    """No pre-beat mark (an older/external caller) ⇒ assume-genuine, exactly today's behavior —
    the classification layer must never regress a caller that doesn't mark."""
    _seed_campaign(tmp_path, [PROSE_1])
    script = (
        _hdr(tmp_path, tmp_path / "chat.jsonl")
        + 'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        + 'printf "failed=%s recovered=%s reply=[%s]\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "failed=0" in r.stdout and "recovered=1" in r.stdout
    assert PROSE_1 in r.stdout


def test_wrapper_heartbeat_lane_dead_beat_ends_in_visible_failure_row(tmp_path):
    """The HEARTBEAT lane (play.sh/play_party.sh, post-#763): a dead beat's only post-mark row
    is the wrapper heartbeat, which BREAKS the #357 fallback's trailing block — so resolve
    yields an EMPTY reply (no recycle, no recovery), and the downstream blank guard records the
    wrapper-authored VISIBLE failure beat instead of the old unflagged EMPTY row. ALSO pins
    that the heartbeat row alone never counts as a genuine recovery (recovered=0)."""
    import wrapper_progress

    log = _seed_campaign(tmp_path, [PROSE_1])
    out = _write_result_jsonl(tmp_path / "out.jsonl", result="")
    chat = tmp_path / "chat.jsonl"
    r = _bash(_hdr(tmp_path, chat) + 'worldos_dm_prebeat_mark "$STATE_DIR"\n')
    assert r.returncode == 0, r.stderr
    with log.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"t": 99.0, "kind": "narration",
                 "text": wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE}
            ) + "\n"
        )
    script = (
        _hdr(tmp_path, chat)
        + f'_="$(worldos_dm_final_text "{out}" "$STATE_DIR" 124)"\n'
        + 'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        + 'printf "recovered=%s reply=[%s]\\n" "$WORLDOS_FALLBACK_RECOVERED" "$WORLDOS_DM_REPLY"\n'
        + 'record_dm_reply "c1" "$WORLDOS_DM_REPLY" beat\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "recovered=0" in r.stdout
    assert "reply=[]" in r.stdout, (
        f"a heartbeat row alone must never be recovered as this beat's prose: {r.stdout!r}"
    )
    rows = _chat_rows(chat)
    assert len(rows) == 1 and rows[0].get("beat_failed") is True, (
        f"the dead heartbeat-lane beat must surface as ONE visible failure row: {rows}"
    )
    assert PROSE_1 not in rows[0]["text"]


# ── leg 2: record_dm_reply blank guard + the visible failure beat ────────────────────────────


def test_record_dm_reply_blank_records_visible_failure_row(tmp_path):
    """Blank text never writes a blank/hidden dm row: the wrapper-authored VISIBLE failure beat
    is recorded instead — stamped {"beat_failed":true}, NOT engine_logged (so the client always
    renders it), logged exactly once, and warned on stderr."""
    chat = tmp_path / "chat.jsonl"
    script = _hdr(tmp_path, chat) + 'record_dm_reply "" "" beat\n'
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "warning" in r.stderr.lower() and "failure beat" in r.stderr.lower(), r.stderr
    rows = _chat_rows(chat)
    assert len(rows) == 1, f"exactly ONE failure row expected, got: {rows}"
    row = rows[0]
    assert row["role"] == "dm"
    assert row.get("beat_failed") is True, f"the failure row must be stamped: {row}"
    assert row["text"].strip(), "the failure row must be VISIBLE prose, never blank"
    assert "engine_logged" not in row, (
        "the failure row must NOT be engine_logged — the client would hide it (app.jsx drops "
        "engine_logged rows in favor of /events, where this row never lands)"
    )


def test_record_dm_reply_whitespace_only_is_blank(tmp_path):
    chat = tmp_path / "chat.jsonl"
    script = _hdr(tmp_path, chat) + 'record_dm_reply "" "   " beat\n'
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _chat_rows(chat)
    assert len(rows) == 1 and rows[0].get("beat_failed") is True, rows


def test_record_dm_reply_nonblank_path_unchanged(tmp_path):
    """The legacy non-blank path stays byte-identical (the engine-log-failure branch here)."""
    chat = tmp_path / "chat.jsonl"
    script = _hdr(tmp_path, chat) + f'record_dm_reply "" {PROSE_1!r} beat\n'
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert _chat_rows(chat) == [{"role": "dm", "text": PROSE_1}]


def test_failure_row_helper_shape_and_consume_once(tmp_path):
    """worldos_chatlog_dm_failed: stamps beat_failed (never fallback_recovered, even when the
    resolve flag was set) and consumes the resolve flags so the NEXT row is unflagged."""
    chat = tmp_path / "chat.jsonl"
    script = (
        _hdr(tmp_path, chat)
        + "WORLDOS_FALLBACK_RECOVERED=1\nWORLDOS_DM_BEAT_FAILED=1\n"
        + "worldos_chatlog_dm_failed\n"
        + f"worldos_chatlog_dm {PROSE_2!r}\n"
        + 'printf "post_failed=%s post_recovered=%s\\n" "$WORLDOS_DM_BEAT_FAILED" "$WORLDOS_FALLBACK_RECOVERED"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _chat_rows(chat)
    assert rows[0].get("beat_failed") is True and "fallback_recovered" not in rows[0], rows
    assert rows[1] == {"role": "dm", "text": PROSE_2}, (
        f"the failure helper must consume the resolve flags: {rows}"
    )
    assert "post_failed=0 post_recovered=0" in r.stdout


def test_failure_text_never_pollutes_engine_memory(tmp_path):
    """The failure beat is chat-only BY DESIGN: nothing may land in the engine session log
    (recap/FTS/lean-tail story memory + the next beat's #357 fallback all read it)."""
    log = _seed_campaign(tmp_path, [PROSE_1])
    before = log.read_text(encoding="utf-8")
    chat = tmp_path / "chat.jsonl"
    r = _bash(_hdr(tmp_path, chat) + 'record_dm_reply "c1" "" beat\n')
    assert r.returncode == 0, r.stderr
    assert log.read_text(encoding="utf-8") == before, (
        "the failure beat must NOT be written to the engine session log"
    )
    assert _chat_rows(chat)[0].get("beat_failed") is True


# ── leg 3: the chatlog overrides are deleted (the lib is the single implementation) ──────────


def test_runner_chatlog_overrides_deleted():
    """F12-7: qa/run_duo.sh:135, qa/ui_playtest.sh:138, qa/run_party.sh:169 each re-defined a
    3-arg chatlog AFTER sourcing the lib, silently discarding worldos_chatlog_dm's honesty
    stamp. The shared lib chatlog (a verified drop-in superset — it reads ambient $CHAT at call
    time and writes a byte-identical row with no 3rd arg) must be the ONLY implementation."""
    override = re.compile(r"(?m)^\s*(function\s+)?chatlog\s*\(\)")
    for rel in QA_RUNNERS:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "lib_beat_driver.sh" in src, f"{rel} must source the shared lib"
        assert not override.search(src), (
            f"{rel} re-defines chatlog() after sourcing the lib — the override shadows the "
            f"shared 3-arg chatlog and kills the fallback_recovered/beat_failed stamps"
        )
        assert "worldos_chatlog_dm" in src, f"{rel} must write dm rows via the shared helper"


def test_dm_wrappers_classify_and_mark():
    """Every DM-driving wrapper routes its final-text extraction through the shared
    classification front door and takes the pre-beat mark (once per beat, before attempt 1)."""
    for rel in ALL_DM_WRAPPERS:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "worldos_dm_final_text" in src, f"{rel} must extract via worldos_dm_final_text"
        assert "worldos_dm_prebeat_mark" in src, f"{rel} must take the pre-beat log-tail mark"
    for rel in QA_RUNNERS:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "worldos_chatlog_dm_failed" in src, (
            f"{rel} must record the visible failure beat on a failed/blank DM beat"
        )


def test_lib_chatlog_three_arg_contract_for_runners(tmp_path):
    """The lib chatlog the runners now inherit: 2-arg rows byte-identical to the old override;
    3-arg rows merge the extra JSON (the stamp path the overrides were killing)."""
    chat = tmp_path / "chat.jsonl"
    script = (
        _hdr(tmp_path, chat)
        + f"chatlog player {PROSE_1!r}\n"
        + "WORLDOS_FALLBACK_RECOVERED=1\n"
        + f"worldos_chatlog_dm {PROSE_2!r}\n"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _chat_rows(chat)
    assert rows[0] == {"role": "player", "text": PROSE_1}
    assert rows[1] == {"role": "dm", "text": PROSE_2, "fallback_recovered": True}


# ── leg 3: the assert_behavioral consumer (count + report; no gate flip) ─────────────────────


def _minimal_green_run(tmp_path) -> tuple[Path, Path]:
    run = tmp_path / "run.jsonl"
    run.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "The scene unfolds."},
                        {"type": "tool_use", "name": "mcp__worldos-engine__roll",
                         "id": "t1", "input": {}},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"characters": {"p1": {"kind": "player"}}, "party": ["p1"]}),
        encoding="utf-8",
    )
    return run, state


def test_assert_behavioral_counts_and_reports_stamps_without_gating(tmp_path):
    """The fallback_recovered/beat_failed consumer: counted + reported on every gate run, but
    NEVER flips RED by itself (the discount/gate policy stays #757's call)."""
    run, state = _minimal_green_run(tmp_path)
    chat = tmp_path / "chat.jsonl"
    chat.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"role": "player", "text": "[say] hello"},
                {"role": "dm", "text": "A fine evening."},
                {"role": "dm", "text": "(The tale falters...)", "beat_failed": True},
                {"role": "dm", "text": PROSE_1, "fallback_recovered": True},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "qa" / "assert_behavioral.py"),
         str(run), str(state), str(chat)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, f"the honesty counters must not flip the gate: {r.stdout}\n{r.stderr}"
    assert "dm_beat_honesty" in r.stdout
    assert "beats_failed=1" in r.stdout, r.stdout
    assert "fallback_recovered=1" in r.stdout, r.stdout
    assert "[WARN] dm_beat_honesty" in r.stdout, "counts surface as a WARN, never a FAIL"


def test_assert_behavioral_honesty_passes_clean_run(tmp_path):
    run, state = _minimal_green_run(tmp_path)
    chat = tmp_path / "chat.jsonl"
    chat.write_text(
        json.dumps({"role": "player", "text": "[say] hi"}) + "\n"
        + json.dumps({"role": "dm", "text": "Welcome."}) + "\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "qa" / "assert_behavioral.py"),
         str(run), str(state), str(chat)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, r.stdout
    assert "[PASS] dm_beat_honesty" in r.stdout, r.stdout


# ── hygiene: every touched script stays /bin/bash -n clean (macOS bash 3.2) ──────────────────


@pytest.mark.parametrize("rel", list(ALL_DM_WRAPPERS) + ["qa/lib_beat_driver.sh"])
def test_touched_scripts_parse_under_bin_bash(rel):
    r = subprocess.run(["/bin/bash", "-n", str(REPO_ROOT / rel)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"{rel} failed bash -n: {r.stderr}"
