#!/usr/bin/env bash
# qa/glm_profile.sh — a model-keyed settings profile for running the WorldOS QA
# duo against GLM (z.ai's GLM 5.2, served over an Anthropic-compatible endpoint)
# INSTEAD of Claude. Sourced by qa/run_duo.sh right after the model vars resolve.
#
# CONTRACT (the load-bearing guarantee):
#   • GLM-ONLY. If NEITHER $WORLDOS_DM_MODEL NOR $WORLDOS_ACTOR_MODEL starts with
#     "glm", worldos_apply_glm_profile does NOT apply the GLM profile. On that Claude
#     path it does ONE defensive thing before returning: it SCRUBS any stray GLM-injected
#     env (ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / API_TIMEOUT_MS / a GLM
#     CLAUDE_CONFIG_DIR, and a GLM-looking ANTHROPIC_API_KEY) so switching back to Opus is
#     always clean even if a stray GLM export leaked from the interactive shell. The scrub
#     is a NO-OP when nothing is set, so a normal Claude run with a clean env is byte-for-
#     byte unchanged — this file still never alters a Claude default value.
#   • MIXED-MODEL GUARD. If EXACTLY ONE role is GLM (a half-GLM/half-Claude config, almost
#     always a mistake), it warns to stderr and NORMALIZES both roles to the GLM profile so
#     the run can never silently route one role to z.ai and the other to Anthropic over the
#     shared process env.
#   • When GLM IS the DM/actor model it:
#       - sources the GLM credentials/endpoint from ~/.openclaw/secrets/glm.env
#         (ANTHROPIC_BASE_URL / API key / CLAUDE_CONFIG_DIR) if that file exists;
#         warns to stderr and continues if it does not (the caller may have set
#         them another way). NEVER hardcodes or prints the key — only sources it.
#       - RAISES the cold-open / per-beat timeouts and the DM+player retry ceilings
#         (GLM is slower than Opus/Sonnet and can return an empty intro at beat 0
#         even with no timeout), using ${VAR:-default} so an explicit override
#         from the environment still wins.
#   • Idempotent + safe to source and call more than once.
#
# It NEVER changes Claude defaults: the timeout/retry tiers in lib_beat_driver.sh
# and the Opus budget floor in run_duo.sh are untouched for a Claude run.

# True when $1 names a GLM model (case-insensitive "glm" prefix), false otherwise.
# Kept tiny + bash-3.2-clean (no associative arrays / ${var,,} would also work on
# 3.2's bash but we stay conservative with a case glob on a lowercased copy).
_worldos_is_glm_model() {
  case "$1" in
    glm*|GLM*|Glm*) return 0 ;;
    *) return 1 ;;
  esac
}

# The z.ai endpoint the GLM profile injects (glm.env's ANTHROPIC_BASE_URL). Used by the
# Claude-path defensive unset below to recognize a STRAY GLM base URL in the ambient env
# (so the API-key unset can be conditional on "this really is z.ai"). Kept here as the ONE
# place the host appears in this file; it is the public endpoint host, not a secret.
_WORLDOS_GLM_BASE_URL_HOST="api.z.ai"

