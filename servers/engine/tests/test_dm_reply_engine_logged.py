"""Regression: the cold-open DM narration must NOT appear twice in the OpenWorlds chronicle.

Root cause this guards (issue #720, RRI 2026-06-09): the opening prose lands in TWO
viewer-read sources — the engine per-session log (per-paragraph, fed to ``/events``) AND
``chat.jsonl`` (the whole opening as one blob, written by the play wrappers' ``chatlog``
with NO ``engine_logged`` flag). The client's mid-session de-dup
(``eventsStreamedThisTurnRef``, ``viewer/openworlds/app.jsx``) holds mid-session but does
NOT guard the cold-open blob (the opening is already complete pre-mount), so the opening
renders twice.

The CLIENT side is already done + tested: ``app.jsx`` does ``if (it.engine_logged === true)
return null;`` and ``viewer/tests/test_live_narration_stream.py
::test_engine_logged_chat_reply_resolves_without_rendering_duplicate`` passes. This file
guards the WRAPPER side — the proven Codex idiom (``scripts/play_codex_dm.sh``'s 3-arg
``chatlog`` + ``log_engine_narration`` + ``record_dm_reply``) ported into the shared
``qa/lib_beat_driver.sh`` so BOTH ``scripts/play.sh`` and ``scripts/play_party.sh`` stamp
``{"engine_logged": true}`` on a DM reply IFF that prose was also logged to the engine
session log (so the client can de-dup it). On engine-log FAILURE the row is written WITHOUT
the flag (byte-identical to the pre-fix behavior; the ``eventsStreamedThisTurnRef`` backstop
still applies). The flag is NEVER stamped unconditionally — that would suppress a
legitimately /chat-only beat to zero rendered rows.

These tests are gateway-free and exercise the REAL bash helpers under ``/bin/bash`` (macOS
system bash is 3.2; the helpers are 3.2-clean, so this also guards 3.2 compatibility). The
SUCCESS path seeds a REAL campaign via the engine (no live LLM) so ``record_dm_reply`` runs
its genuine ``log_event`` success branch. Discovered + run in CI via ``servers/engine``
pytest.
"""

import json
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


