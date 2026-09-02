#!/usr/bin/env python3
"""qa/scores_persist.py — the FAIL-LOUD glue between each QA runner and scores_db.add_run (#1414).

WHY THIS EXISTS
---------------
Every EXPENSIVE run type used to rely on a MANUAL ``scores_db.py --add`` after completion (the
"manual-append bucket": ``qa/run_duo.sh``, ``qa/run_combat_sprint.sh``, ``qa/vm/sweep_v2.sh``,
``qa/ui_playtest_app.sh``, ``qa/release_readiness.py`` — see docs/RUNBOOK-INDEX.md "known wiring
gaps" #1). A run could finish cleanly and never land a scores_ledger row (the Universal Run
Contract's SCORE step had no teeth for exactly the runs that cost the most). This module is the
auto-append each runner now calls at its own completion — ONE small, TESTED surface instead of
five divergent inline heredocs (mirrors ``qa/mechanism_probe.sh``'s pioneering auto-append, but
FAIL LOUD instead of ``|| echo WARN``).

CONTRACT
--------
* **FAIL LOUD.** Every ``persist_*`` function either returns cleanly (the row landed) or raises.
  Callers — bash runners via the CLI below, or ``release_readiness.py`` directly — MUST treat a
  raise / non-zero exit as a FAILED RUN (Universal Run Contract: "No row = no run") — never
  swallow it with ``|| echo WARN``.
* **IDEMPOTENT-ISH.** Every ``persist_*`` function takes the run's own stable name as ``run_id``.
  ``scores_db.add_run`` is INSERT OR REPLACE keyed on ``run_id`` (see scores_db.py's module
  docstring + ``add_run``), so re-persisting the SAME run (a retry, a resumed checkpoint, a
  re-run at the same SHA) overwrites rather than duplicating; a genuinely different run needs a
  distinct run_id — exactly the discipline every existing ``add_run`` call site already follows.
* **CONTAMINATED runs get a MARKER row, never a clean-looking one** (the watcher contract,
  docs/OPERATIONS.md "Infra-fail => NO citable row" / docs/ACTIVE-GOAL.md "write a `*CONTAMINATED`
  marker"): no lens numbers, ``behavioral="CONTAMINATED"``, and the reason lives in ``notes``.

Field conventions (surface / methodology / column mapping) follow the historical rows
``qa/scores_seed_forensics.py`` already seeded — that file is the ledger's own precedent for how
each run type's fields map onto the ``runs`` table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

LATENCY_FIELDS = (
    "s_per_beat", "coldopen_s", "turns_per_beat", "combat_s_per_beat",
    "social_s_per_beat", "mean_tool_call_ms", "slowest_tool", "tool_exec_pct",
    "duration_wall_s",
)


def _read_json(path: Optional[str]) -> dict:
    """Best-effort JSON read: a missing/unparsable evidence file degrades to {} (the caller's
    field ends up NULL) rather than crashing the persist call — the ROW WRITE is what must fail
    loud, not a best-effort evidence read that a scorer failure may legitimately have skipped."""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _overall(path: Optional[str]) -> Optional[float]:
    v = _read_json(path).get("overall")
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------------------
# qa/run_duo.sh — surface=engine-duo
# ---------------------------------------------------------------------------
def persist_duo_row(
    run_id: str,
    *,
    db_path: Path | str = scores_db.DB_PATH,
    build_sha: Optional[str] = None,
    dm_model: Optional[str] = None,
    actor_model: Optional[str] = None,
    scorer_model: str = "sonnet",
    beats: Optional[int] = None,
    completed_beats: Optional[int] = None,
    behavioral: Optional[str] = None,
    story_json: Optional[str] = None,
    mech_json: Optional[str] = None,
    angry_json: Optional[str] = None,
    latency_json: Optional[str] = None,
    gate_reason: Optional[str] = None,
    unscorable_detail: Optional[str] = None,
    infra_note: Optional[str] = None,
    source_path: Optional[str] = None,
    persona: Optional[str] = None,
    contaminated_reason: Optional[str] = None,
) -> None:
    """qa/run_duo.sh's completion row. Pass ``contaminated_reason`` on a QUOTA/INFRA abort to
    write the CONTAMINATED marker row instead of a scored one (never both)."""
    fields: dict[str, Any] = {
        "surface": "engine-duo",
        "dm_model": dm_model,
        "build_sha": build_sha,
        "persona": persona,
        "source_path": source_path,
    }
    if contaminated_reason:
        fields["methodology"] = f"3-lens duo ({beats if beats is not None else '?'}-beat, CONTAMINATED)"
        fields["behavioral"] = "CONTAMINATED"
        fields["notes"] = (
            f"CONTAMINATED — {contaminated_reason} completed_beats="
            f"{completed_beats if completed_beats is not None else 'unknown'}. "
            "No citable lens scores (watcher contract: infra-fail => no citable row)."
        )
        scores_db.add_run(run_id, db_path=db_path, **fields)
        return

    n_beats = completed_beats if completed_beats is not None else beats
    fields["actor_model"] = actor_model
    fields["scorer_model"] = scorer_model
    fields["methodology"] = f"3-lens duo {n_beats if n_beats is not None else '?'}-beat"
    fields["behavioral"] = behavioral
    fields["story_overall"] = _overall(story_json)
    fields["mech_overall"] = _overall(mech_json)
    fields["angrydm_overall"] = _overall(angry_json)
    lat = _read_json(latency_json)
    for k in LATENCY_FIELDS:
        if lat.get(k) is not None:
            fields[k] = lat[k]
    notes = [f"behavioral={behavioral or 'unknown'}"]
    if gate_reason:
        notes.append(f"gate_reason={gate_reason}")
    if unscorable_detail:
        notes.append(f"UNSCORABLE: {unscorable_detail}")
    notes.append(f"infra: {infra_note or 'no throttle/checkpoint issue detected'}")
    fields["notes"] = "; ".join(notes)
    scores_db.add_run(run_id, db_path=db_path, **fields)


# ---------------------------------------------------------------------------
# qa/run_combat_sprint.sh — surface=engine-duo, methodology=combat-sprint
# ---------------------------------------------------------------------------
def persist_combat_sprint_row(
    run_id: str,
    *,
    db_path: Path | str = scores_db.DB_PATH,
    build_sha: Optional[str] = None,
    dm_model: Optional[str] = None,
    behavioral: Optional[str] = None,
    angry_json: Optional[str] = None,
    gate_reason: Optional[str] = None,
    source_path: Optional[str] = None,
) -> None:
    """qa/run_combat_sprint.sh's completion row. The single Angry-DM (5e rules-fidelity) score
    IS the run's mech reading here (docs/OPERATIONS.md: "mech = combat-sprint median") — stamped
    into ``angrydm_overall``, matching the historical qa/scores_seed_forensics.py COMBAT_SPRINT
    convention (methodology="combat-sprint...", angrydm_overall=..., no separate mech_overall)."""
    notes = [f"behavioral={behavioral or 'unknown'}"]
    if gate_reason:
        notes.append(f"gate_reason={gate_reason}")
    scores_db.add_run(
        run_id, db_path=db_path, surface="engine-duo", methodology="combat-sprint",
        dm_model=dm_model, actor_model=dm_model, scorer_model="sonnet",
        angrydm_overall=_overall(angry_json), behavioral=behavioral,
        build_sha=build_sha, source_path=source_path, notes="; ".join(notes),
    )


# ---------------------------------------------------------------------------
# qa/vm/sweep_v2.sh — surface=GUI-headless-proxy (one row per persona)
# ---------------------------------------------------------------------------
def persist_sweep_persona_row(
    run_id: str,
    *,
    db_path: Path | str = scores_db.DB_PATH,
    persona: str,
    build_sha: Optional[str] = None,
    dm_model: Optional[str] = None,
    actor_model: Optional[str] = None,
    score_json: Optional[str] = None,
    source_path: Optional[str] = None,
    notes_extra: Optional[str] = None,
) -> None:
    """qa/vm/sweep_v2.sh's per-persona completion row."""
    score = _read_json(score_json)
    sat = score.get("persona_satisfaction")
    sat_source = score.get("satisfaction_source")
    crit = score.get("bug_reports_critical")
    gave_up = score.get("gave_up")
    per_persona = {
        "persona": persona, "sat": sat, "gaveup": gave_up, "crit": crit,
        "satisfaction_source": sat_source,
    }
    notes = [f"satisfaction_source={sat_source or 'unknown'}"]
    if notes_extra:
        notes.append(notes_extra)
    scores_db.add_run(
        run_id, db_path=db_path, surface="GUI-headless-proxy",
        methodology="AI-playtester palette persona (ui_playtest_app.sh part-B, VM sweep)",
        dm_model=dm_model, actor_model=actor_model,
        scorer_model=sat_source or "derived/self-reported",
        persona=persona, cross_persona_sat=sat, critical_bugs=crit,
        per_persona_json=per_persona, build_sha=build_sha,
        source_path=source_path or score_json, notes="; ".join(notes),
    )


