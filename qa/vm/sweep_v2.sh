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
# ⚠ SYNC REMINDER (#1414): this file is a REFERENCE COPY — editing it (including the per-persona
# scores_persist.py auto-append this revision added to run_persona()) changes NOTHING on the VM
# until you rsync/copy it over the canonical runnable copy at
# /root/worldos-qa/sweep_v2.sh on the evaos-support VM. A change landed here and never synced is a
# silent no-op for every real sweep run — always sync after editing this file.
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
# discipline (clean prose). lean-ON matches the PRODUCTION default (WORLDOS_LEAN_BEATS:-1) and gives
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
export WORLDOS_LEAN_BEATS=1   # lean-ON (production-matching; #683/#685-fixed; fast Opus beats). See header.
cd /root/worldos-qa/WorldOS || { echo "NO REPO"; exit 1; }
ROOT="$PWD"   # repo-root anchor (CodeRabbit #1158): EVERY repo-relative cleanup below MUST use "$ROOT" so a non-repo cwd can't leave stale vm2-* run dirs that contaminate the canary/RRI inputs. The cd above lands us at the repo root; capture it once.
RES=/root/worldos-qa/results; mkdir -p "$RES"
SHA="$(git rev-parse --short HEAD)"; LOG="$RES/sweep2.log"; : > "$LOG"
note(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
rm -f "$RES/DONE" "$RES/CANARY_FAIL" "$RES/QUOTA_ABORT" "$RES/RRI.json" 2>/dev/null  # #842 Fix A: wipe the stale RRI.json too — a sweep that quota-aborts before it writes a fresh one must NEVER leave the PREVIOUS run's RRI in place to masquerade as this run's measurement.
rm -f "$RES"/score-*.json 2>/dev/null; rm -rf "$ROOT"/qa/ui_playtest_runs/vm2-* 2>/dev/null  # FRESHNESS (the Jun-20 stale-score bug): a CRASHED canary/persona must NOT false-pass on a prior run's score-newbie.json (the canary's `[ ! -f ]` check), and the RRI rollup must NOT read a prior run's persona score.json. Wipe both up front so every persona score is THIS run's or absent. ANCHORED to "$ROOT" (CodeRabbit #1158) — the sibling above already anchors on "$RES"; this matches it so a non-repo cwd can never silently skip the wipe.

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
# #842 Fix B: write the explicit {"status":"ABORTED",…} RRI.json for a quota abort. EVERY quota
# exit (canary-abort AND post-batch) must stamp this so a stale RRI.json from a prior run can never
# persist and read as THIS run's product score (the rc3 bug). $1 = RRI.json path, $2 = the abort
# detail string (persona + reset hint). Reused by both the canary-abort path and the post-batch
# QUOTA_ABORT short-circuit so the ABORTED JSON shape stays identical at every exit.
write_aborted_rri(){
  python3 - "$1" "$SHA" "$2" <<'PY' 2>/dev/null
import json, sys
out, sha, detail = sys.argv[1], sys.argv[2], sys.argv[3]
# NOTE: the keys MUST match what qa/evidence_audit.py + qa/release_readiness.py consume —
# `aborted: True` (the boolean evidence_audit keys on) and `abort_detail` (NOT `detail`).
# Without `aborted:true`/`abort_detail`, evidence_audit reads the file as RELEASE_READY — the
# exact quota-masking #842 exists to prevent (caught in review). Mirror release_readiness.py:1288.
json.dump({"status": "ABORTED", "aborted": True, "abort_reason": "quota_session_limit",
           "abort_detail": detail, "build_sha": sha, "release_ready": False,
           "note": "claude account session limit (HTTP 429) tripped mid-sweep; "
                   "this is an INFRA abort, NOT a product RRI. Re-run after the quota resets."},
          open(out, "w"), indent=2)
PY
}

# reap_sweep_ports LOW HIGH — free the sweep's OWN listeners on ports LOW..HIGH, NARROWLY.
# CodeRabbit #1158 (overbroad reap): the old `lsof -ti:$p | kill -9` killed ANY listener on the
# range, which on a shared host/CI runner can break unrelated jobs and cause nondeterministic sweep
# failures. Narrow it: for each PID listening on the port, kill ONLY when its argv matches a sweep
# backend (viewer/server.py · play.sh · play_party.sh · play_codex_dm.sh · ui_playtest). A
# non-matching listener (some unrelated service that happens to squat a port in the range) is LEFT
# ALONE. This is ALSO the root-cause reap for the orphaned-viewer bug (PROVEN on the VM 2026-06-23):
# a timeout-/SIGTERM-killed play.sh leaves its viewer ORPHANED + still bound on the backend port
# (app_port+~20, e.g. 8830) because the SIGTERM hits the main shell while it is blocked inside the
# DM cold-open `claude -p`, so play.sh's own EXIT/TERM cleanup never runs. The next persona/retry
# whose backend wants that port then hits "Port 8830 is already in use" and aborts backend_not_ready.
# TERM first (let the supervisor's trap run if it can), then KILL the stragglers. bash 3.2-clean.
reap_sweep_ports() {
  local lo="$1" hi="$2" p pid cmd
  for p in $(seq "$lo" "$hi"); do
    for pid in $(lsof -nP -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null); do
      cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
      case "$cmd" in
        *viewer/server.py*|*play.sh*|*play_party.sh*|*play_codex_dm.sh*|*ui_playtest*)
          kill "$pid" 2>/dev/null || true
          sleep 0.2
          kill -9 "$pid" 2>/dev/null || true
          ;;
      esac
    done
  done
}

