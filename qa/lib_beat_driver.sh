# shellcheck shell=bash
# Shared beat-driver helpers for the WorldOS play loops (qa/run_duo.sh + scripts/play.sh).
#
# This is the STRUCTURE behind the "living, progressing world" fix (decision-dm-driver.md):
# prose nudges alone never moved the clock past day 1, never visited >1 location, and NEVER
# created an on-screen NPC across 57 campaigns. So the harness now drives progression
# structurally, in two pieces — both routing through the ENGINE so it stays the SOLE WRITER
# of snapshot.json (we never hand-edit state):
#
#   C — soft clock-tick backstop (clawdnd_soft_tick): after a DM beat, if the in-world clock
#       did NOT advance this beat, advance ONE time-of-day phase via the engine's advance_time.
#       Defers to the DM's own pacing when it advances time in-fiction; only fires when frozen.
#
#   A — beat-aware runbook injection (clawdnd_runbook_for_beat): instead of the SAME constant
#       "keep the world moving" paragraph every beat (noise the model learns to skim), emit ONE
#       moment-specific runbook chosen from the beat index + the live snapshot (location/time/
#       visited/peopling): scene-intro on a new place, travel/peopling when stuck, the midpoint
#       reversal at ~beats/2, the climax/payoff in the final ~2 beats.
#
# Sourced by BOTH loops so the two harnesses can't drift. Pure bash + a tiny `uv run` python
# shim into servers/engine (the engine's venv has the deps; bare python3 does not).

# --- WorldOS rename env-compat (issue #295, W0-E) ---------------------------------
# Resolve an env var by suffix, preferring WORLDOS_<suffix> and falling back to the
# legacy CLAWDND_<suffix> (one-time stderr deprecation warning), else a default.
#   worldos_env DM_MODEL sonnet   ->  $WORLDOS_DM_MODEL, else $CLAWDND_DM_MODEL, else "sonnet"
# Mirrors servers/*/_env.py for the shell side; both names resolve for v1.x.
# Note: worldos_env is typically called inside $(...) (a forked subshell), so an
# in-memory "warned" flag wouldn't survive between calls. We key the once-warning off
# a tiny per-(invocation, var) sentinel file under $TMPDIR so it stays one-time across
# the subshells of a single script run (PPID = the script's pid from the subshell).
worldos_env() {
  local suffix="$1" default="${2:-}"
  local w="WORLDOS_${suffix}" c="CLAWDND_${suffix}"
  if [ -n "${!w:-}" ]; then
    printf '%s' "${!w}"
  elif [ -n "${!c:-}" ]; then
    local sentinel="${TMPDIR:-/tmp}/worldos-envwarn.$PPID.$c"
    if [ ! -e "$sentinel" ]; then
      : > "$sentinel" 2>/dev/null || true
      printf '[worldos] DEPRECATION: env var %s is renamed to %s; the old name still works for v1.x but will be removed in v2.0.\n' "$c" "$w" >&2
    fi
    printf '%s' "${!c}"
  else
    printf '%s' "$default"
  fi
}

# Resolve the run's campaign snapshot.json the same way the harnesses pick state for scoring:
# the LARGEST non-empty snapshot under <state_dir>/campaigns (never a blind head -1, which a
# fat-fingered campaign_id could point at a lock-only orphan dir). Echoes the path or nothing.
# $1 = STATE_DIR
clawdnd_snapshot_path() {
  local state_dir="$1"
  find "$state_dir/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c \
    -exec ls -S {} + 2>/dev/null | head -1
}

