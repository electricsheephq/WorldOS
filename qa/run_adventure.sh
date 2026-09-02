#!/usr/bin/env bash
# ARC-DIRECTED WorldOS adventure eval (A-series A-T). A derivative of qa/run_duo.sh: two
# gateway-free `claude -p` sessions (a DM agent + a purposeful PLAYER agent) play against each
# other, mediated only by the shared engine state. The difference from run_duo is the SPINE:
#
#   * the world is NOT built by the DM cold-open — it is SEEDED once, deterministically, by
#     qa/seed_adventure_demo.py (the one-call Diablo-1 quest-loop fixture: camp <-> tavern
#     (Keeper Maera) <-> shop, camp <-> crypt (goblins) <-> throne hall (the Goblin Boss),
#     the 4-objective quest "The Crypt Below"). The campaign id is FIXED: adventure_demo_v1.
#   * the player pursues that ONE quest to COMPLETION (qa/play_player_adventure.txt) — speak to
#     Maera, travel to the crypt, clear the goblins, slay the boss, return for the reward.
#   * the run is arc-directed with a ~20-beat budget and a COMPLETION SHORT-CIRCUIT: after each
#     beat qa/quest_progress.py stamps the arc stages into <run>.quest_trace.json and reports the
#     quest status; the moment the quest leaves "active" the loop stops (a completed loop needs no
#     filler beats). qa/adventure_eval.py aggregates N such runs.
#
# Usage:   qa/run_adventure.sh <run-id> [beats] [budget] [player-persona]
#          qa/run_adventure.sh --help
#          qa/run_adventure.sh <run-id> --dry-run   # seed + wire + poll, NO claude (smoke path)
# Example: qa/run_adventure.sh adv1 20 4.00 qa/play_player_adventure.txt
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}
case "${1:-}" in
  -h|--help|help) usage; exit 0 ;;
  "") echo "[adventure] missing <run-id>. Try --help." >&2; exit 2 ;;
esac

# Shared beat-driver helpers (the SAME implementation run_duo/play use, so they can't drift).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="$1"; shift
BEATS="20"; BUDGET="4.00"; PLAYER_PROMPT_FILE="qa/play_player_adventure.txt"; DRY_RUN=0
# Positional [beats] [budget] [persona] with a --dry-run/-n flag accepted anywhere.
_pos=()
for a in "$@"; do
  case "$a" in
    -n|--dry-run) DRY_RUN=1 ;;
    *) _pos+=("$a") ;;
  esac
done
[ "${#_pos[@]}" -ge 1 ] && BEATS="${_pos[0]}"
[ "${#_pos[@]}" -ge 2 ] && BUDGET="${_pos[1]}"
[ "${#_pos[@]}" -ge 3 ] && PLAYER_PROMPT_FILE="${_pos[2]}"

CAMPAIGN_ID="adventure_demo_v1"   # FIXED by the seeder (its docstring guarantees this id)
SEEDER="$ROOT/qa/seed_adventure_demo.py"   # ABSOLUTE — the seeder's uv --directory contract
QUEST_TITLE="The Crypt Below"
EX_TEMPFAIL=75
CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# ── Root + IS_SANDBOX preflight (same real beat-0 blocker as run_duo) ───────────────────────────
if [ "$(id -u)" = "0" ] && [ -z "${IS_SANDBOX:-}" ]; then
  echo "[adventure] FATAL: running as root without IS_SANDBOX=1 — claude refuses --dangerously-skip-permissions as root." >&2
  echo "[adventure]        re-run as: IS_SANDBOX=1 bash qa/run_adventure.sh $RUN ..." >&2
  exit 2
fi

WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
WORLDOS_ACTOR_MODEL="$(worldos_env ACTOR_MODEL sonnet)"
SCORE_SCRIPT="$(worldos_env SCORE_SCRIPT qa/score.sh)"
ASSERT_BEHAVIORAL_SCRIPT="$(worldos_env ASSERT_BEHAVIORAL_SCRIPT qa/assert_behavioral.py)"
# Opus cold-open headroom floor (same rationale as run_duo — a low per-turn cap trips the DM grounding turn).
case "$WORLDOS_DM_MODEL" in
  *opus*) if awk "BEGIN{exit !($BUDGET < 4.0)}"; then echo "[adventure] opus: flooring per-turn budget \$$BUDGET -> \$4.00"; BUDGET=4.00; fi ;;
esac
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile

# Lean-per-beat context (default ON, byte-identical to run_duo).
WORLDOS_LEAN_BEATS="${WORLDOS_LEAN_BEATS:-1}"

# -- ARC MODE (2026-09-02, G2 lever) ------------------------------------------------------------
# This runner drives a PRE-SEEDED arc: the cast, the map and the foes already exist. Three directives
# the shared duo path injects every beat pull AGAINST that seed and were measured driving the three
# failed Opus-5 arc runs -- the SCENE-INTRO "named face who SPEAKS" mandate (which minted an invented
# grief-NPC on beat 0 in 3/3 runs), the duo brief's "new named faces enter and speak" obligation, and
# the MIDPOINT REVERSAL line the DM answered with an invented monster. ARC MODE suppresses/rewrites
# exactly those three. It is scoped to THIS runner: qa/run_duo.sh never sets it, so the duo runbook
# and the duo brief stay byte-identical.
export WORLDOS_ARC_MODE=1

