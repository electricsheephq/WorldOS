#!/usr/bin/env bash
# ARC SMOKE — the FAST companion/relationship-arc iteration test (<5 min, <$0.50).
#
# WHY: validating a newly-authored companion arc today means either a full ~$2 run_duo
# (3-act, all 3 LLM lenses) or a ~$10 5-persona sweep. Neither is a tight inner loop. A
# content author who just wrote an `arc.arc_gates`/`companion_dossier` for an adventure
# needs a 30-second answer to ONE question: "does the arc ENGAGE end-to-end — does the
# companion get met, and does approval actually MOVE off zero?" (the #1 inert-arc failure
# the owner cares about: relationship superstructure that never fires in real play).
#
# WHAT IT IS: a THIN wrapper around qa/run_duo.sh in golden-spine mode
# (WORLDOS_ADVENTURE_ID=<adv> → the DM cold-opens an AUTHORED adventure via
# start_adventure, which PRE-SEEDS the world + the authored companion + their arc), driven
# by an arc-FOCUSED player persona (qa/play_player_arc.txt — "meet the companion and earn
# their trust", not general wandering) at a LOW beat count. It does NOT fork run_duo's
# logic — it parametrizes it. After the run it asserts, DETERMINISTICALLY (no LLM lens), that
# the arc engaged, from the final engine snapshot run_duo already wrote to
# qa/transcripts/<run>.state.json.
#
# COST CONTROL: the smoke points run_duo's scorer at a NO-OP stub
# (WORLDOS_SCORE_SCRIPT=<stub>), so the 3 LLM scoring lenses (mechanical / Tolkien /
# Angry-DM, ~$1.50 each) are SKIPPED — the deterministic approval/engagement assertion below
# is the only signal the smoke needs. run_duo's behavioral gate + the state.json snapshot run
# AFTER the scoring block regardless, so both still happen. Set WORLDOS_SMOKE_NO_LENS=0 to
# keep the real lenses (a slower, costlier smoke).
#
# Usage:  qa/run_arc_smoke.sh [run-id] [adventure-id] [beats]
#   run-id        a label for this run's transcripts (default: arcsmoke-<HHMMSS>)
#   adventure-id  an authored adventure with a companion+arc (default: the-ledger-of-mercy —
#                 has Sergeant Ondine Marsh + a 3-gate arc, threshold-25 first gate).
#                 Others with a companion+arc: three-knives, hollow-mile, ashfall-reach,
#                 embergloom-pact, cellar-rats (Vesper; dossier only, no betrayal agenda).
#   beats         how many player<->DM beats (default: 4 — enough to recruit + 2-3
#                 arc-moving beats; NOT a full 3-act run).
#
# Example:  qa/run_arc_smoke.sh ledger1 the-ledger-of-mercy 4
#
# PASS/FAIL: exits NON-ZERO if the companion's approval never moved off zero (the core
# "the arc is inert" failure). Prints a tight stamp:
#   ARC SMOKE | recruit ✓ | approval Δ=+27 ✓ | arc-gate gate@25:unlocked | companion=Ondine Marsh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

RUN="${1:-arcsmoke-$(date +%H%M%S)}"
ADVENTURE_ID="${2:-the-ledger-of-mercy}"
BEATS="${3:-4}"
PERSONA="${WORLDOS_SMOKE_PERSONA:-qa/play_player_arc.txt}"
# run_duo's golden-spine mode swaps the cold-open to start_adventure("$ADVENTURE_ID"). The
# WORLD arg is unused by the authored cold-open, but run_duo still takes it positionally —
# pass the adventure id (harmless; the authored branch ignores it).
WORLD="$ADVENTURE_ID"
# A per-turn budget cap (NOT a spend): low for the smoke. run_duo floors this to $4.00 for an
# Opus DM cold-open (it needs the headroom), so the per-turn cap is really only a guard on the
# player/continuing turns; total smoke spend is bounded by the LOW beat count, not this.
BUDGET="${WORLDOS_SMOKE_BUDGET:-0.80}"

T="qa/transcripts"
STATE_JSON="$T/$RUN.state.json"   # run_duo writes the FINAL engine snapshot here (run_duo.sh:489)

# ── Validate the adventure exists + actually carries a companion (fail fast, no live run) ──
ADV_JSON="content/campaigns/$ADVENTURE_ID/adventure.json"
if [ ! -f "$ADV_JSON" ]; then
  echo "[arc-smoke] FATAL: no authored adventure '$ADVENTURE_ID' (missing $ADV_JSON)." >&2
  echo "[arc-smoke]        choices: $(ls content/campaigns 2>/dev/null | paste -sd' ' -)" >&2
  exit 2
fi
HAS_COMPANION="$(jq -r '((.companions // []) | length) > 0' "$ADV_JSON" 2>/dev/null)"
if [ "$HAS_COMPANION" != "true" ]; then
  echo "[arc-smoke] FATAL: adventure '$ADVENTURE_ID' declares no companion — nothing to smoke." >&2
  exit 2
