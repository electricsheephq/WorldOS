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

port_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; }
pid_cwd() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }
pid_cmd() { ps -p "$1" -o command= 2>/dev/null || true; }
repo_realpath() { (cd "$ROOT" && pwd -P) 2>/dev/null || printf '%s' "$ROOT"; }
pid_belongs_to_root() {
  local pid="$1" root_real cwd cmd
  root_real="$(repo_realpath)"
  cwd="$(pid_cwd "$pid")"
  cmd="$(pid_cmd "$pid")"
  [ "$cwd" = "$root_real" ] && return 0
  case "$cmd" in
    *"$root_real/"*|*"$ROOT/"*) return 0;;
  esac
  return 1
}
free_port() {
  local port="$1" pid cmd
  for pid in $(port_pids "$port"); do
    if pid_belongs_to_root "$pid"; then
      cmd="$(pid_cmd "$pid")"
      warn "port $port occupied by current worktree process pid=$pid (${cmd:-unknown}) — stopping it"
      kill "$pid" 2>/dev/null || true
    else
      cmd="$(pid_cmd "$pid")"
      fail "port $port is occupied by a non-current process pid=$pid (${cmd:-unknown}); pass --port to use an isolated release-gate range"
    fi
  done
}
free_port_range() {
  local start="$1" count="${2:-1}" p
  for p in $(seq "$start" $((start + count - 1))); do
    free_port "$p"
  done
  sleep 2
}

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
  local art_root="" candidate art
  for candidate in "${WORLDOS_ART_REPO_ROOT:-}" "${CLAWDND_ART_REPO_ROOT:-}" "${WORLDOS_REPO_ROOT:-}" "${CLAWDND_REPO_ROOT:-}" "$ROOT"; do
    [ -n "$candidate" ] || continue
    art="$candidate/content/worlds/_private/baldurs-gate/images"
    if [ -d "$art" ] && [ "$(find "$art" -name 'image.png' 2>/dev/null | head -5 | wc -l | tr -d ' ')" -ge 1 ]; then
      art_root="$candidate"
      break
    fi
  done
  art="${art_root:-$ROOT}/content/worlds/_private/baldurs-gate/images"
  if [ -d "$art" ] && [ "$(find "$art" -name 'image.png' 2>/dev/null | head -5 | wc -l | tr -d ' ')" -ge 1 ]; then
    export WORLDOS_ART_REPO_ROOT="$art_root" CLAWDND_ART_REPO_ROOT="$art_root"
    ok "_private art present at $art ($(find "$art" -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ') scopes)"
  else
    fail "_private art MISSING. Checked WORLDOS_ART_REPO_ROOT/CLAWDND_ART_REPO_ROOT, WORLDOS_REPO_ROOT/CLAWDND_REPO_ROOT, and $ROOT. Set WORLDOS_ART_REPO_ROOT to the canonical private-art checkout before running the gate from a clean worktree."
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
    if [ "$PREFLIGHT_ONLY" = "1" ]; then
      warn "port $PORT is occupied — preflight-only will not kill it"
      ok "gate port $PORT checked"
    else
      warn "port $PORT is occupied — stopping only current-worktree listeners"
      free_port "$PORT"
      sleep 2
      ok "gate port $PORT free"
    fi
  else
    ok "gate port $PORT free"
  fi

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
  local missing_cmds=() cmd
  for cmd in python3 jq curl lsof uv claude node npx swift timeout; do
    command -v "$cmd" >/dev/null 2>&1 || missing_cmds+=("$cmd")
  done
  if [ "${#missing_cmds[@]}" -gt 0 ]; then
    fail "missing required command(s): ${missing_cmds[*]}"
  fi
  [ -f "$ROOT/qa/playwright/node_modules/playwright/package.json" ] \
    || fail "Playwright not installed at qa/playwright/node_modules/playwright. Run: (cd qa/playwright && npm install && npx playwright install chromium)"
  ok "all orchestrated tools and command deps present"
  echo "── preflight OK ──────────────────────────────────────────────────"
}

preflight
[ "$PREFLIGHT_ONLY" = "1" ] && { echo "preflight-only: done."; exit 0; }

[ "$LEAN" = "1" ] && export CLAWDND_LEAN_BEATS=1 && ok "lean-beats ENABLED (CLAWDND_LEAN_BEATS=1)"

