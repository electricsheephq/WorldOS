#!/usr/bin/env bash
# Score ONE distilled transcript against a rubric+schema using Codex/GPT.
#
# Drop-in signature parity with qa/score.sh:
#   qa/score_codex.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]
#
# This is the same-family scorer for Codex/OpenAI proof lanes. It intentionally does
# not invoke claude, so a GPT-only user/provider proof does not depend on Anthropic
# quota or auth. The optional budget argument is accepted for caller parity but not
# passed to Codex CLI.
set -uo pipefail

if [ "$#" -lt 5 ]; then
  echo "usage: qa/score_codex.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MD="$1"; STATE="$2"; RUBRIC="$3"; SCHEMA="$4"; OUT="$5"; BUDGET="${6:-1.50}"
SCORER_MODEL="${WORLDOS_SCORER_MODEL:-${WORLDOS_CODEX_MODEL:-gpt-5.5}}"

command -v codex >/dev/null 2>&1 || {
  echo "[score_codex] codex CLI is required for Codex/GPT scoring" >&2
  exit 127
}

codex_config_path() {
  if [ -n "${CODEX_HOME:-}" ]; then
    printf '%s/config.toml\n' "$CODEX_HOME"
  elif [ -n "${HOME:-}" ]; then
    printf '%s/.codex/config.toml\n' "$HOME"
  fi
}

codex_top_level_service_tier() {
  python3 - "$1" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    text = path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("["):
        break
    match = re.match(r"""service_tier\s*=\s*(['"]?)([^'"\s#]+)\1""", line)
    if match:
        print(match.group(2))
        break
PY
}

validate_codex_service_tier() {
  local config tier
  config="$(codex_config_path)"
  [ -n "${config//[[:space:]]/}" ] || return 0
  [ -f "$config" ] || return 0
  tier="$(codex_top_level_service_tier "$config")"
  case "$tier" in
    ""|fast|flex) return 0 ;;
    *)
      echo "[score_codex] Codex CLI config drift: service_tier must be unset, 'fast', or 'flex' in $config for codex-cli >=0.128.0; found '$tier'. Run scripts/codex_qa_home.sh and set CODEX_HOME, or update the selected Codex config." >&2
      exit 2
      ;;
  esac
}

validate_codex_service_tier

PROMPT_FILE="$(mktemp "${TMPDIR:-/tmp}/worldos-score-codex.XXXXXX.prompt")"
LAST="${OUT%.json}.last.txt"
RAW="${OUT%.json}.codex.raw.jsonl"
ERR="${OUT%.json}.codex.err"
trap 'rm -f "$PROMPT_FILE"' EXIT

python3 - "$RUBRIC" "$SCHEMA" "$MD" "$STATE" "$BUDGET" > "$PROMPT_FILE" <<'PY'
import sys
rubric, schema, transcript, state, budget = sys.argv[1:]
parts = [
    open(rubric, encoding="utf-8").read(),
    "",
    "# ===== OUTPUT FORMAT =====",
    "Respond with ONLY a single JSON object conforming to this schema — no prose, no markdown, no code fences:",
    open(schema, encoding="utf-8").read(),
    "",
    "# ===== SCORER CONTEXT =====",
    "Provider family: codex-openai",
    "Scoring budget argument accepted for caller compatibility: " + budget,
    "",
    "# ===== DISTILLED TRANSCRIPT =====",
    open(transcript, encoding="utf-8").read(),
    "",
    "# ===== FINAL ENGINE STATE (ground truth) =====",
    open(state, encoding="utf-8").read(),
]
sys.stdout.write("\n".join(parts))
PY

MODEL_ARGS=()
case "$(printf '%s' "$SCORER_MODEL" | tr '[:upper:]' '[:lower:]')" in
  ""|auto|default|cli-default) ;;
  *) MODEL_ARGS=(--model "$SCORER_MODEL") ;;
esac

extract_scorecard() {
  python3 - "$LAST" "$OUT" <<'PY'
import json
import re
import sys
from pathlib import Path

last = Path(sys.argv[1])
out = Path(sys.argv[2])
text = last.read_text(encoding="utf-8", errors="ignore") if last.exists() else ""
text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
match = re.search(r"\{.*\}", text, flags=re.S)
if not match:
    raise SystemExit(1)
payload = json.loads(match.group(0))
if not isinstance(payload, dict) or not isinstance(payload.get("scores"), dict):
    raise SystemExit(1)
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))
  : > "$LAST"
  : > "$RAW"
  : > "$ERR"
  status=0
  codex exec \
    --sandbox read-only \
    --json \
    ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --cd "$ROOT" \
    --output-last-message "$LAST" \
    - < "$PROMPT_FILE" > "$RAW" 2> "$ERR" || status=$?

  if [ "$status" -eq 0 ] && extract_scorecard; then
    exit 0
  fi

  echo "[score_codex] attempt $attempt failed for $(basename "$OUT") (codex rc=$status); stderr tail:" >&2
  tail -n 20 "$ERR" >&2 2>/dev/null || true
  sleep 5
done

echo "[score_codex] FAILED after $attempt attempts: $OUT" >&2
exit 1
