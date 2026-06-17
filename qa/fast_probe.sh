#!/usr/bin/env bash
# WorldOS FAST-GATE Tier 1 — fast LLM iteration probe (~18-25 min, ~$2-3).
# A ROTATED persona (sweeps cross-persona variance over 5 loops at one-persona cost — the critique's
# fix for "one fixed optimizer misses the veteran/adversarial fails") + a 6-beat duo (G5 story/mech;
# >=6 beats keeps assert_behavioral's progression floors armed). An ITERATION signal, NOT a release
# verdict — run the milestone 5-persona sweep before merge/release. See docs/qa/FAST_GATE.md.
#   Usage: qa/fast_probe.sh [persona]   (omit to rotate by iteration index; pass one to pin it)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 2
PERSONAS=(newbie veteran adversarial narrative optimizer)
ITERF="$ROOT/qa/.fast_gate_iter"   # gitignored; tracks the rotation across loops
i="$(cat "$ITERF" 2>/dev/null || echo 0)"; case "$i" in ''|*[!0-9]*) i=0;; esac
P="${1:-${PERSONAS[$((i % 5))]}}"; echo $((i + 1)) > "$ITERF"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"; RUN="fastp-$SHA"; LOGD="${TMPDIR:-/tmp}"
echo "── FAST-GATE Tier 1 — persona=$P (iter $i; rotates newbie→veteran→adversarial→narrative→optimizer) + 6-beat duo ──"
echo "   ~18-25 min, ~\$2-3. ITERATION signal only — the milestone 5-persona sweep is the release verdict."

if [ "${FAST_PROBE_DRYRUN:-0}" = 1 ]; then
  echo "DRYRUN: would run  qa/ui_playtest.sh $RUN-$P baldurs-gate $P 12 1.50"
  echo "DRYRUN: would run  qa/run_duo.sh $RUN-duo baldurs-gate qa/play_player_duo.txt 6 0.80"
  exit 0
fi

# The persona half needs the Playwright palette install (qa/playwright/node_modules — gitignored, so a
# FRESH WORKTREE lacks it; the duo half doesn't, which is why a probe-from-worktree fails persona-only).
# Reuse a shared install via symlink so Tier-1 runs from any worktree without a per-checkout npm install.
PW="$ROOT/qa/playwright/node_modules"
if [ ! -d "$PW" ]; then
  for cand in "${WORLDOS_PLAYWRIGHT_DIR:-}" /root/worldos-qa/WorldOS/qa/playwright/node_modules "$HOME/WorldOS/qa/playwright/node_modules"; do
    [ -n "$cand" ] && [ -d "$cand" ] && { ln -s "$cand" "$PW" 2>/dev/null && echo "  (linked Playwright palette from $cand)"; break; }
  done
  [ -d "$PW" ] || echo "  ⚠ Playwright palette not found — persona half will skip. Install once: (cd qa/playwright && npm install && npx playwright install chromium)"
fi

echo "[1/2] persona $P (headless GUI lane, no .app rebuild) …"
qa/ui_playtest.sh "$RUN-$P" baldurs-gate "$P" 12 1.50 >"$LOGD/$RUN-$P.log" 2>&1 || echo "  (persona returned nonzero — see $LOGD/$RUN-$P.log)"
echo "[2/3] 6-beat duo (G5 STORY — roleplay/arc craft; floors armed at ≥6 beats) …"
CLAWDND_LEAN_BEATS=1 qa/run_duo.sh "$RUN-duo" baldurs-gate qa/play_player_duo.txt 6 0.80 >"$LOGD/$RUN-duo.log" 2>&1 || echo "  (duo returned nonzero — see $LOGD/$RUN-duo.log)"

# G5 MECH read MUST come from the combat-sprint, NOT the duo: an emergent duo player drifts to roleplay
# and rarely fights (combat≈0), so the duo's mech/Angry-DM lens scores low for lack of mechanics to
# judge — a SAMPLING ARTIFACT, not an engine defect (validated 2026-06-05: duo mech 3.3 while a forced
# sprint resolved 587 attacks with the behavioral gate ALL-GREEN). The sprint forces the fight.
echo "[3/3] combat-sprint (G5 MECH + G1 engine-combat — forces a fight; the reliable mech read) …"
qa/run_combat_sprint.sh "$RUN-sprint" >"$LOGD/$RUN-sprint.log" 2>&1 || echo "  (sprint returned nonzero — see $LOGD/$RUN-sprint.log)"

echo "── Tier-1 result (build $SHA, persona=$P) ──"
SC="$ROOT/qa/ui_playtest_runs/$RUN-$P/score.json"
if [ -f "$SC" ]; then
  python3 -c "import json;d=json.load(open('$SC'));print('  persona %-11s sat=%s gaveup=%s crit=%s intro=%s'%('$P',d.get('persona_satisfaction'),d.get('gave_up'),d.get('bug_reports_critical'),d.get('completed_intro_flow')))"
else echo "  persona score.json missing — see $LOGD/$RUN-$P.log"; fi
echo "  G5 STORY (duo — roleplay/arc):"
grep -iE "tolkien|story[- ]craft|behavioral|GREEN|RED|overall=" "$LOGD/$RUN-duo.log" | tail -4 | sed 's/^/    /'
echo "  G5 MECH + G1 combat (combat-sprint — the RELIABLE mech read; the duo under-samples it):"
grep -iE "angry|mechanical|combat_resolved|xp_awarded|behavioral|GREEN|RED|PASS|FAIL|overall=" "$LOGD/$RUN-sprint.log" | tail -6 | sed 's/^/    /'
echo "  → run 5× to sweep all personas (coverage-over-time). Before merge: the milestone sweep + RRI."
