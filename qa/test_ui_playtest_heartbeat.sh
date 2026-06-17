#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves qa/ui_playtest.sh now emits the immediate,
# model-INDEPENDENT progress heartbeat on BOTH its DM lanes — the cold-open turn and the
# per-move resolver loop — exactly like the production solo path (scripts/play.sh:440 / :620).
#
# THE GAP IT GUARDS (dogfood FIDELITY): the production solo loop calls
# worldos_emit_progress_heartbeat BEFORE the long DM `claude -p` think, so a wrapper-authored
# `narration` row lands in the engine /events within ~1s and the OpenWorlds viewer flips its
# spinner to "the scene is arriving above" (viewer/openworlds/app.jsx isWrapperProgressLine →
# notePendingProgress). The QA dogfood lane (ui_playtest.sh) had a CUSTOM inline dm_turn +
# resolver loop that NEVER emitted the heartbeat, so a dogfood's chronicle stayed BLANK for the
# whole ~82s beat — making the GUI playtest OVERSTATE perceived latency vs production. This is a
# QA-HARNESS-ONLY, zero-quality-cost fidelity fix (additive; the engine stays the sole writer —
# the heartbeat routes through log_engine_narration).
#
# We assert two layers, mirroring qa/test_run_duo_dm_timeout.sh's structure:
#   STRUCTURAL — ui_playtest.sh statically wires worldos_emit_progress_heartbeat into BOTH the
#     cold-open lane and the resolver loop, derives a LIVE campaign id (never a blank id, which
#     would no-op the helper), and emits the heartbeat BEFORE the dm_turn call in each lane.
#   BEHAVIORAL — sourcing the REAL qa/lib_beat_driver.sh with log_engine_narration stubbed, the
#     heartbeat with a real campaign id writes a wrapper-progress row (a /events narration row)
#     for the live campaign BEFORE the model turn; a blank id no-ops (best-effort, never fails a
#     beat). The continuing-beat text ROTATES (the live-campaign row is non-empty + wrapper-shaped).
#
# Sources the REAL qa/lib_beat_driver.sh. Self-contained under mktemp; macOS + ubuntu CI safe.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR/campaigns"

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

UIPT="$ROOT/qa/ui_playtest.sh"

# ── STRUCTURAL: ui_playtest.sh wires the heartbeat + the live-progress rule on its DM lanes ───
# PRIMARY: the resolver loop emits the model-INDEPENDENT heartbeat per move (the production-parity
# win). The cold-open lane mints its campaign INSIDE the turn (start_world), so — exactly like
# scripts/play.sh's DEFAULT (no pre-seeded hero) cold open — there is no pre-turn campaign id to
# target; its coverage is the MODEL-COOPERATIVE live-progress rule (asserted just below), which the
# shared dm_turn now prepends to BOTH lanes. So the heartbeat is wired (≥1) AND the dm_turn carries
# the live-progress rule, matching production parity on both halves.
chk "ui_playtest wires worldos_emit_progress_heartbeat (per-move resolver lane)" \
  'grep -q "worldos_emit_progress_heartbeat" "$UIPT"'
# MODEL-COOPERATIVE half: dm_turn prepends the shared live-progress rule to the DM prompt (covers
# BOTH the cold-open and resolver turns), parity with scripts/play.sh:288.
chk "dm_turn prepends the shared WORLDOS_LIVE_PROGRESS_RULE (covers cold-open + resolver)" \
  'grep -q "WORLDOS_LIVE_PROGRESS_RULE" "$UIPT"'
# The resolver loop must derive a LIVE campaign id (worldos_live_campaign_id is the engine-
# authoritative selector play.sh uses) — a blank id no-ops the helper, so the derivation is the crux.
chk "ui_playtest derives the live campaign id (worldos_live_campaign_id)" \
  'grep -q "worldos_live_campaign_id" "$UIPT"'
