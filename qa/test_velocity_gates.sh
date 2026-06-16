#!/usr/bin/env bash
# VELOCITY-GATES TEST (no model call, $0, deterministic) — proves the two inner-loop
# friction-cutters are ADDITIVE and OFF by default:
#
#   (A) qa/fast_gate.sh OPT-IN green-cache (CLAWDND_FASTGATE_CACHE=1):
#       - default OFF  => the inner gate runs EVERY time (today's behavior, untouched).
#       - cache ON     => a 1st run executes the inner gate + records a green verdict keyed on
#                         (git HEAD + persona + gate args); a 2nd run on the SAME key short-circuits
#                         to the cached verdict WITHOUT re-running the inner gate.
#       - a RED inner result is NEVER cached (you must be able to re-run and see it fail).
#
#   (B) qa/release_gate.sh RAM-aware PREFLIGHT:
#       - under a forced-LOW-RAM stub the preflight WARNS by default (warning-only — today's
#         sweep still proceeds) and REFUSES (non-zero, "GATE-ABORT") under
#         CLAWDND_RAM_PREFLIGHT_STRICT=1, pointing at GitHub CI / the support VM.
#       - under a forced-HIGH-RAM stub the preflight is silent-OK even in strict mode.
#
# We drive both scripts WITHOUT touching Eva / the gateway / any model: the inner fast-gate run is
# mocked via CLAWDND_FASTGATE_INNER_CMD, and available RAM is forced via CLAWDND_RAM_PREFLIGHT_AVAIL_MB.
# We invoke release_gate's preflight in --preflight-only mode so no sweep ever launches.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# ──────────────────────────────────────────────────────────────────────────────
# (A) fast_gate.sh green-cache
# ──────────────────────────────────────────────────────────────────────────────
# Mock inner gate: increments a counter file each time it runs, and obeys an exit code we control.
# This lets us assert the inner gate was (not) invoked on the 2nd run.
COUNTER="$TMP/inner_runs"; : > "$COUNTER"
CACHE_HOME="$TMP/cache"            # redirect the cache root away from the repo

mk_inner() {  # $1 = exit code the mock inner gate should return
  local rc="$1"
  cat > "$TMP/inner.sh" <<STUB
#!/usr/bin/env bash
printf 'x' >> "$COUNTER"
echo "  ✓ deterministic engine tier: 42 passed (MOCK)"
exit $rc
STUB
  chmod +x "$TMP/inner.sh"
}
runs() { cat "$COUNTER" 2>/dev/null; }   # emits the accumulated 'x' marks (one per inner-gate run)

run_fastgate() {  # runs fast_gate.sh with the mocked inner + redirected cache home
  CLAWDND_FASTGATE_CACHE=1 \
  CLAWDND_FASTGATE_INNER_CMD="$TMP/inner.sh" \
  CLAWDND_FASTGATE_CACHE_DIR="$CACHE_HOME" \
  CLAWDND_FASTGATE_PERSONA="velocity-test" \
    bash "$ROOT/qa/fast_gate.sh" >"$TMP/fg.out" 2>&1
}

# A0: default OFF — inner gate runs every time, no cache dir created.
: > "$COUNTER"; mk_inner 0
CLAWDND_FASTGATE_INNER_CMD="$TMP/inner.sh" CLAWDND_FASTGATE_CACHE_DIR="$TMP/cache_off" \
  bash "$ROOT/qa/fast_gate.sh" >/dev/null 2>&1
CLAWDND_FASTGATE_INNER_CMD="$TMP/inner.sh" CLAWDND_FASTGATE_CACHE_DIR="$TMP/cache_off" \
  bash "$ROOT/qa/fast_gate.sh" >/dev/null 2>&1
chk "cache OFF by default: inner gate runs BOTH times (today's behavior)" '[ "$(runs)" = "xx" ]'
chk "cache OFF by default: no cache dir is created"                       '[ ! -d "$TMP/cache_off" ]'