# ---------------------------------------------------------------------------
# qa/ui_playtest_app.sh — surface=GUI-built-app, from score.json
# ---------------------------------------------------------------------------
def persist_app_gate_row(
    run_id: str,
    *,
    db_path: Path | str = scores_db.DB_PATH,
    build_sha: Optional[str] = None,
    dm_model: Optional[str] = None,
    actor_model: Optional[str] = None,
    part: Optional[str] = None,
    provider: Optional[str] = None,
    score_pass: Optional[bool] = None,
    score_json: Optional[str] = None,
    notes_extra: Optional[str] = None,
    source_path: Optional[str] = None,
) -> None:
    """qa/ui_playtest_app.sh's completion row, from score.json. ``satisfaction_source`` is
    preserved as this row's ``scorer_model`` (the model/method that produced the satisfaction
    number is the row's provenance — matches the sweep-persona convention above)."""
    score = _read_json(score_json)
    sat = score.get("persona_satisfaction")
    sat_source = score.get("satisfaction_source")
    crit = score.get("bug_reports_critical")
    notes = [f"satisfaction_source={sat_source or 'unknown'}", f"provider={provider or 'unknown'}"]
    if notes_extra:
        notes.append(notes_extra)
    fields: dict[str, Any] = dict(
        surface="GUI-built-app",
        methodology=f"deterministic-ui-playtest (built dist/WorldOS.app, part {part or 'AB'})",
        dm_model=dm_model, actor_model=actor_model,
        scorer_model=sat_source or "derived/self-reported",
        cross_persona_sat=sat, critical_bugs=crit,
        build_sha=build_sha, source_path=source_path or score_json, notes="; ".join(notes),
    )
    if score_pass is not None:
        fields["pass"] = int(bool(score_pass))
    scores_db.add_run(run_id, db_path=db_path, **fields)


