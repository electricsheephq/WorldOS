"""Drive an engine-run combat into the LIVE PREVIEW so it can be WATCHED in the web #battle
viewer (= exactly what the Mac app's battle tab renders). NO LLM — this is the v2.0 competent
engine AI (combat_loop.run_combat_autonomous's per-round step), paced so the polling viewer shows
the fight UNFOLD round-by-round (tokens, HP dropping, heals/casts/abilities, the battle log).

Usage (via qa/preview_combat.sh, which sets WORLDOS_STATE_DIR=play-state/preview):
    python preview_combat_driver.py [delay_s] [seed] [rounds]

It clears prior preview campaigns so the only campaign is this one (the #battle screen
auto-selects it), seeds a sandbox party vs monsters, then runs each round + sleeps `delay_s`
so the ~poll-interval viewer catches each round's end state.
"""
from __future__ import annotations
import os
import shutil
import sys
import time

delay = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 11
max_rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 25

# The engine modules (dice/combat_loop/store/server) live in servers/engine — put it on the path
# so this works whether run as a file or via `uv run --directory servers/engine`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "servers", "engine")))
import dice
import combat_loop
import store
import server

state = os.environ.get("WORLDOS_STATE_DIR", "")
assert state, "WORLDOS_STATE_DIR must be set (run via qa/preview_combat.sh)"
# Fresh: clear prior preview campaigns so the #battle screen auto-selects THIS combat.
camps = os.path.join(state, "campaigns")
if os.path.isdir(camps):
    shutil.rmtree(camps)
os.makedirs(camps, exist_ok=True)

dice.reseed_process_rng(seed)
cid = server.create_campaign("Combat Preview")["id"]
server.add_location(campaign_id=cid, name="Ruined Keep", description="a broken hall lit by torchlight",
                    make_current=True)
c = server._require(cid)
c.is_sandbox = True
store.save_campaign(c)


def _mk(name, kind, race, cls, lvl, ab):
    return server.create_character(cid, name, kind=kind, race=race, class_name=cls, level=lvl,
                                   abilities=ab, apply_srd_defaults=True)["id"]


STR = {"strength": 17, "dexterity": 12, "constitution": 16, "intelligence": 10, "wisdom": 12, "charisma": 10}
WIS = {"strength": 12, "dexterity": 12, "constitution": 14, "intelligence": 10, "wisdom": 17, "charisma": 12}
DEX = {"strength": 10, "dexterity": 17, "constitution": 14, "intelligence": 12, "wisdom": 12, "charisma": 10}
party = [
    _mk("Borin", "player", "dwarf", "fighter", 5, STR),
    _mk("Mira", "companion", "human", "cleric", 5, WIS),
    _mk("Sly", "companion", "halfling", "rogue", 5, DEX),
]
foes = [m["id"] for m in server.spawn_monster(cid, "Goblin", count=5)["spawned"]]
server.start_combat(cid, party + foes)
names = {ch.id: ch.name for ch in server._require(cid).characters.values()}

print(f"campaign={cid}", flush=True)
print(f"WATCH:  http://127.0.0.1:8799/openworlds/#battle   (auto-selects this preview combat)", flush=True)
print(f"driving the v2.0 engine AI, {delay}s/round (NO LLM)\n", flush=True)

for _ in range(max_rounds):
    c = server._require(cid)
    if not c.combat.active:
        break
    rr = combat_loop.run_combat_round(cid, mode="test")
    acts = [f"{names.get(e['actor_id'], '?')}:{e['kind']}" for e in rr["round_digest"]
            if not names.get(e["actor_id"], "").startswith("Goblin")]
    print(f"  round {rr['round']}: {', '.join(acts[:8])}", flush=True)
    living = rr.get("living_sides", [])
    if not rr["combat_active"] or len(living) < 2:
        break
    time.sleep(delay)

# Let the final round linger on screen, then resolve so the viewer shows the victory closeout.
c = server._require(cid)
living = combat_loop._living_sides(c)
if c.combat.active and len(living) == 1:
    time.sleep(delay)
    try:
        server.end_combat(cid, resolution=f"{next(iter(living))} victorious (engine-run preview)")
    except Exception as exc:
        print(f"  (end_combat: {exc})", flush=True)

c = server._require(cid)
alive = [names[ch.id] for ch in c.characters.values() if ch.current_hp > 0 and ch.id in (party + foes)]
print(f"\nDONE — survivors: {alive}", flush=True)
