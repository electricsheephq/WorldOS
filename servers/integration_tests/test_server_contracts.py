"""Cross-service contract tests for the WorldOS MCP boundaries.

Deterministic and gateway-free: no live LLM, NULL TTS backend, OFFLINE rules
(no network). These prove the SHAPES the DM / orchestrator relies on when it
calls across the engine / rules / voice services — so a schema drift on any one
boundary fails here instead of silently corrupting a live game.

Run (single-process, under the engine venv)::

    uv run --directory servers/engine python -m pytest servers/integration_tests -q -p no:xdist

The rules calls go through ONE long-lived subprocess worker in the rules venv
(see conftest); voice is imported in-process on the NULL backend.
"""

from __future__ import annotations

import pytest

# Expected reply schemas (the contract the DM depends on).
_SPELL_KEYS = {"name", "level", "school", "_source"}
_MONSTER_KEYS = {"name", "ac", "hp", "abilities", "_source"}
_CONDITION_KEYS = {"name", "_source"}
_SPEAK_KEYS = {"ok", "voice_id", "backend", "backend_voice", "audio_path", "played", "detail"}
_TRANSCRIBE_KEYS = {"text", "backend"}


# ===========================================================================
# (a) engine -> rules: exact-match lookups + schema on the bundled SRD.
# ===========================================================================
class TestRulesExactMatchAndSchema:
    def test_rules_worker_offline_dataset_loaded(self, rules_client):
        # Proves the worker booted in the rules venv with the full bundled SRD
        # merged in (not just the starter slice) and the network disabled.
        sizes = rules_client.sizes()
        assert sizes["spells"] > 100
        assert sizes["monsters"] > 100
        assert sizes["conditions"] >= 14

    def test_spell_exact_match_fireball(self, rules_client):
        r = rules_client.find_spell("Fireball")
        assert r is not None
        assert r["name"] == "Fireball"
        assert r["level"] == 3
        assert r["school"] == "Evocation"
        assert r["_source"] == "srd-bundled"  # came from bundled data, NOT the API

    def test_spell_exact_match_schema(self, rules_client):
        r = rules_client.find_spell("Fire Bolt")
        assert r is not None
        # Contract: every spell carries at least these keys.
        assert _SPELL_KEYS <= set(r.keys())
        assert r["level"] == 0
        assert r["school"] == "Evocation"

    def test_monster_exact_match_goblin(self, rules_client):
        r = rules_client.find_monster("goblin")
        assert r is not None
        assert r["name"] == "Goblin"
        assert r["ac"] == 15 and r["hp"] == 7
        assert r["abilities"]["dex"] == 14
        assert r["_source"] == "srd-bundled"

    def test_monster_exact_match_schema(self, rules_client):
        r = rules_client.find_monster("Tarrasque")
        assert r is not None
        assert _MONSTER_KEYS <= set(r.keys())
        assert r["cr"] == "30"
        assert r["abilities"]["str"] == 30
        assert r["actions"]  # actions joined from the fixture children

    def test_condition_exact_match_prone(self, rules_client):
        r = rules_client.find_condition("prone")
        assert r is not None
        assert r["name"] == "Prone"
        assert _CONDITION_KEYS <= set(r.keys())
        assert r["_source"] == "srd-bundled"

    def test_rule_lookup_advantage(self, rules_client):
        r = rules_client.find_rule("advantage")
        assert r is not None
        assert "Advantage" in r["name"]
        assert r["_source"] == "srd-bundled"

    def test_item_lookup_schema(self, rules_client):
        longsword = rules_client.find_item("Longsword")
        assert longsword is not None and longsword["damage"] == "1d8"
        bag = rules_client.find_item("Bag of Holding")
        assert bag is not None and bag["rarity"] == "uncommon"

    def test_lookup_spell_tool_wrap_found(self, rules_client):
        # The MCP tool wraps results as {"found": True/False, ...} — the shape
        # the orchestrator branches on.
        wrapped = rules_client.lookup_spell("Fireball")
        assert wrapped["found"] is True
        assert wrapped["name"] == "Fireball"

    def test_lookup_spell_tool_wrap_miss(self, rules_client):
        wrapped = rules_client.lookup_spell("definitely-not-a-spell-zzz")
        assert wrapped["found"] is False
        assert wrapped["query"] == "definitely-not-a-spell-zzz"
        assert "name" not in wrapped  # miss carries only found + query

    def test_unknown_returns_none_offline(self, rules_client):
        # Genuinely absent + offline -> None (no silent API fallthrough).
        assert rules_client.find_spell("not-a-spell-zzz") is None
        assert rules_client.find_monster("definitely-not-a-real-monster-xyz") is None


