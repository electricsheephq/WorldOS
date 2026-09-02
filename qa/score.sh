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
# baseline; never flip it casually). Setting WORLDOS_SCORER_MODEL swaps the scorer, which
# silently skews every score on the gate. So an override is only honored with an EXPLICIT
# opt-in (WORLDOS_ALLOW_SCORER_OVERRIDE=1) for a deliberate scorer-calibration probe /
# re-baseline (e.g. "does a stronger scorer read Opus craft higher than sonnet does?") —
# see docs/MODEL-TIERING. WORLDOS_SCORER_MODEL set WITHOUT the opt-in is an ERROR, so a
# stray env var can't quietly move the baseline. The legacy WORLDOS_* names are still read
# as a fallback (the worldos->worldos rename bi-names direct readers) so old call sites work.
CANONICAL_SCORER_MODEL="sonnet"
SCORER_MODEL_OVERRIDE="${WORLDOS_SCORER_MODEL:-}"
ALLOW_SCORER_OVERRIDE="${WORLDOS_ALLOW_SCORER_OVERRIDE:-}"
if [ -n "$SCORER_MODEL_OVERRIDE" ] && [ "$ALLOW_SCORER_OVERRIDE" != "1" ]; then
  echo "[score] REFUSING scorer-model override: WORLDOS_SCORER_MODEL='${SCORER_MODEL_OVERRIDE}' is set but WORLDOS_ALLOW_SCORER_OVERRIDE=1 is NOT." >&2
  echo "[score]   The scorer is pinned to '${CANONICAL_SCORER_MODEL}' (the gate baseline); a silent model swap skews every score." >&2
  echo "[score]   To deliberately re-baseline with a different scorer, also export WORLDOS_ALLOW_SCORER_OVERRIDE=1." >&2
  exit 3
fi
SCORER_MODEL="${SCORER_MODEL_OVERRIDE:-$CANONICAL_SCORER_MODEL}"

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

# --- (c) Scorer EFFORT (#1040 latency). The Angry-DM 5e-fidelity lens is the HEAVY one (~32 KB rubric);
# at default effort it ran ~400s — far too long for a GRADING call. Scoring is checklist-application, not
# generation, so LOW effort holds quality while cutting wall-clock ~2.2x (measured on csmed-1: 402s -> 179s,
# a valid full scorecard). Auto-apply low effort to the angrydm lens BY RUBRIC NAME; the story (tolkien)
# lens — quality-critical AND already fast (~60-150s) — stays at default. Override per call with
# WORLDOS_SCORER_EFFORT (low|medium|high|max, or empty "" to force NO flag). Uses `-` (not `:-`) so an
# explicit empty override means "no --effort", while unset uses the per-rubric default.
case "$RUBRIC" in
  *angry_dm*) _EFFORT_DEFAULT="low" ;;
  *)          _EFFORT_DEFAULT="" ;;
esac
SCORER_EFFORT="${WORLDOS_SCORER_EFFORT-$_EFFORT_DEFAULT}"
EFFORT_ARG=""
[ -n "$SCORER_EFFORT" ] && EFFORT_ARG="--effort $SCORER_EFFORT"

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
# WORLDOS_SCORE_GUARD_ONLY=1 runs all guards + emits the hashed artifact, then exits 0
# BEFORE the live `claude -p` loop. This keeps the determinism test gateway-free / offline
# (it never touches Eva, the gateway, or any LLM). In normal use this is unset and the
# script behaves exactly as before.
if [ "${WORLDOS_SCORE_GUARD_ONLY:-}" = "1" ]; then
  printf '{}\n' > "$OUT"
  stamp_prompt_hash "$OUT"
  exit 0
fi

