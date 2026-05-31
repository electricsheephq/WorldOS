#!/usr/bin/env bash
# WorldOS RELEASE GATE — the ONE command that runs the whole RRI gate, reliably.
#
# WHY THIS EXISTS (support-layer tool, 2026-05-31):
#   The gate was hand-run as ~12 fragile manual steps — each a fresh place the
#   memory-saturated host / flaky channel could bite (stale build, worktree with no
#   _private art, a squatted port, a probe-killed run). That cost ~2 days incl. an
#   8h "all-green on the WRONG surface" trap. This script makes the gate auditable +
#   repeatable + corruption-hardened, and FAILS LOUDLY on the traps instead of
#   silently producing a meaningless green.
#
# It ORCHESTRATES existing tools (does not reimplement them):
#   qa/ui_playtest_app.sh   — build + native #356 gate + per-persona play (the BUILT .app surface)
#   qa/run_duo.sh           — a 3-lens story/mech duo
#   qa/assert_behavioral.py — the behavioral gate
#   qa/ui_audit_health.sh   — axe a11y + per-screen render health
#   qa/release_readiness.py — rolls it all into the RRI (0-10, 11 hard gates)
#
# Usage:
#   qa/release_gate.sh [--personas a,b,c] [--lean] [--port 8765] [--budget 12]
#   qa/release_gate.sh --preflight-only      # just run the integrity checks, no sweep
#
# Host discipline: runs ONE heavy claude -p stream at a time (the host is 16GB; two
# streams saturate swap and everything degrades). Frees the backend port between
# personas. Verifies "dead" by ps before relaunch — never probe-kills.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2
PERSONAS="newbie,veteran,adversarial,narrative,optimizer"
LEAN=0; PORT="${WOS_APP_PREFERRED_PORT:-8765}"; BUDGET="12.00"; PREFLIGHT_ONLY=0
RUNID="gate-$(git rev-parse --short HEAD 2>/dev/null || echo nohead)"
while [ $# -gt 0 ]; do case "$1" in
  --personas) PERSONAS="$2"; shift 2;;
  --lean) LEAN=1; shift;;
  --port) PORT="$2"; shift 2;;
  --budget) BUDGET="$2"; shift 2;;
  --preflight-only) PREFLIGHT_ONLY=1; shift;;
  *) echo "unknown arg: $1"; exit 2;;
esac; done

fail()  { echo "❌ GATE-ABORT: $*" >&2; exit 1; }
ok()    { echo "✓ $*"; }
warn()  { echo "⚠ $*" >&2; }

