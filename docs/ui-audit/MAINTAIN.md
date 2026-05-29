# Maintaining the UI/UX audit — how it stays current

> The 2026-05-29 audit is a snapshot. The codebase moves. **An audit you don't
> re-run is a stale audit.** This doc explains how to keep the findings under
> `docs/ui-audit/` consistent with the live state — and what "100% confidence"
> actually means on a moving target.

## What "100% confidence" means on this audit (the honest framing)

100% on a software audit of a working project is an **asymptote**, not a state.
Three reasons:

1. **The code moves.** Every commit can land a finding fix, a regression, or a
   field-shape change in a `/<screen>-surface` route. The audit captured behavior
   at git rev `46523f9..` (mainline) + `b2f79e1..` (Loop 2 branch). State drifts
   from there continuously.
2. **Observation is sampled.** Loop 1 saw a fresh empty save; Loop 3 saw a
   populated Astarion @ Aldenmoor Estate save with active combat at rest. Each
   sample reveals different aspects of the UI. A new state can surface new
   findings or invalidate old ones.
3. **Judgment is interpretive.** Per-screen scores (0–100 weighted across 10
   criteria) involve subjective weighting. Two auditors could disagree by 5–10
   points on the same screen and both be defensible.

**So what does "100% maintained" mean in practice?** Two artifacts together:

- **`qa/ui_audit_health.sh`** — a deterministic re-run of every structural check
  the audit relied on. PASS = the findings filed at the time of the audit
  reflect the current code. FAIL = either an audit finding was fixed (good
  drift) OR a regression landed (bad drift). Either way, the audit docs need
  updating before re-claiming confidence.
- **A re-audit cadence + the maintain loop** — run the health check on every
  PR that touches `viewer/openworlds/`, `viewer/server.py`, `content/worlds/`,
  or `styles.css`; re-author affected per-screen audit files when health goes
  FAIL.

When both run, **the audit IS the implementation agent's specification**: any
deviation from `docs/ui-audit/screens/<screen>.md` is either a documented finding
in flight or an audit miss to file.

## The health-check script

`qa/ui_audit_health.sh` runs 30 checks (Loop 4 baseline) covering:

| # | Check | Detects |
|---|---|---|
| 1 | Viewer reachable | `viewer/server.py` ran OK on the configured port |
| 2 | `data.js` empty | Demo content regression |
| 3 | Icon-registry baseline ids | `OpenWorldsIcon` registry shrunk or renamed |
| 4 | Demo-leak strings (non-comment) | Linzi / Stolen Marches / Cassian / Kingmaker / Pathfinder rejoin |
| 5 | `<Placeholder>` portrait count ≤ baseline | New screen accidentally falls back to placeholder portraits |
| 6 | `/openworlds/campaigns.json` shape | Catalog API broke |
| 7 | Every `/<screen>-surface` returns 200 | A surface route was renamed/removed |
| 8 | `server.py` route literals still present | Refactor preserved the route names |
| 9 | `styles.css` responsive + a11y rules | Breakpoints / reduced-motion / high-contrast lost |
| 10 | `_private/baldurs-gate/images/` ≥ 2000 dirs | Asset catalog shrunk |
| 11 | (default) Captures via `qa/owshot.sh` succeed | Headless capture pipeline still works |

**Run it:**
```sh
qa/ui_audit_health.sh              # full sweep including captures (~3 min)
qa/ui_audit_health.sh --quick      # skip captures (~5 s)
qa/ui_audit_health.sh --port 8895  # custom viewer port
```

Exit code 0 = PASS (audit findings still valid as filed). Exit code 1 = drift
that needs handling.

## When to run