fi
COMP_AUTHORED="$(jq -r '(.companions[0].name // .companions[0]) // "?"' "$ADV_JSON" 2>/dev/null)"
HAS_ARC="$(jq -r '[.companions[]? | select(.arc != null)] | length > 0' "$ADV_JSON" 2>/dev/null)"
echo "[arc-smoke] adventure=$ADVENTURE_ID companion(authored)=$COMP_AUTHORED arc=$HAS_ARC beats=$BEATS run=$RUN"
[ "$HAS_ARC" != "true" ] && echo "[arc-smoke] NOTE: '$ADVENTURE_ID' has a companion_dossier but no arc.arc_gates — approval can still move; the arc-gate line will read 'none'."

# ── No-op scorer stub (skips the 3 LLM lenses for the smoke; cost control). ──────────────
# Written to a temp path and handed to run_duo via WORLDOS_SCORE_SCRIPT. It writes the
# minimal valid scorecard run_duo's downstream `jq -r '.overall//"?"'` + worldos_cap_score_red
# expect (a parseable JSON object with `.overall` + `.scores`), so the no-lens path doesn't
# trip run_duo's "done." summary or the gate-RED capping. The deterministic gate + the
# state.json snapshot run AFTER scoring in run_duo, so BOTH still happen with the stub in place.
SCORE_STUB=""
if [ "${WORLDOS_SMOKE_NO_LENS:-1}" = "1" ]; then
  SCORE_STUB="$(mktemp "${TMPDIR:-/tmp}/arc-smoke-score.XXXXXX.sh")"
  cat > "$SCORE_STUB" <<'STUB'
#!/usr/bin/env bash
# arc-smoke NO-OP scorer: skip the LLM lens, write the minimal valid scorecard shape.
# Args mirror qa/score.sh: <md> <state> <rubric> <schema> <out.json> [budget]
OUT="${5:?score stub needs an out path}"
printf '{"overall":null,"scores":{},"note":"arc-smoke: LLM lens skipped (WORLDOS_SMOKE_NO_LENS=1)"}\n' > "$OUT"
STUB
  chmod +x "$SCORE_STUB"
  echo "[arc-smoke] no-lens mode: 3 LLM scoring lenses SKIPPED (set WORLDOS_SMOKE_NO_LENS=0 to keep them)"
fi
# Always clean up the stub.
cleanup() { [ -n "$SCORE_STUB" ] && rm -f "$SCORE_STUB" 2>/dev/null; }
trap cleanup EXIT

# ── Drive run_duo in golden-spine (authored-adventure) mode. ─────────────────────────────
# We DON'T fork run_duo — we parametrize it: WORLDOS_ADVENTURE_ID swaps its cold-open to the
# authored adventure, the arc persona focuses the player on the companion, and the low BEATS
# keeps it fast. WORLDOS_SCORE_SCRIPT (set above) skips the lenses. Everything else (the
# behavioral gate, the snapshot writer, lean beats, retries) is run_duo's shipped behavior.
echo "[arc-smoke] launching run_duo (golden-spine: start_adventure(\"$ADVENTURE_ID\"))…"
DUO_RC=0
# Export the parametrization as ENV (run_duo reads WORLDOS_ADVENTURE_ID directly, and
# WORLDOS_SCORE_SCRIPT via worldos_env SCORE_SCRIPT). Set via `export`, NOT an inline
# `NAME=val cmd` prefix built from a parameter expansion — `${VAR:+NAME=val}` expands to a
# WORD, which bash then tries to RUN as a command (not an assignment), so it must be a real
# export before the call. The no-op stub var is only exported in no-lens mode (SCORE_STUB set).
export WORLDOS_ADVENTURE_ID="$ADVENTURE_ID"
[ -n "$SCORE_STUB" ] && export WORLDOS_SCORE_SCRIPT="$SCORE_STUB"
bash "$ROOT/qa/run_duo.sh" "$RUN" "$WORLD" "$PERSONA" "$BEATS" "$BUDGET" || DUO_RC=$?
# run_duo's exit code IS the behavioral gate (0 GREEN / 1 RED). We surface it but the
# arc-smoke verdict below is the DETERMINISTIC approval assertion — the smoke's whole point.
echo "[arc-smoke] run_duo finished (behavioral-gate rc=$DUO_RC)"

# ── Deterministic ARC assertion from the final engine snapshot (no LLM lens needed). ─────
if [ ! -s "$STATE_JSON" ]; then
  echo "ARC SMOKE | FAIL — no engine snapshot at $STATE_JSON (the run never minted state; see $T/$RUN.dm.err)" >&2
  exit 1
fi

