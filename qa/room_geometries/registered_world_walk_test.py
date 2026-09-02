import os, sys
os.environ["WORLDOS_STATE_DIR"] = "/tmp/regworld_state"
sys.path.insert(0, "servers/engine" if os.path.isdir("servers/engine") else ".")
import server
CID = "registered_world_v1"
c = server._require(CID)
name = {lid: loc.name for lid, loc in c.locations.items()}
cur = c.current_location_id
print("start:", name[cur], "| grid", c.locations[cur].scene_grid.grid.cols, "x", c.locations[cur].scene_grid.grid.rows, "| doors", c.locations[cur].scene_grid.door_cells)
hops = [([7,0],"tavern"), ([7,0],"crypt"), ([15,5],"throne_hall"), ([8,11],"crypt")]
for cell, exp in hops:
    r = server.cross_door(campaign_id=CID, x=cell[0], y=cell[1])
    c = server._require(CID); cur = c.current_location_id
    g = c.locations[cur].scene_grid
    ok = exp in cur
    print(f"cross_door{tuple(cell)} -> {name.get(cur)} [{g.grid.cols}x{g.grid.rows}]", "OK" if ok else f"WRONG (exp *{exp})")
    if not ok: sys.exit(1)
print("REGISTERED-WORLD WALK: crypt->tavern->crypt->throne->crypt ALL OK")
