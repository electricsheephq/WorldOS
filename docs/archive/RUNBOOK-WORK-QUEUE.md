# WorldOS — RUNBOOK archived "CURRENT STATE + WORK QUEUE"

> **ARCHIVED 2026-06-02** during the canonical-doc consolidation. This is the historical
> "CURRENT STATE + WORK QUEUE" section that previously lived near the bottom of
> `WorldOS-RUNBOOK.md`. It was self-labeled "Historical snapshot, not current authority" and is moved
> here so the runbook stays a clean HOW-TO (architecture + invariants + dev/QA loops + lessons).
>
> **Historical context only — does NOT override current truth.** For the live release state read the
> STATE block in `WorldOS-OPERATING-GOAL.md`; for the score ledger read `qa/SCORECARD.md`. Most of the
> Quest-Arc / combat-fidelity items below are MERGED and closed; they are kept for provenance.

---

## CURRENT STATE + WORK QUEUE (historical)

**Historical snapshot, not current authority:** this queue was written around `ea815fc`
(2026-05-27 cont.3). During the 2026-05-31 takeover, the gate-truth stabilization merged as PR #465,
the UX-first doc sync merged as PR #468, and first-minute click/title chrome proof merged as PR #470.
Local routing sync merged as PR #471, native provider-selection sync merged as PR #472, takeover
state docs synced as PR #473, Codex-DM app observability merged as PR #475, scripted smoke provider merged
as PR #494, stable agent UI hooks merged as PR #495, failure-bucket/RRI split metadata merged as PR #496,
and takeover truth sync merged as PR #498, followed by PR #499 recording current-main built-app proof
PR #500 fixing Codex-DM provider trace cancellations, PR #501 recording that proof in docs, and
PR #504 adding the 100/100 hybrid app handoff gate, PR #505 adding the RRI `--handoff-json`
bridge for Mac app proof, PR #506 syncing docs to that proof, and PR #508 adding the support-VM
preflight artifact gate.
The local app/private-art checkout should stay fast-forwarded to `origin/main`; current gate truth lives in
`WorldOS-OPERATING-GOAL.md` + `qa/SCORECARD.md`. Do not use this section to decide release state.
The next sprint is UX-first (#467):
fast handoff play is proven diagnostically on `9545383` rather than automatically on later docs-only tips,
including private art, Codex DM, an active player,
five enabled actions, one accepted/resolved `/move`, no evidence-manifest gaps, zero failed/error provider
trace events, and a post-merge `handoff_score=100`. With #479 proven and #504/#505 merged, run #466 only after
support-VM routing/auth/config preflight is explicit; an earlier read-only check found the local
`support-vm-1` SSH alias did not resolve and the operator-endpoint VM checkout was stale at `4524b3e`.
Then prioritize clickability/chrome, launcher clarity,
live-response feel, and CRPG depth before more hardening/proxy/security work.

**LATEST (2026-05-27 cont.3) — the Quest & Arc engine is COMPLETE + WIRED, all combat
defects closed, and the sibling's draft-PR backlog is fully landed.** Since the queue
below was written: L3 events (#196), faction arcs (#205), the DM-wiring (#203), the North
Star doc (#206), the full combat-fidelity wave (#207/#209/#210/#211/#213→**#215** maneuver
die — all 6 flagged combat defects CLOSED), canon content-fill (**#216** — 4 stumble-into
Events + 2 faction arcs + 2 decision-gated agendas), and **all 10 sibling roadmap-squeeze
PRs** (#190/#192/#199/#191/#197 engine + #204/#201/#202/#198/#187 viewer) merged after a
2-agent read-only triage. **Combat-sprint at a new high: angry-dm 3.7** (combat core "clean
— every number traces to a tool call"); residual is DM adherence (monster reactions #218)
+ narration nits (#219), not engine. distill now surfaces auto-fired repeat-saves +
maneuver damage (#217). IN FLIGHT: a post-content-fill story-lift duo (does story clear
4.3?) + the **2nd-seed generativity spike** (the North Star deliverable-B gate — a thin
ORIGINAL non-BG world, zero engine changes; branch `spike/second-seed-generativity`).
The detailed queue below is now mostly HISTORICAL — read `qa/SCORECARD.md` for the live state.

### Quest & Arc engine (the living-story skeleton — `decision-quest-arc-engine.md`)
- **L1 — rule-of-three** (`Quest.evolves_to` + `callback_in_days`; `complete_quest`
  schedules the follow-on via `consequences.schedule`) — **MERGED #185**.
- **L2 — decision-gated flips** (`CompanionAgenda.decision_flag` adds +0.30 to the
  attitude-gated betrayal roll; the breaking-point guard is checked FIRST so it never fires
  above threshold; warning bands telegraph) — **MERGED #189**.
- **L3 — Event / ParleyOption / Outcome** (the first-class Kingmaker decisional; thin
  wrapper over `_apply_structured_effect`; converges with the parley research; an Event
  Outcome sets a decision_flag → the L2↔L3 seam) — **MERGED #196**.
- **BUILD-NEXT — faction-growth arcs** (the Skyrim/Kingmaker join→grow→lead): additive
  `Faction` fields (`rank`, `standing`, `joined`, `questline_arc_id`) + generalize the
  companion stage-machine into a faction-owned `Arc` gated on `Faction.reputation`.
  **MERGED #205.** (Closes the "rep is tracked but reads nothing" gap.)
- **DM-skill WIRING + canon content-fill** (author Raphael / Flaming-Fist into agendas +
  quest `evolves_to` chains) — **MERGED #203/#216**.
- **DEFER** — kingdom/guild-building continuation (tick the inert `RegionControl`/
  `FactionAsset` primitives). Far-future.

### Other engine / QA threads
- **Campaign Director (#72)** — `director.py` + `scene_debt.py` + 3 advisory tools.
  **MERGED + integrated** (DM consults `get_campaign_director` each beat → `add_quest` gap
  closed in play). #71 (path-compiler) + #73 (predicates) deferred until a world authors an
  `adventure_path`.
- **Combat-fidelity fixes** — Multiattack economy (#181), Round-1 turn-skip ENGINE block
  (#183), Guiding-Bolt-on-hit (#188) all **MERGED**. Residual: broader DM-adherence /
  class-feature coverage (issue **#166**) — push via the reach-for pattern + a richer sprint
  seed, not engine force.
- **Observability (#184)** — snapshot version-stamp (`schema_version` + `engine_sha`) +
  centralized QA findings collector (`qa/collect_findings.py`) — **MERGED**. Possible next
  slice: a real-play event log.
- **#143 variant matrices** — foundation ~90% shipped; remaining = (a) doc the weight
  convention in `content/worlds/README.md` (common 11 / uncommon 6 / rare 2 / very_rare 1
  on a ~20 base), (b) author canon variant CONTENT (free-rollable slots first), (c) ONE
  engine bit: generalize the companion agenda-roll to rare non-companion NPC flips (cap
  ~0.07, 3–7%/scene). **GATED on an owner glance** at the quest-idea boundary before a big
  content push.
- **#141 Parley → AI-player relay** — reopened; the relay was never built. Pairs with
  L3 / the social encounter type.
- **Party-ensemble betrayal validation** — `run_party.sh` not exercised since L2 shipped;
  restore it to validate the decision-flip in play.

### NATIVE DESKTOP APP — the play path (IN SCOPE; was the sibling lane)
The macOS/OpenWorlds Swift shell is now part of this lane (the old "another agent owns it,
stay out" note is retired; those PRs #150/#182/#187/#190-192 are merged). The app
(`macos/WorldOSApp/`, built by `script/build_and_run.sh run` → `dist/WorldOS.app`) is a
WKWebView loading the **live worktree** viewer at `/openworlds/` — so a **viewer/JS change
lands on app relaunch with NO Swift rebuild** (the swift build is a ~0.1s no-op).

**How in-app PLAY works (2026-05-27 cont.26 — the read-only→functional fix):**
- The OpenWorlds launcher Play buttons call the native bridge
  `OpenWorldsNative.request("startProviderSession",{provider?,world,runId,companions})`
  (`screen-launcher.jsx`). `provider` is optional: when the web surface has not loaded app status yet,
  Swift `RootView.startProviderFromBridge` falls back to the macOS app's `selectedProviderRaw` setting.
  Swift then asks `AppProcessService.startProviderSession` to launch the selected provider on a fresh
  port and returns `{url}`; the JS then `window.location.assign(reply.url)` — drive the reload from
  **JS**, not the Swift `webURL` @State (which didn't repoint reliably across the async hop).
- The Claude provider still shells **`scripts/play.sh`** / `scripts/play_party.sh`. `play.sh` IS the play loop:
  it binds a viewer with `CLAWDND_PLAYER_MOVES` +
  `CLAWDND_VIEWER_CHAT` set (→ `_live_play()` true) and runs a `claude -p` DM watching the
  move sink. `POST /move` → sink; `/chat?since=` → DM narration the Session tails.
- The checked-in Codex provider now defaults to `scripts/play_codex_dm.sh`, a DM wrapper that owns
  the live viewer, the engine/rules/voice MCP contract, `chat.jsonl`, and `player_moves.jsonl`.
  Keep `scripts/play_codex_actor.sh` as the constrained player/companion actor helper through
  `player_server.py`; it is not the native provider's DM loop. OpenClaw still requires an explicit
  configured command before it can be treated as a startable provider.
- Agents and app harnesses should read `GET /app-status` before trying to infer state from pixels or
  process lists. It is a read-only contract for the live OpenWorlds surface: provider, run/state roots,
  private-art presence, active campaign/session, move sink, actor, enabled actions, and canonical endpoints.
  The built-app harness captures launcher and minted-provider app-status JSON as release evidence.
- **`can_act = _live_play() AND is_live_view`**, and `is_live_view` requires
  `cid == self.campaign_id`. The viewer launches with an EMPTY campaign id; `_resolve_campaign`
  lazily sets `self.campaign_id` to the **current** campaign (`_pick_campaign`). So the
  ★gotcha★: the SPA must VIEW the live/current campaign — `app.jsx` auto-routes launcher→table
  when `runningProvider` is set, **re-polls `campaigns.json` (4s), and auto-follows the
  `current` campaign** so the surface binds to the DM-minted run, not a stale save the
  one-shot catalog pick had selected. Verify playable: `curl /session-surface` → `can_act:true`.
- **Footguns (all fixed cont.26, watch for regressions):** (1) a Finder/Dock GUI launch gets
  launchd's minimal PATH → `claude`/`uv` not found; `launch_common.sh:clawdnd_augment_path`
  prepends `~/.local/bin`+Homebrew. (2) `play.sh`/`play_party.sh` traps must SEPARATE EXIT
  (cleanup) from INT/TERM (cleanup+`exit`) or SIGTERM resumes the loop (wedged orphan). (3)
  `AppProcessService` does NOT kill its viewer child on app SIGTERM → orphaned viewers accrue
  across relaunches (still open — terminate-on-quit TODO). (4) `play.sh` always starts a
  FRESH campaign (true resume-by-id is a later enhancement).
- **Critical files:** `RootView.swift` (startProviderSession bridge), `ProviderAdapters.swift`
  (shells play.sh, ClaudeProvider.detect), `AppProcessService.swift` (PortFinder), `WebView.swift`
  (bridge user-script `window.ClawDnDNative`), `screen-launcher.jsx` + `app.jsx`, `scripts/play.sh`.
