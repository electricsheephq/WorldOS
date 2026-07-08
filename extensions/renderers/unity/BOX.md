# GEX44 box — connection, claim discipline, and live facts

> Read this alongside `CANONICAL.md` (canonical render state) before any box op. `CANONICAL.md` is
> the "what's the current-best render" doc; this file is the "how do I reach/claim/drive the box"
> doc.

## Connection

- **Box:** `root@46.4.26.123` (GEX44, RTX 4000 Ada / 64GB — the proven primary Unity host).
- **Key:** `~/.openclaw/secrets/evaos-gpu-gex44-1-key`
- **Env:** `~/.openclaw/secrets/gex44.env`
- **SSH flakes = SYN-rate-limit, not a dead box.** Use the ControlMaster pattern (socket
  `/tmp/gex44-cm.sock`) rather than repeated fresh connections. NEVER hammer retries — that is what
  trips the rate limit in the first place.

## Live-claim rule (box is single-tenant)

- Before any box op: **comment on the ACTIVE sprint-charter issue** naming the op you're about to
  run + a start timestamp. Check for an unreleased claim first — an unreleased/stale claim means
  treat the box as occupied and do non-box work instead.
- **Release when done** (a release comment, or a ~30-min inactivity timeout).
- **One box op at a time.** Concurrent paints/builds collide (e.g. concurrent Scenario paints
  silently produce no output).
- **Restore box state after your op** (active plate / active scene) so the next claimant starts
  from a known state.

## Facts (current as of 2026-07-08)

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

## Procedure pointer

For the full drive loop (bring-up, liveness preflight, the unity-mcp raw drive loop, full-res
capture, non-black gate), use the `gex44-unity-host` skill. This file is the durable
connection/claim/facts reference the skill and any cold agent should read first.