# ── SWEEP — 5 personas, ONE heavy stream at a time (host discipline) ────────────
echo "── SWEEP (${PERSONAS}) on the BUILT .app ─────────────────────────"
first=1; RUN_DIRS=""; MISSING_SCORES=""; FIRST_PERSONA_FAILED=0
IFS=',' read -ra PS <<< "$PERSONAS"
for p in "${PS[@]}"; do
  free_port_range "$PORT" 4
  free_port_range "$((PORT+20))" 12
  rd="$ROOT/qa/ui_playtest_runs/${RUNID}-${p}"
  RUN_DIRS="${RUN_DIRS:+$RUN_DIRS,}$rd"
  if [ "$first" = "1" ]; then
    # persona 1 does the FULL build (part A rebuilds the .app + native #356 gate)
    echo "[$p] part A+B (fresh build + native gate + play)…"
    WOS_APP_NO_GLOBAL_KILL=1 WOS_APP_PART=AB WOS_APP_PREFERRED_PORT="$PORT" qa/ui_playtest_app.sh "${RUNID}-${p}" baldurs-gate "$p" 40 "$BUDGET" >"$ROOT/qa/ui_playtest_runs/${RUNID}-${p}.log" 2>&1
    first=0
  else
    echo "[$p] part B (reuse build)…"
    WOS_APP_NO_GLOBAL_KILL=1 WOS_APP_PART=B WOS_APP_SKIP_BUILD=1 WOS_APP_PREFERRED_PORT="$PORT" qa/ui_playtest_app.sh "${RUNID}-${p}" baldurs-gate "$p" 40 "$BUDGET" >"$ROOT/qa/ui_playtest_runs/${RUNID}-${p}.log" 2>&1
  fi
  if [ -f "$rd/score.json" ]; then
    sat=$(python3 -c "import json;d=json.load(open('$rd/score.json'));print('sat=%s gave_up=%s crit=%s arc=%s'%(d.get('persona_satisfaction'),d.get('gave_up'),d.get('bug_reports_critical'),d.get('completed_intro_flow')))" 2>/dev/null)
    echo "  [$p] $sat"
  else
    warn "[$p] missing persona score.json — run may have failed (see ${RUNID}-${p}.log)"
    MISSING_SCORES="${MISSING_SCORES:+$MISSING_SCORES,}$p"
    # FAST-STOP: persona 1 does the full build + native gate. If IT produced no score,
    # the build/play path is broken (e.g. #420 play.sh crash, backend_not_ready) — every
    # subsequent persona will fail identically. Don't burn ~30min on duo+behavioral+axe
    # against a dead build. Still fall through to RRI so the result is explicitly partial
    # and harness-contaminated instead of disappearing as a shell abort.
    if [ "$p" = "${PS[0]}" ]; then
      pa=$(python3 -c "import json;print((json.load(open('$rd/run.json')).get('part_a') or {}).get('result','?'))" 2>/dev/null || echo "?")
      pb=$(python3 -c "import json;print((json.load(open('$rd/run.json')).get('part_b') or {}).get('persona_loop','?'))" 2>/dev/null || echo "?")
      echo ""
      echo "❌ GATE-PARTIAL: first persona ($p) produced NO score on a FRESH build."
      echo "   part_a=$pa  part_b=$pb  — the build/play path is broken; the rest of the"
      echo "   sweep would likely fail identically. Fix the build/play blocker, then re-run."
      echo "   Diagnostic tail (backend.log):"
      tail -3 "$rd/backend.log" 2>/dev/null | sed 's/^/     /'
      echo "   Full log: qa/ui_playtest_runs/${RUNID}-${p}.log"
      FIRST_PERSONA_FAILED=1
      break
    fi
  fi
done
[ -n "$MISSING_SCORES" ] && warn "missing persona score(s): $MISSING_SCORES"

# ── 3-LENS DUO (story/mech) — single stream ────────────────────────────────────
STORY=""; MECH=""; BEHAV="RED"; AUDIT="FAIL"; UI_AUDIT_LOG="$ROOT/qa/ui_playtest_runs/${RUNID}-audit.log"; BEHAV_PATH=""
if [ "$FIRST_PERSONA_FAILED" = "1" ]; then
  warn "skipping duo/behavioral/ui-audit because the fresh built-app persona failed before scoring"
