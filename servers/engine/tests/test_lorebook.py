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


def test_lookup_lore_redacts_superseded_sentence_keeping_clean_page_first(tmp_path, monkeypatch):
    # A tier-0 page asserting a now-superseded fact must NOT lead the hits and must not
    # LEAK the superseded sentence: the offending SENTENCE is redacted, the page is KEPT
    # (demoted below clean pages), and the corrected canon (header / clean pages) leads.
    # This is the structural close of the two-surface bug — at sentence granularity, so a
    # multi-fact page survives instead of being dropped whole.
    lore = _world(tmp_path, monkeypatch)
    # MULTI-FACT page: a valid city fact AND a superseded one in the SAME page.
    (lore / "city.md").write_text(
        "# The City\n*Era: after the war*\nThe City sits on the river Chionthar and trades far. "
        "The tyrant Gortash is dead and the seat is empty.\n",
        encoding="utf-8",
    )
    (lore / "watch.md").write_text(
        "# The Watch\nThe Steel Watch patrols every street under the Archduke Gortash.\n",
        encoding="utf-8",
    )
    subs = ["gortash is dead", "the seat is empty"]
    hdr = "CURRENT WORLD (authoritative): tenor=grim — gortash=archduke. Treat as canon."
    hits = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=subs, canon_header=hdr)
    # the header leads; the clean (unredacted) watch page out-ranks the redacted city page
    assert hits[0]["source"] == "world-state" and hits[0]["excerpt"] == hdr
    bodies = {h["source"]: h["excerpt"].lower() for h in hits if h["source"] != "world-state"}
    assert not any("gortash is dead" in b for b in bodies.values()), "superseded sentence must be redacted"
    # the multi-fact city page is KEPT (not dropped) with its VALID sentence intact...
    assert "city.md" in bodies and "river chionthar" in bodies["city.md"]
    # ...the superseded sentence elided in place (no leak, visible gap), and the page demoted
    assert "[…superseded…]" in bodies["city.md"]
    page_sources = [h["source"] for h in hits if h["source"] != "world-state"]
    assert page_sources.index("watch.md") < page_sources.index("city.md"), "clean page leads the redacted one"
    assert "patrols every street" in bodies["watch.md"], "the non-contradicting page survives"


def test_lookup_lore_redacts_but_keeps_page_when_no_clean_alternative(tmp_path, monkeypatch):
    # If the ONLY matching page contradicts, it is still returned (we don't hide all canon
    # / never go empty) with its VALID content intact and the superseded sentence redacted;
    # the authoritative header leads so the DM's ground truth is correct, and the
    # contradiction never leaks.
    lore = _world(tmp_path, monkeypatch)
    (lore / "only.md").write_text(
        "# Only Page\nGortash rose to power through the Steel Watch. Gortash is dead, slain at the battle.\n",
        encoding="utf-8",
    )
    subs = ["gortash is dead"]
    hdr = "CURRENT WORLD (authoritative): tenor=grim — gortash=archduke. Treat as canon."
    hits = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=subs, canon_header=hdr)
    assert hits[0]["source"] == "world-state"  # the corrected canon leads
    # the lone page is still present (better than empty) and its valid sentence survives...
    pages = [h for h in hits[1:] if h["source"] == "only.md"]
    assert pages and "rose to power" in pages[0]["excerpt"].lower()
    # ...but the superseded sentence is redacted, never leaked
    assert "gortash is dead" not in pages[0]["excerpt"].lower()
    assert "[…superseded…]" in pages[0]["excerpt"]


def test_lookup_lore_no_header_when_unset_but_still_deconflicts(tmp_path, monkeypatch):
    # supersedes without a header still redacts the contradiction (the filter is independent
    # of the belt-and-suspenders header); with neither, the contradicting sentence is returned.
    lore = _world(tmp_path, monkeypatch)
    (lore / "city.md").write_text("# City\nThe city endures. Gortash is dead here.\n", encoding="utf-8")
    (lore / "watch.md").write_text("# Watch\nGortash the Archduke rules the watch.\n", encoding="utf-8")
    deconflicted = lorebook.lookup_lore("tw", "Gortash", 5, supersedes=["gortash is dead"])
    assert deconflicted and deconflicted[0]["source"] != "world-state"  # no header injected
    assert not any("gortash is dead" in h["excerpt"].lower() for h in deconflicted)
    # the city page is kept, just with its superseded sentence redacted (not dropped whole)
    assert any(h["source"] == "city.md" and "the city endures" in h["excerpt"].lower() for h in deconflicted)
    # baseline: no de-confliction -> the contradicting sentence can appear
    raw = lorebook.lookup_lore("tw", "Gortash", 5)
    assert any("gortash is dead" in h["excerpt"].lower() for h in raw)


# --- S5 fixes: excerpt-vs-page granularity (the adversarial-review HIGH) ---------------
# These use MULTI-FACT pages (the original 9 tests used single-fact synthetic pages and so
# missed both the over- and under-suppression sides of the whole-page-drop bug).

