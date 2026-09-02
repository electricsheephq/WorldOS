#!/usr/bin/env bash
# Shared ADVENTURE-DM beat primitives — the ONE implementation used by BOTH
# qa/run_adventure.sh (DM + scripted player agent) and qa/agent_play.sh (DM only; an external
# agent/human is the player). Extracted verbatim from run_adventure.sh so the two runners can
# never drift on the hermetic env, the DM brief, the per-beat claude invocation, the retry
# policy, or the quest telemetry contract.
#
# Ambient globals the callers must define BEFORE calling (same contract lib_beat_driver.sh uses):
#   $ROOT $T $RUN $STATE_DIR $CAMPAIGN_ID $QUEST_TITLE $TRACE $DM_CFG $COMBINED $BUDGET
#   $WORLDOS_DM_MODEL $WORLDOS_LEAN_TAIL $ADV_TELEMETRY_FAIL_FILE
# Optional: $ADV_LOG_TAG (the `[tag]` stderr prefix; default "adventure" == run_adventure's text).
#
# Requires qa/lib_beat_driver.sh to already be sourced (worldos_dm_* helpers).
#
# BASH 3.2 SAFETY: adv_dm_write_mcp_config carries a quoted heredoc — call it DIRECTLY, never
# inside $(...). adv_dm_brief / adv_dm_turn / adv_dm_turn_retry / adv_quest_poll are heredoc-free
# and safe in command substitution (that is how they are used).

# ── HERMETIC SESSIONS (#1656 root cause) ───────────────────────────────────────────────────────────
# The DM/player `claude -p` sessions previously inherited the USER-level ~/.claude config — including
# the claude-mem plugin, whose SessionStart hook injects OLD WorldOS session observations. Measured on
# adv_live2: the FIRST DM session carried 39 claude-mem refs incl. a FOREIGN campaign's beats, and the
# DM ultimately "closed" THAT story mid-fight ("the standoff at the bonesetter's door"). Both live
# runs died this way at depth ~7. Fix = the #1260-proven isolation: a per-run CLAUDE_CONFIG_DIR with
# empty settings (no user plugins/hooks/CLAUDE.md) + an explicit keychain OAuth token. The repo plugin
# and MCP servers are unaffected (flag-scoped: --plugin-dir / --mcp-config --strict-mcp-config).
# Sets the GLOBALS DUO_CFG / DUO_TOK / DUO_ENV / WORLDOS_LEAN_TAIL. No args.
adv_dm_hermetic_env() {
  local _blob
  DUO_CFG="$(mktemp -d "${TMPDIR:-/tmp}/worldos-duo-config.XXXXXX")"
  printf '{}' > "$DUO_CFG/settings.json"
  DUO_TOK="${CLAUDE_CODE_OAUTH_TOKEN:-}"
  if [ -z "$DUO_TOK" ] && [ "$(uname)" = "Darwin" ]; then
    _blob="$(security find-generic-password -s 'Claude Code-credentials' -a "$USER" -w 2>/dev/null || true)"
    [ -n "$_blob" ] && DUO_TOK="$(printf '%s' "$_blob" | python3 -c 'import json,sys
try: d=json.load(sys.stdin).get("claudeAiOauth",{})
except Exception: d={}
sys.stdout.write(d.get("accessToken") or "")' 2>/dev/null || true)"
  fi
  # Hermetic prefix for every duo claude -p: isolated config, no inherited SDK session markers.
  # Must be an ARRAY headed by env (a real executable), not a shell function: worldos_timeout execs
  # the timeout(1) binary, which cannot exec a function (rc=127 on the DM path, adv_live3 beat 0).
  DUO_ENV=(env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN
           -u CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH -u CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH -u CLAUDE_CODE_SESSION_ID
           CLAUDE_CONFIG_DIR="$DUO_CFG")
  [ -n "${DUO_TOK:-}" ] && DUO_ENV+=(CLAUDE_CODE_OAUTH_TOKEN="$DUO_TOK")
  WORLDOS_LEAN_TAIL="${WORLDOS_LEAN_TAIL:-8}"
  return 0
}