# ── HERMETIC SESSIONS (#1656 root cause) ───────────────────────────────────────────────────────────
# The DM/player `claude -p` sessions previously inherited the USER-level ~/.claude config — including
# the claude-mem plugin, whose SessionStart hook injects OLD WorldOS session observations. Measured on
# adv_live2: the FIRST DM session carried 39 claude-mem refs incl. a FOREIGN campaign's beats, and the
# DM ultimately "closed" THAT story mid-fight ("the standoff at the bonesetter's door"). Both live
# runs died this way at depth ~7. Fix = the #1260-proven isolation: a per-run CLAUDE_CONFIG_DIR with
# empty settings (no user plugins/hooks/CLAUDE.md) + an explicit keychain OAuth token. The repo plugin
# and MCP servers are unaffected (flag-scoped: --plugin-dir / --mcp-config --strict-mcp-config).
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

# The transcripts dir is threaded via WORLDOS_TRANSCRIPTS_DIR (a REPO-RELATIVE path; default
# qa/transcripts) so the adventure_eval launcher (--transcripts-dir) and this runner can never
# desync on WHERE the per-run artifacts land. Keep it repo-relative: it is composed as $ROOT/$T
# below and used relative to the $ROOT cwd during scoring.
T="${WORLDOS_TRANSCRIPTS_DIR:-qa/transcripts}"; STATE_DIR="$ROOT/qa/state/$RUN"
CHECKPOINT="$STATE_DIR/.adv_checkpoint.json"
CHECKPOINT_SLOT="adv_checkpoint"
LOCKDIR="$STATE_DIR/.adv_run.lock"
TRACE="$ROOT/$T/$RUN.quest_trace.json"   # quest_progress writes stamps straight to the transcripts prefix
mkdir -p "$T" "$STATE_DIR"

RESUME_MODE=0
LAST_COMPLETED_BEAT=-1
START_BEAT=1
DSID=""
PSID=""

adv_release_lock() { rm -rf "$LOCKDIR" 2>/dev/null || true; }
adv_acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then printf '%s\n' "$$" > "$LOCKDIR/pid"; trap adv_release_lock EXIT; return 0; fi
  local oldpid=""; oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "[adventure] another run is using $STATE_DIR (pid $oldpid); use a different run id" >&2; exit 2
  fi
  rm -rf "$LOCKDIR" 2>/dev/null || true
  mkdir "$LOCKDIR" 2>/dev/null || { echo "[adventure] could not acquire lock $LOCKDIR" >&2; exit 2; }
  printf '%s\n' "$$" > "$LOCKDIR/pid"; trap adv_release_lock EXIT
}

# In-process engine checkpoint slot (mirrors run_duo's duo_engine_slot).
adv_engine_slot() {
  local action="$1"
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$action" "$CAMPAIGN_ID" "$CHECKPOINT_SLOT" <<'PY' >/dev/null
import sys, server
action, campaign_id, slot = sys.argv[1], sys.argv[2], sys.argv[3]
if action == "save": server.save_slot(campaign_id, slot)
elif action == "load": server.load_slot(campaign_id, slot)
else: raise SystemExit(f"unknown slot action {action!r}")
PY
}

# ── fresh-run hygiene (item 9) ───────────────────────────────────────────────────────────────────
# A rerun of a COMPLETED run-id must NOT inherit the prior run's quest trace or result sidecars:
# quest_progress stamps are idempotent, so a stale <run>.quest_trace.json carrying a quest_completed
# stamp would make the aggregator read a FALSE completion, and stale gate/lens/summary sidecars would
# be re-aggregated as THIS run's. Truncate them on a FRESH run only — the RESUME path deliberately
# keeps the trace + checkpoint to continue the same run. ($COMBINED/$CHAT/$MOVES/$TOOLTIMING are
# truncated separately below; this covers the transcripts-prefix artifacts they don't.)
adv_clean_stale_artifacts() {
  rm -f \
    "$TRACE" \
    "$T/$RUN.gate.txt" \
    "$T/$RUN.score.json" "$T/$RUN.tolkien.json" "$T/$RUN.angrydm.json" \
    "$T/$RUN.adventure.json" "$T/$RUN.latency.json" "$T/$RUN.state.json" \
    "$T/$RUN.play.md" "$T/$RUN.md" \
    "$T/$RUN.quest.log" "$T/$RUN.quest.err" "$T/$RUN.seed.err" \
    "$T/$RUN.dm.err" "$T/$RUN.player.err" \
    2>/dev/null || true
  rm -f "$T/$RUN".dm.*.jsonl "$T/$RUN".player.*.jsonl 2>/dev/null || true
}

