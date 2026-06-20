# shellcheck shell=bash
# Shared beat-driver helpers for the WorldOS play loops (qa/run_duo.sh + scripts/play.sh).
#
# This is the STRUCTURE behind the "living, progressing world" fix (decision-dm-driver.md):
# prose nudges alone never moved the clock past day 1, never visited >1 location, and NEVER
# created an on-screen NPC across 57 campaigns. So the harness now drives progression
# structurally, in two pieces — both routing through the ENGINE so it stays the SOLE WRITER
# of snapshot.json (we never hand-edit state):
#
#   C — soft clock-tick backstop (worldos_soft_tick): after a DM beat, if the in-world clock
#       did NOT advance this beat, advance ONE time-of-day phase via the engine's advance_time.
#       Defers to the DM's own pacing when it advances time in-fiction; only fires when frozen.
#
#   A — beat-aware runbook injection (worldos_runbook_for_beat): instead of the SAME constant
#       "keep the world moving" paragraph every beat (noise the model learns to skim), emit ONE
#       moment-specific runbook chosen from the beat index + the live snapshot (location/time/
#       visited/peopling): scene-intro on a new place, travel/peopling when stuck, the midpoint
#       reversal at ~beats/2, the climax/payoff in the final ~2 beats.
#
# Sourced by BOTH loops so the two harnesses can't drift. Pure bash + a tiny `uv run` python
# shim into servers/engine (the engine's venv has the deps; bare python3 does not).

# --- Removable-volume env hygiene (the LEXAR-popup P0) -----------------------------
# Strip inherited env vars whose value points at a removable volume (/Volumes/...) — e.g.
# GBRAIN_SKILLS_DIR=/Volumes/LEXAR/... exported unconditionally by a user's ~/.zshenv. The DM is
# a `claude -p` child of this shell; claude's skills loader reads such a var and enumerates the
# volume → a modal "WorldOS would like to access files on a removable volume" TCC prompt that
# can't be answered headlessly and silently stalls/blocks the QA + dogfood run. The harness needs
# none of them; our OWN roots (WORLDOS_*/WORLDOS_*, which may intentionally live on a /Volumes
# worktree) are preserved. Shell mirror of the native app's
# EnvironmentBootstrap.withoutRemovableVolumeLeaks filter.
for _wos_rmvol_k in $(env | awk -F= '$2 ~ /^\/Volumes\// {print $1}'); do
  # Skip our own roots, and any non-identifier token: a value with an embedded newline can make
  # awk emit a bogus "key", and `unset` on a non-identifier errors — which would abort a future
  # sourcer that runs `set -e`. (Today's sourcers use `set -uo pipefail` only, so this is belt-and-
  # suspenders to keep the shared lib safe regardless of the caller's shell options.)
  case "$_wos_rmvol_k" in WORLDOS_*|WORLDOS_*|""|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
  unset "$_wos_rmvol_k"
done
unset _wos_rmvol_k

# --- WorldOS rename env-compat (issue #295, W0-E) ---------------------------------
# Resolve an env var by suffix, preferring WORLDOS_<suffix> and falling back to the
# legacy WORLDOS_<suffix> (one-time stderr deprecation warning), else a default.
#   worldos_env DM_MODEL sonnet   ->  $WORLDOS_DM_MODEL, else $WORLDOS_DM_MODEL, else "sonnet"
# Mirrors servers/*/_env.py for the shell side; both names resolve for v1.x.
# Note: worldos_env is typically called inside $(...) (a forked subshell), so an
# in-memory "warned" flag wouldn't survive between calls. We key the once-warning off
# a tiny per-(invocation, var) sentinel file under $TMPDIR so it stays one-time across
# the subshells of a single script run (PPID = the script's pid from the subshell).
worldos_env() {
  local suffix="$1" default="${2:-}"
  local w="WORLDOS_${suffix}" c="WORLDOS_${suffix}"
  if [ -n "${!w:-}" ]; then
    printf '%s' "${!w}"
  elif [ -n "${!c:-}" ]; then
    local sentinel="${TMPDIR:-/tmp}/worldos-envwarn.$PPID.$c"
    if [ ! -e "$sentinel" ]; then
      : > "$sentinel" 2>/dev/null || true
      printf '[worldos] DEPRECATION: env var %s is renamed to %s; the old name still works for v1.x but will be removed in v2.0.\n' "$c" "$w" >&2
    fi
    printf '%s' "${!c}"
  else
    printf '%s' "$default"
  fi
}

# Resolve the run's campaign snapshot.json the same way the harnesses pick state for scoring:
# the LARGEST non-empty snapshot under <state_dir>/campaigns (never a blind head -1, which a
# fat-fingered campaign_id could point at a lock-only orphan dir). Echoes the path or nothing.
# $1 = STATE_DIR
#
# CAUTION (issue #640): "largest snapshot" is a SCORING-state heuristic (find the fattest save
# to grade) — it is the WRONG selector for the LEAN RE-GROUND campaign id. When two campaigns
# coexist in one state dir (a cold-open start_world retry, or a stale leftover), the largest may
# be the STALE/PARALLEL one, and pointing a transcript-free lean beat at it folds a DIFFERENT
# save's opening scene into the re-ground (cross-chronicle contamination). For the LEAN id, use
# worldos_live_campaign_id below (the engine's authoritative most-recently-played save), NOT this.
worldos_snapshot_path() {
  local state_dir="$1"
  find "$state_dir/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c \
    -exec ls -S {} + 2>/dev/null | head -1
}

# Resolve the LIVE campaign id for the lean re-ground — the ENGINE-authoritative answer to
# "which save is being played right now", so a fast/transcript-free beat re-grounds against the
# RIGHT campaign and can never fold a parallel save's opening scene into scene_context (#640).
#
# The engine is the sole source of truth for which campaign is live; it returns the
# MOST-RECENTLY-UPDATED campaign (the one the harness is actively writing each beat), optionally
# scoped to the launched world so a stale save from another world can't shadow it. This REPLACES
# the old per-harness guesses (worldos_snapshot_path = largest, or `find … | head -1` = first dir)
# that the #640 A/B proved could select the wrong campaign. Read-only (active_campaign never
# mutates). Echoes the campaign id, or NOTHING when no campaign exists / the engine errors — in
# which case the caller's lean branch no-ops (worldos_dm_lean_args returns 0 on an empty id) and
# the normal --resume path is used (no regression). $1 = ROOT (repo root)  $2 = STATE_DIR
# $3 = world_id (optional; scopes the resolution to the launched world)
worldos_live_campaign_id() {
  local root="$1" state_dir="$2" world="${3:-}"
  [ -d "$state_dir/campaigns" ] || return 0
  WORLDOS_STATE_DIR="$state_dir" \
    uv run --directory "$root/servers/engine" python - "$world" 2>/dev/null <<'PY'
import sys
import store
cid = store.active_campaign_id(sys.argv[1] if len(sys.argv) > 1 else "")
if cid:
    print(cid)
PY
}

# EMPTY-NARRATION FALLBACK (issue #357). The play/QA loops record the DM turn's FINAL reply
# text to the chat panel (chatlog dm "$DMSG"). But a DM turn can end on a tool call (e.g. its
# last act was log_event/roll) or a bare 3rd-person status line, leaving that final `result`
# text EMPTY — so the player-facing chat shows nothing even though the engine work happened and
# the DM logged real 2nd-person prose via log_event(kind="narration"/"dialogue"). That prose
# lands in the engine's per-session log (campaigns/<id>/sessions/<sid>.jsonl) and renders in the
# viewer's `recentEvents`, but never reaches the chat. This recovers it: given the (possibly
# empty) reply text + the run's STATE_DIR, echo the reply unchanged when it has prose, else fall
# back to the most recent player-facing narration the engine logged THIS turn — so every resolved
# beat shows non-empty 2nd-person prose. Read-only on engine state (the engine stays the sole
# writer); a missing snapshot / session log / narration just yields the original text (no
# regression — today's behavior). Mirrors the viewer's session-log resolution
# (viewer/server.py:_session_event_tail_from_dir): active_session_id, else the last session id.
# $1 = the DM turn's reply text (may be empty)  $2 = STATE_DIR ; echoes the text to chatlog.
worldos_dm_narration_or_fallback() {
  local reply="$1" state_dir="$2" snap
  # Non-empty (after trimming whitespace) → the DM ended on prose; use it verbatim.
  if [ -n "${reply//[[:space:]]/}" ]; then
    printf '%s' "$reply"
    return 0
  fi
  snap="$(worldos_snapshot_path "$state_dir")"
  [ -n "$snap" ] || { printf '%s' "$reply"; return 0; }
  # Read the tail of the active session log and stitch the most recent contiguous run of
  # player-facing entries (narration | dialogue) into the prose for this missing beat (see
  # qa/dm_narration_fallback.py). It is a standalone script (NOT a heredoc-in-command-sub: the
  # macOS system bash 3.2 mis-parses a quoted heredoc nested in $(...), which is why every other
  # such call here is one too). Bounded; skips roll/system/combat rows. Empty when no such prose.
  local recovered fb_py="${WORLDOS_LIB_DIR:-$(dirname "${BASH_SOURCE[0]}")}/dm_narration_fallback.py"
  recovered="$(python3 "$fb_py" "$snap" 2>/dev/null)"
  if [ -n "${recovered//[[:space:]]/}" ]; then
    printf '%s' "$recovered"
  else
    printf '%s' "$reply"   # nothing to recover → unchanged (no regression)
  fi
}

# worldos_resolve_dm_reply REPLY STATE_DIR — the DIRECT-call front door over
# worldos_dm_narration_or_fallback (#749c fallback honesty + SYN-01 #757/#745 dead-beat
# classification). Sets:
#   WORLDOS_DM_REPLY            — the resolved reply text (the DM's own, the recovered prose,
#                                 or "" when the beat FAILED)
#   WORLDOS_FALLBACK_RECOVERED  — 1 IFF the #357 fallback recovered GENUINE prose (the DM's
#                                 reply was blank and the engine log supplied prose the DM
#                                 logged THIS beat), else 0
#   WORLDOS_DM_BEAT_FAILED      — 1 IFF the beat FAILED: the final result event was ERROR-class
#                                 (is_error / api_error_status — its "result" text is the API's
#                                 error string, e.g. "Failed to authenticate…", NEVER a reply),
#                                 or the only recoverable prose PREDATES the pre-beat mark
#                                 (recycling the previous beat's prose would mask a dead beat).
# SYN-01 ORDER OF OPERATIONS: the FINAL result event (noted by worldos_dm_final_text in
# $STATE_DIR/.dm_last_result) is classified FIRST — before any fallback — so an error-class
# result can never be chatted as DM prose NOR "recovered" into recycled prose. Every failed
# beat resolves to an EMPTY reply; callers branch on that and record the failure VISIBLY
# (worldos_chatlog_dm_failed, or record_dm_reply's blank guard).
# Call it DIRECTLY (never in a command substitution — a subshell would drop the globals), then
# read WORLDOS_DM_REPLY. The flag is consumed (and reset) by the next record_dm_reply /
# worldos_chatlog_dm, which stamps {"fallback_recovered":true} on the dm chat row so behavioral
# tallies can later discount a masked-dead beat that was "resolved" with recovered prose.
worldos_resolve_dm_reply() {
  local original="$1" state_dir="$2" last=""
  WORLDOS_DM_BEAT_FAILED=0
  WORLDOS_FALLBACK_RECOVERED=0
  # SYN-01 leg 1: parse the FINAL result event FIRST. A 401-class failure carries NON-empty
  # result text, which used to bypass the empty-only retry AND this fallback and land in chat
  # AS DM PROSE. Classify it -> the beat FAILED; never chat the error text.
  last="$(cat "$state_dir/.dm_last_result" 2>/dev/null)"
  if [ -n "$last" ] && [ -f "$last" ] && worldos_dm_result_is_error "$last"; then
    echo "[worldos] DM beat FAILED: error-class result event (see the [dm-attempt] line above) — the error text will NOT be chatted as narration" >&2
    WORLDOS_DM_BEAT_FAILED=1
    WORLDOS_DM_REPLY=""
    return 0
  fi
  WORLDOS_DM_REPLY="$(worldos_dm_narration_or_fallback "$original" "$state_dir")"
  if [ -z "${original//[[:space:]]/}" ] && [ -n "${WORLDOS_DM_REPLY//[[:space:]]/}" ]; then
    # The #357 fallback recovered prose. GENUINE recovery = the DM logged NEW prose THIS beat
    # (then died before its final reply). If everything recoverable PREDATES the pre-beat mark
    # the beat was fully dead and the "recovery" is the PREVIOUS beat's prose — recycling it
    # would mask the dead beat (F12-14), so the beat FAILS instead. No mark file (an older /
    # external caller) keeps the legacy assume-genuine behavior.
    if worldos_dm_logged_new_prose "$state_dir"; then
      WORLDOS_FALLBACK_RECOVERED=1
    else
      echo "[worldos] DM beat FAILED: only recyclable (pre-beat) prose available — refusing to mask a dead beat with the previous beat's narration" >&2
      WORLDOS_DM_BEAT_FAILED=1
      WORLDOS_DM_REPLY=""
    fi
  fi
}

