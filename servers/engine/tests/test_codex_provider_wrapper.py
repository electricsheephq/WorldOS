"""Codex provider wrapper contract tests.

The wrapper is allowed to create provider-local logs/config and a move sink. It
must fail closed on missing launch env and its smoke mode must not start Codex or
run narrative QA.
"""

import json
import os
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "play_codex_actor.sh"
DM_SCRIPT = ROOT / "scripts" / "play_codex_dm.sh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", ""),
        "CLAWDND_PROVIDER": "codex",
        "CLAWDND_WORLD": "baldurs-gate",
        "CLAWDND_RUN_ID": "codex-smoke",
        "CLAWDND_PLAY_PORT": "8765",
        "CLAWDND_PLAY_BUDGET": "0.05",
        "CLAWDND_PLAY_SESSION_BUDGET": "0.25",
        "CLAWDND_PLAY_MAX_TURNS": "1",
        "CLAWDND_PLAY_COMPANIONS": "",
        "CLAWDND_STATE_ROOT": str(tmp_path),
        "WORLDOS_PROVIDER_STOP_GRACE_SECONDS": "0",
    }
    env.update(overrides)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def _run_dm(
    args: list[str],
    env: dict[str, str],
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    command = ["/bin/bash", str(DM_SCRIPT), *args]
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def test_codex_wrapper_fails_closed_without_required_env(tmp_path):
    result = _run(["--dry-run"], {"PATH": os.environ.get("PATH", ""), "TMPDIR": str(tmp_path)})

    assert result.returncode != 0
    assert "missing required env" in result.stderr


def test_codex_wrapper_rejects_non_codex_provider(tmp_path):
    result = _run(["--dry-run"], _env(tmp_path, CLAWDND_PROVIDER="openclaw"))

    assert result.returncode != 0
    assert "CLAWDND_PROVIDER must be codex" in result.stderr


def test_codex_wrapper_rejects_unknown_options_before_run_mode(tmp_path):
    result = _run(["--dryrun"], _env(tmp_path))

    assert result.returncode != 0
    assert "unknown option: --dryrun" in result.stderr


def test_codex_wrapper_smoke_generates_player_facade_config_only(tmp_path):
    result = _run(["--smoke"], _env(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["ok"] is True
    assert summary["mode"] == "smoke"
    assert summary["provider"] == "codex"

    config = Path(summary["config"]).read_text(encoding="utf-8")
    assert "[mcp_servers.clawdnd-player]" in config
    assert "player_server.py" in config
    assert "CLAWDND_PLAYER_MOVES" in config
    assert 'default_tools_approval_mode = "approve"' in config
    assert "servers/engine/server.py" not in config
    assert "qa/" not in config

    moves = Path(summary["moves"])
    assert moves.exists()
    assert moves.read_text(encoding="utf-8") == ""


def test_codex_wrapper_dry_run_uses_play_state_layout(tmp_path):
    result = _run(["--dry-run"], _env(tmp_path, CLAWDND_RUN_ID="layout-check"))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["config"].endswith("/layout-check/codex-provider/codex-player.toml")
    assert summary["moves"].endswith("/layout-check/player_moves.jsonl")


def test_codex_dm_wrapper_dry_run_generates_dm_contract(tmp_path):
    result = _run_dm(["--dry-run"], _env(tmp_path, CLAWDND_RUN_ID="dm-layout"))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["ok"] is True
    assert summary["mode"] == "dry-run"
    assert summary["provider"] == "codex"
    assert summary["role"] == "dm"
    assert summary["viewer_url"].endswith(":8765/openworlds/")
    assert summary["model"] == "gpt-5.5"
    assert summary["wrapper"] == "scripts/play_codex_dm.sh"
    assert summary["lean_beats"] is True
    assert summary["lean_tail"] == 8
    assert summary["turn_cap"] == 1
    assert summary["sha"]
    assert summary["config"].endswith("/dm-layout/codex-provider/codex-dm.toml")
    assert summary["moves"].endswith("/dm-layout/player_moves.jsonl")
    assert summary["chat"].endswith("/dm-layout/chat.jsonl")

    config = Path(summary["config"]).read_text(encoding="utf-8")
    assert "[mcp_servers.clawdnd-engine]" in config
    assert "[mcp_servers.clawdnd-rules]" in config
    assert "[mcp_servers.clawdnd-voice]" in config
    assert "/servers/engine" in config
    assert "/servers/rules" in config
    assert "/servers/voice" in config
    assert '"python"' in config
    assert '"server.py"' in config
    assert config.count('default_tools_approval_mode = "approve"') == 3
    assert "player_server.py" not in config
    assert "CLAWDND_STATE_DIR" in config

    assert Path(summary["moves"]).exists()
    assert Path(summary["chat"]).exists()


def test_codex_dm_wrapper_dry_run_surfaces_native_selected_hero(tmp_path):
    hero = json.dumps({"canon": True, "name": "Abby"})
    result = _run_dm(["--dry-run"], _env(tmp_path, CLAWDND_PLAY_HERO=hero))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["hero"] == {"canon": True, "name": "Abby"}


def test_codex_dm_wrapper_dry_run_surfaces_origin_template_hero(tmp_path):
    hero = json.dumps({"origin": "template:rolan-evoker"})
    result = _run_dm(["--dry-run"], _env(tmp_path, CLAWDND_PLAY_HERO=hero))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["hero"] == {"origin": "template:rolan-evoker"}


def test_codex_dm_wrapper_dry_run_surfaces_fallback_origin_template_hero(tmp_path):
    result = _run_dm(
        ["--dry-run"],
        _env(tmp_path, CLAWDND_PLAY_CANON_HERO="template:rolan-evoker"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["hero"] == {"origin": "template:rolan-evoker"}


def test_codex_dm_wrapper_seed_smoke_surfaces_origin_template_sheet(tmp_path):
    result = _run_dm(
        ["--seed-smoke"],
        _env(tmp_path, CLAWDND_PLAY_CANON_HERO="template:rolan-evoker"),
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["ok"] is True
    assert summary["mode"] == "seed-smoke"
    seed = summary["seed"]
    assert seed["seed_source"] == "origin"
    assert seed["origin"] == "template:rolan-evoker"
    pc = seed["pc"]
    assert pc["class"] == "Wizard"
    assert pc["level"] == 3
    # #624: the origin template's loose "Evocation" is normalized to the canonical SRD
    # subclass "Evoker" (and Rolan now actually gains its level-3 features at seed time).
    assert pc["subclass"] == "Evoker"
    assert "Magic Missile" in pc["spells"]
    assert "Mage Armor" in pc["spells"]


def test_codex_dm_wrapper_honors_native_selected_hero():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "CLAWDND_PLAY_HERO" in source
    assert "CLAWDND_PLAY_CANON_HERO" in source
    assert "--seed-smoke" in source
    assert "origin_spec" in source
    assert 'server.start_character(camp, origin=origin_spec, name=name_override)' in source
    assert "server.get_character(camp, pc_id)" in source
    assert 'server.load_canon_character(camp, name, kind="player", add_to_party=True)' in source
    assert "Native-selected hero already seated" in source
    assert "HERO_PC_SUBCLASS" in source
    assert "HERO_PC_SPELLS" in source
    assert '"fixture": fixture' in source
    assert "known/prepared spells from engine sheet" in source
    assert "do NOT call start_world, start_session, start_character, or load_canon_character" in source
    assert "seeded solo player" in source


def test_codex_dm_wrapper_forbids_null_speaker_arguments():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "LOG_EVENT_TOOL_RULE=" in source
    assert "LIVE_PROGRESS_LOG_RULE=" in source
    assert "LIVE_DIALOGUE_LOG_RULE=" in source
    assert "OPENING_LOG_EVENT_RULE=" in source
    assert "omit the speaker argument entirely" in source
    assert "Never pass JSON null for speaker" in source
    assert "visible story progress while your turn is still running" in source
    assert 'do not call log_event(kind=\\"dialogue\\")' in source
    assert "without hiding dialogue from the player" in source
    assert "Do not log the full opening this way" in source
    assert "log_engine_narration" in source
    assert '[ -n "${campaign_id//[[:space:]]/}" ] || return 1' in source
    assert '[ -n "${text//[[:space:]]/}" ] || return 1' in source
    assert source.count("$LOG_EVENT_TOOL_RULE") >= 3
    assert source.count("$LIVE_PROGRESS_LOG_RULE") == 3
    assert source.count("$LIVE_DIALOGUE_LOG_RULE") == 3
    assert 'record_dm_reply "$ACTIVE_CAMPAIGN_ID" "$REPLY" "move"' in source
    assert '"engine_logged":true' in source
    assert "invalid chatlog extra_json" in source


def test_codex_dm_wrapper_records_engine_narration_before_chat_tail():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    helper_start = source.index("record_dm_reply() {")
    helper_end = source.index("codex_dm_turn() {", helper_start)
    helper_block = source[helper_start:helper_end]
    assert helper_block.index('log_engine_narration "$campaign_id" "$text"') < helper_block.index('chatlog dm "$text"')
    assert 'chatlog dm "$text" \'{"engine_logged":true}\'' in helper_block

    opening_start = source.index('if ! OPENING="$(codex_dm_turn "$OPENING_PROMPT")"')
    opening_end = source.index('CAMPAIGN_TOOL_HINT="$(campaign_tool_hint "$ACTIVE_CAMPAIGN_ID")"', opening_start)
    opening_block = source[opening_start:opening_end]
    assert 'record_dm_reply "$ACTIVE_CAMPAIGN_ID" "$OPENING" "opening"' in opening_block

    move_start = source.index('if ! REPLY="$(codex_dm_turn')
    move_end = source.index('DM_TURNS=$((DM_TURNS + 1))', move_start)
    move_block = source[move_start:move_end]
    assert 'record_dm_reply "$ACTIVE_CAMPAIGN_ID" "$REPLY" "move"' in move_block


def test_codex_dm_wrapper_prompts_use_engine_state_discovery():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "discover_active_campaign_id()" in source
    assert "Live campaign_id:" in source
    assert "Do not use shell commands, rg, find" in source
    assert "CAMPAIGN_TOOL_HINT" in source


def test_codex_dm_wrapper_honors_lean_reground_contract():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert 'CLAWDND_LEAN_BEATS="${CLAWDND_LEAN_BEATS:-1}"' in source
    assert 'CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"' in source
    assert "CLAWDND_LEAN_TAIL must be an integer when CLAWDND_LEAN_BEATS=1" in source
    assert "codex_lean_reground_rule()" in source
    assert 'scene_context(campaign_id="%s", recent_narration=%s)' in source
    assert "each Codex provider turn is a fresh invocation" in source
    assert "not from replaying chat history or reading files" in source
    assert "CODEX_LEAN_REGROUND_RULE" in source
    assert 'lean_beats=$CLAWDND_LEAN_BEATS recent_narration=$CLAWDND_LEAN_TAIL' in source

    start = source.index("You are the Dungeon Master mid-session")
    move_prompt = source[start : source.index("Player move:", start)]
    assert "$CAMPAIGN_TOOL_HINT" in move_prompt
    assert "$CODEX_LEAN_REGROUND_RULE" in move_prompt


def test_codex_dm_wrapper_prompts_are_self_contained_for_live_app_turns():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "DM_CONTRACT_RULE" in source
    assert "DM_VOICE_RULE" in source
    assert "Self-contained DM contract" in source
    assert "Do not read skill files" in source
    assert "~/.codex skills" in source
    assert "warm, fair, generous storyteller voice" in source
    assert "never invent dice, rules outcomes, or campaign state" in source
    assert "Use clawdnd-engine as the sole writer of campaign state" in source
    assert "Final output must be 2nd-person player-facing narration" in source
    assert "skills/dungeon-master/SKILL.md" not in source
    assert "/Users/lume/.codex/skills/dungeon-master" not in source
    assert source.count("$DM_CONTRACT_RULE") == 3
    assert source.count("$DM_VOICE_RULE") == 3


def test_codex_dm_wrapper_forbids_unconfigured_solo_companions():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "COMPANION_TOOL_RULE" in source
    assert "this is a solo provider launch" in source
    assert 'Do not call load_canon_character with kind=\\"companion\\"' in source
    assert "only add companions named by CLAWDND_PLAY_COMPANIONS" in source


def test_codex_dm_wrapper_constrains_startup_roster_mutation():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "STARTUP_MUTATION_RULE" in source
    assert "the wrapper has already seated the one player" in source
    assert "Before the first player-facing narration" in source
    assert "load_canon_character, create_character, or recruit_companion" in source
    assert "do not call start_world, start_session, start_character, load_canon_character" in source
    assert source.count("$STARTUP_MUTATION_RULE") == 2


def test_codex_dm_wrapper_requires_tracked_social_targets():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "SOCIAL_CHECK_TARGET_RULE" in source
    assert "call social_check only when scene_context already shows a real tracked npc_id" in source
    assert "Do not call load_canon_character or create_character solely to manufacture" in source
    assert "do not use persuasion, deception, intimidation" in source
    assert "Use a non-attitude skill_check such as investigation or perception" in source
    assert source.count("$SOCIAL_CHECK_TARGET_RULE") == 3


def test_codex_dm_wrapper_avoids_noisy_provider_tool_retries():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    assert "RULES_LOOKUP_RULE" in source
    assert "do not call lookup_class" in source
    assert "PARLEY_TOOL_RULE" in source
    assert "pass an explicit skills array" in source
    assert "Do not rely on include_alignment" in source
    assert "OPENING_PERSIST_BEAT_RULE" in source
    assert "MOVE_PERSIST_BEAT_RULE" in source
    assert "REWARD_MUTATION_RULE" in source
    assert "Do not call award_xp" in source
    assert "do not call persist_beat during the opening turn" in source
    assert "This is a post-move turn: at least one real player move has been accepted" in source
    assert "each memory must be an object with character_id and fact fields" in source
    assert source.count("$RULES_LOOKUP_RULE") == 3
    assert source.count("$PARLEY_TOOL_RULE") == 3
    assert source.count("$REWARD_MUTATION_RULE") == 3
    assert source.count("$OPENING_PERSIST_BEAT_RULE") == 2
    assert source.count("$MOVE_PERSIST_BEAT_RULE") == 1


def test_codex_dm_wrapper_move_prompt_does_not_restate_opening_persist_ban():
    source = DM_SCRIPT.read_text(encoding="utf-8")

    start = source.index("You are the Dungeon Master mid-session")
    move_prompt = source[start : source.index("Player move:", start)]
    assert "$MOVE_PERSIST_BEAT_RULE" in move_prompt
    assert "$OPENING_PERSIST_BEAT_RULE" not in move_prompt
    assert "do not call persist_beat during the opening turn" not in move_prompt


def test_codex_dm_wrapper_run_pins_supported_default_model_with_fake_codex(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message)
      last="$2"
      shift 2
      ;;
    --model)
      [ "$2" = "gpt-5.5" ] || {
        echo "unexpected model: $2" >&2
        exit 7
      }
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
cat >/dev/null
printf 'Opening narration from fake Codex.' > "$last"
printf '{"type":"result","result":"Opening narration from fake Codex."}\n'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="fake-codex-run",
        CLAWDND_PLAY_PORT="8797",
        CLAWDND_PLAY_HERO=json.dumps({"canon": True, "name": "Abby"}),
    )

    result = _run_dm([], env, timeout=20)

    assert result.returncode == 0, result.stdout + result.stderr
    chat = tmp_path / "fake-codex-run" / "chat.jsonl"
    assert "Opening narration from fake Codex." in chat.read_text(encoding="utf-8")


def test_codex_dm_wrapper_can_delegate_to_cli_default_with_fake_codex(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message)
      last="$2"
      shift 2
      ;;
    --model)
      echo "unexpected model arg" >&2
      exit 7
      ;;
    *)
      shift
      ;;
  esac
done
cat >/dev/null
printf 'Opening narration from fake Codex.' > "$last"
printf '{"type":"result","result":"Opening narration from fake Codex."}\n'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="fake-codex-cli-default",
        CLAWDND_PLAY_PORT="8799",
        WORLDOS_CODEX_MODEL="auto",
        CLAWDND_PLAY_HERO=json.dumps({"canon": True, "name": "Abby"}),
    )

    result = _run_dm([], env, timeout=20)

    assert result.returncode == 0, result.stdout + result.stderr
    chat = tmp_path / "fake-codex-cli-default" / "chat.jsonl"
    assert "Opening narration from fake Codex." in chat.read_text(encoding="utf-8")


def test_codex_dm_wrapper_processes_moves_submitted_during_opening(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message)
      last="$2"
      shift 2
      ;;
    *)
      shift
      ;;
	  esac
	done
	prompt="$(cat)"
	marker="$CLAWDND_STATE_DIR/.fake-opening-seen"
	if [ ! -f "$marker" ]; then
	  touch "$marker"
	  mkdir -p "$CLAWDND_STATE_DIR/campaigns/camp_fake"
	  printf '{"id":"camp_fake","active_session_id":"session_fake","characters":{"pc":{"kind":"player"}}}' > "$CLAWDND_STATE_DIR/campaigns/camp_fake/snapshot.json"
	  printf '{"role":"player","kind":"do","text":"queued during opening"}\\n' >> "$CLAWDND_STATE_DIR/player_moves.jsonl"
	  printf 'Opening narration from fake Codex.' > "$last"
	  printf '{"type":"result","result":"Opening narration from fake Codex."}\\n'
	else
	  printf '%s' "$prompt" | grep -q 'Live campaign_id: "camp_' || {
	    echo "missing live campaign hint" >&2
	    exit 8
	  }
	  printf '%s' "$prompt" | grep -q 'Do not use shell commands, rg, find' || {
	    echo "missing no-shell state discovery rule" >&2
	    exit 9
	  }
	  printf 'Second turn response from fake Codex.' > "$last"
	  printf '{"type":"result","result":"Second turn response from fake Codex."}\\n'
	fi
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="queued-opening-move",
        CLAWDND_PLAY_PORT="8798",
        CLAWDND_PLAY_MAX_TURNS="2",
    )

    result = _run_dm([], env, timeout=20)

    assert result.returncode == 0, result.stdout + result.stderr
    chat = (tmp_path / "queued-opening-move" / "chat.jsonl").read_text(encoding="utf-8")
    assert "Opening narration from fake Codex." in chat
    assert "[do] queued during opening" in chat
    assert "Second turn response from fake Codex." in chat


