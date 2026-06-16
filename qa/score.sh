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
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- (a) Scorer-model pinning (DETERMINISM GUARD; additive — unset env == today) --------
# The scorer model is PINNED at a single canonical model (sonnet — the documented gate
# baseline; never flip it casually). Setting CLAWDND_SCORER_MODEL swaps the scorer, which
# silently skews every score on the gate. So an override is only honored with an EXPLICIT
# opt-in (CLAWDND_ALLOW_SCORER_OVERRIDE=1) for a deliberate scorer-calibration probe /
# re-baseline (e.g. "does a stronger scorer read Opus craft higher than sonnet does?") —
# see docs/MODEL-TIERING. CLAWDND_SCORER_MODEL set WITHOUT the opt-in is an ERROR, so a
# stray env var can't quietly move the baseline.
CANONICAL_SCORER_MODEL="sonnet"
if [ -n "${CLAWDND_SCORER_MODEL:-}" ] && [ "${CLAWDND_ALLOW_SCORER_OVERRIDE:-}" != "1" ]; then
  echo "[score] REFUSING scorer-model override: CLAWDND_SCORER_MODEL='${CLAWDND_SCORER_MODEL}' is set but CLAWDND_ALLOW_SCORER_OVERRIDE=1 is NOT." >&2
  echo "[score]   The scorer is pinned to '${CANONICAL_SCORER_MODEL}' (the gate baseline); a silent model swap skews every score." >&2
  echo "[score]   To deliberately re-baseline with a different scorer, also export CLAWDND_ALLOW_SCORER_OVERRIDE=1." >&2
  exit 3
fi
SCORER_MODEL="${CLAWDND_SCORER_MODEL:-$CANONICAL_SCORER_MODEL}"

# --- (b) prompt_construction_hash: rubric+schema+template fingerprint (NOT the transcript)
# Computed by the shared helper so score.sh and the test agree by construction. Used to
# detect rubric/prompt-template drift across versions. Additive: it's stamped into the OUT
# JSON after the scorecard is produced; the scorecard content is otherwise unchanged.
PROMPT_HASH="$(python3 "$HERE/_score_prompt_hash.py" "$RUBRIC" "$SCHEMA")" || {
  echo "[score] failed to compute prompt_construction_hash from $RUBRIC + $SCHEMA" >&2
  exit 4
}

INPUT="$(printf '%s\n\n# ===== OUTPUT FORMAT =====\nRespond with ONLY a single JSON object conforming to this schema — no prose, no markdown, no code fences:\n%s\n\n# ===== DISTILLED TRANSCRIPT =====\n%s\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n%s\n' \
  "$(cat "$RUBRIC")" "$(cat "$SCHEMA")" "$(cat "$MD")" "$(cat "$STATE")")"

ERR="${OUT%.json}.err"
RAW="${OUT%.json}.raw.json"   # raw claude --output-format json envelope (kept for the guard)

# stamp_prompt_hash <json-file>: merge the prompt_construction_hash into a score JSON in place.
stamp_prompt_hash() {
  local f="$1" tmp
  tmp="$(mktemp "${f}.XXXXXX")"
  if jq --arg h "$PROMPT_HASH" '. + {prompt_construction_hash: $h}' "$f" > "$tmp" 2>/dev/null; then
    mv "$tmp" "$f"
  else
    rm -f "$tmp"
  fi
}

# --- test-only dry-run hook (additive; unset == today) ---------------------------------
# CLAWDND_SCORE_GUARD_ONLY=1 runs all guards + emits the hashed artifact, then exits 0
# BEFORE the live `claude -p` loop. This keeps the determinism test gateway-free / offline
# (it never touches Eva, the gateway, or any LLM). In normal use this is unset and the
# script behaves exactly as before.
if [ "${CLAWDND_SCORE_GUARD_ONLY:-}" = "1" ]; then
  printf '{}\n' > "$OUT"
  stamp_prompt_hash "$OUT"
  exit 0
fi

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))
  # The rubric+schema+transcript+state prompt is ~100-200KB. Passing it as a single
  # argv (`claude -p "$INPUT"`) is what `execve` carries, and macOS counts the WHOLE
  # argv + the full environment against ARG_MAX (1 MB). Under the parallel QA swarm the
  # inherited env is fat (plugin/MCP/state vars), so prompt + env crosses 1 MB →
  # `Argument list too long` (E2BIG) → claude never runs → a 0-byte $OUT that the old
  # retry loop MISREAD as a transient auth/rate blip. Pipe via STDIN instead: `claude -p`
  # with no positional prompt reads the prompt from stdin (the documented pipe path), so
  # the prompt never touches the argv budget. Model, flags, and output schema unchanged.
  #
  # --json-schema was found to suppress the result text in this CLI; we rely on the
  # JSON-only instruction in the prompt and strip any stray code fences.
  printf '%s' "$INPUT" | claude -p \
    --model "$SCORER_MODEL" --permission-mode bypassPermissions \
    --max-budget-usd "$BUDGET" \
    --output-format json > "$RAW" 2> "$ERR"

  # The scorecard text lives at .result; strip any stray code fences.
  jq -r '.result // empty' "$RAW" 2>/dev/null | sed -E '/^```/d' > "$OUT" 2>/dev/null

  # A valid scorecard parses as JSON AND carries a .scores object.
  if jq -e '.scores' "$OUT" >/dev/null 2>&1; then
    stamp_prompt_hash "$OUT"   # (b) record rubric/prompt-template fingerprint for drift detection
    rm -f "$RAW"
    exit 0
  fi

  # GUARD: distinguish a genuine API error / E2BIG / dead process from a transient blip,
  # and FAIL LOUDLY in the non-transient cases instead of silently calling it "auth/rate".
  api_err="$(jq -r 'select(.is_error == true) | .api_error_status // .subtype // "error"' "$RAW" 2>/dev/null)"
  if [ ! -s "$RAW" ]; then
    # No envelope at all → claude itself never produced output (E2BIG, killed, exec fail).
    echo "[score] attempt $attempt: EMPTY output for $(basename "$OUT") — claude wrote NOTHING to stdout. This is NOT a rate blip (likely E2BIG / killed process). stderr tail:" >&2
    tail -n 20 "$ERR" >&2 2>/dev/null || echo "[score]   (no stderr captured at $ERR)" >&2
  elif [ -n "$api_err" ]; then
    # A real API-error envelope (e.g. 401 auth, 400, overload). Surface it — don't bury it.
    echo "[score] attempt $attempt: API ERROR ($api_err) for $(basename "$OUT"): $(jq -r '.result // "<no message>"' "$RAW" 2>/dev/null | head -1)" >&2
  else
    echo "[score] attempt $attempt: unparseable scorecard / missing .scores for $(basename "$OUT") (possibly transient); retrying…" >&2
  fi
  sleep 5
done

echo "[score] FAILED after $attempt attempts: $OUT — last stderr tail:" >&2
tail -n 20 "$ERR" >&2 2>/dev/null || echo "[score]   (no stderr captured at $ERR)" >&2
rm -f "$RAW"
exit 1