# ── seed the campaign (fresh run) ───────────────────────────────────────────────────────────────
adv_seed() {
  rm -rf "$STATE_DIR/campaigns" 2>/dev/null
  local out cid
  out="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python "$SEEDER" "$STATE_DIR" 2>"$T/$RUN.seed.err")"
  cid="$(printf '%s\n' "$out" | tail -n1 | tr -d '[:space:]')"
  if [ "$cid" != "$CAMPAIGN_ID" ]; then
    echo "[adventure] FATAL: seed did not yield campaign id '$CAMPAIGN_ID' (got '$cid'). See $T/$RUN.seed.err" >&2
    cat "$T/$RUN.seed.err" >&2 || true
    exit 1
  fi
  echo "[adventure] seeded $CAMPAIGN_ID into $STATE_DIR ($(printf '%s' "$out" | tail -n1))"
  adv_write_seed_species
}

# The SEEDED bestiary species, read off the FRESH seed snapshot (before any DM beat can add to it)
# and written to <run>.seed_species.json. The arc behavioral gate compares every DM spawn_monster
# against THIS list, so the allowed cast is derived from the seed rather than hard-coded in the gate:
# re-seed with different foes and the gate follows. `creature_slug` is the engine's own stable TYPE
# key ("Goblin Warrior" -> goblin-warrior), which is also what spawn_monster's canonical result name
# slugifies to -- so the comparison is exact, not a name-substring guess.
SEED_SPECIES="$ROOT/$T/$RUN.seed_species.json"
adv_write_seed_species() {
  python3 - "$STATE_DIR" "$SEED_SPECIES" <<'SEEDPY' || echo "[adventure] WARN: could not write the seed-species manifest; the arc spawn gate stands down" >&2
import glob, json, re, sys
state_dir, out = sys.argv[1:3]
species = {}
for sp in glob.glob(f"{state_dir}/campaigns/*/snapshot.json"):
    try:
        d = json.loads(open(sp, encoding="utf-8").read())
    except Exception:
        continue
    for ch in (d.get("characters") or {}).values():
        if not isinstance(ch, dict) or ch.get("kind") != "monster":
            continue
        name = str(ch.get("name") or "")
        slug = str(ch.get("creature_slug") or "") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug:
            species.setdefault(slug, name)
if not species:
    raise SystemExit("no seeded monsters found in the snapshot")
json.dump({"species": sorted(species), "names": [species[s] for s in sorted(species)]},
          open(out, "w", encoding="utf-8"), indent=2)
print(f"[adventure] seeded species: {', '.join(sorted(species))}")
SEEDPY
}

# ── quest telemetry + completion short-circuit (between beats) ───────────────────────────────────
# Prints the quest status on stdout ("active"/"completed"/...) and stamps the trace, reading the
# machine-contract last line `quest_status=<s>` from qa/quest_progress.py.
#
# FAIL-OPEN, BUT LOUD (items 16 + 18): telemetry must NEVER abort a run (a dead poll must not burn
# the LLM budget). But a silent fail-open was the trap — an empty capture was treated as "active" and
# the loop ran on. So we VALIDATE the contract: the uv invocation must exit 0 (PIPESTATUS[0]) AND the
# captured last line must be `quest_status=…`. On violation: warn visibly, COUNT the failure, and
# return EMPTY with rc=1 — the beat loop still maps empty→active (never aborts), and the --dry-run
# path reports telemetry FAILED (nonzero) instead of a false "telemetry OK".
ADV_TELEMETRY_FAIL_FILE="$STATE_DIR/.telemetry_fails"
rm -f "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || true   # each run process counts its own telemetry fails
adv_telemetry_note_fail() {
  local n; n="$(cat "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || echo 0)"; n="${n:-0}"
  echo $((n + 1)) > "$ADV_TELEMETRY_FAIL_FILE"
}
adv_quest_poll() {
  local beat="$1" rc out status
  # Run the poll to a capture file (NOT through a `$(pipe)`) so PIPESTATUS[0] is the REAL uv rc and
  # a subshell can't swallow it. Mirror stdout into the quest.log as before.
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python "$ROOT/qa/quest_progress.py" \
      "$STATE_DIR" "$CAMPAIGN_ID" --beat "$beat" --trace "$TRACE" --quest-title "$QUEST_TITLE" \
      >"$STATE_DIR/.quest_poll.out" 2>>"$T/$RUN.quest.err"
  rc=$?
  cat "$STATE_DIR/.quest_poll.out" >> "$T/$RUN.quest.log" 2>/dev/null || true
  out="$(tail -n1 "$STATE_DIR/.quest_poll.out" 2>/dev/null)"
  if [ "$rc" -ne 0 ] || [ "${out#quest_status=}" = "$out" ]; then
    adv_telemetry_note_fail
    echo "[adventure] WARN beat $beat: quest telemetry unparseable (uv rc=$rc; missing quest_status= contract line; see $T/$RUN.quest.err) — treating as active" >&2
    printf '%s' ""
    return 1
  fi
  status="${out#quest_status=}"
  printf '%s' "$status"
  return 0
}

adv_acquire_lock

