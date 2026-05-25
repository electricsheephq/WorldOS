#!/usr/bin/env bash
# ClawDnD full-plugin QA: play a session through the REAL plugin (Claude Code +
# --plugin-dir), distill the transcript, then score it TWICE — the mechanical
# rubric (does the machinery work) and the Tolkien story-craft lens (is it EPIC).
#
# Usage:  qa/run_qa.sh [run-id] [budget-usd] [play-prompt] [mechanical-rubric]
# Run from the repo root. Requires the `claude` CLI + uv + jq. Voice runs on the
# null backend (torch-free). State is isolated PER RUN (qa/state/<run>) + a per-run
# MCP config, so multiple runs can play in PARALLEL without clobbering each other.
#
# Outputs (in qa/transcripts/):
#   <run>.jsonl        raw stream-json
#   <run>.md           distilled, readable play log + tool-call tally
#   <run>.state.json   the final persisted campaign (ground truth)
#   <run>.score.json   the MECHANICAL scorecard (loop / rules / state integrity)
#   <run>.tolkien.json the STORY-CRAFT scorecard (grandeur/character/prose/…)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
# Shared beat-driver helpers — here for clawdnd_cap_score_red (honest scoring on a gate-RED run).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="${1:-$(date +%Y%m%d-%H%M%S)}"
BUDGET="${2:-3.00}"
PROMPT_FILE="${3:-qa/play_prompt.txt}"
RUBRIC_FILE="${4:-qa/rubric.md}"
# DM model knob (default sonnet → unchanged); a one-flag flip for Opus structural-adherence tests.
CLAWDND_DM_MODEL="${CLAWDND_DM_MODEL:-sonnet}"
T="qa/transcripts"
STATE_DIR="$ROOT/qa/state/$RUN"          # per-run isolation -> parallel-safe
MCP_CONFIG="$STATE_DIR/qa.mcp.json"
mkdir -p "$T" "$STATE_DIR"

echo "[qa] run=$RUN budget=\$$BUDGET state=$STATE_DIR"
rm -rf "$STATE_DIR/campaigns" 2>/dev/null

# Per-run MCP config: qa/qa.mcp.json with this run's state dir patched in, so each
# parallel run's engine writes to its own campaigns/ tree.
python3 - "$ROOT/qa/qa.mcp.json" "$STATE_DIR" "$MCP_CONFIG" <<'PY'
import json, sys
src, state_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(src))
cfg["mcpServers"]["clawdnd-engine"]["env"]["CLAWDND_STATE_DIR"] = state_dir
json.dump(cfg, open(out, "w"))
PY

echo "[qa] playing (claude --plugin-dir, $CLAWDND_DM_MODEL) prompt=$PROMPT_FILE…"
claude -p "$(cat "$PROMPT_FILE")" \
  --plugin-dir "$ROOT" \
  --mcp-config "$MCP_CONFIG" --strict-mcp-config \
  --model "$CLAWDND_DM_MODEL" --permission-mode bypassPermissions \
  --max-budget-usd "$BUDGET" \
  --output-format stream-json --verbose \
  > "$T/$RUN.jsonl" 2> "$T/$RUN.err"
echo "[qa] play exit=$? ($(wc -l < "$T/$RUN.jsonl") events)"

# A blank transcript means the play never ran (transient EINTR / auth-rate blip,
# often from too many concurrent runs) — fail loudly instead of distilling nothing
# and emitting a misleading 1.0 scorecard.
if [ ! -s "$T/$RUN.jsonl" ]; then
  echo "[qa] PLAY PRODUCED NO OUTPUT — transient failure (see $T/$RUN.err). Skipping distill+score; re-run '$RUN' (ideally solo, not concurrent)." >&2
  exit 1
fi

echo "[qa] distilling…"
python3 qa/distill.py "$T/$RUN.jsonl"

# Snapshot the final persisted campaign as ground truth for the scorers. Pick the
# campaign with the LARGEST NON-EMPTY snapshot.json — NOT a blind `head -1` over dirs:
# if the play agent ever fat-fingers/hallucinates a campaign_id, campaign_lock() can
# orphan a lock-only dir (no snapshot), and head -1 may grab THAT and mis-report the run
# as "no state persisted" (a false-RED behavioral gate on a session that actually played).
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ] && [ -f "$SNAP" ]; then
  cp "$SNAP" "$T/$RUN.state.json"
else
  echo '{"warning":"no campaign state was persisted"}' > "$T/$RUN.state.json"
fi

# Behavioral gate — hard PASS/FAIL on a structurally broken run (no DM output, no
# dice, combat with no attacks, no PC in party, dup companion). Treat it like software.
echo "[qa] behavioral gate…"
python3 qa/assert_behavioral.py "$T/$RUN.jsonl" "$T/$RUN.state.json" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}

# Three INDEPENDENT lenses on the same distill — run them CONCURRENTLY (background +
# wait) so a third scoring pass doesn't add a third pass of wall-clock. The Angry-DM
# lens (5e rules-fidelity: commission + omission seams) is additive — score.sh is
# lens-agnostic and gates only on `.scores`, so the new schema passes the same gate.
echo "[qa] scoring (mechanical + Tolkien story-craft + Angry-DM 5e rules-fidelity, concurrent)…"
qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" "$RUBRIC_FILE" qa/score_schema.json "$T/$RUN.score.json" 1.50 &
qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50 &
wait

# Honest scoring: a gate-RED (non-progressing/structurally broken) run must NOT display as 4.1.
# CAP both scorecards to ≤2.5 / INVALID and annotate WHY before they're printed/consumed.
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  clawdnd_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi

echo "[qa] ===== MECHANICAL scorecard ($RUN) ====="
jq -r '
  "scores: \(.scores)",
  "overall: \(.overall)",
  "verdict: \(.verdict)",
  "defects (\(.defects|length)):",
  (.defects[]? | "  [\(.severity)] \(.area): \(.evidence) -> \(.suggested_fix)")
' "$T/$RUN.score.json" 2>/dev/null || { echo "(mechanical parse failed; raw:)"; cat "$T/$RUN.score.json"; }

echo "[qa] ===== STORY-CRAFT scorecard — the Tolkien lens ($RUN) ====="
jq -r '
  "scores: \(.scores)",
  "overall: \(.overall)",
  "verdict: \(.verdict)",
  "highlights:",
  (.highlights[]? | "  + \(.)"),
  "defects (\(.defects|length)):",
  (.defects[]? | "  [\(.severity)] \(.area): \(.evidence) -> \(.suggested_fix)")
' "$T/$RUN.tolkien.json" 2>/dev/null || { echo "(tolkien parse failed; raw:)"; cat "$T/$RUN.tolkien.json"; }

echo "[qa] ===== 5e RULES-FIDELITY scorecard — the Angry DM ($RUN) ====="
jq -r '
  "scores: \(.scores)",
  "overall: \(.overall)",
  "coverage: had_caster=\(.coverage.had_caster) fights=\(.coverage.fights) gaps=\(.coverage.gaps)",
  "verdict: \(.verdict)",
  "defects (\(.defects|length)):",
  (.defects[]? | "  [\(.severity)/\(.kind)] \(.area) — \(.rule): \(.evidence) -> \(.suggested_fix)")
' "$T/$RUN.angrydm.json" 2>/dev/null || { echo "(angry-dm parse failed; raw:)"; cat "$T/$RUN.angrydm.json"; }

echo "[qa] behavioral=$([ "${GATE:-0}" = 0 ] && echo GREEN || echo RED)"
exit "${GATE:-0}"