# Scrub any GLM-injected endpoint/credential vars from THIS process so a Claude run is clean
# EVEN IF a stray GLM export leaked in from the interactive shell. Called on the Claude (no-GLM)
# path BEFORE worldos_apply_glm_profile returns — the load-bearing "switching back to Opus is
# always clean" guarantee.
#
# DESIGN PRINCIPLE — scrub GLM, never a legitimate Claude/Anthropic value. The owner contract is
# that a Claude run with NO *GLM* env present is byte-identical to today. So every unset here is
# GLM-CONDITIONAL: we strip a var ONLY when we can positively tie it to GLM (a z.ai base URL, or
# a byte-match against glm.env's own value). A user who legitimately exports ANTHROPIC_BASE_URL=
# https://api.anthropic.com (the live harness does exactly this), a corporate Anthropic-compatible
# proxy, a real Claude API key, or their own CLAUDE_CONFIG_DIR is left UNTOUCHED. An unconditional
# unset would have been "clean" but would also clobber those legitimate values and break the
# byte-identical guarantee — so we deliberately do NOT do that.
#
# UNSET POLICY (per var):
#   • ANTHROPIC_BASE_URL  — unset ONLY when it names the GLM (z.ai) host. A non-z.ai base URL
#     (api.anthropic.com, a user's proxy) is a legitimate Claude value → left as-is.
#   • ANTHROPIC_AUTH_TOKEN — unset when the base URL was z.ai (the whole GLM set leaked together)
#     OR when it byte-matches glm.env's token (a token leaked with the URL already cleared).
#     A Claude subscription run does not set this, so a GLM-matched token is always leakage.
#   • API_TIMEOUT_MS — unset when the base URL was z.ai OR it byte-matches glm.env's value
#     (3000000). Otherwise (e.g. the harness's own 900000) it is a legitimate user knob → kept.
#   • CLAUDE_CONFIG_DIR — unset ONLY when it points at GLM's fresh config dir (/tmp/glm-claude-
#     config from glm.env). Any other value is a user's own config dir → kept.
#   • ANTHROPIC_API_KEY — unset ONLY when the base URL was z.ai OR the key byte-matches glm.env's
#     key. A real Claude API key is left intact — we never clear a key we can't tie to GLM.
_worldos_scrub_glm_env() {
  local base_url="${ANTHROPIC_BASE_URL:-}" url_is_glm=0
  case "$base_url" in
    *"$_WORLDOS_GLM_BASE_URL_HOST"*) url_is_glm=1 ;;
  esac

  # Read glm.env's own values (WITHOUT printing them) so the remaining vars can be matched by
  # byte-equality even when the z.ai base URL was already stripped. Secrets stay in locals.
  local _glm_key="" _glm_token="" _glm_timeout=""
  local glm_env="${WORLDOS_GLM_ENV:-$HOME/.openclaw/secrets/glm.env}"
  if [ -f "$glm_env" ]; then
    _glm_key="$(_worldos_glm_env_val ANTHROPIC_API_KEY "$glm_env")"
    _glm_token="$(_worldos_glm_env_val ANTHROPIC_AUTH_TOKEN "$glm_env")"
    _glm_timeout="$(_worldos_glm_env_val API_TIMEOUT_MS "$glm_env")"
  fi

  # ANTHROPIC_BASE_URL — only the GLM host.
  [ "$url_is_glm" = "1" ] && unset ANTHROPIC_BASE_URL
  # ANTHROPIC_AUTH_TOKEN — GLM URL present, or a glm.env byte-match.
  if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    if [ "$url_is_glm" = "1" ] || { [ -n "$_glm_token" ] && [ "$ANTHROPIC_AUTH_TOKEN" = "$_glm_token" ]; }; then
      unset ANTHROPIC_AUTH_TOKEN
    fi
  fi
  # API_TIMEOUT_MS — GLM URL present, or a glm.env byte-match (never a user's own value).
  if [ -n "${API_TIMEOUT_MS:-}" ]; then
    if [ "$url_is_glm" = "1" ] || { [ -n "$_glm_timeout" ] && [ "$API_TIMEOUT_MS" = "$_glm_timeout" ]; }; then
      unset API_TIMEOUT_MS
    fi
  fi
  # CLAUDE_CONFIG_DIR — only GLM's fresh config dir.
  case "${CLAUDE_CONFIG_DIR:-}" in
    */glm-claude-config|*/glm-claude-config/) unset CLAUDE_CONFIG_DIR ;;
  esac
  # ANTHROPIC_API_KEY — GLM URL present, or a glm.env byte-match. Never an unrelated Claude key.
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    if [ "$url_is_glm" = "1" ] || { [ -n "$_glm_key" ] && [ "$ANTHROPIC_API_KEY" = "$_glm_key" ]; }; then
      unset ANTHROPIC_API_KEY
    fi
  fi
  unset _glm_key _glm_token _glm_timeout
  return 0
}

# Read one VAR=value from glm.env, stripping optional `export ` and surrounding quotes. Echoes
# the raw value (callers keep it in a local and NEVER print it). $1=var name $2=glm.env path.
_worldos_glm_env_val() {
  local _v
  _v="$(sed -n "s/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}$1=//p" "$2" | head -n1)"
  _v="${_v%\"}"; _v="${_v#\"}"
  _v="${_v%\'}"; _v="${_v#\'}"
  printf '%s' "$_v"
}

