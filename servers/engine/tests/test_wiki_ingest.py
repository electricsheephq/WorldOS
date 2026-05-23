"""Guards the wiki→lore converter (tools/ingest/wiki_to_lore.to_markdown).

The ingestion CLI is stdlib-only content tooling that lives outside the engine venv;
this test imports it via a path insert so CI (which runs pytest in servers/engine)
still guards the wikitext→markdown conversion — especially the regressions found in
the dry-run (a line-leading '''bold''' must not be eaten by the list-marker pass)."""

import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[3] / "tools" / "ingest"
sys.path.insert(0, str(_INGEST))

import wiki_to_lore  # noqa: E402


_FIXTURE = (
    "{{Infobox settlement\n| name = Testburg\n| ruler = [[Bob the Bold]]\n}}\n"
    "'''Testburg''', also called the **Test**, is a [[city|town]] on the "
    "[[Sword Coast]].<ref name=cite>a citation</ref> It is old.\n\n"
    "== History ==\n"
    "Founded by [[File:Map.png|thumb|a caption with a [[link]]]] settlers.\n"
    "* first bullet\n"
    "* second bullet\n\n"
    "== References ==\n"
    "<references/>\n"
    "[[Category:Cities]]\n"
)


def test_to_markdown_cleans_wikitext():
    md = wiki_to_lore.to_markdown("Testburg", _FIXTURE, "https://ex/wiki/Testburg")
    assert md.startswith("# Testburg")
    assert "**Testburg**" in md                       # bold lead preserved (the dry-run bug)
    assert "town on the [[" not in md                 # wikilinks rendered, not raw
    assert "town on the Sword Coast" in md            # [[city|town]]→town, [[Sword Coast]]→Sword Coast
    assert "{{" not in md and "}}" not in md           # infobox/template stripped
    assert "<ref" not in md and "citation" not in md   # refs stripped
    assert "File:" not in md and "Category:" not in md # file/category links dropped
    assert "## History" in md                          # == heading == → ## heading
    assert "- first bullet" in md                       # list markers normalized
    assert "References" not in md                       # trailing noise section dropped
    assert "CC-BY-SA" in md and "Testburg" in md        # attribution footer present


def test_to_markdown_truncates_long_pages():
    md = wiki_to_lore.to_markdown("Big", "word " * 6000, "u", max_chars=500)
    assert "truncated" in md and len(md) < 1400


def test_slug_is_filesystem_safe():
    assert wiki_to_lore._slug("Wyrm's Crossing") == "wyrm-s-crossing"
    assert wiki_to_lore._slug("Baldur's Gate/Rivington") == "baldur-s-gate-rivington"
