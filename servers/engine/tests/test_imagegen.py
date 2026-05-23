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