# Resume support (the checkpoint binds the live seeded campaign via the engine slot).
if [ -s "$CHECKPOINT" ] && jq -e . "$CHECKPOINT" >/dev/null 2>&1; then
  ck_sha="$(jq -r '.sha // ""' "$CHECKPOINT")"
  ck_beats="$(jq -r '.total_beats // ""' "$CHECKPOINT")"
  ck_persona="$(jq -r '.persona // ""' "$CHECKPOINT")"
  if [ "$ck_sha" = "$CURRENT_SHA" ] && [ "$ck_beats" = "$BEATS" ] && [ "$ck_persona" = "$PLAYER_PROMPT_FILE" ]; then
    RESUME_MODE=1
    LAST_COMPLETED_BEAT="$(jq -r '.last_completed_beat' "$CHECKPOINT")"
    PSID="$(jq -r '.player_session_id' "$CHECKPOINT")"
    DSID="$(jq -r '.dm_session_id' "$CHECKPOINT")"
    START_BEAT=$((LAST_COMPLETED_BEAT + 1))
    echo "[adventure] resuming: last_completed=$LAST_COMPLETED_BEAT next=$START_BEAT"
  else
    echo "[adventure] checkpoint mismatch (sha/beats/persona differ from this invocation). The run" >&2
    echo "[adventure]   dir + lock are SAFE to reuse — the lock auto-releases on exit (trap), so no" >&2
    echo "[adventure]   cleanup is needed. To restart this run-id from scratch, delete the checkpoint:" >&2
    echo "[adventure]   rm -f $CHECKPOINT" >&2
    exit 2
  fi
fi

if [ "$RESUME_MODE" != "1" ]; then
  adv_clean_stale_artifacts   # fresh run: a rerun of a completed run-id must not inherit stale state
  adv_seed
fi

worldos_isolate_claude_auth

