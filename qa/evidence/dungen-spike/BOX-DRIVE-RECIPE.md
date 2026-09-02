# DUNGEN-SPIKE — box drive recipe (run when the #1386 claim frees)

Repo-side is DONE + green (PR #1509). This is the ready-to-run box phase: generate ONE small dungeon,
export it, convert, greybox-render, then one room through the registered plate pipeline. The DunGen API
is already validated against the installed source, so this is near-mechanical.

**Box:** `root@46.4.26.123`, key `~/.openclaw/secrets/evaos-gpu-gex44-1-key` (never print), ControlMaster
`/tmp/gex44-cm.sock`, bounded ssh. On exit: `chown -R unity:unity` any files touched, ctrl+r (refresh
Unity), restore the scene, RELEASE the claim on #1386.

## Confirmed on the box (2026-07-11, read-only probe)
- DunGen imported: `Assets/DunGen/` (Code + Samples asmdefs).
- Ready minimal tileset / DungeonFlow: **`Assets/DunGen/Samples/Basic/Basic Sample Dungeon.asset`**
  (fastest to rig — a complete flow with Start/Goal/Cap/Castle tile sets). Synty POLYGON Dungeon is also
  present (`Assets/Synty/PolygonDungeonMap`) if a nicer tileset is wanted, but Basic Sample is fastest.

## Steps
1. **Deploy the exporter** to the Unity project's Editor folder:
   `scp` `extensions/renderers/unity/scripts/Editor/DunGenLayoutExporter.cs` →
   `/home/unity/worldos-unity/Assets/Editor/DunGenLayoutExporter.cs`; `chown unity:unity`; ctrl+r; wait for
   compile (read_console clean).
2. **Generate + export** via unity-mcp `execute_code` (or menu):
   ```csharp
   return WorldOS.Editor.DunGenLayoutExporter.Export(
       "Assets/DunGen/Samples/Basic/Basic Sample Dungeon.asset",
       "/home/unity/worldos-unity/dungen_layout.json", 12345);
   ```
   Expect `OK: N rooms, M doorways, K props -> ... (status=Complete)`. If N is large, shrink via the flow's
   Length setting (or accept it and pick one small room downstream). Pull the json back:
   `scp` `/home/unity/worldos-unity/dungen_layout.json` → `qa/evidence/dungen-spike/`.
3. **Convert** (locally or on box):
   ```bash
   python3 tools/dungen_to_fixtures.py qa/evidence/dungen-spike/dungen_layout.json \
       --out-dir qa/evidence/dungen-spike --name dungen_basic --room room_0
   ```
   Pick the smallest 3-4-room slice; use `--room <id>` for the per-room plate input.
4. **Greybox render** (proves geometry flows): `qa/greybox_render_headless.py
   qa/evidence/dungen-spike/dungen_basic_room_0_geometry.json qa/evidence/dungen-spike/dungen_basic_room_0_greybox.png`
   — or the box's `build_room_greybox.cs` for a lit depth/normal capture (the plate base).
5. **Plate pipeline** (one room) per `docs/roadmap/PLATE-RECIPE-DECISION.md`: flux depth-CN base from the
   greybox → Gemini style pass (STRUCTURE-LOCK + DIMETRIC-LOCK) → registration gate (edge-recall ≥0.95
   for masonry) → stage the 5-scorer blind panel via `qa/plate_loop.py`. No `referenceImages` unless a
   greybox-aligned anchor exists (THE REFERENCE-IMAGES LAW).
6. **Panel:** run the 5-scorer blind panel (orchestrator), ingest the verdict:
   `python3 qa/plate_loop.py --panel-verdict <verdict.json> --out-dir <dir> --gallery <html>`.
7. **Evidence:** drop dungen_layout.json, the fixtures, greybox, plate candidate, registration number, and
   panel_verdict.json into `qa/evidence/dungen-spike/`. Append the room's registration + panel numbers +
   end-to-end wall-clock + the adopt/iterate/prefer-Tessera verdict to the PR.
8. **Restore + release:** chown unity:unity, ctrl+r, reload the prior scene, comment RELEASE on #1386.