| Trigger | Action |
|---|---|
| Implementation agent finishes a finding (e.g., closes #267 — combat foe portraits) | Run `--quick` to confirm no regression elsewhere; re-author the screen's audit doc score table; close the GitHub issue with the new state. |
| Before opening any PR touching `viewer/openworlds/` | `--quick` in pre-commit; full sweep in CI. |
| Before re-claiming a confidence number | Full sweep; document the resulting PASS in this MAINTAIN.md as evidence. |
| Once a sprint | Full sweep + re-score the 3–4 most recently touched screens. |
| When a screen's surface route shape changes (engine refactor) | Re-author the screen's audit doc; re-baseline the health check's PASS/FAIL thresholds. |

## What the audit guarantees vs doesn't

| Guarantees (within Loop-4 baseline) | Does NOT guarantee |
|---|---|
| Every `/openworlds/#<screen>` deep-link resolves | Live-DM (`can_act=true`) flow renders correctly |
| Every screen `.jsx` has been read end-to-end | Native macOS app behavior (the title-bar fix #260 against real traffic lights) |
| Every `<Img scope=…>` references a knowable scope | Visual diff between baseline screenshots and current state (the `qa/owshot.sh` artifacts are gitignored; a human compare is still needed for polish drift) |
| Every surface route `/(<screen>|chat|image)` returns 200 with usable JSON shape | That the JSON shape didn't change (Loop 3 found `feats === classFeatures` — health-check doesn't yet verify shape semantics) |
| The asset catalog has 2,359 art dirs (Loop 2) | Per-asset slug-alias correctness (Bestiary `gnoll → gnoll-warrior` — manual) |

The first three are the **structural** floor. The last three are the **semantic**
ceiling — they need either human eyes or a richer per-shape contract test
(future work, beyond this audit's scope).

## How to extend the health check

When the implementation agent lands a fix, the script should grow to defend it.
Pattern:

1. Land the fix (e.g., #260 title-bar overlap).
2. Add a check in `qa/ui_audit_health.sh` that fails if the fix regresses
   (e.g., grep `chrome.jsx` for `paddingLeft: 76` in browser branch — and
   confirm the platform-aware fork landed).
3. Run the health script; verify it PASSes.
4. Commit script + fix together.

Each landed fix becomes a regression test. After 50 fixes the script defends a
substantial portion of the audit.

## Honest confidence trajectory (this audit cycle)

| Loop | Confidence | What lifted it |
|---|---|---|
| Loop 1 closeout | ~75% | Initial sweep of 16 screens, scoring rubric, reference patterns, master tracker, 16 epics + 20 sub-issues filed |
| Loop 2 closeout | ~85% | Read skipped shared sources (data.js / icon-registry / tooltip / toast / server.py); cataloged 2,359 art dirs in `_private`; standalone camp-sidebar audit; #281 asset re-calibration; #284 responsive |
| Loop 3 closeout | ~90% | Multi-viewport captures at 1366 / 1920; state validation against populated save; #286 + #287 + #288 + #289 sub-issues; generativity proof |
| Loop 4 closeout | **~95%** | Snapshot-at-rest inspection of `camp_54fd704d985b` confirms live combat state shape; native Swift chrome inspection clarifies #260 platform-awareness; this maintain-loop script + doc |

**The remaining ~5% asymptote.** Three things would close it briefly but the
gap re-opens as the code moves:

1. **Live-DM session observation** — operator-launched (\`claude -p\`) walk-through of one full session: parley → encounter → combat → rest → next location. Captures the `can_act=true` UI state and validates the action bar / chat / declare flow.
2. **Native-app build + walk** — `script/build_and_run.sh --verify`; observe traffic-light interaction with #260 fix candidate; capture window-chrome screenshots; confirm in-app deep-link aliases work.
3. **A11y scan** — run axe-core or similar against the live page; reconcile its automated findings with C6 scores per screen.

The user-driven QA harness `qa/run_duo.sh` does (1) deterministically. (2) is a
one-time Swift build + 5-minute walk. (3) is a one-time `npx @axe-core/cli`. None
of the three is artifact-only work — they each need an operator or a build to
run. They're documented here as the ongoing closure path.

**After (1) (2) (3) land, confidence asymptotes to ~99%. The remaining 1% is the
software-is-mutable constant.** Re-run the health check after each PR to keep it
near 99%.