# Read the run's progression facts from the snapshot in ONE python pass. Echoes a single
# TAB-separated line:  day <TAB> time_of_day <TAB> visited_count <TAB> npcs_met <TAB>
# current_location_id <TAB> current_location_visited(0/1) <TAB> combat_active(0/1)
# (combat_active is field 7, appended additively — fields 1-6 are unchanged for callers that
# cut -f1..6). Echoes nothing when there's no snapshot yet (pre-first-beat). Read-only.
# $1 = STATE_DIR
clawdnd_read_progress() {
  local snap; snap="$(clawdnd_snapshot_path "$1")"
  [ -n "$snap" ] || return 0
  python3 - "$snap" <<'PY' 2>/dev/null
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
day = s.get("day") or 1
tod = (s.get("time_of_day") or "").strip().lower() or "morning"
locs = s.get("locations", {}) or {}
visited = sum(1 for l in locs.values() if isinstance(l, dict) and l.get("visited"))
chars = s.get("characters", {}) or {}
npcs_met = sum(1 for c in chars.values()
               if isinstance(c, dict) and c.get("kind") == "npc" and c.get("met"))
cur = s.get("current_location_id") or ""
cur_visited = 0
cl = locs.get(cur) if cur else None
if isinstance(cl, dict) and cl.get("visited"):
    cur_visited = 1
combat = s.get("combat") or {}
combat_active = 1 if isinstance(combat, dict) and combat.get("active") else 0
print("\t".join([str(day), tod, str(visited), str(npcs_met), str(cur),
                 str(cur_visited), str(combat_active)]))
PY
}

# The C backstop. After a DM beat, compare the live clock to the clock BEFORE the beat; if the
# DM did NOT advance it this beat, advance ONE phase through the engine (advance_time(phases=1))
# so the day cannot freeze at morning. Engine stays the sole writer. No-op (and silent) until a
# snapshot exists. Echoes a short "[tick] …" status to stderr for the run log.
# $1 = ROOT (repo root)  $2 = STATE_DIR  $3 = prev_day  $4 = prev_tod
clawdnd_soft_tick() {
  local root="$1" state_dir="$2" prev_day="$3" prev_tod="$4"
  local snap cur cur_day cur_tod cur_combat
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  cur="$(clawdnd_read_progress "$state_dir")"
  cur_day="$(printf '%s' "$cur" | cut -f1)"
  cur_tod="$(printf '%s' "$cur" | cut -f2)"
  cur_combat="$(printf '%s' "$cur" | cut -f7)"
  # In COMBAT the clock is measured in rounds (6s) via next_turn, NOT world phases. Advancing a
  # phase here would expire every round/minute-scale buff at once (Bless, Hex) and drop
  # concentration mid-fight (combat.expire_clock_effects). A frozen world-clock during combat is
  # CORRECT — combat doesn't burn time-of-day phases — so SKIP the tick entirely while active.
  if [ "$cur_combat" = "1" ]; then
    printf '%s\n' "[tick] combat active -> world clock not advanced (combat runs in rounds; use next_turn)" >&2
    return 0
  fi
  # The DM already moved the clock this beat → defer to its pacing, do nothing.
  if [ "$cur_day" != "$prev_day" ] || [ "$cur_tod" != "$prev_tod" ]; then
    return 0
  fi
  # Frozen this beat → advance one phase via the engine. The campaign id is the snapshot's
  # parent dir name; CLAWDND_STATE_DIR scopes the engine to THIS run's state tree. We capture
  # the engine's output (status or error) and echo it to STDERR for the run log — we do NOT
  # blanket-suppress, so a real engine failure is visible. On a contended host `uv` can return a
  # transient cache error; that's non-fatal here (the NEXT beat re-reads the clock and re-ticks),
  # so we never let the tick's exit status fail the loop (the function always returns 0).
  local camp out; camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>&1 <<'PY'
import sys
import server
camp = sys.argv[1]
try:
    r = server.advance_time(camp, phases=1, note="harness soft clock-tick backstop")
    print(f"[tick] clock frozen this beat -> engine advanced to day {r.get('day')} {r.get('time_of_day')}")
except Exception as e:
    print(f"[tick] soft-tick FAILED ({e})")
PY
)"
  [ -n "$out" ] && printf '%s\n' "$out" >&2
  return 0
}

