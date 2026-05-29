"""Image-generation seam tests — no MCP, no network.

They exercise the null provider (deterministic placeholder), env-var provider
selection with graceful degradation to null, the hosted-provider stubs' loud
NotImplementedError seam, and the content-hash cache write/read under a tmp
CLAWDND_STATE_DIR. Real OpenAI/Stability generation is intentionally out of scope
here — the whole point of the null default is that this all runs offline.
"""

import json

import pytest

import imagegen
from imagegen import (
    NullImageProvider,
    OpenAIImageProvider,
    StabilityImageProvider,
)


# --------------------------------------------------------------------------- #
# Null provider: deterministic placeholder, no network.
# --------------------------------------------------------------------------- #

def test_null_provider_returns_placeholder_descriptor():
    p = NullImageProvider()
    assert p.name == "null"
    d = p.generate("portrait", "a grizzled dwarf cleric", seed=7)
    assert d == {
        "provider": "null",
        "kind": "portrait",
        "prompt": "a grizzled dwarf cleric",
        "placeholder": True,
        "seed": 7,
    }


def test_null_provider_is_deterministic():
    p = NullImageProvider()
    a = p.generate("map", "the ashen barrow", seed=42)
    b = p.generate("map", "the ashen barrow", seed=42)
    assert a == b  # same input -> identical descriptor


def test_null_provider_satisfies_protocol():
    assert isinstance(NullImageProvider(), imagegen.ImageProvider)
    assert isinstance(OpenAIImageProvider(), imagegen.ImageProvider)
    assert isinstance(StabilityImageProvider(), imagegen.ImageProvider)


def test_null_provider_normalizes_unknown_kind():
    # An unknown kind degrades to "scene" rather than crashing.
    d = NullImageProvider().generate("banner", "a heraldic crest")
    assert d["kind"] == "scene"


@pytest.mark.parametrize("kind", list(imagegen.KINDS))
def test_known_kinds_pass_through(kind):
    assert NullImageProvider().generate(kind, "x")["kind"] == kind


# --------------------------------------------------------------------------- #
# Provider selection: default null + graceful degradation.
# --------------------------------------------------------------------------- #

def test_default_provider_is_null(monkeypatch):
    monkeypatch.delenv("CLAWDND_IMAGE_PROVIDER", raising=False)
    assert imagegen.provider_name() == "null"
    assert isinstance(imagegen.get_provider(), NullImageProvider)


def test_unknown_provider_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "no-such-provider")
    assert isinstance(imagegen.get_provider(), NullImageProvider)


def test_named_real_provider_unconfigured_falls_back_to_null(monkeypatch):
    # Named but no API key wired -> degrade to null (never crash the server).
    monkeypatch.delenv("CLAWDND_IMAGE_API_KEY", raising=False)
    for name in ("openai", "stability"):
        monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", name)
        assert isinstance(imagegen.get_provider(), NullImageProvider)


def test_selection_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "OpenAI")
    monkeypatch.setenv("CLAWDND_IMAGE_API_KEY", "sk-test")
    p = imagegen.get_provider()
    assert isinstance(p, OpenAIImageProvider) and p.name == "openai"


def test_configured_real_provider_is_selected(monkeypatch):
    # With a key present, the selector hands back the real provider (which then
    # raises on use — see below). This proves the configured() gate, not just the
    # fallback path.
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "stability")
    monkeypatch.setenv("CLAWDND_IMAGE_API_KEY", "key-123")
    assert isinstance(imagegen.get_provider(), StabilityImageProvider)


# --------------------------------------------------------------------------- #
# The seam: hosted providers raise a clear NotImplementedError when invoked.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cls", [OpenAIImageProvider, StabilityImageProvider])
def test_hosted_provider_generate_raises_clear_error(cls):
    with pytest.raises(NotImplementedError) as excinfo:
        cls().generate("scene", "a burning village at dusk")
    msg = str(excinfo.value)
    assert "not implemented" in msg.lower()
    # Mentions the env var the user must set to wire it — the actionable seam.
    assert "CLAWDND_IMAGE_API_KEY" in msg