def test_lookup_lore_no_oversuppression_no_unrelated_backfill(tmp_path, monkeypatch):
    # OVER-SUPPRESSION repro: a curated multi-fact page whose excerpt INCIDENTALLY contains
    # a superseded substring (in an unrelated sentence) must NOT be dropped whole — which
    # under the old logic evicted valid canon and let an unrelated page (a shoe shop) backfill
    # the top results. The curated page must survive with its queried content intact, and the
    # filler page must NOT be promoted into its place.
    lore = _world(tmp_path, monkeypatch)
    # The curated faction page: the query term ("Zhentarim") is here, AND — in a DIFFERENT
    # sentence — an incidental phrase the ending supersedes ("with the steel watch gone").
    (lore / "factions.md").write_text(
        "# Factions\n"
        "The Zhentarim are a Bane-tinged mercantile-mercenary cabal expanding into the power vacuum. "
        "With the Steel Watch gone, the Flaming Fist is the only law and is stretched thin. "
        "The Harpers fight tyranny and watch the contested dukedom closely.\n",
        encoding="utf-8",
    )
    # An unrelated filler page that also matches the query weakly (so it COULD backfill if the
    # curated page were wrongly dropped). It is NOT superseded.
    (lore / "flymm-s-cobblers.md").write_text(
        "# Flymm's Cobblers\nA humble Zhentarim-adjacent shoe shop in the Lower City. "
        + "It sells fine boots and resoles old ones. " * 20,
        encoding="utf-8",
    )
    subs = ["with the steel watch gone"]  # supersedes an INCIDENTAL sentence on factions.md
    hdr = "CURRENT WORLD (authoritative): tenor=grim — gortash=archduke. Treat as canon."
    hits = lorebook.lookup_lore("tw", "Zhentarim", 5, supersedes=subs, canon_header=hdr)
    bodies = {h["source"]: h["excerpt"].lower() for h in hits if h["source"] != "world-state"}
    # the curated faction page SURVIVES (not dropped) with its queried + other valid content...
    assert "factions.md" in bodies, "curated multi-fact page must not be dropped whole"
    assert "zhentarim" in bodies["factions.md"] and "harpers fight tyranny" in bodies["factions.md"]
    # ...the incidental superseded sentence is the ONLY thing elided (no leak)
    assert "with the steel watch gone" not in bodies["factions.md"]
    assert "[…superseded…]" in bodies["factions.md"]
    # and the curated page still LEADS the shoe shop (no unrelated backfill into the top slot)
    page_sources = [h["source"] for h in hits if h["source"] != "world-state"]
    if "flymm-s-cobblers.md" in page_sources:
        assert page_sources.index("factions.md") < page_sources.index("flymm-s-cobblers.md"), \
            "the shoe-shop filler must not out-rank the curated faction canon"


def test_lookup_lore_no_undersuppression_when_contradiction_outside_window(tmp_path, monkeypatch):
    # UNDER-SUPPRESSION repro: the contradicting sentence sits OUTSIDE the 600-char excerpt
    # window (far from the query hit). The old logic ran _contradicts on the excerpt only, so
    # the page escaped flagging AND the excerpt could later surface the claim. Redacting the
    # FULL page before excerpting guarantees the superseded sentence can NEVER appear in any
    # returned excerpt, wherever it centers, and the page is demoted.
    lore = _world(tmp_path, monkeypatch)
    # Query term "Gortash" appears LATE; the contradicting sentence is at the very top, >600
    # chars away — so a hit-centered excerpt would have hidden the top, masking the contradiction
    # from the old excerpt-only check, yet a head-fallback excerpt would have leaked it.
    page = (
        "# History\n"
        "Enver Gortash is dead, slain in the harbor battle. "        # contradiction (top)
        + "The river Chionthar runs to the sea past the old docks. " * 30  # >600 chars of filler
        + "Long after, a statue of Gortash was raised in the square."  # query hit (far end)
    )
    (lore / "history.md").write_text(page, encoding="utf-8")
    subs = ["gortash is dead"]
    hits = lorebook.lookup_lore("tw", "statue Gortash square", 5, supersedes=subs)
    body = next(h["excerpt"].lower() for h in hits if h["source"] == "history.md")
    # whichever way the excerpt centers, the superseded sentence is gone (the under-supp leak)
    assert "gortash is dead" not in body, "contradiction outside the excerpt window still leaked"
    # the page is KEPT and the queried content survives (the statue line)
    assert "statue of gortash" in body


def test_redact_superseded_helper_sentence_granularity():
    # Unit-level: the redactor drops only the matching SENTENCE(s), keeps the rest, and
    # reports (redacted, gutted). (subs lowercased + non-empty by contract.)
    from lorebook import _redact_superseded
    text = "Alpha stands tall. Gortash is dead now. Beta endures the winter."
    out, did, gut = _redact_superseded(text, ["gortash is dead"])
    assert did is True and gut is False  # clean sentences survived -> not gutted
    assert "alpha stands tall" in out.lower() and "beta endures" in out.lower()
    assert "gortash is dead" not in out.lower() and "[…superseded…]" in out
    # no match -> unchanged text, did=False, gutted=False
    out2, did2, gut2 = _redact_superseded(text, ["never appears here"])
    assert did2 is False and gut2 is False and "gortash is dead" in out2.lower()
    # consecutive superseded sentences collapse to a single elision (no run of markers)
    text3 = "Keep me. Gortash is dead. The seat is empty. Keep me too."
    out3, did3, gut3 = _redact_superseded(text3, ["gortash is dead", "the seat is empty"])
    assert did3 is True and gut3 is False and out3.count("[…superseded…]") == 1
    assert "keep me" in out3.lower() and "keep me too" in out3.lower()
    # EVERY sentence superseded -> gutted=True (the only-superseded page; demoted by caller)
    out4, did4, gut4 = _redact_superseded("Gortash is dead. The seat is empty.",
                                          ["gortash is dead", "the seat is empty"])
    assert did4 is True and gut4 is True and out4 == "[…superseded…]"