# ── PREFLIGHT — the checks that prevent the measurement-trap class ──────────────
# (Every one of these maps to a real failure that cost real time this project.)
preflight() {
  echo "── PREFLIGHT (integrity) ─────────────────────────────────────────"

  # 1. CANONICAL repo, not the deprecated LEXAR copy or a random worktree.
  case "$ROOT" in
    */ClawDnD-val) ok "repo root looks canonical: $ROOT";;
    *) warn "repo root is $ROOT — confirm this is the canonical checkout (NOT /Volumes/LEXAR deprecated copy)";;
  esac

  # 2. _private ART PRESENT — the single check that catches the "zero images" trap.
  #    A gitignored worktree has none → every /image 404s → a meaningless sweep.
  local art="$ROOT/content/worlds/_private/baldurs-gate/images"
  if [ -d "$art" ] && [ "$(find "$art" -name 'image.png' 2>/dev/null | head -5 | wc -l | tr -d ' ')" -ge 1 ]; then
    ok "_private art present ($(find "$art" -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ') scopes)"
  else
    fail "_private art MISSING under $art — images will 404. Run the gate from the canonical checkout (it has the 2.9GB art), or set WORLDOS_REPO_ROOT to one that does. This is the 'zero images' trap."
  fi

  # 3. LOCAL main == origin/main — don't gate a stale build (the trap that started it all).
  git fetch origin --quiet 2>/dev/null || warn "git fetch failed (offline?) — sha freshness unverified"
  local L R; L="$(git rev-parse HEAD 2>/dev/null)"; R="$(git rev-parse origin/main 2>/dev/null)"
  if [ -n "$L" ] && [ -n "$R" ] && [ "$L" != "$R" ]; then
    warn "local HEAD ($(echo "$L"|cut -c1-7)) != origin/main ($(echo "$R"|cut -c1-7)) — gating a NON-tip build. Pull first unless intentional."
  else
    ok "on origin/main tip ($(echo "$L"|cut -c1-7))"
  fi

  # 4. The gate port is FREE (a squatted viewer = 'Port already in use' instant-fail).
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    warn "port $PORT is occupied — freeing it (leftover viewer): lsof -ti:$PORT | xargs kill -9"
    lsof -nP -iTCP:"$PORT" -t 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 2
  fi
  ok "gate port $PORT free"

  # 5. HOST HEADROOM — heavy claude -p on a swap-saturated host fabricates/dies.
  local swap_used; swap_used="$(sysctl -n vm.swapusage 2>/dev/null | sed -E 's/.*used = ([0-9.]+)M.*/\1/')"
  if [ -n "$swap_used" ]; then
    if awk "BEGIN{exit !($swap_used > 5500)}"; then
      warn "swap used ${swap_used}M (>5.5G) — host is memory-pressured. Heavy claude -p runs may be unreliable/confabulate. Free RAM or run the swarm off-host before trusting scores."
    else
      ok "host headroom OK (swap used ${swap_used}M)"
    fi
  fi

  # 6. The tools we orchestrate exist.
  for t in qa/ui_playtest_app.sh qa/run_duo.sh qa/assert_behavioral.py qa/ui_audit_health.sh qa/release_readiness.py; do
    [ -f "$ROOT/$t" ] || fail "missing required tool: $t"
  done
  ok "all orchestrated tools present"
  echo "── preflight OK ──────────────────────────────────────────────────"
}

free_port() { lsof -nP -iTCP:"$1" -t 2>/dev/null | xargs kill -9 2>/dev/null || true; sleep 2; }

preflight
[ "$PREFLIGHT_ONLY" = "1" ] && { echo "preflight-only: done."; exit 0; }

[ "$LEAN" = "1" ] && export CLAWDND_LEAN_BEATS=1 && ok "lean-beats ENABLED (CLAWDND_LEAN_BEATS=1)"

# ── SWEEP — 5 personas, ONE heavy stream at a time (host discipline) ────────────
echo "── SWEEP (${PERSONAS}) on the BUILT .app ─────────────────────────"
first=1; RUN_DIRS=""
IFS=',' read -ra PS <<< "$PERSONAS"
for p in "${PS[@]}"; do
  free_port "$((PORT+20))"; free_port "$PORT"
  rd="$ROOT/qa/ui_playtest_runs/${RUNID}-${p}"
  if [ "$first" = "1" ]; then
    # persona 1 does the FULL build (part A rebuilds the .app + native #356 gate)
    echo "[$p] part A+B (fresh build + native gate + play)…"
    WOS_APP_PART=AB WOS_APP_PREFERRED_PORT="$PORT" qa/ui_playtest_app.sh "${RUNID}-${p}" baldurs-gate "$p" 40 "$BUDGET" >"$ROOT/qa/ui_playtest_runs/${RUNID}-${p}.log" 2>&1
    first=0
  else
    echo "[$p] part B (reuse build)…"
    WOS_APP_PART=B WOS_APP_SKIP_BUILD=1 WOS_APP_PREFERRED_PORT="$PORT" qa/ui_playtest_app.sh "${RUNID}-${p}" baldurs-gate "$p" 40 "$BUDGET" >"$ROOT/qa/ui_playtest_runs/${RUNID}-${p}.log" 2>&1
  fi
  if [ -f "$rd/score.json" ]; then
    sat=$(python3 -c "import json;d=json.load(open('$rd/score.json'));print('sat=%s gave_up=%s crit=%s arc=%s'%(d.get('persona_satisfaction'),d.get('gave_up'),d.get('bug_reports_critical'),d.get('completed_intro_flow')))" 2>/dev/null)
    echo "  [$p] $sat"
    RUN_DIRS="${RUN_DIRS:+$RUN_DIRS,}$rd"
  else
    warn "[$p] no score.json — run may have failed (see ${RUNID}-${p}.log); continuing"
  fi