def _rows(chat_path: Path) -> list:
    return [
        json.loads(line)
        for line in chat_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- behavioral: the 3-arg chatlog ------------------------------------------------------------


def test_chatlog_three_arg_merges_extra_json(tmp_path):
    """The 3rd arg (an extra-JSON object) is merged into the row alongside role+text."""
    chat = tmp_path / "chat.jsonl"
    script = (
        f'set -u; CHAT="{chat}"; . "{LIB}"\n'
        "chatlog dm 'the opening scene' '{\"engine_logged\":true}'\n"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _rows(chat)
    assert len(rows) == 1, rows
    assert rows[0] == {"role": "dm", "text": "the opening scene", "engine_logged": True}


def test_chatlog_two_arg_is_byte_identical_to_legacy(tmp_path):
    """No 3rd arg -> exactly {role,text} (no stray keys); the legacy fallback row shape."""
    chat = tmp_path / "chat.jsonl"
    script = f'set -u; CHAT="{chat}"; . "{LIB}"\nchatlog player "I draw my blade"\n'
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _rows(chat)
    assert rows == [{"role": "player", "text": "I draw my blade"}]


def test_chatlog_empty_extra_arg_is_treated_as_legacy(tmp_path):
    """An empty 3rd arg (the FAILURE-path call shape) must not add keys / must not error."""
    chat = tmp_path / "chat.jsonl"
    script = f'set -u; CHAT="{chat}"; . "{LIB}"\nchatlog dm "terse beat" ""\n'
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert _rows(chat) == [{"role": "dm", "text": "terse beat"}]


# --- behavioral: record_dm_reply (the truthfulness guard) -------------------------------------


def _record_dm_reply_script(state_dir: Path, chat: Path, campaign_id: str, text: str, phase: str):
    # ROOT + STATE_DIR + CHAT are the ambient globals the ported helpers read (exactly as
    # play_codex_dm.sh's versions read $ROOT/$RUN_DIR/$CHAT). uv resolves the engine venv.
    return (
        f'set -u; ROOT="{ROOT}"; STATE_DIR="{state_dir}"; CHAT="{chat}"; . "{LIB}"\n'
        f"record_dm_reply {campaign_id!r} {text!r} {phase}\n"
    )


def test_record_dm_reply_success_stamps_engine_logged_and_logs_to_engine(tmp_path, monkeypatch):
    """SUCCESS path: a real campaign exists -> the narration is appended to the engine session
    log AND the dm chat row carries engine_logged:true (so the client de-dups the blob)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server  # engine tools as plain functions; state dir from CLAWDND_STATE_DIR

    campaign_id = server.start_world("sundered-reach")["campaign_id"]

    chat = tmp_path / "chat.jsonl"
    prose = "You stand at the gate as rain hisses on the cobbles."
    r = _bash(_record_dm_reply_script(tmp_path, chat, campaign_id, prose, "opening"))
    assert r.returncode == 0, r.stderr

    # 1) the dm chat row got the flag.
    rows = _rows(chat)
    assert rows == [{"role": "dm", "text": prose, "engine_logged": True}], rows

    # 2) the engine session log actually received the narration (the success precondition for
    #    stamping the flag — the client trusts the flag to mean "this prose is also in /events").
    sessions_dir = tmp_path / "campaigns" / campaign_id / "sessions"
    logged = "\n".join(
        p.read_text(encoding="utf-8") for p in sessions_dir.glob("*.jsonl")
    )
    assert prose in logged, "record_dm_reply SUCCESS must log the narration to the engine"
    assert '"narration"' in logged, "the engine entry must be a narration kind"


def test_record_dm_reply_failure_writes_row_without_flag(tmp_path):
    """FAILURE path: a blank/whitespace campaign id is rejected by log_engine_narration -> the
    dm row is written WITHOUT the flag (byte-identical fallback; no live LLM/engine needed)."""
    chat = tmp_path / "chat.jsonl"
    prose = "A terse beat the engine could not record."
    # A whitespace-only campaign id -> log_engine_narration returns 1 -> fallback chatlog.
    r = _bash(_record_dm_reply_script(tmp_path, chat, "   ", prose, "beat"))
    assert r.returncode == 0, r.stderr
    rows = _rows(chat)
    assert rows == [{"role": "dm", "text": prose}], rows
    assert "engine_logged" not in rows[0], "FAILURE path must NOT stamp the flag"


def test_log_engine_narration_rejects_blank_inputs(tmp_path):
    """The guard: a blank campaign id OR blank text returns non-zero WITHOUT touching the
    engine (this is what drives record_dm_reply's fallback branch)."""
    script = (
        f'set -u; ROOT="{ROOT}"; STATE_DIR="{tmp_path}"; . "{LIB}"\n'
        'log_engine_narration "" "some prose"; echo "blank_cid=$?"\n'
        'log_engine_narration "cid" "   "; echo "blank_text=$?"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "blank_cid=1" in r.stdout, r.stdout
    assert "blank_text=1" in r.stdout, r.stdout


# --- static contract: anti-drift across both viewer-backed wrappers ---------------------------


def test_shared_helpers_live_in_lib_beat_driver():
    """The 3 ported helpers live ONCE in the shared lib (DRY, mirroring
    worldos_dm_remint_session_on_retry) and stamp the flag only on the success branch."""
    lib = _src("qa/lib_beat_driver.sh")
    assert "log_engine_narration()" in lib
    assert "record_dm_reply()" in lib
    # the 3-arg chatlog (optional extra_json merged into the row) lives in the lib too.
    assert "chatlog()" in lib
    assert '"engine_logged":true' in lib, "success branch must stamp the flag"
    # the flag is conditional on engine-log success (truthfulness guard), never unconditional.
    assert "if log_engine_narration" in lib


def test_both_viewer_wrappers_route_dm_replies_through_record_dm_reply():
    """play.sh + play_party.sh (the two CLAUDE-DM viewer-backed wrappers) must route their
    DM-reply writes through the shared record_dm_reply rather than a bare `chatlog dm`."""
    play, party = _src("scripts/play.sh"), _src("scripts/play_party.sh")
    for name, src in (("play.sh", play), ("play_party.sh", party)):
        assert "record_dm_reply" in src, name
    # play.sh: the opening + per-move DM writes route through record_dm_reply.
    assert "record_dm_reply \"$CAMPAIGN_ID\" \"$DMSG\" opening" in play
    assert "record_dm_reply \"$CAMPAIGN_ID\" \"$DMSG\" beat" in play
    # play.sh must NOT keep a bare `chatlog dm` for the DM reply (it would re-introduce the dup).
    assert "chatlog dm " not in play, "play.sh must route all DM replies through record_dm_reply"
    # play_party.sh: the opening routes through record_dm_reply (the cold-open dup source).
    assert "record_dm_reply \"$CAMPAIGN_ID\" \"$DMSG\" opening" in party
    assert "chatlog dm " not in party, "play_party.sh must route all DM replies through record_dm_reply"


def test_non_dm_chat_rows_are_left_untouched():
    """Player + companion rows must still be plain `chatlog ...` calls (NOT routed through
    record_dm_reply, which would wrongly engine-log a player line as DM narration)."""
    play, party = _src("scripts/play.sh"), _src("scripts/play_party.sh")
    assert "chatlog player " in play, "play.sh player row must stay a plain chatlog call"
    assert "chatlog player " in party, "play_party.sh player row must stay a plain chatlog call"
    assert 'chatlog "companion:' in party, "play_party.sh companion rows must stay plain chatlog"


# --- #720 IDEMPOTENCY (adversarial-review fix) -------------------------------------------------
# The CLAUDE DM (the release path) frequently logs the opening/beat narration to the engine
# session log DURING its turn (confirmed across real VM runs). An UNCONDITIONAL re-log in
# record_dm_reply would then put the prose in the log TWICE → a SECOND /events row (the viewer
# keys /events by line-index seq, not by text) → the duplicate is RELOCATED (/events-vs-/events),
# not fixed. So log_engine_narration appends ONLY when the prose is not already in the recent
# session-log narration, but ALWAYS returns success so the flag is stamped: the prose ends up in
# the engine log EXACTLY ONCE and the redundant /chat blob is dropped → rendered once.

def _narration_texts(state_dir: Path, campaign_id: str) -> list:
    out = []
    sdir = state_dir / "campaigns" / campaign_id / "sessions"
    for p in sorted(sdir.glob("*.jsonl")) if sdir.is_dir() else []:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("kind") == "narration":
                out.append(e.get("text", ""))
    return out


def test_record_dm_reply_does_not_double_log_when_dm_already_logged(tmp_path, monkeypatch):
    """The CLAUDE-path bug: the DM already logged the opening this turn. record_dm_reply must NOT
    append a SECOND copy (which would render twice in /events) — it detects the prose is present,
    skips the append, and STILL stamps engine_logged so the /chat blob is dropped → single render."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    campaign_id = server.start_world("sundered-reach")["campaign_id"]
    opening = "You stand at the gate as rain hisses on the cobbles and a guard eyes your blade."
    server.log_event(campaign_id, "narration", opening)  # the DM logs it during its own turn

    def n_opening():
        return sum(1 for t in _narration_texts(tmp_path, campaign_id) if t == opening)

    assert n_opening() == 1, "precondition: the DM logged the opening exactly once"
    chat = tmp_path / "chat.jsonl"
    r = _bash(_record_dm_reply_script(tmp_path, chat, campaign_id, opening, "opening"))
    assert r.returncode == 0, r.stderr
    assert n_opening() == 1, "record_dm_reply must NOT double-log the already-logged opening"
    assert _rows(chat) == [{"role": "dm", "text": opening, "engine_logged": True}], _rows(chat)


def test_record_dm_reply_idempotent_across_per_paragraph_logging(tmp_path, monkeypatch):
    """The DM may log the opening as SEPARATE paragraph beats while record_dm_reply gets the whole
    reply. The whitespace-normalized membership check must still see it as already-logged and skip
    the append (no extra full-blob narration row added)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    campaign_id = server.start_world("sundered-reach")["campaign_id"]
    para1 = "Morning in the Lower City arrives loud."
    para2 = "Temple bells clash with crier calls over cracked cobbles."
    server.log_event(campaign_id, "narration", para1)
    server.log_event(campaign_id, "narration", para2)
    before = _narration_texts(tmp_path, campaign_id)

    chat = tmp_path / "chat.jsonl"
    full = f"{para1}\n\n{para2}"  # the DM's final reply = the paragraphs concatenated (REAL newlines)
    # Pass the reply with REAL newlines via ANSI-C $'...' quoting — exactly as play.sh passes a
    # double-quoted $DMSG (the repr-based shared helper would escape \n to a literal backslash-n and
    # defeat the whitespace normalization that production never hits).
    full_ansi = "$'" + full.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"
    script = (
        f'set -u; ROOT="{ROOT}"; STATE_DIR="{tmp_path}"; CHAT="{chat}"; . "{LIB}"\n'
        f"record_dm_reply {campaign_id!r} {full_ansi} opening\n"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    after = _narration_texts(tmp_path, campaign_id)
    assert after == before, f"per-paragraph already-logged prose must not be re-appended: {after}"
    assert full not in after, "the full-blob copy must NOT be added"
    assert _rows(chat)[0].get("engine_logged") is True


def test_record_dm_reply_appends_canonical_when_not_already_logged(tmp_path, monkeypatch):
    """The CODEX-path / DM-didn't-self-log case: the prose is NOT in the engine log yet, so
    record_dm_reply MUST append it (the canonical /events + recap/memory copy) exactly once,
    then flag — keeping the engine_logged stamp truthful."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    campaign_id = server.start_world("sundered-reach")["campaign_id"]
    prose = "A lantern gutters in the gatehouse; the sergeant waves you through without a word."

    def n_prose():
        return sum(1 for t in _narration_texts(tmp_path, campaign_id) if t == prose)

    assert n_prose() == 0, "precondition: prose not yet in the engine log"
    chat = tmp_path / "chat.jsonl"
    r = _bash(_record_dm_reply_script(tmp_path, chat, campaign_id, prose, "opening"))
    assert r.returncode == 0, r.stderr
    assert n_prose() == 1, "absent prose must be appended exactly once (canonical copy)"
    assert _rows(chat) == [{"role": "dm", "text": prose, "engine_logged": True}]
