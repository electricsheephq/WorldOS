# NOW — the you-are-here surface

> Update this file at every session close and every charter transition. It is the FIRST thing
> a bootstrapping agent reads after OPERATIONS.md. Keep it under a screen. History belongs in
> git, not here.

_Last updated: 2026-09-02 (UTC) — **REBOOT MEASURED.** Five weeks dormant + a machine migration
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
  exists · **G2 FAIL 0/3** at the 15-beat budget (root cause: the `opus` alias now resolves to
  Opus 5, which invents extra fights and overruns a knife-edge budget; the July DM model completes
  at beat 19 under 20) → ruler raised to **20 beats** (#1722, predeclared) and the N=3 re-run at 20
  is in flight · **G3 NAV-GREEN / VQA-ERROR** (6/6 stages arrived; scorer credential now fixed,
  re-run pending) · **G4 PENDING** (owner install kit in flight; local ad-hoc-signed build).
- **Shipping surface = the 3D-first KIT chain** (crypt kit v1 / tavern kit v2 plates, kit-derived
  sidecars, per-object gate #1703, build contamination gate #1705). Paint-first generation is
  RETIRED for new rooms; legacy rooms (throne/shop/snug/camp) carry the registration debt G1 measures.
- **Merged this reboot:** #1703/#1705 (the #1690 split) · #1706 (control re-pin) · #1707/#1708
  (docs, lume→m1) · #1710 (CI flake) · #1712 (registry exclusions) · #1713/#1721 (honest G1–G4
  table + G2 correction) · #1715 (`qa/packaged_pins.py` — the certified≠installed instrument) ·
  #1717 (windowed, leak-safe QA sandbox — the old launcher went fullscreen and crashed the Mac) ·
  #1722 (20-beat ruler) · #1723 (certs).

## Live lanes

- **A-T N=3 @20 beats** (`adv_b20_1..3`, shipping config) → blind adjudication → G2 row.
- **Codex:** #1717 post-merge fixes (T2–T7) · `qa/owner_install.sh` kit (dry-run only; the real
  install is an owner gate: ports 8776/8981, campaign `adventure_demo_v1`, one pinned worktree).
- **Track B ★ editor head-to-head** (prompt-fix · Gemini 4K+refs · Qwen 3.0 Pro · FLUX.2) on the
  kit crypt base, repo gates + one blind panel; ≤$25 of the owner's ~$100 trial cap.
- **Owner review:** #1714 (CANONICAL.md proposed diff — kit chain as current-best) · #1716
  (model-registry allowlist for `model_google-gemini-pro-image-editing`; merging = approval).
- **Then:** charter re-rank by the G2 verdict → client fixes (#1677/#1522/#1666/#1665) → ONE
  build → owner install → G4 → Track C (town layout generator design first).

## Blockers

None probe-verified. Owner gates only: the install moment, #1714/#1716, any recurring paid
service (Rodin Business), the camp-hub art fork (taste — post frames first).

## Known frictions (not blockers)

- Three review bots re-review EVERY push and open threads; branch protection requires resolved
  conversations → reply + resolve with a disposition; avoid extra pushes; `--admin` is emergency-only.
- **Port 8766 is the claude-max bridge**, not WorldOS: owner engine 8776 / QA 8981; sandbox
  8866 / 8972; every tool still defaults to 8766/8971 → always pass ports.
- `BuildMacOSPlayer.EnsurePackaged` sources the Unity project ROOT (not StreamingAssets) →
  sync data there before a build and run `qa/packaged_pins.py <app> --repo` after.
- `qa/adventure_eval.py --runs` needs ABSOLUTE prefixes (relative ones aggregate nothing — #1709).
- Never launch the player outside `qa/qa_sandbox.py` (windowed contract) with the Editor open; one
  heavy Unity process at a time.