# ---------------------------------------------------------------------------
# qa/release_readiness.py — surface=GUI-built-app (the RRI verdict row)
# ---------------------------------------------------------------------------
def persist_rri_row(
    run_id: str,
    *,
    db_path: Path | str = scores_db.DB_PATH,
    rri_json: str,
    build_sha: Optional[str] = None,
) -> None:
    """qa/release_readiness.py's completion row — persisted whenever ``--out`` is written
    (matches the historical qa/scores_seed_forensics.py RRI_SWEEPS convention: surface=
    "GUI-built-app", methodology="...RRI gate...", rri=..., story/mech/behavioral/cross_persona_sat
    from the rollup). An ABORTED (quota) rollup gets the CONTAMINATED marker, never a citable RRI."""
    rri = _read_json(rri_json)
    sha = build_sha or rri.get("build_sha")
    if rri.get("aborted"):
        scores_db.add_run(
            run_id, db_path=db_path, surface="GUI-built-app",
            methodology="release-readiness gate rollup (release_readiness.py)",
            behavioral="CONTAMINATED", build_sha=sha, source_path=str(rri_json),
            notes=(f"CONTAMINATED — quota abort: {rri.get('abort_detail') or 'session limit hit'}. "
                   "No citable RRI (watcher contract: infra-fail => no citable row)."),
        )
        return
    signals = rri.get("signals") or {}
    failed = rri.get("failed_gates") or []
    notes = (f"status={rri.get('status')}; gates {rri.get('gates_passed')}/{rri.get('gates_total')}; "
             f"failed={','.join(failed) or 'none'}; release_ready={rri.get('release_ready')}")
    scores_db.add_run(
        run_id, db_path=db_path, surface="GUI-built-app",
        methodology="release-readiness gate rollup (release_readiness.py)",
        scorer_model="claude/derived",
        rri=rri.get("rri"), story_overall=signals.get("story_overall"),
        mech_overall=signals.get("mech_overall"), behavioral=signals.get("behavioral") or None,
        cross_persona_sat=signals.get("cross_persona_satisfaction"),
        critical_bugs=signals.get("total_critical_bugs"),
        image_render_rate=signals.get("image_render_rate"),
        build_sha=sha, source_path=str(rri_json), notes=notes,
        **{"pass": int(bool(rri.get("release_ready")))},
    )


