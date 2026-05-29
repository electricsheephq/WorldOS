#!/usr/bin/env python3
"""Seed a RICH canon-NPC fixture — the CORRECT GUI/QA verification surface.

Seats a real mid-tier canon Baldur's Gate NPC (default Dal Lightspark, who has an ingested
portrait) as the kind="player" PC. NEVER an invented custom PC (no portrait-less "Caelar")
and NEVER one of the 7 BG3 origin heroes. Then travels to a real location (so scene art
renders), recruits a canon companion, adds inventory + a quest — so every OpenWorlds screen
has real content to render and audit.

Usage:
  CLAWDND_STATE_DIR=<dir> uv run --directory servers/engine python qa/seed_canon_fixture.py \
      ["Dal Lightspark"] ["Arthus"] ["loc-lower-city"]
Prints the campaign_id on the last line.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
import server

PLAYER = sys.argv[1] if len(sys.argv) > 1 else "Dal Lightspark"
COMPANION = sys.argv[2] if len(sys.argv) > 2 else "Arthus"
LOC = sys.argv[3] if len(sys.argv) > 3 else "loc-lower-city"


def cid_of(r):
    if isinstance(r, dict):
        return r.get("campaign_id") or r.get("id") or (r.get("campaign") or {}).get("id")
    return None


def player_id(cid):
    st = server.get_state(cid)
    chars = (st or {}).get("characters") if isinstance(st, dict) else None
    chars = list(chars.values()) if isinstance(chars, dict) else (chars or [])
    for c in chars:
        if isinstance(c, dict) and c.get("kind") == "player":
            return c.get("id")
    return None


w = server.start_world("baldurs-gate")
cid = cid_of(w)
print("campaign:", cid)

pc = server.load_canon_character(cid, PLAYER, kind="player", add_to_party=True)
print("PC:", PLAYER, "loaded")
server.start_session(cid)

try:
    t = server.travel_to(cid, LOC, advance_time=False)
    print("travel:", LOC, "→", "ok" if isinstance(t, dict) and not t.get("error") else t)
except Exception as e:
    print("travel ERR:", e)

try:
    server.load_canon_character(cid, COMPANION, kind="companion", add_to_party=True)
    print("companion:", COMPANION, "loaded")
except Exception as e:
    print("companion ERR:", e)

pid = player_id(cid)
print("player_id:", pid)
if pid:
    for it in ["Quarterstaff", "Potion of Healing", "Robe of the Archmagi"]:
        try:
            server.add_item(cid, pid, item_name=it)
        except Exception as e:
            print("item ERR", it, e)
    print("items added")

try:
    server.add_quest(cid, "The Silent Sending Stone",
                     description="A Harper contact's sending stone has gone quiet in the Lower City — someone should look in.",
                     location_id=LOC,
                     objectives=["Find the Harper's last known haunt", "Learn who silenced the stone"])
    print("quest added")
except Exception as e:
    print("quest ERR:", e)

print("DONE", cid)
