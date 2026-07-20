# KIMI-ONBOARDING — Kimi's routing doc into WorldOS

> Written 2026-07-20 by the Kimi orchestrator after a full 8-agent recon sweep.
> **Status: tracked (landed via the #1623 PR).** It is the Kimi-side entry point; Claude-side agents
> can read it as-is.
> **Decision (owner-asked):** we *use and link to* the existing Claude-side documentation — it is live-edited
> within days, so duplicating it would stale immediately. This file is routing + interop glue only.

---

## 1. Where everything lives

| Thing | Path / identity | Notes |
|---|---|---|
| Canonical checkout | `/Users/lume/WorldOS` | GitHub `electricsheephq/WorldOS`; local branch **`base` tracks `origin/main`**; tip `fd23e972` (dress_focal v2 / #1621, 2026-07-16) |
| Live worktrees | `/Users/lume/WorldOS-worktrees/` (same dir as lowercase spelling, case-insensitive APFS) | `wt-owner-play` — owner's play copy, branch `main` @ `c8a0b155`, **`.worldos-keep` = never touch**; behind main → `git pull --ff-only origin main` BEFORE any owner reseed (schema-skew class, OPERATIONS.md). `walk-gate-hardening` — clean, @ `92edf8d7`, looks merged; only its owning lane may prune it |
| Stale clone (trap) | `/Volumes/LEXAR/repos/WorldOS` | Jul-1 clone @ `76439e2`, ~19 days behind. Do not mistake for canonical |
| Session scratch/evidence | `~/worldos-session-notes` → symlink to `/Volumes/LEXAR/Codex/offloaded-local/2026-07-09/worldos-session-notes` (9.8 GB) | Newest: `walkability-fix-2026-07-16/`, `ship-2026-07-15/` |
| Unity player install | `/Users/lume/Applications/WorldOSPlayer.app` | Jul 16 12:16 build |
| Built app | `dist/` — **EMPTY right now** (no `dist/WorldOS.app` exists) | AGENTS.md defines the built app as the product; last app evidence early July |
| GEX44 backups | `/Volumes/LEXAR/Codex/worldos-unity-backups/` | `worktree-20260716.tgz` confirmed |

## 2. Reading order (doc routing)

1. `docs/OPERATIONS.md` — the bootstrap (fresh, Jul 16). Universal Run Contract: every run closes HEALTH → EVIDENCE → SCORE → VERDICT → POINTER.
2. `VISION.md` — pillars + load-bearing invariants + decision-by-eval.
3. `docs/ACTIVE-GOAL.md` — the standing driver + blocker law (policy is ageless, blocker table stale by construction).
4. `docs/roadmap/PRODUCT-ROADMAP.md` — the plan (durable); its §4b execution-state block is stale (Jul 8).
5. **`docs/roadmap/PROCEDURAL-SCORECARD.md` (Jul 16) — the freshest narrative truth** for the generator chain; narrates tip commits #1611/#1614/#1618/#1621 and the dwing-wing 0-adopted close-out.
6. `docs/ROOM-PIPELINE-RUNBOOK.md` (Jul 16) — the 11-step room pipeline; §10b sandbox hot-load gate loop; §11 walkability ship gate (BOTH beauty panel AND `qa/walk_test.py` GREEN to ship).
7. `qa/PANEL-PROTOCOL.md` (Jul 16) — the versioned blind-panel ruler; nobody freehands the ruler; author never renders own verdict.
8. Active sprint charter: GitHub issue **#1386** (Act II close-out, Rendered Felt) — box claim queue lives in its comments.
9. ⚠ `docs/roadmap/NOW.md` is **STALE (Jul 9)** — it predates the whole generator chain; its "Blockers: None" is unverified. Treat the live plan (below) + PROCEDURAL-SCORECARD as the you-are-here until NOW.md is refreshed.
10. Live plan (Claude-side but plain markdown): `~/.claude/plans/bubbly-cooking-stallman.md` — read **lines 596–668 first** (final log + queue); the CURRENT STATE block at 669+ is an older anchor.

## 3. Live state snapshot (verified 2026-07-20)

- **Generator chain merged & staged on main**: 9 PRs (#1604–#1609 spine, #1610→#1611→#1621 generator, #1613/#1615/#1614/#1616 instruments). dress_focal v2 gives every generated room 3 non-collinear fire beacons; paint has an `err_cells ≤ 0.35` hard gate with similarity re-registration; walk gates are fire-masked tri-state (GREEN/RED/ERROR).
- **Task #76 (next-cycle packet) is PENDING, never started** — see §4.
- **QA truth**: `qa/RRI.json` = RRI 2.7, release_ready=false, 3/11 gates, 1-of-5 personas — partial/harness-contaminated and unsuperseded (no valid release verdict exists). Scores ledger: 117 runs, visual-only since Jul 10. Walk-GREEN: crypt, tavern, throne_hall (live) + shop, tavern_snug (pinned certs in `qa/certifications/`). dwing wing: 0-adopted / 3 honest negatives (owner-falsified adoptions → "instruments adjudicate eyeballs").
- **Live campaign**: `play-state/app-gate-v105-b/campaigns/camp_280cfd4d22a0` — DM budget-stopped after 2 turns (Jul 8).
- **Open PRs that matter**: #1617 (Unity persistence runbook — its OPERATIONS.md sections are NOT in base yet), #1498 (outdoor LoRA, blocked on Scenario train-model quota — owner decision), #1298 (day-plate selection, intentionally unmerged pending owner), #1012 draft (slab tiering, A/B-gated), #1622 (dependabot).
- **GEX44**: reachable, up 19 days, near-idle (probed 2026-07-20). Unity project saved via LFS + autosave cron; **GitHub LFS push blocked on paid data pack (~$5/mo owner billing decision)** — local commits + LEXAR tarball are the save story meanwhile.

## 4. The next-cycle packet (Task #76 — the staged opener)

Source: `~/.claude/tasks/237280f0-e8fe-4529-bb6e-72957d537c61/76.json` (status: pending) + GitHub issues #1618/#1619/#1620 + plan STATE block. All paths verified to exist.

1. **Regen** the wing with dress_focal v2 via `tools/generate_town.py` (staged on main @ `fd23e972`).
2. **Box render ×3** per ROOM-PIPELINE-RUNBOOK §5 + `~/.claude/skills/gex44-unity-host/SKILL.md`; batch the #1616 T-pose registry-sync + player rebuild into the same box build; end the box session with `/home/unity/worldos-unity-save.sh`.
3. **Paint** via `qa/paint_room.py --boxes` (err_cells hard gate auto-warps via the similarity fit).
4. **Hot-load sandbox gates** per runbook §10b (fire-masked, tri-state; cycle `current_room` per room).
5. **Blind-adjudicated verdicts** per `qa/PANEL-PROTOCOL.md` — panels via `qa/panel_workflow.mjs` with `CAL_shipped_shop` calibration reference.
6. Adopt or park per verdict; certifications + `record_room_walk` on adoption.

- Companions: **#1619 render_recipe** (zero-CU code; consider landing BEFORE the repaint — it kills recipe-authoring bugs) and **#1620** experience gates.
- **Budget gate**: the cycle costs ~140 Scenario CU; ~160 CU remained as of 2026-07-16 19:00 local with a measured 2/6 bug-fix-repaint base rate → the previous orchestrator deliberately handed off rather than strand it mid-budget. **Confirm/top up Scenario CU before starting step 3.**

## 5. GEX44 access (probed working 2026-07-20)

- Secrets (never print values): `~/.openclaw/secrets/evaos-gpu-gex44-1-key`, `~/.openclaw/secrets/gex44.env` (defines `GEX44_SSH_HOST/USER/PORT/KEY`, `GEX44_UNITY_*` etc.). Endpoint (from tracked docs): `root@46.4.26.123`. Moonlight/WireGuard configs also in that dir for display.
- **All box access multiplexes over ONE ControlMaster** at `/tmp/gex44-cm.sock` (rapid separate ssh trips trip fail2ban and kill the master for ~10–17 min — batch box checks into one ssh):
  `ssh -i ~/.openclaw/secrets/evaos-gpu-gex44-1-key -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=~/.openclaw/support/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30 -M -S /tmp/gex44-cm.sock -f -N root@46.4.26.123`
- Tunnels: Unity-MCP `ssh -N -L 8080:127.0.0.1:8080 …`; reverse combat-surface `-R 8765:127.0.0.1:8770` via the master; NoMachine desktop `-L 4000`.
- On box: Unity `6000.5.1f1` project `/home/unity/worldos-unity` (drive as user `unity`, **Built-in Render Pipeline — not URP**); Unity-MCP on localhost `:8080` (~42 tools, `execute_code` needs `action:"execute"` + `safety_checks:false`); ComfyUI finisher `:8188`; render scripts versioned in repo at `extensions/renderers/unity/scripts/` (deploy to box, never author box-only).
- **Discipline**: single-tenant — claim by commenting on charter #1386 BEFORE any box op; repo-side work first; `chown -R unity:unity` touched files → ctrl+r → restore the scene you found → **save** (`sudo -u unity /home/unity/worldos-unity-save.sh --push` if anything scene/asset/build changed) → comment release. One bounded MCP op at a time; never `pkill -9`; never touch Mac:8765 (Eva's bridge; WorldOS uses 8770).
- Box-drive recipe pattern: `qa/evidence/dungen-spike/BOX-DRIVE-RECIPE.md`.
- Fallback: 32 GB `support-vm-1` (`root@178.104.123.213`, key `~/.openclaw/secrets/cloud-deploy-key`), preflight via `python3 qa/support_vm_preflight.py`; cannot prove Mac-only surfaces.

## 6. Skills & agent profiles — porting decision (read-on-demand, NO symlinks/copies)

The skill surface is live-edited (worldos-dev touched Jul 15, blind-adjudicator born Jul 16); copies stale within days and symlinks would mis-register foreign files as Kimi-managed skills. Kimi reads absolute paths natively — so:

| Surface | Path | How Kimi uses it |
|---|---|---|
| Dev loop (canonical) | `/Users/lume/WorldOS/.claude/skills/worldos-dev/SKILL.md` | Read before any dev loop |
| Decision apparatus | `/Users/lume/WorldOS/.claude/skills/worldos-decide/SKILL.md` | Read when a decision lacks an eval |
| GEX44/Unity lane | `~/.claude/skills/gex44-unity-host/SKILL.md`, `~/.claude/skills/unity-asset-stack/SKILL.md` | Read before any box op |
| Visual critic / asset gen | `/Users/lume/WorldOS/.claude/skills/visual-critic/SKILL.md`, `.../asset-gen/SKILL.md` | Read on those lanes |
| Blind adjudicator | `~/.claude/agents/blind-adjudicator.md` | **Port the prompt verbatim** into a Kimi read-only subagent (`plan`/`explore` type) for gate verdicts — self-contained, model-agnostic |
| Other agent profiles | `~/.claude/agents/{coder,deep-reasoner,fast-worker,codex-worker}.md` | Map frontmatter to Kimi subagent types (`coder`/`plan`/`explore`) |
| WorldOS memory index | `~/.claude/projects/-Users-lume/memory/worldos-index.md` | Read on any WorldOS resume |
| Ops runbook | `~/.claude/runbooks/worldos-evaos-ops.md` | GLM lane, GitNexus reindex, Unity persistence |
| Routing ledger | `~/.claude/routing-ledger.jsonl` | **Shared append-only JSONL** — Kimi appends rows (`{"ts","lane","task","outcome","repo","note"}`) so Claude-side sessions see Kimi dispatches |
| Fable lane table | `~/.claude/CLAUDE.md` | "Fable = orchestrator only, the brain never types" → the Kimi orchestrator inherits that role; cheaper work delegates to subagents |

Do NOT port: Claude hooks, stop-guards, keepalive ticks (Claude-app mechanics; Kimi has its own turn lifecycle). `.mcp.json` (engine/rules/voice stdio servers) is portable in substance — substitute `${CLAUDE_PLUGIN_ROOT}` → `/Users/lume/WorldOS` if ever wired.

**Known gap — GitNexus**: this repo's AGENTS.md mandates GitNexus `impact` before edits and `detect_changes` before committing, but Kimi has no `mcp__gitnexus__*` tools. Fallback: the CLI (`node .gitnexus/run.cjs analyze`, index fresh at Jul 16 17:12) + grep — or the owner grants an explicit waiver / wires the MCP.

## 7. Kimi ↔ Claude interop contract (both sides co-drive)

Shared surfaces that make either side's work visible to the other:
1. **git + GitHub** — the trunk truth; PR loop per OPERATIONS.md (worktree off main → additive change → focused pytest + `qa/fast_gate.sh` → PR → review-gated merge; shepherd every PR to merged/parked; `gh pr merge <n> --squash --auto`).
2. **Routing ledger** — both sides append (`~/.claude/routing-ledger.jsonl`).
3. **Scores ledger** — every scored run to `qa/scores_db.py` with provider/methodology stamps.
4. **POINTER step** — update the lane's charter/issue + `docs/roadmap/NOW.md` at session close (NOW.md is currently stale — first Kimi-side candidate task).
5. **Universal Run Contract** — HEALTH → EVIDENCE → SCORE → VERDICT → POINTER, every run type.
6. **Box claim queue** — charter #1386 comments, regardless of which agent drives.

## 8. Where Kimi can add value now (ordered, cheapest-first)

1. **Doc-hygiene PR** (zero CU, zero box): refresh `docs/roadmap/NOW.md` to the Jul-16 truth + add RUNBOOK-INDEX rows for `qa/walk_test.py` / `tools/generate_town.py` chain + consider landing this file. Pure repo loop.
2. **#1619 render_recipe** (zero CU): the code half that kills recipe-authoring bugs — the previous session recommended landing it BEFORE the repaint.
3. **Task #76 next cycle** (needs Scenario CU confirmation + GEX44 box claim): the full regen → render → paint → gate → adjudicate loop, §4 above.
4. **#1620 experience gates** and the open companions of #1618.
5. Later/larger: a valid 5-persona RRI sweep to replace the contaminated f5500ac row; dist/WorldOS.app rebuild; towns beyond the 4-room proof (exteriors/streetscape generator is the known gap; TILED-SPACE-SPIKE ruling says towns = layout problem).

## 9. Open questions for the owner

1. Scenario CU balance now — top up before Task #76 step 3, or land #1619 first and defer paint?
2. GitHub LFS data pack for the box repo (~$5/mo) — buy, or keep tarball-only saves?
3. GitNexus: explicit waiver for CLI-fallback, or wire the MCP for Kimi?
4. PR this onboarding doc (+ NOW.md refresh) through the normal loop?
5. Green-light to claim the box on #1386 and start Task #76 when 1 is answered?
