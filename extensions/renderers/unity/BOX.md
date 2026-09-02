# GEX44 box — connection, claim discipline, and live facts

> **GEX44 retired 2026-08-06 — see `docs/roadmap/NOW.md`; do not run the historical procedures below.**
> Local equivalent: Unity 6000.5.6f1 at `/Users/m1/worldos-unity` via
> `extensions/renderers/unity/tools/mcp_stdio_exec.py`; build with the WorldOS macOS menu command,
> capture with `manage_camera`, QA via `qa/qa_sandbox.py` (8866/8972; owner 8776/8981).

## Connection

- **Box:** `root@46.4.26.123` (GEX44, RTX 4000 Ada / 64GB — the proven primary Unity host).
- **Key:** `~/.openclaw/secrets/evaos-gpu-gex44-1-key`
- **Env:** `~/.openclaw/secrets/gex44.env`
- **SSH flakes = SYN-rate-limit, not a dead box.** Use the ControlMaster pattern (socket
  `/tmp/gex44-cm.sock`) rather than repeated fresh connections. NEVER hammer retries — that is what
  trips the rate limit in the first place.

## Historical live-claim rule (retired)

- Before any box op: **comment on the ACTIVE sprint-charter issue** naming the op you're about to
  run + a start timestamp. Check for an unreleased claim first — an unreleased/stale claim means
  treat the box as occupied and do non-box work instead.
- **Release when done** (a release comment, or a ~30-min inactivity timeout).
- **One box op at a time.** Concurrent paints/builds collide (e.g. concurrent Scenario paints
  silently produce no output).
- **Restore box state after your op** (active plate / active scene) so the next claimant starts
  from a known state.

## Historical facts (as of 2026-07-08)

- **Unity MCP on :8080 now exposes 29 tools with NO `execute_code`.** To run arbitrary editor code,
  use `create_script` with a `MenuItem` wrapper: create the script → `refresh_unity` +
  `read_console` to confirm it compiled → `execute_menu_item` to run it → `delete_script` to clean
  up. Do not assume a prior `execute_code`-based workflow still applies.
- **:8765 is a reverse-forward to the Mac bridge — NEVER bind it.** (`ssh -O forward -R
  8765:127.0.0.1:8770` from the box side; this is Eva's bridge, not a free port.)
- **Screenshots** via `manage_camera` with `screenshot_super_size` 2–4; captures land in
  `Assets/Screenshots/`.
- **Plate resolution is NAME-KEYED.** `location:<loc_id>` only resolves art for **slug** ids (e.g.
  `loc-lower-city`) — a **hash** id (`loc_<hex>`) renders a bare grid, not the intended plate. Check
  the location's id shape before assuming a missing-art bug.

## Historical procedure pointer

For the full drive loop (bring-up, liveness preflight, the unity-mcp raw drive loop, full-res
capture, non-black gate), use the `gex44-unity-host` skill. This file is the durable
connection/claim/facts reference the skill and any cold agent should read first.

## Historical bridge re-arm (session dropped) — PROVEN recovery, no VNC
Symptom: MCP calls return `no_unity_session` while the editor process is alive and
`curl 127.0.0.1:8080/mcp` returns 406 (server healthy). The editor↔server SESSION dropped.
Fix (headless, over SSH as the `unity` user on DISPLAY=:0):
1. `scrot -o /tmp/s.png` → scp back → locate the "MCP For Unity" window (red "No Session" + Connect).
2. `xdotool mousemove <titlebar-x> <titlebar-y> click 1` — focus the window first (openbox needs it).
3. `xdotool mousemove <connect-x> <connect-y> click 1`, sleep ~5.
4. Verify: panel shows green "Session Active (worldos-unity)"; a `read_console` probe returns entries.
Do NOT restart the editor or the :8080 server for this symptom, and do not wait for a human VNC
connect — ssh+xdotool is the proven agent-control path on this box.
