#!/usr/bin/env bash
# Score ONE distilled transcript against a rubric+schema in a single `claude -p` pass.
# Generalizes the inline scorer so any lens (mechanical rubric, Tolkien story-craft,
# …) can be applied to any transcript — including re-scoring old ones.
#
# Usage: qa/score.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]
set -uo pipefail

MD="$1"; STATE="$2"; RUBRIC="$3"; SCHEMA="$4"; OUT="$5"; BUDGET="${6:-1.50}"

INPUT="$(printf '%s\n\n# ===== OUTPUT FORMAT =====\nRespond with ONLY a single JSON object conforming to this schema — no prose, no markdown, no code fences:\n%s\n\n# ===== DISTILLED TRANSCRIPT =====\n%s\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n%s\n' \
  "$(cat "$RUBRIC")" "$(cat "$SCHEMA")" "$(cat "$MD")" "$(cat "$STATE")")"

# NB: --json-schema was found to suppress the result text in this CLI; we rely on the
# JSON-only instruction embedded in the prompt and strip any stray code fences.
claude -p "$INPUT" \
  --model sonnet --permission-mode bypassPermissions \
  --max-budget-usd "$BUDGET" \
  --output-format json 2> "${OUT%.json}.err" \
  | jq -r '.result' \
  | sed -E '/^```/d' > "$OUT" 2>/dev/null