# DM gets the engine (state dir patched in); the player gets the constrained move facade.
DM_CFG="$STATE_DIR/dm.mcp.json"; PLAYER_CFG="$STATE_DIR/player.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$MOVES"
TOOLTIMING_PATH="$ROOT/$T/$RUN.tooltiming.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$TOOLTIMING_PATH"
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" "$ROOT" "$TOOLTIMING_PATH" <<'PY'
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
python3 - "$ROOT" "$STATE_DIR" "$MOVES" "$PLAYER_CFG" <<'PY'
import json, sys
root, state, moves, out = sys.argv[1:5]
json.dump({"mcpServers": {"worldos-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"WORLDOS_STATE_DIR": state, "WORLDOS_PLAYER_MOVES": moves}}}}, open(out, "w"))
PY

if [ "$RESUME_MODE" = "1" ]; then
  if ! adv_engine_slot load; then
    echo "[adventure] could not restore campaign $CAMPAIGN_ID from slot $CHECKPOINT_SLOT; delete $CHECKPOINT to restart" >&2; exit 2
  fi
else
  DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
fi

# The DM brief = the shared duo brief + the ARC ADDENDUM (the ONLY brief-plumbing run_duo exposes
# is `cat qa/play_dm_duo.txt`; we CONCATENATE the addendum, we do not fork the brief).
#
# ARC MODE first REWRITES two clauses of the shared brief. The duo brief obliges the DM to make
# "new named faces enter and speak" -- correct for an emergent duo session, wrong for a seeded arc:
# all three failed Opus-5 runs opened by minting a camp NPC with a missing brother and walking them
# into the crypt as a second body to lose, while the passing control created no opening NPC at all.
# The edits are EXACT-MATCH and FAIL LOUD: if the shared brief is reworded and a clause stops
# matching, this aborts rather than silently shipping the un-suppressed obligation.
DM_BRIEF_BASE="$(python3 - qa/play_dm_duo.txt <<'BRIEFPY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
EDITS = [
    ("the session obligations you OWN (the clock advances; the party travels to \u22652 locations; "
     "new named faces enter and speak)",
     "the session obligations you OWN (the clock advances; the party travels to \u22652 locations; "
     "the cast already on the table speaks)"),
    ("and new named NPCs enter and SPEAK \u2014 a whole session spent in the opening location at the "
     "opening hour with no new faces is a FAILED session that flips the gate RED",
     "and the characters already in the scene SPEAK \u2014 a whole session spent in the opening "
     "location at the opening hour is a FAILED session that flips the gate RED"),
]
for old, new in EDITS:
    if src.count(old) != 1:
        raise SystemExit(f"arc-mode brief filter: expected exactly 1 match for {old[:48]!r}, "
                         f"found {src.count(old)} -- qa/play_dm_duo.txt was reworded; update qa/run_adventure.sh")
    src = src.replace(old, new)
sys.stdout.write(src)
BRIEFPY
)" || { echo "[adventure] FATAL: arc-mode brief filter failed (see the message above)" >&2; exit 2; }

DM_BRIEF="$DM_BRIEF_BASE
ARC ADDENDUM (this is a PRE-SEEDED adventure, NOT a world you build): the world already exists in
engine state as campaign \"$CAMPAIGN_ID\" — do NOT start_world / start_adventure / create the map.
GROUND on it: call get_state(\"$CAMPAIGN_ID\"), look_around(\"$CAMPAIGN_ID\"), and get_quests(\"$CAMPAIGN_ID\")
at the start of every beat. The map: a camp clearing (the party's start) with a door to Keeper Maera's
tavern (the quest giver; a merchant is one room further) and a door DOWN into a goblin-infested crypt
that opens onto a throne hall where the Goblin Boss waits. The quest \"$QUEST_TITLE\" has four
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
PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"

COMBINED="$T/$RUN.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$COMBINED"
CHAT="$T/$RUN.chat.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$CHAT"

echo "[adventure] run=$RUN campaign=$CAMPAIGN_ID beats=$BEATS budget=\$$BUDGET persona=$PLAYER_PROMPT_FILE dm=$WORLDOS_DM_MODEL"

# ── DRY-RUN SMOKE PATH: seed + wire + one telemetry poll, NO claude ─────────────────────────────
# The smoke-check the CLAUDE.md test policy allows: prove the seed + MCP-config + quest-telemetry
# wiring end-to-end WITHOUT spending any LLM budget. Runs the real engine (local-allowed) only.
if [ "$DRY_RUN" = "1" ]; then
  echo "[adventure] DRY RUN — seeded + configs built; polling quest telemetry once (no claude)…"
  status="$(adv_quest_poll 0)"; poll_rc=$?
  # Item 18: a smoke check must FAIL LOUD when the telemetry contract is violated — otherwise a
  # broken poll ships as "telemetry OK". Report FAILED + exit nonzero instead of the OK banner.
  if [ "$poll_rc" -ne 0 ]; then
    echo "[adventure] DRY-RUN FAILED: quest telemetry violated its contract (see $T/$RUN.quest.err)" >&2
    exit 1
  fi
  echo "[adventure] dry-run quest status: ${status:-<unknown>}  (trace: $TRACE)"
  [ -s "$DM_CFG" ] && echo "[adventure] dry-run OK: dm.mcp.json + player.mcp.json written"
  echo "[adventure] dry-run plan: intro -> DM ground -> $BEATS beats (short-circuit on quest!=active) -> score"
  exit 0
fi

# P0: the player introduces their character with a SINGLE say() (a tagged move — the behavioral gate
# requires structured player turns). They do NOT act yet.
turn() {  # $1=role $2=sid $3=first? $4=msg ; echoes the reply text (mirrors run_duo's turn)
  local role="$1" sid="$2" first="$3" msg="$4" out resume=() extra=() rc=0
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$role" = "dm" ]; then
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
  else
    # The player's result envelope is TEE'd before jq: it carries the CONCRETE model the API served,
    # which qa/model_provenance.py reads back (the `sonnet` alias drifts — recording only the alias is
    # what made the 2026-09-02 model swap invisible). jq still consumes the same bytes on stdout.
    "${DUO_ENV[@]}" claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
      --model "$WORLDOS_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format json 2>> "$T/$RUN.player.err" \
      | tee "$T/$RUN.player.$(date +%s%N).jsonl" | jq -r '.result // ""' 2>/dev/null
  fi
}

turn_retry() {  # transient-aware empty-output retry (mirrors run_duo)
  local r attempt max="${WORLDOS_DM_MAX_ATTEMPTS:-4}" last_out last_rc transient
  worldos_dm_prebeat_mark "$STATE_DIR"
  r="$(turn "$@")"; attempt=1
  while [ -z "$r" ] && [ "$attempt" -lt "$max" ]; do
    last_out="$(cat "$STATE_DIR/.dm_last_result" 2>/dev/null | tail -n1)"
    last_rc="$(cat "$STATE_DIR/.dm_last_rc" 2>/dev/null | tail -n1)"; last_rc="${last_rc:-0}"
    transient=0; worldos_dm_failure_is_transient "$last_out" "$last_rc" && transient=1
    if [ "$transient" != "1" ] && [ "$attempt" -ge 2 ]; then echo "[adventure] empty ($1) — REAL failure; stop retry." >&2; break; fi
    if [ "$transient" = "1" ]; then echo "[adventure] empty ($1) — transient; retry $((attempt+1))/${max}…" >&2; worldos_dm_retry_backoff "$attempt"; else echo "[adventure] empty ($1) — retry once…" >&2; fi
    if [ "${3:-}" = "1" ]; then
      worldos_dm_remint_session_on_retry --session-id "$2"; local _fresh="$2"
      [ "${#WORLDOS_DM_RETRY_SESSION[@]}" -ge 2 ] && _fresh="${WORLDOS_DM_RETRY_SESSION[1]}"
      r="$(turn "$1" "$_fresh" "$3" "${@:4}")"
    else r="$(turn "$@")"; fi
    attempt=$((attempt + 1))
  done
  printf '%s' "$r"
}

MCURSOR_FILE="$STATE_DIR/.mcursor"
if [ "$RESUME_MODE" = "1" ]; then
  _mc="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; echo "${_mc:-0}" > "$MCURSOR_FILE"
else echo 0 > "$MCURSOR_FILE"; fi
player_move() {  # $1=first $2=prompt ; relays ONLY the structured moves made this turn
  local first="$1" prompt="$2" cur total new
  turn player "$PSID" "$first" "$prompt" >/dev/null
  cur=$(cat "$MCURSOR_FILE" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -le "$cur" ]; then
    turn player "$PSID" 0 "You didn't act. Take your action THROUGH YOUR TOOLS now — say / do / request_check / attack / cast_spell / use_item. Tools only, no prose." >/dev/null
    total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  fi
  new="$(tail -n +"$((cur + 1))" "$MOVES" 2>/dev/null)"; echo "$total" > "$MCURSOR_FILE"
  [ -n "$new" ] && printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
}

DMSG=""
if [ "$RESUME_MODE" != "1" ]; then
  PLAYER_INTRO_PROMPT="$PLAYER_BRIEF

This is the very start — you are already in the party at the camp above the crypt. Introduce Aidan with a SINGLE say(\"…\"): who you are and that you mean to take the crypt job. Do NOT travel/attack yet — the DM opens the scene next. One say(), nothing else."
  WORLDOS_PLAYER_MAX_ATTEMPTS="${WORLDOS_PLAYER_MAX_ATTEMPTS:-3}"
  PMSG=""; _pa=1
  while [ -z "$PMSG" ] && [ "$_pa" -le "$WORLDOS_PLAYER_MAX_ATTEMPTS" ]; do
    [ "$_pa" -gt 1 ] && echo "[adventure] player produced no intro — retry $_pa/${WORLDOS_PLAYER_MAX_ATTEMPTS}…" >&2
    PMSG="$(player_move 1 "$PLAYER_INTRO_PROMPT")"; _pa=$((_pa + 1))
  done
  [ -z "$PMSG" ] && { echo "[adventure] player produced no intro — aborting" >&2; exit 1; }
  echo "[adventure] player intro: ${PMSG:0:120}…"
  chatlog player "$PMSG"

  SETUP_DIRECTIVE="GROUND on the pre-seeded campaign now: get_state(\"$CAMPAIGN_ID\"), look_around(\"$CAMPAIGN_ID\"), get_quests(\"$CAMPAIGN_ID\"). Do NOT build or reset the world — it already exists. OPEN the scene at the camp clearing around Aidan and the quest \"$QUEST_TITLE\": set the tavern door (Keeper Maera) and the crypt door in front of the player, and hand the first live moment back to them (where do they go first — the tavern to hear Maera out, or straight for the crypt?)."
  DMSG="$(turn_retry dm "$DSID" 1 "$DM_BRIEF

The player agent introduces their character and intent:

$PMSG

$SETUP_DIRECTIVE OUTPUT DISCIPLINE — your final reply IS the opening scene: 2nd-person in-fiction PROSE + quoted dialogue ONLY. Never narrate your own setup/process.")"
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  if [ -z "$DMSG" ]; then worldos_chatlog_dm_failed; echo "[adventure] DM produced no opening — aborting" >&2; exit 1; fi
  echo "[adventure] DM opened: ${DMSG:0:120}…"
  worldos_chatlog_dm "$DMSG"
  adv_quest_poll 0 >/dev/null   # stamp the baseline (beat 0)
else
  DMSG="$(python3 - "$CHAT" <<'PY'
import json,sys
last=""
for line in open(sys.argv[1]).read().splitlines():
    try: row=json.loads(line)
    except ValueError: continue
    if row.get("role")=="dm" and row.get("text") and not row.get("beat_failed"): last=row["text"]
print(last)
PY
)"
fi

# ── the arc-directed beat loop (with the completion short-circuit) ───────────────────────────────
adv_write_checkpoint() {
  local beat="$1"
  adv_engine_slot save || { echo "[adventure] checkpoint slot save failed" >&2; return 1; }
  python3 - "$CHECKPOINT" "$beat" "$PSID" "$DSID" "$CAMPAIGN_ID" "$PLAYER_PROMPT_FILE" "$BEATS" "$BUDGET" "$CURRENT_SHA" <<'PY'
import json,sys
from pathlib import Path
path,beat,psid,dsid,cid,persona,beats,budget,sha=sys.argv[1:]
p=Path(path); tmp=p.with_suffix(p.suffix+".tmp")
tmp.write_text(json.dumps({"last_completed_beat":int(beat),"player_session_id":psid,"dm_session_id":dsid,
  "campaign_id":cid,"persona":persona,"total_beats":int(beats),"budget":budget,"sha":sha},indent=2)+"\n")
import os; os.replace(tmp,p)
PY
  LAST_COMPLETED_BEAT="$beat"; START_BEAT=$((beat + 1))
}

COMPLETED_STATUS="active"
if [ "$START_BEAT" -le "$BEATS" ]; then
for b in $(seq "$START_BEAT" "$BEATS"); do
  PROG_PRE="$(worldos_read_progress "$STATE_DIR")"
  PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
  PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
  PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"

  # ARC BEAT MARKER: stamp the beat number into the combined DM stream BEFORE this beat's events are
  # appended, so the arc behavioral gate can attribute an offending tool call to its BEAT rather than
  # guessing from stream position (retries append extra `system/init` events, so init-counting lies).
  # An unknown event `type` is ignored by every existing consumer (distill.py dispatches on type and
  # drops the rest; assert_behavioral's _tally/_tool_events read message.content; model_provenance
  # regex-scans for a "model" field) — so this is invisible to the transcript, the play log and the
  # scorers.
  printf '{"type":"worldos_arc_beat","beat":%s}\n' "$b" >> "$COMBINED"

  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) toward the mission using your tools — say / do / request_check / attack / cast_spell / use_item (look or my_sheet first if useful). Push toward the NEXT objective. Tools only.")"
  echo "[adventure] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[adventure] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"

  RUNBOOK="$(worldos_runbook_for_beat "$b" "$BEATS" "$PREV_LOC" "$STATE_DIR")"
  DIRECTOR="$(worldos_director_advisory "$ROOT" "$STATE_DIR")"
  EVENT_ADV="$(worldos_event_advisory "$ROOT" "$STATE_DIR")"
  DMSG="$(turn_retry dm "$DSID" 0 "The player does:

$PMSG

Resolve it through the engine (roll/attack/travel as needed), then PLAY the next beat as a full lived scene — any NPC or companion in the scene SPEAKS at least one quoted line; weave the open moment back to the player. Mark quest objectives with complete_objective as they land, and run real combat in the crypt/throne hall.

$RUNBOOK

$DIRECTOR

$EVENT_ADV")"
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  echo "[adventure] beat $b DM: ${DMSG:0:100}…"
  if [ -z "$DMSG" ]; then worldos_chatlog_dm_failed; echo "[adventure] DM went silent at beat $b; stopping early"; break; fi
  worldos_chatlog_dm "$DMSG"

  worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
  adv_write_checkpoint "$b"

  # Quest telemetry + COMPLETION SHORT-CIRCUIT: stamp this beat, then stop if the quest left "active".
  COMPLETED_STATUS="$(adv_quest_poll "$b")"
  echo "[adventure] beat $b quest status: ${COMPLETED_STATUS:-active}"
  case "${COMPLETED_STATUS:-active}" in
    active|""|None) : ;;
    *) echo "[adventure] quest '$QUEST_TITLE' resolved ($COMPLETED_STATUS) at beat $b — short-circuiting."; break ;;
  esac
done
fi

# ── wrap + score (same 3-lens + behavioral + latency tail as run_duo) ───────────────────────────
turn dm "$DSID" 0 "We are out of time. Bring this to a clean stop and call end_session with a one-line summary." >/dev/null
echo "[adventure] distilling + scoring…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
PLAY="$T/$RUN.play.md"
jq -rs 'map((.role|ascii_upcase) + ": " + (.text // "")) | join("\n\n")' "$CHAT" > "$PLAY" 2>/dev/null
[ -s "$PLAY" ] || cp "$T/$RUN.md" "$PLAY" 2>/dev/null
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ]; then cp "$SNAP" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && "$SCORE_SCRIPT" "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
wait
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50