# Apply the GLM-only profile. No-op for Claude (see the CONTRACT above) EXCEPT that the
# Claude path still scrubs any stray GLM env so a switch-back-to-Opus run is always clean.
worldos_apply_glm_profile() {
  # Detect GLM from EITHER role.
  local dm_glm=0 actor_glm=0
  _worldos_is_glm_model "${WORLDOS_DM_MODEL:-}" && dm_glm=1
  _worldos_is_glm_model "${WORLDOS_ACTOR_MODEL:-}" && actor_glm=1

  # ── Claude path (NEITHER role GLM) ────────────────────────────────────────────────────
  # Scrub any stray GLM-injected env FIRST (the load-bearing "switch back to Opus is always
  # clean" guarantee — see _worldos_scrub_glm_env), THEN return. The scrub is a NO-OP when
  # nothing is set, so a normal Claude run with a clean env is byte-for-byte unchanged. The
  # unset MUST run here — do not let an early return skip it.
  if [ "$dm_glm" = "0" ] && [ "$actor_glm" = "0" ]; then
    _worldos_scrub_glm_env
    return 0
  fi

  # ── MIXED-MODEL GUARD ─────────────────────────────────────────────────────────────────
  # EXACTLY ONE role is GLM (a half-GLM/half-Claude config) — almost always a mistake: a
  # GLM-DM + Claude-actor (or vice versa) run would route the two roles to DIFFERENT providers
  # over the SAME process env (ANTHROPIC_BASE_URL is a process-global, not per-role), so the
  # "Claude" half would silently inherit z.ai too. Refuse the accidental mix: warn loudly and
  # NORMALIZE BOTH roles to the GLM profile so the whole run is coherently GLM (never a leaky
  # half-and-half). An operator who truly wants a split must set BOTH roles explicitly.
  if [ "$dm_glm" != "$actor_glm" ]; then
    echo "[glm_profile] WARNING: MIXED model config — WORLDOS_DM_MODEL='${WORLDOS_DM_MODEL:-}' / " \
         "WORLDOS_ACTOR_MODEL='${WORLDOS_ACTOR_MODEL:-}'. Exactly one role is GLM; the other " \
         "would silently inherit the GLM (z.ai) endpoint over the shared process env. " \
         "NORMALIZING BOTH roles to the GLM profile so the run is coherently GLM." >&2
    # Pin whichever role is NOT already GLM onto the GLM one so downstream model selection is
    # coherent. We keep the actual glm-* model id (the GLM role's value) for both.
    if [ "$dm_glm" = "1" ]; then
      export WORLDOS_ACTOR_MODEL="$WORLDOS_DM_MODEL"
    else
      export WORLDOS_DM_MODEL="$WORLDOS_ACTOR_MODEL"
    fi
  fi

  # ── GLM detected ──────────────────────────────────────────────────────────
  # Source the GLM endpoint + credentials (ANTHROPIC_BASE_URL, the API key/token,
  # CLAUDE_CONFIG_DIR) from OUTSIDE the repo. NEVER committed or printed here — we
  # only `.` the file so its exports land in this process. Idempotent: sourcing a
  # second time just re-exports the same values.
  local glm_env="${WORLDOS_GLM_ENV:-$HOME/.openclaw/secrets/glm.env}"
  if [ -f "$glm_env" ]; then
    # shellcheck disable=SC1090  # path is resolved at runtime, not a static include
    . "$glm_env"
  else
    echo "[glm_profile] WARNING: GLM model selected but $glm_env not found — " \
         "assuming ANTHROPIC_BASE_URL / API key / CLAUDE_CONFIG_DIR were set " \
         "another way; continuing." >&2
  fi

  # Raise the timeouts. ${VAR:-default} means an explicit caller/env override still
  # wins; we only fill in the GLM-appropriate floor when the knob is unset. These
  # mirror the names lib_beat_driver.sh's worldos_dm_timeout resolves (via
  # worldos_env COLDOPEN_TIMEOUT / BEAT_TIMEOUT) — by exporting WORLDOS_* here the
  # existing tiers pick up the raised values without any change to that file.
  export WORLDOS_COLDOPEN_TIMEOUT="${WORLDOS_COLDOPEN_TIMEOUT:-900}"
  export WORLDOS_BEAT_TIMEOUT="${WORLDOS_BEAT_TIMEOUT:-600}"

  # Raise the retry ceilings. GLM's slower turns make a transient empty turn (and
  # an empty beat-0 intro) more likely, so give the DM-turn retry loop and the new
  # player-intro retry more attempts. Both honor an explicit override (:-).
  export WORLDOS_DM_MAX_ATTEMPTS="${WORLDOS_DM_MAX_ATTEMPTS:-5}"
  export WORLDOS_PLAYER_MAX_ATTEMPTS="${WORLDOS_PLAYER_MAX_ATTEMPTS:-5}"

  # Effort is intentionally LEFT to the existing model-aware tiers in
  # lib_beat_driver.sh (worldos_dm_effort_arg). There is no GLM-specific effort need
  # today, and the cold-open/routine split already does the right thing; keeping the
  # profile minimal avoids a second source of truth for effort.

  return 0
}
