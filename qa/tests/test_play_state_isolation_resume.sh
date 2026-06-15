#!/usr/bin/env bash
# GA PLAYABILITY PROOF — state isolation + resume re-attach (scripts/play.sh).
#
# The owner hit two blockers playing the REAL shipped .app:
#   A) the .app read/wrote the DEV REPO's play-state (play.sh hardcoded "$ROOT/play-state/$RUN");
#   B) Resume minted a brand-new EMPTY world instead of re-opening the saved campaign.
#
# Deterministic, $0, no LLM. We assert the SHELL-level contracts play.sh now honors — without a
# claude -p DM turn — by exercising the exact resolution expressions + the resume gate the script
# uses, and by driving the REAL engine to seed a save that the resume gate must recognize. These
# are the structural halves of the fix (the DM turn itself is GUI-verified). Asserts:
#   1. unset env  → STATE_ROOT == "$ROOT/play-state" (BYTE-IDENTICAL to the old behavior);
#   2. WORLDOS_STATE_DIR=<userdir> → STATE_ROOT == <userdir> (the .app's per-user dir wins);
#   3. CLAWDND_STATE_DIR fallback also wins when WORLDOS_STATE_DIR is unset;
#   4. the engine MCP config pins BOTH WORLDOS_/CLAWDND_STATE_DIR to the per-$RUN dir;
#   5. RESUME gate: a requested campaign WITH an on-disk snapshot under the run dir → RESUME=1
#      (move sink preserved, not truncated); a requested campaign with NO snapshot → RESUME=0
#      (fresh cold open, never a dead table); no request → RESUME=0.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/qa/lib_beat_driver.sh"

FAILS=0
note() { printf '  %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*"; FAILS=$((FAILS + 1)); }
pass() { printf '  ✓ %s\n' "$*"; }

PLAY="$ROOT/scripts/play.sh"
[ -f "$PLAY" ] || { fail "scripts/play.sh missing"; echo "── STATE/RESUME PROOF: FAIL ──"; exit 1; }

# --- (1-3) STATE_ROOT resolution — the EXACT expression play.sh uses. --------------------------
# play.sh: STATE_ROOT="${WORLDOS_STATE_DIR:-${CLAWDND_STATE_DIR:-$ROOT/play-state}}"
resolve_state_root() {  # $1=WORLDOS_STATE_DIR $2=CLAWDND_STATE_DIR (empty = unset)
  WORLDOS_STATE_DIR="$1" CLAWDND_STATE_DIR="$2" bash -c \
    'ROOT="'"$ROOT"'"; echo "${WORLDOS_STATE_DIR:-${CLAWDND_STATE_DIR:-$ROOT/play-state}}"'
}
# First, prove play.sh actually CONTAINS the override expression (guards a silent revert).
if grep -q 'STATE_ROOT="${WORLDOS_STATE_DIR:-${CLAWDND_STATE_DIR:-$ROOT/play-state}}"' "$PLAY"; then
  pass "play.sh uses the WORLDOS_/CLAWDND_ STATE_DIR override expression"
else
  fail "play.sh no longer carries the STATE_DIR override expression"
fi

GOT="$(resolve_state_root "" "")"
[ "$GOT" = "$ROOT/play-state" ] && pass "unset env → \$ROOT/play-state (byte-identical)" \
  || fail "unset env resolved to '$GOT' (expected '$ROOT/play-state')"

USERDIR="$(mktemp -d "${TMPDIR:-/tmp}/wos_userstate_XXXXXX")"
trap 'rm -rf "$USERDIR"' EXIT
GOT="$(resolve_state_root "$USERDIR" "")"
[ "$GOT" = "$USERDIR" ] && pass "WORLDOS_STATE_DIR wins → $USERDIR" \
  || fail "WORLDOS_STATE_DIR override resolved to '$GOT' (expected '$USERDIR')"

GOT="$(resolve_state_root "" "$USERDIR")"
[ "$GOT" = "$USERDIR" ] && pass "CLAWDND_STATE_DIR fallback wins when WORLDOS_ unset" \
  || fail "CLAWDND_STATE_DIR fallback resolved to '$GOT' (expected '$USERDIR')"

# --- (4) the engine MCP config pins BOTH names to the per-$RUN dir. ----------------------------
# Generate the dm.mcp.json exactly as play.sh does and assert clawdnd-engine carries BOTH
# WORLDOS_STATE_DIR and CLAWDND_STATE_DIR set to the per-run state dir (so an inherited bare
# WORLDOS_STATE_DIR=<user-root> can't repoint the engine away from this game's dir).
RUNDIR="$USERDIR/play-fixture-run"
mkdir -p "$RUNDIR"
DMCFG="$RUNDIR/dm.mcp.json"
python3 - "$ROOT" "$RUNDIR" "$DMCFG" <<'PY'
import json, sys
root, state_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"mcpServers": {"clawdnd-engine": {"type": "stdio", "command": "uv", "alwaysLoad": True,
    "args": ["run", "--directory", f"{root}/servers/engine", "server.py"],
    "env": {"WORLDOS_STATE_DIR": state_dir, "CLAWDND_STATE_DIR": state_dir}}}}
