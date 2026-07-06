---
name: wire-external-api-service
description: Wire a new external AI/API service (and its MCP) so future agents can reliably use it — secure keys, a CLI wrapper, an optional engine provider, and a service skill. Use when integrating ANY new external generation / API / MCP service (image, 3D, audio, data) that agents will call — storing credentials, registering an MCP, building a wrapper, or adding an image/asset provider. Encodes the probe-auth-first discipline, the key-security pattern, and the subagent-MCP-inheritance gap.
---

# Wire an external API / MCP service for agents

The repeatable recipe (used to add Meshy / Tripo3D / Scenario / PixelLab in one pass). Three
non-obvious facts up front — each one saved or would have saved real time:

1. **Probe auth BEFORE building.** Public docs *and your own research* are wrong roughly half the
   time on the auth scheme and base URL. `curl` a cheap endpoint with each plausible scheme and
   trust only what returns `200`/`404` (vs `401`/`403`). *(Real catch this session: Scenario is HTTP
   **Basic** at `api.cloud.scenario.com`, not Bearer at `api.scenario.com`.)*
2. **Keys live OUTSIDE the repo.** `~/.worldos/<service>.key` (mode 600), never committed; env
   fallback `WORLDOS_<SERVICE>_API_KEY`. ⚠ if a key was pasted into a chat, flag it for rotation.
3. **A subagent does NOT inherit user-scope MCPs.** A freshly-spawned agent can't rely on
   `claude mcp add` registrations, so the **CLI wrapper is the portable path**. (Corollary:
   adversarial subagents will falsely report "MCP not registered" — a context artifact; verify in
   the main session with `claude mcp list`.)

## The 8 steps
1. **Probe auth** — `curl -s -o /dev/null -w "%{http_code}"` with `-H "Authorization: Bearer …"` AND
   `-u key:secret` AND no-auth, against a cheap GET. Nail base URL + endpoint + scheme first.
2. **Store the key** — `printf %s '<key>' > ~/.worldos/<svc>.key && chmod 600 ~/.worldos/<svc>.key`
   (+ a `.secret` file if the service needs two). Confirm mode 600. Never echo the key into a
   committed file or a log.
3. **CLI wrapper** — mirror an existing one (`extensions/renderers/godot/tools/meshy_gen.py`): urllib (no `requests`
   dependency), key from `~/.worldos/` or `WORLDOS_<SVC>_API_KEY`, async create → poll → download,
   atomic write to a **gitignored** output dir. MUST have `--test-key` (live auth probe → "Auth OK")
   and `--dry-run` (plan + est. cost, no call).
4. **Flag parity** — every service wrapper gets the SAME `--test-key` / `--dry-run` (don't leave one
   the odd-one-out — fresh agents expect uniformity).
5. **Register the MCP at USER scope** (if the service has one) — `claude mcp add --scope user
   --transport http <name> <url> --header "Authorization: <scheme>"`. It lands in `~/.claude.json`
   (NOT the project `.mcp.json`, which is the runtime plugin's engine/rules/voice servers). Confirm
   `claude mcp list` → ✔ connected.
6. **Engine provider (optional)** — if the engine has a provider Protocol
   (`servers/engine/imagegen.py` `ImageProvider`), add a `<Svc>Provider`: implement `configured()`,
   register in `_HOSTED`, **degrade to null** on unconfigured/offline (never raise), select via the
   env switch, keep the default null. (This is how you get a real provider that doesn't touch Eva's
   gateway.)
7. **Secret-scan the diff** — `git diff --cached | grep -iE '<key-prefixes>'` MUST be empty before commit.
8. **Document it in a service skill** — the routing (which service for which job), the VERIFIED API
   facts (scheme, base URL, endpoints, cost), key locations, wrapper + MCP usage. For WorldOS asset
   services this is the **`asset-gen`** skill — extend it rather than making a new one.

## Verify
Each wrapper's `--test-key` (live) → "Auth OK"; `claude mcp list` shows the new MCP connected;
secret-scan clean. A cold agent should reach the service via the CLI wrapper with zero setup beyond
the key file.

> Generalizes beyond WorldOS — promote to a global skill (`~/.claude/skills/`) if you integrate APIs across repos.
