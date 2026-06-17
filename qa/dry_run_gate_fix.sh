#!/usr/bin/env bash
# DRY-RUN GATE FIX — validate a candidate fix in the CURRENT (dirty) working tree BEFORE a PR.
#
# This is a TOOL the implementing agent runs by hand after editing engine/content code. It runs K
# gate runs (default the combat-sprint — the fast bug-finder), takes the MEDIAN-OF-N of each lens
# (median is robust to the LLM scorer's per-run noise), reports the median mech + story + behavioral
# GREEN/RED, and — if qa/detect_regression.py + a canonical baseline exist — prints its better/worse
# verdict. It then PASS/FAILs against a bar so the agent gets a single go/no-go signal before opening
# a PR.
#
# It is a PURE READER of QA artifacts: it never writes engine state, never scores into the committed
# qa/scores.db / qa/scores_ledger.md / qa/RRI.json, and never touches Eva / the gateway / global mcp
# config. The live runs need a model key, so the inner runner is a SEAM (see env below) — the median
# / verdict / regression logic is fully testable offline with a stub.
#
# Usage:
#   qa/dry_run_gate_fix.sh [--runs N] [--gate combat-sprint|duo] [--mech-min X] [--story-min Y]
#                          [--regress] [--db <scores.db>] [--baseline-key k=v,...] [-- <inner args>]
#
# Env (test/offline seams — additive, default empty == real live runs):
#   CLAWDND_DRYRUN_RUNNER=<cmd>  Run <cmd> ONCE per gate run instead of the real combat-sprint. It must
#                                print ONE score JSON line to stdout: {"mech":N,"story":N,"behavioral":"GREEN|RED"}.
#                                ("story" is optional for mech-only gates like combat-sprint.)
#   CLAWDND_DRYRUN_STUB=1        Use a built-in fixed-score stub (offline, GREEN) — for smoke/demo.
#
# Exit: 0 = GREEN (median clears the bar AND behavioral majority GREEN); 2 = BELOW bar / behavioral RED;
#       3 = nothing ran. A regression verdict is ADVISORY unless it is a hard REGRESSED on a passing gate.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 3

# ── Defaults ─────────────────────────────────────────────────────────────────
RUNS=3
GATE_KIND="combat-sprint"
# Combat-sprint is mech-focused (Angry-DM 5e fidelity). Match loop.sh's published north-star bars.
MECH_MIN="${WORLDOS_MECH_MIN:-${CLAWDND_MECH_MIN:-4.5}}"
STORY_MIN="${WORLDOS_STORY_MIN:-${CLAWDND_STORY_MIN:-4.3}}"
DO_REGRESS=0
DB_PATH="qa/scores.db"        # default ONLY used when --regress is given; tests always pass a temp --db
BASELINE_KEY=""               # comma k=v list folded into the candidate JSON for detect_regression
INNER_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs)         RUNS="$2"; shift 2 ;;
    --gate)         GATE_KIND="$2"; shift 2 ;;
    --mech-min)     MECH_MIN="$2"; shift 2 ;;
    --story-min)    STORY_MIN="$2"; shift 2 ;;
    --regress)      DO_REGRESS=1; shift ;;
    --db)           DB_PATH="$2"; shift 2 ;;
    --baseline-key) BASELINE_KEY="$2"; shift 2 ;;
    --)             shift; INNER_ARGS=("$@"); break ;;
    -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
    *)              echo "[dryrun] unknown arg: $1" >&2; exit 3 ;;
  esac
done

case "$RUNS" in (''|*[!0-9]*) echo "[dryrun] --runs must be a positive integer" >&2; exit 3 ;; esac
[ "$RUNS" -ge 1 ] || { echo "[dryrun] --runs must be >= 1" >&2; exit 3; }

echo "[dryrun] gate=$GATE_KIND runs=$RUNS  bars: mech>=$MECH_MIN story>=$STORY_MIN"