attempt=0
LAST_API_ERROR="unknown"   # WS0a: captured per-attempt; stamped into the scorer_failed sentinel below
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
  # GLM ISOLATION: the scorer is canonical Claude infrastructure (pinned sonnet, the gate
  # baseline) and MUST run on clean Claude regardless of which model PLAYED the game. When the
  # run is GLM, qa/glm_profile.sh exports ANTHROPIC_BASE_URL=z.ai + the GLM key + a fresh
  # CLAUDE_CONFIG_DIR globally — which would route THIS scorer call to z.ai with a Claude model
  # name (→ "Unknown Model") and skew/abort every score. Neutralize those vars for the scorer
  # so it uses the default ~/.claude (Claude OAuth) + api.anthropic.com. On a normal Claude run
  # these vars are unset, so `env -u …` is a NO-OP → byte-identical to today.
  # TIMEOUT GUARD: `claude -p` occasionally HANGS (a stuck stream / a slow response that never
  # returns) — without a wall-clock bound that blocks the ENTIRE run forever. `timeout` kills a hung
  # call so the retry loop below catches it (empty $RAW → the EMPTY branch → retry).
  # Default 600s (#1040): the social lenses (tolkien/mechanical) finish in ~60–150s, but the
  # Angry-DM 5e-fidelity lens (rubric_angry_dm.md is ~32 KB, ~3× tolkien) LEGITIMATELY takes ~400s
  # to grade a COMBAT-DENSE transcript — a single-turn generation, MEASURED 402s on csmed-1 (num_turns=1,
  # valid 7.7 KB scorecard). The old 300s default KILLED that mid-generation → empty stdout that LOOKED
  # like a hang (the #1040 "combat-scorer hang" was a too-short timeout, not a true hang). 600s covers it
  # with headroom; the fast lenses are unaffected (the bound only fires on a genuinely slow/stuck call).
  # run_duo.sh ALSO isolates the angrydm lens (scores it alone, not concurrent with the 2 light lenses)
  # so it gets full API throughput and lands near the ~400s baseline rather than slower under contention.
  # #1260 round 2: env scrubbing alone is NOT enough — with the process env clean, the child
  # CLI re-applies ~/.claude/settings.json's `env` block, and on this host that block routes
  # to the z.ai/GLM proxy (measured: the scrubbed scorer scored on a GLM-mapped model and died
  # on its context window). The scorer is the CANONICAL instrument (pinned sonnet, real
  # Anthropic, keychain auth) and must be immune to user routing config, so it runs under a
  # FRESH scorer-only CLAUDE_CONFIG_DIR with an empty settings.json (measured to hit the
  # default endpoint on keychain auth). The SDK child-session markers are still scrubbed —
  # they make a detached child wait for host-provided auth that never comes (rc=124/401).
  _scorer_cfg="${TMPDIR:-/tmp}/worldos-scorer-config"
  mkdir -p "$_scorer_cfg"
  [ -s "$_scorer_cfg/settings.json" ] || printf '{}' > "$_scorer_cfg/settings.json"
  # #1260 round 3 (measured): a FRESH config dir has no login state, and the CLI does NOT
  # fall back to the default keychain identity from there -> "Not logged in". On macOS,
  # derive the scorer credential EXPLICITLY from the CLI keychain item and hand it to the
  # child as CLAUDE_CODE_OAUTH_TOKEN (same isolated-config+env-credential pattern the duo
  # runner's auth block uses; measured to produce valid scorecards). The token is passed
  # via the child env only — never printed, never written. A caller-provided
  # CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY still wins (first clause).
  # Linux fallback (VM sweep hosts, #1264/#1266 follow-up): there is no Keychain on Linux —
  # login state instead lives in a plain ~/.claude/.credentials.json (the CLI writes it there
  # after `claude login`/token refresh). Same shape as the Keychain blob
  # ({"claudeAiOauth":{"accessToken":...}}), so reuse the identical jq/python extraction.
  # Respects CLAUDE_CONFIG_DIR if the caller already points it somewhere non-default. This
  # branch never fires on Darwin (Keychain branch above wins there) and is a no-op if the
  # file is absent — falls through to the existing "Not logged in" retry/failure path.
  #
  # NOTE (CodeRabbit, PR #1279): gating derivation on ANTHROPIC_API_KEY being UNSET in the
  # parent shell is wrong — the child invocation below unconditionally strips
  # ANTHROPIC_API_KEY via `env -u ANTHROPIC_API_KEY` regardless of what the parent had. If a
  # caller's shell happens to export ANTHROPIC_API_KEY (common on a shared VM), the old gate
  # skipped derivation, the key was stripped anyway, and the child got NO auth at all — the
  # exact "Not logged in" failure this fix exists to solve. Derivation must only be gated on
  # whether we already HAVE a token (first clause), not on an env var the child never sees.
  # #1404: capture the token's expiresAt (epoch ms) and its source ALONGSIDE the accessToken, so
  # we can PROACTIVELY detect an expired credential and fail fast with an actionable diagnostic
  # (gate (d) below) instead of 401ing three times into a generic scorer_failed corpse that reads
  # like a transient blip. A caller-provided CLAUDE_CODE_OAUTH_TOKEN still wins and is used as-is —
  # we can't introspect its expiry, so the pre-check simply doesn't fire for that path (unchanged).
  _scorer_tok="${CLAUDE_CODE_OAUTH_TOKEN:-}"
  _scorer_tok_exp=""           # expiresAt (epoch ms) of a DERIVED token; empty when caller-provided/unknown
  _cred_src=""                 # human-readable source of the derived credential (for the diagnostic)
  if [ -z "$_scorer_tok" ]; then
    _cred_blob=""
    if [ "$(uname)" = "Darwin" ]; then
      _cred_src="macOS Keychain item 'Claude Code-credentials'"
      _cred_blob="$(security find-generic-password -s 'Claude Code-credentials' -a "$USER" -w 2>/dev/null || true)"
    else
      _scorer_creds_file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"
      _cred_src="$_scorer_creds_file"
      [ -s "$_scorer_creds_file" ] && _cred_blob="$(cat "$_scorer_creds_file" 2>/dev/null || true)"
    fi
    if [ -n "$_cred_blob" ]; then
      # Emit "<accessToken>\t<expiresAt-or-empty>" from the shared {claudeAiOauth:{…}} shape
      # (identical extraction to before, now also carrying expiresAt for the expiry gate).
      _cred_line="$(printf '%s' "$_cred_blob" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin).get("claudeAiOauth", {})