json.dump(cfg, open(out, "w"))
PY
ENGINE_BOTH="$(python3 - "$DMCFG" "$RUNDIR" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); want = sys.argv[2]
env = cfg["mcpServers"]["clawdnd-engine"]["env"]
print("1" if env.get("WORLDOS_STATE_DIR") == want and env.get("CLAWDND_STATE_DIR") == want else "0")
PY
)"
[ "$ENGINE_BOTH" = "1" ] && pass "engine MCP config pins BOTH WORLDOS_/CLAWDND_STATE_DIR to the per-run dir" \
  || fail "engine MCP config does not pin both state-dir names to the per-run dir"
# And prove play.sh's generator carries both names (guards a revert to CLAWDND-only).
grep -q '"env": {"WORLDOS_STATE_DIR": state_dir, "CLAWDND_STATE_DIR": state_dir}}' "$PLAY" \
  && pass "play.sh engine config sets both state-dir names" \
  || fail "play.sh engine config no longer sets both state-dir names"

# --- (5) RESUME gate — the exact condition play.sh uses to confirm a re-attach. ----------------
# play.sh: RESUME=1 iff WORLDOS_RESUME_CAMPAIGN set AND a snapshot exists under STATE_DIR.
resume_gate() {  # $1=STATE_DIR $2=RESUME_CAMPAIGN_REQ ; echoes RESUME (1/0)
  local state_dir="$1" req="$2" RESUME=0
  if [ -n "${req//[[:space:]]/}" ] && [ -f "$state_dir/campaigns/$req/snapshot.json" ]; then
    RESUME=1
  fi
  echo "$RESUME"
}
# Seed a REAL campaign via the engine under the run dir so the gate has a true snapshot to find.
CID="$(WORLDOS_STATE_DIR="$RUNDIR" CLAWDND_STATE_DIR="$RUNDIR" \
  uv run --directory "$ROOT/servers/engine" python - <<'PY' 2>/dev/null
import server
camp = server.start_world("baldurs-gate")["campaign_id"]
server.start_session(camp, title="resume fixture")
print(camp)
PY
)"
if [ -z "$CID" ] || [ ! -f "$RUNDIR/campaigns/$CID/snapshot.json" ]; then
  fail "engine fixture seed failed (no snapshot) — CID='$CID'"
else
  note "seeded campaign $CID under $RUNDIR"
  [ "$(resume_gate "$RUNDIR" "$CID")" = "1" ] \
    && pass "RESUME=1 when the requested campaign's snapshot exists (re-attach armed)" \
    || fail "RESUME gate did NOT arm for an existing saved campaign"
  [ "$(resume_gate "$RUNDIR" "no-such-campaign-id")" = "0" ] \
    && pass "RESUME=0 for a stale/missing campaign id (falls back to a fresh cold open)" \
    || fail "RESUME gate armed for a NON-existent campaign (would hand a dead table)"
  [ "$(resume_gate "$RUNDIR" "")" = "0" ] \
    && pass "RESUME=0 when no resume requested (fresh path untouched)" \
    || fail "RESUME gate armed with NO resume request"
fi

# Guard the move-sink-preservation branch is present (append on resume, truncate on fresh).
grep -q ': >> "$MOVES"; : >> "$CHAT"; : >> "$COMBINED"' "$PLAY" \
  && pass "play.sh preserves (appends) the move sink on resume" \
  || fail "play.sh resume no longer preserves the move sink"
grep -q ': > "$MOVES"; : > "$CHAT"; : > "$COMBINED"' "$PLAY" \
  && pass "play.sh still truncates the move sink on a FRESH launch" \
  || fail "play.sh fresh-launch sink truncation missing"

if [ "$FAILS" -eq 0 ]; then
  echo "── STATE/RESUME PROOF: PASS ──"
  exit 0
fi
echo "── STATE/RESUME PROOF: FAIL ($FAILS) ──"
exit 1