# CHRONICLE WRITE + ENGINE-LOG TRUTHFULNESS GUARD (issue #720 — the ONE shared impl).
#
# The cold-open opening prose lands in TWO viewer-read sources: the engine per-session log
# (per-paragraph, fed to the viewer's /events) AND chat.jsonl (the whole opening as one blob,
# written here by `chatlog`). The OpenWorlds client de-dups mid-session via
# eventsStreamedThisTurnRef, but that backstop assumes "/events lands a turn's paragraphs
# before its /chat blob" — true mid-session, FALSE on cold-open (the opening is complete
# pre-mount). So the opening rendered TWICE. The client already honors an `engine_logged:true`
# marker on a /chat row (viewer/openworlds/app.jsx: `if (it.engine_logged === true) return
# null;`) to drop the blob when its prose is also in /events. These helpers stamp that marker —
# but ONLY when the prose was truly logged to the engine session log, so the client never drops
# a legitimately /chat-only beat (which would render it to zero rows).
#
# Ported verbatim-in-intent from scripts/play_codex_dm.sh (the codex DM path already solved this
# with the same three pieces); factored here ONCE so the two CLAUDE-DM viewer-backed wrappers
# (scripts/play.sh + scripts/play_party.sh) share it and can't drift — mirroring how
# worldos_dm_remint_session_on_retry is shared. These read the caller's ambient globals (exactly
# as the codex versions read $CHAT/$RUN_DIR/$ROOT): $CHAT (the chat.jsonl path), $STATE_DIR (the
# play state dir), and $ROOT (the repo root). Both wrappers define all three before sourcing.
#
# BASH 3.2 SAFETY: chatlog + log_engine_narration carry quoted heredocs, which macOS bash 3.2
# mis-parses when nested inside $(...). They are ALWAYS called DIRECTLY (never in a command
# substitution) — keep it that way.

# chatlog ROLE TEXT [EXTRA_JSON] — append one row to $CHAT. The optional 3rd arg is a JSON
# object merged into the {role,text} row (e.g. '{"engine_logged":true}'). Empty/absent → a
# plain {role,text} row, byte-identical to the pre-#720 one-liner.
chatlog() {
  python3 - "$CHAT" "$1" "$2" "${3:-}" <<'PY'
import json
import sys

path, role, text, extra_json = sys.argv[1:]
row = {"role": role, "text": text}
if extra_json:
    try:
        extra = json.loads(extra_json)
    except ValueError as exc:
        raise SystemExit(f"invalid chatlog extra_json: {exc}") from exc
    if not isinstance(extra, dict):
        raise SystemExit(f"invalid chatlog extra_json: expected object, got {type(extra).__name__}")
    row.update(extra)
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row) + "\n")
PY
}

# log_engine_narration CAMPAIGN_ID TEXT — ensure the reply is in the engine's session log as a
# `narration` beat (the /events + recap/memory source), then return 0 so record_dm_reply stamps
# engine_logged. Returns non-zero (WITHOUT touching the engine) on a blank campaign id or blank
# text, OR if the engine call fails — that is the signal record_dm_reply uses to fall back to an
# unflagged chat row.
#
# IDEMPOTENT (#720, adversarial-review fix): the CLAUDE DM often ALREADY logs the opening/beat
# narration to the session log DURING its turn. An unconditional append would put the prose in
# the log TWICE → a SECOND /events row (the viewer keys /events by line-index seq, not by text)
# → the duplicate would be RELOCATED (/events-vs-/events), not fixed. So we append ONLY when the
# prose is not already in the recent session-log narration. Either way the prose ends up in the
# engine log EXACTLY ONCE and we return 0 → the redundant /chat blob is dropped → rendered once.
# The CODEX DM (which does not self-log narration) still gets the canonical append. Whitespace-
# normalized substring match covers both the single-blob and per-paragraph logging shapes.
log_engine_narration() {
  local campaign_id="$1" text="$2"
  [ -n "${campaign_id//[[:space:]]/}" ] || return 1
  [ -n "${text//[[:space:]]/}" ] || return 1
  WORLDOS_STATE_DIR="$STATE_DIR" \
    uv run --directory "$ROOT/servers/engine" python - "$campaign_id" "$text" <<'PY'
import glob
import json
import os
import sys

import server
import wrapper_progress

campaign_id, text = sys.argv[1], sys.argv[2]
norm = " ".join(text.split())

already = False
try:
    state = os.environ.get("WORLDOS_STATE_DIR") or "."
    files = sorted(
        glob.glob(os.path.join(state, "campaigns", campaign_id, "sessions", "*.jsonl")),
        key=os.path.getmtime,
    )
    if files:
        recent = []
        with open(files[-1], encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    entry = json.loads(ln)
                except ValueError:
                    continue
                if entry.get("kind") == "narration":
                    recent.append(" ".join((entry.get("text") or "").split()))
        blob = " ".join(recent[-8:])
        if norm and norm in blob:
            already = True  # the DM already logged this prose this turn — do NOT double-log
except Exception:
    already = False  # any read failure → fall through to a normal append (no regression)

# #749(d): the wrapper progress heartbeat legitimately REPEATS — the 4-line rotation means
# beat N+4 re-emits beat N's exact text, and a run of DEAD beats logs ONLY heartbeats, so the
# repeat always sits inside the last-8 tail scanned above. It is a liveness signal, never
# duplicated prose: dedup-dropping it silently stops the player's spinner from flipping on
# cadence-aligned beats (exactly when the heartbeat is the only life sign). The extra rows are
# inert — they never render (app.jsx flips progress and returns null) and every engine memory
# consumer exact-match filters them (recap / FTS ledger / lean tail / narration fallback).
if wrapper_progress.is_wrapper_progress_line(text):
    already = False

if not already:
    server.log_event(campaign_id, "narration", text)
PY
}

# record_dm_reply CAMPAIGN_ID TEXT PHASE — write a DM reply to the chronicle, stamping
# engine_logged:true IFF the prose was also logged to the engine session log (so the client can
# de-dup the /chat blob against /events). On failure → an UNFLAGGED row (byte-identical to the
# pre-#720 behavior; the client's eventsStreamedThisTurnRef backstop still applies). NEVER stamp
# the flag unconditionally — that would suppress a legitimately /chat-only beat to zero rows.
# #749(c): when the preceding worldos_resolve_dm_reply recovered this prose from the engine log
# (WORLDOS_FALLBACK_RECOVERED=1), BOTH branches additionally stamp fallback_recovered:true so
# behavioral tallies can discount masked-dead beats. Consume-once: the flag resets here.
record_dm_reply() {
  local campaign_id="$1" text="$2" phase="$3" extra='{"engine_logged":true}' plain_extra=''
  # SYN-01 (#757 leg 2): NEVER write a blank dm row. A dead beat that recovered nothing used to
  # land an unflagged EMPTY chat row (the post-#763 play.sh mode) — invisible to the player AND
  # the tallies. Record the wrapper-authored VISIBLE failure beat instead, and warn.
  if [ -z "${text//[[:space:]]/}" ]; then
    echo "[worldos] warning: ${phase} produced NO narration (dead beat) — recording a visible failure beat instead of a blank row" >&2
    worldos_chatlog_dm_failed
    return 0
  fi
  if [ "${WORLDOS_FALLBACK_RECOVERED:-0}" = "1" ]; then
    extra='{"engine_logged":true,"fallback_recovered":true}'
    plain_extra='{"fallback_recovered":true}'
  fi
  if log_engine_narration "$campaign_id" "$text"; then
    chatlog dm "$text" "$extra"
  else
    echo "[worldos] warning: could not record ${phase} narration through engine — chat row written without engine_logged" >&2
    chatlog dm "$text" "$plain_extra"
  fi
  WORLDOS_FALLBACK_RECOVERED=0
}

# worldos_chatlog_dm TEXT — `chatlog dm TEXT` for the runners that write the dm row directly
# (run_duo / run_party / ui_playtest), stamping {"fallback_recovered":true} when the preceding
# worldos_resolve_dm_reply recovered the prose (#749c). Consume-once, mirroring record_dm_reply.
# With the flag unset the row is byte-identical to the plain `chatlog dm` it replaces.
worldos_chatlog_dm() {
  if [ "${WORLDOS_FALLBACK_RECOVERED:-0}" = "1" ]; then
    chatlog dm "$1" '{"fallback_recovered":true}'
  else
    chatlog dm "$1"
  fi
  WORLDOS_FALLBACK_RECOVERED=0
}

# ── SYN-01 (#757/#745): dead-beat failure classification — the honesty layer ────────────────
# ~10.5% of DM invocations produce no usable beat (28 no-result + 3x401 of 294 archived files;
# audit 2026-06-11). Three masks made them look "resolved":
#   (a) a 401-class failure's NON-empty error text bypassed the empty-only retry + the #357
#       fallback gate and was chatlogged AS DM PROSE;
#   (b) a fully-dead beat either recycled the PREVIOUS beat's prose into a hidden row, or wrote
#       an unflagged EMPTY dm row — either way the player saw nothing while the harness counted
#       a resolved turn;
#   (c) the fallback_recovered honesty stamp was dead in every QA runner (local chatlog
#       overrides shadowed the lib) and had no consumer.
# These helpers close (a)+(b); (c) is closed by deleting the runner overrides + the
# qa/assert_behavioral.py dm_beat_honesty counter. Flow per beat:
#   worldos_dm_prebeat_mark  (once, BEFORE attempt 1)
#   worldos_dm_final_text    (per attempt — notes the result file + classifies error-class)
#   worldos_resolve_dm_reply (classification first; failed beats resolve to an EMPTY reply)
#   worldos_chatlog_dm_failed / record_dm_reply's blank guard (the VISIBLE failure row)

# The wrapper-authored, PLAYER-VISIBLE failure beat. Chat-only BY DESIGN — deliberately NOT
# routed through log_engine_narration: (1) the #727 substring dedup would swallow the constant
# text on a repeat failure, and an engine_logged stamp would then HIDE the row (app.jsx drops
# engine_logged rows in favor of /events, where the deduped repeat never lands); (2) a failure
# line in the session log would pollute recap/FTS/lean-tail story memory (#763 decontamination)
# and could itself be "recovered" by the NEXT beat's #357 fallback. A plain /chat row renders
# unconditionally in every consumer — visible, exactly once per failure.
WORLDOS_DM_FAILED_BEAT_TEXT="(The tale falters — the Dungeon Master could not resolve this beat. Your last action still stands; give it a moment and try again.)"

# worldos_dm_result_is_error OUT — is the FINAL result event of a DM attempt's stream-json an
# ERROR-class result? The 401 shape (verified verbatim) is subtype:"success", is_error:true,
# api_error_status:401 with the API's error string in .result — so the test is is_error OR an
# api_error_status, NEVER the subtype or the text. A missing/empty file or a stream with no
# result event is NOT error-class (the empty-reply path owns those modes). 0 = error-class.
worldos_dm_result_is_error() {
  local out="$1" flag
  [ -n "$out" ] && [ -s "$out" ] || return 1
  flag="$(jq -rs 'map(select(.type=="result"))[-1] | if . == null then "none" else (((.is_error == true) or ((.api_error_status // null) != null)) | tostring) end' "$out" 2>/dev/null)"
  case "$flag" in
    true) return 0 ;;
    false | none) return 1 ;;
  esac
  # jq unavailable/unparseable -> the same conservative grep-fallback discipline as
  # worldos_report_attempt_failure: only the explicit error markers flip it.
  grep -q '"is_error"[[:space:]]*:[[:space:]]*true' "$out" 2>/dev/null && return 0
  grep -qE '"api_error_status"[[:space:]]*:[[:space:]]*[0-9]+' "$out" 2>/dev/null && return 0
  return 1
}