# ── Inner runner: the SEAM. One call == one scored gate run; prints ONE score JSON line. ──────────
# Order of preference: explicit CLAWDND_DRYRUN_RUNNER, then CLAWDND_DRYRUN_STUB, else the real gate.
run_one() { # run_one <run-id> -> prints one score JSON line on stdout
  local rid="$1"
  if [ -n "${CLAWDND_DRYRUN_RUNNER:-}" ]; then
    CLAWDND_DRYRUN_RUN_ID="$rid" "$CLAWDND_DRYRUN_RUNNER" ${INNER_ARGS[@]+"${INNER_ARGS[@]}"}
    return $?
  fi
  if [ "${CLAWDND_DRYRUN_STUB:-0}" = "1" ]; then
    # Built-in offline stub: a fixed GREEN, above-bar score. No model, no state, no I/O.
    printf '{"mech":4.7,"story":4.6,"behavioral":"GREEN"}\n'
    return 0
  fi
  # ── REAL run path (needs a model key) ───────────────────────────────────────────────────────────
  # Reuse the existing gate runners verbatim; do NOT duplicate their median/seed/score logic.
  case "$GATE_KIND" in
    combat-sprint)
      qa/run_combat_sprint.sh "$rid" >"qa/transcripts/$rid.runlog" 2>&1 || true
      _score_from_artifacts "$rid" angrydm ;;
    duo)
      qa/run_duo.sh "$rid" >"qa/transcripts/$rid.runlog" 2>&1 || true
      _score_from_artifacts "$rid" duo ;;
    *)
      echo "[dryrun] unknown --gate '$GATE_KIND'" >&2; return 3 ;;
  esac
}

# Turn the runner's per-run artifacts into one score JSON line. Combat-sprint writes
# qa/transcripts/$RUN.angrydm.json (.overall = mech/5e) + echoes behavioral=GREEN|RED in the runlog;
# duo additionally writes $RUN.tolkien.json (.overall = story).
_score_from_artifacts() { # <run-id> <combat|duo|angrydm>
  local rid="$1" kind="$2" T="qa/transcripts"
  local mech story beh
  if [ "$kind" = "duo" ]; then
    mech="$(jq -r '.overall // empty' "$T/$rid.score.json" 2>/dev/null)"
    story="$(jq -r '.overall // empty' "$T/$rid.tolkien.json" 2>/dev/null)"
  else
    mech="$(jq -r '.overall // empty' "$T/$rid.angrydm.json" 2>/dev/null)"
    story=""  # combat-sprint is mech-only
  fi
  beh="$(grep -o 'behavioral=[A-Z]*' "$T/$rid.runlog" 2>/dev/null | tail -1 | cut -d= -f2)"
  beh="${beh:-RED}"
  python3 - "$mech" "$story" "$beh" <<'PY'
import json, sys
mech, story, beh = sys.argv[1], sys.argv[2], sys.argv[3]
out = {"behavioral": beh}
if mech not in ("", "null"):
    out["mech"] = float(mech)
if story not in ("", "null"):
    out["story"] = float(story)
print(json.dumps(out))
PY
}

# ── Drive K runs, collect score JSON lines ────────────────────────────────────────────────────────
STAMP="$(date +%y%m%d-%H%M%S)"
SCORES_FILE="$(mktemp "${TMPDIR:-/tmp}/dryrun_scores.XXXXXX")"
trap 'rm -f "$SCORES_FILE"' EXIT

for i in $(seq 1 "$RUNS"); do
  RID="dryrun-$STAMP-$i"
  echo "[dryrun] run $i/$RUNS ($RID)…" >&2
  line="$(run_one "$RID" 2>>"${TMPDIR:-/tmp}/dryrun_inner.err" | grep -E '^\s*\{' | tail -1)"
  if [ -z "$line" ]; then
    echo "[dryrun] run $i produced no score JSON — counting as RED/0" >&2
    line='{"mech":0,"story":0,"behavioral":"RED"}'
  fi
  printf '%s\n' "$line" >> "$SCORES_FILE"
done

# ── Median-of-N + behavioral majority (all numeric work in one python pass) ───────────────────────
# Median rule: sort ascending; ODD N -> middle element; EVEN N -> mean of the two middle elements.
# behavioral verdict: GREEN iff strictly more GREEN than RED (a tie defaults to RED — the honest floor).
SUMMARY="$(
python3 - "$SCORES_FILE" "$MECH_MIN" "$STORY_MIN" <<'PY'
import json, sys
path, mech_min, story_min = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])

rows = []
for ln in open(path):
    ln = ln.strip()
    if not ln:
        continue
    try:
        rows.append(json.loads(ln))
    except Exception:
        pass

def median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return round(vals[mid], 4)
    return round((vals[mid - 1] + vals[mid]) / 2.0, 4)

mechs  = [r.get("mech")  for r in rows if r.get("mech")  is not None]
stories= [r.get("story") for r in rows if r.get("story") is not None]
greens = sum(1 for r in rows if str(r.get("behavioral", "")).upper() == "GREEN")
reds   = len(rows) - greens

med_mech  = median(mechs)
med_story = median(stories)
beh = "GREEN" if greens > reds else "RED"

# Verdict: behavioral majority must be GREEN AND every present lens median must clear its bar.
below = []
if beh != "GREEN":
    below.append(f"behavioral={beh} ({greens} GREEN / {reds} RED)")
