# shellcheck shell=bash
# Shared beat-driver helpers for the WorldOS play loops (qa/run_duo.sh + scripts/play.sh).
#
# This is the STRUCTURE behind the "living, progressing world" fix (decision-dm-driver.md):
# prose nudges alone never moved the clock past day 1, never visited >1 location, and NEVER
# created an on-screen NPC across 57 campaigns. So the harness now drives progression
# structurally, in two pieces — both routing through the ENGINE so it stays the SOLE WRITER
# of snapshot.json (we never hand-edit state):
#
#   C — soft clock-tick backstop (clawdnd_soft_tick): after a DM beat, if the in-world clock
#       did NOT advance this beat, advance ONE time-of-day phase via the engine's advance_time.
#       Defers to the DM's own pacing when it advances time in-fiction; only fires when frozen.
#
#   A — beat-aware runbook injection (clawdnd_runbook_for_beat): instead of the SAME constant
#       "keep the world moving" paragraph every beat (noise the model learns to skim), emit ONE
#       moment-specific runbook chosen from the beat index + the live snapshot (location/time/
#       visited/peopling): scene-intro on a new place, travel/peopling when stuck, the midpoint
#       reversal at ~beats/2, the climax/payoff in the final ~2 beats.
#
# Sourced by BOTH loops so the two harnesses can't drift. Pure bash + a tiny `uv run` python
# shim into servers/engine (the engine's venv has the deps; bare python3 does not).