def test_configured_provider_still_raises_until_wired(monkeypatch):
    # Even fully "configured", the stub is not implemented — invoking it is a loud,
    # intentional failure, not a silent no-op.
    monkeypatch.setenv("CLAWDND_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("CLAWDND_IMAGE_API_KEY", "sk-live")
    provider = imagegen.get_provider()
    with pytest.raises(NotImplementedError):
        provider.generate("map", "the underdark")


# --------------------------------------------------------------------------- #
# Content-hash cache: write/read by hash under a tmp CLAWDND_STATE_DIR.
# --------------------------------------------------------------------------- #

@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point the engine's state dir at a tmp dir so the cache writes there."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CLAWDND_IMAGE_PROVIDER", raising=False)  # null by default
    return tmp_path


def test_content_hash_is_stable_and_input_sensitive():
    h1 = imagegen.content_hash("map", "ruined keep", seed=1, provider="null")
    h2 = imagegen.content_hash("map", "ruined keep", seed=1, provider="null")
    assert h1 == h2 and len(h1) == 64  # sha256 hex
    # Any input change -> different key.
    assert h1 != imagegen.content_hash("map", "ruined keep", seed=2, provider="null")
    assert h1 != imagegen.content_hash("portrait", "ruined keep", seed=1, provider="null")
    assert h1 != imagegen.content_hash("map", "ruined keep", seed=1, provider="openai")


def test_cache_lives_under_state_dir_images(state):
    desc = NullImageProvider().generate("scene", "a misty crossroads", seed=3)
    path = imagegen.cache_write(desc, scope="embergloom-pact")
    # Derived cache root: <state>/images/<scope>/<hash>.json — NOT under campaigns/.
    assert path.parent == state / "images" / "embergloom-pact"
    assert path.name.endswith(".json")
    assert "campaigns" not in str(path)
    assert path.is_file()


def test_cache_write_then_read_by_hash(state):
    desc = NullImageProvider().generate("portrait", "an elven ranger", seed=9)
    imagegen.cache_write(desc, scope="w1")
    key = imagegen.content_hash("portrait", "an elven ranger", seed=9, provider="null")
    got = imagegen.cache_read(key, scope="w1")
    assert got is not None
    assert got["kind"] == "portrait" and got["prompt"] == "an elven ranger"
    assert got["hash"] == key  # self-describing entry


def test_cache_miss_returns_none(state):
    assert imagegen.cache_read("0" * 64, scope="w1") is None


def test_corrupt_cache_entry_is_treated_as_miss(state):
    key = imagegen.content_hash("map", "x", provider="null")
    path = imagegen.cache_path(key, scope="w1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert imagegen.cache_read(key, scope="w1") is None  # rebuildable -> never fatal


def test_generate_caches_and_then_hits(state):
    first = imagegen.generate("scene", "a thunderstorm over the moor", seed=5, scope="w2")
    assert first["placeholder"] is True and first["cache_hit"] is False
    # Same request -> served from cache.
    second = imagegen.generate("scene", "a thunderstorm over the moor", seed=5, scope="w2")
    assert second["cache_hit"] is True
    assert second["kind"] == first["kind"] and second["prompt"] == first["prompt"]
    # Exactly one descriptor on disk for this request.
    files = list((state / "images" / "w2").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["prompt"] == "a thunderstorm over the moor"


def test_scopes_partition_the_cache(state):
    imagegen.generate("map", "shared prompt", seed=1, scope="alpha")
    imagegen.generate("map", "shared prompt", seed=1, scope="beta")
    assert (state / "images" / "alpha").is_dir()
    assert (state / "images" / "beta").is_dir()
    # Same prompt, different scope -> separate entries (no cross-campaign bleed).
    assert imagegen.cache_read(
        imagegen.content_hash("map", "shared prompt", seed=1, provider="null"), scope="beta"
    ) is not None


def test_scope_is_sanitized_to_safe_segment(state):
    # A nasty scope id can't escape the images/ dir via path traversal.
    imagegen.generate("scene", "x", scope="../../etc/passwd")
    images_root = state / "images"
    written = list(images_root.rglob("*.json"))
    assert written, "expected a cached descriptor"
    for p in written:
        assert images_root in p.parents  # stayed inside the cache root


def test_generate_degrades_to_null_when_provider_raises(monkeypatch, tmp_path):
    """A hosted/gateway provider failure must NOT crash the caller — the skill promises
    generate_image is 'always safe, a cheap no-op'. It degrades to the null placeholder
    and is NOT cached, so a transient gateway blip stays retryable (review #1)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))

    class _Boom:
        name = "boom"

        def generate(self, kind, prompt, *, seed=None):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(imagegen, "get_provider", lambda: _Boom())
    desc = imagegen.generate("scene", "a torchlit door", scope="t")  # must NOT raise
    assert desc.get("degraded_from") == "boom" and "error" in desc
    # degraded result is not cached (a later, gateway-up retry can still succeed)
    assert imagegen.cache_read(imagegen.content_hash("scene", "a torchlit door", provider="boom"), "t") is None


# --------------------------------------------------------------------------- #
# Portrait prompt builder (#265) — pure, deterministic, no PII, no newlines.
# --------------------------------------------------------------------------- #

def test_portrait_prompt_contains_race_and_class_words():
    p = imagegen.portrait_prompt("human", "fighter")
    assert "human" in p and "fighter" in p
    assert p.lower().startswith("character portrait of a human fighter")


def test_portrait_prompt_expands_race_shorthand():
    # The wizard emits "half" for Half-Elf; the brief must read "half-elf", never "half".
    p = imagegen.portrait_prompt("half", "wizard")
    assert "half-elf" in p
    assert "a half-elf wizard" in p


def test_portrait_prompt_is_deterministic():
    a = imagegen.portrait_prompt("elf", "rogue", appearance="scarred, silver hair", alignment="chaotic-good")
    b = imagegen.portrait_prompt("elf", "rogue", appearance="scarred, silver hair", alignment="chaotic-good")
    assert a == b


def test_portrait_prompt_has_no_newlines():
    # A multi-line brief could smuggle prompt structure — must stay one line.
    p = imagegen.portrait_prompt("tiefling", "warlock", appearance="line one\nline two\r\nthree\tfour")
    assert "\n" not in p and "\r" not in p and "\t" not in p


def test_portrait_prompt_omits_name_pii():
    # name is accepted (callers pass the wizard struct) but must NOT appear in the brief.
    p = imagegen.portrait_prompt("human", "bard", name="Eira of the Hollow Reach")
    assert "Eira" not in p and "Hollow Reach" not in p


def test_portrait_prompt_sanitizes_appearance_cues():
    # Quotes/braces/backslashes that could confuse a downstream prompt are stripped.
    p = imagegen.portrait_prompt("dwarf", "cleric", appearance='braided {beard} "with" \\runes')
    assert '"' not in p and "{" not in p and "}" not in p and "\\" not in p
    assert "braided" in p and "beard" in p and "runes" in p


def test_portrait_prompt_carries_aesthetic_and_safety_clauses():
    p = imagegen.portrait_prompt("human", "paladin")
    low = p.lower()
    assert "oil-painting" in low or "oil painting" in low
    assert "no text" in low and "no watermark" in low
    assert "forgotten realms" in low


def test_portrait_prompt_unknown_race_class_humanized_not_crash():
    # An unmapped race/class is humanized verbatim rather than crashing.
    p = imagegen.portrait_prompt("aarakocra", "artificer")
    assert "aarakocra" in p and "artificer" in p


def test_portrait_prompt_empty_inputs_have_safe_defaults():
    p = imagegen.portrait_prompt("", "")
    # Falls back to sensible words; still a usable single-line brief.
    assert "a human adventurer" in p
    assert "\n" not in p


def test_portrait_prompt_drives_cache_key():
    # The prompt feeds content_hash, so two different briefs key to different entries.
    p1 = imagegen.portrait_prompt("human", "fighter")
    p2 = imagegen.portrait_prompt("human", "wizard")
    assert imagegen.content_hash("portrait", p1) != imagegen.content_hash("portrait", p2)


# --------------------------------------------------------------------------- #
# copy_scope (#265 re-key) — null-provider round-trip, miss/corrupt handling.
# --------------------------------------------------------------------------- #

def test_copy_scope_round_trip_with_null_provider(state):
    # Generate to a provisional scope, then re-key onto a real char scope.
    src = "portrait-pc-abc123"
    dst = "portrait-char_09bfb0ec913c"
    imagegen.generate("portrait", "a human fighter, painterly", scope=src)
    written = imagegen.copy_scope(src, dst)
    assert written is not None and written.is_file()
    # The destination scope now resolves a descriptor (this is what the viewer reads).
    got = imagegen._newest_descriptor(dst)
    assert got is not None
    assert got["kind"] == "portrait" and got["prompt"] == "a human fighter, painterly"
    # It lands under <state>/images/<safe-dst>/ — a derived artifact, not campaign state.
    assert written.parent == state / "images" / imagegen._safe_scope(dst)
    assert "campaigns" not in str(written)


def test_copy_scope_recomputes_hash_for_destination(state):
    src = "portrait-pc-xyz"
    dst = "portrait-char_deadbeef"
    imagegen.generate("portrait", "an elf ranger", scope=src)
    written = imagegen.copy_scope(src, dst)
    assert written is not None
    # The copy's filename is its OWN recomputed content hash, self-consistent on read.
    key = imagegen.content_hash("portrait", "an elf ranger", provider="null")
    assert written.name == f"{key}.json"
    assert imagegen.cache_read(key, scope=dst) is not None


def test_copy_scope_missing_source_returns_none(state):
    # No source descriptor -> benign miss (caller falls back to the gallery face).
    assert imagegen.copy_scope("portrait-pc-nope", "portrait-char_x") is None


def test_copy_scope_carries_inline_bytes(state):
    # A descriptor with inline image bytes copies the payload verbatim into the new scope.
    desc = {
        "provider": "null",
        "kind": "portrait",
        "prompt": "a dwarf cleric",
        "seed": None,
        "placeholder": False,
        "bytes_b64": "QUJD",  # "ABC"
        "mime_type": "image/png",
    }
    imagegen.cache_write(desc, scope="portrait-pc-bytes")
    written = imagegen.copy_scope("portrait-pc-bytes", "portrait-char_bytes")
    assert written is not None
    got = imagegen._newest_descriptor("portrait-char_bytes")
    assert got is not None and got.get("bytes_b64") == "QUJD" and got.get("mime_type") == "image/png"


def test_copy_scope_corrupt_source_is_a_miss(state):
    # A corrupt source descriptor is treated as a miss, never a crash.
    src = "portrait-pc-corrupt"
    cdir = state / "images" / imagegen._safe_scope(src)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "bad.json").write_text("{ not json", encoding="utf-8")
    assert imagegen.copy_scope(src, "portrait-char_c") is None
