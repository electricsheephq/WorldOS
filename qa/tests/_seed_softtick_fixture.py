"""F04-2 fixture: seed a campaign with (a) a thread-beat due TODAY so the harness soft-tick's
advance_time(phases=1) fires it into `world_beats`, AND (b) a minute-scale clock effect that
EXPIRES on that same phase advance so it lands in `expired_effects` — the two living-world
channels the leak used to discard. The expiry channel is list[{character_id, name}] (NOT plain
strings like the others), which is why its carry line must render the effect NAME, not the raw
dict repr (F04-2 follow-up).

Run with WORLDOS_STATE_DIR/CLAWDND_STATE_DIR pointed at a temp state dir (the shell proof
does this). Prints ONLY the campaign id on success so the caller can capture it.
"""
import os
import sys

# Make the engine package importable however we're invoked (the shell proof runs us with
# `uv run --directory servers/engine python <abs-path>`, which puts THIS file's dir on the
# path, not the engine dir). WORLDOS_ENGINE_DIR is set by the proof; fall back to the repo layout.
_engine = os.environ.get("WORLDOS_ENGINE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "servers", "engine",
)
if _engine not in sys.path:
    sys.path.insert(0, _engine)

import server  # noqa: E402
import store  # noqa: E402
from models import ActiveEffect, Consequence  # noqa: E402


def main() -> int:
    cid = server.create_campaign("F04-2 soft-tick carry-forward proof")["id"]
    # Seat a player PC so the snapshot looks like a real run (clawdnd_snapshot_path finds it).
    server.create_character(cid, "Renn", kind="player")

    c = store.load_campaign(cid)
    # A standing thread-beat DUE today: worldsim.tick fires it on the next clock advance and
    # re-arms it forward (so it can't double-fire). The text is what must reach the DM.
    c.consequences.append(
        Consequence(
            trigger_day=c.day,  # due now -> fires on the soft-tick's advance_time
            text="FIXTURE-BEAT: the marketplace fixers move on the docks while the party sleeps",
            note="F04-2 fixture thread-beat",
            thread_id="thread-fixture-f042",
        )
    )
    # A minute-scale clock effect that EXPIRES on the soft-tick's phase advance: out of combat,
    # ANY time-of-day phase advance expires a round/minute-scale effect
    # (combat.expire_clock_effects), so advance_time(phases=1) reports it in `expired_effects`.
    # That channel is list[{character_id, name}] (dicts, unlike the string-valued world_beats /
    # world_developments), so the carry line must surface the effect NAME ("Bless"), never the
    # raw "{'character_id': ..., 'name': 'Bless'}" dict repr (the F04-2-follow-up bug).
    pc = next(iter(c.characters.values()))
    pc.active_effects.append(ActiveEffect(name="Bless", scale="minutes", rounds_remaining=10))

    # Put the clock at evening so advance_time(phases=1) rolls a real phase (steps > 0 -> tick).
    c.time_of_day = "evening"
    store.save_campaign(c)

    print(cid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
