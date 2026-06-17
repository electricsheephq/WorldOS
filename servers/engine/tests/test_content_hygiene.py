"""F10-7 — wiki magic-word directive hygiene in canon character prose.

The ingested canon backstories carry leading MediaWiki magic words — ``__notoc__`` and
the uppercase ``__NOTOC__`` (and a stray ``__TOC__``) — which is editor markup, not lore.
``_backstory_snippet`` (the picker card) and ``load_canon_character`` (the prose the DM
voices + the portrait prompt) read the field VERBATIM, so the directive rode straight into
the player-facing surface (516/516 within the 220-char snippet window per the audit).

These tests pin two things:
  1. a load-time BELT — the snippet + the canon-record read strip the directive (case-
     INsensitively, anywhere in the string, not just a leading prefix);
  2. a CI INVARIANT — zero case-insensitive directive hits remain under any shipped
     world's characters/ dir (the one-shot strip script cleaned the corpus).

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F10-7, #758 enrichment — case-insensitivity).
"""
import json
import re

import content


# --- the pure helper: strip directives anywhere, case-insensitively ------------------
def test_strip_wiki_directives_handles_case_and_position():
    f = content.strip_wiki_directives
    assert f("__notoc__A famed hunter.") == "A famed hunter."
    assert f("__NOTOC__A famed hunter.") == "A famed hunter."   # uppercase (170 of 515 files)
    assert f("__TOC__A famed hunter.") == "A famed hunter."
    # consecutive directives collapse, leading whitespace trimmed
    assert f("__notoc__ __NOTOC__  A hunter.") == "A hunter."
    # a directive embedded MID-string is stripped too (anywhere, not just the prefix)
    assert f("A hunter. __notoc__ A second line.") == "A hunter. A second line."
    # a non-directive double-underscore token is left alone (only __word__ shapes go)
    assert f("a __b c__ d") == "a __b c__ d"
    assert f("snake_case stays") == "snake_case stays"
    # empty / non-directive prose is unchanged
    assert f("") == ""
    assert f("Just a plain backstory.") == "Just a plain backstory."


# --- the load-time belt: snippet + record read are clean -----------------------------
def _write_world(tmp_path, monkeypatch, rec):
    monkeypatch.setenv("WORLDOS_CONTENT_DIR", str(tmp_path))
    cdir = tmp_path / "worlds" / "hygiene-test" / "characters"
    cdir.mkdir(parents=True)
    (cdir / "subject.json").write_text(json.dumps(rec), encoding="utf-8")


def test_backstory_snippet_strips_directives(tmp_path, monkeypatch):
    # _backstory_snippet is the picker card text — it must never show the magic word.
    assert not content._backstory_snippet("__notoc__A wandering scholar.").startswith("__")
    assert content._backstory_snippet("__NOTOC__A wandering scholar.") == "A wandering scholar."


def test_load_canon_character_strips_directives_from_prose(tmp_path, monkeypatch):
    _write_world(tmp_path, monkeypatch, {
        "name": "Subject",
        "backstory": "__NOTOC__A famed monster hunter who never returned.",
        "appearance": "__notoc__Scarred, lean, grey-eyed.",
        "personality": "Wry.",  # no directive — untouched
    })
    rec = content.load_canon_character("hygiene-test", "Subject")
    assert rec is not None
    assert rec["backstory"] == "A famed monster hunter who never returned."
    assert rec["appearance"] == "Scarred, lean, grey-eyed."
    assert rec["personality"] == "Wry."


def test_roster_surface_snippet_is_directive_free(tmp_path, monkeypatch):
    _write_world(tmp_path, monkeypatch, {
        "name": "Subject", "race": "Human", "class": "Ranger", "level": "3",
        "backstory": "__NOTOC__A famed monster hunter.",
    })
    r = content.roster_surface("hygiene-test", playable_only=False, alive_only=False)
    card = next(c for c in r["characters"] if c["name"] == "Subject")
    assert "__" not in card["backstory"]
    assert card["backstory"] == "A famed monster hunter."


# --- the CI invariant: the shipped corpus is clean -----------------------------------
# Case-INsensitive (re.IGNORECASE) is load-bearing: a case-SENSITIVE scan finds only 344
# of the 515 dirty files — ~170 are uppercase __NOTOC__ (#758 enrichment).
_DIRECTIVE = re.compile(r"__[a-z]+__", re.IGNORECASE)


def test_no_wiki_directives_in_any_shipped_canon_character():
    offenders = []
    for w in content.list_worlds():
        for cdir in content._characters_dirs(w["id"]):
            if not cdir.is_dir():
                continue
            for p in sorted(cdir.glob("*.json")):
                try:
                    raw = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                # scan the STRING VALUES (a key could legitimately be __proto__-ish in JSON,
                # though none are; the prose fields are what reach the player).
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for field in ("backstory", "appearance", "personality", "mannerisms"):
                    val = rec.get(field)
                    if isinstance(val, str) and _DIRECTIVE.search(val):
                        offenders.append(f"{w['id']}/{p.name}:{field}")
    assert not offenders, (
        f"{len(offenders)} canon prose fields still carry a wiki magic-word directive "
        f"(case-insensitive). Run servers/engine/scripts/strip_wiki_directives.py. "
        f"First few: {offenders[:5]}"
    )