def test_native_codex_provider_defaults_to_dm_wrapper():
    source = (ROOT / "macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift").read_text(
        encoding="utf-8"
    )

    assert "scripts/play_codex_dm.sh" in source
    assert "scripts/play_codex_actor.sh" in source
    assert source.index("scripts/play_codex_dm.sh") < source.index("scripts/play_codex_actor.sh")


def test_native_codex_provider_passes_selected_hero_to_wrapper():
    source = (ROOT / "macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift").read_text(
        encoding="utf-8"
    )

    assert 'environment["CLAWDND_PLAY_HERO"] = trimmedHero' in source
    assert "hero: hero," in source


def test_native_codex_provider_custom_command_does_not_require_default_wrapper():
    source = (ROOT / "macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift").read_text(
        encoding="utf-8"
    )

    assert "if configuredCommand.isEmpty" in source
    assert "detectedPath: configuredCommand.isEmpty ? wrapper.path : configuredCommand" in source


def test_codex_wrappers_match_current_cli_flags():
    for script in (SCRIPT, DM_SCRIPT):
        source = script.read_text(encoding="utf-8")
        assert "--ask-for-approval" not in source
        assert "--sandbox read-only" in source
        assert 'default_tools_approval_mode=\\"approve\\"' in source
        assert "gpt-5.1-codex-max" not in source


