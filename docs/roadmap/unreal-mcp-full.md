# EPIC (BACKLOG) — "Unreal MCP Full"

> **Status: PARKED / FUTURE.** Deferred, not rejected. This is the durable record of the
> Unreal-Engine-MCP integration research (2026-06-20) so we can return to it without re-deriving.
> It is a **future fidelity escalation** + an **alternative dev-time asset factory** for WorldOS's
> graphics phase — NOT the current spine. The current spine is the AI-built **isometric grid CRPG**
> (see `WORLDOS-GRAPHICS-ROADMAP.md`). Re-open this epic only after that ships and a concrete product
> reason (a high-end 3D tier, or a UE-driven asset pipeline) is in front of us.

## Why this exists

The graphics phase opened with "integrate Unreal Engine MCP into WorldOS." Deep research showed Epic's
UE MCP is an **editor-authoring** surface, not a runtime renderer — valuable, but the wrong backbone for
shipping a coordinate-free, theater-of-the-mind, narrative engine to all licensees' players *now*. The
owner's underlying excitement — *"AI builds towns / cities / assets in 3D"* — is correct and lives on,
but as a **dev-time asset factory** whose output bakes down to the shippable 2D/3D-on-2D isometric
runtime (the Baldur's-Gate / Pillars production technique), not as the player-facing client.

## What Epic's UE 5.8 MCP actually is (load-bearing facts)

- An **MCP server embedded in the running Unreal _Editor_** process. An MCP *client* (Claude Code,
  Cursor, the MCP Inspector) connects and calls tools to **build/edit content**: spawn actors, set
  transforms / labels / parent-child / components, configure lighting, create material instances,
  inspect Slate widgets, run automation tests.
- **Transport: HTTP + Server-Sent Events only**, default `http://127.0.0.1:8000/mcp`. **No stdio, no
  WebSocket.** Client config (`ModelContextProtocol.GenerateClientConfig ClaudeCode`):
  ```json
  { "mcpServers": { "unreal-mcp": { "type": "http", "url": "http://127.0.0.1:8000/mcp" } } }
  ```
- **No authentication.** Loopback-only by default; rejects non-loopback `Origin`. Tool calls are
  **serialized onto the UE game thread** (slow under load). MCP Resources/Prompts are not advertised.
  Adding a tool requires an **editor restart**. Marked **Experimental**; **version-locked** (a 5.8 build
  won't load in 5.6/5.7); "many features incomplete, APIs subject to change."
- **Cooked/shipping builds CAN host an MCP server** via `IModelContextProtocolModule::StartServer()` at
  startup (the Toolset-Registry auto-discovery adapter is editor-only; shipping builds register tools
  explicitly). → a *shipped* UE client could expose MCP at runtime for an in-client AI director.
- **Third-party UE MCP servers** are broader + hardened (e.g. a C++ Automation Bridge server: ~370+
  tools across 54 categories, `run_tool_script` multi-step-one-undo transactions, `describe_graph`
  Blueprint read-back, bearer auth / BindAddress / DNS-rebind defense, catalog-mode ~3K-token overhead).
  Prefer one of these over the first-party plugin if/when we build the authoring tier.
- **It is NOT a runtime renderer** you push game-state into; it is an authoring/automation surface.

## The two orthogonal axes any UE integration must pick

- **Axis A — MCP direction:**
  - **(A) WorldOS-drives-Unreal** — Claude is the MCP *client*, the UE Editor is the *server*; an AI
    "set-dresser/director" **builds** 3D scenes. *Epic-MCP-native.*
  - **(B) Unreal-renders-WorldOS** — a UE app is a *client of WorldOS's existing HTTP surfaces*
    (`/atlas-surface`, `/combat-surface`, `/session-surface`) and **draws** them, POSTing player intents
    to `/move`. *Epic's MCP is barely involved — this rides the `render-profile` seam.*
- **Axis B — where pixels are produced / who needs hardware:**
  - **Dev-time** (capture to images/video → existing web viewer shows them; **players need nothing**).
  - **Player-local** (player runs the UE client; **needs a GPU** → cannot be the "everyone" baseline).
  - **Cloud-rendered + Pixel Streaming** (cloud GPU renders UE, streams WebRTC to **any browser** — same
    surface OpenWorlds already is; reaches everyone; ~$0.18–0.53/user-hr).

**Anchoring principle (all tiers):** WorldOS stays the **sole writer** of game state; UE is *always* a
downstream presentation/authoring consumer, never a writer. Authoring tier → UE receives build commands.
Rendering tier → UE reads surfaces + posts constrained `/move` intents like any client.

## Fit dossier (External-OSS scoring)

```
Direct adoption (UE MCP into the live play loop, player-facing, as-is):   2/10
Wrapper/sidecar (render-agent client + cache; dev-time capture / demo rig): 6/10
Port (lift UE MCP code into WorldOS):                                      1/10  (protocol server; nothing to port)
Inspired/native rebuild (a `unreal` renderer that reads our surfaces):    6/10  (real long-term path; uses render-profile, not Epic MCP)
```

## Milestone ladder (deferred — return here when re-opened)

- **U0 — Contract reservation.** Reserve a `unreal` block under `render-profile.renderer_profiles`
  (spec-only, like the reserved `rpgmaker`). Additive; no engine change.
- **U1 — Authoring spike** *(Axis A, dev box; UE-5.8-capable GPU machine, NOT the 16 GB Mac).* A one-shot
  **render-agent** (separate process, **never the DM** — keep off the beat-latency path) reads a frozen
  `/combat-surface` + `/session-surface` and issues `spawn_actor` / set-transform / `configure_lighting`
  to assemble a diorama from a small D&D asset pack. Measure tool-call count, latency, $, fidelity.
- **U2 — Dev-time capture provider behind `imagegen`** *(Axis A, captured; players need nothing).* A
  `unreal` provider (selected by `WORLDOS_IMAGE_PROVIDER`) triggers U1-style authoring + a high-res
  capture (still → short clip), writes a descriptor into the existing `state_dir/images|videos` cache;
  the web viewer shows it via `/image`, unchanged. Richer art than diffusion, zero player hardware.
- **U3 — (covered by the main plan)** isometric web/native baseline — **no UE**.
- **U4 — Live authoring loop** *(Axis A, live demo rig).* Render-agent subscribes to `/surface-stream`
  (SSE) and incrementally re-dresses the UE scene per beat. Wow-factor; per-beat cost/latency is the risk
  (mitigate with batching / `run_tool_script` transactions / a third-party UE MCP).
- **U5 — Shippable 3D client** *(Axis B; optionally A at runtime via `StartServer()`).* (5a) player-local
  GPU, or **(5b) cloud GPU + Pixel Streaming → WebRTC to any browser** (reaches all licensees' players;
  embeds in an OpenWorlds screen). Custom UE build consuming our surfaces — Epic's MCP not the mechanism.
- **U6 — = the main plan's grid (#461).** Engine coordinate authority benefits *every* renderer; promoted
  out of this epic into the active graphics plan as "Track A — engine grid authority."

## Dependencies, costs, and traps

- **Content/asset pack** is the dominant real cost — `spawn_actor` references **assets that already exist**
  in the UE project; the MCP supplies the verb, not the art. A D&D mesh/material/skeletal/animation library
  is required for any authoring tier. (Meshy / asset-store can seed it.)
- **Hardware/distribution** — local UE needs a GPU → cannot be the "everyone" baseline; **Pixel Streaming**
  is the only universal path for live 3D.
- **UE EULA** — free under **$1M** lifetime revenue attributable to the UE product, then **5% royalty**
  (3.5% if Epic-Store-first; royalty-free on EGS); seat licenses only for *non-game* commercial use. A
  **flag for licensees shipping a UE client — not legal advice / not legal clearance.**
- **Latency** — keep any UE work off the DM beat-latency critical path (`worldos-latency-forensics`).
- **AI-content disclosure** — populate `render-profile.core.ai_disclosure` (EU machine-readable disclosure
  effective 2026-08-02; Steam AI-content survey) on any UE-generated art.
- **Experimental / version-locked / no-auth** Epic plugin — pin UE 5.8; prefer a hardened third-party UE
  MCP for breadth/auth/transactions if we build the authoring tier.

## Why parked (decision record)

Editor-authoring nature + experimental/version-lock + no-auth + WorldOS's coordinate-free state model +
hardware/distribution reality make UE the wrong *spine now*. It returns as a **fidelity escalation** (a
high-end 3D tier via Pixel Streaming) and/or a **dev-time asset factory** (UE-rendered art baked into the
2D/3D-on-2D isometric runtime) once the AI-fed isometric grid CRPG is proven. Reference, not commitment.