# worldos_dm_final_text OUT STATE_DIR [RC] — the ONE extraction front door for a DM attempt's
# reply text (replaces the bare `jq -rs '… .result // ""'` in every DM wrapper). Notes OUT in
# $STATE_DIR/.dm_last_result (a FILE, because the turn helpers run inside $(...) subshells
# where a global cannot escape) so the caller's worldos_resolve_dm_reply can classify the SAME
# final result event — then echoes the final result text, UNLESS the event is ERROR-class:
# then it echoes NOTHING (the "result" text is the API's error string, never a reply) and
# surfaces the real failure + the 401/403 re-auth hint via worldos_report_attempt_failure.
# The empty echo also makes the callers' existing empty-only retries fire on error results.
worldos_dm_final_text() {
  local out="$1" state_dir="$2" rc="${3:-0}"
  printf '%s\n' "$out" > "$state_dir/.dm_last_result" 2>/dev/null || true
  # Persist the attempt's exit code too (the turn helpers run inside $(...) subshells, so the
  # caller's transient-vs-real retry classifier can't see a local rc) — paired with .dm_last_result
  # so worldos_dm_failure_is_transient gets the SAME (out, rc) this attempt produced.
  printf '%s\n' "$rc" > "$state_dir/.dm_last_rc" 2>/dev/null || true
  if worldos_dm_result_is_error "$out"; then
    worldos_report_attempt_failure "$out" "$rc"
    return 0
  fi
  jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
}

# worldos_dm_prebeat_mark STATE_DIR — snapshot the active session log's position BEFORE a DM
# beat launches (file: $STATE_DIR/.dm_prebeat_mark), so worldos_dm_logged_new_prose can tell a
# GENUINE #357 recovery (NEW prose logged THIS beat, then the turn died) from RECYCLED prose
# (everything recoverable predates the beat — a masked dead beat). Call it ONCE per beat,
# BEFORE attempt 1: a retry must NOT re-mark, or attempt 1's prose would stop counting as this
# beat's. Best-effort (never fails a beat); standalone python — no heredoc-in-$() (bash 3.2).
worldos_dm_prebeat_mark() {
  local state_dir="$1"
  # FIX 2(a) (#623): pass the beat's first/cold-open signal ("1"=cold open, "0"=continuing) so
  # dm_beat_mark.py can force-fail a CONTINUING beat whose mark came back empty (a mark-write bug
  # that else recycles the previous beat's prose). Default "1" (treat as cold open / fail-open)
  # when the caller did not pass it — never tightens an unknown caller's behavior.
  local first="${2:-1}"
  local mark_py="${WORLDOS_LIB_DIR:-$(dirname "${BASH_SOURCE[0]}")}/dm_beat_mark.py"
  # Drop the 2>/dev/null swallow (FIX 2(a)): an empty/failed mark must surface on stderr so a
  # mark-write bug is visible in the beat log instead of silently producing a recycled beat.
  python3 "$mark_py" mark "$state_dir" "$state_dir/.dm_prebeat_mark" "$first" || true
  return 0
}

# worldos_dm_logged_new_prose STATE_DIR — did the DM log NEW player-facing prose (narration |
# dialogue; wrapper heartbeats + setup-brief notation excluded, exactly as the #357 fallback
# filters) since the pre-beat mark? 0 = yes (a recovery is GENUINE); 1 = no (anything the
# fallback recovered is RECYCLED pre-beat prose). NO mark file -> 0: an older/external caller
# keeps the legacy assume-genuine behavior; dm_beat_mark.py also fails OPEN internally.
worldos_dm_logged_new_prose() {
  local state_dir="$1"
  local mark="$state_dir/.dm_prebeat_mark"
  [ -f "$mark" ] || return 0
  local mark_py="${WORLDOS_LIB_DIR:-$(dirname "${BASH_SOURCE[0]}")}/dm_beat_mark.py"
  python3 "$mark_py" check "$state_dir" "$mark" 2>/dev/null
}

# worldos_chatlog_dm_failed — record the wrapper-authored VISIBLE failure beat for a FAILED DM
# beat: ONE /chat dm row carrying {"beat_failed":true} (counted + reported by
# qa/assert_behavioral.py's dm_beat_honesty; the discount/gate policy stays #757's call). The
# row text is WORLDOS_DM_FAILED_BEAT_TEXT — never an error string, never recycled prose, never
# blank, never hidden (no engine_logged stamp — see the constant's comment). Consume-once on
# the resolve flags, mirroring worldos_chatlog_dm. Reads ambient $CHAT exactly as chatlog does.
worldos_chatlog_dm_failed() {
  WORLDOS_DM_BEATS_FAILED=$((${WORLDOS_DM_BEATS_FAILED:-0} + 1))
  echo "[worldos] beat FAILED — visible failure beat recorded (beats_failed=$WORLDOS_DM_BEATS_FAILED this run)" >&2
  chatlog dm "$WORLDOS_DM_FAILED_BEAT_TEXT" '{"beat_failed":true}'
  WORLDOS_FALLBACK_RECOVERED=0
  WORLDOS_DM_BEAT_FAILED=0
}

# LIVE-PROGRESS + WRAPPER HEARTBEAT (#623 — the ONE shared implementation of the perceived-latency fix).
#
# The bug #623 ("beat silently DROPPED / HUNG >10min, no recovery") was a PERCEIVED-latency defect, not
# real DM unreliability: forensics on the filing run showed all beats ran cleanly at ttft 2-5s / 85-157s
# wall with ZERO timeouts/retries/empty-fallbacks. The defect is that /events stayed BLANK for the whole
# beat → the OpenWorlds viewer's notePendingProgress streaming-flip never fired → the player stared at a
# static "weaving the next beat" spinner and called a healthy 157s beat a "drop"/"hang". Two layers close
# the gap (BOTH perceived-latency — neither touches wall-clock; the bounded timeout+retry+#357 fallback
# are unchanged):
#
#   1. WRAPPER HEARTBEAT (model-INDEPENDENT, the guarantee): worldos_emit_progress_heartbeat writes a
#      short wrapper-authored `narration` row to the engine session log via log_engine_narration BEFORE
#      the DM `claude -p` even starts. That row lands in /events within ~1s, so the viewer flips its
#      spinner to "the scene is arriving above" no matter how long the model thinks — and crucially, even
#      when the model SKIPS the cooperative early log_event (Eva measured exactly that: a run with the rule
#      present but streaming refs = 0). This is the same proven pattern the Codex DM wrapper already uses
#      (scripts/play_codex_dm.sh: OPENING_PROGRESS_TEXT / MOVE_PROGRESS_TEXTS), factored here so every
#      harness shares it. The player is NEVER staring at nothing.
#
#   2. WORLDOS_LIVE_PROGRESS_RULE (model-COOPERATIVE, the richer signal): prepended to the DM beat prompt
#      so the model ALSO logs an early in-voice progress beat. Was already in scripts/play_party.sh +
#      scripts/play_codex_dm.sh but MISSING from scripts/play.sh (the SOLO path that filed #623) — that
#      absence is the bug. Factored here verbatim so the three harnesses can never drift again.
#
# The heartbeat is best-effort: a blank campaign id or an engine error no-ops (return 0) — it is a
# perceived-latency nicety, never allowed to fail a beat. The engine stays the SOLE WRITER (this only
# routes through log_engine_narration, which appends a narration event exactly as the DM's own would).
WORLDOS_LIVE_PROGRESS_RULE="Live progress rule: after you know the live campaign and scene, call log_event(kind=\"narration\", text=\"...\") ONCE with a short, non-duplicate, player-facing progress beat BEFORE any longer resolution work. This is how /events shows visible story progress while your turn is still running. The progress beat MUST be 2nd-person prose addressed to \"you\" (a vivid one-line teaser of where the player stands or what they sense) — it is rendered STRAIGHT into the player's Chronicle. NEVER log a 3rd-person scene summary, a \"Cold open —\"/\"Scene:\"/\"Setup:\" header, a \"Choice: X or Y\" branch list, bracketed stage directions, or any director/planning note: that scaffolding is your private scratchpad and shatters immersion if it reaches the player. Keep the final reply as the full 2nd-person scene; do not copy this progress beat verbatim, because the wrapper records the final reply through the engine after the turn."

# The wrapper-authored heartbeat texts. SHORT 2nd-person teasers (they render straight into the player's
# Chronicle). The cold open (first=1) gets the opening teaser; continuing beats (first=0) ROTATE by index
# so a multi-beat session never repeats the same line. Mirrors play_codex_dm.sh's two text banks.
WORLDOS_OPENING_PROGRESS_TEXT="The first scene gathers around you; voices, risks, and choices come into focus."
WORLDOS_MOVE_PROGRESS_TEXTS=(
  "Your choice takes hold; nearby voices, risks, and consequences begin to answer."
  "The world turns with your action; the scene shifts toward its answer."
  "Your move lands; attention gathers around what changes next."
  "Momentum carries through the scene; consequences are beginning to surface."
)

# worldos_progress_beat_text FIRST BEAT_INDEX — echo the heartbeat teaser for this beat. $1=first?(1/0)
# (the same cold-open signal as the effort/timeout tiers); $2=a 0-based beat index used to ROTATE the
# continuing-beat bank. Cold open → the opening teaser; continuing beat → MOVE_PROGRESS_TEXTS[idx % N].
worldos_progress_beat_text() {
  local first="$1" idx="${2:-0}"
  if [ "$first" != "0" ]; then
    printf '%s' "$WORLDOS_OPENING_PROGRESS_TEXT"
    return 0
  fi
  [[ "$idx" =~ ^[0-9]+$ ]] || idx=0
  local count="${#WORLDOS_MOVE_PROGRESS_TEXTS[@]}"
  printf '%s' "${WORLDOS_MOVE_PROGRESS_TEXTS[$((idx % count))]}"
}

# worldos_emit_progress_heartbeat CAMPAIGN_ID FIRST BEAT_INDEX — write the wrapper-authored progress beat
# to the engine session log (via log_engine_narration) so /events has a row for the viewer to flip its
# live-progress state on BEFORE the model's long think (the row itself never renders — app.jsx returns
# null on the exact wrapper lines). Best-effort: a blank campaign id no-ops. #749(d): heartbeats are
# EXEMPT from log_engine_narration's #727 substring dedup (the 4-line rotation legitimately repeats on
# cadence-aligned beats, and a run of dead beats logs ONLY heartbeats — dropping the repeat would kill
# the only liveness signal); the repeated rows are inert (never rendered, filtered from recap/FTS/lean
# tail/fallback). ALWAYS returns 0 — a heartbeat failure must never fail a beat. Reads ambient
# $STATE_DIR / $ROOT exactly as log_engine_narration / record_dm_reply do. $1=campaign_id $2=first?(1/0)
# $3=beat index.
worldos_emit_progress_heartbeat() {
  local campaign_id="$1" first="${2:-0}" idx="${3:-0}" text
  [ -n "${campaign_id//[[:space:]]/}" ] || return 0
  text="$(worldos_progress_beat_text "$first" "$idx")"
  log_engine_narration "$campaign_id" "$text" \
    || printf '%s\n' "[worldos] note: could not record progress heartbeat (non-fatal)" >&2
  return 0
}

