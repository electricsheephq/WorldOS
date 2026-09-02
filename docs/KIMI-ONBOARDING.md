# KIMI-ONBOARDING — Kimi's routing doc into WorldOS

> Written 2026-07-20 by the Kimi orchestrator after a full 8-agent recon sweep.
> **Status: tracked — landed via PR #1624 (closes issue #1623).** It is the Kimi-side entry point; Claude-side agents
> can read it as-is.
> **Decision (owner-asked):** we *use and link to* the existing Claude-side documentation — it is live-edited
> within days, so duplicating it would stale immediately. This file is routing + interop glue only.

---

## 1. Where everything lives

| Thing | Path / identity | Notes |
|---|---|---|
| Canonical checkout | `/Users/m1/WorldOS` | GitHub `electricsheephq/WorldOS`; local branch **`base` tracks `origin/main`**; tip `fd23e972` (dress_focal v2 / #1621, 2026-07-16) |
| Live worktrees | `/Users/m1/repos/WorldOS-worktrees/` | `wt-owner-play` — owner's play copy, branch `main` @ `c8a0b155`, **`.worldos-keep` = never touch**; behind main → `git pull --ff-only origin main` BEFORE any owner reseed (schema-skew class, OPERATIONS.md). `walk-gate-hardening` — clean, @ `92edf8d7`, looks merged; only its owning lane may prune it |
| Stale clone (trap) | `/Volumes/LEXAR/repos/WorldOS` (historical path — /Volumes/LEXAR/repos/WorldOS; that machine is gone, see NOW.md) | Jul-1 clone @ `76439e2`, ~19 days behind. Do not mistake for canonical |
| Session scratch/evidence | `/Users/m1/Codex/session-notes` | Newest: `walkability-fix-2026-07-16/`, `ship-2026-07-15/` |
| Unity player install | `/Users/lume/Applications/WorldOSPlayer.app` (historical path — /Users/lume/Applications/WorldOSPlayer.app; that machine is gone, see NOW.md) | Jul 16 12:16 build |
| Built app | `dist/` — **EMPTY right now** (no `dist/WorldOS.app` exists) | AGENTS.md defines the built app as the product; last app evidence early July |
| GEX44 backups | `/Volumes/LEXAR/Codex/worldos-unity-backups/` (historical path — /Volumes/LEXAR/Codex/worldos-unity-backups/; that machine is gone, see NOW.md) | `worktree-20260716.tgz` confirmed |

## 2. Reading order (doc routing)

1. `docs/OPERATIONS.md` — the bootstrap (fresh, Jul 16). Universal Run Contract: every run closes HEALTH → EVIDENCE → SCORE → VERDICT → POINTER.
2. `VISION.md` — pillars + load-bearing invariants + decision-by-eval.
3. `docs/ACTIVE-GOAL.md` — the standing driver + blocker law (policy is ageless, blocker table stale by construction).
4. `docs/roadmap/PRODUCT-ROADMAP.md` — the plan (durable); its §4b execution-state block is stale (Jul 8).
5. **`docs/roadmap/PROCEDURAL-SCORECARD.md` (Jul 16) — the freshest narrative truth** for the generator chain; narrates the tip events (dress_focal v2/#1618, the wing hardening, the dwing-wing 0-adopted close-out).
6. `docs/ROOM-PIPELINE-RUNBOOK.md` (Jul 16) — the 11-step room pipeline; §10b sandbox hot-load gate loop; §11 walkability ship gate (BOTH beauty panel AND `qa/walk_test.py` GREEN to ship).
7. `qa/PANEL-PROTOCOL.md` (Jul 16) — the versioned blind-panel ruler; nobody freehands the ruler; author never renders own verdict.
8. Active sprint charter: GitHub issue **#1386** (Act II close-out, Rendered Felt) — box claim queue lives in its comments.
9. `docs/roadmap/NOW.md` — **refreshed to the 2026-07-20 truth in PR #1624** (the Jul-9 staleness is resolved); it is again the you-are-here surface. Keep it current per its session-close contract (update at every session close / charter transition).
10. Live plan (Claude-side but plain markdown): `~/.claude/plans/bubbly-cooking-stallman.md` — read **lines 596–668 first** (final log + queue); the CURRENT STATE block at 669+ is an older anchor.

## 3. Live state snapshot (verified 2026-07-20)

- **Generator chain merged & staged on main**: 13 PRs — spine (#1604–#1609, incl. sha-pinned certifications #1607 + ledger walk surface #1608), generator (#1610/#1611/#1621), instruments (#1613–#1616). dress_focal v2 gives every generated room 3 non-collinear fire beacons; paint has an `err_cells ≤ 0.35` hard gate with similarity re-registration; walk gates are fire-masked tri-state (GREEN/RED/ERROR).
- **Task #76 (next-cycle packet) is IN FLIGHT** — stage 1 (beacon regen) opened as PR #1625; the render → paint → gate half remains — see §4.
- **QA truth**: `qa/RRI.json` = RRI 2.7, release_ready=false, 3/11 gates, 1-of-5 personas — partial/harness-contaminated and unsuperseded (no valid release verdict exists). Scores ledger: 117 runs, visual-only since Jul 10. Walk-GREEN: crypt, tavern, throne_hall (live) + shop, tavern_snug (pinned certs in `qa/certifications/`). dwing wing: 0-adopted / 3 honest negatives (owner-falsified adoptions → "instruments adjudicate eyeballs").
- **Live campaign**: `play-state/app-gate-v105-b/campaigns/camp_280cfd4d22a0` — DM budget-stopped after 2 turns (Jul 8).
- **Open PRs that matter**: #1617 (Unity persistence runbook — its OPERATIONS.md sections are NOT in base yet), #1498 (outdoor LoRA, blocked on Scenario train-model quota — owner decision), #1298 (day-plate selection, intentionally unmerged pending owner), #1012 draft (slab tiering, A/B-gated), #1622 (dependabot).
- **GEX44**: reachable, up 19 days, near-idle (probed 2026-07-20). Unity project saved via LFS + autosave cron; **GitHub LFS push blocked on paid data pack (~$5/mo owner billing decision)** — local commits + LEXAR tarball are the save story meanwhile.

## 4. The next-cycle packet (Task #76 — the staged opener)

Source: `~/.claude/tasks/237280f0-e8fe-4529-bb6e-72957d537c61/76.json` (status: pending) + GitHub issues #1618/#1619/#1620 + plan STATE block. All paths verified to exist.

1. **Regen** the wing with dress_focal v2 via `tools/generate_town.py` (staged on main @ `fd23e972`).
2. **Local Unity render ×3** per ROOM-PIPELINE-RUNBOOK §3 (greybox render — the shaded base + optional depth/normal sidecars) via `extensions/renderers/unity/tools/mcp_stdio_exec.py`; batch the #1616 T-pose registry-sync + player rebuild into the same local build; use the Unity menu build command and commit the source changes here.
3. **Paint** via `qa/paint_room.py --boxes` (err_cells hard gate auto-warps via the similarity fit).
4. **Hot-load sandbox gates** per runbook §10b (fire-masked, tri-state; cycle `current_room` per room).
5. **Blind-adjudicated verdicts** per `qa/PANEL-PROTOCOL.md` — panels via `qa/panel_workflow.mjs` with `CAL_shipped_shop` calibration reference.
6. Adopt or park per verdict; certifications + `record_room_walk` on adoption.

- Companions: **#1619 render_recipe** (zero-CU code; consider landing BEFORE the repaint — it kills recipe-authoring bugs) and **#1620** experience gates.
- **Budget gate**: the cycle costs ~140 Scenario CU; ~160 CU remained as of 2026-07-16 19:00 local with a measured 2/6 bug-fix-repaint base rate → the previous orchestrator deliberately handed off rather than strand it mid-budget. **Update 2026-07-20: Scenario CU refilled (5k+) — the "needs fresh budget" blocker is cleared; still confirm the balance before starting step 3.**

## 5. GEX44 retired / local Unity lane

- **GEX44 was retired 2026-08-06.** Do not use its endpoint, `/home/unity`, ControlMaster,
  `gex44-unity-host`, or `worldos-unity-save.sh` references.
- Use local Unity 6000.5.6f1 at `/Users/m1/worldos-unity` (mirror `/Users/m1/Codex/worldos-unity-mirror`)
  through `extensions/renderers/unity/tools/mcp_stdio_exec.py`; build with
  `execute_menu_item "Tools/WorldOS/Build/macOS Player (Universal)"`,
  capture with `manage_camera`, and run QA via `qa/qa_sandbox.py` (8866/8972; owner 8776/8981).
- Commit renderer source here; `support-vm-1` is only the heavy-backend-sweep fallback.

## 6. Skills & agent profiles — porting decision (read-on-demand, NO symlinks/copies)

The skill surface is live-edited (worldos-dev touched Jul 15, blind-adjudicator born Jul 16); copies stale within days and symlinks would mis-register foreign files as Kimi-managed skills. Kimi reads absolute paths natively — so:

| Surface | Path | How Kimi uses it |
|---|---|---|
| Dev loop (canonical) | `/Users/m1/WorldOS/.claude/skills/worldos-dev/SKILL.md` | Read before any dev loop |
| Decision apparatus | `/Users/m1/WorldOS/.claude/skills/worldos-decide/SKILL.md` | Read when a decision lacks an eval |
| Local Unity lane | `extensions/renderers/unity/CANONICAL.md`, `extensions/renderers/unity/tools/mcp_stdio_exec.py` | Read before local Unity work |
| Visual critic / asset gen | `/Users/m1/WorldOS/.claude/skills/visual-critic/SKILL.md`, `.../asset-gen/SKILL.md` | Read on those lanes |
| Blind adjudicator | `~/.claude/agents/blind-adjudicator.md` | **Port the prompt verbatim** into a Kimi read-only subagent (`plan`/`explore` type) for gate verdicts — self-contained, model-agnostic |
| Other agent profiles | `~/.claude/agents/{coder,deep-reasoner,fast-worker,codex-worker}.md` | Map frontmatter to Kimi subagent types (`coder`/`plan`/`explore`) |
| WorldOS memory index | `~/.claude/projects/-Users-lume/memory/worldos-index.md` | Read on any WorldOS resume |
| Ops runbook | `~/.claude/runbooks/worldos-evaos-ops.md` | GLM lane, GitNexus reindex, Unity persistence |
| Routing ledger | `~/.claude/routing-ledger.jsonl` | **Shared append-only JSONL** — Kimi appends rows (`{"ts","lane","task","outcome","repo","note"}`) so Claude-side sessions see Kimi dispatches |
| Fable lane table | `~/.claude/CLAUDE.md` | "Fable = orchestrator only, the brain never types" → the Kimi orchestrator inherits that role; cheaper work delegates to subagents |

Do NOT port: Claude hooks, stop-guards, keepalive ticks (Claude-app mechanics; Kimi has its own turn lifecycle). `.mcp.json` (engine/rules/voice stdio servers) is portable in substance — substitute `${CLAUDE_PLUGIN_ROOT}` → `/Users/m1/WorldOS` if ever wired.

**Known gap — GitNexus**: this repo's AGENTS.md mandates GitNexus `impact` before edits and `detect_changes` before committing, but Kimi has no `mcp__gitnexus__*` tools. Fallback: the CLI (`node .gitnexus/run.cjs analyze`, index fresh at Jul 16 17:12) + grep — or the owner grants an explicit waiver / wires the MCP.

## 7. Kimi ↔ Claude interop contract (both sides co-drive)

Shared surfaces that make either side's work visible to the other:
1. **git + GitHub** — the trunk truth; PR loop per OPERATIONS.md (worktree off main → additive change → focused pytest + `qa/fast_gate.sh` → PR → review-gated merge; shepherd every PR to merged/parked; `gh pr merge <n> --squash --auto`).
2. **Routing ledger** — both sides append (`~/.claude/routing-ledger.jsonl`).
3. **Scores ledger** — every scored run to `qa/scores_db.py` with provider/methodology stamps.
4. **POINTER step** — update the lane's charter/issue + `docs/roadmap/NOW.md` at session close (NOW.md refreshed in #1624; the POINTER step keeps it current).
5. **Universal Run Contract** — HEALTH → EVIDENCE → SCORE → VERDICT → POINTER, every run type.
6. **Box claim queue** — charter #1386 comments, regardless of which agent drives.

## 8. Where Kimi can add value now (ordered, cheapest-first)

1. **Doc-hygiene PR — DONE (this file + NOW.md + RUNBOOK-INDEX land via #1624)** (zero CU, zero box): refreshed `docs/roadmap/NOW.md` to the 2026-07-20 truth + added RUNBOOK-INDEX rows for the `qa/walk_test.py` / `tools/generate_town.py` chain. Pure repo loop.
2. **#1619 render_recipe** (zero CU): the code half that kills recipe-authoring bugs — the previous session recommended landing it BEFORE the repaint. **Update: render_recipe PR #1626 opened, in review.**
3. **Task #76 next cycle** (CU refilled; box claim via #1386): the full regen → render → paint → gate → adjudicate loop, §4 above. **Update: stage 1 regen opened as PR #1625.**
4. **#1620 experience gates** and the open companions of #1618.
5. Later/larger: a valid 5-persona RRI sweep to replace the contaminated f5500ac row; dist/WorldOS.app rebuild; towns beyond the 4-room proof (exteriors/streetscape generator is the known gap; TILED-SPACE-SPIKE ruling says towns = layout problem).

## 9. Open questions for the owner

1. Scenario CU balance — **refilled 2026-07-20 (5k+)**; the "needs fresh budget" framing is stale. Open remainder: land #1619 (PR #1626, in review) first and defer paint?
2. GitHub LFS data pack for the box repo (~$5/mo) — buy, or keep tarball-only saves?
3. GitNexus — **RESOLVED 2026-07-20: the MCP is wired for Kimi** (impact/detect_changes live from the canonical checkout; index rebuild to the tip runs detached).
4. PR this onboarding doc (+ NOW.md refresh) through the normal loop? — **RESOLVED: landed via #1624.**
5. Green-light to claim the box on #1386 and start Task #76 — **GRANTED 2026-07-21** (CU refilled; reviews + merge first).