# --- F12-9: codex DM wrapper hardening (timeout + retry, EXIT-failed status, budget enforcement) ----

def test_codex_dm_wrapper_bounds_codex_exec_with_one_retry():
    """The codex turn must be worldos_timeout-bounded with ONE session-safe retry (was unbounded)."""
    source = DM_SCRIPT.read_text(encoding="utf-8")
    assert "worldos_timeout()" in source                 # the inline F12-8 shim is present
    assert "WORLDOS_CODEX_TURN_TIMEOUT" in source         # the codex turn deadline knob
    assert "worldos_timeout" in source and "codex exec" in source
    # the shim wraps the codex invocation
    exec_start = source.index("_codex_exec_once() {")
    exec_block = source[exec_start : source.index("\n}", exec_start)]
    assert "worldos_timeout" in exec_block
    assert "codex exec" in exec_block
    # ONE retry, documented as session-safe (stateless codex turns)
    turn_start = source.index("codex_dm_turn() {")
    turn_block = source[turn_start : source.index("LOG_EVENT_TOOL_RULE=", turn_start)]
    assert turn_block.count("_codex_exec_once") >= 2      # attempt 1 + the retry
    assert "retrying once" in turn_block
    assert "stateless" in turn_block


def test_codex_dm_wrapper_marks_failed_on_abnormal_exit():
    """The EXIT/INT/TERM trap must stamp provider_status 'failed' on a crash (was stuck 'running')."""
    source = DM_SCRIPT.read_text(encoding="utf-8")
    cleanup_start = source.index("_cleanup() {")
    cleanup_block = source[cleanup_start : source.index("trap _cleanup EXIT", cleanup_start)]
    assert 'write_provider_status "failed"' in cleanup_block
    assert "PROVIDER_STOPPED_CLEANLY" in cleanup_block
    # a clean turn-cap / budget stop sets the flag so the trap does NOT relabel it
    assert source.count("PROVIDER_STOPPED_CLEANLY=1") >= 2


