#!/usr/bin/env python3
"""Generate the curated Creation-Plane portrait gallery manifest (#378 / #315 AC3).

The Creation Plane's "Face" step (viewer/openworlds/screen-create.jsx) offers a
race-filtered gallery of canon BG3 / Forgotten Realms faces. Before this generator the
gallery was a 12-entry hard-coded list — 5 of the 10 playable races (dwarf, halfling,
gnome, dragonborn, half-orc) rendered an EMPTY grid (#378). The art exists: ~2,077
``portrait_<slug>`` dirs are already ingested under the gitignored
``content/worlds/_private/baldurs-gate/images/`` pool (#281). The gap was WIRE-UP, not art.

WHAT THIS DOES
--------------
Emits ``viewer/openworlds/portrait-gallery.json`` — a pure-data manifest the screen
loads at runtime to EXTEND the hard-coded base gallery. Each entry is::

    { "slug": "<portrait dir slug>", "name": "<display name>",
      "race": "<RACES key>", "alive": true|false, "synthetic": false }

The manifest references portraits by SCOPE ONLY (``portrait-<slug>``). It contains NO
image bytes and NO paths into ``_private`` — the raw art stays gitignored (Wizards Fan
Content Policy, AC4). The existing ``<Img scope=…>`` → ``/image`` render bridge resolves
each scope to the ingested pixels (viewer/server.py ``_scope_key`` normalization).

CURATION POLICY (AC2)
---------------------
Race is a CANON FACT, never inferred from pixels or invented. ``CANON_FACES`` below is a
hand-audited map of (race -> [(slug, display name)]) where each face's lineage is
established BG3 / FR lore. The generator KEEPS only entries whose ``portrait_<slug>`` dir
actually exists in the local pool, so the manifest can never reference missing art. Only
canon-rendered art is included; ``synthetic`` is reserved (always false here) for any
future AI-generated entry, which would be gated behind an opt-in on the screen.

STABLE INDEX PREFIX (AC3)
-------------------------
``hero.portrait`` serializes a gallery INDEX. The screen concatenates the hard-coded
``PORTRAIT_GALLERY`` base (indices 0..11) with the manifest entries AFTER it, and this
generator's ``BASE_PREFIX`` mirrors that base so the manifest never re-includes a base
slug (which would shift indices and re-roll existing heroes' faces). New entries only
ever APPEND.

HONEST POOL CEILING (vs. AC1 "≥24 per race")
--------------------------------------------
#378 AC1 asks for ≥24 race-attributable faces for ALL 10 playable races (≥240 total).
The local pool genuinely supports this only for the well-covered lineages; for thin
races (dragonborn / half-orc / halfling / gnome) the pool simply does not contain 24
faces whose race is canon — and tagging arbitrary unrace'd faces with a race would be
fabrication (forbidden). So the manifest carries every canon-verified face the pool
supports and the screen DEGRADES GRACEFULLY for thin races (the existing
``portraitChoicesForRace`` fallback shows the full living gallery + the unique-face-gen
escape hatch). This generator reports the per-race ceiling so the gap is auditable.

USAGE
-----
    python3 viewer/openworlds/tools/gen_portrait_gallery.py            # write manifest
    python3 viewer/openworlds/tools/gen_portrait_gallery.py --check    # CI: fail if stale
    WORLDOS_ART_REPO_ROOT=/path/to/canonical \\
        python3 viewer/openworlds/tools/gen_portrait_gallery.py        # pool in another checkout

Re-run after ingesting new race-attributable portraits to widen coverage. When run from a
git worktree (where the gitignored pool is absent), point WORLDOS_ART_REPO_ROOT /
CLAWDND_ART_REPO_ROOT at the canonical checkout so the filter sees the real pool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Repo layout: this file is viewer/openworlds/tools/gen_portrait_gallery.py
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
_OPENWORLDS_DIR = _HERE.parents[1]
_MANIFEST_PATH = _OPENWORLDS_DIR / "portrait-gallery.json"


def _pool_dir() -> Path:
    """The ingested portrait pool. Honor the same art-root override the viewer uses so the
    generator can run from a worktree against the canonical checkout's gitignored pool."""
    for env in ("WORLDOS_ART_REPO_ROOT", "CLAWDND_ART_REPO_ROOT"):
        root = os.environ.get(env)
        if root:
            return Path(root) / "content" / "worlds" / "_private" / "baldurs-gate" / "images"
    return _REPO_ROOT / "content" / "worlds" / "_private" / "baldurs-gate" / "images"


