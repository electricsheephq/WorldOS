#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# REFERENCE COPY - the support-VM heavy gate-sweep harness (part-B 5-persona).
#
# This is a checked-in snapshot of the script that lives on the evaos-support VM
# at /root/worldos-qa/sweep_v2.sh (the canonical runnable copy). It runs the heavy
# 5-persona OpenWorlds-browser sweep + duo + ui_audit and rolls up an RRI via
# qa/release_readiness.py. Paths/ports below are VM-internal (/root/worldos-qa/...)
# and are intentionally left as-is for reference; this copy is NOT meant to run on
# the Mac. See the `worldos-dev` skill (sec. "VM GATE SWEEP" and sec. "RRI evidence-path
# contract") and WorldOS-GUI-RUNBOOK.md sec. "Support VM lane".
#
# TWO load-bearing things this version gets right (cost a real diagnosis):
#   1. RRI evidence-path contract - it passes the *evidence-path* flags
#      (--behavioral-path / --ui-audit-log / --palette-source), not just value
#      flags. Without them release_readiness.py records an `evidence_gap` and
#      RED-caps the gate -> the RRI reads FALSELY LOW (a real ~4.5/11 once masked
#      as 1.8/11). native_gate's --handoff-json is the Mac Part-A and is
#      *structurally absent* on a VM-only sweep (joined at the same SHA later).
#   2. behavioral-from-duo-log - it reads `behavioral=GREEN` from run_duo.sh's own
#      log (run_duo runs assert_behavioral.py itself), NOT the nonexistent
#      qa/transcripts/vm2-duo.combined.jsonl, which defaulted RED. These two fixes
#      took the sweep's RRI from 1.8 -> 4.5 with no behavior/score change.
#
# LEAN IS ON (2026-06-06) — the 2026-06-05 lean-OFF decision is SUPERSEDED. #683 fixed the
# cross-campaign contamination (the lean re-ground was selecting the WRONG campaign by largest-
# snapshot; now resolves the engine-authoritative live campaign) and #685 added the lean output-
# discipline (clean prose). lean-ON matches the PRODUCTION default (CLAWDND_LEAN_BEATS:-1) and gives
# FAST routine beats — lean-OFF would replay the growing Opus transcript (3-5+ min/beat), risking
# latency give-ups / per-persona timeouts (the wasted-sweep vector). Set explicitly below.
# -----------------------------------------------------------------------------
# v2 VM gate sweep: canary-first, then SEQUENTIAL personas (#844 quota-safe — the old
# "PARALLEL = use the 30GB/16vCPU" premise was wrong: cold-open is API-generation-bound,
# not VM-CPU-bound, so parallel bought no speed and burst the account session-quota 4x;
# rc3 429'd mid-batch and rolled a junk 1.8. Sequential lets the FIRST 429 abort the rest).
# lean is ON (production-matching; #683/#685-fixed) — see the header block for the supersession rationale.
# no set -e (one persona failing must not abort the batch). Explicit PATH + IS_SANDBOX.
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH
export IS_SANDBOX=1
export CLAWDND_LEAN_BEATS=1   # lean-ON (production-matching; #683/#685-fixed; fast Opus beats). See header.
cd /root/worldos-qa/WorldOS || { echo "NO REPO"; exit 1; }
RES=/root/worldos-qa/results; mkdir -p "$RES"
SHA="$(git rev-parse --short HEAD)"; LOG="$RES/sweep2.log"; : > "$LOG"
note(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
rm -f "$RES/DONE" "$RES/CANARY_FAIL" "$RES/QUOTA_ABORT" 2>/dev/null

# QUOTA-ABORT detection (the rc3 lesson). A `claude -p` DM beat that 429s on the account
# session limit writes "session limit" / "HTTP 429" into the persona backend.log. A sweep
# that 429s is INFRA-aborted, NOT a product measurement — detect it so we abort honestly
# instead of rolling up quota-corpses into a misleading RRI (rc3 rolled a fake 1.8 from a
# 429-storm). Sequential personas (below) mean the FIRST 429 aborts before the rest spend.
quota_tripped(){  # $1 = a run dir or a log path; rc 0 if a session-limit/429 is present
  grep -qriE "session limit|HTTP 429|hit your (session|usage) limit" "$1" 2>/dev/null
}
quota_reset_hint(){  # echoes e.g. "resets 3:50pm UTC" from the log(s), if present
  grep -hroiE "resets [0-9: ]*[ap]m \(?(UTC|[A-Za-z/_]+)\)?" "$@" 2>/dev/null | head -1
}

# 0) kill the stuck v1 orchestrator + any stray vm- play procs; free ports
note "killing v1 orchestrator + stray procs..."
pkill -f gate_sweep.sh 2>/dev/null
pkill -f 'lean_beats_check' 2>/dev/null
pkill -f 'play.sh baldurs-gate vm-' 2>/dev/null; pkill -f 'play_party.sh baldurs-gate vm-' 2>/dev/null
pkill -f 'play.sh baldurs-gate leanchk' 2>/dev/null
for p in $(seq 8810 8830) 8884 8885; do lsof -ti:$p 2>/dev/null | xargs kill -9 2>/dev/null; done
sleep 4
note "start build=$SHA (sequential personas — quota-safe #844, lean ON — production-matching, fast Opus beats)"

# 0.5) SUPPORT-VM PREFLIGHT (#730) — run BEFORE any persona spend so a blocked host is
# recorded up front, and so the split VM+Mac RRI rollup can prove same-SHA heavy-lane
# readiness (release_readiness.py requires --support-preflight-json whenever VM persona
# evidence relies on --handoff-json for the Mac native proof). The CLI writes
# support_vm_preflight.json into --artifact-dir; we copy it to the stable rollup path.
# --no-fail: a blocked preflight is RECORDED, not fatal — the sweep continues and the
# rollup surfaces the gap honestly (verdict/ready_for_rri carry the blockers).
# On this VM the private art IS rsynced into the repo at content/worlds/_private,
# so --private-art-mode required with --art-root at the repo root is correct.
note "support-VM preflight (artifact-first; failure tolerated + surfaced in the rollup)..."
rm -f "$RES/support_preflight.json" 2>/dev/null
mkdir -p "$RES/preflight"
timeout 300 python3 qa/support_vm_preflight.py \
  --repo "$PWD" \
  --expected-sha "$SHA" \
  --artifact-dir "$RES/preflight" \
  --artifact-return-target "$RES" \
  --art-root "$PWD" \
  --private-art-mode required \
  --no-fail \
  > "$RES/preflight.log" 2>&1
note "preflight rc=$? (rc is informational under --no-fail; see preflight.log)"
if [ -f "$RES/preflight/support_vm_preflight.json" ]; then
  cp "$RES/preflight/support_vm_preflight.json" "$RES/support_preflight.json"
  note "preflight artifact -> $RES/support_preflight.json (ready_for_rri=$(python3 -c "import json;print(json.load(open('$RES/support_preflight.json')).get('ready_for_rri'))" 2>/dev/null))"
else
  note "preflight did NOT write support_vm_preflight.json — continuing; the RRI rollup will surface the support_preflight evidence gap honestly"
fi

run_persona(){  # $1=persona $2=port  -> writes results/score-$1.json
  local persona="$1" port="$2"
  # #735: wipe THIS persona's reused play-state stores (the solo run + the Part-B `-b` store)
  # before launch so each run mints into a CLEAN campaigns/ tree -> exactly one seated campaign.
  # A re-run otherwise stacks a 2nd seated save in the same store (the ': >' truncations reset
  # only the sidecars, never campaigns/), and two equal-recency seated saves were the precondition
  # for the active-PC silent-switch (the live-campaign resolvers disagreed on the tie). cwd is the
  # repo (line 38). Guarded on a non-empty persona so the glob can never widen to all of play-state/.
  if [ -n "$persona" ]; then
    rm -rf "play-state/vm2-$persona" "play-state/vm2-$persona-b" 2>/dev/null
  fi
  # Opus de-risk: longer per-persona deadline (Opus cold-open ~300s + slower beats) + a bigger run
  # budget (Opus cold-open ~$2.4 + beats + player). The harnesses cap per-turn model-aware (#684/#686).
  WOS_APP_PART=B WOS_APP_SKIP_BUILD=1 WOS_APP_PREFERRED_PORT=$port \
    timeout 2400 bash qa/ui_playtest_app.sh "vm2-$persona" baldurs-gate "$persona" 40 18.00 \
    > "$RES/vm2-$persona.log" 2>&1
  local rc=$?
  lsof -ti:$port 2>/dev/null | xargs kill -9 2>/dev/null
  pkill -f "play.sh baldurs-gate vm2-$persona" 2>/dev/null
  pkill -f "play_party.sh baldurs-gate vm2-$persona" 2>/dev/null
  local sc="qa/ui_playtest_runs/vm2-$persona/score.json"
  if [ -f "$sc" ]; then
    cp "$sc" "$RES/score-$persona.json"
    note "  $persona rc=$rc $(python3 -c "import json;d=json.load(open('$sc'));print('sat=%s gaveup=%s crit=%s arc=%s turns=%s'%(d.get('persona_satisfaction'),d.get('gave_up'),d.get('bug_reports_critical'),d.get('completed_intro_flow'),d.get('in_story_turns')))" 2>/dev/null)"
  else
    note "  $persona rc=$rc - NO SCORE (see vm2-$persona.log)"
  fi
}

# 1) CANARY: newbie alone. Verify scoring works before spending on the batch.
note "CANARY: newbie (verifying part-B produces a score on the VM)..."
run_persona newbie 8810
CANARY_BL="qa/ui_playtest_runs/vm2-newbie/backend.log"
if [ ! -f "$RES/score-newbie.json" ]; then
  # Distinguish a QUOTA abort (account 429) from a genuine product/harness failure: a
  # 429-killed canary means the account is already over its session limit, so the whole
  # batch would 429 too — abort honestly with the reset hint rather than burning it.
  if quota_tripped "$CANARY_BL"; then
    note "QUOTA ABORT at the canary — claude account session limit ($(quota_reset_hint "$CANARY_BL")). The batch would 429 too; not spending it. INFRA abort, NOT a product measurement."
    echo "newbie $(quota_reset_hint "$CANARY_BL")" > "$RES/QUOTA_ABORT"
    touch "$RES/DONE"; exit 0
  fi
  note "CANARY FAILED - no score-newbie.json. Aborting batch; see vm2-newbie.log for the cause."
  note "  --- vm2-newbie.log tail ---"; tail -25 "$RES/vm2-newbie.log" >> "$LOG" 2>/dev/null
  touch "$RES/CANARY_FAIL"; touch "$RES/DONE"; exit 0
fi
# Even a SCORED canary can be followed by a quota trip on its own retries; check before the batch.
if quota_tripped "$CANARY_BL"; then
  note "QUOTA ABORT — the canary scored but its backend 429'd ($(quota_reset_hint "$CANARY_BL")); the account is at its session limit. Not spending the batch."
  echo "newbie $(quota_reset_hint "$CANARY_BL")" > "$RES/QUOTA_ABORT"
  touch "$RES/DONE"; exit 0
fi
note "CANARY OK - scoring works. Running the other 4 personas SEQUENTIALLY (quota-safe)."

# 2) Sequential batch: veteran/adversarial/narrative/optimizer. WHY sequential, not parallel
# (the rc3 lesson): cold-open is API-generation-bound, NOT VM-CPU-bound (worldos-latency-
# forensics), so the old "parallel = use the 16 vCPUs" premise does not speed up generation —
# it just bursts the account session-quota 4x and, when the limit trips mid-batch, wastes 3-4
# cold-opens on 429 corpses AND rolls up a junk RRI. Sequential spends one cold-open at a time
# and the FIRST 429 aborts the remainder. (Set SWEEP_PERSONA_CONCURRENCY>1 for a quota-rich window.)
for pp in "veteran 8812" "adversarial 8814" "narrative 8816" "optimizer 8818"; do
  set -- $pp
  run_persona "$1" "$2"
  bl="qa/ui_playtest_runs/vm2-$1/backend.log"
  if quota_tripped "$bl"; then
    note "QUOTA ABORT after '$1' — claude account session limit ($(quota_reset_hint "$bl")). Stopping; remaining personas NOT spent. INFRA abort, NOT a product result."
    echo "$1 $(quota_reset_hint "$bl")" > "$RES/QUOTA_ABORT"
    break
  fi
done
note "persona batch done."

# QUOTA-ABORT short-circuit: if the persona batch 429'd, do NOT spend the duo on a dead
# account, and do NOT roll up an RRI from quota-corpses (rc3 emitted a misleading 1.8 this
# way). Emit an explicit ABORTED status the ledger/scorecard can never read as a product score.
if [ -f "$RES/QUOTA_ABORT" ]; then
  note "=== RRI SKIPPED — QUOTA_ABORT ($(cat "$RES/QUOTA_ABORT")) — not a product measurement ==="
  python3 - "$RES/RRI.json" "$SHA" "$(cat "$RES/QUOTA_ABORT")" <<'PY' 2>/dev/null
import json, sys
out, sha, detail = sys.argv[1], sys.argv[2], sys.argv[3]
json.dump({"status": "ABORTED", "abort_reason": "quota_session_limit",
           "detail": detail, "build_sha": sha, "release_ready": False,
           "note": "claude account session limit (HTTP 429) tripped mid-sweep; "
                   "this is an INFRA abort, NOT a product RRI. Re-run after the quota resets."},
          open(out, "w"), indent=2)
PY
  note "=== SWEEP COMPLETE (QUOTA-ABORTED) -> $RES ==="; touch "$RES/DONE"; exit 0
fi

# 3) duo (story/mech) + behavioral + audit - run after personas (sequential, cheap-ish)
note "3-lens duo..."
timeout 3600 bash qa/run_duo.sh vm2-duo baldurs-gate veteran 8 5.00 > "$RES/duo.log" 2>&1
for f in tolkien angrydm; do s="qa/transcripts/vm2-duo.$f.json"; [ -f "$s" ] && cp "$s" "$RES/duo-$f.json" && note "  $f overall=$(python3 -c "import json;print(json.load(open('$s')).get('overall'))" 2>/dev/null)"; done
# F13-4 (#753): carry the duo's derived latency ledger into the sweep results so scores_db
# can stamp s_per_beat/coldopen_s/turns_per_beat (the #753 budget ledger). run_duo wrote it.
LAT="qa/transcripts/vm2-duo.latency.json"; [ -f "$LAT" ] && cp "$LAT" "$RES/duo-latency.json" && note "  latency $(python3 -c "import json;d=json.load(open('$LAT'));print('s/beat=%s cold-open=%ss turns/beat=%s'%(d.get('s_per_beat'),d.get('coldopen_s'),d.get('turns_per_beat')))" 2>/dev/null)"
DCOMB="qa/transcripts/vm2-duo.combined.jsonl"; DSTATE="qa/transcripts/vm2-duo.state.json"
[ -f "$DCOMB" ] && { python3 qa/assert_behavioral.py "$DCOMB" "$DSTATE" > "$RES/behavioral.log" 2>&1; echo "rc=$?" >> "$RES/behavioral.log"; note "behavioral rc=$(tail -1 "$RES/behavioral.log")"; }
aport=8861; lsof -ti:$aport 2>/dev/null | xargs kill -9 2>/dev/null
( WORLDOS_STATE_DIR=/root/worldos-qa/auditstate python3 viewer/server.py '' $aport >/dev/null 2>&1 & echo $! >"$RES/av.pid" )
sleep 6
# WORLDOS_STATE_DIR on the audit call too: ui_audit_health.sh's --ui-gate seed step needs the
# SAME state dir the audit viewer serves, so an empty auditstate gets one resumable campaign
# (play_reachable can never pass against an empty launcher).
# NO --axe on the VM lane (deliberate): the axe driver detection is Mac-pathed (mac_arm-*
# chromedriver dirs + /Applications Chrome) and would HARD-FAIL here by the honest-gate
# calibration — historically it silently WARN-skipped, so axe NEVER actually ran on the VM
# and rc1's "FAIL(axe)" was a misattribution. axe coverage rides the Mac lane until Linux
# driver support lands (tracked); the VM ui_audit verdict = the structural + ui-gate checks.
WORLDOS_STATE_DIR=/root/worldos-qa/auditstate timeout 600 bash qa/ui_audit_health.sh --port $aport --quick --ui-gate > "$RES/ui_audit.log" 2>&1
note "ui_audit rc=$?"; [ -f "$RES/av.pid" ] && kill "$(cat "$RES/av.pid")" 2>/dev/null

# 4) RRI
behav=RED; grep -q 'behavioral=GREEN' "$RES/duo.log" 2>/dev/null && behav=GREEN   # run_duo.sh runs assert_behavioral itself + prints the verdict; the old $DCOMB path was wrong -> false RED in the RRI
audit=FAIL; grep -qiE 'all checks pass|0 regress|PASS' "$RES/ui_audit.log" 2>/dev/null && audit=PASS
python3 qa/release_readiness.py \
  --runs $(ls -d qa/ui_playtest_runs/vm2-* 2>/dev/null | grep -v duo | paste -sd, -) \
  --story "$RES/duo-tolkien.json" --mech "$RES/duo-angrydm.json" \
  --behavioral $behav --behavioral-path "$RES/duo.log" \
  --ui-audit $audit --ui-audit-log "$RES/ui_audit.log" \
  --palette-live true --palette-source "$RES/ui_audit.log" \
  --support-preflight-json "$RES/support_preflight.json" \
  --build-sha "$SHA" \
  --out "$RES/RRI.json" --scorecard-row > "$RES/rri.txt" 2>&1
note "=== RRI ==="; cat "$RES/rri.txt" | tee -a "$LOG"
note "=== SWEEP COMPLETE -> $RES ==="
touch "$RES/DONE"

touch /root/worldos-qa/results/DONE
