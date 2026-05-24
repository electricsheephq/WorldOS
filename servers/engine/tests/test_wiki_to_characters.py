"""Guards the wiki→characters parser (tools/ingest/wiki_to_characters.parse_character).

Like test_wiki_ingest.py, the ingestion CLI lives outside the engine venv; this test
imports it via a path insert so CI (which runs pytest in servers/engine) still guards
the wikitext→NPC-JSON conversion: infobox parsing (race/class/level/alignment), section→
field mapping, equipment/relationship lists, ref/template/wikilink stripping, voice_hint
derivation, and per-source license/attribution stamping (bg3.wiki vs Fandom)."""

import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[3] / "tools" / "ingest"
sys.path.insert(0, str(_INGEST))

import wiki_to_characters  # noqa: E402


# A small inline character page in the bg3.wiki/Fandom infobox + sections style.
_FIXTURE = (
    "{{Character infobox\n"
    "| name = Testarion\n"
    "| race = [[High Elf|High elf]]\n"
    "| class = [[Rogue]]\n"
    "| level = 1\n"
    "| alignment = Chaotic Neutral\n"
    "| voice actor = Jane Doe<ref>credits</ref>\n"
    "}}\n"
    "'''Testarion''' is a [[High Elf|high-elf]] [[Rogue|rogue]] companion on the "
    "[[Sword Coast]].<ref name=a>a citation</ref> He is vain and sardonic.\n\n"
    "== Appearance ==\n"
    "Pale, with crimson eyes and an immaculate coat.<ref>cite</ref>\n\n"
    "== Personality ==\n"
    "Razor-witted and '''vain''', he masks two centuries of cruelty with charm.\n\n"
    "== Biography ==\n"
    "Spawn to the vampire [[Cazador]] for {{nowrap|200 years}}, now freed.\n\n"
    "== Equipment ==\n"
    "* [[Dagger]]\n"
    "* Fine [[clothing]]\n\n"
    "== Relationships ==\n"
    "* [[Cazador]] — his former master\n"
    "* The [[player character|Tav]] — uneasy ally\n\n"
    "== References ==\n"
    "<references/>\n"
    "[[Category:Companions]]\n"
)

_LICENSE = "CC BY-SA 4.0 / CC BY-NC-SA 4.0 (dual)"
_ATTR = "Text from bg3.wiki, dual-licensed CC BY-SA 4.0 / CC BY-NC-SA 4.0."


def _rec():
    return wiki_to_characters.parse_character(
        "Testarion", _FIXTURE, "https://bg3.wiki/wiki/Testarion", _LICENSE, _ATTR,
    )


def test_infobox_fields_parsed():
    r = _rec()
    assert r["name"] == "Testarion"
    assert r["race"] == "High elf"          # [[High Elf|High elf]] → display text
    assert r["class"] == "Rogue"
    assert r["level"] == "1"
    assert r["alignment"] == "Chaotic Neutral"


def test_sections_mapped_to_fields():
    r = _rec()
    assert "crimson eyes" in r["appearance"]
    assert "sardonic" in r["personality"] or "vain" in r["personality"]
    assert "Cazador" in r["backstory"] and "freed" in r["backstory"]


def test_wikitext_is_stripped():
    r = _rec()
    blob = " ".join([r["appearance"], r["personality"], r["backstory"], r["name"]])
    assert "{{" not in blob and "}}" not in blob          # templates stripped ({{nowrap}})
    assert "[[" not in blob and "]]" not in blob          # wikilinks rendered, not raw
    assert "<ref" not in blob and "citation" not in blob  # refs stripped
    assert "'''" not in blob and "<references" not in blob
    assert "200 years" in r["backstory"]                   # {{nowrap|200 years}} → text kept


def test_equipment_and_relationships_are_lists():
    r = _rec()
    assert isinstance(r["equipment"], list)
    assert "Dagger" in r["equipment"]
    assert any("clothing" in e.lower() for e in r["equipment"])
    assert isinstance(r["relationships"], list)
    assert any("Cazador" in rel for rel in r["relationships"])
    assert any("Tav" in rel for rel in r["relationships"])
    # List items carry no leading bullet markers or raw links.
    assert all(not e.startswith(("*", "-")) and "[[" not in e for e in r["equipment"])


def test_voice_hint_derived():
    r = _rec()
    vh = r["voice_hint"].lower()
    assert "elf" in vh and "rogue" in vh                   # race/class register
    assert "voiced by jane doe" in vh                       # infobox voice actor noted


def test_license_and_attribution_stamped_per_source():
    r = _rec()
    assert r["source_url"] == "https://bg3.wiki/wiki/Testarion"
    assert r["license"] == _LICENSE
    assert r["attribution"] == _ATTR