# The A runbook selector. Given the beat index, the total beats, the party's location at the
# START of this beat, and the live snapshot, return EXACTLY ONE moment-specific runbook to fold
# into the DM's prompt for THIS beat — replacing the old constant paragraph. Precedence (first
# match wins) so the most urgent structural gap is the one we fire:
#   1. new-location / early       -> "scene-intro"   (the party just arrived somewhere fresh,
#                                     or it's beat 1) — open the place before they act.
#   2. final ~2 beats             -> "climax/payoff" — converge; pay off what was set up.
#   3. midpoint (~beats/2)        -> "reversal"      — a turn that COSTS the hero personally.
#   4. stuck (no move in N beats) -> "travel/peopling" — move the party OR bring a new named NPC.
#   5. otherwise                  -> "rising-action" — escalate; keep friction that sticks.
# Echoes the runbook text (a short paragraph). Never empty.
# $1 = beat (1-based)  $2 = total beats  $3 = prev_location_id (loc at beat start)  $4 = STATE_DIR
clawdnd_runbook_for_beat() {
  local beat="$1" beats="$2" prev_loc="$3" state_dir="$4"
  local prog day tod visited npcs_met cur_loc cur_visited
  prog="$(clawdnd_read_progress "$state_dir")"
  day="$(printf '%s' "$prog" | cut -f1)";        day="${day:-1}"
  tod="$(printf '%s' "$prog" | cut -f2)";        tod="${tod:-morning}"
  visited="$(printf '%s' "$prog" | cut -f3)";    visited="${visited:-0}"
  npcs_met="$(printf '%s' "$prog" | cut -f4)";   npcs_met="${npcs_met:-0}"
  cur_loc="$(printf '%s' "$prog" | cut -f5)"
  cur_visited="$(printf '%s' "$prog" | cut -f6)"; cur_visited="${cur_visited:-0}"

  # Beat windows (integer math). Midpoint = round(beats/2); final window = last 2 beats.
  local mid=$(( (beats + 1) / 2 ))
  local final_start=$(( beats - 1 )); [ "$final_start" -lt 1 ] && final_start=1
  # "Stuck": the party's current location is UNCHANGED from the start of this beat AND we're
  # several beats in with still only one place visited. N = a couple of beats of standing still.
  local moved=1; [ -n "$cur_loc" ] && [ "$cur_loc" = "$prev_loc" ] && moved=0

  # 1) Scene-intro: brand-new location this beat (just arrived → not yet "visited"-narrated) or
  #    the very first beat. Open the place FIRST, then hand back the moment.
  if [ "$beat" -le 1 ] || { [ -n "$cur_loc" ] && [ "$cur_loc" != "$prev_loc" ] && [ "$cur_visited" = "0" ]; }; then
    printf '%s' "RUNBOOK — SCENE-INTRO (a new place, beat $beat): the party has just arrived somewhere new. BEFORE they act, YOU set this scene — look_around / get_scene first, then narrate the place's tone in your own prose (the light, the sound, who is present, what is wrong), generate_image(kind=\"scene\") for it, and put at least one named face here who SPEAKS. Then hand back the open moment. Do not wait for the player to author the room."
    return 0
  fi

  # 2) Climax / payoff: the final ~2 beats. Converge the threads; spend what was set up.
  if [ "$beat" -ge "$final_start" ] && [ "$beats" -ge 4 ]; then
    printf '%s' "RUNBOOK — CLIMAX / PAYOFF (beat $beat of $beats, the end is here): bring the arc to a head NOW — the confrontation, the reckoning, the choice that has been building. PAY OFF what Act 1 set up and what the midpoint reversal cost; let the price already paid matter. No new sub-plots. Land one decisive, dramatized moment and the consequence the player has earned — then a clean, resonant close."
    return 0
  fi

  # 3) Midpoint reversal: at ~beats/2, fire the turn — and make it COST.
  if [ "$beat" -eq "$mid" ] && [ "$beats" -ge 4 ]; then
    printf '%s' "RUNBOOK — MIDPOINT REVERSAL (beat $beat ≈ the turn): deliver the REVERSAL now, not merely 'harder'. Flip the situation — the ally is the informant, the prize is already gone, the safe path was the trap, the cost lands on the HERO personally (their own skin, bond, or secret on the line, not abstract world-stakes). Make a real attempt FAIL or a choice exact a price that STICKS and changes the scene. This is the lever the story score keeps docking — do not smooth it over."
    return 0
  fi

  # 4) Travel / peopling: the party has stood still and the world hasn't grown. Move OR people it.
  if [ "$moved" = "0" ] && [ "$beat" -ge 3 ] && { [ "$visited" -lt 2 ] || [ "$npcs_met" -lt 1 ]; }; then
    local why="the party has not moved"
    [ "$visited" -lt 2 ] && why="$why and has visited only $visited location(s)"
    [ "$npcs_met" -lt 1 ] && why="$why and NO new named NPC has entered yet (0 met)"
    printf '%s' "RUNBOOK — TRAVEL / PEOPLING (beat $beat: $why): the world must MOVE. Pick ONE and do it THIS beat through the engine — either (a) move the party to a NEW place: travel_to along a connection (advance_time=True for a real journey) or add_location(make_current=True), then narrate that new place's tone yourself; OR (b) bring a NEW named NPC on-screen: create_character with a name + a voice + at least one quoted line, mark met=True when the party meets them. A session frozen in one room with no new faces is a FAILED session — close that gap now."
    return 0
  fi

  # 5) Default — rising action between the named turns. Escalate; keep friction that sticks.
  printf '%s' "RUNBOOK — RISING ACTION (beat $beat of $beats, day $day $tod): raise the pressure a notch and keep momentum with friction that STICKS — an NPC refuses/stalls/lies/counters, a clever move has a real cost, the human-scale trouble widens. Don't grant every ask. If the scene has run its course, advance the clock or move on rather than lingering. End on a live, dramatized open moment — never a bare 'Your move.'"
}