# Write the DM's MCP config (the engine facade, state dir + tooltiming patched in).
# $1 = repo root  $2 = state dir  $3 = out path  $4 = tooltiming jsonl path
adv_dm_write_mcp_config() {
  python3 - "$1/qa/qa.mcp.example.json" "$2" "$3" "$1" "$4" <<'PY'
import json, sys, os
cfg_path, state, out, root, tooltiming = sys.argv[1:6]
cfg = json.load(open(cfg_path))
for name, srv in cfg.get("mcpServers", {}).items():
    args = srv.get("args", [])
    if "--directory" in args:
        i = args.index("--directory"); raw = args[i + 1].rstrip("/")
        if raw.startswith("./"): raw = raw[2:]
        if "/servers/" in raw: pkg = raw.rsplit("/servers/", 1)[1]
        elif raw.startswith("servers/"): pkg = raw[len("servers/"):]
        else: pkg = raw
        args[i + 1] = f"{root}/servers/{pkg}"
    if name == "worldos-engine":
        srv.setdefault("env", {})["WORLDOS_STATE_DIR"] = state
        srv["env"]["WORLDOS_TOOLTIMING_PATH"] = tooltiming
        if os.environ.get("WORLDOS_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
PY
}

# The DM brief = the shared duo brief + a short ARC ADDENDUM (the ONLY brief-plumbing run_duo
# exposes is `cat qa/play_dm_duo.txt`; we CONCATENATE the addendum, we do not fork the brief).
# $1 = campaign id  $2 = quest title ; echoes the brief.
# ARC MODE brief filter. The shared duo brief obliges the DM to make "new named faces enter and
# speak" — correct for an emergent duo session, WRONG for a seeded arc: all three failed Opus-5 arc
# runs opened by minting a camp NPC with a missing brother and walking them into the crypt as a
# second body to lose, while the passing opus-4-8 control created no opening NPC at all
# (session-notes 2026-09-02 DM-DEVIATIONS). The edits are EXACT-MATCH and FAIL LOUD: if the shared
# brief is reworded and a clause stops matching, the run aborts rather than silently shipping the
# un-suppressed obligation. Heredoc-free (the python is a `-c` argument), so this stays safe inside
# the $(...) that adv_dm_brief is called in. Echoes the (possibly filtered) brief.
adv_dm_duo_brief() {
  local f="$ROOT/qa/play_dm_duo.txt"
  [ "${WORLDOS_ARC_MODE:-0}" = "1" ] || { cat "$f"; return 0; }
  python3 -c 'import sys
src = open(sys.argv[1], encoding="utf-8").read()
for i in range(2, len(sys.argv), 2):
    old, new = sys.argv[i], sys.argv[i + 1]
    if src.count(old) != 1:
        raise SystemExit("arc-mode brief filter: expected exactly 1 match for [" + old[:48] +
                         "], found " + str(src.count(old)) +
                         " -- qa/play_dm_duo.txt was reworded; update adv_dm_duo_brief in qa/lib_adventure_dm.sh")
    src = src.replace(old, new)
sys.stdout.write(src)' \
    "$f" \
    "the session obligations you OWN (the clock advances; the party travels to ≥2 locations; new named faces enter and speak)" \
    "the session obligations you OWN (the clock advances; the party travels to ≥2 locations; the cast already on the table speaks)" \
    "and new named NPCs enter and SPEAK — a whole session spent in the opening location at the opening hour with no new faces is a FAILED session that flips the gate RED" \
    "and the characters already in the scene SPEAK — a whole session spent in the opening location at the opening hour is a FAILED session that flips the gate RED"
}

adv_dm_brief() {
  local campaign_id="$1" quest_title="$2" base
  base="$(adv_dm_duo_brief)" || return 1
  printf '%s' "$base
ARC ADDENDUM (this is a PRE-SEEDED adventure, NOT a world you build): the world already exists in
engine state as campaign \"$campaign_id\" — do NOT start_world / start_adventure / create the map.
GROUND on it: call get_state(\"$campaign_id\"), look_around(\"$campaign_id\"), and get_quests(\"$campaign_id\")
at the start of every beat. The map: a camp clearing (the party's start) with a door to Keeper Maera's
tavern (the quest giver; a merchant is one room further) and a door DOWN into a goblin-infested crypt
that opens onto a throne hall where the Goblin Boss waits. The quest \"$quest_title\" has four
objectives — Speak with Keeper Maera, Clear the crypt of goblins, Slay the goblin boss, Return to
Maera for the reward. As the party achieves each, call complete_objective so the engine records it
(the last one auto-resolves the quest and hands over the reward via complete_quest). Run real combat
in the crypt and the throne hall THROUGH THE ENGINE, and CLOSE it — an unclosed fight is this arc's
#1 failure mode: it eats the whole beat budget and flips the behavioral gate RED before the boss is
ever reached. COMBAT-CLOSURE DISCIPLINE, non-negotiable and enforced by the QA gate:
  (1) DRIVE EVERY ATTACK THROUGH THE ACTION ECONOMY, not prose — start_combat on the foe ids, then
      each round resolve the strikes with attack() (or cast_spell / use_action) and advance with
      next_turn. Narrating 'you cut the goblin down' with no attack() call leaves action_used=False
      and 0 attacks (an action_economy WARN): the blow never landed in engine state.
  (2) THE BEAT THE LAST HOSTILE DROPS, CALL end_combat(resolution='...'). The engine surfaces a LOUD
      pending_resolution nudge in the combat state the moment no living hostile remains — obey it.
      end_combat auto-awards the defeated foes' XP in xp mode (so a clean fight needs no separate
      award_xp); a fight left active is a combat_ended + xp_awarded WARN and its XP never lands.
  (3) ADVANCE THE CLOCK after significant beats — a resolved fight, a cleared room, reaching the
      throne hall — via advance_time / long_rest / travel_to(advance_time=True). An arc where the
      clock never moves is a dm_advanced_time WARN.
THE SEEDED-ARC RULES, each one enforced by the QA gate as a hard FAIL:
  (A) THE ONLY HOSTILE CREATURES IN THIS WORLD ARE THE SEEDED ONES: three Goblin Warriors in the
      crypt and the Goblin Boss in the throne hall. Never spawn_monster a species the seed does not
      contain — no undead, no hobgoblins, no wights, nothing from the bestiary that is not already
      here. Nothing hostile is on the road, in the camp, or in the tavern; those are safe rooms.
  (B) THE REVERSAL IS A PRICE, NOT A FIGHT: a betrayal, a lost item, a broken promise, a time cost,
      a locked way back — never a new fight and never a new creature. It fires only after the crypt
      is cleared (objective 2) or at the true midpoint, whichever is LATER.
  (C) NEVER TAKE THE PC BELOW 1 HP BEFORE THE CRYPT IS CLEARED, AND NEVER SEAT A REPLACEMENT PC:
      no reroll_character, and never offer the player a new hero — not as a table note, not as a
      kindness. If the PC drops, an NPC stabilises them INSIDE THE SAME BEAT and the scene goes on.
  (D) REACH KEEPER MAERA BY BEAT 3 AND THE THRONE HALL BY BEAT 6. Do NOT add_location: the crypt
      connects DIRECTLY to the seeded throne_hall, and the Goblin Boss never leaves that room — do
      not stage him in the crypt, do not build a second hall for him, travel the party to him.
  (E) ONE COMBAT AT A TIME. Call end_combat ONLY when the engine reports zero living hostiles — a
      result carrying warning_live_hostiles means the fight is NOT over: finish it in that beat
      rather than re-spawning the survivors later.
Keep the arc MOVING toward the crypt and the boss; the player is here to finish this job, not to
linger. The player is the seeded PC already in the party — do NOT seat a new character."
}

# ONE DM beat. $1=session id  $2=first?(1/0)  $3=message ; echoes the reply text.
adv_dm_turn() {
  local sid="$1" first="$2" msg="$3" out resume=() extra=() rc=0
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  worldos_dm_lean_args "$first" "$CAMPAIGN_ID" "$WORLDOS_LEAN_TAIL"
  if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then resume=("${WORLDOS_DM_LEAN_SESSION[@]}"); extra=("${WORLDOS_DM_LEAN_EXTRA[@]}"); fi
  worldos_dm_effort_arg "$first"
  out="$T/$RUN.dm.$(date +%s%N).jsonl"
  local beat_timeout; beat_timeout="$(worldos_dm_timeout "$first")"
  worldos_stream_flag_arg
  worldos_stream_tailer_start "$out" "$STATE_DIR"
  worldos_timeout "$beat_timeout" \
    "${DUO_ENV[@]}" claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      ${WORLDOS_STREAM_FLAG[@]+"${WORLDOS_STREAM_FLAG[@]}"} \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
  rc=$?
  worldos_stream_tailer_stop
  cat "$out" >> "$COMBINED"
  if [ "$rc" -ne 0 ] && ! worldos_dm_result_is_error "$out"; then worldos_report_attempt_failure "$out" "$rc"; fi
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# transient-aware empty-output retry (mirrors run_duo). $1=sid $2=first? $3=message.
adv_dm_turn_retry() {
  local sid="$1" first="$2" msg="$3" tag="${ADV_LOG_TAG:-adventure}"
  local r attempt max="${WORLDOS_DM_MAX_ATTEMPTS:-4}" last_out last_rc transient
  worldos_dm_prebeat_mark "$STATE_DIR"
  r="$(adv_dm_turn "$sid" "$first" "$msg")"; attempt=1
  while [ -z "$r" ] && [ "$attempt" -lt "$max" ]; do
    last_out="$(cat "$STATE_DIR/.dm_last_result" 2>/dev/null | tail -n1)"
    last_rc="$(cat "$STATE_DIR/.dm_last_rc" 2>/dev/null | tail -n1)"; last_rc="${last_rc:-0}"
    transient=0; worldos_dm_failure_is_transient "$last_out" "$last_rc" && transient=1
    if [ "$transient" != "1" ] && [ "$attempt" -ge 2 ]; then echo "[$tag] empty (dm) — REAL failure; stop retry." >&2; break; fi
    if [ "$transient" = "1" ]; then echo "[$tag] empty (dm) — transient; retry $((attempt+1))/${max}…" >&2; worldos_dm_retry_backoff "$attempt"; else echo "[$tag] empty (dm) — retry once…" >&2; fi
    if [ "$first" = "1" ]; then
      worldos_dm_remint_session_on_retry --session-id "$sid"; local _fresh="$sid"
      [ "${#WORLDOS_DM_RETRY_SESSION[@]}" -ge 2 ] && _fresh="${WORLDOS_DM_RETRY_SESSION[1]}"
      r="$(adv_dm_turn "$_fresh" "$first" "$msg")"
    else r="$(adv_dm_turn "$sid" "$first" "$msg")"; fi
    attempt=$((attempt + 1))
  done
  printf '%s' "$r"
}

# ── quest telemetry (between beats) ─────────────────────────────────────────────────────────────
# Prints the quest status on stdout ("active"/"completed"/...) and stamps the trace, reading the
# machine-contract last line `quest_status=<s>` from qa/quest_progress.py.
#
# FAIL-OPEN, BUT LOUD (items 16 + 18): telemetry must NEVER abort a run (a dead poll must not burn
# the LLM budget). But a silent fail-open was the trap — an empty capture was treated as "active" and
# the loop ran on. So we VALIDATE the contract: the uv invocation must exit 0 AND the captured last
# line must be `quest_status=…`. On violation: warn visibly, COUNT the failure, and return EMPTY
# with rc=1 — the beat loop still maps empty→active (never aborts).
adv_telemetry_note_fail() {
  local n; n="$(cat "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || echo 0)"; n="${n:-0}"
  echo $((n + 1)) > "$ADV_TELEMETRY_FAIL_FILE"
}
adv_quest_poll() {
  local beat="$1" rc out status tag="${ADV_LOG_TAG:-adventure}"
  # Run the poll to a capture file (NOT through a `$(pipe)`) so the rc is the REAL uv rc and
  # a subshell can't swallow it. Mirror stdout into the quest.log as before.
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python "$ROOT/qa/quest_progress.py" \
      "$STATE_DIR" "$CAMPAIGN_ID" --beat "$beat" --trace "$TRACE" --quest-title "$QUEST_TITLE" \
      >"$STATE_DIR/.quest_poll.out" 2>>"$T/$RUN.quest.err"
  rc=$?
  cat "$STATE_DIR/.quest_poll.out" >> "$T/$RUN.quest.log" 2>/dev/null || true
  out="$(tail -n1 "$STATE_DIR/.quest_poll.out" 2>/dev/null)"
  if [ "$rc" -ne 0 ] || [ "${out#quest_status=}" = "$out" ]; then
    adv_telemetry_note_fail
    echo "[$tag] WARN beat $beat: quest telemetry unparseable (uv rc=$rc; missing quest_status= contract line; see $T/$RUN.quest.err) — treating as active" >&2
    printf '%s' ""
    return 1
  fi
  status="${out#quest_status=}"
  printf '%s' "$status"
  return 0
}
