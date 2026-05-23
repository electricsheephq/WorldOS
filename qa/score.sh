#!/usr/bin/env bash
# Score ONE distilled transcript against a rubric+schema in a single `claude -p` pass.
# Generalizes the inline scorer so any lens (mechanical rubric, Tolkien story-craft,
# …) can be applied to any transcript — including re-scoring old ones.
#
# Resilient: under heavy parallelism the CLI can transiently return a non-JSON blip
# ("Not logged in", a rate bounce). We retry a few times — a valid scorecard must
# parse AND carry a `.scores` object, else we retry; only after N tries do we fail.
#
# Usage: qa/score.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]
set -uo pipefail

MD="$1"; STATE="$2"; RUBRIC="$3"; SCHEMA="$4"; OUT="$5"; BUDGET="${6:-1.50}"

INPUT="$(printf '%s\n\n# ===== OUTPUT FORMAT =====\nRespond with ONLY a single JSON object conforming to this schema — no prose, no markdown, no code fences:\n%s\n\n# ===== DISTILLED TRANSCRIPT =====\n%s\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n%s\n' \
  "$(cat "$RUBRIC")" "$(cat "$SCHEMA")" "$(cat "$MD")" "$(cat "$STATE")")"

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))
  # --json-schema was found to suppress the result text in this CLI; we rely on the
  # JSON-only instruction in the prompt and strip any stray code fences.
  claude -p "$INPUT" \
    --model sonnet --permission-mode bypassPermissions \
    --max-budget-usd "$BUDGET" \
    --output-format json 2> "${OUT%.json}.err" \
    | jq -r '.result // empty' 2>/dev/null \
    | sed -E '/^```/d' > "$OUT" 2>/dev/null

  # A valid scorecard parses as JSON AND carries a .scores object.
  if jq -e '.scores' "$OUT" >/dev/null 2>&1; then
    exit 0
  fi
  echo "[score] attempt $attempt: no valid scorecard for $(basename "$OUT") (transient auth/rate?); retrying…" >&2
  sleep 5
done

echo "[score] FAILED after $attempt attempts: $OUT" >&2
exit 1
