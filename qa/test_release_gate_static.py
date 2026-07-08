import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateStaticContractTests(unittest.TestCase):
    def test_release_gate_passes_expected_personas_to_rri(self):
        source = (ROOT / "qa" / "release_gate.sh").read_text(encoding="utf-8")

        self.assertIn('--expected-personas "$PERSONAS"', source)
        self.assertIn('--handoff-json) HANDOFF_JSON="$2"; shift 2;;', source)
        self.assertIn('--support-preflight-json) SUPPORT_PREFLIGHT_JSON="$2"; shift 2;;', source)
        self.assertIn("[ -s \"$HANDOFF_JSON\" ] || fail", source)
        self.assertIn("[ -s \"$SUPPORT_PREFLIGHT_JSON\" ] || fail", source)
        self.assertIn("RRI_ARGS=(", source)
        self.assertIn('[ -n "$HANDOFF_JSON" ] && RRI_ARGS+=(--handoff-json "$HANDOFF_JSON")', source)
        self.assertIn(
            '[ -n "$SUPPORT_PREFLIGHT_JSON" ] && RRI_ARGS+=(--support-preflight-json "$SUPPORT_PREFLIGHT_JSON")',
            source,
        )
        self.assertIn("MISSING_SCORES", source)
        self.assertIn("missing persona score", source)
        self.assertIn('set +e\npython3 qa/release_readiness.py "${RRI_ARGS[@]}"', source)
        self.assertIn('RRI_RC="${PIPESTATUS[0]}"', source)
        self.assertIn('set -e\n\necho ""', source)
        self.assertIn('exit "$RRI_RC"', source)
        self.assertIn("WORLDOS_ART_REPO_ROOT", source)
        self.assertIn("WORLDOS_ART_REPO_ROOT", source)
        self.assertIn('preflight-only will not kill it', source)
        self.assertIn("missing required command(s)", source)
        self.assertIn("qa/playwright/node_modules/playwright/package.json", source)
        self.assertIn("all orchestrated tools and command deps present", source)

    def test_release_gate_preserves_non_current_app_and_viewer_processes(self):
        source = (ROOT / "qa" / "release_gate.sh").read_text(encoding="utf-8")

        self.assertIn("pid_belongs_to_root", source)
        self.assertIn("port_pids", source)
        self.assertIn("pass --port to use an isolated release-gate range", source)
        self.assertIn("WOS_APP_NO_GLOBAL_KILL=1 WOS_APP_PART=AB", source)
        self.assertIn("WOS_APP_NO_GLOBAL_KILL=1 WOS_APP_PART=B", source)
        self.assertNotIn("xargs kill", source)
        self.assertNotIn("kill -9", source)

    def test_palette_live_requires_six_enabled_actions(self):
        source = (ROOT / "qa" / "release_gate.sh").read_text(encoding="utf-8")

        self.assertIn("n >= 6", source)
        self.assertNotIn("n >= 4", source)

    def test_release_gate_uses_real_duo_prompt_file(self):
        source = (ROOT / "qa" / "release_gate.sh").read_text(encoding="utf-8")

        self.assertIn('DUO_PROMPT="$ROOT/qa/play_player_duo.txt"', source)
        self.assertNotIn('qa/run_duo.sh "${RUNID}-duo" baldurs-gate veteran', source)

    def test_release_gate_stamps_latency_sidecar_into_persona_run_dirs(self):
        # The RRI latency gate was DORMANT: run_duo.sh derives the per-beat ledger into the
        # TRANSCRIPT dir, but release_readiness.read_latency() reads each PERSONA run dir's
        # latency.json sidecar. Lock in the wiring that activates the gate — release_gate.sh must
        # stamp the duo rollup into the run dirs via latency_rollup.py --stamp-into, BEFORE the
        # RRI rollup reads them — so the gate can never silently fall back to a skip again.
        source = (ROOT / "qa" / "release_gate.sh").read_text(encoding="utf-8")

        self.assertIn("qa/latency_rollup.py", source)
        self.assertIn('--run "${RUNID}-duo"', source)
        self.assertIn('--stamp-into "$RUN_DIRS"', source)
        # the stamp must run BEFORE release_readiness reads the per-run sidecars
        self.assertLess(source.index("--stamp-into"), source.index("python3 qa/release_readiness.py"))

    def test_ui_playtest_persists_final_session_surface_before_teardown(self):
        source = (ROOT / "qa" / "ui_playtest_app.sh").read_text(encoding="utf-8")

        self.assertIn("session_surface.final.json", source)
        self.assertIn('"session_surface_path": "session_surface.final.json"', source)
        self.assertIn('chat_lines="$(grep -c . "$b_state/chat.jsonl" 2>/dev/null || true)"', source)

    def test_play_scripts_launch_openworlds_not_legacy_dashboard(self):
        for rel in ("scripts/play_party.sh", "qa/play_human.sh"):
            with self.subTest(rel=rel):
                source = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("http://127.0.0.1:$PORT/openworlds/", source)
                self.assertNotIn("http://127.0.0.1:$PORT/dashboard", source)

    def test_build_launcher_preserves_art_root_override(self):
        source = (ROOT / "script" / "build_and_run.sh").read_text(encoding="utf-8")

        self.assertIn("WORLDOS_REPO_ROOT=\"$ROOT_DIR\"", source)
        self.assertIn("WORLDOS_ART_REPO_ROOT=\"$ART_ROOT\"", source)
        self.assertIn('ART_ROOT="${WORLDOS_ART_REPO_ROOT:-$ROOT_DIR}"', source)
        self.assertIn('ART_ROOT_PLIST="$(plist_escape "$ART_ROOT")"', source)
        self.assertIn("WORLDOS_NO_STOP_EXISTING", source)
        self.assertIn("<key>WorldOSRepoRoot</key>", source)
        self.assertIn("<key>WorldOSArtRepoRoot</key>", source)
        self.assertIn("<key>WorldOSPreferLaunchRoots</key>", source)
        self.assertIn("wait_for_bundle_pid", source)
        self.assertIn("pid_in_list", source)
        self.assertIn('existing_pids="$(bundle_pid | tr', source)
        self.assertIn('pid="$(wait_for_bundle_pid "$existing_pids")"', source)
        self.assertIn('WORLDOS_NO_STOP_EXISTING', source)
        self.assertIn('pkill -f "$ROOT_DIR/viewer/server.py"', source)

    def test_ui_playtest_app_can_preserve_other_worldos_app_instances(self):
        source = (ROOT / "qa" / "ui_playtest_app.sh").read_text(encoding="utf-8")

        self.assertIn("WOS_APP_NO_GLOBAL_KILL", source)
        self.assertIn("app_pid_for_bundle", source)
        self.assertIn("found=0", source)
        self.assertIn('[ "$found" = "1" ] || return 1', source)
        self.assertIn("WORLDOS_NO_STOP_EXISTING", source)
        self.assertIn("WORLDOS_PREFER_LAUNCH_ROOTS=1", source)

    def test_ui_playtest_app_exit_requires_requested_parts_to_pass(self):
        source = (ROOT / "qa" / "ui_playtest_app.sh").read_text(encoding="utf-8")

        self.assertIn('PART_B_SCORE_PASS="false"', source)
        self.assertRegex(
            source,
            re.compile(r'"part_b": \{"persona_loop": b_res,\s+"score_pass": b_score_pass == "true"', re.MULTILINE),
        )
        self.assertIn('[ "$player_rc" -eq 0 ] && [ -f "$RUNDIR/score.json" ]', source)
        self.assertIn('case "$PART" in', source)
        self.assertIn('[ "$PART_A_RESULT" = "PASS" ] || EXIT_OK=0', source)
        self.assertIn('[ "$PART_B_RESULT" = "PASS" ] && [ "$PART_B_SCORE_PASS" = "true" ] || EXIT_OK=0', source)

    def test_sweep_v2_runs_support_preflight_before_personas_and_rolls_it_up(self):
        # #730: the VM sweep never produced the support-preflight artifact, so split
        # VM+Mac rollups always carried the support_preflight evidence gap.
        source = (ROOT / "qa" / "vm" / "sweep_v2.sh").read_text(encoding="utf-8")

        self.assertIn("qa/support_vm_preflight.py", source)
        self.assertIn('--expected-sha "$SHA"', source)
        self.assertIn("--private-art-mode required", source)
        self.assertIn("--no-fail", source)
        self.assertIn('"$RES/support_preflight.json"', source)
        self.assertIn('--support-preflight-json "$RES/support_preflight.json"', source)
        # The preflight must run BEFORE the personas (canary included) so a blocked
        # host is recorded before any model spend.
        self.assertLess(source.index("qa/support_vm_preflight.py"), source.index("CANARY: newbie"))
        # The rollup invocation carries the artifact.
        self.assertLess(source.index("qa/release_readiness.py"), source.index('--support-preflight-json "$RES/support_preflight.json"'))
        # ui_audit gets the auditstate dir so its --ui-gate seed step can act on it.
        self.assertIn("WORLDOS_STATE_DIR=/root/worldos-qa/auditstate timeout 600 bash qa/ui_audit_health.sh", source)

    def test_ui_audit_health_axe_missing_driver_is_hard_fail(self):
        # rc1 was misattributed as FAIL(axe) when axe never ran: a missing
        # browser-driver-manager silently WARN-skipped the sweep. When --axe is
        # requested, a missing driver must FAIL loudly with the install command.
        source = (ROOT / "qa" / "ui_audit_health.sh").read_text(encoding="utf-8")

        self.assertIn('fail "--axe requested but npx is not on PATH', source)
        self.assertIn('fail "--axe requested but browser-driver-manager', source)
        self.assertIn("npx --yes browser-driver-manager install chrome=", source)
        self.assertNotIn('warn "browser-driver-manager not installed', source)
        self.assertNotIn('warn "npx not on PATH; skipping --axe"', source)

    def test_ui_audit_health_seeds_resumable_campaign_for_play_reachable(self):
        # The play_reachable ui-gate probe can never pass against an EMPTY auditstate
        # (no Resume/Continue CTA exists without a resumable campaign) — seed one
        # minimal campaign first, guarded so an already-seeded state is untouched.
        source = (ROOT / "qa" / "ui_audit_health.sh").read_text(encoding="utf-8")

        self.assertIn('AUDIT_STATE_DIR="${WORLDOS_STATE_DIR:-}"', source)
        self.assertIn('campaigns/*/snapshot.json', source)
        self.assertIn('server.start_world(', source)
        self.assertIn("uv run --directory servers/engine python", source)
        # Seeding must happen before the probe runs.
        self.assertLess(source.index("AUDIT_STATE_DIR"), source.index("node qa/ui_gate_probe.js"))

    def test_solo_play_contract_does_not_silently_recruit_companion(self):
        play = (ROOT / "scripts" / "play.sh").read_text(encoding="utf-8")
        party = (ROOT / "scripts" / "play_party.sh").read_text(encoding="utf-8")

        self.assertGreaterEqual(play.count("This is a SOLO session: the player begins ALONE"), 2)
        self.assertIn("Do NOT recruit a companion at cold-open", play)
        self.assertIn("party at the end of this opening turn is the player alone", play)
        self.assertIn('if [ -z "${COMPANION_SPEC//[[:space:]]/}" ]; then', party)
        self.assertIn('ARGS=()', party)
        self.assertIn('exec "$ROOT/scripts/play.sh" "${ARGS[@]}"', party)