except Exception:
    d = {}
exp = d.get("expiresAt")
sys.stdout.write("%s\t%s" % (d.get("accessToken") or "", exp if isinstance(exp, int) else ""))' 2>/dev/null || true)"
      _scorer_tok="${_cred_line%%$'\t'*}"
      _scorer_tok_exp="${_cred_line#*$'\t'}"
    fi
  fi

  # --- (d) PROACTIVE auth-expiry circuit-breaker (#1404) -------------------------------------
  # A DERIVED token whose expiresAt is already past (or lapses within a 60s skew) will 401 on
  # EVERY attempt. Fail fast with a DISTINCT, actionable sentinel — mirroring the 429 quota
  # breaker below — rather than burning the 3 retries + ~15s of sleeps only to land on a generic
  # scorer_failed. No-op when the expiry is unknown/absent (caller-token path, or an old keychain
  # blob without expiresAt), so that behavior is byte-identical to today. This does NOT mint a new
  # token: refreshing OAuth is the CLI's job — `claude login` (or the CLI's own background refresh)
  # rewrites the keychain; re-implementing rotation here would race the CLI under the parallel swarm.
  if [ -n "$_scorer_tok" ] && [[ "$_scorer_tok_exp" =~ ^[0-9]+$ ]]; then
    _now_ms="$(python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null || echo 0)"
    if [ "$_now_ms" != 0 ] && [ "$_scorer_tok_exp" -le "$(( _now_ms + 60000 ))" ]; then
      _ago_s=$(( (_now_ms - _scorer_tok_exp) / 1000 ))
      echo "[score] AUTH EXPIRED for $(basename "$OUT"): scorer OAuth access token (from ${_cred_src}) expired ~${_ago_s}s ago (expiresAt=${_scorer_tok_exp}ms, now=${_now_ms}ms)." >&2
      echo "[score]   Every scoring call 401s until the credential is refreshed. Run \`claude login\` on this host (or let the Claude CLI refresh the keychain), then re-run scoring." >&2
      echo "[score]   Failing fast (no retries); writing an auth sentinel + exiting rc=2." >&2
      printf '{"error":"scorer_auth_expired","expired_at_ms":%s,"expired_ago_seconds":%s}\n' "$_scorer_tok_exp" "$_ago_s" > "$OUT"
      rm -f "$RAW" 2>/dev/null || true
      exit 2
    fi
  fi
  printf '%s' "$INPUT" | env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
    -u API_TIMEOUT_MS \
    -u CLAUDECODE -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT \
    -u CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH -u CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH \
    -u CLAUDE_CODE_SESSION_ID \
    CLAUDE_CONFIG_DIR="$_scorer_cfg" \
    ${_scorer_tok:+CLAUDE_CODE_OAUTH_TOKEN="$_scorer_tok"} \
    timeout "${WORLDOS_SCORE_TIMEOUT:-600}" claude -p \
    --model "$SCORER_MODEL" --permission-mode bypassPermissions $EFFORT_ARG \
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
  # #842 Fix F (quota circuit-breaker): a 429 account-session-limit must FAIL FAST — do NOT burn the
  # 3 retries (re-hitting a quota'd account just wastes the window AND, on the LAST retry, would fall
  # through to the generic "FAILED after N attempts" path that callers can't tell from a real product
  # failure). Detect a 429 (api_error_status==429, or a "session limit"/"429" body) and short-circuit:
  # write an explicit quota sentinel to $OUT (the caller checks .quota_exhausted) and exit rc=2 so a
  # quota corpse can never be mistaken for a valid scorecard.
  if [ "$api_err" = "429" ] || jq -e 'select(.is_error == true) | (.result // "") | test("session limit|HTTP 429|hit your (session|usage) limit"; "i")' "$RAW" >/dev/null 2>&1; then
    echo "[score] QUOTA EXHAUSTED (HTTP 429 account session limit) for $(basename "$OUT") — failing fast (no retries). Writing a quota sentinel + exiting rc=2." >&2
    printf '{"quota_exhausted":true,"api_error_status":429}\n' > "$OUT"
    rm -f "$RAW"
    exit 2
  fi
  # #1404 (auth breaker, live-call half): a 401 (expired/invalid credential) will NOT heal across a
  # 5s sleep — retrying just burns the window and ends in a generic scorer_failed that hides the real
  # cause. Fail fast with the SAME actionable auth sentinel as the proactive pre-check. Covers what
  # the pre-check can't: a token that lapses mid-run, or a caller-provided CLAUDE_CODE_OAUTH_TOKEN we
  # couldn't introspect for expiry.
  if [ "$api_err" = "401" ] || jq -e 'select(.is_error == true) | (.result // "") | test("HTTP 401|invalid.*(auth|credential|token)|authentication_error|OAuth token (has )?expired|Invalid authentication credentials"; "i")' "$RAW" >/dev/null 2>&1; then
    echo "[score] AUTH FAILURE (HTTP 401 / invalid credential) for $(basename "$OUT") — failing fast (no retries)." >&2
    echo "[score]   The scorer credential is expired or invalid. Run \`claude login\` on this host to refresh it, then re-run scoring. Detail: $(jq -r '.result // "<no message>"' "$RAW" 2>/dev/null | head -1)" >&2
    printf '{"error":"scorer_auth_expired","api_error_status":401}\n' > "$OUT"
    rm -f "$RAW"
    exit 2
  fi
  if [ ! -s "$RAW" ]; then
    # No envelope at all → claude itself never produced output (E2BIG, killed, exec fail).
    echo "[score] attempt $attempt: EMPTY output for $(basename "$OUT") — claude wrote NOTHING to stdout (E2BIG / killed / TIMED OUT at ${WORLDOS_SCORE_TIMEOUT:-600}s). Retrying. stderr tail:" >&2
    tail -n 20 "$ERR" >&2 2>/dev/null || echo "[score]   (no stderr captured at $ERR)" >&2
    LAST_API_ERROR="empty_output (E2BIG / killed / timed out at ${WORLDOS_SCORE_TIMEOUT:-600}s)"
  elif [ -n "$api_err" ]; then
    # A real API-error envelope (e.g. 401 auth, 400, overload). Surface it — don't bury it.
    echo "[score] attempt $attempt: API ERROR ($api_err) for $(basename "$OUT"): $(jq -r '.result // "<no message>"' "$RAW" 2>/dev/null | head -1)" >&2
    LAST_API_ERROR="$api_err: $(jq -r '.result // "<no message>"' "$RAW" 2>/dev/null | head -1)"
  else
    echo "[score] attempt $attempt: unparseable scorecard / missing .scores for $(basename "$OUT") (possibly transient); retrying…" >&2
    LAST_API_ERROR="unparseable_scorecard / missing .scores"
  fi
  sleep 5