# 0) kill the stuck v1 orchestrator + any stray vm- play procs; free ports
note "killing v1 orchestrator + stray procs..."
pkill -f gate_sweep.sh 2>/dev/null
pkill -f 'lean_beats_check' 2>/dev/null
pkill -f 'play.sh baldurs-gate vm-' 2>/dev/null; pkill -f 'play_party.sh baldurs-gate vm-' 2>/dev/null
pkill -f 'play.sh baldurs-gate leanchk' 2>/dev/null
# NARROWED reap (CodeRabbit #1158): only the sweep's OWN backend listeners on 8800-8870 (each
# persona's GUI BACKEND binds app_port+~20, 8830-8838) + the audit viewer ports 8884/8885. A prior
# run's un-reaped/orphaned backend that held a port and crashed EVERY persona rc=1 ("Port … already
# in use") is killed; an unrelated service squatting a port in the range is left untouched.
reap_sweep_ports 8800 8870; reap_sweep_ports 8884 8885
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
  # ROOT-CAUSE reap (PROVEN on the VM 2026-06-23): ui_playtest_app.sh part-B binds its faithful
  # backend at WOS_APP_PREFERRED_PORT+20 ($port+20, e.g. 8830 for app port 8810). A previous
  # persona/attempt whose play.sh was timeout-/SIGTERM-killed mid-cold-open leaves its viewer
  # ORPHANED + still bound on that backend port (the SIGTERM hit play.sh while it was blocked inside
  # the DM `claude -p`, so play.sh's own EXIT/TERM cleanup never ran). The next launch then hits
  # "Port $((port+20)) is already in use" → backend_not_ready → no score (the exact #1158 canary
  # bug). Reap the WHOLE app+backend band ($port..$port+30) — NARROWLY (sweep backends only) —
  # BEFORE the launch so a lingering orphan can't crash THIS persona.
  reap_sweep_ports "$port" "$((port+30))"
  # Opus de-risk: longer per-persona deadline (Opus cold-open ~300s + slower beats) + a bigger run
  # budget (Opus cold-open ~$2.4 + beats + player). The harnesses cap per-turn model-aware (#684/#686).
  WOS_APP_PART=B WOS_APP_SKIP_BUILD=1 WOS_APP_PREFERRED_PORT=$port \
    timeout 2400 bash qa/ui_playtest_app.sh "vm2-$persona" baldurs-gate "$persona" 40 18.00 \
    > "$RES/vm2-$persona.log" 2>&1
  local rc=$?
  # Reap the FULL app+backend band (not just $port): attempt-1's faithful backend lives on
  # $port+20, so the old single-port `lsof -ti:$port` left it bound and crashed the retry below.
  reap_sweep_ports "$port" "$((port+30))"
  # FIX 1 (#623 false-cap): a NON-ZERO player/harness PROCESS exit (rc!=0) is a harness CRASH,
  # not a product-quality signal — it must NOT be laundered into a score_pass quality fail.
  # RETRY ONCE on a clean store before we believe it. A 429 still short-circuits to the honest
  # quota path (reuse quota_tripped), so a quota abort is never spent on a pointless retry. Only
  # when the RE-RUN also exits rc!=0 do we keep the result (ui_playtest_app.sh has by then
  # stamped part_b.harness_error=true, which the RRI rollup reads as INCONCLUSIVE, not a FAIL).
  local _bl="qa/ui_playtest_runs/vm2-$persona/backend.log"
  if [ "$rc" -ne 0 ] && ! quota_tripped "$_bl"; then
    note "  $persona rc=$rc (non-quota harness crash) — retrying ONCE on a clean store"
    pkill -f "play.sh baldurs-gate vm2-$persona" 2>/dev/null
    pkill -f "play_party.sh baldurs-gate vm2-$persona" 2>/dev/null
    if [ -n "$persona" ]; then
      rm -rf "play-state/vm2-$persona" "play-state/vm2-$persona-b" 2>/dev/null
    fi
    # Reap the backend band before the retry too: attempt-1's orphaned viewer on $port+20 is the
    # very thing that crashes the retry with "Port … already in use" if left bound.
    reap_sweep_ports "$port" "$((port+30))"
    WOS_APP_PART=B WOS_APP_SKIP_BUILD=1 WOS_APP_PREFERRED_PORT=$port \
      timeout 2400 bash qa/ui_playtest_app.sh "vm2-$persona" baldurs-gate "$persona" 40 18.00 \
      >> "$RES/vm2-$persona.log" 2>&1
    rc=$?
    note "  $persona retry rc=$rc"
    reap_sweep_ports "$port" "$((port+30))"
  fi
  pkill -f "play.sh baldurs-gate vm2-$persona" 2>/dev/null
  pkill -f "play_party.sh baldurs-gate vm2-$persona" 2>/dev/null
  local sc="qa/ui_playtest_runs/vm2-$persona/score.json"
  if [ -f "$sc" ]; then
    # STRUCTURAL COVERAGE (the owner's "full circle"; pairs with the #961 gate). The persona
    # scorer reads only the player's actions.ndjson — blind to the DM's tool calls + the engine
    # end-state. The sweep KNOWS the persona's Part-B play-state store (play-state/vm2-$persona-b:
    # the campaign snapshot.json is the GROUND TRUTH; dm.combined.jsonl carries the tool counts),
    # so it computes structural_coverage_from_state HERE and MERGES it into score.json (additive —
    # a missing snapshot leaves the score untouched). The shared story_readout helper keeps this
    # block, the readout stamp, and the #961 assertion from drifting.
    local bstore="play-state/vm2-$persona-b"
    SC_STRUCTURAL="$(python3 qa/inject_structural_coverage.py "$sc" "$bstore" 2>/dev/null)"
    cp "$sc" "$RES/score-$persona.json"
    note "  $persona rc=$rc $(python3 -c "import json;d=json.load(open('$sc'));print('sat=%s gaveup=%s crit=%s arc=%s turns=%s'%(d.get('persona_satisfaction'),d.get('gave_up'),d.get('bug_reports_critical'),d.get('completed_intro_flow'),d.get('in_story_turns')))" 2>/dev/null)${SC_STRUCTURAL:+ | $SC_STRUCTURAL}"
    # #1414: auto-persist this persona's row (surface=GUI-headless-proxy) — FAIL LOUD (never
    # `|| echo WARN`; a failed write is a failed run per the Universal Run Contract,
    # docs/OPERATIONS.md "No row = no run"). run_id is keyed on persona+SHA so a re-run of the
    # SAME sweep at the SAME commit replaces this persona's row instead of duplicating, while a
    # new commit gets its own trend row (scores_db.add_run's INSERT OR REPLACE-on-run_id).
    if ! python3 "$ROOT/qa/scores_persist.py" sweep-persona \
        --run-id "vm2-$persona-$SHA" --persona "$persona" --build-sha "$SHA" \
        --score-json "$sc" --source-path "$RES/score-$persona.json"; then
      note "  FATAL: scores_db row write failed for persona=$persona — a failed write is a failed run per the Universal Run Contract (docs/OPERATIONS.md)."
      exit 1
    fi
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
    write_aborted_rri "$RES/RRI.json" "$(cat "$RES/QUOTA_ABORT")"  # #842 Fix B: stamp the ABORTED RRI so no stale RRI.json persists past a canary-abort
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
  write_aborted_rri "$RES/RRI.json" "$(cat "$RES/QUOTA_ABORT")"  # #842 Fix B: stamp the ABORTED RRI so no stale RRI.json persists past a canary-abort
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
  write_aborted_rri "$RES/RRI.json" "$(cat "$RES/QUOTA_ABORT")"  # #842 Fix B: shared ABORTED-RRI writer (same shape the canary-abort path uses)
  note "=== SWEEP COMPLETE (QUOTA-ABORTED) -> $RES ==="; touch "$RES/DONE"; exit 0