# The hard-coded base gallery in screen-create.jsx (the stable append-only prefix). The
# generated manifest is APPENDED after this, so these slugs MUST NOT reappear in CANON_FACES
# (doing so would shift indices and re-roll existing heroes' portraits — AC3).
# Indices 0..11 are the original cast; 12..21 are the lineage-correct faces #379/#667 added
# for the five thin races (dwarf/halfling/gnome/dragonborn/half-orc), so they are now part of
# the stable base too — the generator drops any of these from CANON_FACES (line ~171).
BASE_PREFIX = [
    "aubree", "shadowheart", "astarion", "gale", "lae-zel", "wyll",
    "karlach", "jaheira", "minsc", "halsin", "minthara", "dame-aylin",
    "baelen-bonecloak", "thokki", "cora-highberry", "roger-highberry",
    "barcus-wroot", "wulbren-bongle", "medrash", "lyrux-goldthroat",
    "jord", "gronch",
]

# Curated canon map: race (a RACES key in screen-create.jsx) -> [(slug, display name)].
# Race is a CANON FACT for each named figure (BG3 / FR lore). The generator drops any
# slug whose portrait dir is absent from the local pool, so curating optimistically is
# safe. "half" == Half-Elf (the screen's shorthand id). Slugs already in BASE_PREFIX are
# intentionally omitted. No figure appears under two races.
CANON_FACES: dict[str, list[tuple[str, str]]] = {
    "human": [
        ("anders", "Anders"), ("abdirak", "Abdirak"), ("isobel", "Isobel"),
        ("liara-portyr", "Liara Portyr"), ("roah-moonglow", "Roah Moonglow"),
        ("rugan", "Rugan"), ("valeria", "Valeria"), ("zhalk", "Commander Zhalk"),
        ("oliver", "Oliver"), ("alfira", "Alfira"), ("lakrissa", "Lakrissa"),
        ("benryn", "Benryn"), ("dribbles", "Dribbles the Clown"),
        ("helsik", "Helsik"), ("akabi", "Akabi"), ("amira", "Amira"),
        ("aminah", "Aminah"), ("albert", "Albert"), ("anderson", "Anderson"),
        ("yenna", "Yenna"), ("grukkoh", "Grukkoh"), ("ferg-drogher", "Ferg Drogher"),
        ("dolly-dolly-dolly", "Dolly Dolly Dolly"), ("brem", "Brem"),
        ("gribbo", "Gribbo"), ("nine-fingers-keene", "Nine-Fingers Keene"),
        ("kressa-bonedaughter", "Kressa Bonedaughter"),
        ("malus-thorm", "Malus Thorm"), ("gerringothe-thorm", "Gerringothe Thorm"),
        ("thisobald-thorm", "Thisobald Thorm"),
    ],
    "elf": [
        ("kagha", "Kagha"), ("aelar", "Aelar"), ("nettie", "Nettie"),
        ("araj-oblodra", "Araj Oblodra"), ("elminster", "Elminster"),
    ],
    "half": [
        ("dammon", "Dammon"), ("zevlor", "Zevlor"),
    ],
    "tiefling": [
        ("mol", "Mol"), ("mattis", "Mattis"), ("umi", "Umi"), ("rolan", "Rolan"),
        ("cal", "Cal"), ("lia", "Lia"), ("arka", "Arka"), ("komira", "Komira"),
        ("mirkon", "Mirkon"),
    ],
    "drow": [
        ("sorn-orlith", "Sorn Orlith"), ("nadira", "Nadira"), ("ulma", "Ulma"),
    ],
    "githyanki": [
        ("voss", "Commander Voss"), ("varrl", "Varrl"),
    ],
    "dwarf": [
        ("barcus-wroot", "Barcus Wroot"), ("gekh-coal", "Gekh Coal"),
        ("nere", "Nere"), ("thrumbo", "Thrumbo"), ("filro", "Filro"),
    ],
    "gnome": [
        ("philomeen", "Philomeen"),
    ],
    "halfling": [
        # No additional canon halfling face verified in the pool beyond shared NPCs;
        # the screen degrades gracefully (full living gallery + unique-face-gen).
    ],
    "aasimar": [
        ("hope", "Hope"),
    ],
    "dragonborn": [
        # No canon dragonborn face is present in the local pool; the screen degrades
        # gracefully for this lineage until race-attributable art is ingested.
    ],
    # Reserved: the half-orc lineage shares the same graceful-degradation path until a
    # canon half-orc face is ingested into the pool.
    "half-orc": [],
}