# LEAN-BEAT DM-TURN ARGS (the ONE shared implementation of the WORLDOS_LEAN_BEATS path).
#
# Both play loops (scripts/play.sh AND qa/run_duo.sh) drive a DM turn through `claude -p`,
# normally `--resume`-ing the DM's growing session every beat (which REPLAYS the whole
# transcript → prefill grows ~6–10K tok/beat → the late-session slowdown a narrative persona
# quit over). With WORLDOS_LEAN_BEATS=1, continuing beats (NOT the cold open) instead start a
# FRESH session — a new --session-id, NO transcript carried — plus an --append-system-prompt
# re-ground directive telling the DM to re-ground from the engine's persisted truth via
# scene_context (which bundles state/threads/arcs + the recent player-facing narration TAIL)
# rather than from the fat transcript.
#
# This used to live INLINE in scripts/play.sh's dm_turn only, so qa/run_duo.sh's DM turn
# silently ignored the flag and the duo QA harness could never exercise lean. Factored here so
# the lean SESSION-ARG decision + the (long, drift-prone) re-ground directive prose live in
# EXACTLY ONE place and the two harnesses can't drift again (the file's stated intent).
#
# Bash 3.2 (the macOS system bash both harnesses run under) has NO namerefs, so this CANNOT
# return arrays by reference. Instead it POPULATES two well-known GLOBAL arrays the caller
# splices into its claude -p invocation:
#   WORLDOS_DM_LEAN_SESSION  — () when NOT lean (caller keeps its own --resume/--session-id),
#                              else (--session-id <fresh-uuid>) for a transcript-free turn.
#   WORLDOS_DM_LEAN_EXTRA    — () when NOT lean, else (--append-system-prompt "<directive>").
# Lean fires ONLY when: first = 0 (a CONTINUING beat)  AND  $WORLDOS_LEAN_BEATS = 1  AND
# campaign_id is non-empty. The cold open (first != 0) or an unknown campaign id falls through
# to the caller's normal resume path — byte-identical to today when the flag is off.
# CONVENTION (both harnesses): first=1 ⇒ the cold open that mints the session; first=0 ⇒ a
# continuing beat that would otherwise --resume it. Lean replaces that --resume on continuing
# beats with a fresh transcript-free session — so the firing condition is first=0, NOT first!=0.
# (scripts/play.sh's old inline lean used `first != 0`, which — combined with campaign_id only
# being known on continuing beats — meant its lean branch NEVER actually fired; this shared
# helper restores the documented intent: lean on beats 2+, full cold open. See PR notes.)
# $WORLDOS_LEAN_TAIL ($3, default 8) is the recent-narration depth the re-ground asks for.
# $1 = first?(1/0)  $2 = campaign_id (may be empty)  $3 = lean_tail (optional; default 8)
worldos_dm_lean_args() {
  local first="$1" campaign_id="${2:-}" lean_tail="${3:-8}"
  WORLDOS_DM_LEAN_SESSION=()
  WORLDOS_DM_LEAN_EXTRA=()
  # The cold open (first != 0), flag explicitly off (WORLDOS_LEAN_BEATS=0), or no campaign to
  # re-ground against → no-op (caller's existing --resume/--session-id path is used unchanged).
  # Lean fires on CONTINUING beats only. DEFAULT is now lean-ON (:-1): lean is standard, and
  # WORLDOS_LEAN_BEATS=0 is the explicit opt-out (matches the default flipped in both harnesses).
  if [ "$first" != "0" ] || [ "${WORLDOS_LEAN_BEATS:-1}" != "1" ] || [ -z "$campaign_id" ]; then
    return 0
  fi
  # LEAN beat: fresh session, no transcript replay. Re-ground from persisted truth.
  WORLDOS_DM_LEAN_SESSION=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
  WORLDOS_DM_LEAN_EXTRA=(--append-system-prompt "LEAN RE-GROUND (this turn has NO prior conversation transcript — by design, to keep your turn fast). You are mid-campaign, NOT starting over. Your FIRST action this turn MUST be worldos-engine scene_context(campaign_id=\"$campaign_id\", recent_narration=$lean_tail). That one call returns the campaign's CANON, and it is your whole memory for this beat — HONOR all of it as canon YOU already authored:
  • durable — the standing threads that persist across the campaign: open_quests (each with its still-open objectives = what the party still OWES), npc_relationships (every NPC the party has MET, with their attitude_value + attitude + relationship tags), companions (each companion's standing bond — attitude_value, has_arc, has_betrayal_agenda), factions (reputation + standing gauges), and the set flags.
  • director — the top structural debts the campaign owes right now (advisory; pay the top one as fiction, never recite it).
  • events / companion_arcs — any decisional that fired this beat, and any bond that just turned or betrayal_warning to foreshadow.
  • recent_narration — the last $lean_tail player-facing beats' prose (the immediate story-so-far).
  • state — the volatile current scene, party vitals, day/time, active quests, combat, pacing_mode, seed_params.
Do NOT contradict any of it, re-introduce an already-met NPC, reset the clock, or forget a prior choice. CRUCIAL — LOSSLESS RULE: this compact bundle is the always-pinned SPINE, not the whole world. For ANYTHING the moment reaches back to that is NOT in this bundle (a fact, NPC, place, event, or lore detail from earlier), you MUST retrieve it BEFORE you narrate — the entire world/lore/history is searchable on disk: call recall(campaign_id=\"$campaign_id\", query=\"…\") for past events/decisions/facts, lookup_lore(campaign_id=\"$campaign_id\", query=\"…\") for world/setting lore, or recall_npc(campaign_id=\"$campaign_id\", npc_id=\"…\") before voicing a returning NPC. NEVER guess and NEVER invent a detail that contradicts established canon — retrieve first. (You may also pass recall_query=\"…\" to scene_context to fold a recall into the same first call.) Then resolve the move and narrate, seamlessly continuing the established story. OUTPUT DISCIPLINE — the lean fresh-session re-grounds on a STATE digest and tends to shortcut into bookkeeping; do NOT. Your reply is 2nd-person in-fiction PROSE + quoted dialogue ONLY: NEVER a planning/intention note (\"I'll play that as a held beat…\"), NEVER a 3rd-person scene SUMMARY (\"The scene is resolved and persisted…\", \"the enforcer's threat now has a clock\", \"The floor is yours\"), NEVER a director/tool/stat leak. Any NPC the player ADDRESSED this move SPEAKS a real quoted in-voice line THIS beat — never summarized (\"she considers\", \"he weighs it\"). Narrate the beat ONCE: the prose you stream via log_event and the prose in your reply MUST be the SAME words (a reworded second version double-shows the beat in the player's chronicle).")
}

# DM-TURN EFFORT TIER (the ONE shared implementation of the cold-open-vs-routine effort split).
#
# Both play loops drive the DM through `claude -p --model …`. Opus (and the other thinking
# models) default to effort=high — i.e. maximum thinking budget every turn — which is the single
# biggest per-turn LATENCY cost. But the two kinds of beat want different budgets:
#   • the COLD OPEN (first != 0) is the one-time, do-it-right world-build — generate the world,
#     scene, PC, opening NPCs — so it earns the richest thinking: --effort max.
#   • CONTINUING / routine beats (first = 0) are the BULK of a session and mostly resolve one
#     move against already-established canon — so a medium thinking budget keeps quality while
#     cutting the thinking-latency that dominates each turn: --effort medium.
# This is keyed off the SAME `first` signal the lean branch uses (worldos_dm_lean_args), so the
# cold open is always full+max and the long tail is lean+medium — the two levers stay in lock-step.
#
# Levels are env-overridable (e.g. bump the routine tier back to high for a quality A/B, or drop
# the cold open to high to save cost) via the same WORLDOS_/WORLDOS_ resolution as everything else:
#   WORLDOS_DM_EFFORT_COLDOPEN (default max)    — the cold open's effort
#   WORLDOS_DM_EFFORT_ROUTINE  (default medium) — every continuing beat's effort
# Valid claude levels: low|medium|high|xhigh|max (a bad override is passed through verbatim and the
# CLI validates it). Applies ONLY to the DM turn — the player/actor facade never gets --effort.
#
# Like the lean helper, bash 3.2 has no namerefs, so this POPULATES a well-known GLOBAL array the
# caller splices into its claude -p invocation (never empty — every DM turn gets an explicit tier):
#   WORLDOS_DM_EFFORT — (--effort max) on the cold open, (--effort medium) on continuing beats.
# $1 = first?(1/0)
worldos_dm_effort_arg() {
  local first="$1" level
  if [ "$first" != "0" ]; then
    # Cold-open effort is model-aware. Opus's max-effort world-build is generation-bound and overruns
    # the cold-open timeout (measured 2026-06-06: Opus --effort max never finishes <400s; --effort HIGH
    # finishes ~300s WITH a full, BG-caliber opening). Opus-high ≈ Sonnet-max world-build quality but
    # lands in time, so Opus defaults to high; Sonnet keeps max (it finishes in ~280–400s at max).
    local _co_default=max
    case "${WORLDOS_DM_MODEL:-}" in *opus*) _co_default=high ;; esac
    level="$(worldos_env DM_EFFORT_COLDOPEN "$_co_default")"
  else
    level="$(worldos_env DM_EFFORT_ROUTINE medium)"
  fi
  WORLDOS_DM_EFFORT=(--effort "$level")
}

# DM-TURN TIMEOUT TIER (the ONE shared implementation of the cold-open-vs-routine timeout split).
#
# Both play loops wrap the DM's `claude -p` in `timeout <secs>` + ONE retry, so a wedged turn
# recovers instead of hanging the session. But the two kinds of beat need very different deadlines,
# for the SAME reason the effort tier splits them (worldos_dm_effort_arg, keyed off `first`):
#   • the COLD OPEN (first != 0) is the one-time, --effort max, full world-build — generate the
#     world, scene, PC, opening NPCs + portraits. That MAX-EFFORT cold open routinely runs ~280–400s;
#     the routine deadline KILLS it mid-build (the masked "cold-open reproducibly broken" mode),
#     so the cold open gets a generous, model-aware deadline: WORLDOS_COLDOPEN_TIMEOUT
#     (default 500s opus / 550s non-opus — see the F12-2 note in worldos_dm_timeout below).
#   • CONTINUING / routine beats (first = 0) are --effort medium and resolve one move against
#     established canon — faster — so they get the per-beat deadline WORLDOS_BEAT_TIMEOUT
#     (default 360s — see the F12-1 note below).
# Keyed off the SAME `first` signal as the effort + lean levers, so cold-open=full+max+(500/550)s and
# the long tail=lean+medium+360s stay in lock-step. Applies ONLY to the DM turn (player/companion turns
# are never wrapped in a per-beat timeout at all).
#
# Both knobs resolve through the same WORLDOS_/WORLDOS_ fallback as everything else (worldos_env):
#   WORLDOS_COLDOPEN_TIMEOUT (default 500 opus / 550 non-opus) — the cold open's deadline, in seconds.
#   WORLDOS_BEAT_TIMEOUT     (default 360) — every continuing beat's deadline (today's knob, kept).
# Note: WORLDOS_BEAT_TIMEOUT keeps its WORLDOS_ name (it predates the WorldOS rename and is the
# documented routine knob); only the NEW cold-open knob takes the WORLDOS_ name. worldos_env still
# honors a WORLDOS_BEAT_TIMEOUT override of the routine tier for forward-compat.
#
# F12-1 (audit 2026-06-11): the routine default was a flat 200s, which killed ~18% of HEALTHY
# routine beats (measured on 206 run_duo beats, same opus/medium defaults: p50=152 p90=224
# p95=264 max=360 — run_duo has no timeout, so its >200s completions are the honest
# counterfactual for what the product lanes were killing). 360s covers the measured max; an
# explicit WORLDOS_BEAT_TIMEOUT/WORLDOS_BEAT_TIMEOUT override still wins unchanged.
#
# Unlike the effort/lean helpers (which populate an array spliced into argv), this just ECHOES the
# resolved seconds — the caller uses it as the scalar `timeout <secs>` argument. $1 = first?(1/0)
worldos_dm_timeout() {
  local first="$1"
  if [ "$first" != "0" ]; then
    # Cold-open deadline is model-aware. Opus-high cold-open measured ~300s here; give it margin (500s)
    # for per-world/per-run variance so it is never killed mid-build.
    # F12-2 (audit 2026-06-11): the NON-opus default was 400s — but a sonnet max-effort cold open's
    # OWN documented band is "~280–400s" (the prior comment), so 400 == the band TOP (zero margin vs
    # the band, ~8% vs the 370s measured max) and a slow sonnet cold open was killed at the same mark
    # the band predicted. Bump the non-opus default to 550s (~38% over the 400 band-top), so the sonnet
    # A/B arm gets proportional margin to opus's (opus: 500 vs ~300 measured ≈ +67%; sonnet: 550 vs 400
    # band-top ≈ +38%). Opus is unchanged (still the default DM model in every lane), so the shipped
    # path is byte-identical; only the explicit sonnet opt-in widens. Env override still wins unchanged.
    local _co_timeout=550
    case "${WORLDOS_DM_MODEL:-}" in *opus*) _co_timeout=500 ;; esac
    worldos_env COLDOPEN_TIMEOUT "$_co_timeout"
  else
    worldos_env BEAT_TIMEOUT 360
  fi
}

