# TOWN SPIKE — generated-town pipeline, end-to-end proof (2026-07-15)

A 4-room town from a DunGen layout, walkable at the engine level, greybox-plated per room.

## Command chain (repeatable)
1. `python3 tools/generate_town.py qa/evidence/dungen-spike/dungen_basic_layout.json \
     --rooms room_0,room_1,room_2,room_3 --town-id oldgate --out-dir /tmp/town`
   → per-room geometry JSONs + oldgate_world.json (reciprocal door pairs) + oldgate_plates_fragment.json
2. `uv run --directory servers/engine python ../../qa/seed_gfx_town.py <state> /tmp/town oldgate`
   → engine world, door_cells[i]↔connections[i] wired
3. per room: build_room_unified.cs (WORLDOS_ROOM_GEO=<room>_geometry.json) → greybox+depth+boxes
   → paint (flux/gemini) → plates_manifest + boxes sidecar

## Proof
- ENGINE WALK: cross_door room_0→room_1→room_2→room_3, all landings correct (walk test log).
- RENDER: all 4 rooms greybox-rendered (town_4room_sheet.png); reciprocal doors + camera-fit per room.
- Files: oldgate_world.json, oldgate_plates_fragment.json, room{0,1,2,3}_greybox.png.

## Remaining for a rendered in-player town walk
Paint the rooms (flux-Modal blocked; greybox-plate interim), wire plates_fragment into a player build's
StreamingAssets, build the macOS player, drive the truth-overlay walk. The GENERATION path is complete.
