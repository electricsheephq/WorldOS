#!/usr/bin/env bash
# Score ONE distilled transcript against a rubric+schema using the OpenClaw gateway
# (clawdnd-qa agent, model openai/gpt-5.4).  Drop-in replacement for score.sh — same
# arg signature, same out.json shape, same jq gate on `.scores`.
#
# The `claude -p` scorer is weekly-rate-limited; this script rides a separate quota via
# the OpenClaw gateway so both can run independently.
#
# Reply envelope path (confirmed by probe):
#   .result.payloads[0].text  (see: openclaw agent --json | jq 'paths')
#
# Resilient: under heavy parallelism the gateway can return a non-JSON blip or an empty
# payload. We retry up to 3 times (same policy as score.sh), sleeping 5 s between
# attempts. A valid scorecard must parse AND carry a `.scores` object.
#
# NOTE: wave3 transcripts (~100-200KB prompts) require --timeout 600. The prompt is
# written to a temp file and read back to avoid any shell arg-length corner cases with
# very large rubrics.
#
# Usage: qa/score_openclaw.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]
# (budget arg accepted for interface parity but not passed to the gateway — the agent
# quota is managed by OpenClaw, not by a USD cap on the CLI side.)
set -uo pipefail

MD="$1"; STATE="$2"; RUBRIC="$3"; SCHEMA="$4"; OUT="$5"
# budget ($6) accepted for API parity but unused — OpenClaw manages quota
BUDGET="${6:-1.50}"

AGENT="${CLAWDND_SCORER_AGENT:-clawdnd-qa}"
MODEL="${CLAWDND_SCORER_MODEL:-openai/gpt-5.4}"
# 600s: large rubrics (angry_dm ~32KB) + long transcripts (~100KB) need the room
GATEWAY_TIMEOUT="${CLAWDND_SCORER_TIMEOUT:-600}"

# Build the same prompt body score.sh uses: rubric + schema instruction + transcript + state
# Write to a temp file so very large prompts don't hit shell variable limits
PROMPT_FILE="$(mktemp /tmp/score_openclaw_prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
python3 -c "
import sys
r = open(sys.argv[1]).read()
s = open(sys.argv[2]).read()
m = open(sys.argv[3]).read()
st = open(sys.argv[4]).read()
prompt = (r + '\n\n# ===== OUTPUT FORMAT =====\n'
          'Respond with ONLY a single JSON object conforming to this schema'
          ' — no prose, no markdown, no code fences:\n'
          + s + '\n\n# ===== DISTILLED TRANSCRIPT =====\n'
          + m + '\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n'
          + st + '\n')
sys.stdout.write(prompt)
" "$RUBRIC" "$SCHEMA" "$MD" "$STATE" > "$PROMPT_FILE"

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))

  # Call the OpenClaw gateway.  Reply text is at .result.payloads[0].text
  RAW_REPLY="$(openclaw agent \
    --agent "$AGENT" \
    --model "$MODEL" \
    --message "$(cat "$PROMPT_FILE")" \
    --json \
    --timeout "$GATEWAY_TIMEOUT" \
    2>"${OUT%.json}.oc.err" \
    | jq -r '.result.payloads[0].text // empty' 2>/dev/null)"

  # Strip any markdown code fences the model wrapped around the JSON
  # Find the outermost {...} block — same approach as score.sh's sed + jq pipeline
  STRIPPED="$(printf '%s\n' "$RAW_REPLY" \
    | sed -E '/^```/d' \
    | python3 -c "
import sys, re
text = sys.stdin.read()
# Extract outermost {...} object
m = re.search(r'\{.*\}', text, re.DOTALL)
if m:
    print(m.group(0))
" 2>/dev/null)"

  if [ -z "$STRIPPED" ]; then
    echo "[score_openclaw] attempt $attempt: empty reply for $(basename "$OUT") (transient gateway/rate?); retrying…" >&2
    sleep 5
    continue
  fi

  printf '%s\n' "$STRIPPED" > "$OUT"

  # A valid scorecard parses as JSON AND carries a .scores object (same gate as score.sh).
  if jq -e '.scores' "$OUT" >/dev/null 2>&1; then
    exit 0
  fi

  echo "[score_openclaw] attempt $attempt: no valid scorecard for $(basename "$OUT") (bad JSON or missing .scores); retrying…" >&2
  sleep 5
done

echo "[score_openclaw] FAILED after $attempt attempts: $OUT" >&2
exit 1
