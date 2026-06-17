#!/usr/bin/env python3
"""Cheap zero-LLM smoke for the enriched combat-sprint seed (#195).

Runs qa/pre_seed_combat.py exactly the way run_combat_sprint.sh does (same
subprocess + state dir), then re-opens the persisted campaign and asserts — and
PRINTS — the enrichment the seed is supposed to carry:

  1. Both PCs have an explicit SUBCLASS (Aldric = Battle Master, Maren = War Domain).
  2. A subclass-resource pool is SEEDED and NON-ZERO for each (Aldric's Superiority
     Dice via set_class_resource; Maren's Channel Divinity via apply_srd_defaults).
  3. Maren's prepared rotation contains at least one SAVE-requiring spell
     (Bane / Hold Person / Inflict Wounds).
  4. A SAVE-inducing enemy (a Ghoul) is present in the encounter.

This is NOT the full claude -p combat-sprint (that heavy run measures the coverage
lift after merge). It only proves the seed populates the hooks the Angry-DM lens
needs, so the follow-up run can actually exercise them.

Run from the repo root with the engine venv (pass an ABSOLUTE path — `uv run
--directory` cd's into servers/engine, so a relative `qa/...` won't resolve):
    uv run --directory servers/engine python "$(pwd)/qa/smoke_pre_seed_combat.py"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _ROOT / "servers" / "engine"
_SEED = _ROOT / "qa" / "pre_seed_combat.py"

# Save-requiring spells the rubric asks the caster to be able to throw (#195).
_SAVE_SPELLS = {"bane", "hold person", "inflict wounds"}
# Enemies whose stat block carries a save-or-condition rider.
_SAVE_ENEMIES = {"ghoul", "ghast"}


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")


def main() -> int:
    state_dir = tempfile.mkdtemp(prefix="worldos-smoke-")
    env = dict(os.environ, WORLDOS_STATE_DIR=state_dir)

    # ── Run the real seed entrypoint (subprocess, exactly like the harness) ──
    proc = subprocess.run(
        ["python", str(_SEED), state_dir],
        cwd=str(_ENGINE),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        print("SEED FAILED to produce output:")
        print(proc.stderr or "(no stderr)")
        return 1
    seed = json.loads(proc.stdout.strip().splitlines()[-1])

    # ── Re-open the persisted campaign via the engine API ────────────────────
    sys.path.insert(0, str(_ENGINE))
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    import server  # noqa: PLC0415

    cid = seed["campaign_id"]
    aldric = server.get_character(cid, seed["player_id"])
    maren = server.get_character(cid, seed["companion_id"])

    ok = True
    print("=== combat-sprint seed smoke (#195) ===")
    print(f"campaign_id: {cid}")
    print(f"state_dir:   {state_dir}")

    # 1+2. Subclasses + seeded, non-zero subclass-resource pools ──────────────
    def _subclass(sheet: dict) -> str:
        classes = sheet.get("classes") or [{}]
        return (classes[0].get("subclass") or "").strip()

    def _resources(sheet: dict) -> dict:
        return sheet.get("class_resources") or {}

    print("\n-- PCs: subclass + class resources --")
    for sheet, want_sub, want_pool in (
        (aldric, "Battle Master", "superiority_dice"),
        (maren, "War Domain", "channel_divinity"),
    ):
        name = sheet.get("name")
        sub = _subclass(sheet)
        res = _resources(sheet)
        # Render every pool as id=used/max(size) so the seeded values are visible.
        rendered = ", ".join(
            f"{rid}={v.get('max', 0) - v.get('used', 0)}/{v.get('max', 0)}"
            + (f"({v.get('size')})" if v.get("size") else "")
            for rid, v in res.items()
        ) or "(none)"
        print(f"  {name}: subclass={sub!r} | resources: {rendered}")
        if not sub:
            ok = False
            _fail(f"{name} has no subclass set")
        elif sub.lower() != want_sub.lower():
            ok = False
            _fail(f"{name} subclass is {sub!r}, expected {want_sub!r}")
        pool = res.get(want_pool)
        if not pool:
            ok = False
            _fail(f"{name} is missing the {want_pool!r} pool")
        elif int(pool.get("max", 0)) <= 0:
            ok = False
            _fail(f"{name} {want_pool!r} pool has max={pool.get('max')} (expected > 0)")

    # 3. Maren carries at least one save-requiring spell ──────────────────────
    print("\n-- Maren: save-requiring spell present --")
    known = {s.lower() for s in (maren.get("spells_known") or [])}
    prepared = {s.lower() for s in (maren.get("spells_prepared") or [])}
    available = known | prepared
    save_hits = sorted(_SAVE_SPELLS & available)
    dc = server.spell_save_dc(cid, seed["companion_id"]).get("spell_save_dc")
    print(f"  prepared: {sorted(maren.get('spells_prepared') or [])}")
    print(f"  save-spells present: {save_hits or '(none)'} | spell_save_dc={dc}")
    if not save_hits:
        ok = False
        _fail("Maren has no save-requiring spell (Bane / Hold Person / Inflict Wounds)")

    # 4. A save-inducing enemy is in the encounter ────────────────────────────
    print("\n-- Encounter: save-inducing enemy present --")
    monsters = [server.get_character(cid, mid) for mid in seed.get("monster_ids", [])]
    roster = [m.get("name", "") for m in monsters]
    save_enemy = [m for m in monsters if any(tag in m.get("name", "").lower() for tag in _SAVE_ENEMIES)]
    print(f"  monsters: {roster}")
    if save_enemy:
        g = save_enemy[0]
        # Surface the rider text from notes so the save trigger is human-verifiable.
        print(f"  save-inducing enemy: {g.get('name')} (notes mention a save: "
              f"{'yes' if 'saving throw' in (g.get('notes') or '').lower() else 'check actions'})")
    else:
        ok = False
        _fail("no save-inducing enemy (Ghoul / Ghast) in the encounter")

    print("\n=== SMOKE: " + ("PASS" if ok else "FAIL") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
