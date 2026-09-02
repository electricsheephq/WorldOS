# GEX44 retired

GEX44 (`evaos-gpu-gex44-1`, `46.4.26.123`) was the dedicated Unity/GPU render and heavy-QA box;
the owner retired and discarded it on **2026-08-06**. Its SSH paths, `/home/unity` project,
`gex44-unity-host` skill, `tools/gex44-box` helpers, and `worldos-unity-save.sh` cron are historical only.

## Local replacements

| Retired surface | Local replacement |
|---|---|
| Unity 6000.5.1f1, `/home/unity/worldos-unity`, HTTP `:8080` | Unity 6000.5.6f1 at `/Users/m1/worldos-unity`; Stdio bridge `extensions/renderers/unity/tools/mcp_stdio_exec.py` |
| Box build and QA endpoints | `execute_menu_item "Tools/WorldOS/Build/macOS Player (Universal)"`; `qa/qa_sandbox.py` on 8866/8972 (owner 8776/8981) |
| Box captures | Unity MCP `manage_camera` screenshot recipe |
| Save tarball / cron | Repo commits plus `/Users/m1/Codex/worldos-unity-mirror` |

## Historical-reference policy

Dated changelogs, evidence, scorecard rows, decision records, and session narratives retain their
GEX44 wording as provenance. Live runbooks and skills must point to the local replacements above or
carry a one-line retirement banner; no document should instruct an agent to contact the retired box.

## Guarded entrypoints (the live runners are disabled)

Every remaining runner that would have contacted the box now refuses by default: it prints
`GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md` on stderr and exits **2** unless
`WORLDOS_ALLOW_RETIRED_HOST=1` is set (forensic/salvage use on a non-GEX44 host only — it is an
escape hatch, not a supported lane). Guarded today:

- `qa/deploy_room.sh`, `qa/gen_dungeon.sh`, `qa/gen_room_from_scene_grid.sh`,
  `qa/validate_active_room_framing.sh`, `qa/drive_gfx_combat.py`
- `tools/gex44-box/ops/launch_editor.sh`, `tools/gex44-box/ops/setup_depth.sh`,
  `tools/gex44-box/ops/worldos-unity-save.sh`, `tools/gex44-box/ops/derive_gbuffer.py`
- `tools/gex44-box/comfyui-detail-finisher/tile_detail.sh`,
  `tools/gex44-box/display-config/gex44-display-profile`,
  `tools/gex44-box/display-config/unity-desktop.sh.4k-readable`

Their `docs/RUNBOOK-INDEX.md` rows are marked HISTORICAL and point at the local successor
(`docs/ROOM-PIPELINE-RUNBOOK.md` → "G1 GATE RECIPE").
