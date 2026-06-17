"""World lore retrieval (lorebook): authored-canon precedence, era, FTS safety.

Guards the adversarial-review fixes: ingested wiki pages must NOT out-rank the seed's
authored (post-canon) pages, and a page's chronology must surface per hit."""

import lorebook


def _world(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_CONTENT_DIR", str(tmp_path))
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


# --- F10-1: natural-query reachability (possessive token-drop + tier-0 noise floor) ----
# The dedicated lore page must be reachable for (a) possessive queries — the 1-char "s" token
# of "Wyrm's Crossing" used to anchor unrelated pages — and (b) stopword-heavy queries — a few
# authored pages matched ONLY a stopword ("the") at noise rank yet took tier-0's absolute
# precedence and buried the genuinely-matching wiki page. The fix drops <2-char tokens and adds
# a NOISE FLOOR to the tier-0-first guarantee (an authored page leads only if it genuinely
# matches), WITHOUT regressing the authored-canon precedence for genuine matches.

def test_safe_match_drops_one_char_tokens_but_never_empties(tmp_path, monkeypatch):
    # Possessive: "Wyrm's Crossing" -> ["Wyrm","s","Crossing"]; the 1-char "s" matched 's'
    # everywhere and dragged unrelated pages up. Drop sub-2-char tokens.
    assert lorebook._safe_match("Wyrm's Crossing") == '"Wyrm" OR "Crossing"'
    assert lorebook._safe_match("the Counting House") == '"the" OR "Counting" OR "House"'
    # ...unless dropping would EMPTY the match (a query of only short tokens) — then keep them,
    # so a degenerate query still searches rather than silently returning nothing.
    assert lorebook._safe_match("a I") == '"a" OR "I"'
    assert lorebook._safe_match("x") == '"x"'
    assert lorebook._safe_match("") == ""  # no tokens at all -> empty (unchanged)


def test_possessive_query_reaches_dedicated_page(tmp_path, monkeypatch):
    # A possessive natural query must reach the page whose slug IS that place — the 1-char "s"
    # token previously dragged unrelated 's'-bearing pages up and buried it. Enough decoy pages
    # contain a bare "s" word that, with the "s" token live, they crowd the dedicated page out of
    # the top-`limit`; dropping the 1-char token surfaces it.
    lore = _world(tmp_path, monkeypatch)
    (lore / "wiki" / "wyrm-s-crossing.md").write_text(
        "# Wyrm's Crossing\nWyrm's Crossing is the great bridge district spanning the river.\n",
        encoding="utf-8",
    )
    # Decoys: tier-0 authored pages that match ONLY the bare 1-char "s" token (no Wyrm/Crossing).
    # With the "s" token live they out-rank the dedicated wiki page (tier-0 precedence) and fill
    # the small cap; with it dropped they no longer match at all.
    for i in range(4):
        (lore / f"decoy{i}.md").write_text(
            f"# Decoy {i}\nThe letter s s s appears s often here, s s, but nothing else.\n",
            encoding="utf-8",
        )
    hits = lorebook.lookup_lore("tw", "Wyrm's Crossing", 3)
    assert any("wyrm-s-crossing" in h["source"] for h in hits), \
        "possessive query must reach the dedicated wiki page (1-char 's' token must not bury it)"


def test_stopword_query_does_not_let_noise_authored_pages_bury_real_page(tmp_path, monkeypatch):
    # Stopword-heavy query: authored pages that match ONLY the stopword ("the") at noise rank
    # must NOT take tier-0 absolute precedence and bury the genuinely-matching wiki page. Enough
    # noise-rank authored decoys exist to fill the small cap under the old tier-0-first rule.
    lore = _world(tmp_path, monkeypatch)
    # Authored decoys that contain ONLY the stopword "the" (no Counting / House) — they match at
    # noise rank yet under the old rule took tier-0 absolute precedence and filled the cap.
    for i in range(4):
        (lore / f"legend{i}.md").write_text(
            f"# Legend {i}\nThe heroes and the deeds and the days of the realm number {i}.\n",
            encoding="utf-8",
        )
    # The genuinely-matching page (the actual subject) lives in the wiki tier.
    (lore / "wiki" / "counting-house.md").write_text(
        "# The Counting House\nThe Counting House is the great bank of the Lower City, "
        "where the Counting House clerks weigh every coin in the House vaults.\n",
        encoding="utf-8",
    )
    hits = lorebook.lookup_lore("tw", "the Counting House", 3)
    assert any("counting-house" in h["source"] for h in hits), \
        "a noise-rank stopword match in tier-0 must not bury the genuinely-matching wiki page"


def test_authored_canon_still_wins_a_GENUINE_tie(tmp_path, monkeypatch):
    # The noise floor only demotes pages that match at NOISE rank. A tier-0 page that GENUINELY
    # matches the query must STILL out-rank a tier-1 page (the post-canon de-confliction guard).
    lore = _world(tmp_path, monkeypatch)
    (lore / "gortash.md").write_text(
        "# Gortash\n*Era: 1492 DR*\nEnver Gortash is dead, slain in the Battle of Baldur's Gate.\n",
        encoding="utf-8",
    )
    (lore / "wiki" / "gortash.md").write_text(
        "# Gortash\n" + "Enver Gortash is the living Archduke of Baldur's Gate. " * 60,
        encoding="utf-8",
    )
    hits = lorebook.lookup_lore("tw", "Gortash Archduke", 3)
    assert hits and hits[0]["title"] == "Gortash" and "dead" in hits[0]["excerpt"].lower(), \
        "a genuinely-matching authored page must still beat the stale wiki page"


def test_possessive_and_stopword_reach_real_corpus_dedicated_pages():
    # The shipped corpus repros from the audit (F10-1): both query classes must reach the
    # dedicated page. (Guards against a regression on the real 356-page baldurs-gate corpus.)
    possessive = lorebook.lookup_lore("baldurs-gate", "Wyrm's Crossing", 5)
    assert any("wyrm-s-crossing" in h["source"] for h in possessive), \
        "real-corpus possessive query must reach wyrm-s-crossing.md"
    stopword = lorebook.lookup_lore("baldurs-gate", "the Counting House", 5)
    assert any("counting-house" in h["source"] for h in stopword), \
        "real-corpus stopword query must reach counting-house-baldur-s-gate.md"


def test_clean_query_output_byte_identical_to_pre_fix_ordering():
    # ADDITIVE guarantee: when no 1-char/stopword-noise is involved, every authored match clears
    # the noise floor (weak0 is empty), so ordering reduces to today's tier-0-then-tier-1 — the
    # output is byte-identical to before the fix. Pinned for a battery of clean queries.
    pinned = {
        "Gortash Archduke": ["the-absolute-and-the-dead-three.md", "baldurs-gate.md",
                             "factions.md", "the-legends.md", "council-of-four.md"],
        "Flaming Fist": ["factions.md", "baldurs-gate.md", "flaming-fist.md",
                         "ulder-ravengard.md", "wyrm-s-rock.md"],
        "Steel Watch": ["the-absolute-and-the-dead-three.md", "baldurs-gate.md",
                        "factions.md", "watch-citadel.md", "guthmere.md"],
    }
    for q, expected in pinned.items():
        got = [h["source"] for h in lorebook.lookup_lore("baldurs-gate", q, 5)]
        assert got == expected, f"clean query {q!r} must be byte-identical: {got!r} != {expected!r}"


def test_legends_page_covers_all_eleven_shipped_heroes():
    # S6 audit (content gap): the authored hero roster the-legends.md must name ALL 11 major
    # heroes so lookup_lore("Gale"/"Halsin") resolves to the authored bio page instead of
    # falling through to unrelated wiki pages (couriers, a mansion). Gale and Halsin were
    # missing (9/11); this guards the base-seed fix that benefits every ending's hero lookups.
    heroes = ["Jaheira", "Minsc", "Astarion", "Shadowheart", "Wyll", "Karlach",
              "Gale", "Lae'zel", "Halsin", "Emperor", "Withers"]
    for hero in ("Gale", "Halsin"):
        hits = lorebook.lookup_lore("baldurs-gate", hero, 5)
        assert any("the-legends" in h["source"] for h in hits), \
            f"lookup_lore({hero!r}) must surface the authored the-legends.md roster page"
    # the page itself names every hero (ending-neutral roster — per-ending fate is separate)
    pages = {p["source"]: p["text"].lower() for p in lorebook._pages("baldurs-gate")}
    legends = next(t for s, t in pages.items() if "the-legends" in s)
    for hero in heroes:
        key = hero.split()[-1].lower()  # "Lae'zel" / "Emperor" etc.
        assert key in legends, f"the-legends.md omits {hero!r}"
