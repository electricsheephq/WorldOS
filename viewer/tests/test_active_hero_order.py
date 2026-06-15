"""GROUP C / #seat-order — the active hero is the engine's AUTHORITATIVE kind=player actor, not party[0].

The owner, playing the real shipped macOS .app, saw a banned/stale companion shown as the hero —
the "ACTIVE ASTARION / Lvl 1 Adventurer" display bug. ROOT CAUSE (verified, NOT a #912 seat
bypass): the OpenWorlds table screen picked the active hero by PARTY ORDER, not by the engine's
authoritative active actor. `viewer/openworlds/screen-table.jsx` seeded
`activeHero = party[0]?.id`, so whoever the engine happened to list FIRST (a stale companion like
Astarion) became the displayed hero — and the header's hero lookup fell to the no-class
`{class:"Adventurer", level:1}` placeholder.

THE FIX (additive, viewer read-only): `resolveActiveHeroId(surface, party)` resolves the hero from
the engine's authoritative actor FIRST — `surface.actor.id` (the engine's active actor, itself
already a kind=player PC; see viewer/server.py `_action_actor`), then the first party card with
`kind === "player"`, and ONLY THEN `party[0]` as a last resort. So the displayed/active hero is
always the real player-controlled PC, never a stale companion ordered first.

These tests exercise the REAL shipped browser function (no reimplementation): mirroring the sibling
JS-behavior tests (test_chronicle_hygiene.py) we brace-match `resolveActiveHeroId` out of the actual
`screen-table.jsx` and eval it under Node, so the test tracks shipped behavior. Skipped if Node is
not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
SCREEN_TABLE = OPENWORLDS / "screen-table.jsx"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _extract_fn(src: str, name: str) -> str:
    """Brace-match a top-level `function name(...) { ... }` out of the source (mirror of the
    sibling test_chronicle_hygiene._extract_fn)."""
    marker = f"function {name}("
    start = src.index(marker)
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"could not brace-match {name}()")


def _resolve(surface, party) -> str:
    src = SCREEN_TABLE.read_text(encoding="utf-8")
    fn = _extract_fn(src, "resolveActiveHeroId")
    snippet = (
        fn
        + "\nconst __surface = "
        + json.dumps(surface)
        + ";\nconst __party = "
        + json.dumps(party)
        + ";\nprocess.stdout.write(String(resolveActiveHeroId(__surface, __party)));\n"
    )
    proc = subprocess.run([_node(), "-e", snippet], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return proc.stdout


# A realistic stale-order party: the COMPANION is listed first (party[0]) and the real PC is later —
# the exact shape that surfaced "ACTIVE ASTARION" as the hero under the old party[0] seed.
_ASTARION = {"id": "astarion-1", "name": "Astarion", "kind": "companion", "class": "Rogue", "level": 3}
_PC = {"id": "pc-tav", "name": "Tav", "kind": "player", "class": "Wizard", "level": 1}


def test_companion_first_is_not_shown_as_the_hero_when_actor_is_the_pc():
    # The core regression: surface.actor is the authoritative kind=player PC and party[0] is a
    # companion. The displayed active hero MUST be the PC, never party[0]. (Old code returned
    # party[0].id = "astarion-1" — the "ACTIVE ASTARION" bug.)
    surface = {"actor": {"id": "pc-tav", "name": "Tav", "kind": "player"}}
    party = [_ASTARION, _PC]
    assert _resolve(surface, party) == "pc-tav"


def test_actor_wins_over_party_order_even_when_pc_is_last():
    # The authoritative actor is honoured regardless of where the PC sits in the roster.
    surface = {"actor": {"id": "pc-tav", "name": "Tav", "kind": "player"}}
    party = [_ASTARION, {"id": "shadowheart-1", "name": "Shadowheart", "kind": "companion"}, _PC]
    assert _resolve(surface, party) == "pc-tav"


def test_falls_back_to_kind_player_when_no_actor():
    # No surface.actor (e.g. a surface that predates the field) → still pick the kind=player PC,
    # not the companion at party[0].
    surface = {}
    party = [_ASTARION, _PC]
    assert _resolve(surface, party) == "pc-tav"


def test_falls_back_to_kind_player_when_actor_not_in_roster():
    # surface.actor names an id that is NOT a current party card (stale/cross-campaign) → ignore it
    # and pick the kind=player PC, never the companion party[0].
    surface = {"actor": {"id": "ghost-actor", "kind": "player"}}
    party = [_ASTARION, _PC]
    assert _resolve(surface, party) == "pc-tav"


def test_last_resort_is_party0_when_no_actor_and_no_player_card():
    # Neither an authoritative actor nor a kind=player card (e.g. a demo/companion-only surface) →
    # preserve today's behavior: fall back to party[0].id. (Invariant: keep the existing fallback.)
    surface = {}
    party = [{"id": "c1", "name": "Comp One", "kind": "companion"},
             {"id": "c2", "name": "Comp Two", "kind": "companion"}]
    assert _resolve(surface, party) == "c1"


def test_empty_party_yields_empty_string():
    # No roster at all → empty id (the component's own hero-lookup then uses its placeholder). The
    # resolver must not throw on an empty/absent party.
    assert _resolve({}, []) == ""
    assert _resolve({"actor": {"id": "x", "kind": "player"}}, []) == ""


def test_actor_preferred_even_when_a_different_player_card_exists():
    # If the engine names a SPECIFIC active actor, honour that exact id even if another kind=player
    # card is also present (multi-PC roster) — the engine's actor is the truth, not "first player".
    surface = {"actor": {"id": "pc-two", "kind": "player"}}
    party = [
        {"id": "pc-one", "name": "Aldric", "kind": "player"},
        {"id": "pc-two", "name": "Bryn", "kind": "player"},
    ]
    assert _resolve(surface, party) == "pc-two"


def test_live_correction_reseeds_to_pc_not_party0_companion_when_active_hero_leaves_roster():
    # The LIVE-UPDATE correction path (screen-table.jsx effect ~907): the previously active hero
    # ("pc-old") has left the roster mid-session (companion churn / campaign switch) and a COMPANION
    # has sorted to party[0]. The effect re-seeds through this resolver — it MUST land on the engine's
    # authoritative kind=player actor (the PC), NEVER party[0]. #932 fixed the initial seed but left
    # this correction effect on the old party[0] path, which would re-pick the companion and resurrect
    # the "ACTIVE ASTARION / Lvl 1 Adventurer" bug on the live path. (Under Node, the old line 909 —
    # `party[0]?.id` — returned "astarion-1" for this exact shape; the resolver returns "pc-tav".)
    surface = {"actor": {"id": "pc-tav", "name": "Tav", "kind": "player"}}
    party = [_ASTARION, _PC]  # companion at party[0], the real PC later; "pc-old" is gone
    assert _resolve(surface, party) == "pc-tav"