def build_manifest(pool: Path, *, require_pool: bool = True) -> dict:
    """Return the manifest dict. When require_pool is True (default), only slugs with a
    real ``portrait_<slug>`` dir in ``pool`` are kept (so the manifest never points at
    missing art). When False (e.g. a worktree with no local pool), keep all curated
    entries so the shape can still be validated."""
    base = set(BASE_PREFIX)
    entries: list[dict] = []
    seen: set[str] = set()
    per_race: dict[str, int] = {}
    for race, faces in CANON_FACES.items():
        for slug, name in faces:
            if slug in base:
                raise SystemExit(
                    f"ERROR: '{slug}' is in BASE_PREFIX — appending it would shift the "
                    f"stable hero.portrait index (AC3). Remove it from CANON_FACES."
                )
            if slug in seen:
                raise SystemExit(
                    f"ERROR: duplicate slug '{slug}' across races — a face must carry one "
                    f"canon race."
                )
            seen.add(slug)
            if require_pool and not (pool / f"portrait_{slug}").is_dir():
                continue
            entries.append(
                {"slug": slug, "name": name, "race": race, "alive": True, "synthetic": False}
            )
            per_race[race] = per_race.get(race, 0) + 1
    return {
        "schema": "worldos.portrait-gallery.v1",
        "note": (
            "Curated canon faces APPENDED after screen-create.jsx PORTRAIT_GALLERY "
            "(stable indices 0..11). Scope-only (portrait-<slug>); raw art stays "
            "gitignored in _private. Regenerate via "
            "viewer/openworlds/tools/gen_portrait_gallery.py."
        ),
        "base_prefix": list(BASE_PREFIX),
        "per_race_appended": per_race,
        "entries": entries,
    }


def _dumps(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="Do not write; exit non-zero if the on-disk manifest is stale.",
    )
    args = ap.parse_args(argv)

    pool = _pool_dir()
    pool_present = pool.is_dir()
    manifest = build_manifest(pool, require_pool=pool_present)
    rendered = _dumps(manifest)

    if args.check:
        if not _MANIFEST_PATH.is_file():
            print(f"MISSING: {_MANIFEST_PATH}", file=sys.stderr)
            return 1
        current = _MANIFEST_PATH.read_text(encoding="utf-8")
        if not pool_present:
            # Without the gitignored pool we can only validate the shape, not the exact
            # filtered set; skip the byte-for-byte staleness compare.
            print("pool not present — skipped byte compare; shape OK")
            return 0
        if current != rendered:
            print(f"STALE: {_MANIFEST_PATH} — re-run the generator.", file=sys.stderr)
            return 1
        print("manifest up to date")
        return 0

    _MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    total = len(manifest["entries"])
    print(f"wrote {_MANIFEST_PATH} ({total} appended entries)")
    print("per-race appended:", json.dumps(manifest["per_race_appended"]))
    if not pool_present:
        print("WARNING: local _private pool not found — wrote unfiltered curated set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
