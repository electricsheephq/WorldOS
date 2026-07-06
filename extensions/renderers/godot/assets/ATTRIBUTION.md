# `extensions/renderers/godot/assets` — attribution & licenses

Only **CC0 / public-domain / WorldOS-original** art may live in this committed tree (see
`LICENSE-TIERS.md`). Copyrighted, paid-AI-final, or Blender-render finals stay **gitignored**
under `content/worlds/_private/` and are served (never committed) via the `/image` bridge.
One row per committed binary asset; the CI license gate (#1064) enforces this (mirrors
`viewer/openworlds/assets/icons/game-icons/` + `scripts/license_check.py:_check_game_icons_attribution`).

| File | Source | License | Notes |
|------|--------|---------|-------|
| `characters/aubree/sheet.png` | WorldOS original (generated) | CC0-1.0 | 8-facing directional **placeholder**; regenerate via `extensions/renderers/godot/tools/gen_placeholder_sheet.py`. Finals (AI-paintover / Blender-render / a CC0 pack such as the Hormelz 8-Directional Knight) drop in at the same `scope_key` with zero renderer change. |
| `props/pillar/sheet.png` | WorldOS original (generated) | CC0-1.0 | Single-frame occluder **placeholder** for the Y-sort test; regenerate via `extensions/renderers/godot/tools/gen_placeholder_sheet.py`. |

The accompanying `sheet.json` manifests and `extensions/renderers/godot/tools/gen_placeholder_sheet.py` are
WorldOS-original source (CC0-1.0) — the manifest, not the pixels, is the durable contract.
