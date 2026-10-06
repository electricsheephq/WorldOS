#!/usr/bin/env python3
"""BG3-style demo asset library batch driver (issue #1628).

Queue-driven Tripo3D bulk generation. Checkpointed + resumable:
- done items recorded in <root>/manifest.jsonl (one row per completed asset)
- re-running skips done items; the tripo_gen.py wrapper itself skips regen
  when model.glb already exists in the item's out dir
- hard stop when Tripo balance < BALANCE_FLOOR
- --max-seconds bounds each invocation (caller re-invokes to continue)

Spec: docs/asset-library/BG3-DEMO-LIBRARY.md
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = "/Volumes/LEXAR/Codex/worldos-asset-library"
WRAPPER = "extensions/renderers/godot/tools/tripo_gen.py"
KEY_PATH = os.path.expanduser("~/.worldos/tripo3d.key")
BALANCE_FLOOR = 100
MANIFEST = os.path.join(ROOT, "manifest.jsonl")

STYLE = "stylized hand-painted dark fantasy RPG, muted earthy palette, game-ready low poly"
POSE = "A-pose, full body, feet on ground"


def pc(asset_id, core):
    return {"id": asset_id, "kind": "character", "wave": 1, "rig": True,
            "animations": ["walk", "idle", "run", "slash"],
            "prompt": "a %s, %s, %s, %s" % (core, POSE, STYLE, "isolated on plain background")}


def npc(asset_id, core):
    return {"id": asset_id, "kind": "character", "wave": 2, "rig": True,
            "animations": ["walk", "idle"],
            "prompt": "a %s, %s, %s, %s" % (core, POSE, STYLE, "isolated on plain background")}


def creature(asset_id, core):
    return {"id": asset_id, "kind": "monster", "wave": 3, "rig": True,
            "animations": ["walk", "idle"],
            "prompt": "a %s, %s, %s, %s" % (core, "full body", STYLE, "isolated on plain background")}


def prop(asset_id, core):
    return {"id": asset_id, "kind": "prop", "wave": 5, "rig": False,
            "animations": [],
            "prompt": "a %s, %s, %s" % (core, STYLE, "isolated on plain background")}


QUEUE = [
    # ---- probe (first 3: one of each pipeline shape) ----
    pc("pc_fighter_human", "human male fighter, plate armor, longsword and kite shield"),
    creature("cre_wolf", "dire wolf, on all fours, hackles raised"),
    prop("prop_tavern_table", "sturdy wooden tavern table"),
    # ---- wave 1: party (12 BG3 classes in signature races) ----
    pc("pc_wizard_elf", "high elf male wizard, long robe, gnarled staff, spellbook at hip"),
    pc("pc_cleric_drow", "drow female cleric, chainmail, mace and round shield, holy symbol"),
    pc("pc_rogue_tiefling", "tiefling male rogue, dark leather armor, dual daggers, hood"),
    pc("pc_ranger_elf", "wood elf female ranger, hooded cloak, longbow, quiver"),
    pc("pc_barbarian_halforc", "half-orc male barbarian, fur and hide, greataxe, bare chest"),
    pc("pc_paladin_dragonborn", "dragonborn male paladin, ornate heavy armor, warhammer"),
    pc("pc_warlock_human", "human female warlock, dark tattered robes, eldritch staff"),
    pc("pc_sorcerer_tiefling", "tiefling female sorcerer, draconic scale accents, flowing robe"),
    pc("pc_bard_gnome", "gnome male bard, colorful doublet, lute on back, rapier"),
    pc("pc_druid_elf", "elf female druid, leaf-and-bark garb, wooden staff, antler circlet"),
    pc("pc_monk_githyanki", "githyanki male monk, wrapped forearms, simple gi, bald head"),
    # ---- wave 2: town NPCs ----
    npc("npc_blacksmith", "burly human male blacksmith, leather apron, smithing hammer"),
    npc("npc_merchant", "stout human male merchant, fine tunic, coin purse, scroll"),
    npc("npc_guard", "human male town guard, chain shirt, spear, kettle helm"),
    npc("npc_beggar", "elderly human female beggar, ragged shawl, walking stick"),
    # ---- wave 3: creatures (Tripo-only rig types) ----
    creature("cre_owlbear", "owlbear, bear body with owl head and feathers, on all fours"),
    creature("cre_giant_spider", "giant spider, eight long legs, venomous fangs"),
    creature("cre_intellect_devourer", "intellect devourer monster, a walking brain creature on four clawed beast legs, quadruped, on all fours"),
    creature("cre_raven", "giant raven, wings spread wide, perched"),
    creature("cre_goblin", "goblin warrior, green skin, scavenged armor, scimitar"),
    creature("cre_skeleton", "animated skeleton warrior, rusted sword and battered shield"),
    creature("cre_imp", "small winged imp, leathery wings, barbed tail"),
    # ---- wave 4: boss (4x4 squares, Gargantuan; v3.1 detail) ----
    {"id": "boss_young_red_dragon", "kind": "monster", "wave": 4, "rig": True,
     "animations": ["walk", "idle", "slash"], "model": "v3.1-20260211",
     "prompt": "a young red dragon, massive gargantuan boss, four legs, folded wings, horns, "
               "crimson scales, full body, stylized hand-painted dark fantasy RPG, muted earthy "
               "palette, game-ready, isolated on plain background"},
    # ---- wave 5: props — town ----
    prop("prop_wooden_chair", "simple wooden tavern chair"),
    prop("prop_barrel", "wooden barrel with iron hoops"),
    prop("prop_crate", "wooden shipping crate"),
    prop("prop_market_stall", "wooden market stall with canvas awning"),
    prop("prop_well", "stone water well with wooden bucket and crank"),
    prop("prop_lantern_post", "iron street lantern post, warm glow"),
    prop("prop_hand_cart", "wooden two-wheel hand cart"),
    prop("prop_signpost", "wooden signpost with hanging tavern sign"),
    prop("prop_anvil", "blacksmith anvil on a tree stump"),
    prop("prop_fountain", "stone town fountain with tiered basin"),
    prop("prop_bench", "wooden park bench"),
    # ---- wave 5: props — dungeon ----
    prop("prop_treasure_chest", "ornate wooden treasure chest with iron bands"),
    prop("prop_sarcophagus", "ancient stone sarcophagus with carved lid"),
    prop("prop_iron_gate", "wrought iron dungeon gate"),
    prop("prop_torch_sconce", "wall-mounted iron torch sconce with flame"),
    prop("prop_stone_pillar", "ancient carved stone pillar"),
    prop("prop_rubble_pile", "pile of broken stone rubble"),
    prop("prop_altar", "dark stone ritual altar with carved runes"),
    prop("prop_brazier", "iron brazier with burning coals"),
    prop("prop_wooden_door", "heavy wooden dungeon door with iron hinges"),
    prop("prop_gargoyle_statue", "crouching stone gargoyle statue"),
    prop("prop_bookshelf", "tall wooden bookshelf filled with old tomes"),
    prop("prop_throne", "imposing stone throne on a small dais"),
    prop("prop_portcullis", "iron portcullis gate, half-raised"),
    # ---- wave 6: demo-filler extras (spend-down of remaining balance) ----
    {"id": "boss_ogre_chieftain", "kind": "monster", "wave": 6, "rig": True,
     "animations": ["walk", "idle", "slash"], "model": "v3.1-20260211",
     "prompt": "an ogre chieftain, huge hulking boss, crude spiked club, fur loincloth, bone "
               "jewelry, A-pose, full body, feet on ground, stylized hand-painted dark fantasy "
               "RPG, muted earthy palette, game-ready, isolated on plain background"},
    creature("cre_zombie", "shambling zombie, rotting clothes, arms hanging low, biped"),
    creature("cre_giant_rat", "giant rat, dog-sized, mangy fur, on all fours, long tail"),
    prop("cre_mimic", "mimic monster, a treasure chest with teeth and a long tongue"),
    npc("npc_noble", "haughty human male noble, velvet doublet, rapier, feathered hat"),
    npc("npc_priest", "human male priest, white and gold robes, holy symbol, staff"),
    prop("prop_weapon_rack", "wooden weapon rack with swords and axes"),
    prop("prop_armor_stand", "armor stand displaying a suit of chainmail"),
    prop("prop_stocks", "wooden pillory stocks"),
    prop("prop_candelabra", "tall iron candelabra with five lit candles"),
    prop("prop_standing_torch", "standing iron torch with flame"),
    prop("prop_bed", "simple wooden bed with wool blanket"),
    # ---- wave 7: final spend-down to the balance floor ----
    creature("cre_gnoll", "gnoll, hyena-headed humanoid, scavenged leather armor, spear, biped"),
    creature("cre_harpy", "harpy, winged bird-woman, talons, wings spread"),
    creature("cre_doppelganger", "doppelganger, pale featureless humanoid, smooth blank face, biped"),
    prop("prop_campfire", "stone-ringed campfire with burning logs"),
    prop("prop_dungeon_stairs", "worn stone dungeon stairs descending"),
    prop("prop_barricade", "makeshift wooden barricade with spikes"),
]


def get_key():
    with open(KEY_PATH) as f:
        return f.read().strip()


def balance(key):
    req = urllib.request.Request(
        "https://api.tripo3d.ai/v2/openapi/user/balance",
        headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["data"]["balance"]


def done_ids():
    ids = set()
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.add(json.loads(line)["id"])
    return ids


def _read_meta(out_dir):
    meta_path = os.path.join(out_dir, "metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def _is_complete(item, out_dir):
    meta = _read_meta(out_dir)
    if not os.path.exists(os.path.join(out_dir, "model.glb")):
        return False
    if not item["rig"]:
        return True
    return bool(meta.get("animation_files"))


def _run(cmd):
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dur = round(time.time() - t0, 1)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        return False, dur, tail
    return True, dur, ""


def run_item(item, key, budget_left):
    """Two-stage so each subprocess fits the 300s shell timeout:
    stage A = text generation only (writes model.glb + metadata.json with gen task id)
    stage B = rig --task <gen_task_id> (rigged.fbx + anim_*.fbx)
    Returns a status dict; 'staged' means progress made, re-invoke to continue.
    """
    out_dir = os.path.join(ROOT, item["id"])
    os.makedirs(out_dir, exist_ok=True)
    meta = _read_meta(out_dir)

    if not os.path.exists(os.path.join(out_dir, "model.glb")):
        cmd = [sys.executable, WRAPPER, "text", "--prompt", item["prompt"],
               "--out", out_dir, "--timeout", "240"]
        if item.get("model") != "v3.1-20260211":
            cmd.append("--lowpoly")
        ok, dur, tail = _run(cmd)
        if not ok:
            return {"id": item["id"], "status": "failed", "stage": "gen",
                    "dur_s": dur, "tail": tail}
        meta = _read_meta(out_dir)
        print("[batch]   gen stage done (%ss)" % dur)
        if not item["rig"]:
            return _finish(item, out_dir, meta, dur)
        if time.time() > budget_left:
            return {"id": item["id"], "status": "staged", "stage": "gen", "dur_s": dur}

    if item["rig"]:
        gen_task = meta.get("generation_task_id")
        if not gen_task:
            return {"id": item["id"], "status": "failed", "stage": "rig",
                    "dur_s": 0, "tail": "model.glb exists but no generation_task_id in metadata.json"}
        cmd = [sys.executable, WRAPPER, "rig", "--task", gen_task,
               "--out", out_dir, "--out-format", "fbx", "--timeout", "240",
               "--animations"] + item["animations"]
        ok, dur, tail = _run(cmd)
        if not ok:
            return {"id": item["id"], "status": "failed", "stage": "rig",
                    "dur_s": dur, "tail": tail}
        print("[batch]   rig stage done (%ss)" % dur)
        return _finish(item, out_dir, _read_meta(out_dir), dur)

    return _finish(item, out_dir, meta, 0)


def _finish(item, out_dir, meta, dur):
    files = {}
    for fn in sorted(os.listdir(out_dir)):
        if fn.startswith(("model.", "rigged.", "anim_")):
            files[fn] = os.path.getsize(os.path.join(out_dir, fn))
    row = {"id": item["id"], "kind": item["kind"], "wave": item["wave"],
           "status": "done", "dur_s": dur, "prompt": item["prompt"],
           "files": files, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "gen_task_id": meta.get("generation_task_id"),
           "rig_task_id": (meta.get("rig") or {}).get("task_id"),
           "clips": sorted((meta.get("animation_files") or {}).keys())}
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def main():
    max_seconds = 280.0
    limit = 0
    args = sys.argv[1:]
    while args:
        a = args.pop(0)
        if a == "--max-seconds":
            max_seconds = float(args.pop(0))
        elif a == "--limit":
            limit = int(args.pop(0))
    os.makedirs(ROOT, exist_ok=True)
    key = get_key()
    done = done_ids()
    skip_file = os.path.join(ROOT, "skip.txt")
    if os.path.exists(skip_file):
        with open(skip_file) as f:
            done |= {ln.strip() for ln in f if ln.strip()}
    start = time.time()
    bal = balance(key)
    print("[batch] balance=%d done=%d queue=%d" % (bal, len(done), len(QUEUE)))
    ran = 0
    for item in QUEUE:
        out_dir = os.path.join(ROOT, item["id"])
        if item["id"] in done or (os.path.isdir(out_dir) and _is_complete(item, out_dir)):
            continue
        if limit and ran >= limit:
            break
        if time.time() - start > max_seconds:
            print("[batch] time budget reached; re-invoke to continue")
            break
        if bal < BALANCE_FLOOR:
            print("[batch] balance floor hit (%d); stopping" % bal)
            break
        print("[batch] -> %s (%s, wave %d)" % (item["id"], item["kind"], item["wave"]))
        sys.stdout.flush()
        row = run_item(item, key, start + max_seconds)
        if row["status"] == "staged":
            print("[batch] STAGED %s — %s done; re-invoke to continue" %
                  (row["id"], row["stage"]))
            break
        ran += 1
        if row["status"] == "done":
            total = sum(row["files"].values())
            print("[batch] OK %s — %d files, %.1f MB, %ss" %
                  (row["id"], len(row["files"]), total / 1e6, row["dur_s"]))
        else:
            print("[batch] FAIL %s (%s) — %ss\n%s" %
                  (item["id"], row.get("stage"), row["dur_s"], row["tail"]))
        try:
            bal = balance(key)
            print("[batch] balance=%d" % bal)
        except Exception as e:
            print("[batch] balance check failed: %s" % e)
        sys.stdout.flush()
    print("[batch] invocation done (ran %d this call)" % ran)


if __name__ == "__main__":
    main()
