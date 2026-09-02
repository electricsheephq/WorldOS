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