fi

# 3) duo (story/mech) + behavioral + audit - run after personas (sequential, cheap-ish)
note "3-lens duo..."
# #842 Fix C (the rc3 stale-score bug): WIPE this run's prior duo artifacts BEFORE the duo runs.
# An aborted/quota'd duo writes NO fresh tolkien/angrydm/latency JSON, so the `[ -f ] && cp` below
# would copy the PREVIOUS run's byte-identical lens scores into THIS sweep's results (rc3 published
# rc2's "story 4.0/mech 3.0" verbatim). Removing both the results copies and run_duo's transcript
# outputs guarantees the cp only fires on CURRENT-run output (a missing file => no stale carry-over).
rm -f "$RES/duo-tolkien.json" "$RES/duo-angrydm.json" "$RES/duo-latency.json" \
      "qa/transcripts/vm2-duo.tolkien.json" "qa/transcripts/vm2-duo.angrydm.json" 2>/dev/null
timeout 3600 bash qa/run_duo.sh vm2-duo baldurs-gate veteran 8 5.00 > "$RES/duo.log" 2>&1
# #842 Fix E (duo half): a session-limit 429 in the DM cold-open makes run_duo log "[duo] QUOTA ABORT"
# and exit rc=2 (no valid scorecard). Treat that exactly like the persona-batch quota abort — write
# QUOTA_ABORT + the ABORTED RRI and STOP, so a quota'd duo can never roll up a junk story/mech score.
if grep -q '\[duo\] QUOTA ABORT' "$RES/duo.log" 2>/dev/null; then
  note "QUOTA ABORT in the duo — claude account session limit ($(quota_reset_hint "$RES/duo.log")). Skipping the duo scores + RRI. INFRA abort, NOT a product result."
  echo "duo $(quota_reset_hint "$RES/duo.log")" > "$RES/QUOTA_ABORT"
  write_aborted_rri "$RES/RRI.json" "$(cat "$RES/QUOTA_ABORT")"
  note "=== SWEEP COMPLETE (QUOTA-ABORTED at duo) -> $RES ==="; touch "$RES/DONE"; exit 0
