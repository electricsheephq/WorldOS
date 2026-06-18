#!/usr/bin/env bash
# WorldOS COMBAT-SPRINT QA — ~1.5-2 min, one claude -p DM call.
#
# Pre-seeds a campaign (zero LLM) then runs ONE DM call for a 3-round fight,
# distills, behavioral-gates, and Angry-DM-scores the result.
#
# Usage:  qa/run_combat_sprint.sh [run-id]
# Run from the repo root. Requires: claude CLI, uv, jq, python3.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Beat-driver helpers (worldos_cap_score_red).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="${1:-cs-$(date +%H%M%S)}"
WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
# GLM-only settings profile (no-op for Claude). Sourced after model vars resolve, before any
# timeout/budget/retry knob is consumed. See qa/glm_profile.sh.
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile
SCORE_SCRIPT="$(worldos_env SCORE_SCRIPT qa/score.sh)"
# Combat runs the whole multi-round fight on ONE budget (pre-seeded, no cold-open). An Opus combat
# costs ~5x a Sonnet one, so the Sonnet-tuned $1.50 cap cut it off mid-fight (observed 2026-06-06:
# error_max_budget_usd at num_turns=26 / mid-Round 2 -> low coverage, mech 3.1). Scale to the model.
case "$WORLDOS_DM_MODEL" in *opus*) CS_BUDGET="${WORLDOS_PLAY_BUDGET:-5.00}" ;; *) CS_BUDGET="${WORLDOS_PLAY_BUDGET:-1.50}" ;; esac
T="$ROOT/qa/transcripts"
STATE_DIR="$ROOT/qa/state/$RUN"
mkdir -p "$T" "$STATE_DIR"
rm -rf "$STATE_DIR/campaigns" 2>/dev/null
# Wave-1 per-tool timing sidecar: the engine appends one {ts,tool,wall_ms,ok,campaign_id} line per
# tool call here — only because we set WORLDOS_TOOLTIMING_PATH in the engine MCP env below (no-op
# when unset). Lets us attribute a COMBAT beat's tool-exec vs generation. Truncate at run start.
TOOLTIMING_PATH="$T/$RUN.tooltiming.jsonl"; : > "$TOOLTIMING_PATH"

echo "[cs] run=$RUN state=$STATE_DIR model=$WORLDOS_DM_MODEL"

# ── 1. Pre-seed (zero LLM) ───────────────────────────────────────────────────
echo "[cs] pre-seeding campaign (zero LLM)…"
SEED_JSON="$(
  WORLDOS_STATE_DIR="$STATE_DIR" \
  uv run --directory "$ROOT/servers/engine" python "$ROOT/qa/pre_seed_combat.py" "$STATE_DIR" 2>"$T/$RUN.seed.err"
)"
if [ -z "$SEED_JSON" ]; then
  echo "[cs] pre-seed produced no output — check $T/$RUN.seed.err" >&2
  cat "$T/$RUN.seed.err" >&2
  exit 1
fi
echo "[cs] seed JSON: $SEED_JSON"

# ── 2. Per-run MCP config (engine points at THIS worktree + THIS state dir) ──
# Mirror run_duo.sh lines ~39-46: patch qa.mcp.example.json with the run-local state dir
# AND override the engine --directory to this worktree so we use local code.
DM_CFG="$STATE_DIR/dm.mcp.json"
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$ROOT" "$DM_CFG" "$TOOLTIMING_PATH" <<'PY'
import json, sys
src, state_dir, root, out, tooltiming = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
cfg = json.load(open(src))
eng = cfg["mcpServers"]["worldos-engine"]
eng["env"]["WORLDOS_STATE_DIR"] = state_dir
eng["env"]["WORLDOS_TOOLTIMING_PATH"] = tooltiming
# Override --directory arg to use THIS worktree's engine (not the template default)
args = eng.get("args", [])
try:
    idx = args.index("--directory")
    args[idx + 1] = f"{root}/servers/engine"