# Honest-scoring cap. When the behavioral gate exited RED, a structurally broken/non-progressing
# run must NOT be allowed to display a glossy 4.1 — the LLM scorers grade prose and happily passed
# frozen one-scene runs. Rewrite a scorecard JSON in place: cap `overall` at 2.5, stamp
# `gate_capped=true` + `gate_status="RED"`, and prepend a critical defect explaining the cap (so
# the cap is visible and diagnosable, not silent). No-op when the file is missing/unparseable.
#
# The appended defect is now DOMAIN-GENERIC: the cap is applied to several lenses (mechanical,
# Tolkien story-craft, Angry-DM 5e-rules-fidelity), so the defect text must NOT be hard-worded for
# world-progression — capping an Angry-DM rules card with a "the party never traveled" defect is
# off-domain noise. The optional $3 picks lens-appropriate wording (default = generic).
# $1 = scorecard JSON path  $2 = a short reason string (e.g. the failed checks)
# $3 = optional domain hint: "story" (world-progression wording) | "" / anything else (generic)
clawdnd_cap_score_red() {
  local path="$1" reason="${2:-behavioral gate RED}" domain="${3:-}"
  [ -f "$path" ] || return 0
  python3 - "$path" "$reason" "$domain" <<'PY' 2>/dev/null || true
import json, sys
path, reason = sys.argv[1], sys.argv[2]
domain = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
CAP = 2.5
try:
    orig = float(d.get("overall"))
except (TypeError, ValueError):
    orig = None
d["gate_status"] = "RED"
d["gate_capped"] = True
d["overall_before_cap"] = orig
if orig is None or orig > CAP:
    d["overall"] = CAP
# Generic by default so the cap is domain-neutral across lenses; the "story" hint keeps the
# original world-progression phrasing for the story-craft / mechanical scorecards.
if domain == "story":
    area = "world-progression / behavioral gate"
    suggested_fix = (
        "This run FAILED the structural gate (e.g. the clock never advanced, the party never "
        "traveled, no new face entered). A long session still stuck in one Act-1 scene is a "
        "FAILURE TO PROGRESS, not a high score — overall CAPPED to 2.5 / INVALID. Fix the "
        "progression, don't polish prose inside a dead scene."
    )
else:
    area = "behavioral gate"
    suggested_fix = (
        "This run FAILED the deterministic behavioral gate, so its quality score is not "
        "trustworthy — overall CAPPED to 2.5 / INVALID. Fix the structural failure named in the "
        "gate reason before reading this scorecard; don't act on the capped number."
    )
defect = {
    "severity": "critical",
    "area": area,
    "evidence": f"behavioral gate RED ({reason}); recorded overall was {orig}",
    "suggested_fix": suggested_fix,
}
# Keep the appended defect schema-shaped for whatever lens this card is. The Angry-DM
# scorecard's defect object also requires kind/rule/five_e_says (it carries a `coverage`
# block — the reliable tell); add those so the capped card stays internally consistent.
if isinstance(d.get("coverage"), dict):
    defect.setdefault("kind", "commission")
    defect.setdefault("rule", "unverified")
    defect.setdefault("five_e_says", "A run that fails the deterministic structural gate cannot be trusted as a fair table; fix the gate failure first.")
defs = d.get("defects")
if isinstance(defs, list):
    d["defects"] = [defect] + defs
else:
    d["defects"] = [defect]
json.dump(d, open(path, "w"), indent=2)
PY
}