else
  echo "── 3-LENS DUO (story/mech) ───────────────────────────────────────"
  free_port_range "$((PORT+40))" 12
  DUO_PROMPT="$ROOT/qa/play_player_duo.txt"
  [ -f "$DUO_PROMPT" ] || fail "missing duo prompt file: $DUO_PROMPT"
  qa/run_duo.sh "${RUNID}-duo" baldurs-gate "$DUO_PROMPT" 8 1.50 >"$ROOT/qa/ui_playtest_runs/${RUNID}-duo.log" 2>&1 || warn "duo run had a non-zero exit (see ${RUNID}-duo.log)"
  STORY="qa/transcripts/${RUNID}-duo.tolkien.json"; MECH="qa/transcripts/${RUNID}-duo.angrydm.json"
  [ -f "$STORY" ] && ok "story: $(python3 -c "import json;print(json.load(open('$STORY')).get('overall'))" 2>/dev/null)" || warn "no story score"
  [ -f "$MECH" ] && ok "mech:  $(python3 -c "import json;print(json.load(open('$MECH')).get('overall'))" 2>/dev/null)" || warn "no mech score"

  # ── BEHAVIORAL + AXE/UI-AUDIT ────────────────────────────────────────────────
  echo "── BEHAVIORAL + UI-AUDIT ─────────────────────────────────────────"
  BEHAV_PATH="$ROOT/qa/ui_playtest_runs/${RUNID}-behavioral.txt"
  DUO_LOG="$ROOT/qa/transcripts/${RUNID}-duo.jsonl"
  DUO_STATE="$ROOT/qa/transcripts/${RUNID}-duo.state.json"
  DUO_CHAT="$ROOT/qa/transcripts/${RUNID}-duo.chat.jsonl"
  DUO_MOVES="$ROOT/qa/state/${RUNID}-duo/player_moves.jsonl"
  if [ -s "$DUO_LOG" ] && [ -s "$DUO_STATE" ]; then
    if python3 qa/assert_behavioral.py "$DUO_LOG" "$DUO_STATE" "$DUO_CHAT" "$DUO_MOVES" >"$BEHAV_PATH" 2>&1; then BEHAV="GREEN"; fi
  else
    echo "missing duo behavioral artifacts" >"$BEHAV_PATH"
  fi
  ok "behavioral: $BEHAV ($BEHAV_PATH)"
  free_port_range 8811 3
  if qa/ui_audit_health.sh --port 8811 --quick --axe --ui-gate >"$UI_AUDIT_LOG" 2>&1; then AUDIT="PASS"; fi
  ok "ui_audit (--quick --axe --ui-gate): $AUDIT ($UI_AUDIT_LOG)"
fi

# ── RRI ROLLUP ─────────────────────────────────────────────────────────────────
echo "── RRI ───────────────────────────────────────────────────────────"
# palette-live: read a persisted session_surface.final.json from the persona run. Never trust
# a live port after teardown.
PALETTE="false"
PALETTE_SOURCE=""
IFS=',' read -ra RD_ARR <<< "$RUN_DIRS"
for ((i=${#RD_ARR[@]}-1; i>=0; i--)); do
  cand="${RD_ARR[$i]}/session_surface.final.json"
  if [ -s "$cand" ]; then PALETTE_SOURCE="$cand"; break; fi
done
if [ -n "$PALETTE_SOURCE" ] && python3 - "$PALETTE_SOURCE" <<'PY'
import json, sys
try:
    d=json.load(open(sys.argv[1]))
    actions = d.get("enabledActions") or d.get("availableActions") or []
    n = sum(1 for a in actions if isinstance(a, dict) and a.get("available", True) is not False)
    sys.exit(0 if d.get("can_act") and n >= 6 else 1)
except Exception:
    sys.exit(1)
PY
then PALETTE="true"; fi

set +e
python3 qa/release_readiness.py \
  --runs "$RUN_DIRS" \
  --expected-personas "$PERSONAS" \
  ${STORY:+--story "$STORY"} ${MECH:+--mech "$MECH"} \
  --behavioral "$BEHAV" --ui-audit "$AUDIT" --palette-live "$PALETTE" \
  ${BEHAV_PATH:+--behavioral-path "$BEHAV_PATH"} \
  --ui-audit-log "$UI_AUDIT_LOG" \
  ${PALETTE_SOURCE:+--palette-source "$PALETTE_SOURCE"} \
  --build-sha "$(git rev-parse --short HEAD 2>/dev/null)" \
  --out "$ROOT/qa/RRI.json" --scorecard-row | tee "$ROOT/qa/ui_playtest_runs/${RUNID}-RRI.txt"
RRI_RC="${PIPESTATUS[0]}"
set -e

echo ""
echo "RRI.json → $ROOT/qa/RRI.json ; per-run logs → qa/ui_playtest_runs/${RUNID}-*.log"
echo "If RRI 10/10: bump .claude-plugin/plugin.json, tag v1.0.x, GitHub release. Else: the failed gates ARE the punch-list."
exit "$RRI_RC"
