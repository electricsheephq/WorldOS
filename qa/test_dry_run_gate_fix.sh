#!/usr/bin/env bash
# Offline tests for qa/dry_run_gate_fix.sh — the candidate-fix pre-PR gate.
#
# The live gate runs K combat-sprint runs (each one claude -p DM call) and needs a model key,
# so the inner runner is a SEAM: CLAWDND_DRYRUN_RUNNER=<cmd> (called once per run, prints one
# score JSON line) or CLAWDND_DRYRUN_STUB=1 (built-in fixed-score stub). These tests drive the
# script entirely through that seam — ZERO model calls, ZERO touching of the real qa/scores.db
# or any committed artifact. We assert:
#   (1) median of N is computed correctly (mech + story), ODD N = middle element;
#   (2) EVEN N = mean of the two middle elements;
#   (3) GREEN verdict (median >= bar) -> exit 0;
#   (4) BELOW-bar verdict (median < bar) -> nonzero exit;
#   (5) a behavioral RED in the majority -> RED verdict + nonzero exit even if scores clear the bar;
#   (6) the built-in CLAWDND_DRYRUN_STUB=1 path runs offline and is GREEN by default;
#   (7) detect_regression is invoked when a baseline DB + --regress is supplied (temp DB only).
# Run: bash qa/test_dry_run_gate_fix.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$ROOT/qa/dry_run_gate_fix.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/dryrun_gate_test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

fail=0
pass=0
chk() { # chk "desc" "predicate"
  if eval "$2"; then printf 'PASS: %s\n' "$1"; pass=$((pass + 1));
  else printf 'FAIL: %s\n' "$1"; fail=1; fi
}

# A reusable stub runner generator: writes a runner script that pops one score JSON per call
# from a queue file (one JSON per line), so K calls yield K distinct scores deterministically.
make_queue_runner() { # make_queue_runner <runner_path> <queue_path> <line1> <line2> ...
  local runner="$1" queue="$2"; shift 2
  : > "$queue"
  local ln
  for ln in "$@"; do printf '%s\n' "$ln" >> "$queue"; done
  cat > "$runner" <<RUNNER
#!/usr/bin/env bash
# Stub inner runner: emit the next queued score JSON line, then drop it from the queue.
set -euo pipefail
Q="$queue"
line="\$(head -n 1 "\$Q")"
tail -n +2 "\$Q" > "\$Q.next" && mv "\$Q.next" "\$Q"
printf '%s\n' "\$line"
RUNNER
  chmod +x "$runner"
}

run_gate() { # run_gate <out_var> <rc_var> -- <gate args...>
  local outv="$1" rcv="$2"; shift 3 # drop the literal --
  local _out _rc
  _out="$("$GATE" "$@" 2>&1)"; _rc=$?
  printf -v "$outv" '%s' "$_out"
  printf -v "$rcv" '%s' "$_rc"
}

hr() { printf '\n========== %s ==========\n' "$1"; }

# ── (1) ODD N median = middle element; GREEN (>= bar) -> exit 0 ───────────────
hr "SCENARIO 1 — ODD N=3, mech {4.0,4.8,4.4} -> median 4.4 (GREEN @ bar 4.5? no — use 4.0) -> exit 0"
R1="$TMP/runner1.sh"; Q1="$TMP/q1.txt"
make_queue_runner "$R1" "$Q1" \
  '{"mech":4.0,"story":4.6,"behavioral":"GREEN"}' \
  '{"mech":4.8,"story":4.2,"behavioral":"GREEN"}' \
  '{"mech":4.4,"story":4.4,"behavioral":"GREEN"}'
CLAWDND_DRYRUN_RUNNER="$R1" run_gate OUT1 RC1 -- --runs 3 --mech-min 4.0 --story-min 4.0
printf '%s\n' "$OUT1"
chk "S1 median mech = 4.4 (middle of sorted 4.0,4.4,4.8)" 'printf "%s" "$OUT1" | grep -Eq "median[_ ]?mech[^0-9]*4\.4"'
chk "S1 median story = 4.4 (middle of sorted 4.2,4.4,4.6)" 'printf "%s" "$OUT1" | grep -Eq "median[_ ]?story[^0-9]*4\.4"'
chk "S1 verdict GREEN" 'printf "%s" "$OUT1" | grep -q "GREEN"'
chk "S1 exit code 0" '[ "$RC1" = "0" ]'

# ── (2) EVEN N median = mean of two middle elements ──────────────────────────
hr "SCENARIO 2 — EVEN N=4, mech {4.0,5.0,4.0,5.0} sorted {4,4,5,5} -> median (4+5)/2 = 4.5"
R2="$TMP/runner2.sh"; Q2="$TMP/q2.txt"
make_queue_runner "$R2" "$Q2" \
  '{"mech":4.0,"story":4.0,"behavioral":"GREEN"}' \
  '{"mech":5.0,"story":5.0,"behavioral":"GREEN"}' \
  '{"mech":4.0,"story":4.0,"behavioral":"GREEN"}' \
  '{"mech":5.0,"story":5.0,"behavioral":"GREEN"}'
CLAWDND_DRYRUN_RUNNER="$R2" run_gate OUT2 RC2 -- --runs 4 --mech-min 4.0 --story-min 4.0
printf '%s\n' "$OUT2"
chk "S2 EVEN-N median mech = 4.5 (mean of two middles)" 'printf "%s" "$OUT2" | grep -Eq "median[_ ]?mech[^0-9]*4\.5"'
chk "S2 EVEN-N median story = 4.5 (mean of two middles)" 'printf "%s" "$OUT2" | grep -Eq "median[_ ]?story[^0-9]*4\.5"'
chk "S2 exit code 0 (GREEN)" '[ "$RC2" = "0" ]'

