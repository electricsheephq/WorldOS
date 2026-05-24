"""World lore retrieval (lorebook): authored-canon precedence, era, FTS safety.

Guards the adversarial-review fixes: ingested wiki pages must NOT out-rank the seed's
authored (post-canon) pages, and a page's chronology must surface per hit."""

import lorebook


def _world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))
    lore = tmp_path / "worlds" / "tw" / "lore"
    (lore / "wiki").mkdir(parents=True)
    return lore


def test_authored_canon_outranks_stale_wiki(tmp_path, monkeypatch):
    lore = _world(tmp_path, monkeypatch)
    # authored: short, post-canon truth (he's dead) + an era line
    (lore / "gortash.md").write_text(
        "# Gortash\n*Era: 1492 DR*\nEnver Gortash is dead, slain in the Battle of Baldur's Gate.\n",
        encoding="utf-8",
    )
    # ingested wiki: long + stale (alive) — would win on bm25 without tier precedence
    (lore / "wiki" / "gortash.md").write_text(
        "# Gortash\n" + "Enver Gortash is the living Archduke of Baldur's Gate. " * 60,
        encoding="utf-8",
    )
    hits = lorebook.lookup_lore("tw", "Gortash Archduke", 3)
    assert hits and hits[0]["title"] == "Gortash"
    assert "dead" in hits[0]["excerpt"].lower()   # authored post-canon page won, not the wiki
    assert hits[0]["era"] == "1492 DR"            # chronology parsed + surfaced per hit


def test_no_hit_empty_and_injection_safe(tmp_path, monkeypatch):
    lore = _world(tmp_path, monkeypatch)
    (lore / "a.md").write_text("# A Place\nsome content about a quiet town\n", encoding="utf-8")
    assert lorebook.lookup_lore("tw", "zzzznotpresent") == []      # no match
    assert lorebook.lookup_lore("no-such-world", "anything") == []  # no corpus
    assert lorebook.lookup_lore("tw", "!@#$%^&*()") == []           # sanitized, no crash
    assert lorebook.page_count("tw") == 1


def test_page_era_parsing():
    assert lorebook._page_era("# X\n*Era: 1492 DR — winter*\nbody") == "1492 DR — winter"
    assert lorebook._page_era("# X\nstatus: ruined\nbody") == "ruined"
    assert lorebook._page_era("# X\nno chronology line here") == ""


# --- S5: world-state de-confliction of the .md corpus (the two-surface fix) -----------

def test_lookup_lore_args_default_to_byte_identical():
    # ADDITIVE: the new keyword args are no-ops by default, so a positional call (and an
    # explicit supersedes=None / canon_header="") returns EXACTLY today's result.
    import os, lorebook as lb
    # reuse the module's content-dir resolution via a tmp world built inline
    # (here we lean on the dedicated tmp_path tests below for corpus shape; this one
    # just proves the no-op equivalence on the shipped corpus).
    base = lb.lookup_lore("baldurs-gate", "Gortash Archduke", 5)
    assert base == lb.lookup_lore("baldurs-gate", "Gortash Archduke", 5, supersedes=None, canon_header="")
    assert base == lb.lookup_lore("baldurs-gate", "Gortash Archduke", 5, supersedes=[], canon_header="")


def test_lookup_lore_drops_superseded_authored_hit_when_clean_exists(tmp_path, monkeypatch):
    # A tier-0 authored excerpt asserting a now-superseded fact must NOT lead the hits:
    # with a clean hit available it is DROPPED; the corrected canon (header / other pages)
    # leads instead. This is the structural close of the two-surface bug.
    lore = _world(tmp_path, monkeypatch)
    (lore / "city.md").write_text(
        "# The City\n*Era: after the war*\nThe tyrant Gortash is dead and the seat is empty.\n",
        encoding="utf-8",
    )
    (lore / "watch.md").write_text(
        "# The Watch\nThe Steel Watch patrols every street under the Archduke Gortash.\n",
        encoding="utf-8",
    )
    subs = ["gortash is dead", "the seat is empty"]
    hdr = "CURRENT WORLD (authoritative): tenor=grim — gortash=archduke. Treat as canon."
    hits = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=subs, canon_header=hdr)
    # the header leads; the contradicting "Gortash is dead" page is gone from the result
    assert hits[0]["source"] == "world-state" and hits[0]["excerpt"] == hdr
    bodies = [h["excerpt"].lower() for h in hits if h["source"] != "world-state"]
    assert not any("gortash is dead" in b for b in bodies), "superseded excerpt must be dropped"
    assert any("patrols every street" in b for b in bodies), "the non-contradicting page survives"


def test_lookup_lore_demotes_superseded_hit_when_no_clean_alternative(tmp_path, monkeypatch):
    # If EVERY matching page contradicts, the demoted hit is still returned (we don't hide
    # all canon), but the authoritative header leads it so the DM's ground truth is correct.
    lore = _world(tmp_path, monkeypatch)
    (lore / "only.md").write_text(
        "# Only Page\nGortash is dead, slain at the battle.\n", encoding="utf-8",
    )
    subs = ["gortash is dead"]
    hdr = "CURRENT WORLD (authoritative): tenor=grim — gortash=archduke. Treat as canon."
    hits = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=subs, canon_header=hdr)
    assert hits[0]["source"] == "world-state"  # the corrected canon leads
    # the lone contradicting page is demoted but still present (better than empty)
    assert any("gortash is dead" in h["excerpt"].lower() for h in hits[1:])


def test_lookup_lore_no_header_when_unset_but_still_deconflicts(tmp_path, monkeypatch):
    # supersedes without a header still drops the contradiction (filter is independent of
    # the belt-and-suspenders header); and with neither, the contradicting page is returned.
    lore = _world(tmp_path, monkeypatch)
    (lore / "city.md").write_text("# City\nGortash is dead here.\n", encoding="utf-8")
    (lore / "watch.md").write_text("# Watch\nGortash the Archduke rules the watch.\n", encoding="utf-8")
    deconflicted = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=["gortash is dead"])
    assert deconflicted and deconflicted[0]["source"] != "world-state"  # no header injected
    assert not any("gortash is dead" in h["excerpt"].lower() for h in deconflicted)
    # baseline: no de-confliction -> the contradicting page can appear
    raw = lorebook.lookup_lore("tw", "Gortash", 5)
    assert any("gortash is dead" in h["excerpt"].lower() for h in raw)