# ── scorer-sentinel guard (item 10; mirrors run_duo's Fix F / #1404) ─────────────────────────────
# score.sh fails FAST on a 429 (writes {"quota_exhausted":true,…}) or an expired/invalid credential
# (writes {"error":"scorer_auth_expired",…}) instead of a real scorecard. A run scored on top of an
# INFRA fault is NOT a clean product measurement — flag it CONTAMINATED (a contaminated summary +
# nonzero EX_TEMPFAIL exit) so adventure_eval excludes it and never persists it as a scored run,
# rather than aggregating a quota/auth corpse as clean.
adv_write_contaminated_summary() {  # $1 = reason
  python3 - "$T/$RUN.adventure.json" "$RUN" "$LAST_COMPLETED_BEAT" "$1" <<'PY'
import json,sys
from pathlib import Path
out,run,last_beat,reason = sys.argv[1:5]
Path(out).write_text(json.dumps({"run":run,"contaminated":True,"contaminated_reason":reason,
  "behavioral":"CONTAMINATED","completed":False,
  "last_completed_beat":int(last_beat) if str(last_beat).lstrip("-").isdigit() else None},indent=2)+"\n")
PY
}
for _scf in "$T/$RUN.tolkien.json" "$T/$RUN.score.json" "$T/$RUN.angrydm.json"; do
  [ -f "$_scf" ] || continue
  if jq -e '.quota_exhausted == true' "$_scf" >/dev/null 2>&1; then
    echo "[adventure] SCORER QUOTA ABORT — $(basename "$_scf") is a 429 quota sentinel, not a scorecard. Marking this run infra-CONTAMINATED (skipping gate/scoring aggregation)." >&2
    adv_write_contaminated_summary "SCORER QUOTA ABORT (HTTP 429) on $(basename "$_scf")"
    rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
    exit "$EX_TEMPFAIL"
  fi
  if jq -e '.error == "scorer_auth_expired"' "$_scf" >/dev/null 2>&1; then
    echo "[adventure] SCORER AUTH ABORT — $(basename "$_scf") is an expired/invalid-credential sentinel, not a scorecard. Marking this run infra-CONTAMINATED." >&2
    adv_write_contaminated_summary "SCORER AUTH ABORT (scorer_auth_expired) on $(basename "$_scf")"
    rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
    exit "$EX_TEMPFAIL"
  fi