# ── (3) BELOW-bar mech median -> nonzero exit + below-bar verdict ────────────
hr "SCENARIO 3 — N=3, mech median 3.5 < bar 4.5 -> BELOW bar -> nonzero exit"
R3="$TMP/runner3.sh"; Q3="$TMP/q3.txt"
make_queue_runner "$R3" "$Q3" \
  '{"mech":3.0,"story":4.6,"behavioral":"GREEN"}' \
  '{"mech":3.5,"story":4.6,"behavioral":"GREEN"}' \
  '{"mech":4.9,"story":4.6,"behavioral":"GREEN"}'
CLAWDND_DRYRUN_RUNNER="$R3" run_gate OUT3 RC3 -- --runs 3 --mech-min 4.5 --story-min 4.0
printf '%s\n' "$OUT3"
chk "S3 median mech = 3.5" 'printf "%s" "$OUT3" | grep -Eq "median[_ ]?mech[^0-9]*3\.5"'
chk "S3 verdict BELOW bar (not GREEN)" 'printf "%s" "$OUT3" | grep -Eq "BELOW|below|FAIL|RED"'
chk "S3 nonzero exit" '[ "$RC3" != "0" ]'

# ── (4) behavioral RED majority -> RED verdict + nonzero even if scores pass ─
hr "SCENARIO 4 — N=3, all scores high but 2/3 behavioral RED -> RED verdict, nonzero exit"
R4="$TMP/runner4.sh"; Q4="$TMP/q4.txt"
make_queue_runner "$R4" "$Q4" \
  '{"mech":4.9,"story":4.9,"behavioral":"RED"}' \
  '{"mech":4.9,"story":4.9,"behavioral":"RED"}' \
  '{"mech":4.9,"story":4.9,"behavioral":"GREEN"}'
CLAWDND_DRYRUN_RUNNER="$R4" run_gate OUT4 RC4 -- --runs 3 --mech-min 4.0 --story-min 4.0
printf '%s\n' "$OUT4"
chk "S4 verdict RED despite high scores" 'printf "%s" "$OUT4" | grep -Eq "RED"'
chk "S4 nonzero exit (behavioral floor)" '[ "$RC4" != "0" ]'

# ── (5) built-in CLAWDND_DRYRUN_STUB=1 runs offline + is GREEN by default ────
hr "SCENARIO 5 — built-in stub (CLAWDND_DRYRUN_STUB=1) offline -> GREEN, exit 0"
CLAWDND_DRYRUN_STUB=1 run_gate OUT5 RC5 -- --runs 3
printf '%s\n' "$OUT5"
chk "S5 built-in stub GREEN" 'printf "%s" "$OUT5" | grep -q "GREEN"'
chk "S5 built-in stub exit 0" '[ "$RC5" = "0" ]'
chk "S5 reports N=3 runs" 'printf "%s" "$OUT5" | grep -Eq "3 run|runs[^0-9]*3|N=3"'

# ── (6) default --runs is sane (no args + stub) ──────────────────────────────
hr "SCENARIO 6 — default run count with built-in stub -> exit 0, prints a median"
CLAWDND_DRYRUN_STUB=1 run_gate OUT6 RC6 --
printf '%s\n' "$OUT6"
chk "S6 default-runs GREEN exit 0" '[ "$RC6" = "0" ]'
chk "S6 default prints a median" 'printf "%s" "$OUT6" | grep -Eqi "median"'

# ── (7) detect_regression invoked when --regress + temp baseline DB given ────
# We point at a TEMP db so we never touch the committed qa/scores.db. detect_regression with a
# nonexistent/empty key emits NO_BASELINE (exit 3 internally) — the gate must SURFACE that verdict
# but NOT let an advisory NO_BASELINE fail the gate (only a true REGRESSED should, and only if the
# scores gate already passed). Here scores pass + no baseline -> still GREEN/exit 0, regression line present.
hr "SCENARIO 7 — --regress with empty temp DB -> NO_BASELINE surfaced, gate still GREEN"
EMPTY_DB="$TMP/empty_scores.db"
python3 - "$EMPTY_DB" <<'PY'
import sqlite3, sys
# Create an empty sqlite file so detect_regression's scores_db can open it without a NO-FILE crash.
con = sqlite3.connect(sys.argv[1]); con.close()
PY
R7="$TMP/runner7.sh"; Q7="$TMP/q7.txt"
make_queue_runner "$R7" "$Q7" \
  '{"mech":4.6,"story":4.6,"behavioral":"GREEN"}' \
  '{"mech":4.6,"story":4.6,"behavioral":"GREEN"}' \
  '{"mech":4.6,"story":4.6,"behavioral":"GREEN"}'
CLAWDND_DRYRUN_RUNNER="$R7" run_gate OUT7 RC7 -- --runs 3 --mech-min 4.0 --story-min 4.0 --regress --db "$EMPTY_DB"
printf '%s\n' "$OUT7"
chk "S7 regression verdict surfaced (NO_BASELINE or a verdict word)" \
  'printf "%s" "$OUT7" | grep -Eqi "NO_BASELINE|regress|baseline"'
chk "S7 advisory NO_BASELINE does not fail a passing gate (exit 0)" '[ "$RC7" = "0" ]'

hr "RESULT"
printf '%d checks passed; overall %s\n' "$pass" "$([ "$fail" = 0 ] && echo PASS || echo FAIL)"
exit "$fail"