# Campaign Director advisory (#72): at the START of a beat, surface what the campaign OWES — an
# untracked hook to add_quest, an NPC introduced but still silent, a due consequence to land — so
# the DM is REMINDED structurally instead of relying on reach-for (the add_quest gap a QA run
# exposed). Read-only (get_campaign_director never mutates). Echoes a short DIRECTOR block for the
# DM beat prompt, or NOTHING when the campaign owes nothing / no snapshot yet. Non-fatal: a
# transient uv error -> empty (the next beat re-reads).
clawdnd_director_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
import sys
import server
try:
    r = server.get_campaign_director(sys.argv[1])
    adv = (r or {}).get("advisory") or []
    if adv:
        print("DIRECTOR — what the campaign OWES right now (weave the TOP one into THIS beat; do not recite the list):")
        for a in adv[:2]:
            print(f"- {a}")
except Exception:
    pass
PY
)"
  [ -n "$out" ] && printf '%s' "$out"
}

# Event advisory (Quest & Arc engine, Layer 3): at the START of a beat, surface the first-class
# stumble-into EVENTS whose contract-safe trigger holds NOW (a set flag, a faction's reputation
# reaching a level, a reached day — never fiction), so the DM is REMINDED to STAGE the available
# decisional instead of relying on reach-for (the same dark-surface gap the Director closed for
# add_quest before #154). MIRRORS clawdnd_director_advisory exactly: read-only (present_events
# never mutates), echoes a short EVENT block for the DM beat prompt, or NOTHING when no Event is
# available / no snapshot yet. Non-fatal: a transient uv error -> empty (the next beat re-reads).
clawdnd_event_advisory() {
  local root="$1" state_dir="$2" snap camp out
  snap="$(clawdnd_snapshot_path "$state_dir")"
  [ -n "$snap" ] || return 0
  camp="$(basename "$(dirname "$snap")")"
  out="$(WORLDOS_STATE_DIR="$state_dir" CLAWDND_STATE_DIR="$state_dir" uv run --directory "$root/servers/engine" python - "$camp" 2>/dev/null <<'PY'
import sys
import server
try:
    r = server.present_events(sys.argv[1])
    evs = (r or {}).get("events") or []
    if evs:
        print("EVENT AVAILABLE — a stumble-into decisional has arrived (STAGE the top one IN-CHARACTER this beat; lay out its options via the parley surface, free-form always allowed; resolve the pick with resolve_event):")
        for ev in evs[:2]:
            prompt = (ev.get("prompt") or "").strip()
            labels = ", ".join((o.get("label") or "").strip() for o in (ev.get("options") or []) if (o.get("label") or "").strip())
            line = f"- {ev.get('id')}: {prompt}" if prompt else f"- {ev.get('id')}"
            if labels:
                line += f"  [options: {labels}]"
            print(line)
except Exception:
    pass
PY
)"
  [ -n "$out" ] && printf '%s' "$out"
}