if med_mech is not None and med_mech < mech_min:
    below.append(f"mech={med_mech}<{mech_min}")
if med_story is not None and med_story < story_min:
    below.append(f"story={med_story}<{story_min}")

verdict = "GREEN" if not below else "BELOW"
out = {
    "n": len(rows),
    "median_mech": med_mech,
    "median_story": med_story,
    "behavioral": beh,
    "greens": greens,
    "reds": reds,
    "verdict": verdict,
    "below": below,
}
print(json.dumps(out))
PY
)"

N="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print(json.load(sys.stdin)["n"])')"
MED_MECH="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;v=json.load(sys.stdin)["median_mech"];print("n/a" if v is None else v)')"
MED_STORY="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;v=json.load(sys.stdin)["median_story"];print("n/a" if v is None else v)')"
BEH="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print(json.load(sys.stdin)["behavioral"])')"
VERDICT="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print(json.load(sys.stdin)["verdict"])')"
GREENS="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print(json.load(sys.stdin)["greens"])')"
REDS="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print(json.load(sys.stdin)["reds"])')"
BELOW="$(printf '%s' "$SUMMARY" | python3 -c 'import json,sys;print("; ".join(json.load(sys.stdin)["below"]))')"

echo "[dryrun] ===== median-of-$N scorecard ($GATE_KIND) ====="
echo "[dryrun]   median_mech  = $MED_MECH  (bar >= $MECH_MIN)"
echo "[dryrun]   median_story = $MED_STORY  (bar >= $STORY_MIN)"
echo "[dryrun]   behavioral   = $BEH  ($GREENS GREEN / $REDS RED of $N)"

# ── Optional regression verdict (read-only; temp DB in tests) ─────────────────────────────────────
REGRESS_HARD_FAIL=0
if [ "$DO_REGRESS" = "1" ]; then
  if [ ! -f "qa/detect_regression.py" ]; then
    echo "[dryrun] --regress requested but qa/detect_regression.py is absent — skipping regression check."
  else
    # Build a candidate JSON for detect_regression from the median lens overalls + behavioral, folding
    # in the comparability key (so it can find the canonical baseline). This NEVER writes the DB.
    CAND_JSON="$(
      python3 - "$MED_MECH" "$MED_STORY" "$BEH" "$BASELINE_KEY" <<'PY'
import json, sys
mech, story, beh, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cand = {"run_id": "dryrun-candidate", "behavioral": beh}
if mech != "n/a":
    cand["mech_overall"] = float(mech); cand["angrydm_overall"] = float(mech)
if story != "n/a":
    cand["story_overall"] = float(story)
for kv in (key or "").split(","):
    kv = kv.strip()
    if "=" in kv:
        k, v = kv.split("=", 1)
        cand[k.strip()] = v.strip()
print(json.dumps(cand))
PY
    )"
    echo "[dryrun] regression check vs canonical baseline (db=$DB_PATH)…"
    REG_OUT="$(python3 qa/detect_regression.py --candidate-json "$CAND_JSON" --db "$DB_PATH" 2>&1)"
    REG_RC=$?
    printf '%s\n' "$REG_OUT" | sed 's/^/[dryrun]   /'
    # Exit semantics of detect_regression: 0=IMPROVED/WITHIN_NOISE, 2=REGRESSED, 3=NO_BASELINE/NO_DATA.
    # NO_BASELINE/NO_DATA is ADVISORY — never fails the gate. A hard REGRESSED (2) does, but only as a
    # secondary signal layered on the scores gate.
    if [ "$REG_RC" = "2" ]; then
      echo "[dryrun]   regression verdict: REGRESSED (hard) — treat as a gate failure."
      REGRESS_HARD_FAIL=1
    fi
  fi
fi

# ── Final verdict + exit ──────────────────────────────────────────────────────────────────────────
if [ "$VERDICT" = "GREEN" ] && [ "$REGRESS_HARD_FAIL" = "0" ]; then
  echo "[dryrun] VERDICT: GREEN — median clears the bar; behavioral majority GREEN. Safe to open the PR."
  exit 0
fi
if [ "$REGRESS_HARD_FAIL" = "1" ] && [ "$VERDICT" = "GREEN" ]; then
  echo "[dryrun] VERDICT: BELOW (REGRESSED) — scores clear the bar but the run REGRESSED vs baseline. Do NOT open the PR yet."
else
  echo "[dryrun] VERDICT: BELOW bar — $BELOW. Do NOT open the PR yet; fix and re-run."
fi
exit 2
