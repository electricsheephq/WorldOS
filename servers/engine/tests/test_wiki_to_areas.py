"""Guards the wiki→areas parser (tools/ingest/wiki_to_areas.to_area_record).

Like test_wiki_to_characters.py, the ingestion CLI lives outside the engine venv; this
test imports it via a path insert so CI (which runs pytest in servers/engine) still
guards the wikitext→navigable-Location-JSON conversion: infobox parsing (parent region +
adjacency keys), section→description mapping, connection-name extraction from the body,
ref/template/wikilink stripping, tag (category) collection, and per-source license/
attribution stamping. NO network — a wikitext FIXTURE is parsed in-process."""

import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[3] / "tools" / "ingest"
sys.path.insert(0, str(_INGEST))

import wiki_to_areas  # noqa: E402


# A realistic area page in the bg3.wiki / FR-settlement infobox + sections style.
_FIXTURE = (
    "{{Location infobox\n"
    "| name = Testhaven\n"
    "| region = [[The Sword Coast|Sword Coast]]\n"
    "| connects = [[Bloomridge Market]], [[Wyrm's Crossing]] and [[Candlekeep]]\n"
    "| population = 4000<ref>census</ref>\n"
    "}}\n"
    "'''Testhaven''' is a walled river-town on the [[Sword Coast]], a day's ride from "
    "[[Baldur's Gate]].<ref name=a>a citation</ref> It is old.\n\n"
    "== Description ==\n"
    "A {{nowrap|terraced}} settlement of slate roofs and narrow stairs, Testhaven "
    "climbs the [[bluff]] above the harbor.<ref>cite</ref> Its '''market''' never sleeps.\n\n"
    "== Geography ==\n"
    "The town borders the [[Risen Road]] to the east and the [[Chionthar|river]] to the "
    "south.\n\n"
    "== References ==\n"
    "<references/>\n"
    "[[Category:Locations on the Sword Coast]]\n"
    "[[Category:Settlements]]\n"
)

_LICENSE = "CC BY-SA 4.0 / CC BY-NC-SA 4.0 (dual)"
_ATTR = "Text from bg3.wiki, dual-licensed CC BY-SA 4.0 / CC BY-NC-SA 4.0."


def _rec():
    return wiki_to_areas.to_area_record(
        "Testhaven", _FIXTURE, "https://bg3.wiki/wiki/Testhaven", _LICENSE, _ATTR,
    )


def test_location_shaped_fields_present():
    r = _rec()
    required = {
        "name", "description", "region", "connections", "tags",
        "source_url", "license", "attribution",
    }
    assert required <= set(r)
    assert r["name"] == "Testhaven"
    assert isinstance(r["connections"], list) and isinstance(r["tags"], list)


def test_region_parsed_from_infobox():
    r = _rec()
    assert r["region"] == "Sword Coast"   # [[The Sword Coast|Sword Coast]] → display text


def test_description_from_sections_cleaned():
    r = _rec()
    d = r["description"]
    assert "terraced" in d                 # {{nowrap|terraced}} → text kept
    assert "climbs the bluff" in d         # [[bluff]] → display, prose preserved
    assert "market" in d.lower()


def test_connections_extracted_as_names():
    r = _rec()
    conns = r["connections"]
    # infobox `connects` (comma / "and"-split) → individual place names
    assert "Bloomridge Market" in conns
    assert "Wyrm's Crossing" in conns
    assert "Candlekeep" in conns
    # body geography-section links also surface as connection hints
    assert "Risen Road" in conns
    # connections are NAMES (resolved to ids at seed time), carry no markup
    assert all("[[" not in c and "{{" not in c for c in conns)
    # a page never lists itself as a connection
    assert "Testhaven" not in conns


def test_wikitext_is_stripped():
    r = _rec()
    blob = " ".join([r["description"], r["region"], r["name"], *r["connections"], *r["tags"]])
    assert "{{" not in blob and "}}" not in blob          # templates stripped
    assert "[[" not in blob and "]]" not in blob          # wikilinks rendered, not raw
    assert "<ref" not in blob and "citation" not in blob  # refs stripped
    assert "'''" not in blob and "<references" not in blob
    assert "census" not in blob                            # ref body gone


def test_tags_from_categories():
    r = _rec()
    assert "Locations on the Sword Coast" in r["tags"]
    assert "Settlements" in r["tags"]


def test_license_and_attribution_stamped():
    r = _rec()
    assert r["source_url"] == "https://bg3.wiki/wiki/Testhaven"
    assert r["license"] == _LICENSE
    assert r["attribution"] == _ATTR


def test_slug_is_filesystem_safe():
    assert wiki_to_areas._slug("Wyrm's Crossing") == "wyrm-s-crossing"
    assert wiki_to_areas._slug("Baldur's Gate/Rivington") == "baldur-s-gate-rivington"


def test_no_infobox_falls_back_to_lead():
    # A page with no infobox still yields a record — the lead paragraph becomes the
    # description and its links become connection hints; region is empty.
    r = wiki_to_areas.to_area_record(
        "Lonely Tor",
        "A windswept [[hill]] overlooking [[Elturel]] and the [[Risen Road]].",
        "u", "L", "A",
    )
    assert r["region"] == ""
    assert "windswept hill" in r["description"]
    assert "Elturel" in r["connections"] and "Risen Road" in r["connections"]


def test_connection_keys_alias_and_self_link_dropped():
    # FR settlement infoboxes use varied adjacency keys (here "borders"); the page's own
    # name must never appear as a connection even if the body self-links.
    fx = (
        "{{Settlement\n| name = Edgeford\n| borders = [[Greenfields]]; [[Edgeford]]\n}}\n"
        "Edgeford sits where two roads meet near [[Greenfields]].\n"
    )
    r = wiki_to_areas.to_area_record("Edgeford", fx, "u", "L", "A")
    assert "Greenfields" in r["connections"]
    assert "Edgeford" not in r["connections"]   # self-link filtered