# LIVE COMPOSITION (#835 Increment 1) — the ONE shared implementation of the stream-beats lever,
# so the three DM wrappers (scripts/play.sh, scripts/play_party.sh, qa/run_duo.sh) can't drift.
#
# GATE: everything here is behind WORLDOS_STREAM_BEATS (worldos_env, default 0 = OFF). When OFF,
# the flag-arg array stays EMPTY and the launcher/killer are no-ops, so the live DM `claude -p`
# invocation is BYTE-IDENTICAL to today (the `${WORLDOS_STREAM_FLAG[@]+...}` splice expands to
# nothing). The owner flips WORLDOS_STREAM_BEATS=1 after validating this dark PR.
#
# When ON: the wrapper adds `--include-partial-messages` to the DM stream-json call (so the
# per-attempt $out jsonl carries the raw API stream events as the model generates), and launches
# scripts/stream_tailer.py against that $out BEFORE the call — the tailer decodes the player-facing
# scene out of the DM's streaming log_event tool-arg and writes chunks to $STATE_DIR/stream/
# current.jsonl, which the viewer polls (/beat-stream). The tailer is a SIDECAR: if it crashes the
# beat is unaffected (the canonical /events + /chat paths still resolve it); the wrapper kills it on
# beat end. The flag is read once into the array so every call site uses the SAME splice form.

# Build WORLDOS_STREAM_FLAG: (--include-partial-messages) when streaming is ON, else empty. Spliced
# into the DM argv via ${WORLDOS_STREAM_FLAG[@]+"${WORLDOS_STREAM_FLAG[@]}"} (set -u safe; empty
# array expands to nothing → today's exact argv when OFF).
worldos_stream_flag_arg() {
  WORLDOS_STREAM_FLAG=()
  [ "$(worldos_env STREAM_BEATS 0)" = "1" ] && WORLDOS_STREAM_FLAG=(--include-partial-messages)
}

# Launch the per-attempt stream tailer against the DM $out file (no-op when streaming is OFF). The
# tailer is started in the BACKGROUND; its PID is captured in WORLDOS_STREAM_TAILER_PID for the
# killer. Best-effort: a launch failure (missing python3 / missing script) never fails the beat —
# the live stream simply doesn't appear and the canonical paths resolve normally.
#   $1 = the DM $out stream-json path   $2 = $STATE_DIR
worldos_stream_tailer_start() {
  WORLDOS_STREAM_TAILER_PID=""
  [ "$(worldos_env STREAM_BEATS 0)" = "1" ] || return 0
  local out="$1" state_dir="$2"
  [ -n "$out" ] && [ -n "$state_dir" ] || return 0
  local script="${WORLDOS_STREAM_TAILER:-$ROOT/scripts/stream_tailer.py}"
  [ -f "$script" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  python3 "$script" "$out" "$state_dir/stream" >/dev/null 2>&1 &
  WORLDOS_STREAM_TAILER_PID="$!"
  return 0
}

# Kill the tailer launched by worldos_stream_tailer_start (no-op when none ran). Idempotent and
# best-effort — a tailer that already exited is a benign no-op. Clears the PID after.
worldos_stream_tailer_stop() {
  local pid="${WORLDOS_STREAM_TAILER_PID:-}"
  [ -n "$pid" ] || return 0
  kill "$pid" >/dev/null 2>&1 || true
  WORLDOS_STREAM_TAILER_PID=""
  return 0
}

# RETRY DEADLINE (F12-1's second half): both dm_turn paths captured `beat_timeout` ONCE and the
# ONE retry re-invoked with the SAME deadline verbatim — so a healthy-but-long beat that tripped
# the routine deadline was killed AGAIN at the same mark (two kills, zero narration). Attempt 2
# ESCALATES to the model-aware COLD-OPEN tier (worldos_dm_timeout 1 — opus 500 / default 400,
# env-overridable via WORLDOS_COLDOPEN_TIMEOUT like everything else), and never DE-escalates: if
# the caller's attempt-1 deadline was already larger (an explicit WORLDOS_BEAT_TIMEOUT override,
# or a cold-open retry whose tier == the escalation tier), the larger value is kept. A
# non-numeric base (an env typo) is treated as 0 → the escalation tier (3.2-clean: no arrays,
# pure case/test). ECHOES the resolved seconds. $1 = attempt 1's deadline in seconds.
worldos_dm_retry_timeout() {
  local base="${1:-0}" esc
  case "$base" in ''|*[!0-9]*) base=0 ;; esac
  esc="$(worldos_dm_timeout 1)"
  if [ "$base" -gt "$esc" ]; then
    printf '%s' "$base"
  else
    printf '%s' "$esc"
  fi
}

# BOUNDED-COMMAND SHIM (F12-8): `timeout(1)` is a GNU coreutils binary that does NOT exist on
# stock macOS (/usr/bin/timeout is absent on Darwin; only Homebrew coreutils provides one) — so
# every `timeout <secs> claude …` beat on a non-coreutils Mac died rc=127 ("command not found")
# in under a second, the retry died the same way, and the empty narration was masked. This shim
# is the ONE bounded-invocation front door for the harnesses: it uses timeout(1) when present
# (cheapest — no extra interpreter per beat), else falls back to a python3 subprocess that
# preserves the exact rc semantics callers branch on:
#   rc=124 — the deadline killed the command (timeout(1)'s contract; the retry triggers on it),
#   rc=127 / rc=126 — command not found / not executable,
#   any other rc — the child's own exit code, passed through verbatim.
# stdout/stderr/stdin of the child pass through untouched (python3 -c keeps stdin free — the
# callers redirect stdout per-attempt). Fallback kill is SIGKILL (subprocess.run's TimeoutExpired
# path) vs timeout(1)'s SIGTERM — acceptable here: the wrapped beat is by definition wedged, and
# every caller treats 124 as "dead, retry". Bash 3.2-clean: no arrays, "$@" pass-through only.
# $1 = seconds, $2.. = the command. NOTE: callers should prefer this over bare `timeout` for any
# new bounded call (F12-9/11/12 land on this same shim).
worldos_timeout() {
  local _secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$_secs" "$@"
    return $?
  fi
  python3 -c '
import subprocess, sys
secs = float(sys.argv[1])
cmd = sys.argv[2:]
try:
    sys.exit(subprocess.run(cmd, timeout=secs).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
except FileNotFoundError:
    sys.exit(127)
except PermissionError:
    sys.exit(126)
except KeyboardInterrupt:
    sys.exit(130)
' "$_secs" "$@"
}

# PROVIDER-STATUS SIDECAR (F12-10): write the provider lifecycle sidecar the OpenWorlds viewer reads
# (provider_status.json, schema worldos.provider-status.v1). Before this, ONLY the codex DM wrapper
# wrote it; the CLAUDE lanes (scripts/play.sh + scripts/play_party.sh) never did, so on a turn-cap /
# budget stop or a crash the viewer fell back to status "unknown" — which is NOT in the
# {stopped,failed,exhausted} set the viewer buckets as `no_provider` (viewer/server.py), so the app
# showed a live-looking-but-dead dashboard instead of the "DM provider is no longer running" surface.
# This is the ONE shared writer for the claude lanes (the codex wrapper keeps its own richer copy with
# the seed fixture). The sidecar is DERIVED / atomic state, NOT campaign state — it is the viewer's
# read-only health view, never read back as truth — so writing it does not violate engine-sole-writer.
#
# Atomic: write a sibling tmp under the SAME dir, fsync, replace, fsync the dir — so a concurrent
# viewer read never sees a torn file. Reads ambient globals with safe defaults (every field the
# viewer's _provider_status_summary expects), so a caller that hasn't set a field still writes a valid
# row. Best-effort: a write failure never fails a beat (returns 0). Bash 3.2-clean: standalone python
# (no heredoc-in-$()), no arrays. $1=PROVIDER_STATUS path  $2=status  $3=reason  $4=detail  $5=turns
# $6=wrapper-name (optional; defaults to the calling script's basename — display-only field).
worldos_write_provider_status() {
  local path="$1" status="$2" reason="$3" detail="$4" turns="${5:-0}" wrapper="${6:-${BASH_SOURCE[1]:-}}"
  local provider model max_turns sha actor_model scorer_model
  provider="$(worldos_env PROVIDER claude)"
  model="$(worldos_env DM_MODEL '')"
  actor_model="$(worldos_env ACTOR_MODEL '')"
  scorer_model="$(worldos_env SCORER_MODEL '')"
  max_turns="${MAX_TURNS:-}"
  sha="${WORLDOS_BUILD_SHA:-$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')}"
  python3 - "$path" "$status" "$reason" "$detail" "$provider" "$model" "$actor_model" "$scorer_model" "$max_turns" "$turns" "$sha" "$wrapper" 2>/dev/null <<'PY' || true
import json, os, sys, time
from pathlib import Path
(path, status, reason, detail, provider, model, actor_model, scorer_model,
 max_turns, turns, sha, wrapper) = sys.argv[1:]
path = Path(path)
wrapper_name = os.path.basename(wrapper) or wrapper or "play.sh"
payload = {
    "schema": "worldos.provider-status.v1",
    "provider": provider,
    "provider_family": "anthropic-claude" if "codex" not in provider.lower() else "codex-openai",
    "auth_surface": "claude-cli",
    "model": model,
    "player_model": actor_model,
    "scorer_model": scorer_model,
    "wrapper": ("scripts/%s" % wrapper_name) if not wrapper_name.startswith("scripts/") else wrapper_name,
    "fixture": {},
    "status": status,
    "reason": reason,
    "detail": detail,
    "max_turns": int(max_turns) if str(max_turns).isdigit() else (max_turns or None),
    "dm_turns": int(turns) if str(turns).isdigit() else turns,
    "updated_at": time.time(),
}
try:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        dfd = None
    if dfd is not None:
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
except OSError:
    sys.exit(0)
PY
  return 0
}

# RE-MINT SESSION ON RETRY (the ONE shared implementation of "never reuse a CONSUMED session id").
# A `claude -p` attempt that fails AFTER startup STILL registered its --session-id on disk
# (~/.claude/projects/<proj>/<uuid>.jsonl), so a retry that re-passes that SAME --session-id dies
# "Session ID <uuid> is already in use." → 0-byte output → empty narration → the cold open never
# completes (the masked failure mode behind the 2026-06-02 "reproducibly broken cold-open" reports:
# attempt 1 actually 401'd, but the retry's session collision is all that reached dm.err). The LEAN
# path already side-steps this (worldos_dm_lean_args mints a fresh uuid every call); the COLD-OPEN /
# legacy --resume path passes a STABLE $DSID and so MUST be re-minted before its retry. Given the
# resume-mode the prior attempt used (the caller's `resume` array, passed as "$@"), populate the
# well-known global WORLDOS_DM_RETRY_SESSION:
#   • prior mode --session-id (a CREATE) → (--session-id <fresh-uuid>)  — retry on a BRAND-NEW session.
#   • prior mode --resume <id>           → ()  — resuming an already-created session on retry is safe;
#                                               leave the caller's --resume untouched.
# Caller contract: call this ONLY when the lean helper did NOT fire (lean re-mints itself), so each
# path mints exactly once. Bash 3.2: no namerefs — inspect args by value + populate a global (mirrors
# worldos_dm_lean_args / worldos_dm_effort_arg). $@ = the caller's current `resume` array tokens.
worldos_dm_remint_session_on_retry() {
  WORLDOS_DM_RETRY_SESSION=()
  if [ "${1:-}" = "--session-id" ]; then
    WORLDOS_DM_RETRY_SESSION=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
  fi
}

# SURFACE A FAILED DM ATTEMPT'S REAL ERROR (stop the masking). A DM turn redirects only STDERR to
# dm.err, but `claude -p`'s real failure (a 401 auth error, a budget stop, an MCP startup failure)
# is a STRUCTURED event on STDOUT — in the per-attempt $out jsonl. So when attempt 1 fails and the
# retry then collides on the session id, dm.err shows ONLY "Session ID … in use" and the TRUE cause
# is invisible (this masked a 401 as a phantom concurrency race across ~3 runs). Parse the last
# result event from $out and echo a clear "[dm-attempt] …" reason to stderr; a 401/403 is flagged
# NON-retryable with an operator hint. Read-only on engine state; jq-optional (a grep fallback still
# names an HTTP status); a missing/0-byte $out → a generic rc line (no regression).
# $1 = the failed attempt's stream-json path  $2 = its exit code (rc)
worldos_report_attempt_failure() {
  local out="$1" rc="$2" reason status
  reason="$(jq -rs 'map(select(.type=="result"))[-1] | if . == null then "" else "is_error=\(.is_error) subtype=\(.subtype // "?") result=\((.result // "")[0:180])" end' "$out" 2>/dev/null)"
  status="$(grep -oE '"(api_error_status|status)":[[:space:]]*[0-9]{3}' "$out" 2>/dev/null | grep -oE '[0-9]{3}' | head -n1)"
  if [ -z "${reason//[[:space:]]/}" ] && [ -z "$status" ]; then
    if [ "$rc" = "124" ]; then
      echo "[dm-attempt] DM turn timed out (rc=124; no result event written to $out)" >&2
    else
      echo "[dm-attempt] DM turn failed (rc=$rc; no parseable result event in $out)" >&2
    fi
    return 0
  fi
  echo "[dm-attempt] DM turn failed (rc=$rc):${status:+ HTTP $status}${reason:+ $reason}" >&2
  case "$status" in
    401|403) echo "[dm-attempt] -> HTTP $status is an AUTH failure and is NOT retryable: check 'claude' login / ANTHROPIC_API_KEY (apiKeySource was likely \"none\"). The retry will also fail until auth is restored." >&2 ;;
  esac
  return 0
}

# TRANSIENT-vs-REAL FAILURE CLASSIFICATION (the overnight-run killer fix). A long playtest
# (gs-ember-18b) died at beat 4 when a DM turn hit a SERVER-SIDE HTTP 500 AND the single retry
# also 500'd — one transient cluster aborted a 2-3h run. turn_retry retried only ONCE on empty
# output and could not tell a SERVER blip (worth several backed-off retries) from a DETERMINISTIC
# failure (an auth 401/403, a bad request — retrying is pointless and just burns time + budget).
# This shared classifier is the front door both harnesses' retry loops consult.
#
# worldos_dm_failure_is_transient OUT RC — is THIS failed DM attempt a TRANSIENT/server-side blip
# that a backed-off retry could recover from? Reads the SAME per-attempt stream-json ($out) +
# exit code the report/extract helpers use; never touches engine state.
#   return 0 (TRANSIENT — retry): rc=124 timeout; HTTP 408/409/425/429/500/502/503/504/520-529;
#            or error text naming overload / rate-limit / internal-server / unavailable / gateway /
#            connection-reset / timeout. These are server-side or load-shed and clear on retry.
#   return 1 (REAL / fail-fast):  HTTP 400/401/403/404/422 (auth/permission/bad-request — the
#            #357 + worldos_report_attempt_failure re-auth path owns 401/403); error text naming
#            authenticate / permission / invalid-request / budget; OR no recognizable transient
#            signal at all (a deterministic bad turn — do NOT retry 4× and mask it).
# Auth ALWAYS wins over a co-occurring 5xx token (a 401 body must never be read as transient).
worldos_dm_failure_is_transient() {
  local out="$1" rc="${2:-0}" status body
  # An rc=124 timeout writes no result event — it is a server/network slowness signal: TRANSIENT.
  [ "$rc" = "124" ] && return 0
  status="$(grep -oE '"(api_error_status|status)":[[:space:]]*[0-9]{3}' "$out" 2>/dev/null | grep -oE '[0-9]{3}' | head -n1)"
  # FAIL-FAST classes first — a deterministic status is NEVER overridden by a transient text token.
  case "$status" in
    400|401|403|404|405|422) return 1 ;;
  esac
  case "$status" in
    408|409|425|429|500|502|503|504|520|521|522|523|524|525|526|527|529) return 0 ;;
  esac
  # No decisive status — fall back to the error text. Auth/permission/bad-request markers are REAL
  # and checked FIRST so an "authentication_error" body can never be mistaken for transient.
  body="$(jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null)"
  [ -n "$body" ] || body="$(grep -oE '"result":[[:space:]]*"[^"]*"' "$out" 2>/dev/null | head -n1)"
  printf '%s' "$body" | grep -qiE 'authenticat|unauthor|permission|forbidden|invalid[_ ]?(request|api[_ ]?key)|x-api-key|credit balance|budget|max.budget' && return 1
  printf '%s' "$body" | grep -qiE 'overloaded|rate[_ ]?limit|too many requests|internal server|service unavailable|bad gateway|gateway time|server error|temporarily|connection (reset|error|refused)|econnreset|etimedout|timed? ?out|503|529|500 ' && return 0
  # Nothing recognizably transient -> treat as REAL (fail fast; do not mask a deterministic failure).
  return 1
}

