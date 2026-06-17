"""A banned BG3-ORIGIN hero must never be seated as the PLAYER (#305 sibling gate).

The owner banned the seven core Baldur's Gate 3 origin heroes — Astarion, Gale, Karlach,
Lae'zel, Shadowheart, Wyll (and Halsin) — as PLAYABLE PCs: they may ONLY appear as temporary
COMPANIONS / lore NPCs, never as the hero the player embodies. Each ships marked
`"playable": false`, and `content.is_playable(rec)` is the ONE canonical origin-ban predicate
the playable surfaces already honour:
  * `content.list_canon_characters(playable_only=True)` drops them from the picker;
  * `server.start_character(origin="pickup:<name>")` refuses them with a "legend of this era"
    error (server.py ~1830, `not is_playable(rec)`).

But `server.load_canon_character(name, kind="player", …)` SEATED whatever name was passed
WITHOUT re-checking `is_playable` — so a DM/seed that NAMED an origin as the player seated
them anyway (the filter existed, the seat didn't enforce). These guard the additive engine
gate that mirrors the existing #305 DEAD gate:
  * `kind="player"` of a banned origin (playable:false) is REJECTED with a clear error;
  * `kind="companion"` / `kind="npc"` of that SAME origin still SUCCEEDS (origins are allowed
    as temporary companions / lore NPCs — only the PC seat is guarded);
  * a LIVING, NON-origin (playable) canon figure as the player is UNCHANGED (happy path).
"""

import content
import server

WORLD = "baldurs-gate"
ORIGIN = "Astarion"          # banned BG3 origin hero (playable:false), alive in canon
# Gale is ALSO a banned origin (playable:false, alive) but is NOT pre-seeded into the world,
# so the companion/npc load runs the FRESH-seat path (not the `already_present` short-circuit
# that a pre-seeded NPC like Astarion would hit) — the clean assertion for "still a companion".
ORIGIN_UNSEEDED = "Gale"
LIVING_PC = "Aubree"         # living half-elf ranger, playable minor figure (the happy path)


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data(WORLD))
    server.save_campaign(c)
    return c


# --- the canon corpus actually marks the origins playable:false (and alive) -------

def test_astarion_is_a_banned_living_origin_in_canon():
    # Belt-and-suspenders: the gate must trip on the ORIGIN-BAN flag, not on death — Astarion
    # is alive, so the existing #305 dead-gate would NOT catch him. The new gate must.
    rec = content.load_canon_character(WORLD, ORIGIN)
    assert rec is not None, "Astarion must resolve from the canon roster"
    assert content.is_playable(rec) is False, "a banned origin ships playable:false"
    assert content.is_dead_record(rec) is False, "Astarion is alive — not caught by the dead gate"


# --- the hard seat gate: banned origin rejected as the PC --------------------------

def test_seat_guard_rejects_a_banned_origin_player(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, ORIGIN, kind="player", add_to_party=True)
    assert res.get("error"), "seating a banned origin as the PC must return an error"
    assert res.get("origin_banned") is True
    assert "companion" in res["error"].lower(), "the error must point at companion-only use"
    # and nothing got seated as the player
    chars = server._require(c.id).characters.values()
    assert not any(ch.kind == "player" for ch in chars)


def test_banned_origin_still_loadable_as_a_companion(tmp_path, monkeypatch):
    # The ban is targeted at the PC ONLY — an origin CAN be a temporary companion. Gale is an
    # un-seeded banned origin, so this runs the FRESH companion seat (not `already_present`).
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, ORIGIN_UNSEEDED, kind="companion", add_to_party=True)
    assert "error" not in res, "a banned origin must still load as a companion"
    assert res["kind"] == "companion"


def test_banned_origin_still_loadable_as_a_lore_npc(tmp_path, monkeypatch):
    # …and as a lore NPC (Bestiary Persons tab). Only kind="player" is guarded.
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, ORIGIN_UNSEEDED, kind="npc")
    assert "error" not in res, "a banned origin must still load as a lore NPC"
    assert res["kind"] == "npc"


def test_living_non_origin_canon_pc_is_seatable(tmp_path, monkeypatch):
    # HAPPY PATH UNCHANGED: a living, playable (non-origin) canon figure seats as the PC.
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, LIVING_PC, kind="player", add_to_party=True)
    assert "error" not in res, res
    ch = server._require(c.id).characters[res["id"]]
    assert ch.kind == "player" and ch.id in server._require(c.id).party


def test_synthetic_banned_origin_blocked_playable_record_allowed(tmp_path, monkeypatch):
    # Belt-and-suspenders with a SYNTHETIC pair (robust to canon-corpus edits): a playable:false
    # record is gated as the PC, an otherwise-identical playable record is not. Patch the content
    # loader the tool calls (same technique as test_canon_abilities / test_playable_alive).
    c = _seed(tmp_path, monkeypatch)
    banned = {"name": "Origin Hero", "class": "Fighter", "level": "3", "playable": False,
              "backstory": "Origin Hero is a living legend of the Sword Coast, a top hero."}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(banned))
    res = server.load_canon_character(c.id, "Origin Hero", kind="player", add_to_party=True)
    assert res.get("origin_banned") is True and res.get("error")
    # …but the SAME banned record IS allowed as a companion.
    res_comp = server.load_canon_character(c.id, "Origin Hero", kind="companion")
    assert "error" not in res_comp and res_comp["kind"] == "companion"

    playable = {"name": "Minor Figure", "class": "Fighter", "level": "3", "playable": True,
                "backstory": "Minor Figure is a living guard who patrols the Lower City."}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(playable))
    res2 = server.load_canon_character(c.id, "Minor Figure", kind="player", add_to_party=True)
    assert "error" not in res2 and not res2.get("origin_banned")
