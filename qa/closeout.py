#!/usr/bin/env python3
"""Emit the WorldOS standardized closeout block from the scores ledger.

WHY THIS EXISTS
---------------
After every scored run/sweep the owner wants a standardized closeout block (RUN / MODEL /
UNIVERSE / RULER / SCORES + Δ-vs-last-comparable / COVERAGE / VERDICT / CAVEATS). It used to
be assembled by hand — inconsistent format, and the "Δ vs last comparable" was eyeballed (so
the wrong prior run, or a cross-ruler / cross-surface comparison, was easy to cite). This tool
reads the curated ledger (``qa/scores.db`` via :mod:`scores_db`) and prints that exact block for
a given run-id, with the Δ computed against the most-recent PRIOR run sharing the SAME
comparability fence (surface + dm_model + scoring_config_version + lens_config_version) — the
ruler-fence, so a number is NEVER compared across a rubric/gate change or a different surface.

It is a PURE READER over the ledger (never writes the db). It reuses :func:`scores_db.fetch_rows`
and the canonical :data:`scores_db.COLUMNS` mapping — no hand-rolled SQL.

USAGE
-----
    python3 qa/closeout.py <run-id>            # print the closeout block for that run
    python3 qa/closeout.py --db /tmp/x.db <id> # read from an alternate ledger
    python3 qa/closeout.py --list              # list recent run-ids and exit
    python3 qa/closeout.py --help

The OPUS-DM default is load-bearing (sonnet under-drives authored campaigns): a non-opus DM run
is flagged with ``⚠ NON-OPUS DM`` on the MODEL line so a non-default measurement is never cited
as the production signal.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Reuse the canonical ledger module (connection/row helpers + the column contract). Import it the
# same tolerant way the rest of qa/ does (works whether invoked from repo root or as a package).
QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

# Quality bar (the owner's standing closeout bar — story >= 4.3, mech >= 4.5).
STORY_BAR = 4.3
MECH_BAR = 4.5

# HV5 auto-nomination heuristics (docs/roadmap/PRODUCT-ROADMAP.md §4c HV5, epic #1327). These live
# next to STORY_BAR/MECH_BAR per the epic's takeover-review ruling so the nomination heuristic is
# never re-derived inline at the call site. A scored run whose story lens clears NOMINATION_STORY_BAR
# makes its extracted artifacts (qa/artifacts_out/<campaign>/**/*.json) harvest candidates; per-class
# signals then decide which ones. NOMINATION_STORY_BAR is deliberately STORY_BAR (the closeout story
# bar, currently 4.3) — NOT HV3's artifact-panel gate (overall>=4.0 / no dim<3.0, #1325), which is a
# different, non-comparable ac_ ruler family. NOMINATION_TURN_MIN is the NPC dialogue floor: an NPC
# artifact is nominated only with at least this many extracted dialogue snippets (the extractor's
# per-NPC snippet proxy for "dialogue turns", capped at _MAX_DIALOGUE_SNIPPETS=5).
NOMINATION_STORY_BAR = STORY_BAR
NOMINATION_TURN_MIN = 3

# The ruler-fence: the axes a number must share to be DIRECTLY comparable. This is intentionally
# STRICTER than scores_db's canonical-baseline key (which keys on methodology + lens) — the
# closeout Δ must never cross a SURFACE (engine-duo vs GUI), a DM MODEL (opus vs sonnet under-drive),
# OR a RULER (full scoring_config_version AND lens_config_version), since any of those moves the
# number with no change in play quality. methodology is deliberately NOT fenced here (the spec asks
# only for surface + dm_model + ruler) so an 8-beat duo and a 24-beat duo on the same surface/model/
# ruler still compare — the methodology shows in the run rows for the reader to judge.
COMPARE_FENCE: tuple[str, ...] = (
    "surface", "dm_model", "scoring_config_version", "lens_config_version",
)


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------
def _s(v: Any, *, missing: str = "?") -> str:
    """Render a scalar for the block; None/empty -> ``missing`` (the spec's graceful '?')."""
    if v is None:
        return missing
    if isinstance(v, float):
        # scores read best as one decimal (e.g. 4.2, 3.0).
        return f"{v:.1f}"
    s = str(v).strip()
    return s if s else missing


def _num(v: Any) -> Optional[float]:
    """Coerce a ledger value to float, or None if absent / non-numeric."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _delta(cur: Any, prev: Any) -> str:
    """Signed Δ string (``+0.3`` / ``-0.2`` / ``+0.0``), or ``?`` if either side is missing."""
    a, b = _num(cur), _num(prev)
    if a is None or b is None:
        return "?"
    return f"{a - b:+.1f}"


def _is_nonopus(dm_model: Any) -> bool:
    """True when the DM model is set AND is not opus (the under-drive flag).

    A NULL/blank dm_model is NOT flagged — we only warn when we positively know it's non-opus
    (an unstamped row is 'unknown', not 'non-opus'; flagging it would cry wolf).
    """
    if dm_model is None:
        return False
    return str(dm_model).strip().lower() != "opus"


# ---------------------------------------------------------------------------
# ledger lookups (pure readers over scores_db.fetch_rows)
# ---------------------------------------------------------------------------
def find_run(rows: list[dict], run_id: str) -> Optional[dict]:
    for r in rows:
        if r.get("run_id") == run_id:
            return r
    return None


def last_comparable(rows: list[dict], run: dict) -> Optional[dict]:
    """The most-recent PRIOR run sharing the run's ruler-fence (surface+dm_model+both ruler hashes).

    ``rows`` is scores_db.fetch_rows order (newest-first). 'Prior' is by the same ordering: among
    rows that match the fence and are not the run itself, the FIRST one that appears AFTER the run
    in newest-first order (i.e. older). Fence values are matched exactly, including None==None, so a
    run with an unstamped ruler only compares to other unstamped-same-surface/model runs.
    """
    key = tuple(run.get(c) for c in COMPARE_FENCE)
    seen_self = False
    for r in rows:
        if r.get("run_id") == run.get("run_id"):
            seen_self = True
            continue
        if not seen_self:
            continue  # newer than the run — not a PRIOR run
        if tuple(r.get(c) for c in COMPARE_FENCE) == key:
            return r
    return None


# ---------------------------------------------------------------------------
# coverage derivation (from whatever coverage columns exist)
# ---------------------------------------------------------------------------
# The ledger has no per-flag boolean columns; the human roll-up lives in `structural_coverage`
# (free text with ✓ / · markers and tokens like "recruit", "camp", "combat", "quest-resolved",
# "betrayal", "acts n/3") and `acts_reached` (int). We parse the roll-up for the closeout COVERAGE
# line and prefer the explicit acts_reached int for the acts count.
_COVERAGE_TOKENS = ("recruit", "travel", "combat", "quest-resolved", "betrayal")


def _coverage_line(run: dict) -> str:
    """Build the COVERAGE line from structural_coverage + acts_reached (graceful when absent)."""
    sc = run.get("structural_coverage")
    acts = run.get("acts_reached")
    text = (sc or "").lower()
    parts: list[str] = []

    for tok in _COVERAGE_TOKENS:
        if not sc:
            mark = "?"
        elif tok in text:
            # token present: a '·' immediately after the token means not-done; otherwise ✓.
            # This mirrors the story_readout '<tok> ✓/·' stamp convention.
            idx = text.find(tok)
            after = text[idx + len(tok): idx + len(tok) + 3]
            mark = "·" if "·" in after else "✓"
        else:
            mark = "·"
        parts.append(f"{tok} {mark}")

    # acts: prefer the explicit int; fall back to an "acts n/3" token in the roll-up; else ?
    if acts is not None:
        acts_str = f"acts {int(acts)}/3"
    elif sc and "acts" in text:
        idx = text.find("acts")
        acts_str = sc[idx: idx + 8].strip()
    else:
        acts_str = "acts ?/3"

    # spec ordering: recruit · travel · acts n/3 · combat · quest-resolved · betrayal
    out = [parts[0], parts[1], acts_str] + parts[2:]
    return " · ".join(out)


def _structural_status(run: dict) -> str:
    """Map the ledger to PASS/WARN/FAIL for the SCORES line's `structural` field.

    The ledger has no dedicated structural-status column; the closest signals are the `pass`
    verdict (1/0) + whether structure was measured at all (acts_reached / structural_coverage).
    Convention: pass==1 -> PASS, pass==0 -> FAIL, and if there's NO pass verdict but structure WAS
    measured we report WARN (structure observed, no hard verdict); fully unmeasured -> '?'.
    """
    p = run.get("pass")
    if p is not None:
        try:
            return "PASS" if int(p) else "FAIL"
        except (TypeError, ValueError):
            pass
    if run.get("acts_reached") is not None or run.get("structural_coverage"):
        return "WARN"
    return "?"


def _beats_from_methodology(methodology: Any) -> str:
    """Best-effort beats count out of a methodology string (e.g. '3-lens duo 8-beat' -> 8)."""
    if not methodology:
        return "?"
    m = re.search(r"(\d+)\s*-?\s*beat", str(methodology).lower())
    return m.group(1) if m else "?"


def _verdict_line(run: dict) -> str:
    story = _num(run.get("story_overall"))
    mech = _num(run.get("mech_overall"))
    # pass/fail vs the bar (story >= 4.3 AND mech >= 4.5). If either is unmeasured we can't claim a
    # clean pass — report 'inconclusive' rather than a false verdict.
    if story is None or mech is None:
        verdict, read = "inconclusive", "story or mech unmeasured — cannot judge vs the bar"
    elif story >= STORY_BAR and mech >= MECH_BAR:
        verdict = "pass"
        read = f"story {story:.1f}≥{STORY_BAR} and mech {mech:.1f}≥{MECH_BAR}"
    else:
        verdict = "fail"
        misses = []
        if story < STORY_BAR:
            misses.append(f"story {story:.1f}<{STORY_BAR}")
        if mech < MECH_BAR:
            misses.append(f"mech {mech:.1f}<{MECH_BAR}")
        read = " and ".join(misses)
    return f"VERDICT: {verdict} vs bar story≥{STORY_BAR} mech≥{MECH_BAR} — {read}"


# ---------------------------------------------------------------------------
# the block
# ---------------------------------------------------------------------------
def render_block(run: dict, prior: Optional[dict]) -> str:
    rid = _s(run.get("run_id"))
    date = _s(run.get("build_date") or run.get("ts"))
    surface = _s(run.get("surface"))

    dm = _s(run.get("dm_model"))
    actor = _s(run.get("actor_model"))
    scorer = _s(run.get("scorer_model"))
    model_line = f"MODEL: DM={dm} · actor={actor} · scorer={scorer}"
    if _is_nonopus(run.get("dm_model")):
        model_line += "     ⚠ NON-OPUS DM"

    # UNIVERSE: the ledger has no explicit world/campaign column; methodology is the closest human
    # description of what was played (e.g. "authored-spine-fulldepth"). beats from that text.
    universe = _s(run.get("methodology"))
    beats = _beats_from_methodology(run.get("methodology"))

    ruler = f"{_s(run.get('scoring_config_version'))}/{_s(run.get('lens_config_version'))}"

    story = _s(run.get("story_overall"))
    mech = _s(run.get("mech_overall"))
    angry = _s(run.get("angrydm_overall"))
    behav = _s(run.get("behavioral"))
    structural = _structural_status(run)
    sat = _s(run.get("cross_persona_sat"))

    if prior is not None:
        d_story = _delta(run.get("story_overall"), prior.get("story_overall"))
        d_mech = _delta(run.get("mech_overall"), prior.get("mech_overall"))
        delta_line = (
            f"  Δ vs last comparable (same surface+dm_model+ruler) "
            f"{_s(prior.get('run_id'))}: story {d_story} mech {d_mech}"
        )
    else:
        delta_line = "  Δ vs last comparable: no comparable prior run"

    lines = [
        f"RUN: {rid} | {date} | {surface}",
        model_line,
        f"UNIVERSE: {universe} · {beats} beats",
        f"RULER: {ruler}            (compare ONLY within the same ruler)",
        (f"SCORES: story {story} · mech {mech} · angrydm {angry} · "
         f"behavioral {behav} · structural {structural} · sat {sat}"),
        delta_line,
        f"COVERAGE: <{_coverage_line(run)}>",
        _verdict_line(run),
        f"CAVEATS: {_s(run.get('notes'), missing='—')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_closeout(run_id: str, db_path: Path | str = scores_db.DB_PATH,
                   *, nominate: bool = True) -> str:
    """Return the closeout block for ``run_id``, or raise KeyError if it's absent.

    HV5 auto-nomination hook (epic #1327): when ``nominate`` is True (the default) the closeout tail
    appends any qualifying extracted artifacts for this run to qa/nominations.jsonl. The hook is
    ADDITIVE and NON-FATAL — a nomination failure (or simply nothing extracted / nothing qualifying)
    never affects the closeout block. Set ``nominate=False`` to render the block only (the pure-reader
    path the block's own tests exercise).
    """
    rows = scores_db.fetch_rows(db_path)
    run = find_run(rows, run_id)
    if run is None:
        raise KeyError(run_id)
    prior = last_comparable(rows, run)
    block = render_block(run, prior)
    if nominate:
        _run_nomination_hook(run_id, db_path)
    return block


def _run_nomination_hook(run_id: str, db_path: Path | str) -> None:
    """Fire the HV5 auto-nominator for ``run_id`` (append-only, best-effort).

    Isolated + swallow-all so the closeout block never breaks if the nominator or the artifacts_out
    tree is unavailable. The nominator is itself an additive no-op when nothing qualifies. A swallowed
    exception is still surfaced as a one-line stderr warning (not raised) — total silence would mean a
    real regression in nominate.py (a broken import, a typo) produces zero signal at all.
    """
    try:
        import nominate as _nominate  # noqa: PLC0415 — local sibling, imported lazily
        _nominate.nominate(run_id, db_path=db_path)
    except Exception as e:  # noqa: BLE001 — closeout must never fail on a harvest-side hiccup
        print(f"[closeout] nomination hook for {run_id!r} failed (non-fatal): {e}", file=sys.stderr)


def _recent_ids(db_path: Path | str, n: int = 20) -> list[str]:
    return [r.get("run_id") for r in scores_db.fetch_rows(db_path)[:n]]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_id", nargs="?", help="the run-id to emit a closeout block for")
    p.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    p.add_argument("--list", action="store_true",
                   help="list recent run-ids (newest first) and exit")
    args = p.parse_args(argv)

    if args.list:
        for rid in _recent_ids(args.db):
            print(rid)
        return 0

    if not args.run_id:
        p.print_help()
        return 2

    try:
        print(build_closeout(args.run_id, db_path=args.db))
    except KeyError:
        recent = _recent_ids(args.db)
        print(f"error: run-id {args.run_id!r} not found in {args.db}", file=sys.stderr)
        if recent:
            print("recent run-ids (newest first):", file=sys.stderr)
            for rid in recent:
                print(f"  {rid}", file=sys.stderr)
        else:
            print("(the ledger has no runs)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