# worldos_dm_retry_backoff ATTEMPT — sleep before retry #ATTEMPT (1-indexed: the wait BEFORE the
# 2nd, 3rd, 4th try) on a TRANSIENT failure, giving a server-side 500/overload cluster time to
# clear instead of hammering it. Schedule: 3s, 8s, 20s (capped). Bounded + finite by design — the
# caller also caps the attempt count, so a transient cluster can never loop forever. Override the
# whole sleep via WORLDOS_RETRY_SLEEP_CMD (a test seam — a bash test substitutes a no-op that just
# records the requested seconds, so the suite doesn't actually wait ~30s).
worldos_dm_retry_backoff() {
  local attempt="${1:-1}" secs
  case "$attempt" in
    1) secs=3 ;;
    2) secs=8 ;;
    *) secs=20 ;;
  esac
  if [ -n "${WORLDOS_RETRY_SLEEP_CMD:-}" ]; then
    "$WORLDOS_RETRY_SLEEP_CMD" "$secs"
  else
    sleep "$secs"
  fi
}

# COLD-OPEN RETRY: RESUME the minted campaign instead of re-seeding (#719). The DEFAULT cold-open
# prompt (scripts/play.sh) says start_world + "if it returns existing_campaigns, start fresh", so a
# timed-out attempt-1 that ALREADY minted+seeded a campaign gets a retry that mints a SECOND,
# party-less campaign — the viewer auto-follows the empty orphan ⇒ party-wipe + frozen/input-locked
# screen (the adversarially-verified engine root of two criticals, RRI 2026-06-09). This returns a
# RESUME directive (get_state on the existing id, DO NOT start_world, seat a canon PC only if the
# party is empty) when — and ONLY when — this is a DEFAULT cold-open RETRY (first=1, no authored
# hero) AND attempt-1 left a live campaign. Otherwise it echoes the base message UNCHANGED:
#   - first=0 (a continuing beat) — never a cold-open re-seed risk;
#   - hero_camp set — the AUTHORED-hero opener already opens on the existing campaign;
#   - live_cid empty — attempt-1 minted nothing, so the normal cold-open MUST run.
# Byte-identical on every path except the one bug. Read-only; 3.2-safe (printf, no heredoc-in-$()).
# $1=first  $2=hero_camp  $3=live_campaign_id  $4=world  $5=base_msg ; echoes the message to use.
worldos_coldopen_retry_msg() {
  local first="$1" hero_camp="$2" live_cid="$3" world="$4" base_msg="$5"
  if [ "$first" != "1" ] || [ -n "$hero_camp" ] || [ -z "$live_cid" ]; then
    printf '%s' "$base_msg"
    return 0
  fi
  printf '%s' "You are the Dungeon Master for a solo WorldOS adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

A previous cold-open attempt already minted THIS session's campaign, so the world ALREADY EXISTS — RESUME it, do NOT start over:
- This session's campaign ALREADY EXISTS: use campaign_id=$live_cid for EVERY engine call. DO NOT call start_world(\"$world\") — it would mint a NEW campaign id and ORPHAN this save (the player's seated party would vanish).
- call get_state(\"$live_cid\") FIRST to read the world bible (premise, era/chronology, tone, standing threads, seeded regions/factions/roster) AND the current party.
- start_session only if get_state shows no active session.
- If get_state shows the party is EMPTY (no PC seated yet), choose the player's hero by SELECTING a real canon NPC — NEVER invent a custom character: list_canon_characters(playable_only=true) (the 7 BG3 origin heroes are excluded), pick a fitting MID-TIER canon figure with an ingested portrait + real backstory, then load_canon_character(that name, kind=\"player\", add_to_party=true). If a PC is ALREADY in the party, KEEP them — do NOT reroll or replace.
- This is a SOLO session: the player begins ALONE. Do NOT recruit a companion at cold-open and do NOT seat anyone but the player. A roster legend may APPEAR as a face in the world (voiced, with a real wound), but they are MET, not recruited.

CRITICAL — your FINAL output THIS turn MUST BE the opening SCENE itself, written as 2nd-person player-facing prose (addressed to \"you\"): where the player IS, what they see/hear/smell, who is present and a real quoted line, ending on a clear open moment + choice. Re-ground via the tools FIRST, then CLOSE the turn by writing the scene. NEVER end this turn on a tool call, and NEVER let your reply be a 3rd-person setup brief or game-system notation.

Their actions will arrive as tagged moves — [say] (their dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — one per turn from the dashboard."
}

# Read the run's progression facts from the snapshot in ONE python pass. Echoes a single
# TAB-separated line:  day <TAB> time_of_day <TAB> visited_count <TAB> npcs_met <TAB>
# current_location_id <TAB> current_location_visited(0/1) <TAB> combat_active(0/1)
# (combat_active is field 7, appended additively — fields 1-6 are unchanged for callers that
# cut -f1..6). Echoes nothing when there's no snapshot yet (pre-first-beat). Read-only.
# $1 = STATE_DIR
worldos_read_progress() {
  local snap; snap="$(worldos_snapshot_path "$1")"
  [ -n "$snap" ] || return 0
  python3 - "$snap" <<'PY' 2>/dev/null
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
day = s.get("day") or 1
tod = (s.get("time_of_day") or "").strip().lower() or "morning"
locs = s.get("locations", {}) or {}
visited = sum(1 for l in locs.values() if isinstance(l, dict) and l.get("visited"))
chars = s.get("characters", {}) or {}
npcs_met = sum(1 for c in chars.values()
               if isinstance(c, dict) and c.get("kind") == "npc" and c.get("met"))
cur = s.get("current_location_id") or ""
cur_visited = 0
cl = locs.get(cur) if cur else None
if isinstance(cl, dict) and cl.get("visited"):
    cur_visited = 1
combat = s.get("combat") or {}
combat_active = 1 if isinstance(combat, dict) and combat.get("active") else 0
print("\t".join([str(day), tod, str(visited), str(npcs_met), str(cur),
                 str(cur_visited), str(combat_active)]))
PY
}

# SEATING GUARD (F12-3): is a PLAYER PC seated in this campaign's party? The cold open's seating
# is DM-STOCHASTIC (a forensic .app run built the world but ended its cold-open turn WITHOUT ever
# calling create_character(kind="player") — party=[] and an unplayable "no_actor" surface), so
# BOTH product opener paths guard it: check, ONE reseat retry, then a LOUD abort. Factored here —
# the audit's recurring fix shape is "extract the shared helper, stop patching one path"
# (play_party.sh carried this guard locally; play.sh had nothing). Matches the viewer's
# _action_actor contract exactly: seated == a party member whose character record is
# kind="player". SNAPSHOT-READ-ONLY (never writes; engine stays the sole writer). A missing
# snapshot or a blank campaign id (the dead-cold-open mode: CAMPAIGN_ID="") is NOT seated.
# $1 = STATE_DIR  $2 = campaign_id ; returns 0 = seated, 1 = not seated.
worldos_pc_seated() {
  local state_dir="$1" campaign_id="${2:-}"
  [ -n "${campaign_id//[[:space:]]/}" ] || return 1
  local snap="$state_dir/campaigns/$campaign_id/snapshot.json"
  [ -f "$snap" ] || return 1   # no snapshot at all -> definitely not seated
  python3 - "$snap" <<'PY'
import json, sys
try:
    snap = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
chars = snap.get("characters") if isinstance(snap.get("characters"), dict) else {}
party = snap.get("party") if isinstance(snap.get("party"), list) else []
# Match the viewer's _action_actor: a party member whose record is kind="player".
seated = any(
    isinstance(chars.get(cid), dict) and chars.get(cid, {}).get("kind") == "player"
    for cid in party if isinstance(cid, str)
)
sys.exit(0 if seated else 1)
PY
}

# The state-dir-scoped CARRY-FORWARD file (F04-2). The soft-tick advances the world
# clock between beats and the engine's advance_time DISCLOSES the world beats / backlog
# developments / effect expiries it processed — but the harness historically printed only
# day/time_of_day and DISCARDED that living-world content to stderr, so a thread beat that
# fired "while the party slept" was lost before the DM ever saw it (invariant-4 breach, on
# the CALLER side — the engine already returns it). We persist those lines HERE, then the
# NEXT beat's runbook prepends them as a "While time passed:" block so the DM weaves them in.
# One file per run state-dir; read-and-cleared each beat (see worldos_take_carryforward).
worldos_carryforward_path() {
  printf '%s/.worldos_softtick_carry.txt' "$1"
}

# Read-and-CLEAR the carry-forward block for this state dir (F04-2). Echoes the stored
# "While time passed:" block (possibly multi-line) and removes the file so each surfaced
# beat is woven EXACTLY once — never re-fed on a later beat. Empty (and a clean no-op) when
# nothing was carried. $1 = STATE_DIR.
worldos_take_carryforward() {
  local f; f="$(worldos_carryforward_path "$1")"
  [ -f "$f" ] || return 0
  cat "$f" 2>/dev/null
  rm -f "$f" 2>/dev/null
}

# The C backstop. After a DM beat, compare the live clock to the clock BEFORE the beat; if the
# DM did NOT advance it this beat, advance ONE phase through the engine (advance_time(phases=1))
# so the day cannot freeze at morning. Engine stays the sole writer. No-op (and silent) until a
# snapshot exists. Echoes a short "[tick] …" status to stderr for the run log. F04-2: also
# persists the engine's returned world beats / developments / effect-expiries to the carry-
# forward file so the NEXT beat's runbook surfaces them to the DM (they were being discarded).
# $1 = ROOT (repo root)  $2 = STATE_DIR  $3 = prev_day  $4 = prev_tod
worldos_soft_tick() {
  local root="$1" state_dir="$2" prev_day="$3" prev_tod="$4"
  local snap cur cur_day cur_tod cur_combat
  snap="$(worldos_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  cur="$(worldos_read_progress "$state_dir")"
  cur_day="$(printf '%s' "$cur" | cut -f1)"
  cur_tod="$(printf '%s' "$cur" | cut -f2)"
  cur_combat="$(printf '%s' "$cur" | cut -f7)"
  # In COMBAT the clock is measured in rounds (6s) via next_turn, NOT world phases. Advancing a
  # phase here would expire every round/minute-scale buff at once (Bless, Hex) and drop
  # concentration mid-fight (combat.expire_clock_effects). A frozen world-clock during combat is
  # CORRECT — combat doesn't burn time-of-day phases — so SKIP the tick entirely while active.
  if [ "$cur_combat" = "1" ]; then
    printf '%s\n' "[tick] combat active -> world clock not advanced (combat runs in rounds; use next_turn)" >&2
    return 0
  fi
  # The DM already moved the clock this beat → defer to its pacing, do nothing.
  if [ "$cur_day" != "$prev_day" ] || [ "$cur_tod" != "$prev_tod" ]; then
    return 0
  fi
  # Frozen this beat → advance one phase via the engine. The campaign id is the snapshot's
  # parent dir name; WORLDOS_STATE_DIR scopes the engine to THIS run's state tree. We capture
  # the engine's output (status or error) and echo it to STDERR for the run log — we do NOT
  # blanket-suppress, so a real engine failure is visible. On a contended host `uv` can return a
  # transient cache error; that's non-fatal here (the NEXT beat re-reads the clock and re-ticks),
  # so we never let the tick's exit status fail the loop (the function always returns 0).
  local camp out carry; camp="$(basename "$(dirname "$snap")")"
  carry="$(worldos_carryforward_path "$state_dir")"
  out="$(WORLDOS_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" "$carry" 2>&1 <<'PY'
import sys
import server
camp = sys.argv[1]
carry_path = sys.argv[2] if len(sys.argv) > 2 else ""
try:
    r = server.advance_time(camp, phases=1, note="harness soft clock-tick backstop")
    print(f"[tick] clock frozen this beat -> engine advanced to day {r.get('day')} {r.get('time_of_day')}")
    # F04-2: the engine DISCLOSES the living-world content it processed this tick. Persist it
    # so the NEXT runbook surfaces it to the DM instead of dropping it to the run log.
    # Only the genuinely-narratable channels (world beats fired, backlog developments,
    # effect expiries). Empty channels add NOTHING (no carry file, no token cost on a quiet
    # tick). Best-effort: a write failure never fails the loop (the tick already advanced).
    def _lines(v):
        # Channels differ in item shape: world_beats / world_developments are plain
        # strings, but expired_effects is list[{character_id, name}] (see
        # _expire_clock_effects_all in servers/engine/server.py). Render the human-
        # readable name for a dict item (falling back to str(x) only if it has no usable
        # "name"), and pass strings through unchanged — so the expiry line reads
        # "Bless, Mage Armor", NOT the raw "{'character_id': 'pc-1', 'name': 'Bless'}"
        # dict repr the player would otherwise see (F04-2 follow-up).
        out = []
        for x in (v or []):
            if isinstance(x, dict):
                s = str(x.get("name") or "").strip() or str(x).strip()
            else:
                s = str(x).strip()
            if s:
                out.append(s)
        return out
    beats = _lines(r.get("world_beats"))
    devs = _lines(r.get("world_developments"))
    exps = _lines(r.get("expired_effects"))
    if (beats or devs or exps) and carry_path:
        chunks = []
        for line in beats:
            chunks.append(f"- {line}")
        for line in devs:
            chunks.append(f"- {line}")
        if exps:
            chunks.append("- effects that ran out overnight: " + ", ".join(exps))
        block = ("While time passed (the world moved between beats — weave these into THIS "
                 "scene; do not silently drop them):\n" + "\n".join(chunks))
        try:
            # APPEND so a second frozen tick before the next beat does not clobber the first
            # tick content; the next beat reads-and-clears the whole accumulation.
            with open(carry_path, "a", encoding="utf-8") as fh:
                if fh.tell() > 0:
                    fh.write("\n")
                fh.write(block + "\n")
        except OSError as e:
            print(f"[tick] (carry-forward write skipped: {e})")
except Exception as e:
    print(f"[tick] soft-tick FAILED ({e})")
PY
)"
  [ -n "$out" ] && printf '%s\n' "$out" >&2
  return 0
}

# The A runbook selector. Given the beat index, the total beats, the party's location at the
# START of this beat, and the live snapshot, return EXACTLY ONE moment-specific runbook to fold
# into the DM's prompt for THIS beat — replacing the old constant paragraph. Precedence (first
# match wins) so the most urgent structural gap is the one we fire:
#   1. new-location / early       -> "scene-intro"   (the party just arrived somewhere fresh,
#                                     or it's beat 1) — open the place before they act.
#   2. final ~2 beats             -> "climax/payoff" — converge; pay off what was set up.
#   3. midpoint (~beats/2)        -> "reversal"      — a turn that COSTS the hero personally.
#   4. stuck (no move in N beats) -> "travel/peopling" — move the party OR bring a new named NPC.
#   5. otherwise                  -> "rising-action" — escalate; keep friction that sticks.
# Echoes the runbook text (a short paragraph). Never empty.
# F04-2: BEFORE the runbook body, this prepends the soft-tick CARRY-FORWARD block (the
# living-world content the engine processed while the clock advanced last beat) so the DM
# is TOLD what moved between beats instead of it being discarded. The carry is read-and-
# cleared, so it surfaces exactly once. A quiet tick carries nothing -> byte-identical to
# the old single-paragraph runbook.
# $1 = beat (1-based)  $2 = total beats  $3 = prev_location_id (loc at beat start)  $4 = STATE_DIR
worldos_runbook_for_beat() {
  local state_dir="$4" carry body
  carry="$(worldos_take_carryforward "$state_dir")"
  body="$(_worldos_runbook_body "$@")"
  if [ -n "$carry" ]; then
    # The world-moved block leads (it's the freshest fact the DM must honor), then the
    # moment-specific runbook. Two newlines so the DM reads them as distinct directives.
    printf '%s\n\n%s' "$carry" "$body"
  else
    printf '%s' "$body"
  fi
}

# The runbook BODY — the moment-specific story directive (the original selector). Kept as an
# inner helper so worldos_runbook_for_beat can prepend the F04-2 carry-forward block in ONE
# place that BOTH opener paths (play.sh / play_party.sh) already route through.
_worldos_runbook_body() {
  local beat="$1" beats="$2" prev_loc="$3" state_dir="$4"
  local prog day tod visited npcs_met cur_loc cur_visited
  prog="$(worldos_read_progress "$state_dir")"
  day="$(printf '%s' "$prog" | cut -f1)";        day="${day:-1}"
  tod="$(printf '%s' "$prog" | cut -f2)";        tod="${tod:-morning}"
  visited="$(printf '%s' "$prog" | cut -f3)";    visited="${visited:-0}"
  npcs_met="$(printf '%s' "$prog" | cut -f4)";   npcs_met="${npcs_met:-0}"
  cur_loc="$(printf '%s' "$prog" | cut -f5)"
  cur_visited="$(printf '%s' "$prog" | cut -f6)"; cur_visited="${cur_visited:-0}"

  # Beat windows (integer math). Midpoint = round(beats/2); final window = last 2 beats.
  local mid=$(( (beats + 1) / 2 ))
  local final_start=$(( beats - 1 )); [ "$final_start" -lt 1 ] && final_start=1
  # "Stuck": the party's current location is UNCHANGED from the start of this beat AND we're
  # several beats in with still only one place visited. N = a couple of beats of standing still.
  local moved=1; [ -n "$cur_loc" ] && [ "$cur_loc" = "$prev_loc" ] && moved=0

  # 1) Scene-intro: brand-new location this beat (just arrived → not yet "visited"-narrated) or
  #    the very first beat. Open the place FIRST, then hand back the moment.
  if [ "$beat" -le 1 ] || { [ -n "$cur_loc" ] && [ "$cur_loc" != "$prev_loc" ] && [ "$cur_visited" = "0" ]; }; then
    printf '%s' "RUNBOOK — SCENE-INTRO (a new place, beat $beat): the party has just arrived somewhere new. BEFORE they act, YOU set this scene — look_around / get_scene first, then narrate the place's tone in your own prose (the light, the sound, who is present, what is wrong), generate_image(kind=\"scene\") for it, and put at least one named face here who SPEAKS. Then hand back the open moment. Do not wait for the player to author the room."
    return 0
  fi

  # 2) Climax / payoff: the final ~2 beats. Converge the threads; spend what was set up.
  if [ "$beat" -ge "$final_start" ] && [ "$beats" -ge 4 ]; then
    printf '%s' "RUNBOOK — CLIMAX / PAYOFF (beat $beat of $beats, the end is here): bring the arc to a head NOW — the confrontation, the reckoning, the choice that has been building. PAY OFF what Act 1 set up and what the midpoint reversal cost; let the price already paid matter. No new sub-plots. Land one decisive, dramatized moment and the consequence the player has earned — then a clean, resonant close."
    return 0
  fi

  # 3) Midpoint reversal: at ~beats/2, fire the turn — and make it COST.
  if [ "$beat" -eq "$mid" ] && [ "$beats" -ge 4 ]; then
    printf '%s' "RUNBOOK — MIDPOINT REVERSAL (beat $beat ≈ the turn): deliver the REVERSAL now, not merely 'harder'. Flip the situation — the ally is the informant, the prize is already gone, the safe path was the trap, the cost lands on the HERO personally (their own skin, bond, or secret on the line, not abstract world-stakes). Make a real attempt FAIL or a choice exact a price that STICKS and changes the scene. This is the lever the story score keeps docking — do not smooth it over."
    return 0
  fi

  # 4) Travel / peopling: the party has stood still and the world hasn't grown. Move OR people it.
  if [ "$moved" = "0" ] && [ "$beat" -ge 3 ] && { [ "$visited" -lt 2 ] || [ "$npcs_met" -lt 1 ]; }; then
    local why="the party has not moved"
    [ "$visited" -lt 2 ] && why="$why and has visited only $visited location(s)"
    [ "$npcs_met" -lt 1 ] && why="$why and NO new named NPC has entered yet (0 met)"
    printf '%s' "RUNBOOK — TRAVEL / PEOPLING (beat $beat: $why): the world must MOVE. Pick ONE and do it THIS beat through the engine — either (a) move the party to a NEW place: travel_to along a connection (advance_time=True for a real journey) or add_location(make_current=True), then narrate that new place's tone yourself; OR (b) bring a NEW named NPC on-screen: create_character with a name + a voice + at least one quoted line, mark met=True when the party meets them. A session frozen in one room with no new faces is a FAILED session — close that gap now."
    return 0
  fi

  # 5) Default — rising action between the named turns. Escalate; keep friction that sticks.
  printf '%s' "RUNBOOK — RISING ACTION (beat $beat of $beats, day $day $tod): raise the pressure a notch and keep momentum with friction that STICKS — an NPC refuses/stalls/lies/counters, a clever move has a real cost, the human-scale trouble widens. Don't grant every ask. If the scene has run its course, advance the clock or move on rather than lingering. End on a live, dramatized open moment — never a bare 'Your move.'"
}

# Honest-scoring cap. When the behavioral gate exited RED, a structurally broken/non-progressing
# run must NOT be allowed to display a glossy 4.1 — the LLM scorers grade prose and happily passed
# frozen one-scene runs. Rewrite a scorecard JSON in place: cap `overall` at 2.5, stamp
# `gate_capped=true` + `gate_status="RED"`, and prepend a critical defect explaining the cap (so
# the cap is visible and diagnosable, not silent). No-op when the file is missing/unparseable.
#
# The appended defect is now DOMAIN-GENERIC: the cap is applied to several lenses (mechanical,
# Tolkien story-craft, Angry-DM 5e-rules-fidelity), so the defect text must NOT be hard-worded for
# world-progression — capping an Angry-DM rules card with a "the party never traveled" defect is
# off-domain noise. The optional $3 picks lens-appropriate wording (default = generic).
# $1 = scorecard JSON path  $2 = a short reason string (e.g. the failed checks)
# $3 = optional domain hint: "story" (world-progression wording) | "" / anything else (generic)
worldos_cap_score_red() {
  local path="$1" reason="${2:-behavioral gate RED}" domain="${3:-}"
  [ -f "$path" ] || return 0
  python3 - "$path" "$reason" "$domain" <<'PY' 2>/dev/null || true
import json, sys
path, reason = sys.argv[1], sys.argv[2]
domain = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
CAP = 2.5
try:
    orig = float(d.get("overall"))
except (TypeError, ValueError):
    orig = None
d["gate_status"] = "RED"
d["gate_capped"] = True
d["overall_before_cap"] = orig
if orig is None or orig > CAP:
    d["overall"] = CAP
# Generic by default so the cap is domain-neutral across lenses; the "story" hint keeps the
# original world-progression phrasing for the story-craft / mechanical scorecards.
if domain == "story":
    area = "world-progression / behavioral gate"
    suggested_fix = (
        "This run FAILED the structural gate (e.g. the clock never advanced, the party never "
        "traveled, no new face entered). A long session still stuck in one Act-1 scene is a "
        "FAILURE TO PROGRESS, not a high score — overall CAPPED to 2.5 / INVALID. Fix the "
        "progression, don't polish prose inside a dead scene."
    )
else:
    area = "behavioral gate"
    suggested_fix = (
        "This run FAILED the deterministic behavioral gate, so its quality score is not "
        "trustworthy — overall CAPPED to 2.5 / INVALID. Fix the structural failure named in the "
        "gate reason before reading this scorecard; don't act on the capped number."
    )
defect = {
    "severity": "critical",
    "area": area,
    "evidence": f"behavioral gate RED ({reason}); recorded overall was {orig}",
    "suggested_fix": suggested_fix,
}
# Keep the appended defect schema-shaped for whatever lens this card is. The Angry-DM
# scorecard's defect object also requires kind/rule/five_e_says (it carries a `coverage`
# block — the reliable tell); add those so the capped card stays internally consistent.
if isinstance(d.get("coverage"), dict):
    defect.setdefault("kind", "commission")
    defect.setdefault("rule", "unverified")
    defect.setdefault("five_e_says", "A run that fails the deterministic structural gate cannot be trusted as a fair table; fix the gate failure first.")
defs = d.get("defects")
if isinstance(defs, list):
    d["defects"] = [defect] + defs
else:
    d["defects"] = [defect]
json.dump(d, open(path, "w"), indent=2)
PY
}

# Campaign Director advisory (#72): at the START of a beat, surface what the campaign OWES — an
# untracked hook to add_quest, an NPC introduced but still silent, a due consequence to land — so
# the DM is REMINDED structurally instead of relying on reach-for (the add_quest gap a QA run
# exposed). Read-only (get_campaign_director never mutates). Echoes a short DIRECTOR block for the
# DM beat prompt, or NOTHING when the campaign owes nothing / no snapshot yet. Non-fatal: a
# transient uv error -> empty (the next beat re-reads).
worldos_director_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(worldos_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
import sys
import server
try:
    r = server.get_campaign_director(sys.argv[1])
    adv = (r or {}).get("advisory") or []
    if adv:
        print("DIRECTOR — what the campaign OWES right now (weave the TOP one into THIS beat; do not recite the list):")
        for a in adv[:2]:
            print(f"- {a}")
except Exception:
    pass
PY
)"
  [ -n "$out" ] && printf '%s' "$out"
}