# ===========================================================================
# (b) rules fuzzy recovery: typo'd / mangled queries recover the right entry.
# ===========================================================================
class TestRulesFuzzyRecovery:
    def test_condition_typo_recovers(self, rules_client):
        r = rules_client.find_condition("prnoe")  # transposed
        assert r is not None and r["name"] == "Prone"

    def test_spell_spacing_recovers(self, rules_client):
        r = rules_client.find_spell("magicmissile")  # missing space
        assert r is not None and r["name"] == "Magic Missile"

    def test_spell_truncation_recovers_fullset(self, rules_client):
        # A full-set entry (not in the starter slice) recovered from a typo.
        r = rules_client.find_spell("firebal")
        assert r is not None and r["name"] == "Fireball"

    def test_fuzzy_recovers_exact_level_and_school(self, rules_client):
        # Fuzzy match still returns the canonical record, not a near-miss.
        r = rules_client.find_spell("firebal")
        assert r["level"] == 3 and r["school"] == "Evocation"

    def test_search_substring_across_layers(self, rules_client):
        names = {h["name"] for h in rules_client.search("fire")}
        assert "Fire Bolt" in names      # starter slice
        assert "Fireball" in names       # full set

    def test_search_category_filter(self, rules_client):
        hits = rules_client.search("go", category="monster")
        assert any(h["name"] == "Goblin" for h in hits)
        assert all(h["category"] == "monster" for h in hits)


# ===========================================================================
# (c) engine -> voice: speak() on the NULL backend returns the schema and
#     degrades gracefully when the backend is unavailable (never raises).
# ===========================================================================
class TestVoiceContract:
    def test_speak_null_backend_schema(self, voice_server):
        out = voice_server.speak("Hello, adventurer.", voice_id="narrator-dm", play=False)
        assert set(out.keys()) == _SPEAK_KEYS
        assert out["ok"] is True
        assert out["backend"] == "null"
        assert out["voice_id"] == "narrator-dm"
        assert out["audio_path"] is None
        assert out["played"] is False

    def test_speak_resolves_logical_voice_id(self, voice_server):
        # The logical voice_id is echoed back unchanged; the backend voice is
        # resolved separately (registry), so character data never depends on the
        # active backend.
        out = voice_server.speak("A line.", voice_id="companion-default", play=False)
        assert out["voice_id"] == "companion-default"
        assert isinstance(out["backend_voice"], str)

    def test_speak_default_voice_id(self, voice_server):
        out = voice_server.speak("Narration.", play=False)
        assert out["voice_id"] == "narrator-dm"  # documented default
        assert out["ok"] is True

    def test_list_voices_null_backend(self, voice_server):
        voices = voice_server.list_voices()
        assert isinstance(voices, list) and voices
        assert all("id" in v and "name" in v for v in voices)

    def test_transcribe_null_backend_schema(self, voice_server):
        out = voice_server.transcribe("/nonexistent/audio.wav")
        assert set(out.keys()) == _TRANSCRIBE_KEYS
        assert isinstance(out["text"], str)
        assert out["backend"]  # a backend name is always reported

    def test_speak_degrades_when_backend_raises(self, voice_server, monkeypatch):
        # A TTS backend that blows up (missing deps / model-load / playback) must
        # DEGRADE to the silent null backend and STILL return cleanly — never
        # raise out of speak() and break the story loop (#55). Voice is an
        # adapter: the engine rolls, the DM is told; a dead speaker is text-only.
        class Boom:
            name = "kokoro"

            def speak(self, *a, **k):
                raise ImportError("kokoro/torch unavailable")

        monkeypatch.setenv("WORLDOS_TTS_BACKEND", "kokoro")
        monkeypatch.setenv("WORLDOS_TTS_BACKEND", "kokoro")
        monkeypatch.setitem(voice_server._backends, "kokoro", Boom())
        out = voice_server.speak("The dragon roars.", voice_id="narrator-dm", play=False)
        assert out["ok"] is True            # never raised
        assert out["backend"] == "null"     # fell back to silent
        assert out["audio_path"] is None
        assert "fail" in out["detail"].lower()
        assert set(out.keys()) == _SPEAK_KEYS  # same schema as the happy path

    def test_speak_never_raises_on_unknown_voice_id(self, voice_server):
        # An unknown logical voice_id still resolves (registry falls back) and
        # returns the contract schema rather than raising.
        out = voice_server.speak("Mystery speaker.", voice_id="totally-unknown-xyz", play=False)
        assert out["ok"] is True
        assert set(out.keys()) == _SPEAK_KEYS
        assert out["voice_id"] == "totally-unknown-xyz"


# ===========================================================================
# Cross-boundary integration: a single fictional beat touches both services
# the same way the DM would — rules lookup feeds the voiced narration. Proves
# the two boundaries compose without sharing state (gateway-free, no mutation).
# ===========================================================================
class TestCrossBoundaryComposition:
    def test_rules_lookup_then_voiced_narration(self, rules_client, voice_server):
        spell = rules_client.find_spell("Fireball")
        assert spell["name"] == "Fireball" and spell["level"] == 3
        line = f"The mage hurls a level-{spell['level']} {spell['name']}!"
        out = voice_server.speak(line, voice_id="narrator-dm", play=False)
        assert out["ok"] is True and out["backend"] == "null"
        assert set(out.keys()) == _SPEAK_KEYS

    def test_boundaries_are_independent(self, rules_client, voice_server):
        # Reading rules does not change anything the voice boundary returns, and
        # vice versa — neither service is the state writer (engine is).
        before = voice_server.speak("Before.", play=False)
        rules_client.find_monster("goblin")
        after = voice_server.speak("After.", play=False)
        assert before["backend"] == after["backend"] == "null"
        assert before["voice_id"] == after["voice_id"]