def test_all_required_fields_present():
    r = _rec()
    required = {
        "name", "race", "class", "level", "alignment", "appearance", "personality",
        "mannerisms", "backstory", "equipment", "relationships", "voice_hint",
        "source_url", "license", "attribution",
    }
    assert required <= set(r)
    # Stubs that cleaned to nothing are caught by main()'s skip; a real profile has prose.
    assert r["backstory"] and r["personality"]


def test_slug_is_filesystem_safe():
    assert wiki_to_characters._slug("Lae'zel") == "lae-zel"
    assert wiki_to_characters._slug("Minsc and Boo") == "minsc-and-boo"


def test_missing_infobox_does_not_crash():
    # A page with no infobox and only a lead paragraph still yields a record (lead→backstory).
    r = wiki_to_characters.parse_character(
        "Nobody", "Just a [[wanderer]] with no infobox.", "u", "L", "A",
    )
    assert r["race"] == "" and r["class"] == ""
    assert "wanderer" in r["backstory"]


# ── regression fixtures for issues found in the live dry-run ──────────────────────────

# bg3.wiki wraps proper-noun links in templates ({{CharLink}}, {{Quest}}, {{deity}}).
# These MUST be unwrapped to their display text, or names vanish from the prose
# (the live dry-run produced "Born to parents  and ," before this was fixed).
_LINKWRAP_FIXTURE = (
    "{{Infobox creature\n| name = Linky\n| race = [[Half-elf]]\n| class = [[Cleric]]\n}}\n"
    "== Biography ==\n"
    "Born to {{CharLink|Arnell Hallowleaf|Arnell}} and {{CharLink|Emmeline Hallowleaf}}, "
    "she served {{deity|Shar}}, the dark sister of {{deity|Selûne|Selûne's}} faith, until "
    "the events of {{Quest|The Chosen of Shar}}.\n"
)


def test_link_wrapper_templates_unwrap_to_display_text():
    r = wiki_to_characters.parse_character("Linky", _LINKWRAP_FIXTURE, "u", "L", "A")
    b = r["backstory"]
    assert "Arnell" in b                       # {{CharLink|Page|Arnell}} → "Arnell"
    assert "Emmeline Hallowleaf" in b          # {{CharLink|Page}} → "Page" (only arg)
    assert "Shar" in b and "Selûne" in b       # {{deity|…}} wrappers unwrapped
    assert "The Chosen of Shar" in b           # {{Quest|…}} unwrapped
    assert "{{" not in b and "}}" not in b
    # the failure mode this guards: no empty " and ," gaps where a link was deleted
    assert "  " not in b.replace("\n", " ")


# Walkthrough headings like "As an origin character" must NOT bleed into personality
# (heading match is by equality, not the substring "character").
_BLEED_FIXTURE = (
    "{{Infobox creature\n| name = Bleeder\n}}\n"
    "== Personality ==\n"
    "Guarded and sharp.\n\n"
    "== As an origin character ==\n"
    "She wakes up in the pod where all player characters start. Pick up the artifact.\n"
)


def test_walkthrough_heading_does_not_bleed_into_personality():
    r = wiki_to_characters.parse_character("Bleeder", _BLEED_FIXTURE, "u", "L", "A")
    assert "Guarded and sharp" in r["personality"]
    assert "wakes up in the pod" not in r["personality"]   # gameplay section excluded
    assert "artifact" not in r["personality"]


# An FR-style prose "Relationships" section (no bullets) becomes sentence list entries.
_PROSE_REL_FIXTURE = (
    "{{Person\n| name = Reler\n| race5e = [[Human]]\n| alignment5e = [[Chaotic good]]\n}}\n"
    "== Relationships ==\n"
    "His most trusted companion was Boo. He was devoted to the witch Dynaheir.\n"
)


def test_prose_relationships_become_list_and_edition_keys_resolve():
    r = wiki_to_characters.parse_character("Reler", _PROSE_REL_FIXTURE, "u", "L", "A")
    assert r["race"] == "Human"                 # race5e edition-suffixed key resolved
    assert r["alignment"] == "Chaotic good"     # alignment5e resolved
    assert isinstance(r["relationships"], list) and len(r["relationships"]) >= 2
    assert any("Boo" in x for x in r["relationships"])
    assert any("Dynaheir" in x for x in r["relationships"])


def test_internal_asset_ids_not_kept_as_equipment():
    # bg3.wiki infoboxes carry equipment = EQ_<Name> (internal ID); a stray bulleted ID
    # or markup fragment must be rejected, not surfaced as gear.
    fx = (
        "{{Infobox creature\n| name = Gearless\n| equipment = EQ_Gearless\n}}\n"
        "== Equipment ==\n* EQ_Gearless\n* }}\n* x2\n* A real [[Dagger]]\n"
    )
    r = wiki_to_characters.parse_character("Gearless", fx, "u", "L", "A")
    assert any("Dagger" in e for e in r["equipment"])     # the one real item is kept
    assert "EQ_Gearless" not in r["equipment"]            # internal asset ID rejected
    assert "}}" not in r["equipment"] and "x2" not in r["equipment"]  # markup/short junk
