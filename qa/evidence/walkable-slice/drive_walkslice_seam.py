import json, os, subprocess, sys, time, urllib.request
ROOT=os.path.expanduser("~/WorldOS-worktrees/wt-walkable-slice")
os.chdir(ROOT)
STATE="/tmp/walkslice_state"; PORT=8790; CID="walkslice_smoke01"; B=f"http://127.0.0.1:{PORT}"
def http(path, body=None):
    url=B+path
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(url, data=data, headers={"Content-Type":"application/json"}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
def eng(code):
    r=subprocess.run(["uv","run","--directory","servers/engine","python","-c",
        "import sys;sys.path.insert(0,'servers/engine')\n"+code],
        cwd=ROOT, env={**os.environ,"WORLDOS_STATE_DIR":STATE},
        capture_output=True, text=True, timeout=120)
    return r.stdout.strip(), r.stderr.strip()
res={}
surf=http(f"/combat-surface?campaign={CID}")
hero=[t for t in surf["stage"]["tokens"] if t.get("rest_role")=="party"][0]["id"]
npc=[t for t in surf["stage"]["tokens"] if t.get("rest_role")=="npc"][0]["id"]
res["1_doors"]=surf["doors"]
res["2_walk"]=http("/move",{"kind":"walk_to_cell","character_id":hero,"x":6,"y":7,"campaign":CID})["ok"]
res["3_parley"]=http("/move",{"kind":"parley_approach","target_id":npc,"character_id":hero,"campaign":CID}).get("ok")
cr=http("/move",{"kind":"cross_door","x":6,"y":0,"campaign":CID})
loc=http(f"/combat-surface?campaign={CID}")["location"]["id"]
res["4_cross_ok"]=cr.get("ok"); res["4_new_location"]=loc
sc=http("/move",{"kind":"start_combat","campaign":CID})
res["5_start_combat_ok"]=sc.get("ok"); res["5_combatants"]=sc.get("combatants")
# advance any opening monster turns to the PC (engine runs hostile turns; stops at the PC)
out,err=eng(f"import combat_loop;r=combat_loop.run_combat_round('{CID}',mode='live');print('RAN')")
cs=http(f"/combat-surface?campaign={CID}")
al=[t for t in cs["tokens"] if t["team"]=="ally"][0]; go=[t for t in cs["tokens"] if t["team"]=="foe"][0]
res["6_combat_tokens"]=[(t["name"],t["team"]) for t in cs["tokens"]]
tt=cs["turnToken"]; cur=[t for t in cs["tokens"] if t.get("isCurrent")]
res["6_current"]=cur[0]["name"] if cur else None
# ensure PC turn; move adjacent to goblin then attack
if cur and cur[0]["team"]=="ally":
    adjx,adjy=go["x"]-1,go["y"]
    mv=http("/move",{"kind":"move_to_cell","x":adjx,"y":adjy,"turn_token":tt,"campaign":CID})
    cs=http(f"/combat-surface?campaign={CID}"); tt=cs["turnToken"]
    hp0,_=eng(f"import server;print(server._require('{CID}').characters['{go['id']}'].current_hp)")
    atk=http("/move",{"kind":"attack","target_id":go["id"],"turn_token":tt,"campaign":CID})
    time.sleep(0.5)
    hp1,_=eng(f"import server;print(server._require('{CID}').characters['{go['id']}'].current_hp)")
    res["7_move_ok"]=mv.get("ok"); res["7_attack_ok"]=atk.get("ok")
    res["7_goblin_hp"]=f"{hp0}->{hp1}"; res["7_hp_dropped"]=int(hp1)<int(hp0)
else:
    res["7_error"]=f"not PC turn after live-advance: {res['6_current']}"
print(json.dumps(res,indent=1))