# --- WorldOS rename env-compat (issue #295, W0-E) ---------------------------------
# Resolve an env var by suffix, preferring WORLDOS_<suffix> and falling back to the
# legacy CLAWDND_<suffix> (one-time stderr deprecation warning), else a default.
#   worldos_env DM_MODEL sonnet   ->  $WORLDOS_DM_MODEL, else $CLAWDND_DM_MODEL, else "sonnet"
# Mirrors servers/*/_env.py for the shell side; both names resolve for v1.x.
# Note: worldos_env is typically called inside $(...) (a forked subshell), so an
# in-memory "warned" flag wouldn't survive between calls. We key the once-warning off
# a tiny per-(invocation, var) sentinel file under $TMPDIR so it stays one-time across
# the subshells of a single script run (PPID = the script's pid from the subshell).
worldos_env() {
  local suffix="$1" default="${2:-}"
  local w="WORLDOS_${suffix}" c="CLAWDND_${suffix}"
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
# clawdnd_live_campaign_id below (the engine's authoritative most-recently-played save), NOT this.
clawdnd_snapshot_path() {
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
# the old per-harness guesses (clawdnd_snapshot_path = largest, or `find … | head -1` = first dir)
# that the #640 A/B proved could select the wrong campaign. Read-only (active_campaign never
# mutates). Echoes the campaign id, or NOTHING when no campaign exists / the engine errors — in
# which case the caller's lean branch no-ops (clawdnd_dm_lean_args returns 0 on an empty id) and
# the normal --resume path is used (no regression). $1 = ROOT (repo root)  $2 = STATE_DIR
# $3 = world_id (optional; scopes the resolution to the launched world)
clawdnd_live_campaign_id() {
  local root="$1" state_dir="$2" world="${3:-}"
  [ -d "$state_dir/campaigns" ] || return 0
  WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" \
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
clawdnd_dm_narration_or_fallback() {
  local reply="$1" state_dir="$2" snap
  # Non-empty (after trimming whitespace) → the DM ended on prose; use it verbatim.
  if [ -n "${reply//[[:space:]]/}" ]; then
    printf '%s' "$reply"
    return 0
  fi
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || { printf '%s' "$reply"; return 0; }
  # Read the tail of the active session log and stitch the most recent contiguous run of
  # player-facing entries (narration | dialogue) into the prose for this missing beat (see
  # qa/dm_narration_fallback.py). It is a standalone script (NOT a heredoc-in-command-sub: the
  # macOS system bash 3.2 mis-parses a quoted heredoc nested in $(...), which is why every other
  # such call here is one too). Bounded; skips roll/system/combat rows. Empty when no such prose.
  local recovered fb_py="${CLAWDND_LIB_DIR:-$(dirname "${BASH_SOURCE[0]}")}/dm_narration_fallback.py"
  recovered="$(python3 "$fb_py" "$snap" 2>/dev/null)"
  if [ -n "${recovered//[[:space:]]/}" ]; then
    printf '%s' "$recovered"
  else
    printf '%s' "$reply"   # nothing to recover → unchanged (no regression)
  fi
}

# LEAN-BEAT DM-TURN ARGS (the ONE shared implementation of the CLAWDND_LEAN_BEATS path).
#
# Both play loops (scripts/play.sh AND qa/run_duo.sh) drive a DM turn through `claude -p`,
# normally `--resume`-ing the DM's growing session every beat (which REPLAYS the whole
# transcript → prefill grows ~6–10K tok/beat → the late-session slowdown a narrative persona
# quit over). With CLAWDND_LEAN_BEATS=1, continuing beats (NOT the cold open) instead start a
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
#   CLAWDND_DM_LEAN_SESSION  — () when NOT lean (caller keeps its own --resume/--session-id),
#                              else (--session-id <fresh-uuid>) for a transcript-free turn.
#   CLAWDND_DM_LEAN_EXTRA    — () when NOT lean, else (--append-system-prompt "<directive>").
# Lean fires ONLY when: first = 0 (a CONTINUING beat)  AND  $CLAWDND_LEAN_BEATS = 1  AND
# campaign_id is non-empty. The cold open (first != 0) or an unknown campaign id falls through
# to the caller's normal resume path — byte-identical to today when the flag is off.
# CONVENTION (both harnesses): first=1 ⇒ the cold open that mints the session; first=0 ⇒ a
# continuing beat that would otherwise --resume it. Lean replaces that --resume on continuing
# beats with a fresh transcript-free session — so the firing condition is first=0, NOT first!=0.
# (scripts/play.sh's old inline lean used `first != 0`, which — combined with campaign_id only
# being known on continuing beats — meant its lean branch NEVER actually fired; this shared
# helper restores the documented intent: lean on beats 2+, full cold open. See PR notes.)
# $CLAWDND_LEAN_TAIL ($3, default 8) is the recent-narration depth the re-ground asks for.
# $1 = first?(1/0)  $2 = campaign_id (may be empty)  $3 = lean_tail (optional; default 8)
clawdnd_dm_lean_args() {
  local first="$1" campaign_id="${2:-}" lean_tail="${3:-8}"
  CLAWDND_DM_LEAN_SESSION=()
  CLAWDND_DM_LEAN_EXTRA=()
  # The cold open (first != 0), flag explicitly off (CLAWDND_LEAN_BEATS=0), or no campaign to
  # re-ground against → no-op (caller's existing --resume/--session-id path is used unchanged).
  # Lean fires on CONTINUING beats only. DEFAULT is now lean-ON (:-1): lean is standard, and
  # CLAWDND_LEAN_BEATS=0 is the explicit opt-out (matches the default flipped in both harnesses).
  if [ "$first" != "0" ] || [ "${CLAWDND_LEAN_BEATS:-1}" != "1" ] || [ -z "$campaign_id" ]; then
    return 0
  fi
  # LEAN beat: fresh session, no transcript replay. Re-ground from persisted truth.
  CLAWDND_DM_LEAN_SESSION=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
  CLAWDND_DM_LEAN_EXTRA=(--append-system-prompt "LEAN RE-GROUND (this turn has NO prior conversation transcript — by design, to keep your turn fast). You are mid-campaign, NOT starting over. Your FIRST action this turn MUST be clawdnd-engine scene_context(campaign_id=\"$campaign_id\", recent_narration=$lean_tail). That one call returns the campaign's CANON, and it is your whole memory for this beat — HONOR all of it as canon YOU already authored:
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
# This is keyed off the SAME `first` signal the lean branch uses (clawdnd_dm_lean_args), so the
# cold open is always full+max and the long tail is lean+medium — the two levers stay in lock-step.
#
# Levels are env-overridable (e.g. bump the routine tier back to high for a quality A/B, or drop
# the cold open to high to save cost) via the same WORLDOS_/CLAWDND_ resolution as everything else:
#   WORLDOS_DM_EFFORT_COLDOPEN (default max)    — the cold open's effort
#   WORLDOS_DM_EFFORT_ROUTINE  (default medium) — every continuing beat's effort
# Valid claude levels: low|medium|high|xhigh|max (a bad override is passed through verbatim and the
# CLI validates it). Applies ONLY to the DM turn — the player/actor facade never gets --effort.
#
# Like the lean helper, bash 3.2 has no namerefs, so this POPULATES a well-known GLOBAL array the
# caller splices into its claude -p invocation (never empty — every DM turn gets an explicit tier):
#   CLAWDND_DM_EFFORT — (--effort max) on the cold open, (--effort medium) on continuing beats.
# $1 = first?(1/0)
clawdnd_dm_effort_arg() {
  local first="$1" level
  if [ "$first" != "0" ]; then
    # Cold-open effort is model-aware. Opus's max-effort world-build is generation-bound and overruns
    # the cold-open timeout (measured 2026-06-06: Opus --effort max never finishes <400s; --effort HIGH
    # finishes ~300s WITH a full, BG-caliber opening). Opus-high ≈ Sonnet-max world-build quality but
    # lands in time, so Opus defaults to high; Sonnet keeps max (it finishes in ~280–400s at max).
    local _co_default=max
    case "${CLAWDND_DM_MODEL:-}" in *opus*) _co_default=high ;; esac
    level="$(worldos_env DM_EFFORT_COLDOPEN "$_co_default")"
  else
    level="$(worldos_env DM_EFFORT_ROUTINE medium)"
  fi
  CLAWDND_DM_EFFORT=(--effort "$level")
}

# DM-TURN TIMEOUT TIER (the ONE shared implementation of the cold-open-vs-routine timeout split).
#
# Both play loops wrap the DM's `claude -p` in `timeout <secs>` + ONE retry, so a wedged turn
# recovers instead of hanging the session. But the two kinds of beat need very different deadlines,
# for the SAME reason the effort tier splits them (clawdnd_dm_effort_arg, keyed off `first`):
#   • the COLD OPEN (first != 0) is the one-time, --effort max, full world-build — generate the
#     world, scene, PC, opening NPCs + portraits. That MAX-EFFORT cold open routinely runs ~280–400s;
#     the routine 200s deadline KILLS it mid-build (the masked "cold-open reproducibly broken" mode),
#     so the cold open gets a generous deadline: WORLDOS_COLDOPEN_TIMEOUT (default 400s).
#   • CONTINUING / routine beats (first = 0) are --effort medium and resolve one move against
#     established canon — fast — so they keep the existing per-beat deadline CLAWDND_BEAT_TIMEOUT
#     (default 200s), unchanged. (Routine behavior is byte-identical to today.)
# Keyed off the SAME `first` signal as the effort + lean levers, so cold-open=full+max+400s and the
# long tail=lean+medium+200s stay in lock-step. Applies ONLY to the DM turn (player/companion turns
# are never wrapped in a per-beat timeout at all).
#
# Both knobs resolve through the same WORLDOS_/CLAWDND_ fallback as everything else (worldos_env):
#   WORLDOS_COLDOPEN_TIMEOUT (default 400) — the cold open's deadline, in seconds.
#   CLAWDND_BEAT_TIMEOUT     (default 200) — every continuing beat's deadline (today's knob, kept).
# Note: CLAWDND_BEAT_TIMEOUT keeps its CLAWDND_ name (it predates the WorldOS rename and is the
# documented routine knob); only the NEW cold-open knob takes the WORLDOS_ name. worldos_env still
# honors a WORLDOS_BEAT_TIMEOUT override of the routine tier for forward-compat.
#
# Unlike the effort/lean helpers (which populate an array spliced into argv), this just ECHOES the
# resolved seconds — the caller uses it as the scalar `timeout <secs>` argument. $1 = first?(1/0)
clawdnd_dm_timeout() {
  local first="$1"
  if [ "$first" != "0" ]; then
    # Cold-open deadline is model-aware. Opus-high cold-open measured ~300s; give it margin (500s) for
    # per-world/per-run variance so it is never killed mid-build. Sonnet keeps 400s (max runs ~280–400s).
    local _co_timeout=400
    case "${CLAWDND_DM_MODEL:-}" in *opus*) _co_timeout=500 ;; esac
    worldos_env COLDOPEN_TIMEOUT "$_co_timeout"
  else
    worldos_env BEAT_TIMEOUT 200
  fi
}

# RE-MINT SESSION ON RETRY (the ONE shared implementation of "never reuse a CONSUMED session id").
# A `claude -p` attempt that fails AFTER startup STILL registered its --session-id on disk
# (~/.claude/projects/<proj>/<uuid>.jsonl), so a retry that re-passes that SAME --session-id dies
# "Session ID <uuid> is already in use." → 0-byte output → empty narration → the cold open never
# completes (the masked failure mode behind the 2026-06-02 "reproducibly broken cold-open" reports:
# attempt 1 actually 401'd, but the retry's session collision is all that reached dm.err). The LEAN
# path already side-steps this (clawdnd_dm_lean_args mints a fresh uuid every call); the COLD-OPEN /
# legacy --resume path passes a STABLE $DSID and so MUST be re-minted before its retry. Given the
# resume-mode the prior attempt used (the caller's `resume` array, passed as "$@"), populate the
# well-known global CLAWDND_DM_RETRY_SESSION:
#   • prior mode --session-id (a CREATE) → (--session-id <fresh-uuid>)  — retry on a BRAND-NEW session.
#   • prior mode --resume <id>           → ()  — resuming an already-created session on retry is safe;
#                                               leave the caller's --resume untouched.
# Caller contract: call this ONLY when the lean helper did NOT fire (lean re-mints itself), so each
# path mints exactly once. Bash 3.2: no namerefs — inspect args by value + populate a global (mirrors
# clawdnd_dm_lean_args / clawdnd_dm_effort_arg). $@ = the caller's current `resume` array tokens.
clawdnd_dm_remint_session_on_retry() {
  CLAWDND_DM_RETRY_SESSION=()
  if [ "${1:-}" = "--session-id" ]; then
    CLAWDND_DM_RETRY_SESSION=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
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
clawdnd_report_attempt_failure() {
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

# Read the run's progression facts from the snapshot in ONE python pass. Echoes a single
# TAB-separated line:  day <TAB> time_of_day <TAB> visited_count <TAB> npcs_met <TAB>
# current_location_id <TAB> current_location_visited(0/1) <TAB> combat_active(0/1)
# (combat_active is field 7, appended additively — fields 1-6 are unchanged for callers that
# cut -f1..6). Echoes nothing when there's no snapshot yet (pre-first-beat). Read-only.
# $1 = STATE_DIR
clawdnd_read_progress() {
  local snap; snap="$(clawdnd_snapshot_path "$1")"
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

# The C backstop. After a DM beat, compare the live clock to the clock BEFORE the beat; if the
# DM did NOT advance it this beat, advance ONE phase through the engine (advance_time(phases=1))
# so the day cannot freeze at morning. Engine stays the sole writer. No-op (and silent) until a
# snapshot exists. Echoes a short "[tick] …" status to stderr for the run log.
# $1 = ROOT (repo root)  $2 = STATE_DIR  $3 = prev_day  $4 = prev_tod
clawdnd_soft_tick() {
  local root="$1" state_dir="$2" prev_day="$3" prev_tod="$4"
  local snap cur cur_day cur_tod cur_combat
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  cur="$(clawdnd_read_progress "$state_dir")"
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
  # parent dir name; CLAWDND_STATE_DIR scopes the engine to THIS run's state tree. We capture
  # the engine's output (status or error) and echo it to STDERR for the run log — we do NOT
  # blanket-suppress, so a real engine failure is visible. On a contended host `uv` can return a
  # transient cache error; that's non-fatal here (the NEXT beat re-reads the clock and re-ticks),
  # so we never let the tick's exit status fail the loop (the function always returns 0).
  local camp out; camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>&1 <<'PY'
import sys
import server
camp = sys.argv[1]
try:
    r = server.advance_time(camp, phases=1, note="harness soft clock-tick backstop")
    print(f"[tick] clock frozen this beat -> engine advanced to day {r.get('day')} {r.get('time_of_day')}")
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
# $1 = beat (1-based)  $2 = total beats  $3 = prev_location_id (loc at beat start)  $4 = STATE_DIR
clawdnd_runbook_for_beat() {
  local beat="$1" beats="$2" prev_loc="$3" state_dir="$4"
  local prog day tod visited npcs_met cur_loc cur_visited
  prog="$(clawdnd_read_progress "$state_dir")"
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
clawdnd_cap_score_red() {
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
clawdnd_director_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
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
# add_quest before #154). MIRRORS clawdnd_director_advisory exactly: read-only (present_events
# never mutates), echoes a short EVENT block for the DM beat prompt, or NOTHING when no Event is
# available / no snapshot yet. Non-fatal: a transient uv error -> empty (the next beat re-reads).
clawdnd_event_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
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
