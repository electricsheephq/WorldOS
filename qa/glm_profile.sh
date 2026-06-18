#!/usr/bin/env bash
# qa/glm_profile.sh — a model-keyed settings profile for running the WorldOS QA
# duo against GLM (z.ai's GLM 5.2, served over an Anthropic-compatible endpoint)
# INSTEAD of Claude. Sourced by qa/run_duo.sh right after the model vars resolve.
#
# CONTRACT (the load-bearing guarantee):
#   • GLM-ONLY. If NEITHER $WORLDOS_DM_MODEL NOR $WORLDOS_ACTOR_MODEL starts with
#     "glm", worldos_apply_glm_profile is a TOTAL no-op (`return 0` before it
#     touches a single var or env). A Claude run is therefore byte-for-byte
#     unchanged — this file must never alter a Claude default.
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

# Apply the GLM-only profile. No-op for Claude (see the CONTRACT above).
worldos_apply_glm_profile() {
  # Detect GLM from EITHER role. If neither is GLM this returns immediately and
  # nothing below runs — the Claude path is byte-for-byte unchanged.
  if ! _worldos_is_glm_model "${WORLDOS_DM_MODEL:-}" \
    && ! _worldos_is_glm_model "${WORLDOS_ACTOR_MODEL:-}"; then
    return 0
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
