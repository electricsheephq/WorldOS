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