# Read the companion's signed approval delta, name, met-flag, arc-gate state, and the
# agenda-fired (betrayal) flag DIRECTLY from the snapshot. The companion is identified in
# `.characters` by `kind=="companion"` (VERIFIED against real snapshots, e.g.
# qa/transcripts/sprint-validate.state.json: Mira Quill attitude_value=52, gate@25 unlocked).
# attitude_value starts at 0 (authored seed); any non-zero == approval moved.
#
# We pick the MOST-MOVED companion (largest |attitude_value|) as THE arc under test, so a
# multi-companion adventure (e.g. three-knives) smokes on whichever arc the run engaged —
# moving ANY authored arc off zero proves the engagement path is live.
read -r COMP_NAME APPROVAL ARCGATE BETRAYAL < <(python3 - "$STATE_JSON" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
chars = state.get("characters", {}) or {}
clist = list(chars.values()) if isinstance(chars, dict) else (chars if isinstance(chars, list) else [])
comps = [c for c in clist if isinstance(c, dict) and c.get("kind") == "companion"]

def att(c):
    try:
        return int(c.get("attitude_value") or 0)
    except (TypeError, ValueError):
        return 0

# THE arc under test = the most-moved companion (falls back to the first companion if none moved).
chosen = max(comps, key=lambda c: abs(att(c)), default=None)
if chosen is None:
    print("none 0 none false")
    sys.exit(0)

name = (chosen.get("name") or "companion").replace(" ", "_") or "companion"
a = att(chosen)

# Arc-gate state: from this companion's arc.arc_gates[].unlocked (authored arc seeded live).
arc = chosen.get("arc") if isinstance(chosen.get("arc"), dict) else None
gates = (arc or {}).get("arc_gates") if isinstance(arc, dict) else None
if isinstance(gates, list) and gates:
    parts = []
    for g in gates:
        if not isinstance(g, dict):
            continue
        thr = g.get("threshold", "?")
        unlocked = bool(g.get("unlocked"))
        parts.append(f"gate@{thr}:{'unlocked' if unlocked else 'locked'}")
    gate_str = ",".join(parts) if parts else "none"
else:
    gate_str = "none"

# Betrayal: this companion's sealed agenda actually FIRED (arc.agenda.fired == True).
agenda = (arc or {}).get("agenda") if isinstance(arc, dict) else None
fired = bool(agenda.get("fired")) if isinstance(agenda, dict) else False

print(f"{name} {a} {gate_str} {'true' if fired else 'false'}")
PY
)
COMP_NAME="${COMP_NAME//_/ }"
APPROVAL="${APPROVAL:-0}"

# Cross-check with the SHARED coverage helper (reuse, don't re-derive): structural_coverage_from_state
# computes `recruited` (a kind=companion in the party) from the SAME snapshot. This is the SAME
# engine-truth path the story_readout coverage stamp + the #961 structural gate use, so the smoke's
# recruit verdict can't drift from the rest of the harness. (approval is asserted directly above from
# the signed attitude_value, which is the smoke's PASS/FAIL pivot.)
RECRUITED="$(python3 - "$STATE_JSON" <<'PY'
import json, sys
sys.path.insert(0, "qa")
import story_readout as sr
state = json.load(open(sys.argv[1]))
cov = sr.structural_coverage_from_state(state)
print("true" if cov.get("recruited") else "false")
PY
)"

# ── Verdict + stamp. ─────────────────────────────────────────────────────────────────────
mark() { [ "$1" = "true" ] && printf '✓' || printf '✗'; }
# Sign the approval delta for the stamp (+27 / -12 / 0).
if [ "$APPROVAL" -gt 0 ] 2>/dev/null; then APPROVAL_DISP="+$APPROVAL"; else APPROVAL_DISP="$APPROVAL"; fi
APPROVAL_MOVED=false; [ "$APPROVAL" != "0" ] && APPROVAL_MOVED=true

RECRUIT_MARK="$(mark "$RECRUITED")"
APPROVAL_MARK="$(mark "$APPROVAL_MOVED")"
BETRAYAL_NOTE=""
[ "$BETRAYAL" = "true" ] && BETRAYAL_NOTE=" | betrayal-agenda FIRED"

echo
echo "ARC SMOKE | recruit $RECRUIT_MARK | approval Δ=$APPROVAL_DISP $APPROVAL_MARK | arc-gate $ARCGATE | companion=$COMP_NAME$BETRAYAL_NOTE"

# The CORE failure the owner cares about: the arc is INERT — approval never moved off zero.
if [ "$APPROVAL_MOVED" != "true" ]; then
  echo "ARC SMOKE | FAIL — companion approval never moved off 0 (the arc is INERT). companion=$COMP_NAME recruit=$RECRUITED" >&2
  echo "ARC SMOKE | (behavioral-gate rc was $DUO_RC; see $T/$RUN.gate.txt — but the smoke verdict is approval movement)" >&2
  exit 1
fi
echo "ARC SMOKE | PASS — approval moved (Δ=$APPROVAL_DISP), arc engaged. behavioral-gate rc=$DUO_RC"
exit 0