# Event advisory (Quest & Arc engine, Layer 3): at the START of a beat, surface the first-class
# stumble-into EVENTS whose contract-safe trigger holds NOW (a set flag, a faction's reputation
# reaching a level, a reached day — never fiction), so the DM is REMINDED to STAGE the available
# decisional instead of relying on reach-for (the same dark-surface gap the Director closed for
# add_quest before #154). MIRRORS worldos_director_advisory exactly: read-only (present_events
# never mutates), echoes a short EVENT block for the DM beat prompt, or NOTHING when no Event is
# available / no snapshot yet. Non-fatal: a transient uv error -> empty (the next beat re-reads).
worldos_event_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(worldos_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
import sys
import server
try:
    r = server.present_events(sys.argv[1])
    evs = (r or {}).get("events") or []
    if evs:
        print("EVENT AVAILABLE — a stumble-into decisional has arrived (STAGE the top one IN-CHARACTER this beat; lay out its options via the parley surface, free-form always allowed; resolve the pick with resolve_event):")
        for ev in evs[:2]:
            prompt = (ev.get("prompt") or "").strip()
            labels = ", ".join((o.get("label") or "").strip() for o in (ev.get("options") or []) if (o.get("label") or "").strip())
            line = f"- {ev.get('id')}: {prompt}" if prompt else f"- {ev.get('id')}"
            if labels:
                line += f"  [options: {labels}]"
            print(line)
except Exception:
    pass
PY
)"
  [ -n "$out" ] && printf '%s' "$out"
}

