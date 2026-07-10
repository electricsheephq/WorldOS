#!/usr/bin/env bash
# vqa_frame.sh — ONE factual visual-QA pass over ONE journey frame (qa/journey_eval.py's default scorer).
#
# Reads {"questions":[{"flag","text"},...]} on STDIN, asks a single `claude -p` (sonnet) to answer each
# YES/NO from what is literally visible in the IMAGE at $1, and prints {"flags":{flag:bool,...}} to
# STDOUT. Every question is YES=defect (see qa/journey_vqa_questions.md); the caller fails the journey on
# any true flag.
#
# Auth-isolation MIRRORS qa/score.sh (the canonical instrument pattern, measured across #1260/#1404): a
# fresh scorer-only CLAUDE_CONFIG_DIR + an EXPLICIT keychain/.credentials OAuth token + a GLM-neutralised
# env, so the VQA call always hits real Anthropic on the pinned model regardless of host routing. The
# image is passed by ABSOLUTE PATH and read via the Read tool under --permission-mode bypassPermissions.
#
# Usage:  qa/vqa_frame.sh <frame.png>   < questions.json
# Env:    WORLDOS_VQA_MODEL (default sonnet) · WORLDOS_VQA_TIMEOUT (default 180)
#         WORLDOS_VQA_GUARD_ONLY=1 — build the prompt + emit an empty {"flags":{}} and exit 0 BEFORE any
#         LLM call (offline/CI wiring proof, no cost — mirrors score.sh's WORLDOS_SCORE_GUARD_ONLY).
set -uo pipefail

IMG="${1:?usage: vqa_frame.sh <frame.png> < questions.json}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${WORLDOS_VQA_MODEL:-sonnet}"
TIMEOUT="${WORLDOS_VQA_TIMEOUT:-180}"

if [ ! -f "$IMG" ]; then echo "[vqa] no such frame: $IMG" >&2; exit 4; fi
IMG_ABS="$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")"

# Capture the stdin question set ONCE (the heredoc below feeds the python program on stdin, so the
# question JSON must travel via env, not stdin).
QUESTIONS_JSON="$(cat)"

# Build the prompt from the question set (python so quoting/newlines can never break the JSON).
PROMPT="$(WORLDOS_VQA_IMG="$IMG_ABS" WORLDOS_VQA_QJSON="$QUESTIONS_JSON" python3 <<'PY'
import json, os, sys
img = os.environ["WORLDOS_VQA_IMG"]
qs = json.loads(os.environ["WORLDOS_VQA_QJSON"]).get("questions", [])
lines = [
    "You are a strict visual-QA inspector. Use the Read tool to open the image file at this absolute",
    f"path: {img}", "",
    "Answer each question below with a literal YES or NO based ONLY on what is visibly in the image",
    "(no lore, no guessing intent). Each question is phrased so YES means a DEFECT is present.", "",
]
for i, q in enumerate(qs, 1):
    lines.append(f"{i}. [{q['flag']}] {q['text']}")
lines += [
    "",
    "Return TEXT-ONLY JSON — no prose, no markdown, no code fences — of exactly this shape:",
    '{"flags": {' + ", ".join(f'"{q["flag"]}": true|false' for q in qs) + '}, "notes": "<=1 sentence"}',
    "true = the defect IS present, false = it is not.",
]
sys.stdout.write("\n".join(lines))
PY
)"

if [ "${WORLDOS_VQA_GUARD_ONLY:-}" = "1" ]; then printf '{"flags":{}}\n'; exit 0; fi

# --- auth isolation (score.sh #1260/#1404 pattern, condensed) --------------------------------------
_cfg="${TMPDIR:-/tmp}/worldos-vqa-config"
mkdir -p "$_cfg"; [ -s "$_cfg/settings.json" ] || printf '{}' > "$_cfg/settings.json"
_tok="${CLAUDE_CODE_OAUTH_TOKEN:-}"
if [ -z "$_tok" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    _blob="$(security find-generic-password -s 'Claude Code-credentials' -a "$USER" -w 2>/dev/null || true)"
  else
    _cf="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"; [ -s "$_cf" ] && _blob="$(cat "$_cf" 2>/dev/null || true)"
  fi
  [ -n "${_blob:-}" ] && _tok="$(printf '%s' "$_blob" | python3 -c 'import json,sys
try: d=json.load(sys.stdin).get("claudeAiOauth",{})
except Exception: d={}
sys.stdout.write(d.get("accessToken") or "")' 2>/dev/null || true)"
fi

RAW="$(mktemp)"; ERR="$(mktemp)"
trap 'rm -f "$RAW" "$ERR"' EXIT

attempt=0
while [ "$attempt" -lt 3 ]; do
  attempt=$((attempt + 1))
  printf '%s' "$PROMPT" | env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
    -u CLAUDECODE -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT \
    -u CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH -u CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH -u CLAUDE_CODE_SESSION_ID \
    CLAUDE_CONFIG_DIR="$_cfg" \
    ${_tok:+CLAUDE_CODE_OAUTH_TOKEN="$_tok"} \
    timeout "$TIMEOUT" claude -p \
    --model "$MODEL" --permission-mode bypassPermissions \
    --output-format json > "$RAW" 2> "$ERR"

  # The answer text lives at .result; strip any stray code fences, keep the {...} object.
  RES="$(jq -r '.result // empty' "$RAW" 2>/dev/null | sed -E '/^```/d')"
  FLAGS="$(printf '%s' "$RES" | python3 -c 'import json,sys,re
t=sys.stdin.read()
m=re.search(r"\{.*\}", t, re.DOTALL)
try:
    obj=json.loads(m.group(0)) if m else {}
    fl=obj.get("flags", {})
    assert isinstance(fl, dict)
    print(json.dumps({"flags": {k: bool(v) for k,v in fl.items()}}))
except Exception:
    sys.exit(1)' 2>/dev/null || true)"
  if [ -n "$FLAGS" ]; then printf '%s\n' "$FLAGS"; exit 0; fi

  api_err="$(jq -r 'select(.is_error==true) | .api_error_status // .subtype // "error"' "$RAW" 2>/dev/null)"
  if [ "$api_err" = "401" ] || [ "$api_err" = "429" ]; then
    echo "[vqa] AUTH/QUOTA error ($api_err) on $(basename "$IMG") — failing fast." >&2; break; fi
  echo "[vqa] attempt $attempt: no parseable flags for $(basename "$IMG") (api_err=${api_err:-none}); retrying." >&2
  sleep 4
done

echo "[vqa] FAILED after $attempt attempts for $IMG — stderr tail:" >&2
tail -n 15 "$ERR" >&2 2>/dev/null || true
exit 1
