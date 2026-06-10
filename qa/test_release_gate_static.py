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
        self.assertIn("CLAWDND_ART_REPO_ROOT", source)
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
        self.assertIn('ART_ROOT="${WORLDOS_ART_REPO_ROOT:-${CLAWDND_ART_REPO_ROOT:-$ROOT_DIR}}"', source)
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

        self.assertIn('AUDIT_STATE_DIR="${WORLDOS_STATE_DIR:-${CLAWDND_STATE_DIR:-}}"', source)
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


if __name__ == "__main__":
    unittest.main()