except ValueError:
    # --directory not present; insert after "run"
    try:
        ri = args.index("run")
        args[ri + 1:ri + 1] = ["--directory", f"{root}/servers/engine"]
    except ValueError:
        args = ["run", "--directory", f"{root}/servers/engine"] + args
eng["args"] = args
json.dump(cfg, open(out, "w"))
PY

# ── 3. Build the DM prompt — inject the seed IDs into the template ───────────
PROMPT_TEMPLATE="$ROOT/qa/play_prompt_combat_sprint.txt"
PROMPT="$(sed "s|{{SEED_JSON}}|${SEED_JSON}|g" "$PROMPT_TEMPLATE")"

# ── 4. ONE claude -p DM call ─────────────────────────────────────────────────
echo "[cs] running DM (claude -p, $WORLDOS_DM_MODEL)…"
claude -p "$PROMPT" \
  --plugin-dir "$ROOT" \
  --mcp-config "$DM_CFG" --strict-mcp-config \
  --model "$WORLDOS_DM_MODEL" \
  --permission-mode bypassPermissions \
  --max-budget-usd "$CS_BUDGET" \
  --output-format stream-json --verbose \
  > "$T/$RUN.jsonl" 2>"$T/$RUN.err"
echo "[cs] play exit=$? ($(wc -l < "$T/$RUN.jsonl") events)"

if [ ! -s "$T/$RUN.jsonl" ]; then
  echo "[cs] PLAY PRODUCED NO OUTPUT — check $T/$RUN.err" >&2
  exit 1
fi

# ── 5. Distill ───────────────────────────────────────────────────────────────
echo "[cs] distilling…"
python3 qa/distill.py "$T/$RUN.jsonl" 2>/dev/null

# ── 6. Final snapshot ────────────────────────────────────────────────────────
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ] && [ -f "$SNAP" ]; then
  cp "$SNAP" "$T/$RUN.state.json"
else
  echo '{"warning":"no campaign state was persisted"}' > "$T/$RUN.state.json"
fi

# ── 7. Behavioral gate ────────────────────────────────────────────────────────
echo "[cs] behavioral gate…"
# Combat-sprint scope: skip the world-progression floor (a single pre-seeded fight in one place
# legitimately never advances days/travels — see assert_behavioral.py). Combat checks still apply.
WORLDOS_GATE_COMBAT_SPRINT=1 python3 qa/assert_behavioral.py "$T/$RUN.jsonl" "$T/$RUN.state.json" | tee "$T/$RUN.gate.txt"
GATE=${PIPESTATUS[0]}

# ── 8. Angry-DM score ────────────────────────────────────────────────────────
echo "[cs] scoring (Angry-DM 5e rules-fidelity)…"
"$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" \
  qa/rubric_angry_dm.md qa/score_schema_angry_dm.json \
  "$T/$RUN.angrydm.json" 1.00

# ── 9. Honest scoring: cap RED runs ──────────────────────────────────────────
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null \
    | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  worldos_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi

echo "[cs] ===== Angry-DM scorecard ($RUN) ====="
jq -r '
  "scores: \(.scores)",
  "overall: \(.overall)",
  "coverage: had_caster=\(.coverage.had_caster) fights=\(.coverage.fights) gaps=\(.coverage.gaps)",
  "verdict: \(.verdict)",
  "defects (\(.defects|length)):",
  (.defects[]? | "  [\(.severity)/\(.kind)] \(.area) — \(.rule): \(.evidence) -> \(.suggested_fix)")
' "$T/$RUN.angrydm.json" 2>/dev/null || { echo "(angry-dm parse failed; raw:)"; cat "$T/$RUN.angrydm.json"; }

echo "[cs] behavioral=$([ "${GATE:-0}" = 0 ] && echo GREEN || echo RED)"
exit "${GATE:-0}"