def test_codex_dm_wrapper_enforces_session_budget():
    """The budget envs were validated then never used — now the session budget is a hard stop."""
    source = DM_SCRIPT.read_text(encoding="utf-8")
    assert "codex_session_spend_usd()" in source
    assert "enforce_session_budget()" in source
    assert "WORLDOS_CODEX_USD_PER_MTOK" in source
    assert 'write_provider_status "exhausted"' in source
    assert "CLAWDND_PLAY_SESSION_BUDGET" in source
    # the spend check actually gates the loop (sets BUDGET_EXCEEDED, honored at the loop top)
    assert "BUDGET_EXCEEDED" in source
    # checked AFTER turns (opening + each move), not on every idle poll
    assert source.count("enforce_session_budget") >= 3


def test_codex_dm_wrapper_run_marks_failed_when_codex_crashes(tmp_path):
    """A codex that always exits nonzero (after the retry) must end with provider_status 'failed'."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env bash
# Consume stdin, write NOTHING to --output-last-message, exit nonzero — every attempt fails.
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message) last="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat >/dev/null
exit 1
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="codex-crash",
        CLAWDND_PLAY_PORT="8801",
        CLAWDND_PLAY_HERO=json.dumps({"canon": True, "name": "Abby"}),
    )
    result = _run_dm([], env, timeout=30)

    # The wrapper `fail`s the opening turn (rc=2), and the EXIT trap stamps the sidecar "failed".
    assert result.returncode != 0, result.stdout + result.stderr
    sidecar = tmp_path / "codex-crash" / "provider_status.json"
    assert sidecar.exists(), result.stdout + result.stderr
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    # 'failed' is in the {stopped,failed,exhausted} set the viewer buckets as no_provider.
    assert payload["status"] in {"stopped", "failed", "exhausted"}


def test_codex_dm_wrapper_run_retries_a_transient_failure(tmp_path):
    """A codex that fails the FIRST attempt then succeeds must survive (the ONE retry recovers it)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    # Fail once per logical turn, then succeed: a per-turn marker keyed off the prompt's turn nonce
    # is overkill — instead use a global attempt counter file; the opening needs attempt 2 to pass.
    fake_codex.write_text(
        """#!/usr/bin/env bash
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message) last="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat >/dev/null
ctr="$CLAWDND_STATE_DIR/.fake-attempts"
n=0; [ -f "$ctr" ] && n="$(cat "$ctr")"; n=$((n + 1)); printf '%s' "$n" > "$ctr"
# Fail the very first attempt (forces the retry), then succeed on every subsequent attempt.
if [ "$n" -eq 1 ]; then
  exit 1
fi
mkdir -p "$CLAWDND_STATE_DIR/campaigns/camp_fake"
printf '{"id":"camp_fake","active_session_id":"session_fake","characters":{"pc":{"kind":"player"}}}' > "$CLAWDND_STATE_DIR/campaigns/camp_fake/snapshot.json"
printf 'Opening narration after a retry.' > "$last"
printf '{"type":"result","result":"Opening narration after a retry."}\\n'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="codex-retry",
        CLAWDND_PLAY_PORT="8802",
        CLAWDND_PLAY_MAX_TURNS="1",
        CLAWDND_PLAY_HERO=json.dumps({"canon": True, "name": "Abby"}),
    )
    result = _run_dm([], env, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    chat = (tmp_path / "codex-retry" / "chat.jsonl").read_text(encoding="utf-8")
    assert "Opening narration after a retry." in chat
    # turn cap (1) reached cleanly → 'stopped', NOT 'failed' (the retry recovered the opening).
    payload = json.loads((tmp_path / "codex-retry" / "provider_status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "stopped"
    assert payload["reason"] == "turn_cap"


def test_codex_dm_wrapper_run_stops_exhausted_over_session_budget(tmp_path):
    """A codex that reports token usage over a tiny session budget must stop 'exhausted'."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    # Seat a PC on the opening, emit a HUGE token_count event (cumulative), then succeed. With a tiny
    # session budget the over-budget check fires BEFORE the next turn and stops 'exhausted'.
    fake_codex.write_text(
        """#!/usr/bin/env bash
last=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message) last="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat >/dev/null
mkdir -p "$CLAWDND_STATE_DIR/campaigns/camp_fake"
printf '{"id":"camp_fake","active_session_id":"session_fake","characters":{"pc":{"kind":"player"}}}' > "$CLAWDND_STATE_DIR/campaigns/camp_fake/snapshot.json"
printf 'Opening narration with heavy token usage.' > "$last"
printf '{"type":"token_count","info":{"total_token_usage":{"input_tokens":4000000,"output_tokens":1000000}}}\\n'
printf '{"type":"result","result":"Opening narration with heavy token usage."}\\n'
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = _env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        CLAWDND_RUN_ID="codex-budget",
        CLAWDND_PLAY_PORT="8803",
        CLAWDND_PLAY_MAX_TURNS="50",                # high, so the BUDGET (not the turn cap) stops it
        CLAWDND_PLAY_SESSION_BUDGET="0.10",          # 5M tokens @ $10/Mtok = $50 >> $0.10
        WORLDOS_CODEX_USD_PER_MTOK="10",
        CLAWDND_PLAY_HERO=json.dumps({"canon": True, "name": "Abby"}),
    )
    result = _run_dm([], env, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((tmp_path / "codex-budget" / "provider_status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "exhausted"
    assert payload["reason"] == "budget"