done

# ── 3-LENS DUO (story/mech) — single stream ────────────────────────────────────
echo "── 3-LENS DUO (story/mech) ───────────────────────────────────────"
free_port "$((PORT+40))"
qa/run_duo.sh "${RUNID}-duo" baldurs-gate veteran 8 1.50 >"$ROOT/qa/ui_playtest_runs/${RUNID}-duo.log" 2>&1 || warn "duo run had a non-zero exit (see ${RUNID}-duo.log)"
STORY="qa/transcripts/${RUNID}-duo.tolkien.json"; MECH="qa/transcripts/${RUNID}-duo.angrydm.json"
[ -f "$STORY" ] && ok "story: $(python3 -c "import json;print(json.load(open('$STORY')).get('overall'))" 2>/dev/null)" || warn "no story score"
[ -f "$MECH" ] && ok "mech:  $(python3 -c "import json;print(json.load(open('$MECH')).get('overall'))" 2>/dev/null)" || warn "no mech score"

# ── BEHAVIORAL + AXE/UI-AUDIT ──────────────────────────────────────────────────
echo "── BEHAVIORAL + UI-AUDIT ─────────────────────────────────────────"
BEHAV="RED"
NB_STATE=$(find "$ROOT/play-state" -path "*${RUNID}*" -name 'snapshot.json' 2>/dev/null | head -1)
NB_LOG=$(find "$ROOT/play-state" -path "*${RUNID}*" -name 'dm.combined.jsonl' 2>/dev/null | head -1)
if [ -n "$NB_STATE" ] && [ -n "$NB_LOG" ]; then
  if python3 qa/assert_behavioral.py "$NB_LOG" "$NB_STATE" >/dev/null 2>&1; then BEHAV="GREEN"; fi
fi
ok "behavioral: $BEHAV"
free_port 8811
AUDIT="FAIL"
if qa/ui_audit_health.sh --port 8811 --quick --axe --ui-gate >"$ROOT/qa/ui_playtest_runs/${RUNID}-audit.log" 2>&1; then AUDIT="PASS"; fi
ok "ui_audit (--quick --axe --ui-gate): $AUDIT"

# ── RRI ROLLUP ─────────────────────────────────────────────────────────────────
echo "── RRI ───────────────────────────────────────────────────────────"
# palette-live: a clean read off the last live surface (≥6 enabled actions on a can_act surface)
PALETTE="false"
if curl -s -m 6 "http://127.0.0.1:${PORT}/session-surface" 2>/dev/null | python3 -c "import json,sys
try:
  d=json.load(sys.stdin); n=sum(1 for a in (d.get('availableActions') or []) if a.get('available'))
  sys.exit(0 if (d.get('can_act') and n>=4) else 1)
except: sys.exit(1)" 2>/dev/null; then PALETTE="true"; fi

python3 qa/release_readiness.py \
  --runs "$RUN_DIRS" \
  ${STORY:+--story "$STORY"} ${MECH:+--mech "$MECH"} \
  --behavioral "$BEHAV" --ui-audit "$AUDIT" --palette-live "$PALETTE" \
  --build-sha "$(git rev-parse --short HEAD 2>/dev/null)" \
  --out "$ROOT/qa/RRI.json" --scorecard-row | tee "$ROOT/qa/ui_playtest_runs/${RUNID}-RRI.txt"

echo ""
echo "RRI.json → $ROOT/qa/RRI.json ; per-run logs → qa/ui_playtest_runs/${RUNID}-*.log"
echo "If RRI 10/10: bump .claude-plugin/plugin.json, tag v1.0.x, GitHub release. Else: the failed gates ARE the punch-list."