done

# WORLDOS_GATE_ARC turns on the seeded-arc lens (the addendum's five rules as hard FAIL rows);
# WORLDOS_ARC_SEED_SPECIES hands it the species this run was actually SEEDED with.
WORLDOS_GATE_ARC=1 WORLDOS_ARC_SEED_SPECIES="$SEED_SPECIES" \
  python3 "$ASSERT_BEHAVIORAL_SCRIPT" "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
# ── honest scoring on a RED gate (item 11; mirrors run_duo) ──────────────────────────────────────
# A structurally broken (gate-RED) run must not persist high lens medians — CAP the three lens files
# to <= 2.5 / INVALID via the SHARED worldos_cap_score_red helper (qa/lib_beat_driver.sh), annotated
# with the failed checks, so a dead scene can't masquerade as prestige play in the aggregate.
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  worldos_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
LATENCY_JSON="$T/$RUN.latency.json"
python3 qa/latency_rollup.py --dir "$T" --run "$RUN" --tooltiming "$TOOLTIMING_PATH" --out "$LATENCY_JSON" >/dev/null 2>&1 || true

# ── the per-run adventure summary (self-describing; adventure_eval also falls back to raw files) ─
# Carries the requested DM/actor MODELS (item 6 provenance), the CONCRETE model ids resolved from the
# run's own transcripts (dm_model_resolved / player_model_resolved — the alias `opus`/`sonnet` drifts,
# and only the resolved id says which model produced this row), + session_beats (item 4 engagement) so
# the aggregator reads provenance/beats from the summary rather than guessing. Completion + behavioral use
# the SAME honest semantics as adventure_eval (items 5 + 17): only a "completed" terminal status is a
# completion; GREEN requires the assert_behavioral success marker, not the mere absence of [FAIL].
python3 - "$T/$RUN.adventure.json" "$TRACE" "$T/$RUN.gate.txt" "$RUN" "$LAST_COMPLETED_BEAT" "$LATENCY_JSON" "$WORLDOS_DM_MODEL" "$WORLDOS_ACTOR_MODEL" "$ROOT/qa" "$T/$RUN" <<'PY'
import json,sys
from pathlib import Path
out,trace,gate,run,last_beat,lat,dm_model,actor_model,qa_dir,prefix = sys.argv[1:11]
sys.path.insert(0, qa_dir)
try:
    from model_provenance import resolve_models   # concrete ids from this run's own transcripts
    resolved = resolve_models(prefix)