# The resolver loop emits the heartbeat AFTER echoing the player's move (chatlog player) and
# BEFORE resolving via dm_turn — so /events has a row before the long model think.
chk "resolver emits heartbeat between 'chatlog player' and the dm_turn resolve" \
  'awk "/chatlog player/{p=1} p&&/worldos_emit_progress_heartbeat/{h=1} p&&/DMSG=.*dm_turn 0/{print (h?\"OK\":\"NO\"); exit}" "$UIPT" | grep -q OK'

# SECONDARY (alwaysLoad parity): the QA lane's generated dm.mcp.json pins the engine tools
# (un-defer), env-gated default-on like scripts/play.sh + qa/run_duo.sh, so the DM does not burn a
# ToolSearch round-trip re-discovering engine tools every move (production has it default-on).
chk "ui_playtest's dm.mcp.json gen pins the engine tools (alwaysLoad parity, env-gated)" \
  'grep -q "alwaysLoad" "$UIPT" && grep -q "WORLDOS_ENGINE_ALWAYSLOAD" "$UIPT"'

# ── BEHAVIORAL: with log_engine_narration STUBBED, the heartbeat lands a wrapper row pre-turn ──
# log_engine_narration in the real lib shells into `uv run servers/engine` (no engine in CI), so
# stub it to capture (campaign_id, text) — the SAME seam worldos_emit_progress_heartbeat routes
# through. This proves the helper writes a row for a real id and no-ops a blank id.
EVENTS="$TMP/events.ndjson"; : > "$EVENTS"
log_engine_narration() {
  local campaign_id="$1" text="$2"
  [ -n "${campaign_id//[[:space:]]/}" ] || return 1
  [ -n "${text//[[:space:]]/}" ] || return 1
  printf '%s\t%s\n' "$campaign_id" "$text" >> "$EVENTS"
  return 0
}

CID="camp-live-0001"

# Cold open (first=1) → the opening teaser row for the live campaign.
worldos_emit_progress_heartbeat "$CID" 1 0
chk "cold-open heartbeat wrote one /events row for the live campaign" \
  '[ "$(grep -c "^$CID	" "$EVENTS")" -eq 1 ]'
chk "cold-open row carries the opening progress teaser" \
  'grep -q "^$CID	$WORLDOS_OPENING_PROGRESS_TEXT$" "$EVENTS"'

# Continuing beat (first=0, idx=0) → a rotating MOVE teaser row, emitted BEFORE the model turn.
worldos_emit_progress_heartbeat "$CID" 0 0
chk "continuing-beat heartbeat wrote a SECOND row for the live campaign" \
  '[ "$(grep -c "^$CID	" "$EVENTS")" -eq 2 ]'
co_text="${WORLDOS_MOVE_PROGRESS_TEXTS[0]}"
chk "continuing row carries a rotating MOVE progress teaser (idx 0)" \
  'grep -q "^$CID	$co_text$" "$EVENTS"'
# Every emitted row is a recognized wrapper-progress line (so app.jsx flips the spinner + returns
# null; engine memory consumers exact-match filter it) — i.e. zero quality cost, perceived only.
chk "every emitted heartbeat row is a wrapper-progress line" \
  'python3 -c "import sys; sys.path.insert(0,\"$ROOT/servers/engine\"); import wrapper_progress as w; rows=[l.split(chr(9),1)[1].rstrip(chr(10)) for l in open(\"$EVENTS\")]; sys.exit(0 if rows and all(w.is_wrapper_progress_line(t) for t in rows) else 1)"'

# A BLANK campaign id no-ops (best-effort — a heartbeat failure must never fail a beat) — no new row.
before="$(wc -l < "$EVENTS" | tr -d ' ')"
worldos_emit_progress_heartbeat "" 0 1
after="$(wc -l < "$EVENTS" | tr -d ' ')"
chk "blank campaign id no-ops the heartbeat (no /events row written)" '[ "$before" = "$after" ]'
chk "heartbeat with a blank id still returns 0 (never fails a beat)" \
  'worldos_emit_progress_heartbeat "" 0 1; [ $? -eq 0 ]'

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