class QuotaCircuitBreakerStaticContractTests(unittest.TestCase):
    """#842: a 429 (account session limit) must yield a QUOTA abort, never a junk RRI/score,
    and a quota-aborted sweep must never republish stale evidence. Grep-the-shell-source contracts
    (mirrors the release-gate static style — no live runs)."""

    def test_sweep_cleanup_wipes_stale_rri_json(self):
        # Fix A: the sweep-start cleanup rm must include RRI.json so a quota abort before a fresh
        # rollup can never leave the PREVIOUS run's RRI.json in place (the rc3 stale-RRI bug).
        source = (ROOT / "qa" / "vm" / "sweep_v2.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "$RES/DONE" "$RES/CANARY_FAIL" "$RES/QUOTA_ABORT" "$RES/RRI.json"', source)

    def test_sweep_canary_abort_writes_aborted_rri(self):
        # Fix B: the canary-abort path must ALSO write the {"status":"ABORTED",…} RRI.json (it
        # previously touched DONE + exited leaving any stale RRI.json behind). The shared
        # write_aborted_rri helper carries the ABORTED status; the canary-abort path must call it.
        source = (ROOT / "qa" / "vm" / "sweep_v2.sh").read_text(encoding="utf-8")
        self.assertIn("write_aborted_rri()", source)
        self.assertIn('"status": "ABORTED"', source)
        self.assertIn('"abort_reason": "quota_session_limit"', source)
        # #842 review (load-bearing): evidence_audit.py keys on `aborted:true` + `abort_detail`
        # (NOT `detail`). Without them the ABORTED RRI reads as RELEASE_READY — the exact masking
        # #842 prevents. Lock the contract statically + functionally (below).
        self.assertIn('"aborted": True', source)
        self.assertIn('"abort_detail"', source)
        self.assertNotIn('"detail": detail', source)  # the old wrong key must be gone
        # the canary-abort branch (QUOTA ABORT at the canary) must call the writer before exiting.
        canary_idx = source.index("QUOTA ABORT at the canary")
        # the next write_aborted_rri call after the canary-abort message proves the path stamps it.
        self.assertIn("write_aborted_rri", source[canary_idx:canary_idx + 600])

    def test_aborted_rri_shape_reads_as_aborted_in_evidence_audit(self):
        # #842 review (the end-to-end contract the static greps back): the ABORTED RRI the sweep
        # writes MUST be classified as aborted (NOT release-ready) by qa/evidence_audit.py.
        # Reproduce the helper's exact shape and assert evidence_audit does not call it ready.
        import json, subprocess, tempfile, os
        rri = {"status": "ABORTED", "aborted": True, "abort_reason": "quota_session_limit",
               "abort_detail": "newbie — quota resets ~3h", "build_sha": "deadbeef",
               "release_ready": False, "note": "infra abort, not a product RRI"}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(rri, f)
            out = subprocess.run(
                ["python3", str(ROOT / "qa" / "evidence_audit.py"), "--rri", path],
                capture_output=True, text=True, timeout=30)
            combined = (out.stdout + out.stderr).upper()
            self.assertNotIn("RELEASE_READY", combined,
                             f"ABORTED RRI mis-classified as release-ready: {combined}")
            self.assertIn("ABORT", combined, f"evidence_audit did not flag the abort: {combined}")
        finally:
            os.unlink(path)

    def test_sweep_wipes_stale_duo_artifacts_before_duo_call(self):
        # Fix C: the duo-artifact rm must PRECEDE the run_duo.sh call so the `[ -f ] && cp` below
        # can only copy CURRENT-run output (rc3 republished rc2's byte-identical lens scores).
        source = (ROOT / "qa" / "vm" / "sweep_v2.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "$RES/duo-tolkien.json" "$RES/duo-angrydm.json" "$RES/duo-latency.json"', source)
        self.assertIn('"qa/transcripts/vm2-duo.tolkien.json" "qa/transcripts/vm2-duo.angrydm.json"', source)
        # the wipe must come before the run_duo invocation.
        self.assertLess(
            source.index('rm -f "$RES/duo-tolkien.json"'),
            source.index("bash qa/run_duo.sh vm2-duo baldurs-gate veteran"),
        )

    def test_ui_playtest_app_has_quota_exhausted_bucket(self):
        # Fix D: quota_exhausted must be a known failure bucket AND the poll loop must detect a
        # 429 in backend.log, drop the QUOTA_EXHAUSTED sentinel, and bucket it as quota_exhausted
        # (not the generic backend_not_ready / no_actor mis-bucketing).
        source = (ROOT / "qa" / "ui_playtest_app.sh").read_text(encoding="utf-8")
        self.assertIn('"quota_exhausted"', source)
        self.assertIn('APP_FAILURE_BUCKETS_JSON=', source)
        # the buckets JSON literal carries quota_exhausted.
        buckets_line = next(
            l for l in source.splitlines() if l.startswith("APP_FAILURE_BUCKETS_JSON=")
        )
        self.assertIn("quota_exhausted", buckets_line)
        # the poll loop drops the sentinel and the readiness-failure path buckets it.
        self.assertIn('touch "$RUNDIR/QUOTA_EXHAUSTED"', source)
        self.assertIn('[ -f "$RUNDIR/QUOTA_EXHAUSTED" ]', source)
        self.assertIn('PART_B_RESULT="quota_exhausted"', source)

    def test_score_sh_has_429_fast_fail_arm(self):
        # Fix F: score.sh must have a 429 fast-fail arm (NO 3 retries) that writes the quota
        # sentinel and exits rc=2.
        source = (ROOT / "qa" / "score.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$api_err" = "429" ]', source)
        self.assertIn('printf \'{"quota_exhausted":true,"api_error_status":429}\\n\' > "$OUT"', source)
        self.assertIn("exit 2", source)
        # the fast-fail arm must sit BEFORE the generic retry-loop tail (the empty/api_err branches)
        # so a 429 short-circuits instead of burning the 3 attempts.
        self.assertLess(source.index('[ "$api_err" = "429" ]'), source.index('if [ ! -s "$RAW" ]; then'))

    def test_score_sh_has_auth_expiry_fast_fail_arm(self):
        # #1404: score.sh must (1) PROACTIVELY detect an expired DERIVED credential (compare the
        # keychain/creds-file expiresAt to now) and fail fast with a distinct {error:scorer_auth_expired}
        # sentinel + exit rc=2, and (2) fail fast on a live 401 instead of burning the 3 retries.
        source = (ROOT / "qa" / "score.sh").read_text(encoding="utf-8")
        # proactive pre-check arm
        self.assertIn('"$_scorer_tok_exp" =~ ^[0-9]+$', source)
        self.assertIn('"error":"scorer_auth_expired"', source)
        # live-call 401 arm
        self.assertIn('[ "$api_err" = "401" ]', source)
        # both auth arms must sit BEFORE the generic retry-loop tail so an auth failure short-circuits.
        self.assertLess(source.index('[ "$api_err" = "401" ]'), source.index('if [ ! -s "$RAW" ]; then'))
        # the proactive pre-check must run BEFORE the live claude call (it gates on the derived token).
        self.assertLess(source.index('"error":"scorer_auth_expired"'), source.index("timeout \"${WORLDOS_SCORE_TIMEOUT:-600}\" claude -p"))
        # the lens validator must recognize the new sentinel as a sentinel (not a numeric score).
        lib = (ROOT / "qa" / "lib_beat_driver.sh").read_text(encoding="utf-8")
        self.assertIn('"scorer_failed", "scorer_auth_expired"', lib)

    def test_run_duo_checks_for_quota_abort_before_scoring(self):
        # Fix E + Fix F (caller half): run_duo.sh must (1) detect a DM cold-open 429 and emit the
        # "[duo] QUOTA ABORT" marker + exit EX_TEMPFAIL BEFORE the empty-reply abort, and (2) treat the
        # score.sh quota sentinel as a quota abort (not a valid scorecard) before the behavioral gate.
        source = (ROOT / "qa" / "run_duo.sh").read_text(encoding="utf-8")
        self.assertIn("[duo] QUOTA ABORT", source)
        self.assertIn("session limit|HTTP 429|hit your (session|usage) limit", source)
        self.assertIn(".quota_exhausted == true", source)
        self.assertIn('ASSERT_BEHAVIORAL_SCRIPT="$(worldos_env ASSERT_BEHAVIORAL_SCRIPT qa/assert_behavioral.py)"', source)
        # the cold-open quota check must precede the empty-reply abort (DM produced no opening).
        self.assertLess(
            source.index("[duo] QUOTA ABORT"),
            source.index("DM produced no opening"),
        )
        # the scorer-sentinel quota check must precede the behavioral gate (no gating a quota corpse).
        self.assertLess(
            source.index(".quota_exhausted == true"),
            source.index('python3 "$ASSERT_BEHAVIORAL_SCRIPT"'),
        )


if __name__ == "__main__":
    unittest.main()