except Exception:
    resolved = {"dm_model_resolved": None, "player_model_resolved": None}
def rj(p):
    try: return json.loads(Path(p).read_text())
    except Exception: return {}
tr=rj(trace); stamps=tr.get("stamps") or []
completed_stamp=next((s for s in stamps if s.get("stage")=="quest_completed"),None)
# Terminal status: prefer the completed stamp's recorded status:<x> signal, else the live status.
status=""
if completed_stamp:
    sig=str(completed_stamp.get("signal") or "")
    if sig.startswith("status:"): status=sig.split(":",1)[1].strip().lower()
if not status: status=str(tr.get("quest_status") or "active").strip().lower()
completed=(status=="completed")   # completion HONESTY — a terminal "failed"/other is NOT a completion
btc=completed_stamp.get("beat") if (completed and completed_stamp) else None
gate_txt=""
try: gate_txt=Path(gate).read_text()
except Exception: pass
def _green_marker(txt):
    return any(l.strip()=="GREEN" or l.strip().startswith("GREEN (") for l in txt.splitlines())
if "[FAIL]" in gate_txt:
    behavioral="RED"
elif "=== behavioral assertions ===" in gate_txt and _green_marker(gate_txt):
    behavioral="GREEN"
else:
    behavioral=None   # empty/truncated/malformed gate — never an assumed GREEN
dead=rj(lat).get("failed_beats")
lb=int(last_beat) if str(last_beat).lstrip("-").isdigit() else None
session_beats=lb if (lb is not None and lb>=0) else None
Path(out).write_text(json.dumps({"run":run,"campaign_id":tr.get("campaign_id"),"quest_status":tr.get("quest_status"),
  "completed":bool(completed),"beats_to_complete":btc,"last_completed_beat":lb,"session_beats":session_beats,
  "dm_model":dm_model or None,"actor_model":actor_model or None,
  "dm_model_resolved":resolved.get("dm_model_resolved"),"player_model_resolved":resolved.get("player_model_resolved"),
  "dead_beats":dead,"behavioral":behavioral,"stages_reached":[s.get("stage") for s in stamps]},indent=2)+"\n")
print(f"[adventure] summary: completed={completed} beats_to_complete={btc} behavioral={behavioral} "
      f"dm={dm_model}({resolved.get('dm_model_resolved') or 'unresolved'}) "
      f"actor={actor_model}({resolved.get('player_model_resolved') or 'unresolved'})")
PY

# Item 16: surface a run-level telemetry-health summary if any quest poll failed its contract.
ADV_TELEMETRY_FAILS="$(cat "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || echo 0)"; ADV_TELEMETRY_FAILS="${ADV_TELEMETRY_FAILS:-0}"
if [ "$ADV_TELEMETRY_FAILS" -gt 0 ]; then
  echo "[adventure] WARN: quest telemetry failed its contract on $ADV_TELEMETRY_FAILS poll(s) this run — stage stamps / the completion signal may be incomplete (see $T/$RUN.quest.err)." >&2
fi

echo "[adventure] done. run=$RUN behavioral=$([ "${GATE:-0}" = 0 ] && echo GREEN || echo RED) quest=${COMPLETED_STATUS:-active} trace=$TRACE"
rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
exit "${GATE:-0}"
