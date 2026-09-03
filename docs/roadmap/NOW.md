# NOW — the you-are-here surface

> Update this file at every session close and every charter transition. It is the FIRST thing
> a bootstrapping agent reads after OPERATIONS.md. Keep it under a screen. History belongs in
> git, not here.

_Last updated: 2026-09-03 (UTC) — **STUCK AUDIT → THE ROOM WEEK** (issue #1793; plan `~/.claude/plans/worldos-rooms-are-the-scene.md`). Reboot context: Five weeks dormant + a machine migration
(Paris Mac mini M4 Pro; the GEX44 GPU box and the LEXAR drive are GONE — any `46.4.26.123`,
`/Volumes/LEXAR` or `/Users/lume` path in older docs is historical). Unity 6000.5.6f1 now runs
LOCALLY on `/Users/m1/worldos-unity` (Stdio MCP bridge:
`extensions/renderers/unity/tools/mcp_stdio_exec.py`); the canonical checkout is `/Users/m1/WorldOS`._

> Depth: `docs/RUNBOOK-INDEX.md` (every gate/runner + its row) · `docs/ROOM-PIPELINE-RUNBOOK.md`
> (room pipeline; §10 hot-load loop, §11 walk ship gate) · `qa/SCORECARD.md` (dated gate table) ·
> `qa/PANEL-PROTOCOL.md` (two-anchor ruler; control lives OFF-repo at `/Users/m1/Codex/worldos-refs/`).

## Active sprint

- **ACTIVE charter: #1702** (label `active-sprint`; #1386 superseded) — REFRESH toward the
  **governing milestone: DEMO COMPLETION, PRODUCT-ROADMAP §9** (owner plays "The Crypt Below"
  end-to-end, zero user-truth defects, proven by gates G1–G4). Owner target beyond it: a
  multi-district TOWN with the crypt as its dungeon (Track C; #1640 discrete areas + travel map).
- **§9 gate table (qa/SCORECARD.md, 2026-09-02):** **G1 INTERIM-GREEN** on local build
  `07a997e9` (walk_test crypt/tavern exhaustive GREEN, visual 4/0; player_cert live slice GREEN;
  packaged pins GREEN; certs sha-pinned #1723) — stays INTERIM until an OWNER-installed build
  exists · **G2 FAIL 0/3 at the honest ruler** (`adv_agg_n3_pin_20260903`, 2026-09-03: N=3, DM pinned
  `claude-opus-4-8`, arc-mode + addendum v2 #1766, VERIFIED completion #1784/#1789/#1791, 20 beats) — the
  model-swap hypothesis is CLOSED (#1781); the arc harness itself is the lever (#1776); PARKED behind the
  room week per #1793 · **G3 ROUTE-INCOMPLETE** (the 6 walked stages camp→snug→camp→crypt→throne→camp all ARRIVED, but the
  binding §9 route also needs the return-for-reward leg back to Keeper Maera — not walked, #1709; VQA scorer
  credential now fixed, full-route re-run pending) · **G4 = the AGENT playthrough** (owner plays only at the 80/20 wall; roadmap §9 protocol #1785) — **FAIL** on
  build `bf890b43` (`agent_g4` row: P1 5 / P2 13 / P3 1; #1755–#1765, #1771); owner install kit landed (#1733/#1768),
  instance LIVE on the actor-light build with grid==paint legacy rooms (#1786/#1790).
- **Shipping surface = the 3D-first KIT chain** (crypt kit v1 / tavern kit v2 plates, kit-derived
  sidecars, per-object gate #1703, build contamination gate #1705). Paint-first generation is
  RETIRED for new rooms; legacy rooms (throne/shop/snug/camp) carry the registration debt G1 measures.
- **Merged this reboot:** #1703/#1705 (the #1690 split) · #1706 (control re-pin) · #1707/#1708
  (docs, lume→m1) · #1710 (CI flake) · #1712 (registry exclusions) · #1713/#1721 (honest G1–G4
  table + G2 correction) · #1715 (`qa/packaged_pins.py` — the certified≠installed instrument) ·
  #1717 (windowed, leak-safe QA sandbox — the old launcher went fullscreen and crashed the Mac) ·
  #1722 (20-beat ruler) · #1723 (certs).

## Live lanes

- **THE ROOM WEEK (#1793 — "the room is the scene", Strategy B; D = predeclared fallback; C never).**
  The audit measured why plates≠collision after two months: the picture is painted from a rough 3D layout
  and nothing reconciles it back to the grid/sidecars. Fix = build each room LIVE in Unity from
  `qa/room_geometries/*.json` (`build_room_kit.cs`), paint only textures; the same meshes are collision +
  occlusion. Day 1 = sidecar re-export (Editor `Build Room From Kit` + `Export Kit Boxes` for snug/shop/
  throne/camp; disagreement list `qa/evidence/legacy-reauthor-20260902/occluder_disagreement.txt` → 0/0) →
  ONE build → `qa/owner_install.sh install`. Days 2–3 = crypt live-3D proof (blind panel within 0.5 of the
  shipped crypt plate ∧ seg 100 % ∧ hero masked at (13,7) ∧ ≥ 60 fps). Days 4–5 = legacy rooms live → ONE
  build → agent G4 pass (`qa/agent_play.sh` + `qa/agent_g4_row.py --persist`).
- **Then:** G3 live walk over the FULL route incl. the return leg (#1746/#1782) → Track C (town) only
  after agent G4 = zero P1 in two consecutive builds. G2's arc harness (#1776) reopens after the room week.
- **Done this reboot (2026-09-02/03):** completion-truth ruler `av_2aa0edfe7407` · arc-mode + FAIL rows
  #1766 · actor lighting + 3D proxy containment #1774 · combat surface #1778 + client HUD #1788 · legacy
  rooms grid==paint #1786/#1790 · owner install kit #1733/#1768 · agent-play loop #1750 · Track B editor
  head-to-head = recorded negative (#1734/#1735).

## Blockers

None probe-verified. Owner gates only: the B-vs-D doctrine call after the Day-3 frames, any recurring
paid service (Rodin Business), live customer-facing changes. Refills approved: Scenario CU + Codex.

## Known frictions (not blockers)

- Three review bots re-review EVERY push and open threads; branch protection requires resolved
  conversations → reply + resolve with a disposition; avoid extra pushes; `--admin` is emergency-only.
- **Port 8766 is the claude-max bridge**, not WorldOS: owner engine 8776 / QA 8981; sandbox
  8866 / 8972; every tool still defaults to 8766/8971 → always pass ports.
- `BuildMacOSPlayer.EnsurePackaged` sources the Unity project ROOT (not StreamingAssets) →
  sync data there before a build and run `qa/packaged_pins.py <app> --repo /Users/m1/WorldOS` after (`--repo` takes the repo root).
- `qa/adventure_eval.py --runs` needs ABSOLUTE prefixes (relative ones aggregate nothing — #1709).
- Never launch the player outside `qa/qa_sandbox.py` (windowed contract) with the Editor open; one
  heavy Unity process at a time.
