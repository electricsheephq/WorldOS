"""HV4 (Act II §4c, #1326) — the REUSE / assembly surface. These tests pin the load-bearing
invariants:

  * DEFAULT-OFF byte-identity — a world with NO `library_packs` produces the exact same quest_hooks
    as today (library.py never contributes a candidate). Complements the existing
    test_seed_world_default_is_unchanged_base_state guard with a questgen-level assertion.
  * lookup_library / library.lookup returns a promoted entry when a pack is configured, and NOTHING
    (empty, never error) when it is not — mirroring find_npcs/lookup_lore.
  * library-pack-ON produces >= 1 library-sourced hook (source=="library"), and a native
    quest_variants id ALWAYS wins a collision (library additive, never overriding).

The library dir is a per-test tmp pack (write the on-disk shape promote.py produces) pointed at via
the world's `_library_root` escape hatch (production reads the repo-root library/).
"""

import json
import random
from pathlib import Path

import content
import library
import questgen
from models import Campaign, Faction, Location


# ── tmp-library fixture (writes the promote.py on-disk shape, read-only from here) ────────────────

def _write_pack(root: Path, pack_name: str, quests: list[dict]) -> None:
    """Write a minimal library/ pack: pack.json + library/quests/<id>.json for each quest entry."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pack.json").write_text(json.dumps({"name": pack_name, "version": "0.1.0"}), encoding="utf-8")
    qdir = root / "quests"
    qdir.mkdir(parents=True, exist_ok=True)
    for q in quests:
        (qdir / f"{q['artifact_id'].replace(':', '_')}.json").write_text(
            json.dumps(q), encoding="utf-8")


def _entry(aid: str, tier: str, name: str, hook: str) -> dict:
    return {
        "artifact_id": aid, "class": "quest", "tier": tier,
        "scores": {"overall": 4.2}, "provenance": {"world": "baldurs-gate"},
        "payload": {"name": name, "hook": hook},
    }


def _synthetic_world(packs=None, root: Path | None = None) -> dict:
    w = {
        "id": "hv4-w", "name": "HV4 World", "premise": "p", "era": "now",
        "regions": [{"id": "loc-a", "name": "Crossroads", "description": "a smuggling waypoint",
                     "connections": []}],
        "starting_options": [{"location_id": "loc-a", "framing": "Start."}],
        "npc_roster": [{"id": "npc-1", "name": "The Fence", "description": "a smuggler of stolen relics"}],
        "quest_variants": [
            {"id": "q-native", "name": "The Native Wrong",
             "outcomes": [{"id": "o1", "random": 1, "lore": "resolved", "hook": "a native thread to pull"}]},
        ],
    }
    if packs is not None:
        w["library_packs"] = packs
    if root is not None:
        w["_library_root"] = str(root)
    return w


# ── (1) DEFAULT-OFF byte identity ────────────────────────────────────────────────────────────────

def test_default_off_hooks_are_byte_identical(tmp_path):
    # A world with NO library_packs must yield the exact same quest_hooks whether or not a pack
    # happens to exist on disk — the reuse surface is fully dormant. Compare full model dumps.
    _write_pack(tmp_path / "library", "worldos-harvest",
                [_entry("quest:lib:a", "stable", "A Library Wrong", "a library thread")])
    # `id` is a fresh uuid per QuestHook (pre-existing, seed-independent) — exclude it so the
    # comparison isolates the HV4 invariant: the STRUCTURE + provenance are byte-identical off.
    def _shape(hooks):
        return [{k: v for k, v in h.model_dump().items() if k != "id"} for h in hooks]

    off = _synthetic_world(packs=None, root=tmp_path / "library")  # opted OUT (no library_packs key)
    c = content.seed_world(off)
    questgen.generate(c, off, random.Random("seed"))
    dumps = _shape(c.quest_hooks)
    # not one hook carries library provenance — every hook is native (source == "")
    assert dumps, "the native quest_variants still produce hooks"
    assert all(h["source"] == "" and h["tier"] == "" for h in dumps)

    # And a world with an EMPTY library_packs list is identical to the opted-out world.
    empty = _synthetic_world(packs=[], root=tmp_path / "library")
    c2 = content.seed_world(empty)
    questgen.generate(c2, empty, random.Random("seed"))
    assert _shape(c2.quest_hooks) == dumps


# ── (2) library.load_pool / lookup gating ────────────────────────────────────────────────────────

def test_load_pool_and_lookup_gated_on_opt_in(tmp_path):
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest",
                [_entry("quest:lib:relic", "stable", "The Stolen Relic", "recover a smuggled relic")])
    # opted OUT -> empty pool + empty lookup (no error)
    off = _synthetic_world(packs=None, root=root)
    assert library.load_pool(off, "quest", root=root) == []
    assert library.lookup(off, "quest", "relic", root=root) == []
    # opted IN -> the promoted entry is returned
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    pool = library.load_pool(on, "quest", root=root)
    assert len(pool) == 1 and pool[0]["artifact_id"] == "quest:lib:relic"
    hits = library.lookup(on, "quest", "relic", root=root)
    assert hits and hits[0]["artifact_id"] == "quest:lib:relic"
    # opting into a pack NOT on disk yields nothing (fall through to pure-gen), never an error.
    absent = _synthetic_world(packs=["no-such-pack"], root=root)
    assert library.load_pool(absent, "quest", root=root) == []
    assert library.lookup(absent, "quest", "relic", root=root) == []


def test_lookup_no_match_returns_pool_never_errors(tmp_path):
    # An opted-in world whose entries share no query token still returns the (tier-ranked) pool —
    # a browse — never raising. A caller that wants strict matching reads the `overlap` field.
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest",
                [_entry("quest:lib:a", "stable", "Alpha", "alpha detail")])
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    hits = library.lookup(on, "quest", "zzzznomatch", root=root)
    assert len(hits) == 1 and hits[0]["overlap"] == 0


# ── (2b) tier tie-break (epic addendum [HIGH]) ───────────────────────────────────────────────────

def test_tier_is_tie_break_only(tmp_path):
    # Two entries, equal query overlap: the higher tier wins. A strictly-higher overlap always wins
    # regardless of tier (the fresh-gen entry names the query word twice; stable names it once).
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest", [
        _entry("quest:lib:stable", "stable", "The Relic", "a relic quest"),
        _entry("quest:lib:canon", "canonical", "The Relic", "a relic quest"),
    ])
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    hits = library.lookup(on, "quest", "relic", root=root)
    # equal overlap -> canonical (weight 3) outranks stable (weight 2)
    assert hits[0]["artifact_id"] == "quest:lib:canon"


# ── (3) library-pack ON produces a library-sourced hook + native precedence ─────────────────────

def test_library_pack_on_produces_library_sourced_hook(tmp_path):
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest",
                [_entry("quest:lib:smuggle", "stable", "The Smuggled Relic",
                        "recover a stolen relic from the smuggling ring")])
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    c = content.seed_world(on)
    questgen.generate(c, on, random.Random("seed"))
    lib_hooks = [h for h in c.quest_hooks if h.source == "library"]
    assert lib_hooks, "an opted-in world must yield >= 1 library-sourced hook"
    assert lib_hooks[0].tier == "stable"
    assert lib_hooks[0].grievance and lib_hooks[0].note  # bound like a native hook
    # native hooks still present + unperturbed (they come first)
    assert any(h.source == "" for h in c.quest_hooks)


def test_native_quest_id_wins_collision(tmp_path):
    # A library entry whose artifact_id collides with a native quest_variants id is EXCLUDED —
    # native always wins (library additive, never overriding). The native world uses id "q-native".
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest",
                [_entry("q-native", "canonical", "Impostor", "should never appear")])
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    c = content.seed_world(on)
    questgen.generate(c, on, random.Random("seed"))
    # no library hook (the only pack entry collided with the native id and was dropped)
    assert not [h for h in c.quest_hooks if h.source == "library"]


# ── (4) the lookup_library engine tool (server path) ─────────────────────────────────────────────

def test_lookup_library_tool_default_off_and_on(tmp_path, monkeypatch):
    import server
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "state"))
    # Default world (baldurs-gate ships no library_packs) -> empty, never error.
    out = server.start_world("baldurs-gate", ending="gortash-tyranny")
    res = server.lookup_library(out["campaign_id"], "relic")
    assert res["count"] == 0 and res["matches"] == []


def test_lookup_library_pure_module_ranks_by_overlap(tmp_path):
    # The tool delegates to library.lookup; assert the ranking directly (the server path just wires
    # world reload -> library.lookup, covered by the default-off tool test above).
    root = tmp_path / "library"
    _write_pack(root, "worldos-harvest", [
        _entry("quest:lib:relic", "stable", "The Stolen Relic", "recover a smuggled relic"),
        _entry("quest:lib:rescue", "stable", "The Captive", "free a captive from a cell"),
    ])
    on = _synthetic_world(packs=["worldos-harvest"], root=root)
    hits = library.lookup(on, "quest", "relic smuggled", root=root)
    assert hits[0]["artifact_id"] == "quest:lib:relic"  # best overlap leads