# COLD-OPEN AUTH ISOLATION (#892 follow-up) — keep the GUI .app's cold-open `claude -p` (the DM)
# off the macOS keychain and off any /Volumes (removable-disk) TCC prompt, so it runs headless
# without popping a system dialog every build.
#
# ROOT CAUSE (already diagnosed — see #892): the .app-spawned `claude -p` (a) reads its OAuth
# credential from the macOS KEYCHAIN, and because the .app is ad-hoc signed its cdhash changes on
# EVERY build → the keychain item's ACL never includes the new cdhash → macOS re-prompts for
# keychain access on every launch; and (b) touches stale `/Volumes/LEXAR/...` entries in the
# user's ~/.claude.json `projects` map → a removable-disk TCC prompt. Both block headless
# auto-testing.
#
# THE FIX (authoritative per Claude Code's `-p` auth model): run the cold-open claude with
#   (1) a credential in the ENV — in `-p` mode CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY take
#       precedence and the keychain is NEVER consulted; and
#   (2) an ISOLATED CLAUDE_CONFIG_DIR — a scratch config holding exactly `{}` (NO projects map),
#       so claude never reads the user's stale /Volumes project entries → no removable-disk prompt.
#
# GATED + ADDITIVE: this is a NO-OP unless a non-keychain credential is resolvable. With no env
# token and no secret file, it changes NOTHING — the existing Terminal path (where the keychain
# works fine interactively) is preserved byte-for-byte. Idempotent; ALWAYS returns 0 so it can
# never break a caller (it is auth plumbing, never allowed to fail a launch).
#
# Credential resolution (priority order):
#   (a) $CLAUDE_CODE_OAUTH_TOKEN or $ANTHROPIC_API_KEY already set+non-empty in the env → use as-is.
#   (b) else a secret file at ${WORLDOS_CLAUDE_TOKEN_FILE:-$HOME/.worldos/claude-token} → read+trim;
#       classify by prefix: 'sk-ant-' → ANTHROPIC_API_KEY, otherwise → CLAUDE_CODE_OAUTH_TOKEN.
# Config dir: ${WORLDOS_CLAUDE_CONFIG_DIR:-${STATE_DIR:-$PWD}/.claude-cfg} (created; minimal `{}`
# .claude.json written iff absent; exported as CLAUDE_CONFIG_DIR).
# Reads ambient $STATE_DIR (the run's state dir, when the caller has set it) for the default
# scratch config location. No args.
worldos_isolate_claude_auth() {
  local cred="" tok_file cfg_dir

  # (a) env credential already present → respect it as-is (no reclassification, no file read).
  if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    cred="env"
  else
    # (b) fall back to the on-disk secret file (gated: only if it exists + has content).
    tok_file="${WORLDOS_CLAUDE_TOKEN_FILE:-$HOME/.worldos/claude-token}"
    if [ -f "$tok_file" ]; then
      local raw
      raw="$(cat "$tok_file" 2>/dev/null)"
      # Trim surrounding whitespace/newlines (a `claude setup-token` dump often has a trailing \n).
      raw="${raw#"${raw%%[![:space:]]*}"}"
      raw="${raw%"${raw##*[![:space:]]}"}"
      if [ -n "$raw" ]; then
        case "$raw" in
          sk-ant-*) export ANTHROPIC_API_KEY="$raw" ;;
          *)        export CLAUDE_CODE_OAUTH_TOKEN="$raw" ;;
        esac
        cred="file"
      fi
    fi
  fi

  # NO credential resolved → true NO-OP. Do NOT set CLAUDE_CONFIG_DIR, do NOT export anything.
  # This preserves today's working Terminal path (the keychain works fine interactively).
  if [ -z "$cred" ]; then
    echo "[auth] no env/file credential — using default (keychain) auth" >&2
    return 0
  fi

  # A credential is in the env → also isolate the config dir so claude never reads the user's
  # stale /Volumes project entries (no removable-disk TCC prompt). Idempotent: re-running just
  # re-points at the same dir; the `{}` is only written when absent.
  cfg_dir="${WORLDOS_CLAUDE_CONFIG_DIR:-${STATE_DIR:-$PWD}/.claude-cfg}"
  mkdir -p "$cfg_dir" 2>/dev/null || true
  if [ ! -f "$cfg_dir/.claude.json" ]; then
    printf '%s\n' '{}' > "$cfg_dir/.claude.json" 2>/dev/null || true
  fi
  export CLAUDE_CONFIG_DIR="$cfg_dir"
  echo "[auth] isolated CLAUDE_CONFIG_DIR + env credential (no keychain / no /Volumes)" >&2
  return 0
}
