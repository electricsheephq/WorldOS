#!/usr/bin/env python3
"""Per-beat quest-stage telemetry for the A-series adventure eval (A-T).

Polls the engine IN-PROCESS (no MCP round-trip — same import-server pattern as
qa/seed_adventure_demo.py and run_duo's checkpoint slot) for the state of the adventure quest
and appends STAGE STAMPS to a ``quest_trace.json`` as the arc advances. Wired into
qa/run_adventure.sh between beats (mirrors how qa/dm_beat_mark.py hooks the beat path): each beat
calls ``quest_progress.py <state_dir> <campaign_id> --beat <n>`` and the runner short-circuits when
the quest status leaves ``active``.

The six stages (the A-T contract, docs/roadmap §4d), in ARC ORDER:

  reached_giver     — the party MET the quest giver (Keeper Maera): current location is/was the
                      giver's location, OR a session-log DIALOGUE record voiced BY the giver (a real
                      parley — NOT bare narration merely mentioning her name), OR (transitively) the
                      speak-objective completed.
  quest_accepted    — the "Speak with …" giver objective is completed (the parley → job taken).
  entered_dungeon   — current location is the quest's dungeon (the crypt), OR (objective fallback,
                      like the sibling stages) a later-arc signal proves the party was inside it:
                      the "Clear the crypt" objective landed, or the boss went down.
  boss_dead         — the "Slay the … boss" objective is completed, OR the boss character is dead
                      in the snapshot (and combat over — get_state.in_combat False).
  reward_received   — the "Return … for the reward" objective is completed, OR the reward item is
                      in the player's inventory.
  quest_completed   — the quest status flipped off ``active`` (completed / failed).

Stamps are MONOTONIC and idempotent: a stage is stamped exactly ONCE, the first beat it is detected;
re-running never re-stamps or re-orders. Each stamp records ``{stage, beat, ts, signal}`` where
``signal`` names WHICH detector fired (objective vs. location vs. snapshot vs. session-log), so a
stage-gap analysis can see how the arc advanced. The trace also carries the live ``quest_status`` and
``completed_objectives`` so the aggregator (qa/adventure_eval.py) reads one file per run.

Offline-testable by construction: every stage has an OBJECTIVE-based detector, so a scripted campaign
(seed → complete_objective / complete_quest directly) drives every stamp with no LLM in the loop.
Run the tests single-process:
    uv run --directory servers/engine python -m pytest qa/test_quest_progress.py -p no:xdist
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent

# The arc-ordered stages. Order is load-bearing: the aggregator reads gaps between CONSECUTIVE
# stages, and a stamp is only written when every EARLIER stage has already fired (monotonic arc).
STAGES: tuple[str, ...] = (
    "reached_giver",
    "quest_accepted",
    "entered_dungeon",
    "boss_dead",
    "reward_received",
    "quest_completed",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _import_server(state_dir: str):
    """Import the engine ``server`` module bound to ``state_dir`` (mirrors seed_adventure_demo)."""
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    eng = str(HERE.parent / "servers" / "engine")
    if eng not in sys.path:
        sys.path.insert(0, eng)
    import server  # noqa: PLC0415
    return server


def _select_quest(quests: list[dict], quest_title: str) -> Optional[dict]:
    """Pick the quest to track: an exact/substring title match if given, else the sole quest,
    else the first quest. Returns None when there are no quests."""
    if not quests:
        return None
    if quest_title:
        needle = quest_title.strip().lower()
        exact = [q for q in quests if str(q.get("title", "")).strip().lower() == needle]
        if exact:
            return exact[0]
        sub = [q for q in quests if needle in str(q.get("title", "")).lower()]
        if sub:
            return sub[0]
    if len(quests) == 1:
        return quests[0]
    # Heuristic default for the fixture: prefer a quest that mentions a crypt/boss, else the first.
    for q in quests:
        if "crypt" in str(q.get("title", "")).lower():
            return q
    return quests[0]


def _objective_done(quest: dict, *needles: str) -> Optional[str]:
    """Return the completed objective text matching ANY needle (case-insensitive substring), or None."""
    done = [str(o) for o in quest.get("completed_objectives") or []]
    for o in done:
        low = o.lower()
        if any(n in low for n in needles):
            return o
    return None


def _giver_location_id(server, campaign_id: str, giver_id: str) -> Optional[str]:
    """The location id of the quest giver character (read-only snapshot access)."""
    if not giver_id:
        return None
    try:
        c = server._require(campaign_id)
        ch = c.characters.get(giver_id)
        return getattr(ch, "location_id", None) if ch is not None else None
    except Exception:
        return None


def _boss_dead_in_snapshot(server, campaign_id: str) -> bool:
    """True when a boss-named character is dead in the snapshot (secondary boss_dead signal)."""
    try:
        c = server._require(campaign_id)
        for ch in c.characters.values():
            if "boss" in str(getattr(ch, "name", "")).lower() and bool(getattr(ch, "dead", False)):
                return True
    except Exception:
        return False
    return False


def _reward_item_in_party(server, campaign_id: str, reward_hint: str = "ring of protection") -> bool:
    """True when a party player carries an item whose name matches the reward hint (secondary
    reward_received signal). Best-effort: inventory shape varies, so any failure returns False."""
    try:
        c = server._require(campaign_id)
        hint = reward_hint.lower()
        for cid in getattr(c, "party", []) or []:
            ch = c.characters.get(cid)
            if ch is None or getattr(ch, "kind", "") != "player":
                continue
            for it in getattr(ch, "inventory", []) or []:
                name = it.get("name") if isinstance(it, dict) else getattr(it, "name", str(it))
                if name and hint in str(name).lower():
                    return True
    except Exception:
        return False
    return False


def _session_log_giver_dialogue(state_dir: str, giver_name: str) -> bool:
    """Scan the active session log for a DIALOGUE record voiced BY the giver — evidence of an actual
    parley, NOT mere narration that happens to mention them. The old any-text-mention fallback was
    too eager: camp narration naming "Maera" stamped reached_giver before the party had met her. So
    require a ``kind="dialogue"`` row whose SPEAKER is the giver. Reuses dm_beat_mark's snapshot /
    session-log resolution. Fail-open-False."""
    if not giver_name:
        return False
    try:
        from dm_beat_mark import _session_log_path, _snapshot_path  # noqa: PLC0415
    except Exception:
        return False
    snap = _snapshot_path(state_dir)
    log = _session_log_path(snap) if snap else ""
    if not log or not os.path.isfile(log):
        return False
    needle = giver_name.strip().lower()
    # Match the giver's SHORT name too (e.g. "Maera" from "Keeper Maera") — the DM voices her by name.
    short = needle.split()[-1] if needle.split() else needle
    try:
        with open(log, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if str(row.get("kind") or "").lower() != "dialogue":
                    continue  # narration/system rows only MENTION; a parley is a dialogue record
                speaker = str(row.get("speaker") or "").lower()
                if needle in speaker or (short and short in speaker):
                    return True
    except OSError:
        return False
    return False


def detect_stages(
    server,
    campaign_id: str,
    state_dir: str,
    *,
    quest: dict,
    giver_name: str = "",
) -> dict[str, str]:
    """Return {stage: signal} for every stage CURRENTLY detected (monotonic ordering is enforced
    by the caller when stamping). ``signal`` names the detector that fired."""
    state = server.get_state(campaign_id)
    cur_loc = (state.get("location") or {}).get("id") if state.get("location") else None
    in_combat = bool(state.get("in_combat"))
    giver_id = str(quest.get("giver_id") or "")
    giver_loc = _giver_location_id(server, campaign_id, giver_id)
    dungeon_loc = str(quest.get("location_id") or "")
    status = str(quest.get("status") or "active")

    found: dict[str, str] = {}

    # reached_giver — met the giver: physically at their room, OR an on-screen parley (a dialogue
    # record voiced by the giver), OR (below) the speak-objective completed. NOT a bare text mention.
    if giver_loc and cur_loc == giver_loc:
        found["reached_giver"] = "location:at-giver"
    elif _session_log_giver_dialogue(state_dir, giver_name):
        found["reached_giver"] = "session-log:giver-parley"

    # quest_accepted — the "Speak with <giver>" objective is completed.
    spoke = _objective_done(quest, "speak")
    if spoke:
        found["quest_accepted"] = f"objective:{spoke!r}"
        # completing the parley objective also PROVES the giver was reached.
        found.setdefault("reached_giver", "objective:speak-completed")

    # entered_dungeon — standing in the quest's dungeon room, OR (objective fallback, like the sibling
    # stages) a later-arc signal that proves the party WAS in the crypt: the "Clear the crypt"
    # objective landed. A transient in-dungeon location beat can fall between polls; a downstream
    # signal must not drop entered_dungeon just because that intermediate location beat was missed.
    if dungeon_loc and cur_loc == dungeon_loc:
        found["entered_dungeon"] = "location:in-dungeon"
    else:
        cleared = _objective_done(quest, "clear the crypt", "clear the")
        if cleared:
            found["entered_dungeon"] = f"objective:{cleared!r}"

    # boss_dead — the slay objective, or a dead boss in the snapshot with combat resolved. A downed
    # boss ALSO implies the party entered the dungeon (setdefault, so an explicit location/objective
    # signal above still wins) — the boss only dies inside the crypt's throne hall.
    slew = _objective_done(quest, "boss", "slay")
    if slew:
        found["boss_dead"] = f"objective:{slew!r}"
        found.setdefault("entered_dungeon", "objective:boss-implies-dungeon")
    elif _boss_dead_in_snapshot(server, campaign_id) and not in_combat:
        found["boss_dead"] = "snapshot:boss-dead+combat-over"
        found.setdefault("entered_dungeon", "snapshot:boss-implies-dungeon")

    # reward_received — the return objective, or the reward item is carried.
    ret = _objective_done(quest, "return", "reward")
    if ret:
        found["reward_received"] = f"objective:{ret!r}"
    elif _reward_item_in_party(server, campaign_id):
        found["reward_received"] = "snapshot:reward-in-inventory"

    # quest_completed — status flipped off active.
    if status != "active":
        found["quest_completed"] = f"status:{status}"

    return found


def load_trace(path: str) -> dict:
    """Read an existing trace, or a fresh skeleton."""
    p = Path(path)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("stamps", [])
                return data
        except (OSError, ValueError):
            # Corrupt/unreadable trace: fall through to a fresh trace rather than
            # aborting telemetry — stamps are re-derivable from engine state next poll.
            return {"stamps": []}
    return {"stamps": []}


def _stamped_stages(trace: dict) -> set[str]:
    return {str(s.get("stage")) for s in trace.get("stamps") or [] if s.get("stage")}


def _seeded_world(server, campaign_id: str, quest: dict) -> dict:
    """Freeze fixture identities before the DM can rename, reuse, move, or mint content."""
    c = server._require(campaign_id)
    giver_id = str(quest.get("giver_id") or "")
    crypt_id = str(quest.get("location_id") or "")
    throne_id = next((str(lid) for lid, loc in c.locations.items()
                      if str(lid) == "throne_hall"
                      or str(getattr(loc, "name", "")).lower().replace(" ", "_") == "throne_hall"), "")
    monsters = [(str(cid), ch) for cid, ch in c.characters.items()
                if str(getattr(ch, "kind", "")) == "monster"]
    boss_id = next((cid for cid, ch in monsters
                    if str(getattr(ch, "creature_slug", "")) == "goblin-boss"), "")
    return {
        "giver_id": giver_id,
        "giver_location_id": getattr(c.characters.get(giver_id), "location_id", None),
        "crypt_location_id": crypt_id,
        "throne_location_id": throne_id,
        "crypt_hostile_ids": [cid for cid, ch in monsters
                              if getattr(ch, "location_id", None) == crypt_id],
        "boss_id": boss_id,
    }


def _verify_objective(server, campaign_id: str, quest: dict, seed: dict, index: int) -> dict:
    c = server._require(campaign_id)
    objective = str((quest.get("objectives") or [])[index - 1])
    party_loc = getattr(c, "current_location_id", None)
    giver = c.characters.get(seed.get("giver_id"))
    giver_alive = giver is not None and not getattr(giver, "dead", False) \
        and (getattr(giver, "current_hp", 1) or 0) > 0
    failures: list[str] = []
    if index == 1:
        if party_loc != seed.get("giver_location_id"):
            failures.append(f"party at {party_loc}, not giver location {seed.get('giver_location_id')}")
        if not giver_alive:
            failures.append(f"seeded giver {seed.get('giver_id')} is not alive")
    elif index == 2:
        for cid in seed.get("crypt_hostile_ids") or []:
            ch = c.characters.get(cid)
            if ch is None or (not getattr(ch, "dead", False)
                              and (getattr(ch, "current_hp", 1) or 0) > 0
                              and getattr(ch, "location_id", None) is not None):
                hp = getattr(ch, "current_hp", "missing") if ch is not None else "missing"
                failures.append(f"seeded crypt hostile {cid} alive at {hp}")
    elif index == 3:
        boss = c.characters.get(seed.get("boss_id"))
        if boss is None or not getattr(boss, "dead", False):
            hp = getattr(boss, "current_hp", "missing") if boss is not None else "missing"
            max_hp = getattr(boss, "max_hp", "?") if boss is not None else "?"
            failures.append(f"seeded boss {seed.get('boss_id')} alive at {hp}/{max_hp}")
        valid_locs = {seed.get("throne_location_id"), getattr(boss, "location_id", None)} - {None, ""}
        if party_loc not in valid_locs:
            failures.append(f"party at {party_loc}, not seeded throne/boss location")
    elif index == 4:
        if not giver_alive:
            failures.append(f"seeded giver {seed.get('giver_id')} is not alive")
        if party_loc != seed.get("giver_location_id"):
            failures.append(f"party at {party_loc}, not giver location {seed.get('giver_location_id')}")
        if str(quest.get("status") or "").lower() != "completed":
            failures.append(f"engine quest status is {quest.get('status')!r}, not completed")
    return {"index": index, "objective": objective, "verified": not failures,
            "reason": f"objective {index} {objective!r}: " + "; ".join(failures) if failures else ""}


def _stamp_completion_truth(server, campaign_id: str, quest: dict, trace: dict) -> None:
    if "seeded_world" not in trace:
        trace["seeded_world"] = _seeded_world(server, campaign_id, quest)
    old_done = set(trace.get("completed_objectives") or [])
    records = {int(r["index"]): r for r in trace.get("objective_truth") or []}
    for index, objective in enumerate(quest.get("objectives") or [], 1):
        if objective in (quest.get("completed_objectives") or []) and objective not in old_done:
            records[index] = _verify_objective(server, campaign_id, quest, trace["seeded_world"], index)
    trace["objective_truth"] = [records[i] for i in sorted(records)]
    claimed = str(quest.get("status") or "").lower() == "completed"
    missing = [i for i in range(1, len(quest.get("objectives") or []) + 1) if i not in records]
    trace["completion_claimed"] = claimed
    trace["completion_verified"] = claimed and not missing and all(r["verified"] for r in records.values())
    trace["completion_truth"] = [r["reason"] for r in records.values() if not r["verified"]]
    if claimed:
        trace["completion_truth"] += [f"objective {i}: no world verification recorded" for i in missing]


def stamp_beat(
    trace: dict,
    found: dict[str, str],
    *,
    beat: int,
    campaign_id: str,
    quest: dict,
) -> list[str]:
    """Append a stamp for every NEWLY-detected stage. Per-stage MONOTONIC: a stage is stamped
    exactly ONCE (the first beat it is detected) and never re-stamped. Within a single poll,
    multiple newly-detected stages are stamped in STAGES (arc) order, so a poll that first observes
    several stages at once records them in order.

    There is deliberately NO earlier-stage GATE: a real terminal signal (a slain boss, a flipped
    quest status) must never be dropped because an intermediate location-beat happened to fall
    between two polls and was missed. The arc order lives in the beat numbers + the STAGES ordering,
    which the aggregator's stage-gap analysis reads; it does not require every prior stage to have
    been independently caught. Returns the stages newly stamped this call, in arc order."""
    already = _stamped_stages(trace)
    ts = _now()
    newly: list[str] = []
    for stage in STAGES:
        if stage in already:
            continue
        if stage in found:
            # Every stage first OBSERVED in the same poll shares this ONE beat number. So the
            # aggregator's inter-stage gaps are measured at POLL GRANULARITY, not wall-clock: a poll
            # that first sees several stages at once records a zero-beat gap between them even if they
            # actually landed a beat or two apart. Coarser polling (every k beats) therefore DEFLATES
            # gap precision — a deliberate tradeoff (the poll is a cheap between-beats telemetry hook,
            # not a per-event clock). The stage-gap outlier threshold is set with this coarseness in mind.
            trace["stamps"].append({
                "stage": stage,
                "beat": int(beat),
                "ts": ts,
                "signal": found[stage],
            })
            newly.append(stage)

    # Refresh the live status fields every call (cheap, read-only).
    trace["campaign_id"] = campaign_id
    trace["quest_id"] = quest.get("id")
    trace["quest_title"] = quest.get("title")
    trace["quest_status"] = quest.get("status")
    trace["completed_objectives"] = list(quest.get("completed_objectives") or [])
    trace["objectives"] = list(quest.get("objectives") or [])
    trace["updated_ts"] = ts
    trace["updated_beat"] = int(beat)
    return newly


def save_trace(path: str, trace: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def poll(
    state_dir: str,
    campaign_id: str,
    *,
    beat: int = 0,
    trace_path: str = "",
    quest_title: str = "",
) -> dict:
    """The one entry point: poll the engine, stamp any new stages, persist the trace. Returns a
    small result dict {quest_status, newly_stamped, trace_path, quest_id}."""
    server = _import_server(state_dir)
    quests = server.get_quests(campaign_id).get("quests") or []
    quest = _select_quest(quests, quest_title)
    trace_path = trace_path or str(Path(state_dir) / "quest_trace.json")
    trace = load_trace(trace_path)
    if quest is None:
        trace.setdefault("campaign_id", campaign_id)
        trace["quest_status"] = None
        trace["updated_beat"] = int(beat)
        save_trace(trace_path, trace)
        return {"quest_status": None, "newly_stamped": [], "trace_path": trace_path, "quest_id": None}

    giver_name = ""
    giver_id = str(quest.get("giver_id") or "")
    if giver_id:
        try:
            c = server._require(campaign_id)
            ch = c.characters.get(giver_id)
            giver_name = getattr(ch, "name", "") if ch is not None else ""
        except Exception:
            giver_name = ""

    _stamp_completion_truth(server, campaign_id, quest, trace)
    found = detect_stages(server, campaign_id, state_dir, quest=quest, giver_name=giver_name)
    newly = stamp_beat(trace, found, beat=beat, campaign_id=campaign_id, quest=quest)
    save_trace(trace_path, trace)
    return {
        "quest_status": quest.get("status"),
        "newly_stamped": newly,
        "trace_path": trace_path,
        "quest_id": quest.get("id"),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Per-beat quest-stage telemetry (A-series A-T).")
    ap.add_argument("state_dir", help="WORLDOS_STATE_DIR the campaign lives under")
    ap.add_argument("campaign_id", help="campaign id to poll (e.g. adventure_demo_v1)")
    ap.add_argument("--beat", type=int, default=0, help="the beat number this poll follows")
    ap.add_argument("--trace", default="", help="quest_trace.json path (default <state_dir>/quest_trace.json)")
    ap.add_argument("--quest-title", default="", help="title (or substring) of the quest to track")
    args = ap.parse_args(argv)

    res = poll(
        args.state_dir, args.campaign_id,
        beat=args.beat, trace_path=args.trace, quest_title=args.quest_title,
    )
    if res["newly_stamped"]:
        print(f"[quest_progress] beat {args.beat}: stamped {', '.join(res['newly_stamped'])}")
    else:
        print(f"[quest_progress] beat {args.beat}: no new stage")
    # LAST line is the machine contract the runner reads for the completion short-circuit.
    print(f"quest_status={res['quest_status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