# ---------------------------------------------------------------------------
# CLI — the fail-loud entrypoint every bash runner calls
# ---------------------------------------------------------------------------
def _fail(cmd: str, run_id: str, exc: Exception) -> None:
    print(
        f"[scores_persist] FATAL: {cmd} row write failed for run_id={run_id!r}: {exc} — "
        "a failed row write is a failed run (Universal Run Contract: 'No row = no run', "
        "docs/OPERATIONS.md).",
        file=sys.stderr,
    )
    sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-id", required=True)
        p.add_argument("--db", default="", help="override scores.db path (testing only)")

    p = sub.add_parser("duo", help="qa/run_duo.sh completion row")
    _common(p)
    p.add_argument("--build-sha")
    p.add_argument("--dm-model")
    p.add_argument("--actor-model")
    p.add_argument("--scorer-model", default="sonnet")
    p.add_argument("--beats", type=int)
    p.add_argument("--completed-beats", type=int)
    p.add_argument("--behavioral")
    p.add_argument("--story-json")
    p.add_argument("--mech-json")
    p.add_argument("--angry-json")
    p.add_argument("--latency-json")
    p.add_argument("--gate-reason")
    p.add_argument("--unscorable-detail")
    p.add_argument("--infra-note")
    p.add_argument("--source-path")
    p.add_argument("--persona")
    p.add_argument("--contaminated-reason")

    p = sub.add_parser("combat-sprint", help="qa/run_combat_sprint.sh completion row")
    _common(p)
    p.add_argument("--build-sha")
    p.add_argument("--dm-model")
    p.add_argument("--behavioral")
    p.add_argument("--angry-json")
    p.add_argument("--gate-reason")
    p.add_argument("--source-path")

    p = sub.add_parser("sweep-persona", help="qa/vm/sweep_v2.sh per-persona completion row")
    _common(p)
    p.add_argument("--persona", required=True)
    p.add_argument("--build-sha")
    p.add_argument("--dm-model")
    p.add_argument("--actor-model")
    p.add_argument("--score-json")
    p.add_argument("--source-path")
    p.add_argument("--notes-extra")

    p = sub.add_parser("app-gate", help="qa/ui_playtest_app.sh completion row")
    _common(p)
    p.add_argument("--build-sha")
    p.add_argument("--dm-model")
    p.add_argument("--actor-model")
    p.add_argument("--part")
    p.add_argument("--provider")
    p.add_argument("--score-pass", choices=["true", "false"])
    p.add_argument("--score-json")
    p.add_argument("--notes-extra")
    p.add_argument("--source-path")

    p = sub.add_parser("rri", help="qa/release_readiness.py completion row")
    _common(p)
    p.add_argument("--rri-json", required=True)
    p.add_argument("--build-sha")

    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    db_path: Path | str = Path(args.db) if args.db else scores_db.DB_PATH
    try:
        if args.cmd == "duo":
            persist_duo_row(
                args.run_id, db_path=db_path, build_sha=args.build_sha, dm_model=args.dm_model,
                actor_model=args.actor_model, scorer_model=args.scorer_model, beats=args.beats,
                completed_beats=args.completed_beats, behavioral=args.behavioral,
                story_json=args.story_json, mech_json=args.mech_json, angry_json=args.angry_json,
                latency_json=args.latency_json, gate_reason=args.gate_reason,
                unscorable_detail=args.unscorable_detail, infra_note=args.infra_note,
                source_path=args.source_path, persona=args.persona,
                contaminated_reason=args.contaminated_reason,
            )
        elif args.cmd == "combat-sprint":
            persist_combat_sprint_row(
                args.run_id, db_path=db_path, build_sha=args.build_sha, dm_model=args.dm_model,
                behavioral=args.behavioral, angry_json=args.angry_json,
                gate_reason=args.gate_reason, source_path=args.source_path,
            )
        elif args.cmd == "sweep-persona":
            persist_sweep_persona_row(
                args.run_id, db_path=db_path, persona=args.persona, build_sha=args.build_sha,
                dm_model=args.dm_model, actor_model=args.actor_model, score_json=args.score_json,
                source_path=args.source_path, notes_extra=args.notes_extra,
            )
        elif args.cmd == "app-gate":
            persist_app_gate_row(
                args.run_id, db_path=db_path, build_sha=args.build_sha, dm_model=args.dm_model,
                actor_model=args.actor_model, part=args.part, provider=args.provider,
                score_pass=(args.score_pass == "true") if args.score_pass else None,
                score_json=args.score_json, notes_extra=args.notes_extra,
                source_path=args.source_path,
            )
        elif args.cmd == "rri":
            persist_rri_row(
                args.run_id, db_path=db_path, rri_json=args.rri_json, build_sha=args.build_sha,
            )
        else:  # pragma: no cover — argparse's `required=True` on the subparsers makes this dead
            raise ValueError(f"unknown subcommand {args.cmd!r}")
    except Exception as exc:  # FAIL LOUD — never swallow (Universal Run Contract: no row = no run)
        _fail(args.cmd, args.run_id, exc)
        return 1  # unreachable (_fail calls sys.exit); keeps type checkers happy
    print(f"[scores_persist] {args.cmd} row written (run_id={args.run_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
