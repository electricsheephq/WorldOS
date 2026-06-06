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

# Default agent = `main` (the canonical gateway agent; the old `clawdnd-qa` default isn't configured on
# every host). By DEFAULT pass NO --model override (use the agent's native model, e.g. main=gpt-5.5) —
# many gateway agents REJECT a foreign model override ("Model override … is not allowed for agent").
# Only pass one when CLAWDND_SCORER_MODEL is explicitly set AND allowed for the agent.
AGENT="${CLAWDND_SCORER_AGENT:-main}"
MODEL="${CLAWDND_SCORER_MODEL:-}"
MODEL_ARGS=(); [ -n "$MODEL" ] && MODEL_ARGS=(--model "$MODEL")
# A fresh session id per scoring run so a scorer turn never pollutes the agent's main session.
SESSION_ID="${CLAWDND_SCORER_SESSION:-qa-score-$(basename "${OUT%.json}")}"
# openclaw agent has NO stdin/file message input — the prompt is a single --message argv, bounded by
# MAX_ARG_STRLEN (~128KB). The state.json alone can be ~140KB, so cap it (the distilled transcript
# carries the prose; the state is supplementary ground-truth). Tune via CLAWDND_SCORER_STATE_CAP.
STATE_CAP="${CLAWDND_SCORER_STATE_CAP:-75000}"
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
cap = int(sys.argv[5])
if len(st) > cap:
    st = st[:cap] + '\n…[FINAL STATE truncated to fit the gateway message size limit]…\n'
prompt = (r + '\n\n# ===== OUTPUT FORMAT =====\n'
          'Respond with ONLY a single JSON object conforming to this schema'
          ' — no prose, no markdown, no code fences:\n'
          + s + '\n\n# ===== DISTILLED TRANSCRIPT =====\n'
          + m + '\n\n# ===== FINAL ENGINE STATE (ground truth) =====\n'
          + st + '\n')
sys.stdout.write(prompt)
" "$RUBRIC" "$SCHEMA" "$MD" "$STATE" "$STATE_CAP" > "$PROMPT_FILE"

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))

  # Call the OpenClaw gateway.  Reply text is at .result.payloads[0].text
  RAW_REPLY="$(openclaw agent \
    --agent "$AGENT" \
    ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --session-id "${SESSION_ID}-${attempt}" \
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
    # GUARD: an empty reply is NOT automatically a rate blip. The gateway may have
    # rejected the call outright (e.g. the ~100-200KB prompt + a fat environment
    # crossing the execve ARG_MAX budget when passed via --message, or an auth
    # failure). Surface the real stderr tail instead of silently calling it transient.
    echo "[score_openclaw] attempt $attempt: EMPTY reply for $(basename "$OUT") — gateway returned no payload text. This may be E2BIG (prompt too large for --message argv), auth, or rate. stderr tail:" >&2
    tail -n 20 "${OUT%.json}.oc.err" >&2 2>/dev/null || echo "[score_openclaw]   (no stderr captured at ${OUT%.json}.oc.err)" >&2
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

echo "[score_openclaw] FAILED after $attempt attempts: $OUT — last stderr tail:" >&2
tail -n 20 "${OUT%.json}.oc.err" >&2 2>/dev/null || echo "[score_openclaw]   (no stderr captured at ${OUT%.json}.oc.err)" >&2
exit 1