# A1: cache ON, GREEN — 1st run executes the inner gate.
: > "$COUNTER"; mk_inner 0
run_fastgate; rc1=$?
chk "cache ON, 1st run: inner gate executed once"  '[ "$(runs)" = "x" ]'
chk "cache ON, 1st run: PASS exit 0"               '[ "$rc1" = "0" ]'

# A2: cache ON, GREEN — 2nd run on the SAME key short-circuits, inner gate NOT re-run.
run_fastgate; rc2=$?
chk "cache ON, 2nd run: inner gate NOT re-run (still one execution)" '[ "$(runs)" = "x" ]'
chk "cache ON, 2nd run: still PASS exit 0"                          '[ "$rc2" = "0" ]'
chk "cache ON, 2nd run: announces a cache HIT"                      'grep -qi "cache" "$TMP/fg.out" && grep -qi "hit\|cached" "$TMP/fg.out"'

# A3: a RED inner result is NEVER cached — re-runs keep executing the inner gate.
: > "$COUNTER"; CACHE_HOME="$TMP/cache_red"; mk_inner 1
run_fastgate; rcr1=$?
run_fastgate; rcr2=$?
chk "cache ON, RED is not cached: inner gate runs BOTH times" '[ "$(runs)" = "xx" ]'
chk "cache ON, RED 1st run: non-zero exit"                    '[ "$rcr1" != "0" ]'
chk "cache ON, RED 2nd run: still non-zero exit"              '[ "$rcr2" != "0" ]'

# ──────────────────────────────────────────────────────────────────────────────
# (B) release_gate.sh RAM-aware preflight
# ──────────────────────────────────────────────────────────────────────────────
# We run release_gate in --preflight-only mode so the heavy sweep never launches, and force the
# "available RAM" reading via CLAWDND_RAM_PREFLIGHT_AVAIL_MB. We don't care whether the OTHER
# integrity checks (art / port / sha) pass — we only assert the RAM-preflight branch behaves.
run_preflight() {  # $1 = forced avail MB ; $2..= extra env assignments (e.g. STRICT=1)
  local avail="$1"; shift
  env "$@" CLAWDND_RAM_PREFLIGHT_AVAIL_MB="$avail" \
    bash "$ROOT/qa/release_gate.sh" --preflight-only >"$TMP/rg.out" 2>&1
}

# B0: LOW RAM, default (non-strict) — WARNS but does NOT abort on the RAM check.
run_preflight 800 >/dev/null 2>&1 || true   # may still fail on art/port; that's fine
chk "low-RAM non-strict: emits a RAM warning"                'grep -qi "RAM\|memory" "$TMP/rg.out" && grep -qi "low\|below\|pressure\|warn" "$TMP/rg.out"'
chk "low-RAM non-strict: points at CI / support VM"          'grep -qi "CI\|support VM\|support-vm\|GitHub" "$TMP/rg.out"'
chk "low-RAM non-strict: did NOT abort on the RAM check"     '! grep -qi "GATE-ABORT.*RAM\|GATE-ABORT.*memory" "$TMP/rg.out"'

# B1: LOW RAM, STRICT — REFUSES with a non-zero exit and a clear RAM abort message.
run_preflight 800 CLAWDND_RAM_PREFLIGHT_STRICT=1; rc_strict=$?
chk "low-RAM strict: refuses (non-zero exit)"                '[ "$rc_strict" != "0" ]'
chk "low-RAM strict: aborts ON the RAM check specifically"   'grep -qi "GATE-ABORT" "$TMP/rg.out" && grep -qi "RAM\|memory" "$TMP/rg.out"'

# B2: HIGH RAM, STRICT — the RAM gate is silent-OK (no RAM warning, no RAM abort).
run_preflight 64000 CLAWDND_RAM_PREFLIGHT_STRICT=1 >/dev/null 2>&1 || true  # may fail later on art/port
chk "high-RAM strict: no RAM abort"   '! grep -qi "GATE-ABORT.*RAM\|GATE-ABORT.*memory" "$TMP/rg.out"'
chk "high-RAM strict: no low-RAM warning" '! ( grep -qi "RAM\|memory" "$TMP/rg.out" && grep -qi "below\|low\b" "$TMP/rg.out" )'

echo ""
[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
