#!/usr/bin/env python3
"""Score + summarize a WorldOS AI-playtest run (issue #324).

Reads the run dir's artifacts (bugs.ndjson, actions.ndjson, console.ndjson,
network.ndjson, status.json, meta.json) and writes:
  - score.json   — the rubric metrics (completed_intro_flow, dead_clicks,
                   console_errors, network_failures, bug counts by severity,
                   self-reported satisfaction, pass/fail)
  - summary.md   — a human-readable digest: what the newbie tried, where they got
                   stuck, the top bugs, and the score.

Pure reader: never imports the engine, never touches campaign state.

Usage: ui_playtest_score.py <run-dir> [player-verdict-text]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

# Shared structural-coverage helper (the owner's "full circle"; pairs with the #961
# structural_completeness gate). Importable so this scorer AND the sweep compute the block
# from the SAME logic the readout stamp uses — they cannot drift. story_readout.py sits
# beside this file in qa/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from story_readout import structural_coverage_from_state
except Exception:  # pragma: no cover - defensive; the scorer must still run without it
    structural_coverage_from_state = None  # type: ignore[assignment]


def _resolve_snapshot(rundir: Path, meta: dict) -> Path | None:
    """Best-effort locate the campaign snapshot.json for this run from the run dir alone.

    The GUI/sweep persona plays through a SEPARATE play-state store (play-state/<run>-b/),
    so the snapshot is NOT under the run dir — in that case this returns None and the SWEEP
    injects the block (it knows the state dir). But when meta.json pins a state_dir (or a
    snapshot lives under the run dir's own state/, as the engine-duo path lays it out), pick
    the largest non-empty snapshot under it. Never fabricates: returns None when unresolved.
    """
    candidates: list[Path] = []
    # 1) an explicit pointer in meta.json (additive — older meta lacks it → skipped).
    for key in ("state_dir", "snapshot_path", "snapshot"):
        v = meta.get(key)
        if v:
            p = Path(v)
            if p.name == "snapshot.json" and p.is_file():
                return p
            if p.is_dir():
                candidates.append(p)
    # 2) the run dir's own state/ tree (the in-run-dir layout some harnesses use).
    candidates.append(rundir / "state")
    candidates.append(rundir)
    best: Path | None = None
    best_size = 1  # require > 1 byte (a lock-only orphan dir writes an empty snapshot)
    for base in candidates:
        if not base.exists():
            continue
        for snap in base.glob("campaigns/*/snapshot.json"):
            try:
                size = snap.stat().st_size
            except OSError:
                continue
            if size > best_size:
                best, best_size = snap, size
    return best


def structural_coverage_for_run(rundir: Path, meta: dict) -> dict | None:
    """Compute the structural_coverage block when the snapshot is resolvable from the run dir
    alone; else None (the sweep injects it from the persona's state dir). Read-only."""
    if structural_coverage_from_state is None:
        return None
    snap = _resolve_snapshot(rundir, meta)
    if snap is None:
        return None
    try:
        state = json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(state, dict) or not state:
        return None
    return structural_coverage_from_state(state)


def read_ndjson(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# A satisfaction number the player may have written in its final verdict
# ("satisfaction: 4/10", "I'd rate this a 3 out of 10").
def extract_satisfaction(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"satisfaction[^0-9]{0,12}(\d{1,2})\s*/\s*10", text, re.I)
    if m:
        return clamp10(int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b", text, re.I)
    if m:
        return clamp10(int(m.group(1)))
    return None


def clamp10(n: int) -> int:
    return max(1, min(10, n))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ui_playtest_score.py <run-dir> [verdict-text]", file=sys.stderr)
        return 2
    rundir = Path(sys.argv[1])
    verdict = sys.argv[2] if len(sys.argv) > 2 else ""
    playerdir = rundir / "player"

    meta = read_json(rundir / "meta.json")
    bugs = read_ndjson(rundir / "bugs.ndjson")
    actions = read_ndjson(playerdir / "actions.ndjson")
    console = read_ndjson(playerdir / "console.ndjson")
    network = read_ndjson(playerdir / "network.ndjson")
    status = read_json(playerdir / "status.json")

    # --- action breakdown ----------------------------------------------------
    action_kinds = Counter(a.get("action", "?") for a in actions)
    clicks = [a for a in actions if a.get("action") == "click"]
    dead_clicks = sum(1 for a in clicks if a.get("dead") is True)

    # An in-story TURN is submitted either by type(submit=true) OR by clicking the table's
    # submit control after typing — the UI exposes a "Declare" button + Do/Say/Continue
    # chips, so a click on one of those (on the play screen) posts a /move just the same.
    SUBMIT_CLICK = re.compile(r"\b(declare|send|submit|continue|^do\b|^say\b)\b", re.I)

    def is_submit_action(a: dict) -> bool:
        if a.get("action") == "type" and a.get("submit"):
            return True
        if a.get("action") == "click" and a.get("ok") and a.get("screen") == "table":
            return bool(SUBMIT_CLICK.search(str(a.get("target") or "")))
        return False

    type_submits = sum(1 for a in actions if is_submit_action(a))

    # --- screens visited (from action stream) --------------------------------
    screens = []
    for a in actions:
        s = a.get("screen")
        if s and (not screens or screens[-1] != s):
            screens.append(s)

    # --- bug counts ----------------------------------------------------------
    by_sev = Counter(b.get("severity", "?") for b in bugs)
    by_screen = Counter(b.get("screen", "?") for b in bugs)
    by_cat = Counter(b.get("category", "?") for b in bugs)
    player_bugs = [b for b in bugs if b.get("source") == "player"]
    auto_bugs = [b for b in bugs if b.get("source") == "auto"]

    # "Failed to load resource" console lines are just the browser echoing a failed fetch
    # (already counted under network); a missing /image is graceful degradation, not a JS
    # error. Exclude both from the console-error HEALTH metric so the gate isn't tripped by
    # expected missing-art noise. (The raw lines stay in console.ndjson / network.ndjson.)
    def is_resource_load(c: dict) -> bool:
        return "Failed to load resource" in (c.get("text") or "")

    console_errors = sum(
        1 for c in console if c.get("type") in ("error", "pageerror") and not is_resource_load(c)
    )

    def is_image_404(n: dict) -> bool:
        return n.get("status") == 404 and "/image?scope=" in (n.get("url") or "")

    # Expectation-class the image 404s (audit F11-1b): the viewer's /image responses
    # carry an additive X-Image-Outcome header (recorded into network rows as
    # `image_outcome` by the capture harness). `no-art` / `placeholder` 404s are the
    # DESIGNED degradation this file's own comment describes — only the rest are
    # unexpected render failures. Rows without the field (older captures) stay
    # unclassified: they count in image_404s exactly as before and the designed/
    # unexpected split is simply not emitted as known-zero certainty.
    DESIGNED_IMAGE_OUTCOMES = {"no-art", "no_art", "placeholder"}

    def image_outcome(n: dict) -> str:
        return str(n.get("image_outcome") or "").strip().lower()

    image_404_rows = [n for n in network if is_image_404(n)]
    image_404s = len(image_404_rows)
    image_404s_designed = sum(1 for n in image_404_rows if image_outcome(n) in DESIGNED_IMAGE_OUTCOMES)
    classified_404s = sum(1 for n in image_404_rows if image_outcome(n))
    # Only claim a known unexpected count when EVERY 404 row carries an outcome class —
    # a partial capture must not let release_readiness treat unclassified designed 404s
    # as proven-clean (None = unknown, omitted from score.json).
    image_404s_unexpected = (image_404s - image_404s_designed) if classified_404s == image_404s else None
    network_failures = sum(1 for n in network if not is_image_404(n))

    # --- completed intro flow? -----------------------------------------------
    # Heuristic (no engine peek): the player reached the play screen ("table") AND
    # submitted at least one in-story action (a typed+submitted turn or a [do]/[say]
    # move that the table posts). "table" appearing in the visited screens or any
    # type-with-submit both signal they got into the actual game.
    reached_table = "table" in screens
    completed_intro_flow = bool(reached_table and type_submits >= 1)

    # actions to first in-story turn (first submitted move), informational.
    actions_to_first_beat = None
    for a in actions:
        if is_submit_action(a):
            actions_to_first_beat = a.get("seq")
            break

    gave_up = status.get("reason") == "give_up"

    # --- satisfaction --------------------------------------------------------
    # A `finish` tool call records a structured 1-10 satisfaction in status.json — the most
    # reliable self-report (a validated tool arg, impossible to mis-parse). Prefer it; then a
    # "N/10" in the verdict text; then derive from measured friction.
    status_sat = status.get("satisfaction")
    if isinstance(status_sat, (int, float)) and not isinstance(status_sat, bool):
        satisfaction = clamp10(int(status_sat))
        satisfaction_source = "self-reported"
    else:
        satisfaction = extract_satisfaction(verdict)
        if satisfaction is None:
            # Derive a rough satisfaction when the player didn't state one: start at 8,
            # subtract for the friction we measured. (Informational, clearly derived.)
            s = 8
            if not completed_intro_flow:
                s -= 3
            if gave_up:
                s -= 2
            s -= min(3, by_sev.get("critical", 0) * 2 + by_sev.get("major", 0))
            s -= min(2, dead_clicks)
            if console_errors:
                s -= 1
            satisfaction = clamp10(s)
            satisfaction_source = "derived"
        else:
            satisfaction_source = "self-reported"

    critical = by_sev.get("critical", 0)
    major = by_sev.get("major", 0)
    passed = bool(completed_intro_flow and critical == 0 and console_errors == 0 and satisfaction >= 6)

    score = {
        "run": meta.get("run"),
        "persona": meta.get("persona"),
        "world": meta.get("world"),
        "completed_intro_flow": completed_intro_flow,
        "reached_play_screen": reached_table,
        "actions_total": len(actions),
        "actions_to_first_beat": actions_to_first_beat,
        "in_story_turns": type_submits,
        "dead_clicks": dead_clicks,
        "console_errors": console_errors,
        "network_failures": network_failures,
        "image_404s": image_404s,
        # Additive expectation-classed split (audit F11-1b). image_404s stays the raw
        # total for older consumers; designed = X-Image-Outcome no-art/placeholder;
        # unexpected = the remainder, or null when any 404 row lacks the class.
        "image_404s_designed": image_404s_designed,
        "image_404s_unexpected": image_404s_unexpected,
        "bug_reports_total": len(bugs),
        "bug_reports_player": len(player_bugs),
        "bug_reports_auto": len(auto_bugs),
        "bug_reports_critical": critical,
        "bug_reports_major": major,
        "bug_reports_minor": by_sev.get("minor", 0),
        "bug_reports_trivial": by_sev.get("trivial", 0),
        "bugs_by_screen": dict(by_screen),
        "bugs_by_category": dict(by_cat),
        "action_kinds": dict(action_kinds),
        "screens_visited": screens,
        "gave_up": gave_up,
        "give_up_reason": status.get("detail", ""),
        "persona_satisfaction": satisfaction,
        "satisfaction_source": satisfaction_source,
        "player_cost_usd": meta.get("player_cost_usd"),
        "pass": passed,
    }
    # Structural coverage (the owner's "full circle"; pairs with the #961 gate). Additive: only
    # stamped when the campaign snapshot is resolvable from the run dir alone — the GUI/sweep
    # persona plays through a separate play-state store, so for those runs the SWEEP injects the
    # block (it knows the state dir + DM transcript). Never fabricated; absent ⇒ not resolvable here.
    sc = structural_coverage_for_run(rundir, meta)
    if sc is not None:
        score["structural_coverage"] = sc
    (rundir / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")

    # --- summary.md ----------------------------------------------------------
    md = build_summary(score, bugs, verdict, meta)
    (rundir / "summary.md").write_text(md, encoding="utf-8")
    return 0


def sev_rank(b: dict) -> int:
    order = {"critical": 0, "major": 1, "minor": 2, "trivial": 3}
    return order.get(b.get("severity", "minor"), 4)


def build_summary(score: dict, bugs: list[dict], verdict: str, meta: dict) -> str:
    L: list[str] = []
    verd = "PASS" if score["pass"] else "FAIL"
    L.append(f"# UI Playtest — {score.get('run')} ({score.get('persona')} on {score.get('world')})")
    L.append("")
    L.append(f"**Verdict: {verd}**  ·  satisfaction {score['persona_satisfaction']}/10 "
             f"({score['satisfaction_source']})  ·  {score['actions_total']} actions  ·  "
             f"~${score.get('player_cost_usd') or 0:.2f}")
    L.append("")
    L.append("Pass gate: reached the play screen + took an in-story turn, zero critical bugs, "
             "zero console errors, satisfaction ≥ 6.")
    L.append("")
    L.append("## Did the first-timer get into the game?")
    L.append("")
    L.append(f"- Reached the play screen (table): **{yn(score['reached_play_screen'])}**")
    L.append(f"- Completed intro flow (played a turn): **{yn(score['completed_intro_flow'])}**")
    if score["actions_to_first_beat"] is not None:
        L.append(f"- Actions to first in-story turn: **{score['actions_to_first_beat']}** "
                 f"(newbie target ≤ 10)")
    L.append(f"- In-story turns taken: **{score['in_story_turns']}**")
    L.append(f"- Screens visited: {', '.join(score['screens_visited']) or '(none recorded)'}")
    if score["gave_up"]:
        L.append(f"- **Gave up:** {score['give_up_reason']}")
    L.append("")
    L.append("## Health signals")
    L.append("")
    L.append(f"- Dead clicks (landed but screen didn't change): **{score['dead_clicks']}** (target 0)")
    L.append(f"- Console errors: **{score['console_errors']}** (target 0)")
    L.append(f"- Failed/4xx-5xx network requests: **{score['network_failures']}** (target 0)")
    if score.get("image_404s"):
        L.append(f"- Missing-image 404s (expected graceful degradation, not counted above): "
                 f"**{score['image_404s']}**")
    L.append("")
    L.append("## Bugs found")
    L.append("")
    L.append(f"- Total: **{score['bug_reports_total']}** "
             f"(player-reported {score['bug_reports_player']}, auto-captured {score['bug_reports_auto']})")
    L.append(f"- By severity: critical **{score['bug_reports_critical']}**, "
             f"major **{score['bug_reports_major']}**, minor **{score['bug_reports_minor']}**, "
             f"trivial **{score['bug_reports_trivial']}**")
    if score["bugs_by_screen"]:
        parts = ", ".join(f"{k} ({v})" for k, v in sorted(score["bugs_by_screen"].items(), key=lambda kv: -kv[1]))
        L.append(f"- By screen: {parts}")
    if score["bugs_by_category"]:
        parts = ", ".join(f"{k} ({v})" for k, v in sorted(score["bugs_by_category"].items(), key=lambda kv: -kv[1]))
        L.append(f"- By category: {parts}")
    L.append("")
    # Top bugs (worst severity first), capped.
    ranked = sorted(bugs, key=lambda b: (sev_rank(b), b.get("action_seq", 0)))
    if ranked:
        L.append("### Top findings")
        L.append("")
        for b in ranked[:12]:
            sev = b.get("severity", "?").upper()
            scr = b.get("screen", "?")
            title = b.get("title", "(untitled)")
            L.append(f"- **[{sev}]** ({scr}) {title}")
            exp = (b.get("expected") or "").strip()
            act = (b.get("actual") or "").strip()
            if exp:
                L.append(f"    - expected: {exp}")
            if act:
                L.append(f"    - actual: {act}")
            shot = b.get("screenshot")
            if shot:
                L.append(f"    - evidence: `{shot}`")
        L.append("")
    if verdict.strip():
        L.append("## Player's closing verdict")
        L.append("")
        L.append("> " + verdict.strip().replace("\n", "\n> "))
        L.append("")
    L.append("---")
    L.append(f"_Generated by qa/ui_playtest_score.py from {meta.get('run')}/. "
             "Artifacts: player/screenshots/, player/a11y/, bugs.ndjson, actions.ndjson, "
             "console.ndjson, network.ndjson._")
    return "\n".join(L) + "\n"


def yn(v) -> str:
    return "yes" if v else "no"


if __name__ == "__main__":
    raise SystemExit(main())
