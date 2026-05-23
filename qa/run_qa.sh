#!/usr/bin/env bash
# ClawDnD full-plugin QA: play a session through the REAL plugin (Claude Code +
# --plugin-dir), distill the transcript, then score it against the rubric.
#
# Usage:  qa/run_qa.sh [run-id] [budget-usd]
# Run from the repo root. Requires the `claude` CLI + uv. Voice runs on the
# null backend (torch-free); state is sandboxed under qa/state.
#
# Outputs (in qa/transcripts/):
#   <run>.jsonl        raw stream-json
#   <run>.md           distilled, readable play log + tool-call tally
#   <run>.state.json   the final persisted campaign (ground truth)
#   <run>.score.json   the rubric scorecard (scores + defects + verdict)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

RUN="${1:-$(date +%Y%m%d-%H%M%S)}"
BUDGET="${2:-3.00}"
PROMPT_FILE="${3:-qa/play_prompt.txt}"
T="qa/transcripts"
mkdir -p "$T" qa/state

echo "[qa] run=$RUN budget=\$$BUDGET"
rm -rf qa/state/campaigns/* 2>/dev/null

echo "[qa] playing (claude --plugin-dir, sonnet) prompt=$PROMPT_FILE…"
claude -p "$(cat "$PROMPT_FILE")" \
  --plugin-dir "$ROOT" \
  --mcp-config qa/qa.mcp.json --strict-mcp-config \
  --model sonnet --permission-mode bypassPermissions \
  --max-budget-usd "$BUDGET" \
  --output-format stream-json --verbose \
  > "$T/$RUN.jsonl" 2> "$T/$RUN.err"
echo "[qa] play exit=$? ($(wc -l < "$T/$RUN.jsonl") events)"

echo "[qa] distilling…"
python3 qa/distill.py "$T/$RUN.jsonl"

# Snapshot the final persisted campaign as ground truth for the scorer.
CAMP="$(find qa/state/campaigns -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
if [ -n "$CAMP" ] && [ -f "$CAMP/snapshot.json" ]; then
  cp "$CAMP/snapshot.json" "$T/$RUN.state.json"
else
  echo '{"warning":"no campaign state was persisted"}' > "$T/$RUN.state.json"
fi

echo "[qa] scoring…"
SCORER_INPUT="$(printf '%s\n\n# ===== OUTPUT FORMAT =====\nRespond with ONLY a single JSON object conforming to this schema — no prose, no markdown, no code fences:\n%s\n\n# ===== DISTILLED TRANSCRIPT =====\n%s\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n%s\n' \
  "$(cat qa/rubric.md)" "$(cat qa/score_schema.json)" "$(cat "$T/$RUN.md")" "$(cat "$T/$RUN.state.json")")"

claude -p "$SCORER_INPUT" \
  --model sonnet --permission-mode bypassPermissions \
  --max-budget-usd 1.50 \
  --output-format json 2> "$T/$RUN.score.err" \
  | jq -r '.result' \
  | sed -E '/^```/d' > "$T/$RUN.score.json" 2>/dev/null   # strip any code fences
# NB: the scorer relies on the JSON-only instruction embedded in the prompt; the
# CLI's --json-schema flag was found to suppress the result text, so it's omitted.

echo "[qa] ===== scorecard ($RUN) ====="
jq -r '
  "scores: \(.scores)",
  "overall: \(.overall)",
  "verdict: \(.verdict)",
  "defects (\(.defects|length)):",
  (.defects[] | "  [\(.severity)] \(.area): \(.evidence) -> \(.suggested_fix)")
' "$T/$RUN.score.json" 2>/dev/null || { echo "(score parse failed; raw:)"; cat "$T/$RUN.score.json"; }