fi
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

# 3.5) STRUCTURAL-COVERAGE ROLL-UP (the owner's "full circle"; pairs with the #961 gate). Each
# score-$persona.json now carries a structural_coverage block (merged in run_persona). Roll it up
# so the sweep summary SHOWS, across personas, which whole systems the build is exercising vs. dead
# (e.g. "max acts 1/3 · recruit 3/5 · camp 0/5 · quest-resolved 1/5"). REPORT-ONLY (never gates) —
# it feeds the human + Agent-2's RRI rollup; the #961 behavioral gate is what actually caps a run.
note "structural coverage roll-up..."
python3 - "$RES" >> "$LOG" 2>&1 <<'PY' || note "  (structural roll-up skipped — see log)"
import json, sys, glob, os
res = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(res, "score-*.json"))):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    sc = d.get("structural_coverage")
    if isinstance(sc, dict):
        rows.append((os.path.basename(f)[len("score-"):-len(".json")], sc))
if not rows:
    print("[structural] no structural_coverage blocks found in score-*.json")
    raise SystemExit(0)
n = len(rows)
def cnt(k): return sum(1 for _, sc in rows if sc.get(k))
max_acts = max((int(sc.get("acts_reached") or 0) for _, sc in rows), default=0)
print(f"[structural] {n} persona(s) | max acts {max_acts}/3 | "
      f"recruit {cnt('recruited')}/{n} · camp {cnt('camped')}/{n} · "
      f"approval {cnt('approval_moved')}/{n} · quest-resolved {cnt('quest_resolved')}/{n} · "
      f"evolved {cnt('quest_evolved')}/{n} · travel {cnt('traveled')}/{n} · combat {cnt('combat')}/{n}")
for name, sc in rows:
    print(f"[structural]   {name}: {sc.get('summary','')}")
rollup = {"personas": n, "max_acts_reached": max_acts,
          "recruited": cnt('recruited'), "camped": cnt('camped'),
          "approval_moved": cnt('approval_moved'), "quest_resolved": cnt('quest_resolved'),
          "quest_evolved": cnt('quest_evolved'), "traveled": cnt('traveled'),
          "combat": cnt('combat'),
          "per_persona": {name: sc for name, sc in rows}}
json.dump(rollup, open(os.path.join(res, "structural_coverage.json"), "w"), indent=2)
PY

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