done

echo "[score] FAILED after $attempt attempts: $OUT — last stderr tail:" >&2
tail -n 20 "$ERR" >&2 2>/dev/null || echo "[score]   (no stderr captured at $ERR)" >&2
# SCORER-INTEGRITY (WS0a): on GENERIC retry-exhaustion (NOT a 429 — that already wrote a
# {quota_exhausted} sentinel + exited rc=2 above) the old code exited rc=1 WITHOUT writing
# anything to $OUT, so the lens file was MISSING/EMPTY. Downstream `jq -r '.overall//"?"'`
# then printed BLANK with no failure indicator — a failed scoring masqueraded as a silent
# valid no-score (observed live: 'story-craft= mechanical= angry-dm= behavioral=GREEN').
# Mirror the 429 sentinel block: ALWAYS leave the lens file as valid JSON carrying an
# explicit {error:"scorer_failed"} marker so run_duo.sh can detect the failure and mark the
# run 'unscorable' instead of reading a blank as a pass. Use a python heredoc (NOT printf with
# a raw $LAST_API_ERROR) so an error string with quotes/newlines can never produce invalid JSON.
python3 - "$OUT" "$attempt" "${LAST_API_ERROR:-unknown}" <<'PY' 2>/dev/null || \
  printf '{"error":"scorer_failed","attempts":%s,"last_api_error":"json_encode_failed"}\n' "$attempt" > "$OUT"
import json, sys
out, attempts, last = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    attempts_n = int(attempts)
except ValueError:
    attempts_n = attempts
json.dump({"error": "scorer_failed", "attempts": attempts_n, "last_api_error": last},
          open(out, "w"))
open(out, "a").write("\n")
PY
rm -f "$RAW"
exit 1
